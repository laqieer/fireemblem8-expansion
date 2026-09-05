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
import time
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO, Literal

from . import reporter, signed_schema


PROTOCOL = "workflow-pilot-authenticated-git-broker-v1"
PLAN_DOMAIN = b"workflow-pilot-git-publication-plan-v1\0"
CAPABILITY_DOMAIN = b"workflow-pilot-git-publication-capability-v1\0"
RESPONSE_DOMAIN = b"workflow-pilot-git-publication-response-v1\0"
REQUEST_MAX_BYTES = 16 * 1024
RESPONSE_MAX_BYTES = 64 * 1024
DEFAULT_PACK_MAX_BYTES = 64 * 1024 * 1024
SUBPROCESS_OUTPUT_MAX_BYTES = 1024 * 1024
CONFIG_MAX_BYTES = 1024 * 1024
JOURNAL_MAX_BYTES = 16 * 1024 * 1024
CAPABILITY_MEMFD_NAME = "/memfd:workflow-pilot-git-capability"
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


class IndeterminatePublication(BrokerError):
    """A transmitted push needs exact remote reconciliation."""


ReconciliationKind = Literal[
    "committed-late",
    "safe-failed",
    "security-hold",
    "indeterminate",
]


@dataclass(frozen=True)
class PublishedOutcome:
    refs: dict[str, str]

    @property
    def kind(self) -> str:
        return "published"

    @property
    def exit_code(self) -> int:
        return 0

    @property
    def retry_disposition(self) -> str:
        return "complete"


@dataclass(frozen=True)
class ReconciliationOutcome:
    kind: ReconciliationKind
    refs: dict[str, str | None] | None

    def __post_init__(self) -> None:
        if self.kind not in {
            "committed-late",
            "safe-failed",
            "security-hold",
            "indeterminate",
        }:
            raise ValueError("reconciliation outcome is not in the closed union")
        if self.kind == "indeterminate" and self.refs is not None:
            raise ValueError("indeterminate reconciliation must not claim refs")
        if self.kind != "indeterminate" and self.refs is None:
            raise ValueError("resolved reconciliation must preserve exact refs")

    @property
    def exit_code(self) -> int:
        return {
            "committed-late": 0,
            "safe-failed": 3,
            "security-hold": 4,
            "indeterminate": 5,
        }[self.kind]

    @property
    def retry_disposition(self) -> str:
        return {
            "committed-late": "complete",
            "safe-failed": "new-higher-sequence-plan-allowed",
            "security-hold": "security-incident-hold",
            "indeterminate": "reconciliation-required",
        }[self.kind]


PublicationOutcome = PublishedOutcome | ReconciliationOutcome


def _fail(code: str, message: str) -> None:
    raise BrokerError(code, message)


class OperationDeadline:
    def __init__(self, absolute: datetime_module.datetime):
        now = datetime_module.datetime.now(datetime_module.timezone.utc)
        remaining = (absolute - now).total_seconds()
        if remaining <= 0:
            _fail("operation-expired", "effective operation deadline expired")
        self.absolute = absolute
        self.monotonic_end = time.monotonic() + remaining

    def check(self, phase: str) -> None:
        now = datetime_module.datetime.now(datetime_module.timezone.utc)
        if now >= self.absolute or time.monotonic() >= self.monotonic_end:
            _fail("operation-expired", f"effective deadline expired {phase}")

    def remaining(self, phase: str, maximum: float) -> float:
        self.check(phase)
        wall = (
            self.absolute
            - datetime_module.datetime.now(datetime_module.timezone.utc)
        ).total_seconds()
        monotonic = self.monotonic_end - time.monotonic()
        remaining = min(wall, monotonic, maximum)
        if remaining <= 0:
            _fail("operation-expired", f"effective deadline expired {phase}")
        return remaining

    def text(self) -> str:
        return self.absolute.strftime("%Y-%m-%dT%H:%M:%SZ")


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


def _validate_private_state_fd(fd: int, label: str) -> os.stat_result:
    metadata = os.fstat(fd)
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or metadata.st_mode & 0o077
    ):
        _fail(
            "journal-corrupt",
            f"{label} is not a broker-owned private regular file",
        )
    return metadata


def _verify_ed25519(
    public_key: Path,
    payload: bytes,
    signature: bytes,
    *,
    deadline: OperationDeadline | None = None,
) -> None:
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
            deadline=deadline,
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


def _read_canonical_json_fd(
    fd: int,
    label: str,
    maximum: int = REQUEST_MAX_BYTES,
) -> dict[str, Any]:
    metadata = os.fstat(fd)
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > maximum:
        _fail("oversized-record", f"{label} is not a bounded regular file")
    raw = os.pread(fd, metadata.st_size, 0)
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


def _read_sealed_capability(fd: int) -> dict[str, Any]:
    try:
        metadata = os.fstat(fd)
        link = os.readlink(f"/proc/self/fd/{fd}")
        seals = fcntl.fcntl(fd, fcntl.F_GET_SEALS)
    except OSError as error:
        raise BrokerError(
            "invalid-capability", "cannot inspect protected launch capability"
        ) from error
    required_seals = (
        fcntl.F_SEAL_SEAL
        | fcntl.F_SEAL_SHRINK
        | fcntl.F_SEAL_GROW
        | fcntl.F_SEAL_WRITE
    )
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_size <= 0
        or metadata.st_size > REQUEST_MAX_BYTES
        or not link.startswith(CAPABILITY_MEMFD_NAME)
        or seals & required_seals != required_seals
    ):
        _fail(
            "invalid-capability",
            "launch capability must be an exact sealed anonymous record",
        )
    try:
        raw = os.pread(fd, metadata.st_size, 0)
    except OSError as error:
        raise BrokerError(
            "invalid-capability", "cannot read protected launch capability"
        ) from error
    capability = _object(_parse_json(raw, "launch capability"), "launch capability")
    if raw != _normalized_json(capability):
        _fail("invalid-capability", "launch capability must use canonical JSON")
    return capability


def _validate_capability(
    capability: dict[str, Any],
    installation: dict[str, Any],
    *,
    now: datetime_module.datetime,
    deadline: OperationDeadline | None = None,
) -> dict[str, Any]:
    try:
        signed_schema.validate_record(
            capability, "capability", "launch capability"
        )
    except signed_schema.SchemaError as error:
        raise BrokerError("invalid-capability", str(error)) from error
    _exact_keys(
        capability,
        "launch capability",
        (
            "schema_version",
            "protocol",
            "installation_id",
            "repository",
            "issue",
            "plan_identity",
            "operation",
            "capability_nonce",
            "issued_at",
            "expires_at",
            "signer",
            "actor",
            "signature",
        ),
    )
    if (
        _integer(
            capability["schema_version"], "capability.schema_version", 1
        )
        != 1
        or capability["protocol"] != PROTOCOL
    ):
        _fail("invalid-capability", "launch capability protocol/version mismatch")
    installation_id = _string(
        capability["installation_id"],
        "capability.installation_id",
        HEX_64_RE,
    )
    repository = _string(
        capability["repository"], "capability.repository", REPOSITORY_RE
    )
    issue = _integer(capability["issue"], "capability.issue", 1)
    plan_identity = _string(
        capability["plan_identity"], "capability.plan_identity", HEX_64_RE
    )
    if capability["operation"] not in {"preflight", "publish", "reconcile"}:
        _fail("invalid-capability", "launch capability operation is not allowlisted")
    _string(
        capability["capability_nonce"],
        "capability.capability_nonce",
        HEX_64_RE,
    )
    issued_at = _time(capability["issued_at"], "capability.issued_at")
    expires_at = _time(capability["expires_at"], "capability.expires_at")
    if issued_at > now or expires_at <= now or expires_at <= issued_at:
        _fail("invalid-capability", "launch capability is not currently valid")
    if (
        expires_at - issued_at
    ).total_seconds() > installation["plan_lifetime_seconds"]:
        _fail(
            "invalid-capability",
            "launch capability lifetime exceeds the bounded contract",
        )
    if (
        installation_id != installation["installation_id"]
        or repository != installation["repository"]
    ):
        _fail(
            "invalid-capability",
            "launch capability does not bind this installation",
        )
    signer = _string(capability["signer"], "capability.signer", ACTOR_RE)
    actor = _string(capability["actor"], "capability.actor", ACTOR_RE)
    signature_record = _object(
        capability["signature"], "capability.signature"
    )
    key_id, signature = _load_signature_record(
        signature_record, "capability.signature"
    )
    authority = installation["plan_signers"].get(key_id)
    if (
        authority is None
        or authority["signer"] != signer
        or authority["actor"] != actor
    ):
        _fail(
            "invalid-capability",
            "launch capability signer/actor is not installed",
        )
    verification_deadline = OperationDeadline(
        min(expires_at, deadline.absolute) if deadline is not None else expires_at
    )
    _verify_ed25519(
        authority["public_key"],
        _signed_payload(CAPABILITY_DOMAIN, capability),
        signature,
        deadline=verification_deadline,
    )
    verification_deadline.check("after capability signature validation")
    capability["_issue"] = issue
    capability["_plan_identity"] = plan_identity
    capability["_expires_time"] = expires_at
    return capability


def _plan_ref(issue: int, kind: str) -> str:
    if kind == "authority":
        return f"refs/heads/workflow-pilot/issue-{issue}/authority"
    return f"refs/tags/workflow-pilot/issue-{issue}/anchor"


def _validate_plan(
    plan: dict[str, Any],
    installation: dict[str, Any],
    *,
    now: datetime_module.datetime,
    deadline: OperationDeadline | None = None,
    allow_expired: bool = False,
) -> dict[str, Any]:
    try:
        signed_schema.validate_record(
            plan, "plan", "signed publication plan"
        )
    except signed_schema.SchemaError as error:
        raise BrokerError("invalid-plan", str(error)) from error
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
    if issued_at > now or (expires_at <= now and not allow_expired):
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
    if allow_expired:
        if deadline is None:
            _fail(
                "invalid-plan",
                "expired plan verification requires a reconcile deadline",
            )
        verification_deadline = deadline
    else:
        verification_deadline = OperationDeadline(
            min(expires_at, deadline.absolute)
            if deadline is not None
            else expires_at
        )
    _verify_ed25519(
        authority["public_key"],
        _signed_payload(PLAN_DOMAIN, plan),
        signature_bytes,
        deadline=verification_deadline,
    )
    verification_deadline.check("after plan signature validation")
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
            "candidate_uid",
            "broker_key_id",
            "broker_private_key",
            "deadline_exec",
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
        "candidate_uid": _integer(
            raw["candidate_uid"], "installation.candidate_uid"
        ),
        "broker_key_id": _string(raw["broker_key_id"], "installation.broker_key_id", KEY_ID_RE),
        "pack_max_bytes": _integer(raw["pack_max_bytes"], "installation.pack_max_bytes", 1),
        "operation_timeout_seconds": _integer(
            raw["operation_timeout_seconds"], "installation.operation_timeout_seconds", 1
        ),
        "reconciliation_timeout_seconds": _integer(
            raw["reconciliation_timeout_seconds"],
            "installation.reconciliation_timeout_seconds",
            1,
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
    if installation["candidate_uid"] in {
        uid,
        installation["expected_capability_uid"],
    }:
        _fail(
            "insecure-installation",
            "candidate, broker, and capability issuer principals must differ",
        )
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
    installation["deadline_exec"] = _resolve_secure_member(
        root,
        raw["deadline_exec"],
        label="deadline exec helper",
        owners=owners,
        regular=True,
    )
    helper_mode = os.stat(
        installation["deadline_exec"], follow_symlinks=False
    ).st_mode
    if helper_mode & 0o022 or not helper_mode & 0o100:
        _fail(
            "insecure-installation",
            "deadline exec helper must be owner-executable and immutable to others",
        )
    installation["plan_store"] = _resolve_secure_member(
        root, raw["plan_store"], label="publication plan store", owners=owners, regular=False
    )
    installation["plan_store_fd"] = os.open(
        installation["plan_store"],
        os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0),
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
    installation["_authority_owners"] = owners
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
    if not path.exists():
        return hashlib.sha256().hexdigest()
    directory_fd = os.open(
        path,
        os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        return _digest_directory_fd(directory_fd)
    finally:
        os.close(directory_fd)


def _validate_tree_entry(
    metadata: os.stat_result,
    *,
    label: str,
    owners: set[int],
    candidate_uid: int,
    mutable: bool,
) -> None:
    if metadata.st_uid == candidate_uid or metadata.st_uid not in owners:
        _fail("insecure-installation", f"{label} owner is not broker authority")
    if metadata.st_mode & 0o022:
        _fail("insecure-installation", f"{label} is group/world writable")
    if mutable and metadata.st_uid != os.geteuid():
        _fail(
            "insecure-installation",
            f"{label} mutable storage is not owned by the broker principal",
        )
    if not (stat.S_ISREG(metadata.st_mode) or stat.S_ISDIR(metadata.st_mode)):
        _fail("insecure-installation", f"{label} has an unsupported file type")


def _reopen_directory_fd(directory_fd: int) -> int:
    return os.open(
        f"/proc/self/fd/{directory_fd}",
        os.O_RDONLY | os.O_DIRECTORY,
    )


def _audit_directory_fd(
    directory_fd: int,
    *,
    label: str,
    owners: set[int],
    candidate_uid: int,
    mutable: bool,
    deadline: OperationDeadline | None = None,
) -> None:
    pending = [(_reopen_directory_fd(directory_fd), label)]
    visited = 0
    try:
        while pending:
            if deadline is not None:
                deadline.check(f"while auditing {label}")
            current_fd, current_label = pending.pop()
            try:
                current_metadata = os.fstat(current_fd)
                _validate_tree_entry(
                    current_metadata,
                    label=current_label,
                    owners=owners,
                    candidate_uid=candidate_uid,
                    mutable=mutable,
                )
                for name in sorted(os.listdir(current_fd)):
                    visited += 1
                    if deadline is not None and visited % 256 == 0:
                        deadline.check(f"while auditing {label}")
                    if visited > 100000:
                        _fail(
                            "insecure-installation",
                            f"{label} exceeds its bounded entry count",
                        )
                    metadata = os.stat(
                        name, dir_fd=current_fd, follow_symlinks=False
                    )
                    entry_label = f"{current_label}/{name}"
                    _validate_tree_entry(
                        metadata,
                        label=entry_label,
                        owners=owners,
                        candidate_uid=candidate_uid,
                        mutable=mutable,
                    )
                    if stat.S_ISDIR(metadata.st_mode):
                        child_fd = os.open(
                            name,
                            os.O_RDONLY
                            | os.O_DIRECTORY
                            | getattr(os, "O_NOFOLLOW", 0),
                            dir_fd=current_fd,
                        )
                        pending.append((child_fd, entry_label))
            finally:
                os.close(current_fd)
    except BaseException:
        for pending_fd, _pending_label in pending:
            os.close(pending_fd)
        raise


def _digest_directory_fd(
    directory_fd: int,
    deadline: OperationDeadline | None = None,
) -> str:
    digest = hashlib.sha256()
    pending = [(_reopen_directory_fd(directory_fd), "")]
    try:
        while pending:
            if deadline is not None:
                deadline.check("while hashing protected hooks")
            current_fd, prefix = pending.pop()
            try:
                for name in sorted(os.listdir(current_fd)):
                    relative = f"{prefix}/{name}" if prefix else name
                    metadata = os.stat(
                        name, dir_fd=current_fd, follow_symlinks=False
                    )
                    digest.update(relative.encode("utf-8") + b"\0")
                    digest.update(
                        f"{stat.S_IMODE(metadata.st_mode):o}\0".encode("ascii")
                    )
                    if stat.S_ISDIR(metadata.st_mode):
                        child_fd = os.open(
                            name,
                            os.O_RDONLY
                            | os.O_DIRECTORY
                            | getattr(os, "O_NOFOLLOW", 0),
                            dir_fd=current_fd,
                        )
                        pending.append((child_fd, relative))
                    elif stat.S_ISREG(metadata.st_mode):
                        file_fd = os.open(
                            name,
                            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                            dir_fd=current_fd,
                        )
                        try:
                            while True:
                                if deadline is not None:
                                    deadline.check(
                                        "while hashing protected hooks"
                                    )
                                chunk = os.read(file_fd, 65536)
                                if not chunk:
                                    break
                                digest.update(chunk)
                        finally:
                            os.close(file_fd)
                    else:
                        _fail(
                            "remote-state-changed",
                            "protected hook tree contains an unsupported entry",
                        )
                    digest.update(b"\0")
            finally:
                os.close(current_fd)
    except BaseException:
        for pending_fd, _prefix in pending:
            os.close(pending_fd)
        raise
    return digest.hexdigest()


def _bind_protected_remote_descriptors(
    remote: dict[str, Any],
    *,
    owners: set[int],
    candidate_uid: int,
) -> dict[str, Any]:
    git_dir = remote["git_dir"]
    git_dir_fd = os.open(
        git_dir,
        os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0),
    )
    descriptors = {"git_dir_fd": git_dir_fd}
    try:
        _validate_tree_entry(
            os.fstat(git_dir_fd),
            label="protected remote Git directory",
            owners=owners,
            candidate_uid=candidate_uid,
            mutable=True,
        )
        for name, directory in (
            ("objects_fd", True),
            ("refs_fd", True),
            ("hooks_fd", True),
            ("config_fd", False),
        ):
            component = name.removesuffix("_fd")
            flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
            if directory:
                flags |= os.O_DIRECTORY
            descriptors[name] = os.open(
                component, flags, dir_fd=git_dir_fd
            )
        _audit_directory_fd(
            descriptors["objects_fd"],
            label="protected remote objects",
            owners=owners,
            candidate_uid=candidate_uid,
            mutable=True,
        )
        _audit_directory_fd(
            descriptors["refs_fd"],
            label="protected remote refs",
            owners=owners,
            candidate_uid=candidate_uid,
            mutable=True,
        )
        _audit_directory_fd(
            descriptors["hooks_fd"],
            label="protected remote hooks",
            owners=owners,
            candidate_uid=candidate_uid,
            mutable=False,
        )
        _validate_tree_entry(
            os.fstat(descriptors["config_fd"]),
            label="protected remote config",
            owners=owners,
            candidate_uid=candidate_uid,
            mutable=False,
        )
        _reject_object_alternates(descriptors["objects_fd"])
        for redirect_name in ("commondir", "gitdir", "config.worktree"):
            try:
                os.stat(
                    redirect_name,
                    dir_fd=git_dir_fd,
                    follow_symlinks=False,
                )
            except FileNotFoundError:
                continue
            _fail(
                "insecure-installation",
                "protected remote repository redirection is forbidden",
            )
        try:
            packed_refs_fd = os.open(
                "packed-refs",
                os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=git_dir_fd,
            )
        except FileNotFoundError:
            packed_refs_fd = None
        if packed_refs_fd is not None:
            descriptors["packed_refs_fd"] = packed_refs_fd
            _validate_tree_entry(
                os.fstat(packed_refs_fd),
                label="protected remote packed refs",
                owners=owners,
                candidate_uid=candidate_uid,
                mutable=True,
            )
        config_size = os.fstat(descriptors["config_fd"]).st_size
        if config_size > CONFIG_MAX_BYTES:
            _fail(
                "insecure-installation",
                "protected remote config exceeds its size limit",
            )
        config = os.pread(
            descriptors["config_fd"],
            config_size,
            0,
        )
        lowered = config.lower()
        for forbidden in (
            b"[include",
            b"hookspath",
            b"alternaterefscommand",
            b"fsmonitor",
            b"refstorage",
            b"worktree",
        ):
            if forbidden in lowered:
                _fail(
                    "insecure-installation",
                    "protected remote config contains an external execution seam",
                )
    except BaseException:
        for descriptor in descriptors.values():
            os.close(descriptor)
        raise
    return {
        **remote,
        **descriptors,
        "_owners": owners,
        "_candidate_uid": candidate_uid,
        "_packed_refs_present": "packed_refs_fd" in descriptors,
    }


def _reject_object_alternates(objects_fd: int) -> None:
    try:
        info_fd = os.open(
            "info",
            os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=objects_fd,
        )
    except FileNotFoundError:
        return
    try:
        for name in ("alternates", "http-alternates"):
            try:
                os.stat(name, dir_fd=info_fd, follow_symlinks=False)
            except FileNotFoundError:
                continue
            _fail(
                "insecure-installation",
                "protected remote object alternates are forbidden",
            )
    finally:
        os.close(info_fd)


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
    return _bind_protected_remote_descriptors(
        expected,
        owners=owners,
        candidate_uid=installation["candidate_uid"],
    )


def _check_protected_remote(
    remote: dict[str, Any] | None,
    deadline: OperationDeadline | None = None,
) -> None:
    if remote is None:
        return
    git_metadata = os.fstat(remote["git_dir_fd"])
    objects_metadata = os.fstat(remote["objects_fd"])
    config_metadata = os.fstat(remote["config_fd"])
    if config_metadata.st_size > CONFIG_MAX_BYTES:
        _fail(
            "remote-state-changed",
            "protected remote config exceeds its size limit",
        )
    config = os.pread(remote["config_fd"], config_metadata.st_size, 0)
    _audit_directory_fd(
        remote["objects_fd"],
        label="protected remote objects",
        owners=remote["_owners"],
        candidate_uid=remote["_candidate_uid"],
        mutable=True,
        deadline=deadline,
    )
    _reject_object_alternates(remote["objects_fd"])
    for redirect_name in ("commondir", "gitdir", "config.worktree"):
        try:
            os.stat(
                redirect_name,
                dir_fd=remote["git_dir_fd"],
                follow_symlinks=False,
            )
        except FileNotFoundError:
            continue
        _fail(
            "remote-state-changed",
            "protected remote repository redirection appeared",
        )
    try:
        packed_refs_metadata = os.stat(
            "packed-refs",
            dir_fd=remote["git_dir_fd"],
            follow_symlinks=False,
        )
    except FileNotFoundError:
        packed_refs_metadata = None
    if (packed_refs_metadata is not None) != remote["_packed_refs_present"]:
        _fail("remote-state-changed", "protected remote packed refs presence changed")
    if packed_refs_metadata is not None:
        _validate_tree_entry(
            packed_refs_metadata,
            label="protected remote packed refs",
            owners=remote["_owners"],
            candidate_uid=remote["_candidate_uid"],
            mutable=True,
        )
    _audit_directory_fd(
        remote["refs_fd"],
        label="protected remote refs",
        owners=remote["_owners"],
        candidate_uid=remote["_candidate_uid"],
        mutable=True,
    )
    _audit_directory_fd(
        remote["hooks_fd"],
        label="protected remote hooks",
        owners=remote["_owners"],
        candidate_uid=remote["_candidate_uid"],
        mutable=False,
        deadline=deadline,
    )
    if (
        git_metadata.st_dev != remote["git_dir_device"]
        or git_metadata.st_ino != remote["git_dir_inode"]
        or objects_metadata.st_dev != remote["objects_device"]
        or objects_metadata.st_ino != remote["objects_inode"]
        or hashlib.sha256(config).hexdigest() != remote["config_sha256"]
        or _digest_directory_fd(remote["hooks_fd"], deadline)
        != remote["hooks_sha256"]
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


def _socket_timeout(
    connection: socket.socket,
    deadline: OperationDeadline | None,
    phase: str,
    maximum: float,
) -> None:
    timeout = deadline.remaining(phase, maximum) if deadline is not None else maximum
    connection.settimeout(timeout)


def _recv_exact(
    connection: socket.socket,
    size: int,
    *,
    deadline: OperationDeadline | None,
    phase: str,
) -> bytes:
    result = bytearray()
    while len(result) < size:
        _socket_timeout(connection, deadline, phase, 30)
        try:
            chunk = connection.recv(size - len(result))
        except (OSError, TimeoutError) as error:
            raise BrokerError(
                "client-disconnected", f"connection failed {phase}"
            ) from error
        if not chunk:
            _fail("client-disconnected", f"connection closed {phase}")
        result.extend(chunk)
    return bytes(result)


def _recv_frame(
    connection: socket.socket,
    *,
    maximum: int,
    label: str,
    deadline: OperationDeadline | None,
) -> tuple[dict[str, Any], bytes]:
    prefix = _recv_exact(
        connection, 4, deadline=deadline, phase=f"before {label} length"
    )
    size = struct.unpack(">I", prefix)[0]
    if size <= 0 or size > maximum:
        _fail("oversized-request", f"{label} exceeds its size limit")
    raw = _recv_exact(
        connection, size, deadline=deadline, phase=f"while reading {label}"
    )
    value = _object(_parse_json(raw, label), label)
    if raw != _normalized_json(value):
        _fail("invalid-json", f"{label} must use canonical JSON")
    return value, raw


def _send_frame(
    connection: socket.socket,
    value: dict[str, Any],
    *,
    maximum: int,
    deadline: OperationDeadline | None,
) -> None:
    raw = _normalized_json(value)
    if len(raw) > maximum:
        _fail("oversized-response", "protocol frame exceeds its size limit")
    _socket_timeout(connection, deadline, "while sending protocol frame", 30)
    connection.sendall(struct.pack(">I", len(raw)) + raw)


def _validate_request_header(
    request: dict[str, Any],
    installation: dict[str, Any],
    capability: dict[str, Any],
) -> None:
    _exact_keys(
        request,
        "broker request",
        (
            "schema_version",
            "protocol",
            "phase",
            "request_nonce",
            "repository",
            "issue",
            "operation",
            "pack_sha256",
            "pack_size",
            "request_deadline",
        ),
    )
    if (
        _integer(request["schema_version"], "request.schema_version", 1) != 1
        or request["protocol"] != PROTOCOL
    ):
        _fail("invalid-request", "broker request protocol/version mismatch")
    if request["phase"] != "request":
        _fail("invalid-request", "broker request phase differs")
    for field in ("request_nonce", "pack_sha256"):
        _string(request[field], f"request.{field}", HEX_64_RE)
    repository = _string(
        request["repository"], "request.repository", REPOSITORY_RE
    )
    issue = _integer(request["issue"], "request.issue", 1)
    if request["operation"] not in {"preflight", "publish", "reconcile"}:
        _fail("invalid-request", "broker request operation is not allowlisted")
    pack_size = _integer(request["pack_size"], "request.pack_size")
    if pack_size > installation["pack_max_bytes"]:
        _fail("oversized-pack", "broker request pack exceeds its size limit")
    if request["operation"] in {"preflight", "reconcile"}:
        if pack_size != 0 or request["pack_sha256"] != hashlib.sha256(b"").hexdigest():
            _fail(
                "invalid-request",
                "non-publication request must not carry an object pack",
            )
    elif pack_size <= 0:
        _fail("invalid-request", "publish request requires an object pack")
    request_deadline = _time(
        request["request_deadline"], "request.request_deadline"
    )
    if request_deadline <= datetime_module.datetime.now(
        datetime_module.timezone.utc
    ):
        _fail("request-expired", "broker request deadline expired")
    if (
        repository != installation["repository"]
        or repository != capability["repository"]
        or issue != capability["_issue"]
        or request["operation"] != capability["operation"]
    ):
        _fail(
            "capability-mismatch",
            "request does not match its issued repository/issue/operation capability",
        )


def _read_pack(
    connection: socket.socket,
    request: dict[str, Any],
    state_directory: Path,
    deadline: OperationDeadline,
) -> Path:
    pack_size = request["pack_size"]
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
            while remaining:
                _socket_timeout(
                    connection, deadline, "while receiving object pack", 30
                )
                try:
                    chunk = connection.recv(min(65536, remaining))
                except (OSError, TimeoutError) as error:
                    raise BrokerError(
                        "client-disconnected",
                        "connection failed while receiving object pack",
                    ) from error
                if not chunk:
                    _fail(
                        "client-disconnected",
                        "client disconnected while sending object pack",
                    )
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
    deadline.check("after object-pack receipt")
    return pack_path


def _git_environment(
    installation: dict[str, Any],
    home: Path,
    deadline: OperationDeadline | None = None,
) -> dict[str, str]:
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
        "GIT_AUTHOR_NAME": "Workflow Pilot Broker",
        "GIT_AUTHOR_EMAIL": "workflow-pilot-broker@example.invalid",
        "GIT_COMMITTER_NAME": "Workflow Pilot Broker",
        "GIT_COMMITTER_EMAIL": "workflow-pilot-broker@example.invalid",
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
    if deadline is not None:
        environment["WORKFLOW_PILOT_EFFECTIVE_DEADLINE"] = deadline.text()
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
    deadline: OperationDeadline | None = None,
    deadline_exec: Path | None = None,
) -> subprocess.CompletedProcess[bytes]:
    if stdin is not None and input_bytes is not None:
        _fail("process-hardening-failed", "bounded process has two stdin sources")

    command = arguments
    exec_read: int | None = None
    exec_write: int | None = None
    if deadline_exec is not None:
        if deadline is None:
            _fail(
                "process-hardening-failed",
                "deadline exec helper requires an absolute deadline",
            )
        wall_seconds = int(deadline.absolute.timestamp())
        wall_nanoseconds = deadline.absolute.microsecond * 1000
        monotonic_seconds = int(deadline.monotonic_end)
        monotonic_nanoseconds = int(
            (deadline.monotonic_end - monotonic_seconds) * 1_000_000_000
        )
        exec_read, exec_write = os.pipe()
        os.set_blocking(exec_read, False)
        command = [
            os.fspath(deadline_exec),
            str(os.getpid()),
            str(wall_seconds),
            str(wall_nanoseconds),
            str(monotonic_seconds),
            str(monotonic_nanoseconds),
            str(SUBPROCESS_OUTPUT_MAX_BYTES),
            str(exec_write),
            "--",
            *arguments,
        ]

    stdout_fd = _empty_memory_file("workflow-pilot-subprocess-stdout")
    stderr_fd = _empty_memory_file("workflow-pilot-subprocess-stderr")
    stop_read, stop_write = os.pipe()
    try:
        child_fds = pass_fds + (
            (exec_write,) if exec_write is not None else ()
        )
        process = subprocess.Popen(
            command,
            cwd=cwd,
            env=environment,
            stdin=subprocess.PIPE if input_bytes is not None else stdin,
            stdout=stdout_fd,
            stderr=stderr_fd,
            pass_fds=child_fds,
            start_new_session=deadline_exec is None,
        )
        if exec_write is not None:
            os.close(exec_write)
            exec_write = None
        watchdog = None
        if deadline_exec is not None:
            try:
                watchdog = subprocess.Popen(
                    [
                        os.fspath(deadline_exec),
                        "--watch",
                        str(os.getpid()),
                        str(process.pid),
                    ],
                    stdin=stop_read,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    close_fds=True,
                )
            except OSError:
                _kill_process_group(process)
                process.communicate()
                raise
        os.close(stop_read)
        stop_read = None
        try:
            try:
                effective_timeout = (
                    deadline.remaining("after subprocess launch", timeout)
                    if deadline is not None
                    else timeout
                )
                effective_timeout = (
                    deadline.remaining(
                        "immediately before subprocess communication",
                        effective_timeout,
                    )
                    if deadline is not None
                    else effective_timeout
                )
                process.communicate(
                    input=input_bytes, timeout=effective_timeout
                )
            except BrokerError:
                _kill_process_group(process)
                process.communicate()
                raise
            except subprocess.TimeoutExpired as error:
                exec_started = _deadline_exec_started(exec_read)
                _kill_process_group(process)
                process.communicate()
                exec_started = (
                    exec_started or _deadline_exec_started(exec_read)
                )
                if deadline is not None and not exec_started:
                    deadline.check("during subprocess")
                raise BrokerError(
                    "git-timeout", "bounded Git operation timed out"
                ) from error
        finally:
            try:
                os.write(stop_write, b"X")
            except OSError:
                pass
            os.close(stop_write)
            stop_write = None
            if watchdog is not None:
                watchdog.wait(timeout=10)
    except OSError as error:
        raise BrokerError("git-unavailable", "cannot execute bounded Git operation") from error
    finally:
        for descriptor in (stop_read, stop_write):
            if descriptor is None:
                continue
            try:
                os.close(descriptor)
            except OSError:
                pass
        exec_started = _deadline_exec_started(exec_read)
        if exec_read is not None:
            os.close(exec_read)
        if exec_write is not None:
            os.close(exec_write)
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
    if deadline is not None:
        try:
            deadline.check("after subprocess")
        except BrokerError:
            if exec_started:
                raise BrokerError(
                    "git-timeout",
                    "executed Git crossed its effective deadline",
                )
            raise
    if deadline_exec is not None:
        if process.returncode == 222:
            _fail(
                "operation-expired",
                "deadline exec boundary rejected the subprocess before execve",
            )
        if process.returncode in {223, 224}:
            _fail(
                "process-hardening-failed",
                "deadline exec boundary rejected process hardening",
            )
    return subprocess.CompletedProcess(arguments, process.returncode, stdout, stderr)


def _deadline_exec_started(exec_read: int | None) -> bool:
    if exec_read is None:
        return False
    try:
        return os.read(exec_read, 1) == b"E"
    except BlockingIOError:
        return False


def _kill_process_group(process: subprocess.Popen[bytes]) -> None:
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        return
    except OSError:
        try:
            process.kill()
        except ProcessLookupError:
            pass


def _git(
    installation: dict[str, Any],
    home: Path,
    arguments: list[str],
    *,
    deadline: OperationDeadline,
    cwd: Path | None = None,
    stdin: BinaryIO | None = None,
    input_bytes: bytes | None = None,
) -> bytes:
    command = [GIT, "--no-pager"]
    if installation["test_only"]:
        command.extend(["-c", "protocol.file.allow=always"])
    command.extend(arguments)
    protected_remote = installation["protected_remote"]
    pass_fds: tuple[int, ...] = ()
    if protected_remote is not None:
        pass_fds = tuple(
            protected_remote[name]
            for name in (
                "git_dir_fd",
                "objects_fd",
                "refs_fd",
                "hooks_fd",
                "config_fd",
            )
        )
    environment = _git_environment(installation, home, deadline)
    delay_ns = installation.get("_deadline_exec_test_delay_ns")
    if delay_ns is not None and "push" in arguments and "--dry-run" not in arguments:
        if not installation["test_only"]:
            _fail(
                "process-hardening-failed",
                "deadline exec test delay is forbidden in production",
            )
        environment["WORKFLOW_PILOT_DEADLINE_EXEC_TEST_DELAY_NS"] = str(
            _integer(delay_ns, "deadline exec test delay", 0)
        )
    completed = _run_bounded(
        command,
        environment=environment,
        timeout=installation["operation_timeout_seconds"],
        cwd=cwd,
        stdin=stdin,
        input_bytes=input_bytes,
        deadline=deadline,
        pass_fds=pass_fds,
        deadline_exec=installation["deadline_exec"],
    )
    if completed.returncode != 0:
        _fail("git-failed", "protected Git operation failed")
    return completed.stdout


def _runtime_endpoint(installation: dict[str, Any]) -> str:
    parsed = urllib.parse.urlsplit(installation["endpoint"])
    remote = installation["protected_remote"]
    if parsed.scheme == "file" and remote is not None:
        return f"file:///proc/self/fd/{remote['git_dir_fd']}"
    return installation["endpoint"]


def _pack_object_ids(
    staging: Path,
    installation: dict[str, Any],
    home: Path,
    pack_path: Path,
    deadline: OperationDeadline,
) -> set[str]:
    with pack_path.open("rb") as stream:
        _git(
            installation,
            home,
            ["-C", os.fspath(staging), "index-pack", "--stdin", "--strict"],
            deadline=deadline,
            stdin=stream,
        )
    indexes = list((staging / "objects" / "pack").glob("*.idx"))
    if len(indexes) != 1:
        _fail("wrong-pack", "object pack did not produce one protected index")
    output = _git(
        installation,
        home,
        ["verify-pack", "-v", os.fspath(indexes[0])],
        deadline=deadline,
    )
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
    staging: Path,
    installation: dict[str, Any],
    home: Path,
    plan: dict[str, Any],
    pack_path: Path,
    deadline: OperationDeadline,
) -> None:
    packed = _pack_object_ids(
        staging, installation, home, pack_path, deadline
    )
    planned = set(plan["object_ids"])
    if packed != planned:
        _fail("wrong-objects", "object pack differs from the signed exact object set")
    for field in ("new_authority_oid", "new_anchor_oid"):
        object_type = _git(
            installation,
            home,
            ["-C", os.fspath(staging), "cat-file", "-t", plan[field]],
            deadline=deadline,
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
        deadline=deadline,
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
    installation: dict[str, Any],
    home: Path,
    authority_ref: str,
    anchor_ref: str,
    deadline: OperationDeadline,
) -> dict[str, str | None]:
    raw = _git(
        installation,
        home,
        [
            "ls-remote",
            "--refs",
            _runtime_endpoint(installation),
            authority_ref,
            anchor_ref,
        ],
        deadline=deadline,
    )
    return _parse_remote_refs(raw, (authority_ref, anchor_ref))


class ReplayJournal:
    def __init__(
        self,
        state_directory: Path,
        installation_id: str,
        deadline: OperationDeadline | None = None,
    ):
        self.state_directory = state_directory
        self.installation_id = installation_id
        self.lock_stream: BinaryIO | None = None
        self.entries: list[dict[str, Any]] = []
        self.last_hash = "0" * 64
        self.deadline = deadline

    def __enter__(self) -> "ReplayJournal":
        self.state_directory.mkdir(mode=0o700, exist_ok=True)
        lock_path = self.state_directory / "journal.lock"
        lock_fd = os.open(
            lock_path,
            os.O_RDWR
            | os.O_APPEND
            | os.O_CREAT
            | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        _validate_private_state_fd(lock_fd, "replay journal lock")
        self.lock_stream = os.fdopen(lock_fd, "a+b")
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
        if self.deadline is not None:
            self.deadline.check("before replay journal validation")
        journal_path = self.state_directory / "journal.jsonl"
        anchor_path = self.state_directory / "journal.anchor"
        previous = "0" * 64
        entries: list[dict[str, Any]] = []
        if journal_path.exists():
            journal_fd = os.open(
                journal_path,
                os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            )
            metadata = _validate_private_state_fd(
                journal_fd, "replay journal"
            )
            if metadata.st_size > JOURNAL_MAX_BYTES:
                os.close(journal_fd)
                _fail(
                    "journal-full", "replay journal exceeds its bounded size"
                )
            raw_journal = _read_bounded_fd(journal_fd, JOURNAL_MAX_BYTES)
            os.close(journal_fd)
            for line_number, raw_line in enumerate(
                raw_journal.splitlines(), 1
            ):
                if self.deadline is not None and line_number % 256 == 0:
                    self.deadline.check(
                        "while validating replay journal"
                    )
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
                if event == "completed" and entry["result"] not in {
                    "published",
                    "safe-failed",
                    "indeterminate",
                    "committed-late",
                    "security-hold",
                }:
                    _fail("journal-corrupt", "replay journal outcome differs")
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
            anchor_fd = os.open(
                anchor_path,
                os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            )
            metadata = _validate_private_state_fd(
                anchor_fd, "replay journal anchor"
            )
            if metadata.st_size > REQUEST_MAX_BYTES:
                os.close(anchor_fd)
                _fail(
                    "journal-corrupt",
                    "replay journal anchor exceeds its size limit",
                )
            raw_anchor = _read_bounded_fd(anchor_fd, REQUEST_MAX_BYTES)
            os.close(anchor_fd)
            anchor = _object(
                _parse_json(raw_anchor, "replay journal anchor"),
                "replay journal anchor",
            )
            _exact_keys(anchor, "replay journal anchor", ("installation_id", "last_hash"))
            if anchor["installation_id"] != self.installation_id or anchor["last_hash"] != previous:
                _fail("journal-rollback", "replay journal and durable anchor differ")
        elif entries:
            _fail("journal-rollback", "replay journal durable anchor is missing")
        self.entries = entries
        self.last_hash = previous

    def _append(self, event: dict[str, Any]) -> None:
        if self.deadline is not None:
            self.deadline.check("before replay journal update")
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
        if self.deadline is not None:
            self.deadline.check("after replay journal update")

    def reserve(self, plan: dict[str, Any], plan_identity: str) -> None:
        self.check_available(plan)
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

    def check_available(self, plan: dict[str, Any]) -> None:
        reservations = [entry for entry in self.entries if entry["event"] == "reserved"]
        if any(entry["nonce"] == plan["nonce"] for entry in reservations):
            _fail("replay", "publication plan nonce was already consumed")
        relevant = [
            entry
            for entry in reservations
            if entry["repository"] == plan["repository"] and entry["issue"] == plan["issue"]
        ]
        for reservation in relevant:
            outcomes = [
                entry
                for entry in self.entries
                if entry["event"] == "completed"
                and entry["nonce"] == reservation["nonce"]
                and entry["plan_identity"] == reservation["plan_identity"]
            ]
            if not outcomes or outcomes[-1]["result"] == "indeterminate":
                _fail(
                    "indeterminate",
                    "an earlier publication requires reconciliation",
                )
            if outcomes[-1]["result"] == "security-hold":
                _fail(
                    "security-hold",
                    "an earlier publication is under security hold",
                )
        if relevant and plan["sequence"] <= max(entry["sequence"] for entry in relevant):
            _fail("replay", "publication plan sequence does not advance")

    def complete(self, plan: dict[str, Any], plan_identity: str, result: str) -> None:
        if result not in {
            "published",
            "safe-failed",
            "indeterminate",
            "committed-late",
            "security-hold",
        }:
            _fail("journal-corrupt", "publication outcome is not allowlisted")
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

    def require_reconciliation(
        self,
        plan: dict[str, Any],
        plan_identity: str,
    ) -> None:
        reservations = [
            entry
            for entry in self.entries
            if entry["event"] == "reserved"
            and entry["nonce"] == plan["nonce"]
            and entry["plan_identity"] == plan_identity
        ]
        outcomes = [
            entry
            for entry in self.entries
            if entry["event"] == "completed"
            and entry["nonce"] == plan["nonce"]
            and entry["plan_identity"] == plan_identity
        ]
        if (
            not reservations
            or (
                outcomes
                and outcomes[-1]["result"]
                not in {"indeterminate", "published", "committed-late"}
            )
        ):
            _fail(
                "reconciliation-not-required",
                "plan does not have an indeterminate publication to reconcile",
            )


def _publish(
    installation: dict[str, Any],
    plan: dict[str, Any],
    plan_identity: str,
    pack_path: Path,
    deadline: OperationDeadline,
) -> dict[str, str]:
    state = installation["state_directory"]
    operation_root = state / "operations" / plan["nonce"]
    operation_root.parent.mkdir(mode=0o700, exist_ok=True)
    operation_root.mkdir(mode=0o700)
    home = operation_root / "home"
    staging = operation_root / "staging.git"
    home.mkdir(mode=0o700)
    try:
        _git(
            installation,
            home,
            ["init", "--bare", "--quiet", os.fspath(staging)],
            deadline=deadline,
        )
        _validate_object_closure(
            staging, installation, home, plan, pack_path, deadline
        )
        refs = (plan["authority_ref"], plan["anchor_ref"])
        current = _remote_refs(installation, home, *refs, deadline)
        expected = {
            plan["authority_ref"]: plan["expected_authority_oid"],
            plan["anchor_ref"]: plan["expected_anchor_oid"],
        }
        if current != expected:
            _fail("stale-remote", "remote refs differ from the signed expected state")
        _check_protected_remote(
            installation["protected_remote"], deadline
        )
        deadline.check("before preparing publication refs")
        for oid, ref in (
            (plan["new_authority_oid"], plan["authority_ref"]),
            (plan["new_anchor_oid"], plan["anchor_ref"]),
        ):
            _git(
                installation,
                home,
                ["-C", os.fspath(staging), "update-ref", ref, oid],
                deadline=deadline,
            )
        leases = [
            f"--force-with-lease={ref}:{expected[ref] or ''}"
            for ref in refs
        ]
        refspecs = [
            f"{plan['new_authority_oid']}:{plan['authority_ref']}",
            f"{plan['new_anchor_oid']}:{plan['anchor_ref']}",
        ]
        deadline.check("immediately before remote mutation")
        try:
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
                    _runtime_endpoint(installation),
                    *refspecs,
                ],
                deadline=deadline,
            )
            deadline.check("immediately after remote push returned")
            _check_protected_remote(
                installation["protected_remote"], deadline
            )
            readback = _remote_refs(installation, home, *refs, deadline)
            deadline.check("after exact remote readback")
        except BrokerError as error:
            if error.code == "operation-expired":
                raise
            raise IndeterminatePublication(
                "indeterminate",
                "transmitted atomic push requires exact reconciliation",
            ) from error
        except OSError as error:
            raise IndeterminatePublication(
                "indeterminate",
                "transmitted atomic push requires exact reconciliation",
            ) from error
        wanted = {
            plan["authority_ref"]: plan["new_authority_oid"],
            plan["anchor_ref"]: plan["new_anchor_oid"],
        }
        if readback != wanted:
            raise IndeterminatePublication(
                "indeterminate",
                "post-push refs require exact reconciliation",
            )
        return {ref: value for ref, value in readback.items() if value is not None}
    finally:
        shutil.rmtree(operation_root, ignore_errors=True)


def _reconcile_remote(
    installation: dict[str, Any],
    plan: dict[str, Any],
    deadline: OperationDeadline | None = None,
) -> tuple[str, dict[str, str | None] | None]:
    if deadline is None:
        deadline = OperationDeadline(
            datetime_module.datetime.now(datetime_module.timezone.utc)
            + datetime_module.timedelta(
                seconds=installation["reconciliation_timeout_seconds"]
            )
        )
    try:
        _check_protected_remote(
            installation["protected_remote"], deadline
        )
    except (BrokerError, OSError):
        return "indeterminate", None
    try:
        home = installation["state_directory"] / "reconciliation-home"
        home.mkdir(mode=0o700, exist_ok=True)
        refs = _remote_refs(
            installation,
            home,
            plan["authority_ref"],
            plan["anchor_ref"],
            deadline,
        )
    except (BrokerError, OSError):
        return "indeterminate", None
    expected = {
        plan["authority_ref"]: plan["expected_authority_oid"],
        plan["anchor_ref"]: plan["expected_anchor_oid"],
    }
    planned = {
        plan["authority_ref"]: plan["new_authority_oid"],
        plan["anchor_ref"]: plan["new_anchor_oid"],
    }
    if refs == planned:
        return "committed-late", refs
    if refs == expected:
        return "safe-failed", refs
    return "security-hold", refs


def _load_plan(
    installation: dict[str, Any],
    plan_identity: str,
    now: datetime_module.datetime,
    *,
    deadline: OperationDeadline | None = None,
    allow_expired: bool = False,
) -> dict[str, Any]:
    _string(plan_identity, "request.plan_identity", HEX_64_RE)
    try:
        plan_fd = os.open(
            f"{plan_identity}.json",
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=installation["plan_store_fd"],
        )
    except OSError as error:
        raise BrokerError(
            "authority-unavailable", "cannot open signed publication plan"
        ) from error
    try:
        metadata = os.fstat(plan_fd)
        owners = installation.get("_authority_owners", {0, os.geteuid()})
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid not in owners
            or metadata.st_mode & 0o022
        ):
            _fail(
                "insecure-installation",
                "signed publication plan authority differs",
            )
        plan = _read_canonical_json_fd(
            plan_fd, "signed publication plan"
        )
    finally:
        os.close(plan_fd)
    if hashlib.sha256(_normalized_json(plan)).hexdigest() != plan_identity:
        _fail("invalid-plan", "signed publication plan identity differs")
    return _validate_plan(
        plan,
        installation,
        now=now,
        deadline=deadline,
        allow_expired=allow_expired,
    )


def _response(
    installation: dict[str, Any],
    request: dict[str, Any],
    capability: dict[str, Any],
    deadline: OperationDeadline,
    *,
    phase: str,
    status: str,
    code: str,
    refs: dict[str, str] | None,
) -> dict[str, Any]:
    broker_user_namespace = os.stat(
        "/proc/self/ns/user", follow_symlinks=True
    ).st_ino
    broker_namespace_uid = os.geteuid()
    broker_uid = _outer_uid(broker_namespace_uid)
    request_nonce = request.get("request_nonce")
    if (
        not isinstance(request_nonce, str)
        or HEX_64_RE.fullmatch(request_nonce) is None
    ):
        request_nonce = None
    repository = request.get("repository")
    if (
        not isinstance(repository, str)
        or REPOSITORY_RE.fullmatch(repository) is None
    ):
        repository = None
    issue = request.get("issue")
    if isinstance(issue, bool) or not isinstance(issue, int) or issue < 1:
        issue = None
    response = {
        "schema_version": 1,
        "protocol": PROTOCOL,
        "phase": phase,
        "request_digest": _digest(request),
        "request_nonce": request_nonce,
        "repository": repository,
        "issue": issue,
        "plan_identity": capability["_plan_identity"],
        "capability_nonce": capability["capability_nonce"],
        "installation_id": installation["installation_id"],
        "broker_key_id": installation["broker_key_id"],
        "broker_pid": os.getpid(),
        "broker_uid": broker_uid,
        "broker_namespace_uid": broker_namespace_uid,
        "broker_user_namespace": broker_user_namespace,
        "effective_deadline": deadline.text(),
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
    try:
        signed_schema.validate_record(
            response, "result", "broker response"
        )
    except signed_schema.SchemaError as error:
        raise BrokerError("invalid-response", str(error)) from error
    return response


def _outer_uid(namespace_uid: int) -> int:
    try:
        mappings = Path("/proc/self/uid_map").read_text(
            encoding="ascii"
        ).splitlines()
    except OSError as error:
        raise BrokerError(
            "process-hardening-failed", "cannot inspect broker UID mapping"
        ) from error
    for line in mappings:
        fields = line.split()
        if len(fields) != 3:
            _fail("process-hardening-failed", "broker UID mapping is malformed")
        inside, outside, length = (int(value) for value in fields)
        if inside <= namespace_uid < inside + length:
            return outside + namespace_uid - inside
    _fail("process-hardening-failed", "broker effective UID is not mapped")


def _credential_readiness(
    installation: dict[str, Any],
    plan: dict[str, Any],
    deadline: OperationDeadline,
) -> None:
    _check_protected_remote(installation["protected_remote"], deadline)
    auth = installation["authentication"]
    if auth["mode"] == "https-askpass":
        completed = _run_bounded(
            [os.fspath(auth["askpass"]), "Password for readiness"],
            environment=_git_environment(
                installation, installation["state_directory"], deadline
            ),
            timeout=installation["operation_timeout_seconds"],
            deadline=deadline,
        )
        if (
            completed.returncode != 0
            or not completed.stdout
            or b"\n" in completed.stdout.rstrip(b"\n")
        ):
            _fail(
                "credential-unavailable",
                "HTTPS askpass credential readiness failed",
            )
    elif auth["mode"] == "ssh-agent":
        completed = _run_bounded(
            ["/usr/bin/ssh-add", "-l"],
            environment=_git_environment(
                installation, installation["state_directory"], deadline
            ),
            timeout=installation["operation_timeout_seconds"],
            deadline=deadline,
        )
        if completed.returncode != 0:
            _fail("credential-unavailable", "SSH agent readiness failed")
    home = installation["state_directory"] / "preflight-home"
    home.mkdir(mode=0o700, exist_ok=True)
    current = _remote_refs(
        installation,
        home,
        plan["authority_ref"],
        plan["anchor_ref"],
        deadline,
    )
    expected = {
        plan["authority_ref"]: plan["expected_authority_oid"],
        plan["anchor_ref"]: plan["expected_anchor_oid"],
    }
    if current != expected:
        _fail("stale-remote", "remote refs differ from the signed expected state")
    probe_root = (
        installation["state_directory"]
        / "readiness-probes"
        / os.urandom(16).hex()
    )
    probe_root.parent.mkdir(mode=0o700, exist_ok=True)
    try:
        _git(
            installation,
            home,
            ["init", "--bare", "--quiet", os.fspath(probe_root)],
            deadline=deadline,
        )
        tree = _git(
            installation,
            home,
            ["-C", os.fspath(probe_root), "mktree"],
            deadline=deadline,
            input_bytes=b"",
        ).decode("ascii").strip()
        _sha(tree, "readiness tree")
        commit = _git(
            installation,
            home,
            [
                "-C",
                os.fspath(probe_root),
                "commit-tree",
                tree,
                "-m",
                "workflow-pilot write authorization probe",
            ],
            deadline=deadline,
        ).decode("ascii").strip()
        _sha(commit, "readiness commit")
        leases = [
            f"--force-with-lease={ref}:{expected[ref] or ''}"
            for ref in (plan["authority_ref"], plan["anchor_ref"])
        ]
        _git(
            installation,
            home,
            [
                "-C",
                os.fspath(probe_root),
                "push",
                "--dry-run",
                "--atomic",
                "--porcelain",
                "--no-verify",
                *leases,
                _runtime_endpoint(installation),
                f"{commit}:{plan['authority_ref']}",
                f"{commit}:{plan['anchor_ref']}",
            ],
            deadline=deadline,
        )
        unchanged = _remote_refs(
            installation,
            home,
            plan["authority_ref"],
            plan["anchor_ref"],
            deadline,
        )
        if unchanged != expected:
            _fail(
                "security-hold",
                "write-authorization dry-run changed protected refs",
            )
    finally:
        shutil.rmtree(probe_root, ignore_errors=True)
    deadline.check("after authenticated readiness")


def serve_connection(
    connection: socket.socket,
    installation: dict[str, Any],
    capability_record: dict[str, Any],
    *,
    enforce_peer: bool = True,
) -> None:
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    _require_unnamed_socket(connection)
    if enforce_peer:
        _pid, uid, _gid = _peer_credentials(connection)
        if uid != installation["expected_capability_uid"]:
            _fail("peer-authentication-failed", "capability issuer principal is not authorized")
    raw_capability_expiry = _time(
        capability_record.get("expires_at"), "capability.expires_at"
    )
    launch_deadline = OperationDeadline(
        min(
            raw_capability_expiry,
            datetime_module.datetime.now(datetime_module.timezone.utc)
            + datetime_module.timedelta(
                seconds=installation["operation_timeout_seconds"]
            ),
        )
    )
    request: dict[str, Any] | None = None
    capability: dict[str, Any] | None = None
    pack_path: Path | None = None
    deadline = launch_deadline
    ack_sent = False
    try:
        request, _raw_request = _recv_frame(
            connection,
            maximum=REQUEST_MAX_BYTES,
            label="broker request",
            deadline=launch_deadline,
        )
        if isinstance(request.get("request_deadline"), str):
            try:
                request_deadline = _time(
                    request["request_deadline"], "request.request_deadline"
                )
                deadline = OperationDeadline(
                    min(request_deadline, raw_capability_expiry)
                )
            except BrokerError:
                pass
        capability = _validate_capability(
            capability_record,
            installation,
            now=datetime_module.datetime.now(datetime_module.timezone.utc),
            deadline=deadline,
        )
        _validate_request_header(request, installation, capability)
        request_deadline = _time(
            request["request_deadline"], "request.request_deadline"
        )
        plan = _load_plan(
            installation,
            capability["_plan_identity"],
            datetime_module.datetime.now(datetime_module.timezone.utc),
            deadline=deadline,
            allow_expired=capability["operation"] == "reconcile",
        )
        if (
            plan["repository"] != capability["repository"]
            or plan["issue"] != capability["_issue"]
        ):
            _fail(
                "capability-mismatch",
                "issued capability does not bind its signed plan repository/issue",
            )
        effective_times = [
            request_deadline,
            capability["_expires_time"],
        ]
        if capability["operation"] != "reconcile":
            effective_times.append(plan["_expires_time"])
        deadline = OperationDeadline(min(effective_times))
        if (
            request["pack_sha256"] != plan["pack_sha256"]
            or request["pack_size"] != plan["pack_size"]
        ) and request["operation"] == "publish":
            _fail("wrong-pack", "request pack does not match the signed plan")
        if request["operation"] == "publish":
            with ReplayJournal(
                installation["state_directory"],
                installation["installation_id"],
                deadline,
            ) as journal:
                journal.check_available(plan)
        elif request["operation"] == "reconcile":
            with ReplayJournal(
                installation["state_directory"],
                installation["installation_id"],
                deadline,
            ) as journal:
                journal.require_reconciliation(
                    plan, capability["_plan_identity"]
                )
        if request["operation"] != "reconcile":
            _credential_readiness(installation, plan, deadline)
        ack = _response(
            installation,
            request,
            capability,
            deadline,
            phase="ack",
            status="ready",
            code="ready",
            refs=None,
        )
        _send_frame(
            connection,
            ack,
            maximum=RESPONSE_MAX_BYTES,
            deadline=deadline,
        )
        ack_sent = True
        continuation, _raw_continuation = _recv_frame(
            connection,
            maximum=REQUEST_MAX_BYTES,
            label="broker continuation",
            deadline=deadline,
        )
        _exact_keys(
            continuation,
            "broker continuation",
            (
                "schema_version",
                "protocol",
                "phase",
                "request_nonce",
                "plan_identity",
            ),
        )
        if (
            _integer(
                continuation["schema_version"],
                "continuation.schema_version",
                1,
            )
            != 1
            or continuation["protocol"] != PROTOCOL
            or continuation["phase"] != "continue"
            or continuation["request_nonce"] != request["request_nonce"]
            or continuation["plan_identity"] != capability["_plan_identity"]
        ):
            _fail(
                "invalid-request",
                "broker continuation does not bind its acknowledgement",
            )
        if request["operation"] == "preflight":
            result = _response(
                installation,
                request,
                capability,
                deadline,
                phase="result",
                status="ok",
                code="ready",
                refs=None,
            )
            _send_frame(
                connection,
                result,
                maximum=RESPONSE_MAX_BYTES,
                deadline=deadline,
            )
            return
        if request["operation"] == "reconcile":
            outcome, reconciled_refs = _reconcile_remote(
                installation, plan, deadline
            )
            with ReplayJournal(
                installation["state_directory"],
                installation["installation_id"],
                deadline,
            ) as journal:
                journal.deadline = None
                journal.complete(
                    plan, capability["_plan_identity"], outcome
                )
            result = _response(
                installation,
                request,
                capability,
                deadline,
                phase="result",
                status=(
                    "ok"
                    if outcome == "committed-late"
                    else "error"
                ),
                code=outcome,
                refs=reconciled_refs,
            )
            _send_frame(
                connection,
                result,
                maximum=RESPONSE_MAX_BYTES,
                deadline=(
                    deadline
                    if result["status"] == "ok"
                    else None
                ),
            )
            return
        pack_path = _read_pack(
            connection,
            request,
            installation["state_directory"],
            deadline,
        )
        if _connection_disconnected(connection):
            _fail("client-disconnected", "client disconnected before publication")
        with ReplayJournal(
            installation["state_directory"],
            installation["installation_id"],
            deadline,
        ) as journal:
            journal.reserve(plan, capability["_plan_identity"])
            try:
                refs = _publish(
                    installation,
                    plan,
                    capability["_plan_identity"],
                    pack_path,
                    deadline,
                )
            except IndeterminatePublication:
                journal.deadline = None
                journal.complete(
                    plan,
                    capability["_plan_identity"],
                    "indeterminate",
                )
                outcome, reconciled_refs = _reconcile_remote(
                    installation, plan
                )
                if outcome != "indeterminate":
                    journal.complete(
                        plan,
                        capability["_plan_identity"],
                        outcome,
                    )
                response_code = outcome
                response_refs = reconciled_refs
                response_status = (
                    "ok" if outcome == "committed-late" else "error"
                )
            except BrokerError:
                journal.deadline = None
                journal.complete(
                    plan, capability["_plan_identity"], "safe-failed"
                )
                raise
            else:
                journal.complete(
                    plan, capability["_plan_identity"], "published"
                )
                response_code = "published"
                response_refs = refs
                response_status = "ok"
        response = _response(
            installation,
            request,
            capability,
            deadline,
            phase="result",
            status=response_status,
            code=response_code,
            refs=response_refs,
        )
    except (BrokerError, OSError) as error:
        if request is None or capability is None:
            raise
        code = error.code if isinstance(error, BrokerError) else "broker-io-failed"
        response = _response(
            installation,
            request,
            capability,
            deadline,
            phase="result" if ack_sent else "ack",
            status="error",
            code=code,
            refs=None,
        )
    finally:
        if pack_path is not None:
            pack_path.unlink(missing_ok=True)
    try:
        _send_frame(
            connection,
            response,
            maximum=RESPONSE_MAX_BYTES,
            deadline=(
                deadline
                if response["status"] == "ok"
                and response["code"] == "published"
                else None
            ),
        )
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
            "test_only",
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
        "test_only": raw["test_only"],
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
    if not isinstance(result["test_only"], bool):
        _fail("invalid-installation", "client installation.test_only must be boolean")
    result["endpoint"] = _canonical_endpoint(
        raw["endpoint"], allow_local=result["test_only"]
    )
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
    expected_phase: str,
    now: datetime_module.datetime,
    expected_context: dict[str, Any] | None = None,
    enforce_broker_process: bool = True,
) -> tuple[
    dict[str, Any],
    PublicationOutcome | None,
    int | None,
]:
    try:
        signed_schema.validate_record(
            response, "result", "broker response"
        )
    except signed_schema.SchemaError as error:
        raise BrokerError("invalid-response", str(error)) from error
    _exact_keys(
        response,
        "broker response",
        (
            "schema_version",
            "protocol",
            "phase",
            "request_digest",
            "request_nonce",
            "repository",
            "issue",
            "plan_identity",
            "capability_nonce",
            "installation_id",
            "broker_key_id",
            "broker_pid",
            "broker_uid",
            "broker_namespace_uid",
            "broker_user_namespace",
            "effective_deadline",
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
        response["phase"] != expected_phase
        or
        response["request_digest"] != _digest(request)
        or response["request_nonce"] != request["request_nonce"]
        or response["repository"] != request["repository"]
        or response["issue"] != request["issue"]
        or response["installation_id"] != installation["installation_id"]
        or response["broker_key_id"] != installation["broker_key_id"]
    ):
        _fail("invalid-response", "broker response does not bind the request/installation")
    completed = _time(response["completed_at"], "response.completed_at")
    request_deadline = _time(
        request["request_deadline"], "request.request_deadline"
    )
    effective_deadline = _time(
        response["effective_deadline"], "response.effective_deadline"
    )
    if (
        effective_deadline > request_deadline
        or completed > now + datetime_module.timedelta(seconds=5)
    ):
        _fail("invalid-response", "broker response exceeds its request deadline")
    for field in ("plan_identity", "capability_nonce"):
        _string(response[field], f"response.{field}", HEX_64_RE)
    broker_pid = _integer(response["broker_pid"], "response.broker_pid", 1)
    broker_uid = _integer(response["broker_uid"], "response.broker_uid")
    broker_namespace_uid = _integer(
        response["broker_namespace_uid"],
        "response.broker_namespace_uid",
    )
    broker_namespace = _integer(
        response["broker_user_namespace"],
        "response.broker_user_namespace",
        1,
    )
    signature_record = _object(response["signature"], "response.signature")
    key_id, signature = _load_signature_record(signature_record, "response.signature")
    if key_id != installation["broker_key_id"]:
        _fail("invalid-signature", "broker response signing key differs")
    _verify_ed25519(
        installation["broker_public_key"],
        _signed_payload(RESPONSE_DOMAIN, response),
        signature,
    )
    context = {
        "plan_identity": response["plan_identity"],
        "capability_nonce": response["capability_nonce"],
        "effective_deadline": response["effective_deadline"],
        "broker_pid": broker_pid,
        "broker_uid": broker_uid,
        "broker_namespace_uid": broker_namespace_uid,
        "broker_user_namespace": broker_namespace,
    }
    if expected_context is not None and context != expected_context:
        _fail("invalid-response", "broker response context changed between phases")
    pidfd = None
    if enforce_broker_process and expected_context is None:
        pidfd = _observe_broker_process(context, installation)
    if expected_phase == "ack":
        allowed_statuses = {("ready", "ready")}
    elif request["operation"] == "preflight":
        allowed_statuses = {("ok", "ready")}
    elif request["operation"] == "reconcile":
        allowed_statuses = {
            ("ok", "committed-late"),
            ("error", "safe-failed"),
            ("error", "security-hold"),
            ("error", "indeterminate"),
        }
    else:
        allowed_statuses = {
            ("ok", "published"),
            ("ok", "committed-late"),
            ("error", "safe-failed"),
            ("error", "security-hold"),
            ("error", "indeterminate"),
        }
    if (response["status"], response["code"]) not in allowed_statuses:
        if pidfd is not None:
            os.close(pidfd)
        _fail("broker-rejected", f"broker rejected publication: {response['code']}")
    if (
        completed > effective_deadline
        and response["code"] in {"ready", "published"}
    ):
        if pidfd is not None:
            os.close(pidfd)
        _fail("invalid-response", "successful broker response exceeded its effective deadline")
    if expected_phase == "ack":
        if response["refs"] is not None:
            if pidfd is not None:
                os.close(pidfd)
            _fail("invalid-response", "broker acknowledgement must not contain refs")
        return context, None, pidfd
    if request["operation"] == "preflight":
        if response["refs"] is not None:
            _fail("invalid-response", "preflight result must not contain refs")
        return context, None, pidfd
    outcome = _verified_publication_outcome(response, request)
    return context, outcome, pidfd


def _verified_publication_outcome(
    response: dict[str, Any],
    request: dict[str, Any],
) -> PublicationOutcome:
    kind = response["code"]
    expected_names = {
        _plan_ref(request["issue"], "authority"),
        _plan_ref(request["issue"], "anchor"),
    }
    if kind == "indeterminate":
        if response["status"] != "error" or response["refs"] is not None:
            _fail(
                "invalid-response",
                "indeterminate result must be a non-success without refs",
            )
        return ReconciliationOutcome("indeterminate", None)
    refs = _object(response["refs"], "response.refs")
    if set(refs) != expected_names:
        _fail(
            "invalid-response",
            "broker result ref names differ from the exact issue pair",
        )
    verified: dict[str, str | None] = {}
    for name in sorted(expected_names):
        oid = refs[name]
        verified[name] = None if oid is None else _sha(oid, f"response ref {name}")
    if kind in {"published", "committed-late"}:
        if response["status"] != "ok" or any(
            oid is None for oid in verified.values()
        ):
            _fail(
                "invalid-response",
                "successful publication result lacks two exact object IDs",
            )
        exact = {
            name: oid
            for name, oid in verified.items()
            if oid is not None
        }
        if kind == "published":
            return PublishedOutcome(exact)
        return ReconciliationOutcome("committed-late", exact)
    if kind == "safe-failed":
        if response["status"] != "error":
            _fail("invalid-response", "safe-failed must be a non-success result")
        return ReconciliationOutcome("safe-failed", verified)
    if kind == "security-hold":
        if response["status"] != "error":
            _fail("invalid-response", "security-hold must be a non-success result")
        return ReconciliationOutcome("security-hold", verified)
    _fail("invalid-response", "broker result is not a closed publication outcome")


def _observe_broker_process(
    context: dict[str, Any],
    installation: dict[str, Any],
) -> int:
    pid = context["broker_pid"]
    if context["broker_uid"] != installation["expected_broker_uid"]:
        _fail("peer-authentication-failed", "signed broker UID differs")
    try:
        pidfd = os.pidfd_open(pid)
        status = Path(f"/proc/{pid}/status").read_text(encoding="ascii")
        uid_map = Path(f"/proc/{pid}/uid_map").read_text(
            encoding="ascii"
        ).splitlines()
        namespace = os.stat(
            f"/proc/{pid}/ns/user", follow_symlinks=True
        ).st_ino
    except OSError as error:
        raise BrokerError(
            "peer-authentication-failed", "cannot observe signed broker process"
        ) from error
    uid_line = next(
        (line for line in status.splitlines() if line.startswith("Uid:")),
        "",
    )
    try:
        observed_uids = {int(value) for value in uid_line.split()[1:]}
    except ValueError as error:
        os.close(pidfd)
        raise BrokerError(
            "peer-authentication-failed", "broker UID status is malformed"
        ) from error
    if (
        observed_uids != {installation["expected_broker_uid"]}
        or namespace != context["broker_user_namespace"]
    ):
        os.close(pidfd)
        _fail(
            "peer-authentication-failed",
            "observed broker principal differs from signed readiness",
        )
    if installation["expected_broker_uid"] == os.geteuid():
        os.close(pidfd)
        _fail(
            "peer-authentication-failed",
            "broker outer host UID is not distinct from the candidate",
        )
    try:
        os.kill(pid, 0)
    except PermissionError:
        pass
    except ProcessLookupError:
        os.close(pidfd)
        _fail("peer-authentication-failed", "signed broker process exited")
    else:
        os.close(pidfd)
        _fail(
            "peer-authentication-failed",
            "candidate can signal the broker process",
        )
    try:
        memory_fd = os.open(f"/proc/{pid}/mem", os.O_RDONLY)
    except PermissionError:
        pass
    except OSError:
        pass
    else:
        os.close(memory_fd)
        os.close(pidfd)
        _fail(
            "peer-authentication-failed",
            "candidate can read broker process memory",
        )
    namespace_uid = context["broker_namespace_uid"]
    mapped_uid = None
    for line in uid_map:
        fields = line.split()
        if len(fields) != 3:
            continue
        inside, outside, length = (int(value) for value in fields)
        if inside <= namespace_uid < inside + length:
            mapped_uid = outside + namespace_uid - inside
            break
    if mapped_uid != installation["expected_broker_uid"]:
        os.close(pidfd)
        _fail(
            "peer-authentication-failed",
            "broker namespace UID does not map to its observed UID",
        )
    return pidfd


def _require_live_pidfd(pidfd: int) -> None:
    poller = select.poll()
    poller.register(pidfd, select.POLLIN | select.POLLHUP | select.POLLERR)
    if poller.poll(0):
        _fail(
            "peer-authentication-failed",
            "signed broker process exited before operation continuation",
        )


def publish_via_connection(
    connection: socket.socket,
    installation: dict[str, Any],
    issue: int,
    pack_path: Path,
    *,
    enforce_peer: bool = True,
) -> PublicationOutcome:
    outcome = _client_operation(
        connection,
        installation,
        issue,
        operation="publish",
        pack_path=pack_path,
        enforce_peer=enforce_peer,
    )
    if outcome is None:
        _fail("invalid-response", "publication lacks a typed outcome")
    return outcome


def preflight_via_connection(
    connection: socket.socket,
    installation: dict[str, Any],
    issue: int,
    *,
    enforce_peer: bool = True,
) -> None:
    _client_operation(
        connection,
        installation,
        issue,
        operation="preflight",
        pack_path=None,
        enforce_peer=enforce_peer,
    )


def reconcile_via_connection(
    connection: socket.socket,
    installation: dict[str, Any],
    issue: int,
    *,
    enforce_peer: bool = True,
) -> ReconciliationOutcome:
    outcome = _client_operation(
        connection,
        installation,
        issue,
        operation="reconcile",
        pack_path=None,
        enforce_peer=enforce_peer,
    )
    if not isinstance(outcome, ReconciliationOutcome):
        _fail("invalid-response", "reconciliation lacks its typed outcome")
    return outcome


def _client_operation(
    connection: socket.socket,
    installation: dict[str, Any],
    issue: int,
    *,
    operation: str,
    pack_path: Path | None,
    enforce_peer: bool,
) -> PublicationOutcome | None:
    _require_unnamed_socket(connection)
    if enforce_peer:
        _pid, uid, _gid = _peer_credentials(connection)
        if uid != installation["expected_capability_uid"]:
            _fail("peer-authentication-failed", "capability issuer principal is not authorized")
    digest = hashlib.sha256()
    if operation == "publish":
        assert pack_path is not None
        try:
            pack_size = pack_path.stat().st_size
        except OSError as error:
            raise BrokerError(
                "pack-unavailable", "cannot inspect object pack"
            ) from error
        if pack_size <= 0 or pack_size > installation["pack_max_bytes"]:
            _fail(
                "oversized-pack",
                "object pack size is outside the installation contract",
            )
        with pack_path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(65536), b""):
                digest.update(chunk)
    else:
        pack_size = 0
    now = datetime_module.datetime.now(datetime_module.timezone.utc)
    deadline_time = now + datetime_module.timedelta(
        seconds=installation["operation_timeout_seconds"]
    )
    request = {
        "schema_version": 1,
        "protocol": PROTOCOL,
        "phase": "request",
        "request_nonce": os.urandom(32).hex(),
        "repository": installation["repository"],
        "issue": _integer(issue, "issue", 1),
        "operation": operation,
        "pack_sha256": digest.hexdigest(),
        "pack_size": pack_size,
        "request_deadline": deadline_time.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    request_deadline = OperationDeadline(deadline_time)
    request_write_error = None
    try:
        _send_frame(
            connection,
            request,
            maximum=REQUEST_MAX_BYTES,
            deadline=request_deadline,
        )
    except OSError as error:
        request_write_error = error
        try:
            connection.shutdown(socket.SHUT_WR)
        except OSError:
            pass
    try:
        ack, _raw_ack = _recv_frame(
            connection,
            maximum=RESPONSE_MAX_BYTES,
            label="broker acknowledgement",
            deadline=None if request_write_error is not None else request_deadline,
        )
    except BrokerError as error:
        if request_write_error is not None:
            raise BrokerError(
                "authenticated-response-unavailable",
                "request write failed without a signed broker rejection",
            ) from error
        raise
    context, _refs, pidfd = _verify_response(
        ack,
        request,
        installation,
        expected_phase="ack",
        now=datetime_module.datetime.now(datetime_module.timezone.utc),
        enforce_broker_process=enforce_peer,
    )
    if request_write_error is not None and ack["status"] == "ready":
        if pidfd is not None:
            os.close(pidfd)
        _fail(
            "invalid-response",
            "broker reported readiness after request write failure",
        )
    try:
        return _finish_client_operation(
            connection,
            request,
            context,
            installation,
            operation=operation,
            pack_path=pack_path,
            pidfd=pidfd,
        )
    finally:
        if pidfd is not None:
            os.close(pidfd)


def _finish_client_operation(
    connection: socket.socket,
    request: dict[str, Any],
    context: dict[str, Any],
    installation: dict[str, Any],
    *,
    operation: str,
    pack_path: Path | None,
    pidfd: int | None,
) -> PublicationOutcome | None:
    effective_deadline = OperationDeadline(
        _time(context["effective_deadline"], "ack.effective_deadline")
    )
    if pidfd is not None:
        _require_live_pidfd(pidfd)
    write_error: OSError | None = None
    continuation = {
        "schema_version": 1,
        "protocol": PROTOCOL,
        "phase": "continue",
        "request_nonce": request["request_nonce"],
        "plan_identity": context["plan_identity"],
    }
    try:
        _send_frame(
            connection,
            continuation,
            maximum=REQUEST_MAX_BYTES,
            deadline=effective_deadline,
        )
    except OSError as error:
        write_error = error
        try:
            connection.shutdown(socket.SHUT_WR)
        except OSError:
            pass
    if operation == "publish" and write_error is None:
        assert pack_path is not None
        try:
            with pack_path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(65536), b""):
                    _socket_timeout(
                        connection,
                        effective_deadline,
                        "while sending object pack",
                        30,
                    )
                    connection.sendall(chunk)
        except OSError as error:
            write_error = error
            try:
                connection.shutdown(socket.SHUT_WR)
            except OSError:
                pass
    final, _raw_final = _recv_frame(
        connection,
        maximum=RESPONSE_MAX_BYTES,
        label="broker result",
        deadline=None,
    )
    _final_context, outcome, _unused_pidfd = _verify_response(
        final,
        request,
        installation,
        expected_phase="result",
        now=datetime_module.datetime.now(datetime_module.timezone.utc),
        expected_context=context,
        enforce_broker_process=False,
    )
    if write_error is not None and final["status"] == "ok":
        _fail(
            "invalid-response",
            "broker reported success after client pack write failure",
        )
    return outcome


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
    service.add_argument("--capability-fd", required=True, type=int)
    client = subparsers.add_parser("publish")
    client.add_argument("--installation", required=True, type=Path)
    client.add_argument("--connection-fd", required=True, type=int)
    client.add_argument("--issue", required=True, type=int)
    client.add_argument("--pack", required=True, type=Path)
    preflight = subparsers.add_parser("preflight")
    preflight.add_argument("--installation", required=True, type=Path)
    preflight.add_argument("--connection-fd", required=True, type=int)
    preflight.add_argument("--issue", required=True, type=int)
    reconcile = subparsers.add_parser("reconcile")
    reconcile.add_argument("--installation", required=True, type=Path)
    reconcile.add_argument("--connection-fd", required=True, type=int)
    reconcile.add_argument("--issue", required=True, type=int)
    return parser


def _outcome_record(outcome: PublicationOutcome) -> dict[str, Any]:
    return {
        "outcome": outcome.kind,
        "refs": outcome.refs,
        "retry_disposition": outcome.retry_disposition,
    }


def main(argv: list[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    try:
        if arguments.mode == "serve":
            _close_unrelated_fds(
                {
                    0,
                    1,
                    2,
                    arguments.connection_fd,
                    arguments.capability_fd,
                }
            )
            _set_parent_death_signal()
            capability = _read_sealed_capability(arguments.capability_fd)
            os.close(arguments.capability_fd)
            installation = _load_broker_installation(arguments.installation.resolve(strict=True))
            with _socket_from_fd(arguments.connection_fd) as connection:
                serve_connection(connection, installation, capability)
            return 0
        installation = _load_client_installation(arguments.installation.resolve(strict=True))
        if arguments.mode in {"preflight", "reconcile"}:
            _close_unrelated_fds({0, 1, 2, arguments.connection_fd})
            with _socket_from_fd(arguments.connection_fd) as connection:
                if arguments.mode == "preflight":
                    preflight_via_connection(
                        connection, installation, arguments.issue
                    )
                    result = {"repository": installation["repository"], "ready": True}
                else:
                    outcome = reconcile_via_connection(
                        connection, installation, arguments.issue
                    )
                    result = _outcome_record(outcome)
            sys.stdout.buffer.write(_normalized_json(result))
            return outcome.exit_code if arguments.mode == "reconcile" else 0
        _close_unrelated_fds({0, 1, 2, arguments.connection_fd})
        with _socket_from_fd(arguments.connection_fd) as connection:
            outcome = publish_via_connection(
                connection, installation, arguments.issue, arguments.pack
            )
        sys.stdout.buffer.write(_normalized_json(_outcome_record(outcome)))
        return outcome.exit_code
    except (BrokerError, OSError) as error:
        code = error.code if isinstance(error, BrokerError) else "broker-io-failed"
        print(f"git-publication-broker: {code}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
