#!/usr/bin/env python3
"""Execute exact-tree Python programs from immutable Linux descriptors."""

from __future__ import annotations

import base64
import ast
import dataclasses
import fcntl
import hashlib
import hmac
import json
import os
import re
import select
import selectors
import signal
import subprocess
import sys
import time
import weakref
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Iterable, Mapping, Sequence

from . import reporter


GIT = "/usr/bin/git"
PYTHON = "/usr/bin/python3"
CC = "/usr/bin/cc"
SCHEMA_VERSION = 2
RECEIPT_VERSION = 2
ARTIFACT_ROLES = frozenset({"data", "module", "package", "program"})
AUTHORITIES = frozenset({"base", "origin", "head"})
REGULAR_MODES = frozenset({"100644", "100755"})
MODULE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*$")
HEX_RE = re.compile(r"^[0-9a-f]+$")
STDLIB_MODULE_RE = MODULE_RE
FORBIDDEN_STDLIB_MODULES = frozenset(
    {
        "_ctypes",
        "ctypes",
        "mmap",
    }
)
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
class _ArtifactSpec:
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
    stdlib_modules: tuple[str, ...]


@dataclasses.dataclass(frozen=True)
class _ArtifactRule:
    """One exact path admitted by a trusted authority policy."""

    path: str
    role: str
    module_name: str | None
    mode: str
    blob_oid: str


@dataclasses.dataclass(frozen=True)
class _AuthorityRecord:
    """The one exact commit/tree and closed artifact set for an authority."""

    authority: str
    revision: str
    tree: str
    object_format: str
    artifacts: tuple[_ArtifactRule, ...]


@dataclasses.dataclass(frozen=True)
class ArtifactRequest:
    """A path/role requested from an already verified authority capability."""

    authority: str
    path: str
    role: str
    module_name: str | None = None


class VerifiedAuthorityPolicy:
    """Opaque, non-serializable capability minted by authenticated policy loading."""

    __slots__ = ("__weakref__",)

    def __new__(cls, *args, **kwargs):
        raise TypeError(
            "VerifiedAuthorityPolicy can only be minted by "
            "load_verified_authority_policy"
        )

    def __copy__(self):
        raise TypeError("verified authority capabilities cannot be copied")

    def __deepcopy__(self, memo):
        raise TypeError("verified authority capabilities cannot be copied")

    def __reduce__(self):
        raise TypeError("verified authority capabilities cannot be serialized")

    def __repr__(self):
        return "<VerifiedAuthorityPolicy opaque>"


@dataclasses.dataclass(frozen=True)
class _VerifiedAuthorityState:
    repository_root: Path
    repository: str
    context: str
    authorities: tuple[_AuthorityRecord, ...]
    relationships: tuple[tuple[str, str], ...]
    stdlib_modules: tuple[str, ...]
    contract_sha256: str


AUTHORITY_CONTRACT_DOMAIN = b"workflow-sealed-authority-contract-v1\0"


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


def _validate_spec(spec: _ArtifactSpec) -> _ArtifactSpec:
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


def _resolve_artifact(repository_root: Path, spec: _ArtifactSpec) -> dict[str, Any]:
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


def _canonical_stdlib_modules(modules: Iterable[str]) -> tuple[str, ...]:
    values = tuple(modules)
    if any(
        not isinstance(name, str)
        or STDLIB_MODULE_RE.fullmatch(name) is None
        or name.split(".", 1)[0] in FORBIDDEN_STDLIB_MODULES
        for name in values
    ):
        raise CapsuleError("standard-library module allowlist is invalid or unsafe")
    canonical = tuple(sorted(set(values)))
    if values != canonical:
        raise CapsuleError("standard-library module allowlist must be unique and sorted")
    return canonical


def _authority_policies(records: Sequence[dict[str, Any]]) -> tuple[_AuthorityRecord, ...]:
    policies = []
    for authority in sorted({record["authority"] for record in records}):
        selected = [record for record in records if record["authority"] == authority]
        revisions = {record["revision"] for record in selected}
        trees = {record["tree"] for record in selected}
        formats = {record["object_format"] for record in selected}
        if len(revisions) != 1 or len(trees) != 1 or len(formats) != 1:
            raise CapsuleError(
                f"authority {authority!r} must use exactly one revision, tree, and object format"
            )
        rules = tuple(
            _ArtifactRule(
                path=record["path"],
                role=record["role"],
                module_name=record["module_name"],
                mode=record["mode"],
                blob_oid=record["blob_oid"],
            )
            for record in selected
        )
        policies.append(
            _AuthorityRecord(
                authority=authority,
                revision=selected[0]["revision"],
                tree=selected[0]["tree"],
                object_format=selected[0]["object_format"],
                artifacts=rules,
            )
        )
    return tuple(policies)


def _policy_json(policy: _AuthorityRecord) -> dict[str, Any]:
    return {
        "authority": policy.authority,
        "revision": policy.revision,
        "tree": policy.tree,
        "object_format": policy.object_format,
        "artifacts": [
            {
                "path": rule.path,
                "role": rule.role,
                "module_name": rule.module_name,
                "mode": rule.mode,
                "blob_oid": rule.blob_oid,
            }
            for rule in policy.artifacts
        ],
    }


def _validate_python_import_closure(
    records: Sequence[dict[str, Any]],
    stdlib_modules: Sequence[str],
) -> None:
    bundled_modules = {
        record["module_name"]
        for record in records
        if record["module_name"] is not None
    }
    admitted_stdlib = set(stdlib_modules)
    for record in records:
        if record["role"] not in {"program", "module", "package"}:
            continue
        source = base64.b64decode(record["content_b64"], validate=True).decode("utf-8")
        syntax = ast.parse(source, filename=f"<exact-tree:{record['path']}>")
        package = record["module_name"] if record["role"] == "package" else None
        if record["role"] == "module" and record["module_name"] is not None:
            package = record["module_name"].rpartition(".")[0]
        for node in ast.walk(syntax):
            imported: list[str] = []
            if isinstance(node, ast.Import):
                imported.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    if not package:
                        raise CapsuleError(
                            f"relative import has no package authority: {record['path']}"
                        )
                    parts = package.split(".")
                    if node.level > len(parts):
                        raise CapsuleError(
                            f"relative import escapes package authority: {record['path']}"
                        )
                    prefix = ".".join(parts[: len(parts) - node.level + 1])
                    if node.module:
                        imported.append(f"{prefix}.{node.module}" if prefix else node.module)
                    else:
                        imported.extend(
                            f"{prefix}.{alias.name}" if prefix else alias.name
                            for alias in node.names
                        )
                elif node.module:
                    imported.append(node.module)
            for module_name in imported:
                if (
                    module_name != "sealed_capsule"
                    and module_name not in bundled_modules
                    and not any(
                        module_name == allowed
                        or module_name.startswith(allowed + ".")
                        for allowed in admitted_stdlib
                    )
                ):
                    raise CapsuleError(
                        f"Python import is outside the closed bundle/stdlib policy: "
                        f"{record['path']} imports {module_name}"
                    )


def _expect_exact_keys(
    value: Any,
    label: str,
    keys: set[str],
) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise CapsuleError(f"{label} has unknown or missing fields")
    return value


def _authority_capability_accessors():
    registry: weakref.WeakKeyDictionary[
        VerifiedAuthorityPolicy, _VerifiedAuthorityState
    ] = weakref.WeakKeyDictionary()

    def mint(state: _VerifiedAuthorityState) -> VerifiedAuthorityPolicy:
        capability = object.__new__(VerifiedAuthorityPolicy)
        registry[capability] = state
        return capability

    def get(capability: VerifiedAuthorityPolicy) -> _VerifiedAuthorityState:
        if type(capability) is not VerifiedAuthorityPolicy:
            raise CapsuleError(
                "execution requires an exact verified authority capability"
            )
        try:
            return registry[capability]
        except (KeyError, TypeError) as error:
            raise CapsuleError(
                "authority capability was not minted by the trusted policy loader"
            ) from error

    return mint, get


_mint_verified_authority, _verified_authority_state = (
    _authority_capability_accessors()
)
del _authority_capability_accessors


def load_verified_authority_policy(
    repository_root: Path,
    authenticated_contract: bytes,
    *,
    hmac_key: bytes,
    expected_context: str,
) -> VerifiedAuthorityPolicy:
    """Authenticate and verify an exact-SHA authority contract, then mint a capability."""

    if not isinstance(authenticated_contract, bytes) or len(authenticated_contract) > (
        MAX_BUNDLE_BYTES
    ):
        raise CapsuleError("authenticated authority contract is invalid or oversized")
    if not isinstance(hmac_key, bytes) or len(hmac_key) < 32:
        raise CapsuleError("authority contract HMAC key must contain at least 32 bytes")
    if not isinstance(expected_context, str) or not expected_context:
        raise CapsuleError("authority contract context must be nonempty")
    envelope = _expect_exact_keys(
        parse_json_bytes(authenticated_contract, "authority contract envelope"),
        "authority contract envelope",
        {"schema_version", "payload_b64", "hmac_sha256"},
    )
    if type(envelope["schema_version"]) is not int or envelope["schema_version"] != 1:
        raise CapsuleError("authority contract envelope version is unsupported")
    if normalized_json(envelope) != authenticated_contract:
        raise CapsuleError("authority contract envelope is not canonical JSON")
    try:
        payload = base64.b64decode(envelope["payload_b64"], validate=True)
    except (TypeError, ValueError) as error:
        raise CapsuleError("authority contract payload is not canonical base64") from error
    if base64.b64encode(payload).decode("ascii") != envelope["payload_b64"]:
        raise CapsuleError("authority contract payload is not canonical base64")
    expected_seal = hmac.new(
        hmac_key,
        AUTHORITY_CONTRACT_DOMAIN + payload,
        hashlib.sha256,
    ).hexdigest()
    if (
        not isinstance(envelope["hmac_sha256"], str)
        or re.fullmatch(r"[0-9a-f]{64}", envelope["hmac_sha256"]) is None
        or not hmac.compare_digest(envelope["hmac_sha256"], expected_seal)
    ):
        raise CapsuleError("authority contract authentication failed")
    contract = _expect_exact_keys(
        parse_json_bytes(payload, "authority contract"),
        "authority contract",
        {
            "schema_version",
            "repository",
            "context",
            "object_format",
            "authorities",
            "relationships",
            "stdlib_modules",
        },
    )
    if type(contract["schema_version"]) is not int or contract["schema_version"] != 1:
        raise CapsuleError("authority contract version is unsupported")
    if contract["context"] != expected_context:
        raise CapsuleError("authority contract context differs")
    if (
        not isinstance(contract["repository"], str)
        or not contract["repository"]
        or contract["object_format"] not in {"sha1", "sha256"}
    ):
        raise CapsuleError("authority contract repository or object format is invalid")
    allowed_stdlib = _canonical_stdlib_modules(contract["stdlib_modules"])
    try:
        root = reporter.validate_repository_root(repository_root)
        remote = _run_git(root, "config", "--get", "remote.origin.url").decode(
            "utf-8"
        ).strip()
        repository = reporter._github_repository_from_remote(remote)
        object_format = _run_git(
            root, "rev-parse", "--show-object-format"
        ).decode("ascii").strip()
    except reporter.PilotDataError as error:
        raise CapsuleError(str(error)) from error
    except UnicodeDecodeError as error:
        raise CapsuleError("cannot verify authority contract repository identity") from error
    if repository != contract["repository"]:
        raise CapsuleError("authority contract repository identity differs")
    if object_format != contract["object_format"]:
        raise CapsuleError("authority contract object format differs")

    raw_authorities = contract["authorities"]
    if not isinstance(raw_authorities, list) or not raw_authorities:
        raise CapsuleError("authority contract must contain authorities")
    authorities: list[_AuthorityRecord] = []
    for index, raw_authority in enumerate(raw_authorities):
        label = f"authority contract authorities[{index}]"
        authority = _expect_exact_keys(
            raw_authority,
            label,
            {"authority", "revision", "tree", "artifacts"},
        )
        name = authority["authority"]
        if name not in AUTHORITIES:
            raise CapsuleError(f"{label}.authority is unsupported")
        revision = authority["revision"]
        tree = authority["tree"]
        oid_length = {"sha1": 40, "sha256": 64}[object_format]
        if (
            not isinstance(revision, str)
            or len(revision) != oid_length
            or HEX_RE.fullmatch(revision) is None
            or not isinstance(tree, str)
            or len(tree) != oid_length
            or HEX_RE.fullmatch(tree) is None
        ):
            raise CapsuleError(f"{label} has malformed exact Git identities")
        actual_revision = _run_git(
            root, "rev-parse", "--verify", f"{revision}^{{commit}}"
        ).decode("ascii").strip()
        actual_tree = _run_git(root, "rev-parse", f"{revision}^{{tree}}").decode(
            "ascii"
        ).strip()
        if actual_revision != revision or actual_tree != tree:
            raise CapsuleError(f"{label} does not match exact Git authority")
        raw_rules = authority["artifacts"]
        if not isinstance(raw_rules, list) or not raw_rules:
            raise CapsuleError(f"{label}.artifacts must be nonempty")
        rules: list[_ArtifactRule] = []
        for rule_index, raw_rule in enumerate(raw_rules):
            rule_label = f"{label}.artifacts[{rule_index}]"
            value = _expect_exact_keys(
                raw_rule,
                rule_label,
                {"path", "role", "module_name", "mode", "blob_oid"},
            )
            spec = _validate_spec(
                _ArtifactSpec(
                    authority=name,
                    revision=revision,
                    path=value["path"],
                    role=value["role"],
                    module_name=value["module_name"],
                    expected_mode=value["mode"],
                    expected_blob_oid=value["blob_oid"],
                )
            )
            _resolve_artifact(root, spec)
            rules.append(
                _ArtifactRule(
                    path=spec.path,
                    role=spec.role,
                    module_name=spec.module_name,
                    mode=spec.expected_mode,
                    blob_oid=spec.expected_blob_oid,
                )
            )
        if len({rule.path for rule in rules}) != len(rules):
            raise CapsuleError(f"{label} contains duplicate artifact paths")
        if rules != sorted(rules, key=lambda rule: rule.path):
            raise CapsuleError(f"{label} artifact rules must be sorted by path")
        authorities.append(
            _AuthorityRecord(
                authority=name,
                revision=revision,
                tree=tree,
                object_format=object_format,
                artifacts=tuple(rules),
            )
        )
    canonical_authorities = tuple(
        sorted(authorities, key=lambda authority: authority.authority)
    )
    if authorities != list(canonical_authorities) or len(
        {authority.authority for authority in authorities}
    ) != len(authorities):
        raise CapsuleError("authority contract authorities must be unique and sorted")

    raw_relationships = contract["relationships"]
    if not isinstance(raw_relationships, list):
        raise CapsuleError("authority contract relationships must be a list")
    relationships: list[tuple[str, str]] = []
    authority_by_name = {
        authority.authority: authority for authority in canonical_authorities
    }
    for index, raw_relationship in enumerate(raw_relationships):
        relationship = _expect_exact_keys(
            raw_relationship,
            f"authority contract relationships[{index}]",
            {"ancestor", "descendant"},
        )
        edge = (relationship["ancestor"], relationship["descendant"])
        if (
            edge[0] not in authority_by_name
            or edge[1] not in authority_by_name
            or edge[0] == edge[1]
        ):
            raise CapsuleError("authority contract relationship is invalid")
        completed = subprocess.run(
            (
                GIT,
                "--no-replace-objects",
                "-C",
                str(root),
                "merge-base",
                "--is-ancestor",
                authority_by_name[edge[0]].revision,
                authority_by_name[edge[1]].revision,
            ),
            env=_git_environment(root),
            check=False,
            capture_output=True,
            timeout=60,
        )
        if completed.returncode != 0:
            raise CapsuleError(
                f"authority relationship {edge[0]}->{edge[1]} is not satisfied"
            )
        relationships.append(edge)
    if relationships != sorted(set(relationships)):
        raise CapsuleError("authority contract relationships must be unique and sorted")
    canonical_payload = normalized_json(contract)
    if canonical_payload != payload:
        raise CapsuleError("authority contract payload is not canonical JSON")
    state = _VerifiedAuthorityState(
        repository_root=root,
        repository=repository,
        context=expected_context,
        authorities=canonical_authorities,
        relationships=tuple(relationships),
        stdlib_modules=allowed_stdlib,
        contract_sha256=_sha256(payload),
    )
    return _mint_verified_authority(state)


def build_artifact_bundle(
    capability: VerifiedAuthorityPolicy,
    requests: Sequence[ArtifactRequest],
) -> ArtifactBundle:
    """Build only artifacts admitted by an authenticated verified authority."""

    state = _verified_authority_state(capability)
    if not requests or len(requests) > MAX_ARTIFACTS:
        raise CapsuleError(f"artifact count must be between 1 and {MAX_ARTIFACTS}")
    rules = {
        (authority.authority, rule.path): (authority, rule)
        for authority in state.authorities
        for rule in authority.artifacts
    }
    specs: list[_ArtifactSpec] = []
    seen: set[tuple[str, str]] = set()
    for request in requests:
        if type(request) is not ArtifactRequest:
            raise CapsuleError("artifact requests must use exact ArtifactRequest values")
        path = _canonical_path(request.path)
        key = (request.authority, path)
        if key in seen:
            raise CapsuleError("artifact requests contain duplicate authority paths")
        seen.add(key)
        admitted = rules.get(key)
        if admitted is None:
            raise CapsuleError("artifact request is not admitted by verified authority")
        authority, rule = admitted
        if (
            request.role != rule.role
            or request.module_name != rule.module_name
        ):
            raise CapsuleError("artifact request role differs from verified authority")
        specs.append(
            _ArtifactSpec(
                authority=authority.authority,
                revision=authority.revision,
                path=rule.path,
                role=rule.role,
                module_name=rule.module_name,
                expected_mode=rule.mode,
                expected_blob_oid=rule.blob_oid,
            )
        )
    return _build_artifact_bundle_from_specs(
        state.repository_root,
        specs,
        stdlib_modules=state.stdlib_modules,
    )


def _build_artifact_bundle_from_specs(
    repository_root: Path,
    specs: Sequence[_ArtifactSpec],
    *,
    stdlib_modules: Sequence[str] = (),
) -> ArtifactBundle:
    """Read a bounded, closed artifact set directly from exact Git objects."""

    try:
        root = reporter.validate_repository_root(repository_root)
    except reporter.PilotDataError as error:
        raise CapsuleError(str(error)) from error
    if not specs or len(specs) > MAX_ARTIFACTS:
        raise CapsuleError(f"artifact count must be between 1 and {MAX_ARTIFACTS}")
    validated_specs = [_validate_spec(spec) for spec in specs]
    revisions_by_authority: dict[str, set[str]] = {}
    for spec in validated_specs:
        revisions_by_authority.setdefault(spec.authority, set()).add(spec.revision)
    mixed = sorted(
        authority
        for authority, revisions in revisions_by_authority.items()
        if len(revisions) != 1
    )
    if mixed:
        raise CapsuleError(
            "each authority must identify exactly one revision: " + ", ".join(mixed)
        )
    allowed_stdlib = _canonical_stdlib_modules(stdlib_modules)
    records = [_resolve_artifact(root, spec) for spec in validated_specs]
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
    paths = [(record["authority"], record["path"]) for record in records]
    if len(paths) != len(set(paths)):
        raise CapsuleError("artifact bundle contains duplicate authority paths")
    modules = [
        record["module_name"]
        for record in records
        if record["module_name"] is not None
    ]
    if len(modules) != len(set(modules)):
        raise CapsuleError("artifact bundle contains duplicate module names")
    _validate_python_import_closure(records, allowed_stdlib)
    policies = _authority_policies(records)
    payload = normalized_json(
        {
            "schema_version": SCHEMA_VERSION,
            "authorities": [_policy_json(policy) for policy in policies],
            "stdlib_modules": list(allowed_stdlib),
            "artifacts": records,
        }
    )
    if len(payload) > MAX_BUNDLE_BYTES:
        raise CapsuleError("artifact bundle exceeds size limit")
    validate_artifact_bundle(payload, expected_artifact_ids=ids)
    return ArtifactBundle(
        payload=payload,
        artifact_ids=tuple(ids),
        stdlib_modules=allowed_stdlib,
    )


def validate_artifact_bundle(
    payload: bytes,
    *,
    expected_artifact_ids: Iterable[str] | None = None,
) -> dict[str, Any]:
    """Validate bundle structure only; this function does not grant authority."""

    if len(payload) > MAX_BUNDLE_BYTES:
        raise CapsuleError("artifact bundle exceeds size limit")
    data = parse_json_bytes(payload, "artifact bundle")
    if not isinstance(data, dict) or set(data) != {
        "schema_version",
        "authorities",
        "stdlib_modules",
        "artifacts",
    }:
        raise CapsuleError("artifact bundle has unknown or missing fields")
    if type(data["schema_version"]) is not int or data["schema_version"] != SCHEMA_VERSION:
        raise CapsuleError("artifact bundle schema version is unsupported")
    stdlib_modules = data["stdlib_modules"]
    if not isinstance(stdlib_modules, list):
        raise CapsuleError("artifact bundle standard-library allowlist must be a list")
    _canonical_stdlib_modules(stdlib_modules)
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
    paths: list[tuple[str, str]] = []
    modules: list[str] = []
    sort_keys: list[tuple[str, str, str, str]] = []
    for index, record in enumerate(records):
        label = f"artifact bundle entry {index}"
        if not isinstance(record, dict) or set(record) != expected_fields:
            raise CapsuleError(f"{label} has unknown or missing fields")
        spec = _validate_spec(
            _ArtifactSpec(
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
        paths.append((spec.authority, spec.path))
        if spec.module_name is not None:
            modules.append(spec.module_name)
        sort_keys.append(
            (spec.authority, spec.path, spec.role, spec.module_name or "")
        )
    if ids != list(dict.fromkeys(ids)):
        raise CapsuleError("artifact bundle contains duplicate identities")
    if paths != list(dict.fromkeys(paths)):
        raise CapsuleError("artifact bundle contains duplicate authority paths")
    if modules != list(dict.fromkeys(modules)):
        raise CapsuleError("artifact bundle contains duplicate module names")
    if sort_keys != sorted(sort_keys):
        raise CapsuleError("artifact bundle is not canonically ordered")
    if normalized_json(data) != payload:
        raise CapsuleError("artifact bundle is not canonical JSON")
    policies = _authority_policies(records)
    expected_policy_json = [_policy_json(policy) for policy in policies]
    if data["authorities"] != expected_policy_json:
        raise CapsuleError("artifact bundle authority map differs from its artifacts")
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


def verify_artifact_bundle(
    capability: VerifiedAuthorityPolicy,
    bundle: ArtifactBundle,
) -> dict[str, Any]:
    """Rebuild the closed bundle from trusted Git immediately before sealing."""

    state = _verified_authority_state(capability)
    if not isinstance(bundle, ArtifactBundle):
        raise CapsuleError("public execution requires a constructed ArtifactBundle")
    if bundle.stdlib_modules != state.stdlib_modules:
        raise CapsuleError("trusted standard-library policy differs from the bundle")
    parsed = validate_artifact_bundle(
        bundle.payload,
        expected_artifact_ids=bundle.artifact_ids,
    )
    requests = tuple(
        ArtifactRequest(
            authority=record["authority"],
            path=record["path"],
            role=record["role"],
            module_name=record["module_name"],
        )
        for record in parsed["artifacts"]
    )
    try:
        root = reporter.validate_repository_root(state.repository_root)
    except reporter.PilotDataError as error:
        raise CapsuleError(str(error)) from error
    root_fd = -1
    try:
        root_fd = os.open(
            root,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
        )
        before = os.fstat(root_fd)
        rebuilt = build_artifact_bundle(capability, requests)
        after = os.fstat(root_fd)
        path_state = os.stat(root, follow_symlinks=False)
        identity = (before.st_dev, before.st_ino, before.st_mode)
        if identity != (after.st_dev, after.st_ino, after.st_mode) or identity != (
            path_state.st_dev,
            path_state.st_ino,
            path_state.st_mode,
        ):
            raise CapsuleError("repository authority root changed during verification")
    except OSError as error:
        raise CapsuleError("cannot hold a stable no-follow repository root") from error
    finally:
        if root_fd >= 0:
            os.close(root_fd)
    if rebuilt.payload != bundle.payload or rebuilt.artifact_ids != bundle.artifact_ids:
        raise CapsuleError("artifact bundle bytes differ from trusted Git authority")
    return validate_artifact_bundle(
        rebuilt.payload,
        expected_artifact_ids=rebuilt.artifact_ids,
    )


def _require_platform() -> None:
    required = (
        "memfd_create",
        "MFD_ALLOW_SEALING",
        "pidfd_open",
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
    if (
        sys.platform != "linux"
        or os.uname().machine != "x86_64"
        or missing
        or fcntl_missing
        or not Path("/proc/self/fd").is_dir()
        or not os.access(CC, os.X_OK)
    ):
        raise CapsuleError(
            "sealed execution capsules require Linux x86_64, memfd sealing, "
            "/proc/self/fd, unprivileged namespaces, and /usr/bin/cc"
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


_SANDBOX_LAUNCHER_SOURCE = r'''
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <linux/audit.h>
#include <linux/filter.h>
#include <linux/capability.h>
#include <linux/landlock.h>
#include <linux/seccomp.h>
#include <poll.h>
#include <sched.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <signal.h>
#include <string.h>
#include <sys/prctl.h>
#include <sys/resource.h>
#include <sys/mount.h>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <unistd.h>

#if !defined(__x86_64__)
#error "sealed capsule launcher currently supports Linux x86_64 only"
#endif

#define DENY_SYSCALL(name) \
    BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K, __NR_##name, 0, 1), \
    BPF_STMT(BPF_RET | BPF_K, SECCOMP_RET_ERRNO | EPERM)
#define FAIL(stage) \
    do { \
        dprintf(2, "capsule-launcher:%s:%s\n", stage, strerror(errno)); \
        return 125; \
    } while (0)

static int parse_fd(const char *text)
{
    char *end = NULL;
    long value;
    errno = 0;
    value = strtol(text, &end, 10);
    if (errno != 0 || end == text || *end != '\0' || value < 3 || value > 63)
        return -1;
    return (int)value;
}

static int keep_fd(
    int fd,
    int program_fd,
    int request_fd,
    int bundle_fd,
    int ready_fd,
    int launcher_fd)
{
    return fd <= 2 || fd == program_fd || fd == request_fd ||
        fd == bundle_fd || fd == ready_fd || fd == launcher_fd;
}

static int set_limit(int resource, rlim_t value)
{
    struct rlimit limit = { value, value };
    return setrlimit(resource, &limit);
}

static int write_text(const char *path, const char *text)
{
    int fd = open(path, O_WRONLY | O_CLOEXEC);
    size_t length = strlen(text);
    ssize_t written;
    if (fd < 0)
        return -1;
    written = write(fd, text, length);
    if (close(fd) != 0 || written != (ssize_t)length)
        return -1;
    return 0;
}

static int setup_user_namespace(uid_t uid, gid_t gid)
{
    char mapping[128];
    if (unshare(CLONE_NEWUSER) != 0)
        return -1;
    if (write_text("/proc/self/setgroups", "deny\n") != 0 && errno != ENOENT)
        return -1;
    if (snprintf(mapping, sizeof(mapping), "0 %lu 1\n", (unsigned long)uid) <= 0 ||
        write_text("/proc/self/uid_map", mapping) != 0)
        return -1;
    if (snprintf(mapping, sizeof(mapping), "0 %lu 1\n", (unsigned long)gid) <= 0 ||
        write_text("/proc/self/gid_map", mapping) != 0)
        return -1;
    if (setresgid(0, 0, 0) != 0 || setresuid(0, 0, 0) != 0)
        return -1;
    return 0;
}

static int add_landlock_path(int ruleset_fd, const char *path, __u64 access)
{
    int path_fd = open(path, O_PATH | O_CLOEXEC);
    struct landlock_path_beneath_attr rule = {
        .allowed_access = access,
        .parent_fd = path_fd,
    };
    int result;
    if (path_fd < 0)
        return -1;
    result = syscall(
        SYS_landlock_add_rule,
        ruleset_fd,
        LANDLOCK_RULE_PATH_BENEATH,
        &rule,
        0);
    close(path_fd);
    return result;
}

static int setup_landlock(void)
{
    const __u64 read_execute =
        LANDLOCK_ACCESS_FS_EXECUTE |
        LANDLOCK_ACCESS_FS_READ_FILE |
        LANDLOCK_ACCESS_FS_READ_DIR;
    struct landlock_ruleset_attr ruleset = {
        .handled_access_fs =
            read_execute |
            LANDLOCK_ACCESS_FS_WRITE_FILE |
            LANDLOCK_ACCESS_FS_REMOVE_DIR |
            LANDLOCK_ACCESS_FS_REMOVE_FILE |
            LANDLOCK_ACCESS_FS_MAKE_CHAR |
            LANDLOCK_ACCESS_FS_MAKE_DIR |
            LANDLOCK_ACCESS_FS_MAKE_REG |
            LANDLOCK_ACCESS_FS_MAKE_SOCK |
            LANDLOCK_ACCESS_FS_MAKE_FIFO |
            LANDLOCK_ACCESS_FS_MAKE_BLOCK |
            LANDLOCK_ACCESS_FS_MAKE_SYM |
            LANDLOCK_ACCESS_FS_REFER |
            LANDLOCK_ACCESS_FS_TRUNCATE,
    };
    int ruleset_fd = syscall(
        SYS_landlock_create_ruleset,
        &ruleset,
        sizeof(ruleset),
        0);
    if (ruleset_fd < 0)
        return -1;
    if (add_landlock_path(ruleset_fd, "/usr", read_execute) != 0 ||
        (access("/lib", F_OK) == 0 &&
            add_landlock_path(ruleset_fd, "/lib", read_execute) != 0) ||
        (access("/lib64", F_OK) == 0 &&
            add_landlock_path(ruleset_fd, "/lib64", read_execute) != 0) ||
        syscall(SYS_landlock_restrict_self, ruleset_fd, 0) != 0) {
        close(ruleset_fd);
        return -1;
    }
    return close(ruleset_fd);
}

static int drop_capabilities(void)
{
    struct __user_cap_header_struct header = {
        .version = _LINUX_CAPABILITY_VERSION_3,
        .pid = 0,
    };
    struct __user_cap_data_struct data[2] = {{0}};
    return syscall(SYS_capset, &header, &data);
}

static int install_filter(int python_fd)
{
    struct sock_filter filter[] = {
        BPF_STMT(BPF_LD | BPF_W | BPF_ABS, offsetof(struct seccomp_data, arch)),
        BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K, AUDIT_ARCH_X86_64, 1, 0),
        BPF_STMT(BPF_RET | BPF_K, SECCOMP_RET_KILL_PROCESS),
        BPF_STMT(BPF_LD | BPF_W | BPF_ABS, offsetof(struct seccomp_data, nr)),
        BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K, __NR_execveat, 0, 6),
        BPF_STMT(BPF_LD | BPF_W | BPF_ABS, offsetof(struct seccomp_data, args[0])),
        BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K, (unsigned int)python_fd, 0, 3),
        BPF_STMT(BPF_LD | BPF_W | BPF_ABS, offsetof(struct seccomp_data, args[4])),
        BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K, AT_EMPTY_PATH, 0, 1),
        BPF_STMT(BPF_RET | BPF_K, SECCOMP_RET_ALLOW),
        BPF_STMT(BPF_RET | BPF_K, SECCOMP_RET_ERRNO | EPERM),
        DENY_SYSCALL(clone),
        DENY_SYSCALL(clone3),
        DENY_SYSCALL(fork),
        DENY_SYSCALL(vfork),
        DENY_SYSCALL(execve),
        DENY_SYSCALL(socket),
        DENY_SYSCALL(socketpair),
        DENY_SYSCALL(connect),
        DENY_SYSCALL(bind),
        DENY_SYSCALL(listen),
        DENY_SYSCALL(accept),
        DENY_SYSCALL(accept4),
        DENY_SYSCALL(sendto),
        DENY_SYSCALL(recvfrom),
        DENY_SYSCALL(ptrace),
        DENY_SYSCALL(process_vm_readv),
        DENY_SYSCALL(process_vm_writev),
        DENY_SYSCALL(mount),
        DENY_SYSCALL(umount2),
        DENY_SYSCALL(pivot_root),
        DENY_SYSCALL(chroot),
        DENY_SYSCALL(unshare),
        DENY_SYSCALL(setns),
        DENY_SYSCALL(setsid),
        DENY_SYSCALL(setpgid),
        DENY_SYSCALL(prctl),
        DENY_SYSCALL(seccomp),
        DENY_SYSCALL(bpf),
        DENY_SYSCALL(perf_event_open),
        DENY_SYSCALL(userfaultfd),
        DENY_SYSCALL(io_uring_setup),
        DENY_SYSCALL(io_uring_enter),
        DENY_SYSCALL(io_uring_register),
        DENY_SYSCALL(memfd_create),
        DENY_SYSCALL(pidfd_open),
        DENY_SYSCALL(pidfd_getfd),
        DENY_SYSCALL(pidfd_send_signal),
        DENY_SYSCALL(open_by_handle_at),
        DENY_SYSCALL(name_to_handle_at),
        DENY_SYSCALL(init_module),
        DENY_SYSCALL(finit_module),
        DENY_SYSCALL(delete_module),
        DENY_SYSCALL(kexec_load),
        DENY_SYSCALL(add_key),
        DENY_SYSCALL(request_key),
        DENY_SYSCALL(keyctl),
        BPF_STMT(BPF_RET | BPF_K, SECCOMP_RET_ALLOW),
    };
    struct sock_fprog program = {
        .len = (unsigned short)(sizeof(filter) / sizeof(filter[0])),
        .filter = filter,
    };
    return prctl(PR_SET_SECCOMP, SECCOMP_MODE_FILTER, &program);
}

int main(int argc, char **argv, char **environment)
{
    int program_fd;
    int request_fd;
    int bundle_fd;
    int ready_fd;
    int launcher_fd;
    int python_fd;
    long maximum_fd;
    pid_t parent;
    pid_t child;
    pid_t worker;
    uid_t uid = getuid();
    gid_t gid = getgid();
    int status;
    int lifeline[2];

    if (argc < 10 || strcmp(argv[6], "--") != 0 ||
        strcmp(argv[7], "/usr/bin/python3") != 0)
        FAIL("arguments");
    program_fd = parse_fd(argv[1]);
    request_fd = parse_fd(argv[2]);
    bundle_fd = parse_fd(argv[3]);
    ready_fd = parse_fd(argv[4]);
    launcher_fd = parse_fd(argv[5]);
    if (program_fd < 0 || request_fd < 0 || bundle_fd < 0 || ready_fd < 0 ||
        launcher_fd < 0 ||
        program_fd == request_fd || program_fd == bundle_fd ||
        program_fd == ready_fd || request_fd == bundle_fd ||
        request_fd == ready_fd || bundle_fd == ready_fd ||
        launcher_fd == program_fd || launcher_fd == request_fd ||
        launcher_fd == bundle_fd || launcher_fd == ready_fd)
        FAIL("descriptors");

    parent = getppid();
    if (parent <= 0 || prctl(PR_SET_PDEATHSIG, SIGKILL) != 0 ||
        getppid() != parent)
        FAIL("parent-death");
    if (setup_user_namespace(uid, gid) != 0 ||
        unshare(
            CLONE_NEWNS | CLONE_NEWNET | CLONE_NEWIPC |
            CLONE_NEWUTS | CLONE_NEWPID) != 0 ||
        sethostname("sealed-capsule", 14) != 0 ||
        pipe2(lifeline, O_CLOEXEC) != 0)
        FAIL("namespaces");
    child = fork();
    if (child < 0)
        FAIL("pid-namespace-fork");
    if (child > 0) {
        close(lifeline[0]);
        close(program_fd);
        close(request_fd);
        close(bundle_fd);
        close(ready_fd);
        close(launcher_fd);
        while (waitpid(child, &status, 0) < 0) {
            if (errno != EINTR)
                FAIL("wait");
        }
        close(lifeline[1]);
        if (WIFEXITED(status))
            return WEXITSTATUS(status);
        if (WIFSIGNALED(status))
            return 128 + WTERMSIG(status);
        return 125;
    }

    close(lifeline[1]);
    if (prctl(PR_SET_PDEATHSIG, SIGKILL) != 0)
        FAIL("pid-namespace-init-parent");
    worker = fork();
    if (worker < 0)
        FAIL("worker-fork");
    if (worker > 0) {
        struct pollfd watch = {
            .fd = lifeline[0],
            .events = POLLIN | POLLHUP | POLLERR,
        };
        close(program_fd);
        close(request_fd);
        close(bundle_fd);
        close(ready_fd);
        close(launcher_fd);
        while (1) {
            pid_t waited = waitpid(worker, &status, WNOHANG);
            if (waited == worker)
                break;
            if (waited < 0 && errno != EINTR)
                FAIL("worker-wait");
            if (poll(&watch, 1, 100) < 0 && errno != EINTR)
                FAIL("lifeline-poll");
            if (watch.revents & (POLLHUP | POLLERR)) {
                kill(-1, SIGKILL);
                while (waitpid(worker, &status, 0) < 0 && errno == EINTR)
                    ;
                return 125;
            }
        }
        close(lifeline[0]);
        kill(-1, SIGKILL);
        {
            int cleanup_status;
            while (1) {
                pid_t cleaned = waitpid(-1, &cleanup_status, 0);
                if (cleaned > 0 || (cleaned < 0 && errno == EINTR))
                    continue;
                if (cleaned < 0 && errno == ECHILD)
                    break;
                FAIL("namespace-cleanup");
            }
        }
        if (WIFEXITED(status))
            return WEXITSTATUS(status);
        if (WIFSIGNALED(status))
            return 128 + WTERMSIG(status);
        return 125;
    }

    close(lifeline[0]);
    parent = getppid();
    if (parent <= 0 || prctl(PR_SET_PDEATHSIG, SIGKILL) != 0 ||
        getppid() != parent)
        FAIL("worker-parent");
    maximum_fd = sysconf(_SC_OPEN_MAX);
    if (maximum_fd < 0 || maximum_fd > 65536)
        maximum_fd = 65536;
    for (int fd = 3; fd < maximum_fd; ++fd) {
        if (!keep_fd(
                fd,
                program_fd,
                request_fd,
                bundle_fd,
                ready_fd,
                launcher_fd) &&
            fcntl(fd, F_GETFD) != -1)
            FAIL("unexpected-descriptor");
    }
    if (close(launcher_fd) != 0 ||
        prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) != 0 ||
        setup_landlock() != 0 ||
        drop_capabilities() != 0)
        FAIL("privilege-drop");
    python_fd = open("/usr/bin/python3", O_PATH | O_CLOEXEC);
    if (python_fd < 0 || python_fd > 63)
        FAIL("python");

    if (set_limit(RLIMIT_CORE, 0) != 0 ||
        set_limit(RLIMIT_FSIZE, 2 * 1024 * 1024) != 0 ||
        set_limit(RLIMIT_NOFILE, 32) != 0 ||
        set_limit(RLIMIT_CPU, 120) != 0 ||
        set_limit(RLIMIT_AS, 512 * 1024 * 1024) != 0)
        FAIL("limits");
    if (install_filter(python_fd) != 0)
        FAIL("seccomp");
    if (write(ready_fd, "R", 1) != 1 || close(ready_fd) != 0)
        FAIL("ready");
    syscall(SYS_execveat, python_fd, "", &argv[7], environment, AT_EMPTY_PATH);
    FAIL("execveat");
}
'''
SANDBOX_COMPILER_FLAGS = (
    "-pipe",
    "-O2",
    "-std=c11",
    "-Wall",
    "-Wextra",
    "-Werror",
    "-x",
    "c",
    "-",
)


def _compile_sandbox_launcher() -> tuple[int, str]:
    _require_platform()
    fd = -1
    try:
        fd = os.memfd_create(
            "workflow-capsule-sandbox-launcher",
            flags=os.MFD_ALLOW_SEALING | getattr(os, "MFD_CLOEXEC", 0),
        )
        completed = subprocess.run(
            (
                CC,
                *SANDBOX_COMPILER_FLAGS,
                "-o",
                f"/proc/self/fd/{fd}",
            ),
            input=_SANDBOX_LAUNCHER_SOURCE.encode("ascii"),
            env={
                "HOME": "/",
                "LANG": "C",
                "LC_ALL": "C",
                "PATH": "/usr/bin:/bin",
                "TMPDIR": "/",
            },
            check=False,
            capture_output=True,
            pass_fds=(fd,),
            timeout=60,
        )
        if completed.returncode != 0:
            detail = completed.stderr.decode("utf-8", errors="replace").strip()
            raise CapsuleError(
                "cannot compile sealed sandbox launcher"
                + (f": {detail}" if detail else "")
            )
        size = os.fstat(fd).st_size
        if size <= 0 or size > 256 * 1024:
            raise CapsuleError("compiled sandbox launcher has an invalid size")
        os.lseek(fd, 0, os.SEEK_SET)
        if os.read(fd, 4) != b"\x7fELF":
            raise CapsuleError("compiled sandbox launcher is not an ELF executable")
        os.lseek(fd, 0, os.SEEK_SET)
        fcntl.fcntl(fd, fcntl.F_ADD_SEALS, REQUIRED_SEALS)
        if fcntl.fcntl(fd, fcntl.F_GET_SEALS) & REQUIRED_SEALS != REQUIRED_SEALS:
            raise CapsuleError("sandbox launcher did not acquire required seals")
        payload = bytearray()
        while True:
            chunk = os.read(fd, 65536)
            if not chunk:
                break
            payload.extend(chunk)
        os.lseek(fd, 0, os.SEEK_SET)
        return fd, _sha256(bytes(payload))
    except BaseException:
        if fd >= 0:
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
import re
import sys
import traceback
import types

REQUIRED_SEALS = fcntl.F_SEAL_WRITE | fcntl.F_SEAL_GROW | fcntl.F_SEAL_SHRINK | fcntl.F_SEAL_SEAL
ROLES = {"data", "module", "package", "program"}
AUTHORITIES = {"base", "origin", "head"}
MODULE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*$")
FORBIDDEN_STDLIB = {"_ctypes", "ctypes", "mmap"}

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

def canonical_path(path):
    if not isinstance(path, str) or not path or path.startswith("/") or path.endswith("/") or "//" in path:
        return False
    return all(part not in {"", ".", ".."} for part in path.split("/"))

def artifact_id(record):
    fields = {key: record[key] for key in ("authority", "revision", "tree", "path", "mode", "blob_oid", "sha256", "role", "module_name")}
    return sha256(canonical(fields))

def validate_bundle(raw):
    data = parse_json(raw, "artifact bundle")
    if not isinstance(data, dict) or set(data) != {"schema_version", "authorities", "stdlib_modules", "artifacts"} or type(data["schema_version"]) is not int or data["schema_version"] != 2:
        raise Failure("artifact bundle header differs")
    stdlib_modules = data["stdlib_modules"]
    if not isinstance(stdlib_modules, list) or stdlib_modules != sorted(set(stdlib_modules)):
        raise Failure("standard-library policy differs")
    if any(not isinstance(name, str) or MODULE_RE.fullmatch(name) is None or name.split(".", 1)[0] in FORBIDDEN_STDLIB for name in stdlib_modules):
        raise Failure("standard-library policy is unsafe")
    records = data["artifacts"]
    if not isinstance(records, list) or not records or len(records) > 256:
        raise Failure("artifact count differs")
    fields = {"artifact_id", "authority", "object_format", "revision", "tree", "path", "mode", "blob_oid", "sha256", "role", "module_name", "content_b64"}
    ids = []
    paths = []
    modules = []
    sort_keys = []
    for record in records:
        if not isinstance(record, dict) or set(record) != fields:
            raise Failure("artifact fields differ")
        if record["authority"] not in AUTHORITIES or record["role"] not in ROLES or record["mode"] not in {"100644", "100755"}:
            raise Failure("artifact authority, role, or mode differs")
        path = record["path"]
        if not canonical_path(path):
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
        paths.append((record["authority"], path))
        if module_name is not None:
            modules.append(module_name)
        sort_keys.append((record["authority"], path, record["role"], module_name or ""))
        record["_content"] = content
    if len(ids) != len(set(ids)) or len(paths) != len(set(paths)) or len(modules) != len(set(modules)):
        raise Failure("artifact bundle contains duplicates")
    if sort_keys != sorted(sort_keys):
        raise Failure("artifact bundle ordering differs")
    authority_rows = []
    for authority in sorted(set(record["authority"] for record in records)):
        selected = [record for record in records if record["authority"] == authority]
        revisions = set(record["revision"] for record in selected)
        trees = set(record["tree"] for record in selected)
        formats = set(record["object_format"] for record in selected)
        if len(revisions) != 1 or len(trees) != 1 or len(formats) != 1:
            raise Failure("authority uses mixed revisions, trees, or object formats")
        authority_rows.append({
            "authority": authority,
            "revision": selected[0]["revision"],
            "tree": selected[0]["tree"],
            "object_format": selected[0]["object_format"],
            "artifacts": [{
                "path": record["path"],
                "role": record["role"],
                "module_name": record["module_name"],
                "mode": record["mode"],
                "blob_oid": record["blob_oid"],
            } for record in selected],
        })
    if data["authorities"] != authority_rows:
        raise Failure("artifact authority map differs")
    clean = {"schema_version": 2, "authorities": authority_rows, "stdlib_modules": stdlib_modules, "artifacts": [{key: value for key, value in record.items() if key != "_content"} for record in records]}
    if canonical(clean) != raw:
        raise Failure("artifact bundle is not canonical")
    return records, set(stdlib_modules)

class BundleLoader(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    def __init__(self, records, stdlib_modules):
        self.modules = {record["module_name"]: record for record in records if record["module_name"] is not None}
        self.stdlib_modules = stdlib_modules
    def find_spec(self, fullname, path=None, target=None):
        record = self.modules.get(fullname)
        if record is not None:
            return importlib.util.spec_from_loader(fullname, self, origin="<sealed:" + record["path"] + ">", is_package=record["role"] == "package")
        if fullname == "sealed_capsule" or any(fullname == name or fullname.startswith(name + ".") for name in self.stdlib_modules):
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

def install_api(records, request_value):
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
    api.read_artifact = read_artifact
    api.artifact_metadata = artifact_metadata
    sys.modules["sealed_capsule"] = api

def install_guards(records, stdlib_modules):
    admitted = set(stdlib_modules)
    admitted.update(record["module_name"] for record in records if record["module_name"] is not None)
    admitted.add("sealed_capsule")
    sys.path[:] = []
    def audit(event, args):
        if event == "open" and args:
            if isinstance(args[0], int):
                return
            raise PermissionError("capsule filesystem access is forbidden")
        if event in {"os.listdir", "os.scandir"}:
            raise PermissionError("capsule filesystem traversal is forbidden")
        if event == "import" and args:
            name = args[0]
            if isinstance(name, str) and any(name == allowed or name.startswith(allowed + ".") for allowed in admitted):
                return
            raise ImportError("module is not present in the sealed import policy: " + str(name))
    sys.addaudithook(audit)
    for name in tuple(sys.modules):
        if (
            name in {"builtins", "_frozen_importlib", "_frozen_importlib_external"}
            or any(name == allowed or name.startswith(allowed + ".") for allowed in admitted)
        ):
            continue
        else:
            sys.modules.pop(name, None)

def preload_stdlib(stdlib_modules):
    for name in stdlib_modules:
        if name.split(".", 1)[0] in FORBIDDEN_STDLIB:
            raise Failure("standard-library policy is unsafe")
        importlib.import_module(name)

def clear_environment():
    for key in tuple(os.environ):
        del os.environ[key]

def main():
    parser = argparse.ArgumentParser(add_help=False)
    for name in ("program", "request", "bundle"):
        parser.add_argument("--" + name + "-fd", type=int, required=True)
        parser.add_argument("--" + name + "-envelope-sha256", required=True)
    parser.add_argument("--nonce", required=True)
    parser.add_argument("--program-artifact-id", required=True)
    args = parser.parse_args()
    fds = [args.program_fd, args.request_fd, args.bundle_fd]
    if len(set(fds)) != len(fds) or any(fd < 3 or fd > 63 for fd in fds):
        raise Failure("capsule descriptors must be distinct inherited FDs")
    program = decode_descriptor(args.program_fd, 3 * 1024 * 1024, "program", "program", args.nonce, args.program_envelope_sha256)
    request_raw = decode_descriptor(args.request_fd, 2 * 1024 * 1024, "request", "request", args.nonce, args.request_envelope_sha256)
    bundle_raw = decode_descriptor(args.bundle_fd, 24 * 1024 * 1024, "bundle", "bundle", args.nonce, args.bundle_envelope_sha256)
    request_value = parse_json(request_raw, "request")
    records, stdlib_modules = validate_bundle(bundle_raw)
    selected = [record for record in records if record["artifact_id"] == args.program_artifact_id]
    if len(selected) != 1 or selected[0]["role"] != "program" or selected[0]["_content"] != program:
        raise Failure("executed program is not the selected sealed bundle artifact")
    for fd in fds:
        os.close(fd)
    preload_stdlib(stdlib_modules)
    loader = BundleLoader(records, stdlib_modules)
    sys.meta_path.insert(0, loader)
    install_api(records, request_value)
    clear_environment()
    install_guards(records, stdlib_modules)
    sys.dont_write_bytecode = True
    sys.argv = [selected[0]["path"]]
    sys.orig_argv = list(sys.argv)
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
    if process.poll() is not None:
        return
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


def _bootstrap_arguments(
    *,
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
        "-S",
        "-c",
        _BOOTSTRAP_SOURCE,
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


def _sandbox_command(
    *,
    launcher_fd: int,
    program_fd: int,
    request_fd: int,
    bundle_fd: int,
    ready_fd: int,
    bootstrap_arguments: Sequence[str],
) -> tuple[str, ...]:
    return (
        f"/proc/self/fd/{launcher_fd}",
        str(program_fd),
        str(request_fd),
        str(bundle_fd),
        str(ready_fd),
        str(launcher_fd),
        "--",
        *bootstrap_arguments,
    )


def _wait_sandbox_ready(
    process: subprocess.Popen[bytes],
    ready_fd: int,
    timeout: float = 10.0,
) -> None:
    poller = select.poll()
    poller.register(ready_fd, select.POLLIN | select.POLLHUP | select.POLLERR)
    events = poller.poll(int(timeout * 1000))
    if not events:
        raise CapsuleExecutionError("sandbox did not establish containment")
    ready = os.read(ready_fd, 2)
    if ready != b"R":
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            _kill_process_group(process)
        stderr = b""
        if process.stderr is not None:
            stderr = process.stderr.read(MAX_STDERR_BYTES + 1)
        raise CapsuleExecutionError(
            "sandbox failed before containment was established",
            returncode=process.poll(),
            stderr=stderr,
        )


def _verify_containment_empty(
    process: subprocess.Popen[bytes],
    pidfd: int,
) -> None:
    if process.poll() is None:
        raise CapsuleExecutionError("sandbox supervisor is still running")
    poller = select.poll()
    poller.register(pidfd, select.POLLIN | select.POLLHUP | select.POLLERR)
    if not poller.poll(0):
        raise CapsuleExecutionError("sandbox PID namespace did not terminate")


def execute_capsule(
    capability: VerifiedAuthorityPolicy,
    bundle: ArtifactBundle,
    *,
    program_artifact_id: str,
    request: Any,
    timeout: float = 30.0,
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
    state = _verified_authority_state(capability)
    bundle_data = verify_artifact_bundle(capability, bundle)
    bundle_bytes = bundle.payload
    program = _program_from_bundle(bundle_data, program_artifact_id)
    request_bytes = _request_bytes(request)

    nonce = os.urandom(32).hex()
    descriptors: list[int] = []
    process: subprocess.Popen[bytes] | None = None
    pidfd = -1
    ready_read_fd = -1
    ready_write_fd = -1
    try:
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
        launcher_fd, launcher_digest = _compile_sandbox_launcher()
        descriptors.append(launcher_fd)
        for fd in descriptors:
            if fcntl.fcntl(fd, fcntl.F_GET_SEALS) & REQUIRED_SEALS != REQUIRED_SEALS:
                raise CapsuleError("descriptor seal verification failed before launch")
        if _before_spawn is not None:
            _before_spawn()
        ready_read_fd, ready_write_fd = os.pipe2(os.O_CLOEXEC)
        environment = {
            "HOME": "/",
            "PATH": "/usr/bin:/bin",
        }
        bootstrap_arguments = _bootstrap_arguments(
            program_fd=program_fd,
            program_digest=program_envelope_digest,
            request_fd=request_fd,
            request_digest=request_envelope_digest,
            bundle_fd=bundle_fd,
            bundle_digest=bundle_envelope_digest,
            nonce=nonce,
            program_artifact_id=program_artifact_id,
        )
        command = _sandbox_command(
            launcher_fd=launcher_fd,
            program_fd=program_fd,
            request_fd=request_fd,
            bundle_fd=bundle_fd,
            ready_fd=ready_write_fd,
            bootstrap_arguments=bootstrap_arguments,
        )
        process = subprocess.Popen(
            command,
            cwd="/",
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            close_fds=True,
            pass_fds=(*descriptors, ready_write_fd),
            start_new_session=True,
        )
        os.close(ready_write_fd)
        ready_write_fd = -1
        try:
            pidfd = os.pidfd_open(process.pid)
        except (AttributeError, OSError) as error:
            _kill_process_group(process)
            raise CapsuleError("cannot create sandbox supervisor pidfd") from error
        _wait_sandbox_ready(process, ready_read_fd)
        os.close(ready_read_fd)
        ready_read_fd = -1
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
        _verify_containment_empty(process, pidfd)
        if stderr:
            raise CapsuleExecutionError(
                "successful capsule emitted stderr",
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
            "authority_map_sha256": _sha256(
                normalized_json(
                    [_policy_json(policy) for policy in state.authorities]
                )
            ),
            "authority_contract_sha256": state.contract_sha256,
            "request_sha256": _sha256(request_bytes),
            "output_sha256": _sha256(stdout),
            "sandbox_launcher_sha256": launcher_digest,
            "sandbox_launcher_source_sha256": _sha256(
                _SANDBOX_LAUNCHER_SOURCE.encode("ascii")
            ),
            "sandbox_compiler_argv_sha256": _sha256(
                normalized_json([CC, *SANDBOX_COMPILER_FLAGS])
            ),
            "sandbox_profile": "linux-x86_64-native-user-pid-net-landlock-seccomp-v2",
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
        if pidfd >= 0:
            os.close(pidfd)
        for fd in (ready_read_fd, ready_write_fd):
            if fd >= 0:
                try:
                    os.close(fd)
                except OSError:
                    pass
        for fd in reversed(descriptors):
            try:
                os.close(fd)
            except OSError:
                pass
