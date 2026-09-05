#!/usr/bin/env python3
"""Execute exact-tree Python programs from immutable Linux descriptors."""

from __future__ import annotations

import base64
import dataclasses
import fcntl
import hashlib
import json
import os
import re
import selectors
import signal
import subprocess
import sys
import time
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Iterable, Mapping, Sequence

from . import reporter


GIT = "/usr/bin/git"
PYTHON = "/usr/bin/python3"
SCHEMA_VERSION = 1
RECEIPT_VERSION = 1
ARTIFACT_ROLES = frozenset({"data", "module", "package", "program"})
AUTHORITIES = frozenset({"base", "origin", "head"})
REGULAR_MODES = frozenset({"100644", "100755"})
MODULE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*$")
HEX_RE = re.compile(r"^[0-9a-f]+$")
CREDENTIAL_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,127}$")
CREDENTIAL_ENV_PREFIX = "SEALED_CAPSULE_CREDENTIAL_"
MAX_ARTIFACTS = 256
MAX_ARTIFACT_BYTES = 2 * 1024 * 1024
MAX_BUNDLE_BYTES = 16 * 1024 * 1024
MAX_REQUEST_BYTES = 1024 * 1024
MAX_OUTPUT_BYTES = 1024 * 1024
MAX_STDERR_BYTES = 64 * 1024
MAX_TIMEOUT_SECONDS = 120.0
REQUIRED_SEALS = (
    fcntl.F_SEAL_WRITE
    | fcntl.F_SEAL_GROW
    | fcntl.F_SEAL_SHRINK
    | fcntl.F_SEAL_SEAL
)


class CapsuleError(Exception):
    """The capsule could not establish or preserve its trust boundary."""


class CapsuleExecutionError(CapsuleError):
    """The sealed child failed without producing a trusted receipt."""

    def __init__(
        self,
        message: str,
        *,
        returncode: int | None = None,
        stdout: bytes = b"",
        stderr: bytes = b"",
    ) -> None:
        super().__init__(message)
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


@dataclasses.dataclass(frozen=True)
class ArtifactSpec:
    """One required exact-tree artifact."""

    authority: str
    revision: str
    path: str
    role: str
    module_name: str | None = None
    expected_mode: str | None = None
    expected_blob_oid: str | None = None


@dataclasses.dataclass(frozen=True)
class ArtifactBundle:
    """Canonical bytes and identities produced from Git objects."""

    payload: bytes
    artifact_ids: tuple[str, ...]


@dataclasses.dataclass(frozen=True)
class CapsuleResult:
    """A successful result and its descriptor-bound receipt."""

    output: dict[str, Any]
    receipt: dict[str, Any]
    receipt_sha256: str


def _reject_constant(value: str) -> None:
    raise CapsuleError(f"non-finite JSON number {value!r} is not permitted")


def _object_no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise CapsuleError(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def parse_json_bytes(raw: bytes, label: str) -> Any:
    try:
        text = raw.decode("utf-8")
        decoder = json.JSONDecoder(
            object_pairs_hook=_object_no_duplicates,
            parse_constant=_reject_constant,
        )
        value, end = decoder.raw_decode(text)
        if text[end:].strip():
            raise CapsuleError(f"{label} contains trailing data")
        return value
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as error:
        raise CapsuleError(f"{label} is not strict UTF-8 JSON: {error}") from error


def normalized_json(value: Any) -> bytes:
    try:
        return (
            json.dumps(
                value,
                allow_nan=False,
                ensure_ascii=True,
                separators=(",", ":"),
                sort_keys=True,
            )
            + "\n"
        ).encode("ascii")
    except (TypeError, ValueError, RecursionError) as error:
        raise CapsuleError(f"value is not canonical JSON: {error}") from error


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _git_environment(repository_root: Path) -> dict[str, str]:
    return {
        "HOME": str(repository_root),
        "LC_ALL": "C",
        "PATH": "/usr/bin:/bin",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_NO_REPLACE_OBJECTS": "1",
        "GIT_NO_LAZY_FETCH": "1",
        "GIT_TERMINAL_PROMPT": "0",
    }


def _run_git(
    repository_root: Path,
    *arguments: str,
    input_bytes: bytes | None = None,
) -> bytes:
    try:
        completed = subprocess.run(
            (GIT, "--no-replace-objects", "-C", str(repository_root), *arguments),
            env=_git_environment(repository_root),
            input=input_bytes,
            check=False,
            capture_output=True,
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise CapsuleError(f"cannot execute bounded Git authority read: {error}") from error
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", errors="replace").strip()
        raise CapsuleError(detail or "trusted Git authority read failed")
    return completed.stdout


def _canonical_path(value: str) -> str:
    if not isinstance(value, str) or not value:
        raise CapsuleError("artifact path must be a nonempty string")
    try:
        decoded = value.encode("utf-8").decode("utf-8")
    except UnicodeError as error:
        raise CapsuleError("artifact path must be valid UTF-8") from error
    path = PurePosixPath(decoded)
    if (
        path.is_absolute()
        or path.as_posix() != decoded
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise CapsuleError(f"artifact path is not canonical: {value!r}")
    return decoded


def _validate_spec(spec: ArtifactSpec) -> ArtifactSpec:
    if spec.authority not in AUTHORITIES:
        raise CapsuleError(f"unknown artifact authority {spec.authority!r}")
    if (
        not isinstance(spec.revision, str)
        or len(spec.revision) not in {40, 64}
        or HEX_RE.fullmatch(spec.revision) is None
    ):
        raise CapsuleError("artifact revision must be an exact lowercase commit ID")
    path = _canonical_path(spec.path)
    if spec.role not in ARTIFACT_ROLES:
        raise CapsuleError(f"unknown artifact role {spec.role!r}")
    if spec.expected_mode is not None and spec.expected_mode not in REGULAR_MODES:
        raise CapsuleError("expected artifact mode must be 100644 or 100755")
    if spec.expected_blob_oid is not None and (
        HEX_RE.fullmatch(spec.expected_blob_oid) is None
        or len(spec.expected_blob_oid) not in {40, 64}
    ):
        raise CapsuleError("expected blob ID must be a lowercase SHA-1 or SHA-256")
    if spec.role in {"module", "package"}:
        if spec.module_name is None or MODULE_RE.fullmatch(spec.module_name) is None:
            raise CapsuleError("module/package artifact needs a canonical module name")
        expected_suffix = "/__init__.py" if spec.role == "package" else ".py"
        if not path.endswith(expected_suffix):
            raise CapsuleError(
                f"{spec.role} artifact path must end with {expected_suffix}"
            )
    elif spec.module_name is not None:
        raise CapsuleError(f"{spec.role} artifact cannot declare a module name")
    return dataclasses.replace(spec, path=path)


def _artifact_id(record: Mapping[str, Any]) -> str:
    identity = {
        key: record[key]
        for key in (
            "authority",
            "revision",
            "tree",
            "path",
            "mode",
            "blob_oid",
            "sha256",
            "role",
            "module_name",
        )
    }
    return _sha256(normalized_json(identity))


def _resolve_artifact(repository_root: Path, spec: ArtifactSpec) -> dict[str, Any]:
    spec = _validate_spec(spec)
    try:
        object_format = _run_git(
            repository_root, "rev-parse", "--show-object-format"
        ).decode("ascii").strip()
        revision = _run_git(
            repository_root, "rev-parse", "--verify", f"{spec.revision}^{{commit}}"
        ).decode("ascii").strip()
        tree = _run_git(repository_root, "rev-parse", f"{revision}^{{tree}}").decode(
            "ascii"
        ).strip()
    except UnicodeDecodeError as error:
        raise CapsuleError("Git returned a non-ASCII object identity") from error
    oid_length = {"sha1": 40, "sha256": 64}.get(object_format)
    if oid_length is None:
        raise CapsuleError(f"unsupported Git object format {object_format!r}")
    if (
        len(revision) != oid_length
        or len(tree) != oid_length
        or HEX_RE.fullmatch(revision) is None
        or HEX_RE.fullmatch(tree) is None
    ):
        raise CapsuleError("Git returned a malformed commit or tree identity")

    raw = _run_git(
        repository_root,
        "ls-tree",
        "-z",
        "--full-tree",
        revision,
        "--",
        spec.path,
    )
    entries = [entry for entry in raw.split(b"\0") if entry]
    if len(entries) != 1:
        raise CapsuleError(
            f"artifact is missing or ambiguous in exact tree: {spec.path}"
        )
    try:
        metadata, raw_path = entries[0].split(b"\t", 1)
        mode, kind, blob_oid = metadata.decode("ascii").split()
        actual_path = raw_path.decode("utf-8")
    except (UnicodeDecodeError, ValueError) as error:
        raise CapsuleError(f"malformed Git tree entry for {spec.path}") from error
    if actual_path != spec.path or kind != "blob" or mode not in REGULAR_MODES:
        raise CapsuleError(f"artifact has an unsafe Git tree entry: {spec.path}")
    if len(blob_oid) != oid_length or HEX_RE.fullmatch(blob_oid) is None:
        raise CapsuleError(f"artifact has a malformed blob ID: {spec.path}")
    if spec.expected_mode is not None and mode != spec.expected_mode:
        raise CapsuleError(f"artifact mode differs from expectation: {spec.path}")
    if spec.expected_blob_oid is not None and blob_oid != spec.expected_blob_oid:
        raise CapsuleError(f"artifact blob differs from expectation: {spec.path}")

    content = _run_git(repository_root, "cat-file", "blob", blob_oid)
    if len(content) > MAX_ARTIFACT_BYTES:
        raise CapsuleError(f"artifact exceeds size limit: {spec.path}")
    try:
        computed_oid = _run_git(
            repository_root, "hash-object", "--stdin", input_bytes=content
        ).decode("ascii").strip()
    except UnicodeDecodeError as error:
        raise CapsuleError("Git returned a non-ASCII computed blob ID") from error
    if computed_oid != blob_oid:
        raise CapsuleError(f"artifact bytes do not match exact blob: {spec.path}")
    if spec.role in {"program", "module", "package"}:
        try:
            compile(content.decode("utf-8"), f"<exact-tree:{spec.path}>", "exec")
        except (UnicodeDecodeError, SyntaxError) as error:
            raise CapsuleError(f"Python artifact is invalid: {spec.path}") from error

    record: dict[str, Any] = {
        "authority": spec.authority,
        "object_format": object_format,
        "revision": revision,
        "tree": tree,
        "path": spec.path,
        "mode": mode,
        "blob_oid": blob_oid,
        "sha256": _sha256(content),
        "role": spec.role,
        "module_name": spec.module_name,
        "content_b64": base64.b64encode(content).decode("ascii"),
    }
    record["artifact_id"] = _artifact_id(record)
    return record


def build_artifact_bundle(
    repository_root: Path,
    specs: Sequence[ArtifactSpec],
) -> ArtifactBundle:
    """Read a bounded, closed artifact set directly from exact Git objects."""

    try:
        root = reporter.validate_repository_root(repository_root)
    except reporter.PilotDataError as error:
        raise CapsuleError(str(error)) from error
    if not specs or len(specs) > MAX_ARTIFACTS:
        raise CapsuleError(f"artifact count must be between 1 and {MAX_ARTIFACTS}")
    records = [_resolve_artifact(root, spec) for spec in specs]
    records.sort(
        key=lambda record: (
            record["authority"],
            record["path"],
            record["role"],
            record["module_name"] or "",
        )
    )
    ids = [record["artifact_id"] for record in records]
    if len(ids) != len(set(ids)):
        raise CapsuleError("artifact bundle contains duplicate identities")
    path_roles = [
        (record["authority"], record["path"], record["role"])
        for record in records
    ]
    if len(path_roles) != len(set(path_roles)):
        raise CapsuleError("artifact bundle contains duplicate path roles")
    modules = [
        record["module_name"]
        for record in records
        if record["module_name"] is not None
    ]
    if len(modules) != len(set(modules)):
        raise CapsuleError("artifact bundle contains duplicate module names")
    payload = normalized_json(
        {"schema_version": SCHEMA_VERSION, "artifacts": records}
    )
    if len(payload) > MAX_BUNDLE_BYTES:
        raise CapsuleError("artifact bundle exceeds size limit")
    validate_artifact_bundle(payload, expected_artifact_ids=ids)
    return ArtifactBundle(payload=payload, artifact_ids=tuple(ids))


def validate_artifact_bundle(
    payload: bytes,
    *,
    expected_artifact_ids: Iterable[str] | None = None,
) -> dict[str, Any]:
    """Validate canonical bundle structure, metadata, and exact closed membership."""

    if len(payload) > MAX_BUNDLE_BYTES:
        raise CapsuleError("artifact bundle exceeds size limit")
    data = parse_json_bytes(payload, "artifact bundle")
    if not isinstance(data, dict) or set(data) != {"schema_version", "artifacts"}:
        raise CapsuleError("artifact bundle has unknown or missing fields")
    if data["schema_version"] != SCHEMA_VERSION:
        raise CapsuleError("artifact bundle schema version is unsupported")
    records = data["artifacts"]
    if not isinstance(records, list) or not records or len(records) > MAX_ARTIFACTS:
        raise CapsuleError("artifact bundle has an invalid artifact count")
    expected_fields = {
        "artifact_id",
        "authority",
        "object_format",
        "revision",
        "tree",
        "path",
        "mode",
        "blob_oid",
        "sha256",
        "role",
        "module_name",
        "content_b64",
    }
    ids: list[str] = []
    path_roles: list[tuple[str, str, str]] = []
    modules: list[str] = []
    sort_keys: list[tuple[str, str, str, str]] = []
    for index, record in enumerate(records):
        label = f"artifact bundle entry {index}"
        if not isinstance(record, dict) or set(record) != expected_fields:
            raise CapsuleError(f"{label} has unknown or missing fields")
        spec = _validate_spec(
            ArtifactSpec(
                authority=record["authority"],
                revision=record["revision"],
                path=record["path"],
                role=record["role"],
                module_name=record["module_name"],
                expected_mode=record["mode"],
                expected_blob_oid=record["blob_oid"],
            )
        )
        oid_length = {"sha1": 40, "sha256": 64}.get(record["object_format"])
        if oid_length is None:
            raise CapsuleError(f"{label} has an unsupported object format")
        for field in ("revision", "tree", "blob_oid"):
            value = record[field]
            if (
                not isinstance(value, str)
                or len(value) != oid_length
                or HEX_RE.fullmatch(value) is None
            ):
                raise CapsuleError(f"{label}.{field} is malformed")
        if not isinstance(record["sha256"], str) or re.fullmatch(
            r"[0-9a-f]{64}", record["sha256"]
        ) is None:
            raise CapsuleError(f"{label}.sha256 is malformed")
        try:
            content = base64.b64decode(record["content_b64"], validate=True)
        except (ValueError, TypeError) as error:
            raise CapsuleError(f"{label} content is not canonical base64") from error
        if base64.b64encode(content).decode("ascii") != record["content_b64"]:
            raise CapsuleError(f"{label} content is not canonical base64")
        if len(content) > MAX_ARTIFACT_BYTES or _sha256(content) != record["sha256"]:
            raise CapsuleError(f"{label} content digest differs")
        expected_id = _artifact_id(record)
        if record["artifact_id"] != expected_id:
            raise CapsuleError(f"{label} identity differs")
        if spec.role in {"program", "module", "package"}:
            try:
                compile(content.decode("utf-8"), f"<sealed:{spec.path}>", "exec")
            except (UnicodeDecodeError, SyntaxError) as error:
                raise CapsuleError(f"{label} Python source is invalid") from error
        ids.append(expected_id)
        path_roles.append((spec.authority, spec.path, spec.role))
        if spec.module_name is not None:
            modules.append(spec.module_name)
        sort_keys.append(
            (spec.authority, spec.path, spec.role, spec.module_name or "")
        )
    if ids != list(dict.fromkeys(ids)):
        raise CapsuleError("artifact bundle contains duplicate identities")
    if path_roles != list(dict.fromkeys(path_roles)):
        raise CapsuleError("artifact bundle contains duplicate path roles")
    if modules != list(dict.fromkeys(modules)):
        raise CapsuleError("artifact bundle contains duplicate module names")
    if sort_keys != sorted(sort_keys):
        raise CapsuleError("artifact bundle is not canonically ordered")
    if normalized_json(data) != payload:
        raise CapsuleError("artifact bundle is not canonical JSON")
    if expected_artifact_ids is not None:
        expected = list(expected_artifact_ids)
        if len(expected) != len(set(expected)):
            raise CapsuleError("expected artifact identities contain duplicates")
        missing = sorted(set(expected) - set(ids))
        extra = sorted(set(ids) - set(expected))
        if missing or extra:
            raise CapsuleError(
                f"artifact membership differs (missing={missing}, extra={extra})"
            )
    return data


def _require_platform() -> None:
    required = (
        "memfd_create",
        "MFD_ALLOW_SEALING",
    )
    missing = [name for name in required if not hasattr(os, name)]
    fcntl_missing = [
        name
        for name in (
            "F_ADD_SEALS",
            "F_GET_SEALS",
            "F_SEAL_WRITE",
            "F_SEAL_GROW",
            "F_SEAL_SHRINK",
            "F_SEAL_SEAL",
        )
        if not hasattr(fcntl, name)
    ]
    if sys.platform != "linux" or missing or fcntl_missing or not Path(
        "/proc/self/fd"
    ).is_dir():
        raise CapsuleError(
            "sealed execution capsules require Linux memfd sealing and /proc/self/fd"
        )


def _write_all(fd: int, payload: bytes) -> None:
    view = memoryview(payload)
    written = 0
    while written < len(payload):
        count = os.write(fd, view[written:])
        if count <= 0:
            raise CapsuleError("sealed descriptor write made no progress")
        written += count


def _descriptor_envelope(kind: str, nonce: str, payload: bytes) -> bytes:
    return normalized_json(
        {
            "kind": kind,
            "nonce": nonce,
            "payload_b64": base64.b64encode(payload).decode("ascii"),
            "payload_sha256": _sha256(payload),
        }
    )


def _create_sealed_descriptor(kind: str, nonce: str, payload: bytes) -> tuple[int, str]:
    _require_platform()
    envelope = _descriptor_envelope(kind, nonce, payload)
    try:
        fd = os.memfd_create(
            f"workflow-capsule-{kind}",
            flags=os.MFD_ALLOW_SEALING | getattr(os, "MFD_CLOEXEC", 0),
        )
        _write_all(fd, envelope)
        os.lseek(fd, 0, os.SEEK_SET)
        fcntl.fcntl(fd, fcntl.F_ADD_SEALS, REQUIRED_SEALS)
        seals = fcntl.fcntl(fd, fcntl.F_GET_SEALS)
        if seals & REQUIRED_SEALS != REQUIRED_SEALS:
            raise CapsuleError(f"{kind} descriptor did not acquire all required seals")
        return fd, _sha256(envelope)
    except BaseException:
        if "fd" in locals():
            os.close(fd)
        raise


def _create_sealed_raw_descriptor(name: str, payload: bytes) -> tuple[int, str]:
    _require_platform()
    try:
        fd = os.memfd_create(
            f"workflow-capsule-{name}",
            flags=os.MFD_ALLOW_SEALING | getattr(os, "MFD_CLOEXEC", 0),
        )
        _write_all(fd, payload)
        os.lseek(fd, 0, os.SEEK_SET)
        fcntl.fcntl(fd, fcntl.F_ADD_SEALS, REQUIRED_SEALS)
        seals = fcntl.fcntl(fd, fcntl.F_GET_SEALS)
        if seals & REQUIRED_SEALS != REQUIRED_SEALS:
            raise CapsuleError(f"{name} descriptor did not acquire all required seals")
        return fd, _sha256(payload)
    except BaseException:
        if "fd" in locals():
            os.close(fd)
        raise


_BOOTSTRAP_SOURCE = r'''#!/usr/bin/python3
import argparse
import base64
import fcntl
import hashlib
import importlib.abc
import importlib.util
import io
import json
import os
import pathlib
import re
import sys
import sysconfig
import traceback
import types

REQUIRED_SEALS = fcntl.F_SEAL_WRITE | fcntl.F_SEAL_GROW | fcntl.F_SEAL_SHRINK | fcntl.F_SEAL_SEAL
ROLES = {"data", "module", "package", "program"}
AUTHORITIES = {"base", "origin", "head"}
MODULE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*$")

class Failure(Exception):
    pass

def reject_constant(value):
    raise Failure("non-finite JSON is forbidden")

def no_duplicates(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise Failure("duplicate JSON key")
        result[key] = value
    return result

def parse_json(raw, label):
    try:
        text = raw.decode("utf-8")
        decoder = json.JSONDecoder(object_pairs_hook=no_duplicates, parse_constant=reject_constant)
        value, end = decoder.raw_decode(text)
        if text[end:].strip():
            raise Failure(label + " contains trailing data")
        return value
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as error:
        raise Failure(label + " is not strict JSON: " + str(error))

def canonical(value):
    try:
        return (json.dumps(value, allow_nan=False, ensure_ascii=True, separators=(",", ":"), sort_keys=True) + "\n").encode("ascii")
    except (TypeError, ValueError, RecursionError) as error:
        raise Failure("value is not canonical JSON: " + str(error))

def sha256(payload):
    return hashlib.sha256(payload).hexdigest()

def read_bounded(fd, maximum, label):
    if fcntl.fcntl(fd, fcntl.F_GET_SEALS) & REQUIRED_SEALS != REQUIRED_SEALS:
        raise Failure(label + " descriptor is not sealed")
    os.lseek(fd, 0, os.SEEK_SET)
    chunks = []
    total = 0
    while True:
        chunk = os.read(fd, min(65536, maximum + 1 - total))
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
        if total > maximum:
            raise Failure(label + " descriptor exceeds size limit")
    return b"".join(chunks)

def decode_descriptor(fd, maximum, label, kind, nonce, envelope_digest):
    raw = read_bounded(fd, maximum, label)
    if sha256(raw) != envelope_digest:
        raise Failure(label + " envelope digest differs")
    envelope = parse_json(raw, label + " envelope")
    if not isinstance(envelope, dict) or set(envelope) != {"kind", "nonce", "payload_b64", "payload_sha256"}:
        raise Failure(label + " envelope fields differ")
    if envelope["kind"] != kind or envelope["nonce"] != nonce:
        raise Failure(label + " envelope identity differs")
    try:
        payload = base64.b64decode(envelope["payload_b64"], validate=True)
    except (TypeError, ValueError) as error:
        raise Failure(label + " payload is not base64") from error
    if base64.b64encode(payload).decode("ascii") != envelope["payload_b64"] or sha256(payload) != envelope["payload_sha256"]:
        raise Failure(label + " payload digest differs")
    return payload

def open_fds():
    result = set()
    for name in os.listdir("/proc/self/fd"):
        try:
            fd = int(name)
            os.fstat(fd)
        except (ValueError, OSError):
            continue
        result.add(fd)
    return result

def artifact_id(record):
    fields = {key: record[key] for key in ("authority", "revision", "tree", "path", "mode", "blob_oid", "sha256", "role", "module_name")}
    return sha256(canonical(fields))

def validate_bundle(raw):
    data = parse_json(raw, "artifact bundle")
    if not isinstance(data, dict) or set(data) != {"schema_version", "artifacts"} or data["schema_version"] != 1:
        raise Failure("artifact bundle header differs")
    records = data["artifacts"]
    if not isinstance(records, list) or not records or len(records) > 256:
        raise Failure("artifact count differs")
    fields = {"artifact_id", "authority", "object_format", "revision", "tree", "path", "mode", "blob_oid", "sha256", "role", "module_name", "content_b64"}
    ids = []
    path_roles = []
    modules = []
    sort_keys = []
    for record in records:
        if not isinstance(record, dict) or set(record) != fields:
            raise Failure("artifact fields differ")
        if record["authority"] not in AUTHORITIES or record["role"] not in ROLES or record["mode"] not in {"100644", "100755"}:
            raise Failure("artifact authority, role, or mode differs")
        path = record["path"]
        if not isinstance(path, str) or not path or pathlib.PurePosixPath(path).is_absolute() or pathlib.PurePosixPath(path).as_posix() != path or any(part in {"", ".", ".."} for part in pathlib.PurePosixPath(path).parts):
            raise Failure("artifact path is not canonical")
        module_name = record["module_name"]
        if record["role"] in {"module", "package"}:
            if not isinstance(module_name, str) or MODULE_RE.fullmatch(module_name) is None:
                raise Failure("module identity differs")
            if record["role"] == "module" and not path.endswith(".py"):
                raise Failure("module path differs")
            if record["role"] == "package" and not path.endswith("/__init__.py"):
                raise Failure("package path differs")
        elif module_name is not None:
            raise Failure("non-module artifact declares module identity")
        oid_length = {"sha1": 40, "sha256": 64}.get(record["object_format"])
        if oid_length is None:
            raise Failure("object format differs")
        for field in ("revision", "tree", "blob_oid"):
            value = record[field]
            if not isinstance(value, str) or len(value) != oid_length or re.fullmatch(r"[0-9a-f]+", value) is None:
                raise Failure("Git object identity differs")
        if not isinstance(record["sha256"], str) or re.fullmatch(r"[0-9a-f]{64}", record["sha256"]) is None:
            raise Failure("artifact digest differs")
        try:
            content = base64.b64decode(record["content_b64"], validate=True)
        except (TypeError, ValueError) as error:
            raise Failure("artifact content is not base64") from error
        if len(content) > 2 * 1024 * 1024 or base64.b64encode(content).decode("ascii") != record["content_b64"] or sha256(content) != record["sha256"]:
            raise Failure("artifact content digest differs")
        if artifact_id(record) != record["artifact_id"]:
            raise Failure("artifact identity differs")
        ids.append(record["artifact_id"])
        path_roles.append((record["authority"], path, record["role"]))
        if module_name is not None:
            modules.append(module_name)
        sort_keys.append((record["authority"], path, record["role"], module_name or ""))
        record["_content"] = content
    if len(ids) != len(set(ids)) or len(path_roles) != len(set(path_roles)) or len(modules) != len(set(modules)):
        raise Failure("artifact bundle contains duplicates")
    if sort_keys != sorted(sort_keys):
        raise Failure("artifact bundle ordering differs")
    clean = {"schema_version": 1, "artifacts": [{key: value for key, value in record.items() if key != "_content"} for record in records]}
    if canonical(clean) != raw:
        raise Failure("artifact bundle is not canonical")
    return records

class BundleLoader(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    def __init__(self, records):
        self.modules = {record["module_name"]: record for record in records if record["module_name"] is not None}
    def find_spec(self, fullname, path=None, target=None):
        record = self.modules.get(fullname)
        if record is not None:
            return importlib.util.spec_from_loader(fullname, self, origin="<sealed:" + record["path"] + ">", is_package=record["role"] == "package")
        if fullname == "sealed_capsule" or fullname.split(".", 1)[0] in sys.stdlib_module_names:
            return None
        raise ModuleNotFoundError("module is not present in sealed artifact closure: " + fullname)
    def create_module(self, spec):
        return None
    def exec_module(self, module):
        record = self.modules[module.__spec__.name]
        module.__file__ = "<sealed:" + record["path"] + ">"
        module.__loader__ = self
        if record["role"] == "package":
            module.__path__ = []
        exec(compile(record["_content"].decode("utf-8"), module.__file__, "exec", dont_inherit=True), module.__dict__)

def install_api(records, request_value, credentials):
    api = types.ModuleType("sealed_capsule")
    by_key = {(record["authority"], record["path"], record["role"]): record for record in records}
    def read_artifact(path, *, authority, role="data"):
        record = by_key.get((authority, path, role))
        if record is None:
            raise Failure("artifact is not present in sealed closure")
        return bytes(record["_content"])
    def artifact_metadata(path, *, authority, role):
        record = by_key.get((authority, path, role))
        if record is None:
            raise Failure("artifact is not present in sealed closure")
        return {key: value for key, value in record.items() if key not in {"_content", "content_b64"}}
    api.request = request_value
    api.credentials = credentials
    api.read_artifact = read_artifact
    api.artifact_metadata = artifact_metadata
    sys.modules["sealed_capsule"] = api

def install_filesystem_guard(roots):
    sys.path[:] = [entry for entry in sys.path if entry and "site-packages" not in entry and any(os.path.realpath(entry) == root or os.path.realpath(entry).startswith(root + os.sep) for root in roots)]
    def audit(event, args):
        if event != "open" or not args:
            return
        raw = args[0]
        if isinstance(raw, int):
            return
        try:
            path = os.path.realpath(os.fspath(raw))
        except (TypeError, ValueError):
            raise PermissionError("capsule filesystem access is forbidden")
        mode = args[1] if len(args) > 1 else "r"
        flags = args[2] if len(args) > 2 else 0
        read_only = (isinstance(mode, str) and all(flag not in mode for flag in "wax+")) or (mode is None and isinstance(flags, int) and flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND) == 0)
        if not read_only or not any(path == root or path.startswith(root + os.sep) for root in roots):
            raise PermissionError("capsule filesystem access is forbidden")
    sys.addaudithook(audit)

def main():
    parser = argparse.ArgumentParser(add_help=False)
    for name in ("bootstrap", "program", "request", "bundle"):
        parser.add_argument("--" + name + "-fd", type=int, required=True)
        parser.add_argument("--" + name + "-envelope-sha256", required=True)
    parser.add_argument("--nonce", required=True)
    parser.add_argument("--program-artifact-id", required=True)
    args = parser.parse_args()
    fds = [args.bootstrap_fd, args.program_fd, args.request_fd, args.bundle_fd]
    if len(set(fds)) != len(fds) or any(fd < 3 for fd in fds):
        raise Failure("capsule descriptors must be distinct inherited FDs")
    inherited = open_fds()
    if inherited != {0, 1, 2, *fds}:
        raise Failure("capsule inherited an unexpected descriptor")
    bootstrap_raw = read_bounded(args.bootstrap_fd, 1024 * 1024, "bootstrap")
    if sha256(bootstrap_raw) != args.bootstrap_envelope_sha256:
        raise Failure("bootstrap descriptor digest differs")
    program = decode_descriptor(args.program_fd, 3 * 1024 * 1024, "program", "program", args.nonce, args.program_envelope_sha256)
    request_raw = decode_descriptor(args.request_fd, 2 * 1024 * 1024, "request", "request", args.nonce, args.request_envelope_sha256)
    bundle_raw = decode_descriptor(args.bundle_fd, 24 * 1024 * 1024, "bundle", "bundle", args.nonce, args.bundle_envelope_sha256)
    request_value = parse_json(request_raw, "request")
    records = validate_bundle(bundle_raw)
    selected = [record for record in records if record["artifact_id"] == args.program_artifact_id]
    if len(selected) != 1 or selected[0]["role"] != "program" or selected[0]["_content"] != program:
        raise Failure("executed program is not the selected sealed bundle artifact")
    for fd in fds:
        os.close(fd)
    roots = []
    for key in ("stdlib", "platstdlib"):
        value = sysconfig.get_path(key)
        if value:
            roots.append(os.path.realpath(value))
    credentials = {}
    for key in tuple(os.environ):
        if key.startswith("SEALED_CAPSULE_CREDENTIAL_"):
            credentials[key[len("SEALED_CAPSULE_CREDENTIAL_"):]] = os.environ.pop(key)
    loader = BundleLoader(records)
    sys.meta_path.insert(0, loader)
    install_api(records, request_value, credentials)
    install_filesystem_guard(roots)
    sys.dont_write_bytecode = True
    sys.argv = [selected[0]["path"]]
    sys.stdin = io.StringIO(request_raw.decode("utf-8"))
    globals_dict = {
        "__builtins__": __builtins__,
        "__file__": "<sealed:" + selected[0]["path"] + ">",
        "__name__": "__main__",
        "__package__": None,
        "__spec__": None,
    }
    exec(compile(program.decode("utf-8"), globals_dict["__file__"], "exec", dont_inherit=True), globals_dict)
    return 0

if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except BaseException:
        traceback.print_exc(file=sys.stderr)
        raise SystemExit(70)
'''


def _request_bytes(request: Any) -> bytes:
    if isinstance(request, bytes):
        if len(request) > MAX_REQUEST_BYTES:
            raise CapsuleError("request exceeds size limit")
        parse_json_bytes(request, "request")
        return request
    payload = normalized_json(request)
    if len(payload) > MAX_REQUEST_BYTES:
        raise CapsuleError("request exceeds size limit")
    return payload


def _program_from_bundle(bundle: dict[str, Any], artifact_id: str) -> bytes:
    selected = [
        record
        for record in bundle["artifacts"]
        if record["artifact_id"] == artifact_id
    ]
    if len(selected) != 1 or selected[0]["role"] != "program":
        raise CapsuleError("program artifact ID must select exactly one program")
    return base64.b64decode(selected[0]["content_b64"], validate=True)


def _kill_process_group(process: subprocess.Popen[bytes]) -> None:
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    if process.poll() is None:
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


def _bounded_communicate(
    process: subprocess.Popen[bytes],
    timeout: float,
) -> tuple[bytes, bytes]:
    assert process.stdout is not None
    assert process.stderr is not None
    selector = selectors.DefaultSelector()
    streams = {process.stdout.fileno(): ("stdout", process.stdout), process.stderr.fileno(): ("stderr", process.stderr)}
    for _name, stream in streams.values():
        os.set_blocking(stream.fileno(), False)
        selector.register(stream, selectors.EVENT_READ)
    buffers = {"stdout": bytearray(), "stderr": bytearray()}
    deadline = time.monotonic() + timeout
    try:
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise CapsuleExecutionError(
                    "capsule timed out",
                    stdout=bytes(buffers["stdout"]),
                    stderr=bytes(buffers["stderr"]),
                )
            events = selector.select(min(remaining, 0.1))
            if not events and process.poll() is not None:
                events = [(key, selectors.EVENT_READ) for key in selector.get_map().values()]
            for key, _mask in events:
                name, stream = streams[key.fd]
                chunk = os.read(key.fd, 65536)
                if not chunk:
                    selector.unregister(stream)
                    continue
                buffers[name].extend(chunk)
                limit = MAX_OUTPUT_BYTES if name == "stdout" else MAX_STDERR_BYTES
                if len(buffers[name]) > limit:
                    raise CapsuleExecutionError(
                        f"capsule {name} exceeds size limit",
                        stdout=bytes(buffers["stdout"][: MAX_OUTPUT_BYTES + 1]),
                        stderr=bytes(buffers["stderr"][: MAX_STDERR_BYTES + 1]),
                    )
        remaining = max(0.0, deadline - time.monotonic())
        process.wait(timeout=remaining)
        return bytes(buffers["stdout"]), bytes(buffers["stderr"])
    finally:
        selector.close()


def _bootstrap_command(
    *,
    bootstrap_fd: int,
    bootstrap_digest: str,
    program_fd: int,
    program_digest: str,
    request_fd: int,
    request_digest: str,
    bundle_fd: int,
    bundle_digest: str,
    nonce: str,
    program_artifact_id: str,
) -> tuple[str, ...]:
    return (
        PYTHON,
        "-I",
        f"/proc/self/fd/{bootstrap_fd}",
        "--bootstrap-fd",
        str(bootstrap_fd),
        "--bootstrap-envelope-sha256",
        bootstrap_digest,
        "--program-fd",
        str(program_fd),
        "--program-envelope-sha256",
        program_digest,
        "--request-fd",
        str(request_fd),
        "--request-envelope-sha256",
        request_digest,
        "--bundle-fd",
        str(bundle_fd),
        "--bundle-envelope-sha256",
        bundle_digest,
        "--nonce",
        nonce,
        "--program-artifact-id",
        program_artifact_id,
    )


def execute_capsule(
    bundle: ArtifactBundle | bytes,
    *,
    program_artifact_id: str,
    request: Any,
    timeout: float = 30.0,
    credentials: Mapping[str, str] | None = None,
    _before_spawn: Callable[[], None] | None = None,
    _after_spawn: Callable[[int], None] | None = None,
) -> CapsuleResult:
    """Execute one program from a closed bundle and return a digest-bound receipt."""

    _require_platform()
    if not isinstance(timeout, (int, float)) or isinstance(timeout, bool):
        raise CapsuleError("timeout must be numeric")
    timeout = float(timeout)
    if timeout <= 0 or timeout > MAX_TIMEOUT_SECONDS:
        raise CapsuleError(
            f"timeout must be greater than zero and at most {MAX_TIMEOUT_SECONDS}"
        )
    if isinstance(bundle, ArtifactBundle):
        bundle_bytes = bundle.payload
        expected_ids: Iterable[str] | None = bundle.artifact_ids
    elif isinstance(bundle, bytes):
        bundle_bytes = bundle
        expected_ids = None
    else:
        raise CapsuleError("bundle must be ArtifactBundle or bytes")
    bundle_data = validate_artifact_bundle(
        bundle_bytes, expected_artifact_ids=expected_ids
    )
    program = _program_from_bundle(bundle_data, program_artifact_id)
    request_bytes = _request_bytes(request)
    if credentials is not None:
        if not isinstance(credentials, Mapping) or any(
            not isinstance(key, str)
            or CREDENTIAL_NAME_RE.fullmatch(key) is None
            or not isinstance(value, str)
            or "\0" in value
            for key, value in credentials.items()
        ):
            raise CapsuleError(
                "credentials must map canonical uppercase names to strings"
            )

    nonce = os.urandom(32).hex()
    descriptors: list[int] = []
    process: subprocess.Popen[bytes] | None = None
    try:
        bootstrap_fd, bootstrap_envelope_digest = _create_sealed_raw_descriptor(
            "bootstrap", _BOOTSTRAP_SOURCE.encode("utf-8")
        )
        descriptors.append(bootstrap_fd)
        program_fd, program_envelope_digest = _create_sealed_descriptor(
            "program", nonce, program
        )
        descriptors.append(program_fd)
        request_fd, request_envelope_digest = _create_sealed_descriptor(
            "request", nonce, request_bytes
        )
        descriptors.append(request_fd)
        bundle_fd, bundle_envelope_digest = _create_sealed_descriptor(
            "bundle", nonce, bundle_bytes
        )
        descriptors.append(bundle_fd)
        for fd in descriptors:
            if fcntl.fcntl(fd, fcntl.F_GET_SEALS) & REQUIRED_SEALS != REQUIRED_SEALS:
                raise CapsuleError("descriptor seal verification failed before launch")
        if _before_spawn is not None:
            _before_spawn()
        environment = {
            "HOME": "/",
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "PATH": "/usr/bin:/bin",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONHASHSEED": "0",
        }
        if credentials:
            environment.update(
                {
                    CREDENTIAL_ENV_PREFIX + key: value
                    for key, value in credentials.items()
                }
            )
        command = _bootstrap_command(
            bootstrap_fd=bootstrap_fd,
            bootstrap_digest=bootstrap_envelope_digest,
            program_fd=program_fd,
            program_digest=program_envelope_digest,
            request_fd=request_fd,
            request_digest=request_envelope_digest,
            bundle_fd=bundle_fd,
            bundle_digest=bundle_envelope_digest,
            nonce=nonce,
            program_artifact_id=program_artifact_id,
        )
        process = subprocess.Popen(
            command,
            cwd="/",
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            close_fds=True,
            pass_fds=tuple(descriptors),
            start_new_session=True,
        )
        if _after_spawn is not None:
            _after_spawn(process.pid)
        try:
            stdout, stderr = _bounded_communicate(process, timeout)
        except BaseException:
            _kill_process_group(process)
            raise
        if process.returncode != 0:
            raise CapsuleExecutionError(
                "capsule child failed",
                returncode=process.returncode,
                stdout=stdout,
                stderr=stderr,
            )
        output = parse_json_bytes(stdout, "capsule output")
        if not isinstance(output, dict):
            raise CapsuleExecutionError(
                "capsule output must be a JSON object",
                returncode=process.returncode,
                stdout=stdout,
                stderr=stderr,
            )
        receipt = {
            "schema_version": RECEIPT_VERSION,
            "program_artifact_id": program_artifact_id,
            "program_sha256": _sha256(program),
            "artifact_bundle_sha256": _sha256(bundle_bytes),
            "request_sha256": _sha256(request_bytes),
            "output_sha256": _sha256(stdout),
        }
        return CapsuleResult(
            output=output,
            receipt=receipt,
            receipt_sha256=_sha256(normalized_json(receipt)),
        )
    except CapsuleExecutionError:
        raise
    except CapsuleError:
        raise
    except BaseException as error:
        if process is not None:
            _kill_process_group(process)
        raise CapsuleExecutionError("capsule launch was interrupted") from error
    finally:
        if process is not None:
            _kill_process_group(process)
            if process.stdout is not None:
                process.stdout.close()
            if process.stderr is not None:
                process.stderr.close()
        for fd in reversed(descriptors):
            try:
                os.close(fd)
            except OSError:
                pass
