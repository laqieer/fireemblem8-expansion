#!/usr/bin/env python3
"""Installed Linux broker/client entry point. Never execute from a candidate."""

from __future__ import annotations

import argparse
import base64
import fcntl
import hashlib
import http.client
import importlib.abc
import importlib.util
import json
import os
import re
import signal
import socket
import sqlite3
import ssl
import stat
import struct
import sys
import time
from contextlib import ExitStack, contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from types import MappingProxyType


INSTALLED_MODULES = (
    "__init__.py", "signed_records.py", "git_broker_protocol.py",
    "git_broker_store.py", "git_broker.py",
)


def _capture_installed_sources(entry: Path) -> tuple[Path, dict[str, bytes]]:
    """Capture the entire protected closure before any repository import."""
    if not entry.is_absolute() or ".." in entry.parts or entry.name != "git_broker.py":
        raise ValueError("absolute installed broker entry point required")
    root = entry.parent
    for directory in (root, *root.parents):
        metadata = directory.lstat()
        if (
            metadata.st_uid != 0 or metadata.st_mode & 0o022
            or not stat.S_ISDIR(metadata.st_mode)
        ):
            raise ValueError("broker source parent is not protected")
    sources = {}
    for name in INSTALLED_MODULES:
        descriptor = os.open(
            root / name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
        )
        with os.fdopen(descriptor, "rb") as source:
            metadata = os.fstat(source.fileno())
            if (
                metadata.st_uid != 0 or metadata.st_mode & 0o022 or metadata.st_nlink != 1
                or not stat.S_ISREG(metadata.st_mode) or metadata.st_size > 1024 * 1024
            ):
                raise ValueError("broker source is not a protected bounded regular file")
            raw = source.read(1024 * 1024 + 1)
            if len(raw) != metadata.st_size:
                raise ValueError("broker source changed while being captured")
            sources[name] = raw
    return root, sources


class _SourceOnlyBroker(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    """Reuse the captured-source loader pattern; never consult installed caches."""

    def __init__(self, root, sources):
        self.root, self.sources = root, MappingProxyType(dict(sources))
        self.modules = {"scripts": (None, True)}
        for name in sources:
            package = name == "__init__.py"
            module = "scripts.workflow_pilot" + ("" if package else "." + name[:-3])
            self.modules[module] = (name, package)

    def find_spec(self, fullname, path=None, target=None):
        if fullname != "scripts" and not fullname.startswith("scripts."):
            return None
        if fullname not in self.modules:
            raise ModuleNotFoundError("import outside installed broker closure", name=fullname)
        name, package = self.modules[fullname]
        return importlib.util.spec_from_loader(
            fullname, self, origin=str(self.root / name) if name else fullname, is_package=package,
        )

    def create_module(self, spec):
        return None

    def exec_module(self, module):
        name, package = self.modules[module.__spec__.name]
        if package:
            module.__path__ = []
        if name is not None:
            module.__file__ = str(self.root / name)
            exec(compile(self.sources[name], module.__file__, "exec", dont_inherit=True), module.__dict__)


@contextmanager
def _source_only_broker(entry: Path):
    loader = _SourceOnlyBroker(*_capture_installed_sources(entry))
    previous = {
        name: module for name, module in sys.modules.copy().items()
        if name == "scripts" or name.startswith("scripts.")
    }
    for name in previous:
        del sys.modules[name]
    sys.meta_path.insert(0, loader)
    try:
        yield importlib.import_module("scripts.workflow_pilot.git_broker")
    finally:
        sys.meta_path.remove(loader)
        for name in tuple(sys.modules):
            if name == "scripts" or name.startswith("scripts."):
                del sys.modules[name]
        sys.modules.update(previous)


if __name__ == "__main__":
    if not sys.flags.isolated:
        print("git-broker: isolated Python startup (-I) required", file=sys.stderr)
        raise SystemExit(2)
    try:
        with _source_only_broker(Path(__file__)) as installed:
            raise SystemExit(installed.main())
    except (OSError, ValueError, ImportError, SyntaxError):
        print("git-broker: protected source bootstrap failed closed", file=sys.stderr)
        raise SystemExit(2)

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
GITHUB_CA = Path("/etc/ssl/certs/ca-certificates.crt")
IDENTITY_MAX = 16 * 1024
TOKEN_MAX = 1024
KEY_MAX = 16 * 1024
HOSTS_MAX = 64 * 1024
SNAPSHOT_SEALS = fcntl.F_SEAL_WRITE | fcntl.F_SEAL_GROW | fcntl.F_SEAL_SHRINK | fcntl.F_SEAL_SEAL
GITHUB_LOGIN = r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?"
SERVER_FIELDS = {
    "schema_version", "role", "policy", "broker_uid", "coordinator_uid", "candidate_uids",
    "socket", "socket_gid", "state", "certificate", "private_key", "ca_certificate",
    "server_certificate_sha256", "response_public_key", "response_private_key", "transport",
}
CLIENT_FIELDS = {
    "schema_version", "role", "policy", "broker_uid", "coordinator_uid", "candidate_uids",
    "socket", "socket_gid", "certificate", "private_key", "ca_certificate",
    "server_certificate_sha256", "response_public_key",
}


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


def read_regular(
    path: Path, maximum: int, *, owners: set[int] | None = None, secret: bool = False,
) -> bytes:
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > maximum:
            raise RecordError("regular input size bound")
        if owners is not None and (
            metadata.st_uid not in owners or metadata.st_nlink != 1
            or metadata.st_mode & (0o077 if secret else 0o022)
        ):
            raise RecordError("opened protected input was substituted")
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


def network_credential_contract(transport: dict, policy: Policy) -> None:
    kind = transport.get("kind")
    if kind == "https":
        fields(transport, {"kind", "credential_kind", "token_file", "helper"})
        if (
            transport["credential_kind"] != "github-fine-grained-user-pat"
            or policy.endpoint != f"https://github.com/{policy.repository}.git"
        ):
            raise RecordError("unsupported HTTPS credential authority")
    elif kind == "ssh":
        fields(transport, {"kind", "credential_kind", "key", "known_hosts", "public_key_fingerprint"})
        fingerprint = transport["public_key_fingerprint"]
        if (
            transport["credential_kind"] != "github-user-ed25519"
            or policy.endpoint != f"ssh://git@github.com/{policy.repository}.git"
            or not isinstance(fingerprint, str)
            or re.fullmatch(r"SHA256:[A-Za-z0-9+/]{42}[AEIMQUYcgkosw048]", fingerprint) is None
        ):
            raise RecordError("unsupported SSH credential authority")
    else:
        raise RecordError("network credential authority required")
    if not any(
        actor == {
            "actor_type": "User", "actor_id": policy.actor_id,
            "database_id": policy.actor_id, "bypass_mode": "always",
        }
        for actor in policy.authorized_bypass_actors
    ):
        raise RecordError("network credentials require the exact installed User bypass")


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
    socket_gid = integer(manifest["socket_gid"], 1, 2**31 - 1)
    if socket_gid not in {os.getegid(), *os.getgroups()}:
        raise RecordError("principal lacks the installed coordinator-only socket group")
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
        for executable in ("/usr/bin/git", "/usr/bin/openssl", "/usr/bin/timeout", "/usr/bin/python3"):
            resolved = protected_path(Path(executable).resolve(strict=True), {0})
            if not os.access(resolved, os.X_OK):
                raise RecordError("protected executable dependency is unavailable")
        protected_path("/usr/lib/git-core", {0}, directory=True)
        if certificate_fingerprint(Path(manifest["certificate"])) != manifest["server_certificate_sha256"]:
            raise RecordError("server certificate does not match installed identity")
        state = protected_path(manifest["state"], {0, broker}, directory=True, secret=True)
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
            if state not in remote.parents:
                raise RecordError("local remote must be a strict descendant of broker state")
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
            network_credential_contract(transport, policy)
            protected_path(GITHUB_CA, {0})
            protected_path(transport["token_file"], {0, broker}, secret=True)
            if Path(transport["helper"]) != Path(__file__).resolve():
                raise RecordError("credential helper must be this exact trusted installed entry point")
            protected_path(transport["helper"], {0})
            protected_path(Path("/usr/lib/git-core/git-remote-https").resolve(strict=True), {0})
        elif kind == "ssh":
            network_credential_contract(transport, policy)
            protected_path(GITHUB_CA, {0})
            protected_path(transport["key"], {0, broker}, secret=True)
            protected_path(transport["known_hosts"], {0})
            protected_path(Path("/usr/bin/ssh").resolve(strict=True), {0})
            protected_path(Path("/usr/bin/ssh-keygen").resolve(strict=True), {0})
        else:
            raise RecordError("unknown transport")
    return manifest, policy


@contextmanager
def sealed_snapshot(raw: bytes):
    descriptor = os.memfd_create("fe8-broker-credential", os.MFD_CLOEXEC | os.MFD_ALLOW_SEALING)
    try:
        os.fchmod(descriptor, 0o600)
        offset = 0
        while offset < len(raw):
            offset += os.write(descriptor, raw[offset:])
        fcntl.fcntl(descriptor, fcntl.F_ADD_SEALS, SNAPSHOT_SEALS)
        # ssh closes inherited descriptors. Keep the sealed object open in its
        # broker parent and use that same procfs handle throughout validation/Git.
        yield f"/proc/{os.getpid()}/fd/{descriptor}"
    finally:
        os.close(descriptor)


def read_snapshot(path: str, maximum: int) -> bytes:
    if not isinstance(path, str) or re.fullmatch(r"/proc/[1-9][0-9]*/fd/[0-9]+", path) is None:
        raise RecordError("broker-held sealed credential handle required")
    descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 0
            or metadata.st_uid != os.geteuid() or stat.S_IMODE(metadata.st_mode) != 0o600
            or not 0 < metadata.st_size <= maximum
            or fcntl.fcntl(descriptor, fcntl.F_GET_SEALS) & SNAPSHOT_SEALS != SNAPSHOT_SEALS
        ):
            raise RecordError("credential handle is not an immutable private snapshot")
        raw = os.pread(descriptor, maximum + 1, 0)
        if len(raw) != metadata.st_size:
            raise RecordError("credential snapshot size mismatch")
        return raw
    finally:
        os.close(descriptor)


def fine_grained_token(raw: bytes) -> bytes:
    token = raw.removesuffix(b"\n")
    if re.fullmatch(rb"github_pat_[A-Za-z0-9_]{1,1013}", token) is None:
        raise RecordError("only a fine-grained GitHub User PAT is supported")
    return token


def ssh_arguments(credential: str, known_hosts: str) -> list[str]:
    return [
        "/usr/bin/ssh", "-F", "/dev/null", "-i", credential,
        "-o", f"UserKnownHostsFile={known_hosts}", "-o", "GlobalKnownHostsFile=/dev/null",
        "-o", "HostName=github.com", "-o", "HostKeyAlias=github.com", "-o", "Port=22",
        "-o", "IdentityAgent=none", "-o", "IdentitiesOnly=yes",
        "-o", "CertificateFile=none", "-o", "PubkeyAcceptedAlgorithms=ssh-ed25519",
        "-o", "StrictHostKeyChecking=yes", "-o", "BatchMode=yes",
        "-o", "VerifyHostKeyDNS=no", "-o", "UpdateHostKeys=no",
        "-o", "ForwardAgent=no", "-o", "ClearAllForwardings=yes",
        "-o", "ProxyCommand=none", "-o", "PermitLocalCommand=no",
        "-o", "ControlMaster=no", "-o", "ControlPath=none",
        "-o", "PreferredAuthentications=publickey",
        "-o", "PasswordAuthentication=no", "-o", "KbdInteractiveAuthentication=no",
        "-o", "ConnectTimeout=5", "-o", "LogLevel=ERROR",
    ]


def github_user(*, token: bytes | None, login: str | None, deadline) -> dict:
    """Fixed GitHub API only; the caller runs this inside the hard-kill worker."""
    if (token is None) == (login is None) or (
        login is not None and re.fullmatch(GITHUB_LOGIN, login) is None
    ):
        raise RecordError("unsupported GitHub identity query")
    seconds = min(5, (deadline - utc_now()).total_seconds())
    if seconds <= 0:
        raise RecordError("credential identity deadline expired")
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    # Do not use create_default_context(), environment-selected roots or proxies.
    context.load_verify_locations(cafile=str(protected_path(GITHUB_CA, {0})))
    headers = {
        "Accept": "application/vnd.github+json", "Accept-Encoding": "identity",
        "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "fe8-issue-git-broker",
        "Connection": "close",
    }
    if token is not None:
        headers["Authorization"] = "Bearer " + fine_grained_token(token).decode("ascii")
    connection = http.client.HTTPSConnection("api.github.com", 443, timeout=seconds, context=context)
    try:
        connection.request("GET", "/user" if login is None else "/users/" + login, headers=headers)
        response = connection.getresponse()
        if (
            response.status != 200
            or response.getheader("Content-Encoding", "identity") != "identity"
            or response.getheader("Content-Type", "").split(";", 1)[0] != "application/json"
        ):
            raise RecordError("GitHub identity response rejected")
        length = response.getheader("Content-Length")
        if length is not None and (
            re.fullmatch(r"[0-9]{1,8}", length) is None or int(length) > IDENTITY_MAX
        ):
            raise RecordError("GitHub identity response bound")
        raw = bytearray()
        while len(raw) <= IDENTITY_MAX:
            seconds = min(5, (deadline - utc_now()).total_seconds())
            if seconds <= 0:
                raise RecordError("credential identity deadline expired")
            if connection.sock is not None:
                connection.sock.settimeout(seconds)
            chunk = response.read(min(4096, IDENTITY_MAX + 1 - len(raw)))
            if not chunk:
                break
            raw.extend(chunk)
        if not raw or len(raw) > IDENTITY_MAX:
            raise RecordError("GitHub identity response bound")

        def pairs(items):
            result = {}
            for key, value in items:
                if key in result:
                    raise RecordError("duplicate GitHub identity field")
                result[key] = value
            return result

        def reject_constant(_value):
            raise RecordError("non-finite GitHub identity field")

        value = json.loads(raw.decode("utf-8"), object_pairs_hook=pairs, parse_constant=reject_constant)
        if not isinstance(value, dict):
            raise RecordError("GitHub identity object required")
        return {key: value.get(key) for key in ("id", "type", "login")}
    except (http.client.HTTPException, OSError, UnicodeError, ValueError, RecursionError):
        raise RecordError("GitHub credential identity verification failed") from None
    finally:
        connection.close()


def validate_credential_identity(manifest: dict, policy: Policy, environment: dict, deadline) -> None:
    transport = manifest["transport"]
    network_credential_contract(transport, policy)
    credential = environment["FE8_BROKER_CREDENTIAL"]
    raw = read_snapshot(credential, TOKEN_MAX if transport["kind"] == "https" else KEY_MAX)
    if transport["kind"] == "https":
        user = github_user(token=fine_grained_token(raw), login=None, deadline=deadline)
        login = user["login"]
    else:
        known_hosts = environment["FE8_BROKER_KNOWN_HOSTS"]
        read_snapshot(known_hosts, HOSTS_MAX)
        options = {
            "cwd": Path(manifest["state"]), "environment": clean_environment(Path(manifest["state"]) / "home"),
            "deadline": deadline, "maximum_output": 4096,
        }
        public = run_bounded([
            "/usr/bin/ssh-keygen", "-y", "-P", "", "-f", credential,
        ], **options).split()
        if len(public) < 2 or public[0] != b"ssh-ed25519":
            raise RecordError("only a plain unencrypted Ed25519 User key is supported")
        decoded = base64.b64decode(public[1], validate=True)
        if (
            len(decoded) != 51 or not decoded.startswith(b"\0\0\0\x0bssh-ed25519\0\0\0\x20")
            or base64.b64encode(decoded) != public[1]
        ):
            raise RecordError("invalid Ed25519 public key")
        fingerprint = "SHA256:" + base64.b64encode(hashlib.sha256(decoded).digest()).decode("ascii").rstrip("=")
        if fingerprint != transport["public_key_fingerprint"]:
            raise RecordError("SSH key differs from the protected public fingerprint")
        greeting = run_bounded(
            [*ssh_arguments(credential, known_hosts), "-T", "-n", "git@github.com"],
            **options, allowed_codes=(1,), capture_stderr=True,
        )
        match = re.fullmatch(
            rf"Hi ({GITHUB_LOGIN})! You've successfully authenticated, but GitHub does not provide shell access\.\r?\n",
            greeting.decode("ascii"),
        )
        if match is None:
            raise RecordError("GitHub did not authenticate a supported SSH User principal")
        login = match[1]
        user = github_user(token=None, login=login, deadline=deadline)
    if (
        user["type"] != "User" or type(user["id"]) is not int or user["id"] != policy.actor_id
        or not isinstance(login, str) or re.fullmatch(GITHUB_LOGIN, login) is None
        or user["login"] != login
    ):
        raise RecordError("credential does not authenticate the installed GitHub User")


def credential_worker_context() -> tuple[dict, Policy, datetime]:
    manifest, policy = load_installation(Path(os.environ.get("FE8_BROKER_INSTALLATION", "")), "server")
    network_credential_contract(manifest["transport"], policy)
    deadline = parse_utc(os.environ.get("FE8_BROKER_CREDENTIAL_DEADLINE"))
    if not 0 < (deadline - utc_now()).total_seconds() <= MAX_LIFETIME:
        raise RecordError("credential identity deadline expired or overlong")
    return manifest, policy, deadline


@contextmanager
def verified_credentials(installation: Path | None, policy: Policy, transport: dict, state: Path, deadline):
    if installation is None:
        raise RecordError("protected network credential installation required")
    manifest, installed = load_installation(installation, "server")
    if installed != policy or manifest["transport"] != transport or Path(manifest["state"]) != state:
        raise RecordError("network credential installation changed")
    network_credential_contract(transport, policy)
    with ExitStack() as snapshots:
        key = "token_file" if transport["kind"] == "https" else "key"
        maximum = TOKEN_MAX if transport["kind"] == "https" else KEY_MAX
        raw = read_regular(
            Path(transport[key]), maximum, owners={0, manifest["broker_uid"]}, secret=True,
        )
        environment = {
            "FE8_BROKER_INSTALLATION": str(installation),
            "FE8_BROKER_CREDENTIAL_DEADLINE": format_utc(deadline),
            "FE8_BROKER_CREDENTIAL": snapshots.enter_context(sealed_snapshot(raw)),
        }
        if transport["kind"] == "ssh":
            hosts = read_regular(Path(transport["known_hosts"]), HOSTS_MAX, owners={0})
            environment["FE8_BROKER_KNOWN_HOSTS"] = snapshots.enter_context(sealed_snapshot(hosts))
        result = run_bounded(
            ["/usr/bin/python3", "-I", str(Path(__file__).resolve()), "credential-check"],
            cwd=state, environment={**clean_environment(state / "home"), **environment},
            deadline=deadline, maximum_output=128,
        )
        if result != b"verified\n":
            raise RecordError("network credential identity was not verified")
        yield environment


def preflight_credentials(manifest: dict, policy: Policy, installation: Path) -> None:
    if manifest["transport"]["kind"] != "local":
        with verified_credentials(
            installation, policy, manifest["transport"], Path(manifest["state"]),
            utc_now() + timedelta(seconds=MAX_LIFETIME),
        ):
            pass


def peer_uid(connection: socket.socket, expected: int) -> None:
    if connection.family != socket.AF_UNIX or not hasattr(socket, "SO_PEERCRED"):
        raise RecordError("kernel-authenticated Unix peer required")
    pid, uid, _gid = struct.unpack(
        "3i", connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i")),
    )
    if pid <= 0 or uid != expected or uid == os.geteuid():
        raise RecordError("same-UID or unauthorized peer")


def socket_permissions(endpoint: Path, manifest: dict) -> None:
    metadata = endpoint.lstat()
    if (
        not stat.S_ISSOCK(metadata.st_mode) or metadata.st_nlink != 1
        or metadata.st_uid != manifest["broker_uid"] or metadata.st_gid != manifest["socket_gid"]
        or stat.S_IMODE(metadata.st_mode) != 0o660
        or "system.posix_acl_access" in os.listxattr(endpoint, follow_symlinks=False)
    ):
        raise RecordError("broker endpoint lacks exclusive coordinator-group access")


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
        preflight_credentials(manifest, policy, installation)
        # Verify the response key before exposing any ready endpoint.
        sign_response(manifest, b"workflow-pilot-broker-preflight\0", utc_now() + timedelta(seconds=5))
        # Never unlink an existing socket: it may belong to a live service.
        listener.bind(str(endpoint))
        bound = endpoint.lstat().st_ino
        os.chown(endpoint, -1, manifest["socket_gid"], follow_symlinks=False)
        os.chmod(endpoint, 0o660)
        socket_permissions(endpoint, manifest)
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
        try:
            listener.close()
            if bound is not None and endpoint.exists() and endpoint.lstat().st_ino == bound:
                endpoint.unlink()
        finally:
            store.close()


class BrokerClient:
    """Production consumer: pinned installation, authenticated peer, exact plan only."""

    def __init__(self, installation: Path):
        self.manifest, self.policy = load_installation(installation, "client")

    def request(self, plan: dict | None = None, pack: bytes | None = None, *, readback: bool = False) -> dict:
        manifest = self.manifest
        endpoint = Path(manifest["socket"])
        socket_permissions(endpoint, manifest)
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
    manifest, policy, _deadline = credential_worker_context()
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
    token = fine_grained_token(read_snapshot(os.environ["FE8_BROKER_CREDENTIAL"], TOKEN_MAX))
    sys.stdout.buffer.write(b"username=x-access-token\npassword=" + token + b"\n\n")


class _ServeStopped(BaseException):
    """SIGTERM unwinds the service without reclassifying failures or SIGINT."""


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
    commands.add_parser("credential-check")
    arguments = parser.parse_args(argv)
    try:
        os.umask(0o077)
        if arguments.command == "credential":
            credential_helper(arguments.action)
            return 0
        if arguments.command == "credential-check":
            manifest, policy, deadline = credential_worker_context()
            validate_credential_identity(manifest, policy, os.environ, deadline)
            sys.stdout.buffer.write(b"verified\n")
            return 0
        if arguments.command == "serve":
            def stop(_signum, _frame):
                signal.signal(signal.SIGTERM, signal.SIG_IGN)
                raise _ServeStopped
            previous = signal.signal(signal.SIGTERM, stop)
            try:
                serve(arguments.installation, once=arguments.once)
            except _ServeStopped:
                return 0
            finally:
                signal.signal(signal.SIGTERM, previous)
            return 0
        if arguments.command == "preflight-server":
            manifest, policy = load_installation(arguments.installation, "server")
            tls_context(manifest, True)
            store = PublicationStore(
                policy, Path(manifest["state"]), arguments.installation, manifest["transport"],
            )
            try:
                store.local_protection(utc_now() + timedelta(seconds=MAX_LIFETIME))
                preflight_credentials(manifest, policy, arguments.installation)
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
