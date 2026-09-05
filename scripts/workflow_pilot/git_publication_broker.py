#!/usr/bin/env python3
"""Authenticated one-shot Git authority and anchor publication broker."""

from __future__ import annotations

import argparse
import base64
import ctypes
import datetime as datetime_module
import fcntl
import hashlib
import json
import os
import re
import resource
import select
import shlex
import shutil
import signal
import socket
import stat
import struct
import subprocess
import sys
import urllib.parse
import warnings
from pathlib import Path
from typing import Any, BinaryIO

from . import reporter


PROTOCOL = "workflow-pilot-authenticated-git-broker-v1"
PLAN_DOMAIN = b"workflow-pilot-git-publication-plan-v1\0"
RESPONSE_DOMAIN = b"workflow-pilot-git-publication-response-v1\0"
REQUEST_MAX_BYTES = 16 * 1024
RESPONSE_MAX_BYTES = 64 * 1024
DEFAULT_PACK_MAX_BYTES = 64 * 1024 * 1024
SUBPROCESS_OUTPUT_MAX_BYTES = 1024 * 1024
JOURNAL_MAX_BYTES = 16 * 1024 * 1024
OPENSSL = "/usr/bin/openssl"
GIT = reporter.GIT
HEX_64_RE = re.compile(r"^[0-9a-f]{64}$")
REPOSITORY_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
ENDPOINT_PATH_RE = re.compile(r"^/[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*\.git$")
KEY_ID_RE = re.compile(r"^[A-Za-z0-9_.-]{1,80}$")
ACTOR_RE = re.compile(r"^[A-Za-z0-9_.:@/-]{1,160}$")


class BrokerError(ValueError):
    """Fail-closed protocol or authority validation error."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _fail(code: str, message: str) -> None:
    raise BrokerError(code, message)


def _object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        _fail("invalid-record", f"{label} must be an object")
    return value


def _list(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        _fail("invalid-record", f"{label} must be an array")
    return value


def _string(value: Any, label: str, pattern: re.Pattern[str] | None = None) -> str:
    if not isinstance(value, str) or not value:
        _fail("invalid-record", f"{label} must be a nonempty string")
    if pattern is not None and pattern.fullmatch(value) is None:
        _fail("invalid-record", f"{label} has invalid syntax")
    return value


def _integer(value: Any, label: str, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        _fail("invalid-record", f"{label} must be an integer at least {minimum}")
    return value


def _exact_keys(value: dict[str, Any], label: str, keys: tuple[str, ...]) -> None:
    if set(value) != set(keys):
        _fail("invalid-record", f"{label} fields differ from the closed contract")


def _parse_json(raw: bytes, label: str) -> Any:
    def no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                _fail("invalid-json", f"{label} contains duplicate key {key!r}")
            result[key] = value
        return result

    try:
        return json.loads(raw.decode("ascii"), object_pairs_hook=no_duplicates)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        _fail("invalid-json", f"{label} is not canonical JSON: {error}")


def _normalized_json(value: Any) -> bytes:
    return reporter.normalized_json(value)


def _digest(value: Any) -> str:
    return hashlib.sha256(_normalized_json(value)).hexdigest()


def _decode_signature(value: Any, label: str) -> bytes:
    encoded = _string(value, label)
    try:
        decoded = base64.b64decode(encoded, validate=True)
    except ValueError as error:
        raise BrokerError("invalid-signature", f"{label} is not base64") from error
    if base64.b64encode(decoded).decode("ascii") != encoded or len(decoded) != 64:
        _fail("invalid-signature", f"{label} is not a canonical Ed25519 signature")
    return decoded


def _memory_file(name: str, payload: bytes) -> int:
    if not hasattr(os, "memfd_create"):
        _fail("signature-tool-failed", "platform lacks anonymous memory files")
    fd = os.memfd_create(name, flags=getattr(os, "MFD_CLOEXEC", 0))
    _write_all(fd, payload)
    os.lseek(fd, 0, os.SEEK_SET)
    return fd


def _write_all(fd: int, payload: bytes) -> None:
    offset = 0
    while offset < len(payload):
        written = os.write(fd, payload[offset:])
        if written <= 0:
            _fail("broker-io-failed", "bounded write made no progress")
        offset += written


def _empty_memory_file(name: str) -> int:
    if not hasattr(os, "memfd_create"):
        _fail("process-hardening-failed", "platform lacks anonymous output files")
    return os.memfd_create(name, flags=getattr(os, "MFD_CLOEXEC", 0))


def _read_bounded_fd(fd: int, maximum: int) -> bytes:
    chunks = bytearray()
    while len(chunks) <= maximum:
        chunk = os.read(fd, min(65536, maximum + 1 - len(chunks)))
        if not chunk:
            break
        chunks.extend(chunk)
    return bytes(chunks)


def _verify_ed25519(public_key: Path, payload: bytes, signature: bytes) -> None:
    payload_fd = _memory_file("workflow-pilot-signature-payload", payload)
    signature_fd = _memory_file("workflow-pilot-signature", signature)
    try:
        completed = _run_bounded(
            [
                OPENSSL,
                "pkeyutl",
                "-verify",
                "-pubin",
                "-inkey",
                os.fspath(public_key),
                "-rawin",
                "-in",
                f"/proc/self/fd/{payload_fd}",
                "-sigfile",
                f"/proc/self/fd/{signature_fd}",
            ],
            timeout=10,
            pass_fds=(payload_fd, signature_fd),
            environment={"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C"},
        )
    finally:
        os.close(payload_fd)
        os.close(signature_fd)
    if completed.returncode != 0:
        _fail("invalid-signature", "Ed25519 signature verification failed")


def _sign_ed25519(private_key: Path, payload: bytes) -> bytes:
    payload_fd = _memory_file("workflow-pilot-response-payload", payload)
    try:
        completed = _run_bounded(
            [
                OPENSSL,
                "pkeyutl",
                "-sign",
                "-inkey",
                os.fspath(private_key),
                "-rawin",
                "-in",
                f"/proc/self/fd/{payload_fd}",
            ],
            timeout=10,
            pass_fds=(payload_fd,),
            environment={"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C"},
        )
    finally:
        os.close(payload_fd)
    if completed.returncode != 0 or len(completed.stdout) != 64:
        _fail("signature-tool-failed", "cannot sign broker response")
    return completed.stdout


def _signed_payload(domain: bytes, record: dict[str, Any]) -> bytes:
    unsigned = dict(record)
    unsigned.pop("signature", None)
    return domain + _normalized_json(unsigned)


def _sha(value: Any, label: str) -> str:
    if not isinstance(value, str) or reporter.SHA_RE.fullmatch(value) is None:
        _fail("invalid-record", f"{label} must be a full lowercase Git SHA")
    return value


def _time(value: Any, label: str) -> datetime_module.datetime:
    try:
        parsed = reporter.parse_time(value, label)
    except reporter.PilotDataError as error:
        raise BrokerError("invalid-time", str(error)) from error
    assert parsed is not None
    return parsed


def _read_json_file(path: Path, label: str, maximum: int = REQUEST_MAX_BYTES) -> dict[str, Any]:
    try:
        with path.open("rb") as stream:
            raw = stream.read(maximum + 1)
    except OSError as error:
        raise BrokerError("authority-unavailable", f"cannot read {label}") from error
    if len(raw) > maximum:
        _fail("oversized-record", f"{label} exceeds its size limit")
    return _object(_parse_json(raw, label), label)


def _read_canonical_json_file(
    path: Path, label: str, maximum: int = REQUEST_MAX_BYTES
) -> dict[str, Any]:
    try:
        raw = path.read_bytes()
    except OSError as error:
        raise BrokerError("authority-unavailable", f"cannot read {label}") from error
    if len(raw) > maximum:
        _fail("oversized-record", f"{label} exceeds its size limit")
    value = _object(_parse_json(raw, label), label)
    if raw != _normalized_json(value):
        _fail("invalid-json", f"{label} must use canonical JSON")
    return value


def _require_secure_path(
    path: Path,
    *,
    label: str,
    allowed_owners: set[int],
    reject_owner: int | None = None,
    regular: bool | None = None,
) -> os.stat_result:
    if not path.is_absolute():
        _fail("insecure-installation", f"{label} path must be absolute")
    current = Path("/")
    parts = path.parts[1:]
    metadata: os.stat_result | None = None
    for index, part in enumerate(parts):
        current = current / part
        try:
            metadata = os.lstat(current)
        except OSError as error:
            raise BrokerError("authority-unavailable", f"cannot inspect {label}") from error
        if stat.S_ISLNK(metadata.st_mode):
            _fail("insecure-installation", f"{label} path must not contain symlinks")
        if metadata.st_mode & 0o022:
            _fail("insecure-installation", f"{label} path must not be group/world writable")
        if metadata.st_uid not in allowed_owners:
            _fail("insecure-installation", f"{label} owner is not authorized")
        if reject_owner is not None and metadata.st_uid == reject_owner:
            _fail("insecure-installation", f"{label} is controlled by the candidate principal")
        if index < len(parts) - 1 and not stat.S_ISDIR(metadata.st_mode):
            _fail("insecure-installation", f"{label} parent is not a directory")
    assert metadata is not None
    if regular is True and not stat.S_ISREG(metadata.st_mode):
        _fail("insecure-installation", f"{label} must be a regular file")
    if regular is False and not stat.S_ISDIR(metadata.st_mode):
        _fail("insecure-installation", f"{label} must be a directory")
    return metadata


def _resolve_secure_member(
    root: Path,
    value: Any,
    *,
    label: str,
    owners: set[int],
    regular: bool | None,
) -> Path:
    raw = _string(value, label)
    path = Path(raw)
    if not path.is_absolute():
        path = root / path
    path = Path(os.path.abspath(path))
    _require_secure_path(path, label=label, allowed_owners=owners, regular=regular)
    return path


def _canonical_endpoint(value: Any, *, allow_local: bool) -> str:
    endpoint = _string(value, "endpoint")
    parsed = urllib.parse.urlsplit(endpoint)
    if (
        parsed.query
        or parsed.fragment
        or ENDPOINT_PATH_RE.fullmatch(parsed.path) is None
        or any(part in {".", ".."} for part in parsed.path.split("/"))
    ):
        _fail("invalid-endpoint", "endpoint must be a canonical repository URL")
    if parsed.scheme == "file":
        if not allow_local or parsed.netloc or not parsed.path.startswith("/"):
            _fail("invalid-endpoint", "file endpoint is allowed only for local protected tests")
        canonical = urllib.parse.urlunsplit(("file", "", parsed.path, "", ""))
    elif parsed.scheme == "https":
        if parsed.username or parsed.password or parsed.hostname is None:
            _fail("invalid-endpoint", "HTTPS endpoint must not contain credentials")
        host = parsed.hostname.lower()
        if ":" in host:
            _fail("invalid-endpoint", "IPv6 endpoints are not in the canonical contract")
        try:
            port = parsed.port
        except ValueError as error:
            raise BrokerError("invalid-endpoint", "endpoint port is malformed") from error
        netloc = host if port is None else f"{host}:{port}"
        canonical = urllib.parse.urlunsplit(("https", netloc, parsed.path, "", ""))
    elif parsed.scheme == "ssh":
        if parsed.username != "git" or parsed.password or parsed.hostname is None:
            _fail("invalid-endpoint", "SSH endpoint must use the git principal")
        host = parsed.hostname.lower()
        if ":" in host:
            _fail("invalid-endpoint", "IPv6 endpoints are not in the canonical contract")
        try:
            port = parsed.port
        except ValueError as error:
            raise BrokerError("invalid-endpoint", "endpoint port is malformed") from error
        netloc = f"git@{host}" if port is None else f"git@{host}:{port}"
        canonical = urllib.parse.urlunsplit(("ssh", netloc, parsed.path, "", ""))
    else:
        _fail("invalid-endpoint", "endpoint scheme is not allowlisted")
    if canonical != endpoint or "%" in parsed.path or "\\" in parsed.path:
        _fail("invalid-endpoint", "endpoint is not canonical")
    return canonical


def _require_repository_endpoint(repository: str, endpoint: str) -> None:
    parsed = urllib.parse.urlsplit(endpoint)
    if parsed.scheme in {"https", "ssh"} and not parsed.path.endswith(
        f"/{repository}.git"
    ):
        _fail("invalid-endpoint", "endpoint path does not bind the exact repository")


def _load_signature_record(record: dict[str, Any], label: str) -> tuple[str, bytes]:
    _exact_keys(record, label, ("algorithm", "key_id", "value"))
    if record["algorithm"] != "ed25519":
        _fail("invalid-signature", f"{label}.algorithm must be ed25519")
    key_id = _string(record["key_id"], f"{label}.key_id", KEY_ID_RE)
    return key_id, _decode_signature(record["value"], f"{label}.value")


def _plan_ref(issue: int, kind: str) -> str:
    if kind == "authority":
        return f"refs/heads/workflow-pilot/issue-{issue}/authority"
    return f"refs/tags/workflow-pilot/issue-{issue}/anchor"


def _validate_plan(
    plan: dict[str, Any],
    installation: dict[str, Any],
    *,
    now: datetime_module.datetime,
) -> dict[str, Any]:
    _exact_keys(
        plan,
        "signed publication plan",
        (
            "schema_version",
            "protocol",
            "installation_id",
            "repository",
            "issue",
            "endpoint",
            "operation",
            "authority_ref",
            "anchor_ref",
            "expected_authority_oid",
            "expected_anchor_oid",
            "new_authority_oid",
            "new_anchor_oid",
            "object_ids",
            "pack_sha256",
            "pack_size",
            "nonce",
            "sequence",
            "issued_at",
            "expires_at",
            "signer",
            "actor",
            "signature",
        ),
    )
    if (
        _integer(plan["schema_version"], "plan.schema_version", 1) != 1
        or plan["protocol"] != PROTOCOL
    ):
        _fail("invalid-plan", "publication plan protocol/version mismatch")
    for field in ("installation_id", "pack_sha256", "nonce"):
        _string(plan[field], f"plan.{field}", HEX_64_RE)
    repository = _string(plan["repository"], "plan.repository", REPOSITORY_RE)
    issue = _integer(plan["issue"], "plan.issue", 1)
    endpoint = _canonical_endpoint(plan["endpoint"], allow_local=installation["test_only"])
    if (
        plan["installation_id"] != installation["installation_id"]
        or repository != installation["repository"]
        or endpoint != installation["endpoint"]
    ):
        _fail("wrong-destination", "publication plan does not bind this installation")
    if plan["operation"] != "publish-authority-anchor":
        _fail("wrong-operation", "publication plan operation is not allowlisted")
    if (
        plan["authority_ref"] != _plan_ref(issue, "authority")
        or plan["anchor_ref"] != _plan_ref(issue, "anchor")
    ):
        _fail("wrong-refs", "publication plan refs are not the exact issue authority pair")
    for field in ("expected_authority_oid", "expected_anchor_oid"):
        if plan[field] is not None:
            _sha(plan[field], f"plan.{field}")
    for field in ("new_authority_oid", "new_anchor_oid"):
        _sha(plan[field], f"plan.{field}")
    object_ids = _list(plan["object_ids"], "plan.object_ids")
    for index, object_id in enumerate(object_ids):
        _sha(object_id, f"plan.object_ids[{index}]")
    if object_ids != sorted(set(object_ids)) or not object_ids:
        _fail("wrong-objects", "plan.object_ids must be sorted, unique, and nonempty")
    if plan["new_authority_oid"] not in object_ids or plan["new_anchor_oid"] not in object_ids:
        _fail("wrong-objects", "new ref targets must belong to the exact object closure")
    pack_size = _integer(plan["pack_size"], "plan.pack_size", 1)
    if pack_size > installation["pack_max_bytes"]:
        _fail("oversized-pack", "publication pack exceeds installation limit")
    sequence = _integer(plan["sequence"], "plan.sequence", 1)
    issued_at = _time(plan["issued_at"], "plan.issued_at")
    expires_at = _time(plan["expires_at"], "plan.expires_at")
    if issued_at > now or expires_at <= now:
        _fail("plan-lifetime", "publication plan is not currently valid")
    lifetime = (expires_at - issued_at).total_seconds()
    if lifetime <= 0 or lifetime > installation["plan_lifetime_seconds"]:
        _fail("plan-lifetime", "publication plan lifetime exceeds the bounded contract")
    signer = _string(plan["signer"], "plan.signer", ACTOR_RE)
    actor = _string(plan["actor"], "plan.actor", ACTOR_RE)
    signature = _object(plan["signature"], "plan.signature")
    key_id, signature_bytes = _load_signature_record(signature, "plan.signature")
    authority = installation["plan_signers"].get(key_id)
    if authority is None or authority["signer"] != signer or authority["actor"] != actor:
        _fail("unauthorized-signer", "publication plan signer/actor is not installed")
    _verify_ed25519(authority["public_key"], _signed_payload(PLAN_DOMAIN, plan), signature_bytes)
    plan["_issued_time"] = issued_at
    plan["_expires_time"] = expires_at
    plan["_sequence"] = sequence
    return plan


def _load_broker_installation(path: Path) -> dict[str, Any]:
    uid = os.geteuid()
    _require_secure_path(
        path,
        label="broker installation",
        allowed_owners={0, uid},
        regular=True,
    )
    raw = _read_json_file(path, "broker installation")
    _exact_keys(
        raw,
        "broker installation",
        (
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
        ),
    )
    if (
        _integer(raw["schema_version"], "installation.schema_version", 1) != 1
        or raw["protocol"] != PROTOCOL
    ):
        _fail("invalid-installation", "broker installation protocol/version mismatch")
    root = path.parent
    owners = {0, uid}
    installation: dict[str, Any] = {
        "installation_id": _string(
            raw["installation_id"], "installation.installation_id", HEX_64_RE
        ),
        "repository": _string(raw["repository"], "installation.repository", REPOSITORY_RE),
        "expected_capability_uid": _integer(
            raw["expected_capability_uid"], "installation.expected_capability_uid"
        ),
        "broker_key_id": _string(raw["broker_key_id"], "installation.broker_key_id", KEY_ID_RE),
        "pack_max_bytes": _integer(raw["pack_max_bytes"], "installation.pack_max_bytes", 1),
        "operation_timeout_seconds": _integer(
            raw["operation_timeout_seconds"], "installation.operation_timeout_seconds", 1
        ),
        "plan_lifetime_seconds": _integer(
            raw["plan_lifetime_seconds"],
            "installation.plan_lifetime_seconds",
            1,
        ),
        "test_only": raw["test_only"],
    }
    if not isinstance(installation["test_only"], bool):
        _fail("invalid-installation", "installation.test_only must be boolean")
    if installation["expected_capability_uid"] == uid:
        _fail("insecure-installation", "broker and capability-issuer principals must differ")
    if installation["pack_max_bytes"] > DEFAULT_PACK_MAX_BYTES * 16:
        _fail("invalid-installation", "installation pack limit is unreasonably large")
    installation["endpoint"] = _canonical_endpoint(
        raw["endpoint"], allow_local=installation["test_only"]
    )
    _require_repository_endpoint(
        installation["repository"], installation["endpoint"]
    )
    installation["broker_private_key"] = _resolve_secure_member(
        root, raw["broker_private_key"], label="broker private key", owners=owners, regular=True
    )
    if os.stat(installation["broker_private_key"], follow_symlinks=False).st_mode & 0o077:
        _fail("insecure-installation", "broker private key must not be accessible by group/other")
    installation["plan_store"] = _resolve_secure_member(
        root, raw["plan_store"], label="publication plan store", owners=owners, regular=False
    )
    installation["state_directory"] = _resolve_secure_member(
        root, raw["state_directory"], label="broker state directory", owners=owners, regular=False
    )
    if os.stat(installation["state_directory"], follow_symlinks=False).st_mode & 0o077:
        _fail("insecure-installation", "broker state directory must be private")
    signers = _object(raw["plan_signers"], "installation.plan_signers")
    if not signers:
        _fail("invalid-installation", "installation.plan_signers must not be empty")
    installation["plan_signers"] = {}
    for key_id, signer_value in signers.items():
        _string(key_id, "installation.plan_signers key", KEY_ID_RE)
        signer = _object(signer_value, f"installation.plan_signers.{key_id}")
        _exact_keys(
            signer,
            f"installation.plan_signers.{key_id}",
            ("public_key", "signer", "actor"),
        )
        installation["plan_signers"][key_id] = {
            "public_key": _resolve_secure_member(
                root,
                signer["public_key"],
                label=f"plan signer {key_id} public key",
                owners=owners,
                regular=True,
            ),
            "signer": _string(signer["signer"], f"plan signer {key_id}.signer", ACTOR_RE),
            "actor": _string(signer["actor"], f"plan signer {key_id}.actor", ACTOR_RE),
        }
    installation["authentication"] = _load_authentication(
        raw["authentication"], root, owners, installation
    )
    installation["protected_remote"] = _load_protected_remote(
        raw["protected_remote"], root, owners, installation
    )
    return installation


def _load_authentication(
    value: Any, root: Path, owners: set[int], installation: dict[str, Any]
) -> dict[str, Any]:
    auth = _object(value, "installation.authentication")
    mode = auth.get("mode")
    if mode == "https-askpass":
        _exact_keys(
            auth,
            "installation.authentication",
            ("mode", "askpass", "credential_file", "ca_file"),
        )
        if not installation["endpoint"].startswith("https://"):
            _fail("invalid-installation", "HTTPS askpass requires an HTTPS endpoint")
        result = {
            "mode": mode,
            "askpass": _resolve_secure_member(
                root, auth["askpass"], label="HTTPS askpass", owners=owners, regular=True
            ),
            "credential_file": _resolve_secure_member(
                root,
                auth["credential_file"],
                label="HTTPS credential file",
                owners=owners,
                regular=True,
            ),
        }
        if os.stat(result["credential_file"], follow_symlinks=False).st_mode & 0o077:
            _fail("insecure-installation", "HTTPS credential file must be private")
        if auth["ca_file"] is not None:
            result["ca_file"] = _resolve_secure_member(
                root, auth["ca_file"], label="HTTPS CA file", owners=owners, regular=True
            )
        return result
    if mode == "ssh-agent":
        _exact_keys(auth, "installation.authentication", ("mode", "agent_socket", "ssh_config"))
        if not installation["endpoint"].startswith("ssh://"):
            _fail("invalid-installation", "SSH agent requires an SSH endpoint")
        result = {
            "mode": mode,
            "agent_socket": _resolve_secure_member(
                root, auth["agent_socket"], label="SSH agent socket", owners=owners, regular=None
            ),
            "ssh_config": _resolve_secure_member(
                root, auth["ssh_config"], label="SSH config", owners=owners, regular=True
            ),
        }
        if not stat.S_ISSOCK(os.stat(result["agent_socket"], follow_symlinks=False).st_mode):
            _fail("insecure-installation", "SSH agent path must be a socket")
        return result
    if mode == "local-test":
        _exact_keys(auth, "installation.authentication", ("mode",))
        if not installation["test_only"] or not installation["endpoint"].startswith("file://"):
            _fail("invalid-installation", "local-test authentication is forbidden in production")
        return {"mode": mode}
    _fail("invalid-installation", "authentication mode is not allowlisted")


def _tree_digest(path: Path) -> str:
    digest = hashlib.sha256()
    if not path.exists():
        return digest.hexdigest()
    for child in sorted(path.rglob("*")):
        relative = child.relative_to(path).as_posix()
        metadata = os.lstat(child)
        if stat.S_ISLNK(metadata.st_mode):
            _fail("remote-state-changed", "protected remote contains a symlink")
        digest.update(relative.encode("utf-8") + b"\0")
        digest.update(f"{stat.S_IMODE(metadata.st_mode):o}\0".encode("ascii"))
        if stat.S_ISREG(metadata.st_mode):
            digest.update(child.read_bytes())
        elif not stat.S_ISDIR(metadata.st_mode):
            _fail("remote-state-changed", "protected remote hook tree has unsupported entries")
        digest.update(b"\0")
    return digest.hexdigest()


def _load_protected_remote(
    value: Any, root: Path, owners: set[int], installation: dict[str, Any]
) -> dict[str, Any] | None:
    if value is None:
        if installation["test_only"]:
            _fail("invalid-installation", "test installation requires a protected local remote")
        return None
    remote = _object(value, "installation.protected_remote")
    _exact_keys(
        remote,
        "installation.protected_remote",
        (
            "git_dir",
            "git_dir_device",
            "git_dir_inode",
            "objects_device",
            "objects_inode",
            "config_sha256",
            "hooks_sha256",
        ),
    )
    git_dir = _resolve_secure_member(
        root, remote["git_dir"], label="protected remote", owners=owners, regular=False
    )
    objects = git_dir / "objects"
    hooks = git_dir / "hooks"
    config = git_dir / "config"
    git_metadata = os.stat(git_dir, follow_symlinks=False)
    objects_metadata = _require_secure_path(
        objects, label="protected remote objects", allowed_owners=owners, regular=False
    )
    _require_secure_path(
        config,
        label="protected remote config",
        allowed_owners=owners,
        regular=True,
    )
    _require_secure_path(
        hooks,
        label="protected remote hooks",
        allowed_owners=owners,
        regular=False,
    )
    expected = {
        "git_dir": git_dir,
        "git_dir_device": _integer(remote["git_dir_device"], "remote.git_dir_device"),
        "git_dir_inode": _integer(remote["git_dir_inode"], "remote.git_dir_inode"),
        "objects_device": _integer(remote["objects_device"], "remote.objects_device"),
        "objects_inode": _integer(remote["objects_inode"], "remote.objects_inode"),
        "config_sha256": _string(remote["config_sha256"], "remote.config_sha256", HEX_64_RE),
        "hooks_sha256": _string(remote["hooks_sha256"], "remote.hooks_sha256", HEX_64_RE),
    }
    actual = (
        git_metadata.st_dev,
        git_metadata.st_ino,
        objects_metadata.st_dev,
        objects_metadata.st_ino,
    )
    wanted = (
        expected["git_dir_device"],
        expected["git_dir_inode"],
        expected["objects_device"],
        expected["objects_inode"],
    )
    if actual != wanted:
        _fail("remote-state-changed", "protected remote directory identity changed")
    if hashlib.sha256(config.read_bytes()).hexdigest() != expected["config_sha256"]:
        _fail("remote-state-changed", "protected remote config changed")
    if _tree_digest(hooks) != expected["hooks_sha256"]:
        _fail("remote-state-changed", "protected remote hooks changed")
    endpoint_path = Path(urllib.parse.urlsplit(installation["endpoint"]).path)
    if installation["test_only"] and endpoint_path != git_dir:
        _fail("invalid-installation", "local endpoint does not name the protected remote")
    return expected


def _check_protected_remote(remote: dict[str, Any] | None) -> None:
    if remote is None:
        return
    git_dir = remote["git_dir"]
    git_metadata = os.stat(git_dir, follow_symlinks=False)
    objects_metadata = os.stat(git_dir / "objects", follow_symlinks=False)
    if (
        git_metadata.st_dev != remote["git_dir_device"]
        or git_metadata.st_ino != remote["git_dir_inode"]
        or objects_metadata.st_dev != remote["objects_device"]
        or objects_metadata.st_ino != remote["objects_inode"]
        or hashlib.sha256((git_dir / "config").read_bytes()).hexdigest() != remote["config_sha256"]
        or _tree_digest(git_dir / "hooks") != remote["hooks_sha256"]
    ):
        _fail("remote-state-changed", "protected remote config/hooks/directory identity changed")


def _peer_credentials(connection: socket.socket) -> tuple[int, int, int]:
    if not hasattr(socket, "SO_PEERCRED"):
        _fail("peer-authentication-unavailable", "platform cannot authenticate Unix peers")
    try:
        raw = connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
    except OSError as error:
        raise BrokerError(
            "peer-authentication-failed", "cannot read Unix peer credentials"
        ) from error
    return struct.unpack("3i", raw)


def _require_unnamed_socket(connection: socket.socket) -> None:
    if (
        connection.family != socket.AF_UNIX
        or connection.type & socket.SOCK_STREAM != socket.SOCK_STREAM
    ):
        _fail("invalid-capability", "broker capability must be a Unix stream socket")
    if connection.getsockname() not in ("", b"") or connection.getpeername() not in ("", b""):
        _fail("invalid-capability", "named and abstract Unix endpoints are forbidden")


def _connection_disconnected(connection: socket.socket) -> bool:
    poller = select.poll()
    poller.register(connection.fileno(), select.POLLERR | select.POLLHUP | select.POLLNVAL)
    return bool(poller.poll(0))


def _read_request(
    connection: socket.socket, state_directory: Path, pack_limit: int
) -> tuple[dict[str, Any], Path]:
    header = bytearray()
    while b"\n" not in header:
        chunk = connection.recv(min(4096, REQUEST_MAX_BYTES + 1 - len(header)))
        if not chunk:
            _fail("client-disconnected", "client disconnected before request header")
        header.extend(chunk)
        if len(header) > REQUEST_MAX_BYTES:
            _fail("oversized-request", "broker request header exceeds its size limit")
    raw_header, initial_pack = bytes(header).split(b"\n", 1)
    request = _object(_parse_json(raw_header, "broker request"), "broker request")
    if raw_header + b"\n" != _normalized_json(request):
        _fail("invalid-json", "broker request must use canonical JSON")
    _exact_keys(
        request,
        "broker request",
        (
            "schema_version",
            "protocol",
            "request_nonce",
            "plan_identity",
            "pack_sha256",
            "pack_size",
            "deadline",
        ),
    )
    if (
        _integer(request["schema_version"], "request.schema_version", 1) != 1
        or request["protocol"] != PROTOCOL
    ):
        _fail("invalid-request", "broker request protocol/version mismatch")
    for field in ("request_nonce", "plan_identity", "pack_sha256"):
        _string(request[field], f"request.{field}", HEX_64_RE)
    pack_size = _integer(request["pack_size"], "request.pack_size", 1)
    if pack_size > pack_limit:
        _fail("oversized-pack", "broker request pack exceeds its size limit")
    deadline = _time(request["deadline"], "request.deadline")
    if deadline <= datetime_module.datetime.now(datetime_module.timezone.utc):
        _fail("request-expired", "broker request deadline expired")
    staging_root = state_directory / "staging"
    staging_root.mkdir(mode=0o700, exist_ok=True)
    token = request["request_nonce"]
    pack_path = staging_root / f"{token}.pack"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    digest = hashlib.sha256()
    remaining = pack_size
    try:
        fd = os.open(pack_path, flags, 0o600)
        with os.fdopen(fd, "wb") as stream:
            if len(initial_pack) > remaining:
                _fail("invalid-request", "broker request contains trailing bytes")
            stream.write(initial_pack)
            digest.update(initial_pack)
            remaining -= len(initial_pack)
            while remaining:
                chunk = connection.recv(min(65536, remaining))
                if not chunk:
                    _fail("client-disconnected", "client disconnected while sending object pack")
                stream.write(chunk)
                digest.update(chunk)
                remaining -= len(chunk)
            stream.flush()
            os.fsync(stream.fileno())
    except Exception:
        pack_path.unlink(missing_ok=True)
        raise
    if digest.hexdigest() != request["pack_sha256"]:
        pack_path.unlink(missing_ok=True)
        _fail("wrong-pack", "broker request object-pack digest differs")
    return request, pack_path


def _git_environment(installation: dict[str, Any], home: Path) -> dict[str, str]:
    environment = {
        "PATH": "/usr/bin:/bin",
        "LANG": "C",
        "LC_ALL": "C",
        "HOME": os.fspath(home),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_PROTOCOL_FROM_USER": "0",
        "GIT_NO_REPLACE_OBJECTS": "1",
        "GIT_NO_LAZY_FETCH": "1",
    }
    auth = installation["authentication"]
    if auth["mode"] == "https-askpass":
        environment.update(
            {
                "GIT_ASKPASS": os.fspath(auth["askpass"]),
                "WORKFLOW_PILOT_BROKER_CREDENTIAL_FILE": os.fspath(auth["credential_file"]),
            }
        )
        if "ca_file" in auth:
            environment["GIT_SSL_CAINFO"] = os.fspath(auth["ca_file"])
    elif auth["mode"] == "ssh-agent":
        environment.update(
            {
                "SSH_AUTH_SOCK": os.fspath(auth["agent_socket"]),
                "GIT_SSH_COMMAND": (
                    f"/usr/bin/ssh -F {shlex.quote(os.fspath(auth['ssh_config']))} "
                    "-o BatchMode=yes -o IdentitiesOnly=no"
                ),
            }
        )
    return environment


def _run_bounded(
    arguments: list[str],
    *,
    environment: dict[str, str],
    timeout: int,
    cwd: Path | None = None,
    stdin: BinaryIO | None = None,
    input_bytes: bytes | None = None,
    pass_fds: tuple[int, ...] = (),
) -> subprocess.CompletedProcess[bytes]:
    if stdin is not None and input_bytes is not None:
        _fail("process-hardening-failed", "bounded process has two stdin sources")

    def child_hardening() -> None:
        os.setsid()
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        resource.setrlimit(
            resource.RLIMIT_FSIZE,
            (SUBPROCESS_OUTPUT_MAX_BYTES, SUBPROCESS_OUTPUT_MAX_BYTES),
        )

    stdout_fd = _empty_memory_file("workflow-pilot-subprocess-stdout")
    stderr_fd = _empty_memory_file("workflow-pilot-subprocess-stderr")
    try:
        process = subprocess.Popen(
            arguments,
            cwd=cwd,
            env=environment,
            stdin=subprocess.PIPE if input_bytes is not None else stdin,
            stdout=stdout_fd,
            stderr=stderr_fd,
            preexec_fn=child_hardening,
            pass_fds=pass_fds,
        )
        stop_read, stop_write = os.pipe()
        broker_pid = os.getpid()
        try:
            with warnings.catch_warnings():
                warnings.filterwarnings(
                    "ignore",
                    message=r"This process .* is multi-threaded, use of fork.*",
                    category=DeprecationWarning,
                )
                watchdog_pid = os.fork()
        except OSError:
            os.killpg(process.pid, signal.SIGKILL)
            process.communicate()
            raise
        if watchdog_pid == 0:
            os.close(stop_write)
            os.close(stdout_fd)
            os.close(stderr_fd)
            for inherited_fd in pass_fds:
                os.close(inherited_fd)
            for stream in (process.stdin,):
                if stream is not None:
                    os.close(stream.fileno())

            def terminate_group(_signum: int, _frame: object) -> None:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                os._exit(0)

            signal.signal(signal.SIGTERM, terminate_group)
            if sys.platform.startswith("linux"):
                libc = ctypes.CDLL(None, use_errno=True)
                if libc.prctl(1, signal.SIGTERM, 0, 0, 0) != 0:
                    terminate_group(signal.SIGTERM, None)
            if os.getppid() != broker_pid:
                terminate_group(signal.SIGTERM, None)
            try:
                os.read(stop_read, 1)
            finally:
                os.close(stop_read)
            os._exit(0)
        os.close(stop_read)
        try:
            try:
                process.communicate(input=input_bytes, timeout=timeout)
            except subprocess.TimeoutExpired as error:
                os.killpg(process.pid, signal.SIGKILL)
                process.communicate()
                raise BrokerError("git-timeout", "bounded Git operation timed out") from error
        finally:
            try:
                os.write(stop_write, b"x")
            except OSError:
                pass
            os.close(stop_write)
            os.waitpid(watchdog_pid, 0)
    except OSError as error:
        raise BrokerError("git-unavailable", "cannot execute bounded Git operation") from error
    finally:
        os.lseek(stdout_fd, 0, os.SEEK_SET)
        stdout = _read_bounded_fd(stdout_fd, SUBPROCESS_OUTPUT_MAX_BYTES)
        os.lseek(stderr_fd, 0, os.SEEK_SET)
        stderr = _read_bounded_fd(stderr_fd, SUBPROCESS_OUTPUT_MAX_BYTES)
        os.close(stdout_fd)
        os.close(stderr_fd)
    if (
        len(stdout) >= SUBPROCESS_OUTPUT_MAX_BYTES
        or len(stderr) >= SUBPROCESS_OUTPUT_MAX_BYTES
    ):
        _fail("oversized-process-output", "bounded subprocess output exceeds its size limit")
    return subprocess.CompletedProcess(arguments, process.returncode, stdout, stderr)


def _git(
    installation: dict[str, Any],
    home: Path,
    arguments: list[str],
    *,
    cwd: Path | None = None,
    stdin: BinaryIO | None = None,
) -> bytes:
    command = [GIT, "--no-pager"]
    if installation["test_only"]:
        command.extend(["-c", "protocol.file.allow=always"])
    command.extend(arguments)
    completed = _run_bounded(
        command,
        environment=_git_environment(installation, home),
        timeout=installation["operation_timeout_seconds"],
        cwd=cwd,
        stdin=stdin,
    )
    if completed.returncode != 0:
        _fail("git-failed", "protected Git operation failed")
    return completed.stdout


def _pack_object_ids(
    staging: Path,
    installation: dict[str, Any],
    home: Path,
    pack_path: Path,
) -> set[str]:
    with pack_path.open("rb") as stream:
        _git(
            installation,
            home,
            ["-C", os.fspath(staging), "index-pack", "--stdin", "--strict"],
            stdin=stream,
        )
    indexes = list((staging / "objects" / "pack").glob("*.idx"))
    if len(indexes) != 1:
        _fail("wrong-pack", "object pack did not produce one protected index")
    output = _git(installation, home, ["verify-pack", "-v", os.fspath(indexes[0])])
    object_ids = set()
    for raw_line in output.decode("ascii", errors="strict").splitlines():
        fields = raw_line.split()
        if len(fields) >= 5 and reporter.SHA_RE.fullmatch(fields[0]):
            if fields[0] in object_ids:
                _fail("wrong-pack", "object pack contains a duplicate object")
            object_ids.add(fields[0])
    if not object_ids:
        _fail("wrong-pack", "object pack contains no objects")
    return object_ids


def _validate_object_closure(
    staging: Path, installation: dict[str, Any], home: Path, plan: dict[str, Any], pack_path: Path
) -> None:
    packed = _pack_object_ids(staging, installation, home, pack_path)
    planned = set(plan["object_ids"])
    if packed != planned:
        _fail("wrong-objects", "object pack differs from the signed exact object set")
    for field in ("new_authority_oid", "new_anchor_oid"):
        object_type = _git(
            installation,
            home,
            ["-C", os.fspath(staging), "cat-file", "-t", plan[field]],
        ).decode("ascii").strip()
        if object_type != "commit":
            _fail("wrong-objects", "published authority targets must be commit objects")
    closure_output = _git(
        installation,
        home,
        [
            "-C",
            os.fspath(staging),
            "rev-list",
            "--objects",
            "--no-object-names",
            plan["new_authority_oid"],
            plan["new_anchor_oid"],
        ],
    )
    closure = set(closure_output.decode("ascii").splitlines())
    if closure != planned:
        _fail("wrong-objects", "signed object set is not the exact reachable closure")


def _parse_remote_refs(raw: bytes, refs: tuple[str, str]) -> dict[str, str | None]:
    result: dict[str, str | None] = {ref: None for ref in refs}
    for line in raw.decode("ascii").splitlines():
        fields = line.split("\t")
        if len(fields) != 2 or fields[1] not in result or result[fields[1]] is not None:
            _fail("remote-readback", "remote returned an unexpected ref")
        _sha(fields[0], "remote ref object")
        result[fields[1]] = fields[0]
    return result


def _remote_refs(
    installation: dict[str, Any], home: Path, authority_ref: str, anchor_ref: str
) -> dict[str, str | None]:
    raw = _git(
        installation,
        home,
        ["ls-remote", "--refs", installation["endpoint"], authority_ref, anchor_ref],
    )
    return _parse_remote_refs(raw, (authority_ref, anchor_ref))


class ReplayJournal:
    def __init__(self, state_directory: Path, installation_id: str):
        self.state_directory = state_directory
        self.installation_id = installation_id
        self.lock_stream: BinaryIO | None = None
        self.entries: list[dict[str, Any]] = []
        self.last_hash = "0" * 64

    def __enter__(self) -> "ReplayJournal":
        self.state_directory.mkdir(mode=0o700, exist_ok=True)
        lock_path = self.state_directory / "journal.lock"
        self.lock_stream = lock_path.open("a+b")
        os.chmod(lock_path, 0o600)
        fcntl.flock(self.lock_stream.fileno(), fcntl.LOCK_EX)
        try:
            self._load()
        except BaseException:
            fcntl.flock(self.lock_stream.fileno(), fcntl.LOCK_UN)
            self.lock_stream.close()
            self.lock_stream = None
            raise
        return self

    def __exit__(self, *_exc: object) -> None:
        assert self.lock_stream is not None
        fcntl.flock(self.lock_stream.fileno(), fcntl.LOCK_UN)
        self.lock_stream.close()

    def _load(self) -> None:
        journal_path = self.state_directory / "journal.jsonl"
        anchor_path = self.state_directory / "journal.anchor"
        previous = "0" * 64
        entries: list[dict[str, Any]] = []
        if journal_path.exists():
            if journal_path.stat().st_size > JOURNAL_MAX_BYTES:
                _fail("journal-full", "replay journal exceeds its bounded size")
            for line_number, raw_line in enumerate(
                journal_path.read_bytes().splitlines(), 1
            ):
                entry = _object(
                    _parse_json(raw_line, f"journal line {line_number}"),
                    "journal entry",
                )
                entry_hash = entry.pop("entry_hash", None)
                event = entry.get("event")
                required = {
                    "installation_id",
                    "previous_hash",
                    "event",
                    "nonce",
                    "sequence",
                    "repository",
                    "issue",
                    "plan_identity",
                }
                if event == "completed":
                    required.add("result")
                if event not in {"reserved", "completed"} or set(entry) != required:
                    _fail("journal-corrupt", "replay journal entry fields differ")
                if (
                    entry.get("previous_hash") != previous
                    or entry.get("installation_id") != self.installation_id
                ):
                    _fail(
                        "journal-corrupt",
                        "replay journal chain or installation identity differs",
                    )
                calculated = hashlib.sha256(_normalized_json(entry)).hexdigest()
                if entry_hash != calculated:
                    _fail("journal-corrupt", "replay journal entry digest differs")
                entry["entry_hash"] = entry_hash
                previous = calculated
                entries.append(entry)
        if anchor_path.exists():
            anchor = _read_json_file(anchor_path, "replay journal anchor")
            _exact_keys(anchor, "replay journal anchor", ("installation_id", "last_hash"))
            if anchor["installation_id"] != self.installation_id or anchor["last_hash"] != previous:
                _fail("journal-rollback", "replay journal and durable anchor differ")
        elif entries:
            _fail("journal-rollback", "replay journal durable anchor is missing")
        self.entries = entries
        self.last_hash = previous

    def _append(self, event: dict[str, Any]) -> None:
        entry = {
            "installation_id": self.installation_id,
            "previous_hash": self.last_hash,
            **event,
        }
        entry_hash = hashlib.sha256(_normalized_json(entry)).hexdigest()
        encoded = _normalized_json({**entry, "entry_hash": entry_hash})
        journal_path = self.state_directory / "journal.jsonl"
        fd = os.open(
            journal_path,
            os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        try:
            _write_all(fd, encoded)
            os.fsync(fd)
        finally:
            os.close(fd)
        anchor_path = self.state_directory / "journal.anchor"
        replacement = self.state_directory / "journal.anchor.new"
        fd = os.open(
            replacement,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        try:
            _write_all(
                fd,
                _normalized_json(
                    {"installation_id": self.installation_id, "last_hash": entry_hash}
                ),
            )
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(replacement, anchor_path)
        directory_fd = os.open(self.state_directory, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        self.last_hash = entry_hash
        self.entries.append({**entry, "entry_hash": entry_hash})

    def reserve(self, plan: dict[str, Any], plan_identity: str) -> None:
        reservations = [entry for entry in self.entries if entry["event"] == "reserved"]
        if any(entry["nonce"] == plan["nonce"] for entry in reservations):
            _fail("replay", "publication plan nonce was already consumed")
        relevant = [
            entry
            for entry in reservations
            if entry["repository"] == plan["repository"] and entry["issue"] == plan["issue"]
        ]
        if relevant and plan["sequence"] <= max(entry["sequence"] for entry in relevant):
            _fail("replay", "publication plan sequence does not advance")
        self._append(
            {
                "event": "reserved",
                "nonce": plan["nonce"],
                "sequence": plan["sequence"],
                "repository": plan["repository"],
                "issue": plan["issue"],
                "plan_identity": plan_identity,
            }
        )

    def complete(self, plan: dict[str, Any], plan_identity: str, result: str) -> None:
        self._append(
            {
                "event": "completed",
                "nonce": plan["nonce"],
                "sequence": plan["sequence"],
                "repository": plan["repository"],
                "issue": plan["issue"],
                "plan_identity": plan_identity,
                "result": result,
            }
        )


def _publish(
    installation: dict[str, Any], plan: dict[str, Any], plan_identity: str, pack_path: Path
) -> dict[str, str]:
    state = installation["state_directory"]
    operation_root = state / "operations" / plan["nonce"]
    operation_root.parent.mkdir(mode=0o700, exist_ok=True)
    operation_root.mkdir(mode=0o700)
    home = operation_root / "home"
    staging = operation_root / "staging.git"
    home.mkdir(mode=0o700)
    try:
        _git(installation, home, ["init", "--bare", "--quiet", os.fspath(staging)])
        _validate_object_closure(staging, installation, home, plan, pack_path)
        refs = (plan["authority_ref"], plan["anchor_ref"])
        current = _remote_refs(installation, home, *refs)
        expected = {
            plan["authority_ref"]: plan["expected_authority_oid"],
            plan["anchor_ref"]: plan["expected_anchor_oid"],
        }
        if current != expected:
            _fail("stale-remote", "remote refs differ from the signed expected state")
        _check_protected_remote(installation["protected_remote"])
        for oid, ref in (
            (plan["new_authority_oid"], plan["authority_ref"]),
            (plan["new_anchor_oid"], plan["anchor_ref"]),
        ):
            _git(installation, home, ["-C", os.fspath(staging), "update-ref", ref, oid])
        leases = [
            f"--force-with-lease={ref}:{expected[ref] or ''}"
            for ref in refs
        ]
        refspecs = [
            f"{plan['new_authority_oid']}:{plan['authority_ref']}",
            f"{plan['new_anchor_oid']}:{plan['anchor_ref']}",
        ]
        _git(
            installation,
            home,
            [
                "-C",
                os.fspath(staging),
                "push",
                "--atomic",
                "--porcelain",
                "--no-verify",
                *leases,
                installation["endpoint"],
                *refspecs,
            ],
        )
        _check_protected_remote(installation["protected_remote"])
        readback = _remote_refs(installation, home, *refs)
        wanted = {
            plan["authority_ref"]: plan["new_authority_oid"],
            plan["anchor_ref"]: plan["new_anchor_oid"],
        }
        if readback != wanted:
            _fail("remote-readback", "exact remote ref readback differs after publication")
        return {ref: value for ref, value in readback.items() if value is not None}
    finally:
        shutil.rmtree(operation_root, ignore_errors=True)


def _load_plan(
    installation: dict[str, Any], plan_identity: str, now: datetime_module.datetime
) -> dict[str, Any]:
    _string(plan_identity, "request.plan_identity", HEX_64_RE)
    plan_path = installation["plan_store"] / f"{plan_identity}.json"
    _require_secure_path(
        plan_path,
        label="signed publication plan",
        allowed_owners={0, os.geteuid()},
        regular=True,
    )
    plan = _read_canonical_json_file(plan_path, "signed publication plan")
    if hashlib.sha256(_normalized_json(plan)).hexdigest() != plan_identity:
        _fail("invalid-plan", "signed publication plan identity differs")
    return _validate_plan(plan, installation, now=now)


def _response(
    installation: dict[str, Any],
    request: dict[str, Any],
    *,
    status: str,
    code: str,
    refs: dict[str, str] | None,
) -> dict[str, Any]:
    response = {
        "schema_version": 1,
        "protocol": PROTOCOL,
        "request_digest": _digest(request),
        "request_nonce": request["request_nonce"],
        "plan_identity": request["plan_identity"],
        "installation_id": installation["installation_id"],
        "broker_key_id": installation["broker_key_id"],
        "status": status,
        "code": code,
        "refs": refs,
        "completed_at": datetime_module.datetime.now(datetime_module.timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        ),
    }
    signature = _sign_ed25519(
        installation["broker_private_key"], _signed_payload(RESPONSE_DOMAIN, response)
    )
    response["signature"] = {
        "algorithm": "ed25519",
        "key_id": installation["broker_key_id"],
        "value": base64.b64encode(signature).decode("ascii"),
    }
    return response


def serve_connection(
    connection: socket.socket,
    installation: dict[str, Any],
    *,
    enforce_peer: bool = True,
) -> None:
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    _require_unnamed_socket(connection)
    if enforce_peer:
        _pid, uid, _gid = _peer_credentials(connection)
        if uid != installation["expected_capability_uid"] or uid == os.geteuid():
            _fail("peer-authentication-failed", "capability issuer principal is not authorized")
    connection.settimeout(installation["operation_timeout_seconds"])
    request: dict[str, Any] | None = None
    pack_path: Path | None = None
    try:
        request, pack_path = _read_request(
            connection, installation["state_directory"], installation["pack_max_bytes"]
        )
        now = datetime_module.datetime.now(datetime_module.timezone.utc)
        plan = _load_plan(installation, request["plan_identity"], now)
        if (
            request["pack_sha256"] != plan["pack_sha256"]
            or request["pack_size"] != plan["pack_size"]
        ):
            _fail("wrong-pack", "request pack does not match the signed plan")
        deadline = _time(request["deadline"], "request.deadline")
        if deadline > plan["_expires_time"]:
            _fail("request-expired", "request deadline exceeds the signed plan")
        if _connection_disconnected(connection):
            _fail("client-disconnected", "client disconnected before publication")
        with ReplayJournal(
            installation["state_directory"], installation["installation_id"]
        ) as journal:
            journal.reserve(plan, request["plan_identity"])
            try:
                refs = _publish(installation, plan, request["plan_identity"], pack_path)
            except BrokerError:
                journal.complete(plan, request["plan_identity"], "failed")
                raise
            journal.complete(plan, request["plan_identity"], "published")
        response = _response(installation, request, status="ok", code="published", refs=refs)
    except (BrokerError, OSError) as error:
        if request is None:
            raise
        code = error.code if isinstance(error, BrokerError) else "broker-io-failed"
        response = _response(
            installation, request, status="error", code=code, refs=None
        )
    finally:
        if pack_path is not None:
            pack_path.unlink(missing_ok=True)
    encoded = _normalized_json(response)
    if len(encoded) > RESPONSE_MAX_BYTES:
        _fail("oversized-response", "broker response exceeds its size limit")
    try:
        connection.sendall(encoded)
    except OSError:
        pass


def _load_client_installation(path: Path) -> dict[str, Any]:
    candidate_uid = os.geteuid()
    metadata = os.lstat(path)
    allowed = {0, metadata.st_uid}
    _require_secure_path(
        path,
        label="client installation",
        allowed_owners=allowed,
        reject_owner=candidate_uid,
        regular=True,
    )
    raw = _read_json_file(path, "client installation")
    _exact_keys(
        raw,
        "client installation",
        (
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
        ),
    )
    if (
        _integer(raw["schema_version"], "installation.schema_version", 1) != 1
        or raw["protocol"] != PROTOCOL
    ):
        _fail("invalid-installation", "client installation protocol/version mismatch")
    root = path.parent
    result = {
        "installation_id": _string(
            raw["installation_id"], "installation.installation_id", HEX_64_RE
        ),
        "repository": _string(raw["repository"], "installation.repository", REPOSITORY_RE),
        "endpoint": _canonical_endpoint(raw["endpoint"], allow_local=False),
        "expected_broker_uid": _integer(
            raw["expected_broker_uid"], "installation.expected_broker_uid"
        ),
        "expected_capability_uid": _integer(
            raw["expected_capability_uid"], "installation.expected_capability_uid"
        ),
        "broker_key_id": _string(raw["broker_key_id"], "installation.broker_key_id", KEY_ID_RE),
        "pack_max_bytes": _integer(raw["pack_max_bytes"], "installation.pack_max_bytes", 1),
        "operation_timeout_seconds": _integer(
            raw["operation_timeout_seconds"], "installation.operation_timeout_seconds", 1
        ),
    }
    if result["expected_broker_uid"] == candidate_uid:
        _fail("insecure-installation", "broker and candidate principals must differ")
    if result["expected_capability_uid"] == candidate_uid:
        _fail("insecure-installation", "capability issuer and candidate principals must differ")
    result["broker_public_key"] = _resolve_secure_member(
        root,
        raw["broker_public_key"],
        label="broker public key",
        owners=allowed,
        regular=True,
    )
    _require_repository_endpoint(result["repository"], result["endpoint"])
    return result


def _verify_response(
    response: dict[str, Any],
    request: dict[str, Any],
    installation: dict[str, Any],
    *,
    now: datetime_module.datetime,
) -> dict[str, str]:
    _exact_keys(
        response,
        "broker response",
        (
            "schema_version",
            "protocol",
            "request_digest",
            "request_nonce",
            "plan_identity",
            "installation_id",
            "broker_key_id",
            "status",
            "code",
            "refs",
            "completed_at",
            "signature",
        ),
    )
    if (
        _integer(response["schema_version"], "response.schema_version", 1) != 1
        or response["protocol"] != PROTOCOL
    ):
        _fail("invalid-response", "broker response protocol/version mismatch")
    if (
        response["request_digest"] != _digest(request)
        or response["request_nonce"] != request["request_nonce"]
        or response["plan_identity"] != request["plan_identity"]
        or response["installation_id"] != installation["installation_id"]
        or response["broker_key_id"] != installation["broker_key_id"]
    ):
        _fail("invalid-response", "broker response does not bind the request/installation")
    completed = _time(response["completed_at"], "response.completed_at")
    deadline = _time(request["deadline"], "request.deadline")
    if completed > deadline or completed > now + datetime_module.timedelta(seconds=5):
        _fail("invalid-response", "broker response exceeds its request deadline")
    signature_record = _object(response["signature"], "response.signature")
    key_id, signature = _load_signature_record(signature_record, "response.signature")
    if key_id != installation["broker_key_id"]:
        _fail("invalid-signature", "broker response signing key differs")
    _verify_ed25519(
        installation["broker_public_key"],
        _signed_payload(RESPONSE_DOMAIN, response),
        signature,
    )
    if response["status"] != "ok" or response["code"] != "published":
        _fail("broker-rejected", f"broker rejected publication: {response['code']}")
    refs = _object(response["refs"], "response.refs")
    for name, oid in refs.items():
        _string(name, "response ref")
        _sha(oid, f"response ref {name}")
    if len(refs) != 2:
        _fail("invalid-response", "broker response must contain exactly two refs")
    return refs


def publish_via_connection(
    connection: socket.socket,
    installation: dict[str, Any],
    plan_identity: str,
    pack_path: Path,
    *,
    enforce_peer: bool = True,
) -> dict[str, str]:
    _require_unnamed_socket(connection)
    if enforce_peer:
        _pid, uid, _gid = _peer_credentials(connection)
        if uid != installation["expected_capability_uid"] or uid == os.geteuid():
            _fail("peer-authentication-failed", "capability issuer principal is not authorized")
    try:
        pack_size = pack_path.stat().st_size
    except OSError as error:
        raise BrokerError("pack-unavailable", "cannot inspect object pack") from error
    if pack_size <= 0 or pack_size > installation["pack_max_bytes"]:
        _fail("oversized-pack", "object pack size is outside the installation contract")
    digest = hashlib.sha256()
    with pack_path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(65536), b""):
            digest.update(chunk)
    now = datetime_module.datetime.now(datetime_module.timezone.utc)
    deadline_time = now + datetime_module.timedelta(
        seconds=installation["operation_timeout_seconds"]
    )
    request = {
        "schema_version": 1,
        "protocol": PROTOCOL,
        "request_nonce": os.urandom(32).hex(),
        "plan_identity": _string(plan_identity, "plan identity", HEX_64_RE),
        "pack_sha256": digest.hexdigest(),
        "pack_size": pack_size,
        "deadline": deadline_time.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    connection.settimeout(installation["operation_timeout_seconds"])
    connection.sendall(_normalized_json(request))
    with pack_path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(65536), b""):
            connection.sendall(chunk)
    raw = bytearray()
    while True:
        chunk = connection.recv(min(65536, RESPONSE_MAX_BYTES + 1 - len(raw)))
        if not chunk:
            break
        raw.extend(chunk)
        if len(raw) > RESPONSE_MAX_BYTES:
            _fail("oversized-response", "broker response exceeds its size limit")
    response = _object(_parse_json(bytes(raw), "broker response"), "broker response")
    if bytes(raw) != _normalized_json(response):
        _fail("invalid-json", "broker response must use canonical JSON")
    return _verify_response(
        response,
        request,
        installation,
        now=datetime_module.datetime.now(datetime_module.timezone.utc),
    )


def _set_parent_death_signal() -> None:
    if not sys.platform.startswith("linux"):
        return
    expected_parent = os.getppid()
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(1, signal.SIGKILL, 0, 0, 0) != 0:
        _fail("process-hardening-failed", "cannot set broker parent-death signal")
    if os.getppid() != expected_parent:
        _fail("process-hardening-failed", "broker parent changed during launch")


def _socket_from_fd(fd: int) -> socket.socket:
    try:
        connection = socket.socket(fileno=fd)
    except OSError as error:
        raise BrokerError("invalid-capability", "connection fd is not a socket") from error
    os.set_inheritable(fd, False)
    return connection


def _close_unrelated_fds(allowed: set[int]) -> None:
    descriptor_root = Path("/proc/self/fd")
    try:
        descriptors = [int(entry.name) for entry in descriptor_root.iterdir()]
    except (OSError, ValueError) as error:
        raise BrokerError(
            "process-hardening-failed", "cannot enumerate inherited descriptors"
        ) from error
    for descriptor in descriptors:
        if descriptor in allowed:
            continue
        try:
            os.close(descriptor)
        except OSError:
            pass


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="One-shot authenticated exact two-ref Git publication broker/client."
    )
    subparsers = parser.add_subparsers(dest="mode", required=True)
    service = subparsers.add_parser("serve")
    service.add_argument("--installation", required=True, type=Path)
    service.add_argument("--connection-fd", required=True, type=int)
    client = subparsers.add_parser("publish")
    client.add_argument("--installation", required=True, type=Path)
    client.add_argument("--connection-fd", required=True, type=int)
    client.add_argument("--plan-identity", required=True)
    client.add_argument("--pack", required=True, type=Path)
    preflight = subparsers.add_parser("preflight")
    preflight.add_argument("--installation", required=True, type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    try:
        if arguments.mode == "serve":
            _close_unrelated_fds({0, 1, 2, arguments.connection_fd})
            _set_parent_death_signal()
            installation = _load_broker_installation(arguments.installation.resolve(strict=True))
            with _socket_from_fd(arguments.connection_fd) as connection:
                serve_connection(connection, installation)
            return 0
        installation = _load_client_installation(arguments.installation.resolve(strict=True))
        if arguments.mode == "preflight":
            print(
                json.dumps(
                    {
                        "protocol": PROTOCOL,
                        "installation_id": installation["installation_id"],
                        "repository": installation["repository"],
                        "endpoint": installation["endpoint"],
                        "expected_broker_uid": installation["expected_broker_uid"],
                        "expected_capability_uid": installation["expected_capability_uid"],
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                )
            )
            return 0
        _close_unrelated_fds({0, 1, 2, arguments.connection_fd})
        with _socket_from_fd(arguments.connection_fd) as connection:
            refs = publish_via_connection(
                connection, installation, arguments.plan_identity, arguments.pack
            )
        print(json.dumps(refs, sort_keys=True, separators=(",", ":")))
        return 0
    except (BrokerError, OSError) as error:
        code = error.code if isinstance(error, BrokerError) else "broker-io-failed"
        print(f"git-publication-broker: {code}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
