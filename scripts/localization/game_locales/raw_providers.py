"""Materialized locale payloads for verified raw-symbol providers."""

from __future__ import annotations

import ast
import hashlib
import json
import re
import struct
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Mapping

JA_RAW_PROVIDER_KIND = "fe8j-raw-provider-catalog"
JA_RAW_PROVIDER_SCHEMA_VERSION = 5
PINNED_SOURCE_REPOSITORY = "https://github.com/laqieer/fireemblem8j"
PINNED_SOURCE_REVISION = "bf424414d075789d757e2f4cd0cea823bfb2862e"
PINNED_GOAL_SOURCE_ID = "expansion-fe8j-raw-v4"
PINNED_GOAL_SOURCE_REPOSITORY = (
    "https://github.com/laqieer/fireemblem8-expansion"
)
PINNED_GOAL_SOURCE_REVISION = "548b240d0a553add88897927049b7f5ce25657a8"
PINNED_GOAL_TARGETS = frozenset({"0x01C1", "0x01C2", "0x01C3"})
_TARGET_ID_RE = re.compile(r"0x[0-9A-F]{4}")
_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_GIT_OID_RE = re.compile(r"[0-9a-f]{40}")


class RawProviderError(ValueError):
    """Raised when a raw-symbol provider catalog is invalid or incomplete."""


@dataclass(frozen=True)
class RawProvider:
    symbol: str
    text: str
    source_repository: str
    source_revision: str
    source_path: str
    source_blob_oid: str
    source_anchor: str
    source_artifact_path: str
    source_artifact_sha256: str
    source_value_index: int
    value_offset: int
    value_length: int
    value_sha256: str


@dataclass(frozen=True)
class GitSourceBlob:
    path: str
    oid: str
    vendored_path: str
    sha256: str
    raw: bytes


@dataclass(frozen=True)
class GitSourceTree:
    path: str
    oid: str
    vendored_path: str
    sha256: str
    raw: bytes


@dataclass(frozen=True)
class GitSource:
    repository: str
    revision: str
    blobs: Mapping[str, GitSourceBlob]
    trees: Mapping[str, GitSourceTree]
    generated_from_paths: tuple[str, ...]
    artifact_path: str
    artifact_sha256: str
    artifact_raw: bytes


_C_STRING_RE = re.compile(r'"(?:\\.|[^"\\])*"', re.DOTALL)


def _strip_c_comments(source: str) -> str:
    output = []
    index = 0
    state = "code"
    while index < len(source):
        char = source[index]
        following = source[index + 1] if index + 1 < len(source) else ""
        if state == "code":
            if char == '"':
                state = "string"
                output.append(char)
                index += 1
                continue
            if char == "'":
                state = "character"
                output.append(char)
                index += 1
                continue
            if char == "/" and following == "*":
                state = "block_comment"
                output.extend((" ", " "))
                index += 2
                continue
            if char == "/" and following == "/":
                state = "line_comment"
                output.extend((" ", " "))
                index += 2
                continue
            output.append(char)
            index += 1
            continue
        if state in ("string", "character"):
            output.append(char)
            if char == "\\" and index + 1 < len(source):
                output.append(source[index + 1])
                index += 2
                continue
            if (state == "string" and char == '"') or (
                state == "character" and char == "'"
            ):
                state = "code"
            index += 1
            continue
        if state == "block_comment":
            if char == "*" and following == "/":
                output.extend((" ", " "))
                index += 2
                state = "code"
                continue
            output.append("\n" if char == "\n" else " ")
            index += 1
            continue
        output.append("\n" if char == "\n" else " ")
        index += 1
        if char == "\n":
            state = "code"
    return "".join(output)


def _strip_asm_line_comment(line: str) -> str:
    in_string = False
    escaped = False
    for index, char in enumerate(line):
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
            continue
        if char == "@" or line.startswith("//", index):
            return line[:index]
    return line


def _decode_c_string(token: str, *, source_path: str) -> bytes:
    try:
        value = ast.literal_eval(token)
    except (SyntaxError, ValueError) as error:
        raise RawProviderError(
            f"ja raw provider source string is malformed in {source_path}"
        ) from error
    if not isinstance(value, str):
        raise RawProviderError(
            f"ja raw provider source string is not text in {source_path}"
        )
    try:
        return value.encode("cp932")
    except UnicodeEncodeError as error:
        raise RawProviderError(
            f"ja raw provider source string is not CP932 in {source_path}"
        ) from error


def _nul_terminated_values(raw: bytes) -> tuple[bytes, ...]:
    return tuple(value + b"\0" for value in raw.split(b"\0") if value)


def _extract_c_anchor_bytes(
    source: str,
    *,
    source_path: str,
    source_anchor: str,
) -> bytes | None:
    source = _strip_c_comments(source)
    anchor = re.escape(source_anchor)
    initializer = re.search(
        rf"(?:^|[;{{}}])\s*"
        rf"(?P<declaration>[^;{{}}]*\b{anchor}\b[^;{{}}]*?)"
        rf"=\s*(?P<body>.*?);",
        source,
        flags=re.DOTALL | re.MULTILINE,
    )
    if initializer is not None:
        declaration = initializer.group("declaration")
        if re.search(r"\bextern\b", declaration):
            return None
        declaration_without_strings = _C_STRING_RE.sub('""', declaration)
        anchor_match = re.search(rf"\b{anchor}\b", declaration_without_strings)
        if anchor_match is None:
            return None
        declaration_prefix = declaration_without_strings[: anchor_match.start()]
        if not re.search(r"\b[A-Za-z_][A-Za-z0-9_]*\b", declaration_prefix):
            return None
        if re.search(r"(?:\.|->)\s*$", declaration_prefix):
            return None
        body = initializer.group("body")
        string_tokens = _C_STRING_RE.findall(body)
        if string_tokens:
            return b"".join(
                _decode_c_string(token, source_path=source_path)
                for token in string_tokens
            )
        numeric_tokens = re.findall(r"0x[0-9A-Fa-f]+|\b\d+\b", body)
        if numeric_tokens:
            values = [int(token, 0) for token in numeric_tokens]
            if any(value < 0 or value > 0xFFFFFFFF for value in values):
                raise RawProviderError(
                    f"ja raw provider source integer is out of range in "
                    f"{source_path}"
                )
            return b"".join(struct.pack("<I", value) for value in values)
    return None


def _extract_asm_anchor_bytes(
    source: str,
    *,
    source_path: str,
    source_anchor: str,
) -> bytes | None:
    source = "\n".join(
        _strip_asm_line_comment(line)
        for line in _strip_c_comments(source).splitlines()
    )
    label = re.search(
        rf"(?m)^[ \t]*{re.escape(source_anchor)}:[ \t]*$",
        source,
    )
    if label is None:
        return None
    next_label = re.search(
        r"(?m)^[ \t]*[A-Za-z_.][A-Za-z0-9_.$]*:[ \t]*$",
        source[label.end() :],
    )
    end = (
        len(source)
        if next_label is None
        else label.end() + next_label.start()
    )
    body = source[label.end() : end]
    output = bytearray()
    for line in body.splitlines():
        byte_directive = re.match(r"^[ \t]*\.byte\s+(.+?)\s*$", line)
        if byte_directive is not None:
            for token in byte_directive.group(1).split(","):
                token = token.strip()
                if not token:
                    continue
                try:
                    value = int(token, 0)
                except ValueError as error:
                    raise RawProviderError(
                        f"ja raw provider .byte value is malformed in "
                        f"{source_path}"
                    ) from error
                if value < 0 or value > 0xFF:
                    raise RawProviderError(
                        f"ja raw provider .byte value is out of range in "
                        f"{source_path}"
                    )
                output.append(value)
        asciz = re.match(
            r"^[ \t]*\.asciz\s+(" + _C_STRING_RE.pattern + r")\s*$",
            line,
        )
        if asciz is not None:
            output.extend(
                _decode_c_string(asciz.group(1), source_path=source_path)
            )
            output.append(0)
    return bytes(output) if output else None


def _extract_source_anchor_values(
    source_blob: GitSourceBlob,
    *,
    source_anchor: str,
) -> tuple[bytes, ...]:
    try:
        source = source_blob.raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise RawProviderError(
            f"ja raw provider source blob is not UTF-8: {source_blob.path}"
        ) from error
    extracted = _extract_c_anchor_bytes(
        source,
        source_path=source_blob.path,
        source_anchor=source_anchor,
    )
    if extracted is None:
        extracted = _extract_asm_anchor_bytes(
            source,
            source_path=source_blob.path,
            source_anchor=source_anchor,
        )
    if extracted is None:
        raise RawProviderError(
            f"ja raw provider source_anchor {source_anchor!r} cannot be "
            f"materialized from {source_blob.path}"
        )
    values = _nul_terminated_values(extracted)
    if not values:
        raise RawProviderError(
            f"ja raw provider source_anchor {source_anchor!r} has no "
            f"extractable values in {source_blob.path}"
        )
    return values


def _git_object_oid(kind: str, raw: bytes) -> str:
    header = f"{kind} {len(raw)}\0".encode("ascii")
    return hashlib.sha1(header + raw).hexdigest()


def _commit_tree_oid(raw: bytes) -> str:
    try:
        first_line = raw.splitlines()[0].decode("ascii")
    except (IndexError, UnicodeDecodeError) as error:
        raise RawProviderError(
            "ja raw provider vendored commit object is malformed"
        ) from error
    match = re.fullmatch(r"tree ([0-9a-f]{40})", first_line)
    if match is None:
        raise RawProviderError(
            "ja raw provider vendored commit object has no exact root tree"
        )
    return match.group(1)


def _tree_entries(raw: bytes, *, tree_path: str) -> Mapping[str, tuple[str, str]]:
    entries: Dict[str, tuple[str, str]] = {}
    offset = 0
    while offset < len(raw):
        try:
            space = raw.index(b" ", offset)
            nul = raw.index(b"\0", space + 1)
        except ValueError as error:
            raise RawProviderError(
                f"ja raw provider vendored tree object is malformed: {tree_path}"
            ) from error
        mode = raw[offset:space].decode("ascii", errors="strict")
        name = raw[space + 1 : nul].decode("utf-8", errors="strict")
        oid_start = nul + 1
        oid_end = oid_start + 20
        if oid_end > len(raw) or not name or "/" in name or name in entries:
            raise RawProviderError(
                f"ja raw provider vendored tree object is malformed: {tree_path}"
            )
        entries[name] = (mode, raw[oid_start:oid_end].hex())
        offset = oid_end
    return entries


def _tree_child(
    trees: Mapping[str, GitSourceTree],
    *,
    parent_path: str,
    name: str,
) -> tuple[str, str]:
    parent = trees.get(parent_path)
    if parent is None:
        raise RawProviderError(
            f"ja raw provider source tree metadata is missing: {parent_path or '/'}"
        )
    entries = _tree_entries(parent.raw, tree_path=parent_path or "/")
    if name not in entries:
        raise RawProviderError(
            f"ja raw provider pinned tree has no path entry "
            f"{parent_path + '/' if parent_path else ''}{name}"
        )
    return entries[name]


def _require_git_oid(value: Any, field: str) -> str:
    if (
        not isinstance(value, str)
        or not _GIT_OID_RE.fullmatch(value)
        or value == "0" * 40
    ):
        raise RawProviderError(f"{field} must be a nonzero full Git OID")
    return value


def _require_relative_path(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise RawProviderError(f"{field} must be a non-empty relative path")
    path = Path(value)
    if path.is_absolute() or ".." in path.parts or ":" in value:
        raise RawProviderError(f"{field} must be a safe relative path")
    return value


def _load_source_snapshot(
    data: Mapping[str, Any],
    *,
    source_root: Path,
) -> tuple[Mapping[str, Any], Path]:
    specification = data.get("source_snapshot")
    if not isinstance(specification, dict) or set(specification) != {
        "path",
        "sha256",
    }:
        raise RawProviderError(
            "ja raw provider source_snapshot must contain path and sha256"
        )
    relative_path = specification["path"]
    expected_sha256 = specification["sha256"]
    if not isinstance(relative_path, str) or not relative_path:
        raise RawProviderError("ja raw provider source_snapshot.path is invalid")
    if (
        not isinstance(expected_sha256, str)
        or not _SHA256_RE.fullmatch(expected_sha256)
    ):
        raise RawProviderError(
            "ja raw provider source_snapshot.sha256 must be a lowercase SHA-256"
        )
    path = Path(source_root) / relative_path
    try:
        raw = path.read_bytes()
        snapshot = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RawProviderError(
            f"ja raw provider source snapshot is unavailable: {path}"
        ) from error
    actual_sha256 = hashlib.sha256(raw).hexdigest()
    if actual_sha256 != expected_sha256:
        raise RawProviderError(
            "ja raw provider source snapshot SHA-256 mismatch"
        )
    if not isinstance(snapshot, dict):
        raise RawProviderError("ja raw provider source snapshot must be an object")
    if snapshot.get("schema_version") != JA_RAW_PROVIDER_SCHEMA_VERSION:
        raise RawProviderError(
            "ja raw provider source snapshot schema_version must be "
            f"{JA_RAW_PROVIDER_SCHEMA_VERSION}"
        )
    if snapshot.get("kind") != "fe8j-raw-symbol-source-snapshot":
        raise RawProviderError("ja raw provider source snapshot kind is invalid")
    return snapshot, path


def _load_git_source(
    snapshot: Mapping[str, Any],
    *,
    snapshot_path: Path,
    expected_repository: str | None,
    expected_revision: str | None,
) -> GitSource:
    repository = snapshot.get("source_repository")
    revision = snapshot.get("source_revision")
    source_url = snapshot.get("source_url")
    if (
        not isinstance(repository, str)
        or not repository.startswith("https://github.com/")
    ):
        raise RawProviderError(
            "ja raw provider source snapshot repository must be a GitHub URL"
        )
    revision = _require_git_oid(
        revision,
        "ja raw provider source snapshot revision",
    )
    if (expected_repository is None) != (expected_revision is None):
        raise RawProviderError(
            "ja raw provider expected repository and revision must be "
            "specified together"
        )
    if expected_repository is not None and repository != expected_repository:
        raise RawProviderError(
            "ja raw provider source snapshot repository differs from the "
            "independently pinned FE8J repository"
        )
    if expected_revision is not None and revision != expected_revision:
        raise RawProviderError(
            "ja raw provider source snapshot revision differs from the "
            "independently pinned FE8J commit"
        )
    expected_source_url = (
        f"{repository.removesuffix('.git').rstrip('/')}/tree/{revision}"
    )
    if source_url != expected_source_url:
        raise RawProviderError(
            "ja raw provider source snapshot URL must exactly pin the full revision"
        )

    commit_specification = snapshot.get("source_commit")
    if not isinstance(commit_specification, dict) or set(commit_specification) != {
        "path",
        "sha256",
    }:
        raise RawProviderError(
            "ja raw provider source_commit must contain path and sha256"
        )
    commit_path = _require_relative_path(
        commit_specification["path"],
        "ja raw provider source_commit.path",
    )
    commit_sha256 = commit_specification["sha256"]
    if not isinstance(commit_sha256, str) or not _SHA256_RE.fullmatch(
        commit_sha256
    ):
        raise RawProviderError(
            "ja raw provider source_commit.sha256 must be a lowercase SHA-256"
        )
    try:
        commit_raw = (snapshot_path.parent / commit_path).read_bytes()
    except OSError as error:
        raise RawProviderError(
            "ja raw provider vendored commit object is unavailable"
        ) from error
    if hashlib.sha256(commit_raw).hexdigest() != commit_sha256:
        raise RawProviderError(
            "ja raw provider vendored commit object SHA-256 mismatch"
        )
    if _git_object_oid("commit", commit_raw) != revision:
        raise RawProviderError(
            "ja raw provider vendored commit object does not match revision"
        )
    commit_tree_oid = _commit_tree_oid(commit_raw)

    raw_source_trees = snapshot.get("source_trees")
    if not isinstance(raw_source_trees, list) or not raw_source_trees:
        raise RawProviderError(
            "ja raw provider source_trees must be a non-empty array"
        )
    source_trees: Dict[str, GitSourceTree] = {}
    for index, raw_tree in enumerate(raw_source_trees):
        field = f"ja raw provider source_trees[{index}]"
        if not isinstance(raw_tree, dict) or set(raw_tree) != {
            "oid",
            "path",
            "sha256",
            "vendored_path",
        }:
            raise RawProviderError(
                f"{field} must contain oid, path, sha256, and vendored_path"
            )
        tree_path = raw_tree["path"]
        if not isinstance(tree_path, str) or (
            tree_path and _require_relative_path(tree_path, f"{field}.path") != tree_path
        ):
            raise RawProviderError(f"{field}.path must be a safe tree path")
        vendored_path = _require_relative_path(
            raw_tree["vendored_path"],
            f"{field}.vendored_path",
        )
        oid = _require_git_oid(raw_tree["oid"], f"{field}.oid")
        sha256 = raw_tree["sha256"]
        if not isinstance(sha256, str) or not _SHA256_RE.fullmatch(sha256):
            raise RawProviderError(
                f"{field}.sha256 must be a lowercase SHA-256"
            )
        if tree_path in source_trees:
            raise RawProviderError(
                f"duplicate ja raw provider source tree path {tree_path or '/'}"
            )
        try:
            raw = (snapshot_path.parent / vendored_path).read_bytes()
        except OSError as error:
            raise RawProviderError(
                f"ja raw provider vendored source tree is unavailable: "
                f"{vendored_path}"
            ) from error
        if hashlib.sha256(raw).hexdigest() != sha256:
            raise RawProviderError(
                f"ja raw provider vendored source tree SHA-256 mismatch: "
                f"{tree_path or '/'}"
            )
        if _git_object_oid("tree", raw) != oid:
            raise RawProviderError(
                f"ja raw provider vendored source tree Git OID mismatch: "
                f"{tree_path or '/'}"
            )
        source_trees[tree_path] = GitSourceTree(
            path=tree_path,
            oid=oid,
            vendored_path=vendored_path,
            sha256=sha256,
            raw=raw,
        )
    root_tree = source_trees.get("")
    if root_tree is None or root_tree.oid != commit_tree_oid:
        raise RawProviderError(
            "ja raw provider vendored commit root tree does not match "
            "source_trees"
        )

    raw_source_blobs = snapshot.get("source_blobs")
    if not isinstance(raw_source_blobs, list) or not raw_source_blobs:
        raise RawProviderError(
            "ja raw provider source_blobs must be a non-empty array"
        )
    source_blobs: Dict[str, GitSourceBlob] = {}
    for index, raw_blob in enumerate(raw_source_blobs):
        field = f"ja raw provider source_blobs[{index}]"
        if not isinstance(raw_blob, dict) or set(raw_blob) != {
            "oid",
            "path",
            "sha256",
            "vendored_path",
        }:
            raise RawProviderError(
                f"{field} must contain oid, path, sha256, and vendored_path"
            )
        source_path = _require_relative_path(raw_blob["path"], f"{field}.path")
        vendored_path = _require_relative_path(
            raw_blob["vendored_path"],
            f"{field}.vendored_path",
        )
        oid = _require_git_oid(raw_blob["oid"], f"{field}.oid")
        sha256 = raw_blob["sha256"]
        if not isinstance(sha256, str) or not _SHA256_RE.fullmatch(sha256):
            raise RawProviderError(
                f"{field}.sha256 must be a lowercase SHA-256"
            )
        if source_path in source_blobs:
            raise RawProviderError(
                f"duplicate ja raw provider source blob path {source_path}"
            )
        try:
            raw = (snapshot_path.parent / vendored_path).read_bytes()
        except OSError as error:
            raise RawProviderError(
                f"ja raw provider vendored source blob is unavailable: "
                f"{vendored_path}"
            ) from error
        if hashlib.sha256(raw).hexdigest() != sha256:
            raise RawProviderError(
                f"ja raw provider vendored source blob SHA-256 mismatch: "
                f"{source_path}"
            )
        if _git_object_oid("blob", raw) != oid:
            raise RawProviderError(
                f"ja raw provider vendored source blob Git OID mismatch: "
                f"{source_path}"
            )
        source_blobs[source_path] = GitSourceBlob(
            path=source_path,
            oid=oid,
            vendored_path=vendored_path,
            sha256=sha256,
            raw=raw,
        )

    required_tree_paths = {""}
    for source_path, source_blob in source_blobs.items():
        parts = Path(source_path).parts
        parent_path = ""
        for part in parts[:-1]:
            mode, oid = _tree_child(
                source_trees,
                parent_path=parent_path,
                name=part,
            )
            if mode not in ("40000", "040000"):
                raise RawProviderError(
                    f"ja raw provider pinned path component is not a tree: "
                    f"{source_path}"
                )
            child_path = f"{parent_path}/{part}".lstrip("/")
            child_tree = source_trees.get(child_path)
            if child_tree is None or child_tree.oid != oid:
                raise RawProviderError(
                    f"ja raw provider source tree OID mismatch for {child_path}"
                )
            required_tree_paths.add(child_path)
            parent_path = child_path
        mode, oid = _tree_child(
            source_trees,
            parent_path=parent_path,
            name=parts[-1],
        )
        if mode in ("40000", "040000") or oid != source_blob.oid:
            raise RawProviderError(
                f"ja raw provider pinned commit path/blob mismatch for "
                f"{source_path}"
            )
    if set(source_trees) != required_tree_paths:
        raise RawProviderError(
            "ja raw provider source_trees must exactly cover pinned source paths"
        )

    artifact_specification = snapshot.get("provider_values_artifact")
    if not isinstance(artifact_specification, dict) or set(
        artifact_specification
    ) != {
        "encoding",
        "generated_from_paths",
        "path",
        "sha256",
    }:
        raise RawProviderError(
            "ja raw provider provider_values_artifact must contain encoding, "
            "generated_from_paths, path, and sha256"
        )
    if artifact_specification["encoding"] != "cp932-nul-terminated":
        raise RawProviderError(
            "ja raw provider provider_values_artifact encoding must be "
            "cp932-nul-terminated"
        )
    artifact_path = _require_relative_path(
        artifact_specification["path"],
        "ja raw provider provider_values_artifact.path",
    )
    expected_sha256 = artifact_specification["sha256"]
    if not isinstance(expected_sha256, str) or not _SHA256_RE.fullmatch(
        expected_sha256
    ):
        raise RawProviderError(
            "ja raw provider provider_values_artifact.sha256 must be a "
            "lowercase SHA-256"
        )
    generated_from_paths = artifact_specification["generated_from_paths"]
    if (
        not isinstance(generated_from_paths, list)
        or not generated_from_paths
        or any(
            not isinstance(path, str) or path not in source_blobs
            for path in generated_from_paths
        )
        or len(set(generated_from_paths)) != len(generated_from_paths)
    ):
        raise RawProviderError(
            "ja raw provider provider_values_artifact.generated_from_paths "
            "must uniquely reference pinned source blobs"
        )
    if set(generated_from_paths) != set(source_blobs):
        raise RawProviderError(
            "ja raw provider provider_values_artifact.generated_from_paths "
            "must cover every pinned source blob"
        )
    path = snapshot_path.parent / artifact_path
    try:
        raw = path.read_bytes()
    except OSError as error:
        raise RawProviderError(
            f"ja raw provider values artifact is unavailable: {path}"
        ) from error
    if hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise RawProviderError(
            "ja raw provider values artifact SHA-256 mismatch"
        )
    return GitSource(
        repository=repository,
        revision=revision,
        blobs=source_blobs,
        trees=source_trees,
        generated_from_paths=tuple(generated_from_paths),
        artifact_path=artifact_path,
        artifact_sha256=expected_sha256,
        artifact_raw=raw,
    )


def _load_additional_git_sources(
    snapshot: Mapping[str, Any],
    *,
    snapshot_path: Path,
) -> Dict[str, GitSource]:
    specifications = snapshot.get("additional_git_sources", {})
    if not isinstance(specifications, dict):
        raise RawProviderError(
            "ja raw provider additional_git_sources must be an object"
        )
    sources: Dict[str, GitSource] = {}
    for source_id, specification in specifications.items():
        if (
            not isinstance(source_id, str)
            or not re.fullmatch(r"[a-z0-9][a-z0-9-]*", source_id)
            or not isinstance(specification, dict)
        ):
            raise RawProviderError(
                "ja raw provider additional_git_sources entries are invalid"
            )
        if source_id == PINNED_GOAL_SOURCE_ID:
            expected_repository = PINNED_GOAL_SOURCE_REPOSITORY
            expected_revision = PINNED_GOAL_SOURCE_REVISION
        else:
            raise RawProviderError(
                f"ja raw provider additional source {source_id!r} is not "
                "independently pinned"
            )
        sources[source_id] = _load_git_source(
            specification,
            snapshot_path=snapshot_path,
            expected_repository=expected_repository,
            expected_revision=expected_revision,
        )
    return sources


def _extract_raw_provider_manifest_value(
    git_source: GitSource,
    source_blob: GitSourceBlob,
    *,
    source_anchor: str,
    expected_symbol: str,
) -> tuple[tuple[bytes, ...], str]:
    if not _TARGET_ID_RE.fullmatch(source_anchor):
        raise RawProviderError(
            "ja raw provider manifest source_anchor must be a canonical target ID"
        )
    try:
        manifest = json.loads(source_blob.raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RawProviderError(
            f"ja raw provider source manifest is malformed: {source_blob.path}"
        ) from error
    if (
        not isinstance(manifest, dict)
        or manifest.get("kind") != "fe8j-raw-symbol-source-snapshot"
        or not isinstance(manifest.get("providers"), dict)
    ):
        raise RawProviderError(
            f"ja raw provider source manifest is invalid: {source_blob.path}"
        )
    provider = manifest["providers"].get(source_anchor)
    if not isinstance(provider, dict):
        raise RawProviderError(
            f"ja raw provider source manifest has no slot {source_anchor}"
        )
    if provider.get("symbol") != expected_symbol:
        raise RawProviderError(
            f"ja raw provider source manifest {source_anchor} symbol mismatch"
        )
    offset = provider.get("offset")
    byte_length = provider.get("byte_length")
    value_sha256 = provider.get("value_sha256")
    if (
        not isinstance(offset, int)
        or isinstance(offset, bool)
        or offset < 0
        or not isinstance(byte_length, int)
        or isinstance(byte_length, bool)
        or byte_length < 2
        or not isinstance(value_sha256, str)
        or not _SHA256_RE.fullmatch(value_sha256)
    ):
        raise RawProviderError(
            f"ja raw provider source manifest {source_anchor} range is invalid"
        )
    artifact = manifest.get("provider_values_artifact")
    if (
        not isinstance(artifact, dict)
        or artifact.get("encoding") != "cp932-nul-terminated"
        or not isinstance(artifact.get("path"), str)
        or not isinstance(artifact.get("sha256"), str)
        or not _SHA256_RE.fullmatch(artifact["sha256"])
    ):
        raise RawProviderError(
            "ja raw provider source manifest artifact metadata is invalid"
        )
    artifact_relative_path = _require_relative_path(
        artifact["path"],
        "ja raw provider source manifest artifact path",
    )
    artifact_path = (
        Path(source_blob.path).parent / artifact_relative_path
    ).as_posix()
    artifact_blob = git_source.blobs.get(artifact_path)
    if artifact_blob is None:
        raise RawProviderError(
            "ja raw provider source manifest artifact blob is not pinned"
        )
    if hashlib.sha256(artifact_blob.raw).hexdigest() != artifact["sha256"]:
        raise RawProviderError(
            "ja raw provider source manifest artifact SHA-256 mismatch"
        )
    end = offset + byte_length
    if end > len(artifact_blob.raw):
        raise RawProviderError(
            f"ja raw provider source manifest {source_anchor} range is out of bounds"
        )
    raw_value = artifact_blob.raw[offset:end]
    if hashlib.sha256(raw_value).hexdigest() != value_sha256:
        raise RawProviderError(
            f"ja raw provider source manifest {source_anchor} value mismatch"
        )
    if not raw_value.endswith(b"\0") or b"\0" in raw_value[:-1]:
        raise RawProviderError(
            f"ja raw provider source manifest {source_anchor} is not one string"
        )
    return (raw_value,), artifact_path


def load_ja_raw_providers(
    data: Any,
    *,
    source_root: Path = Path("."),
    expected_repository: str | None = PINNED_SOURCE_REPOSITORY,
    expected_revision: str | None = PINNED_SOURCE_REVISION,
) -> Dict[int, RawProvider]:
    if not isinstance(data, dict):
        raise RawProviderError("ja raw provider catalog root must be an object")
    if data.get("schema_version") != JA_RAW_PROVIDER_SCHEMA_VERSION:
        raise RawProviderError(
            f"ja raw provider schema_version must be {JA_RAW_PROVIDER_SCHEMA_VERSION}"
        )
    if data.get("kind") != JA_RAW_PROVIDER_KIND:
        raise RawProviderError(
            f"ja raw provider kind must be {JA_RAW_PROVIDER_KIND!r}"
        )
    if data.get("locale_id") != "ja":
        raise RawProviderError("ja raw provider locale_id must be 'ja'")
    if data.get("source_layout") != "FE8J-raw-symbol":
        raise RawProviderError(
            "ja raw provider source_layout must be 'FE8J-raw-symbol'"
        )
    source_revision = data.get("source_revision")
    source_revision = _require_git_oid(
        source_revision,
        "ja raw provider source_revision",
    )
    snapshot, snapshot_path = _load_source_snapshot(
        data,
        source_root=source_root,
    )
    if snapshot.get("source_revision") != source_revision:
        raise RawProviderError(
            "ja raw provider source snapshot revision does not match catalog"
        )
    git_source = _load_git_source(
        snapshot,
        snapshot_path=snapshot_path,
        expected_repository=expected_repository,
        expected_revision=expected_revision,
    )
    additional_git_sources = _load_additional_git_sources(
        snapshot,
        snapshot_path=snapshot_path,
    )
    if git_source.revision != source_revision:
        raise RawProviderError(
            "ja raw provider Git source revision does not match catalog"
        )
    source_blob = git_source.artifact_raw

    raw_providers = data.get("providers")
    if not isinstance(raw_providers, dict):
        raise RawProviderError("ja raw provider providers must be an object")
    if data.get("provider_count") != len(raw_providers):
        raise RawProviderError("ja raw provider provider_count does not match providers")
    if snapshot.get("provider_count") != len(raw_providers):
        raise RawProviderError(
            "ja raw provider source snapshot provider_count does not match providers"
        )
    snapshot_providers = snapshot.get("providers")
    if not isinstance(snapshot_providers, dict):
        raise RawProviderError(
            "ja raw provider source snapshot providers must be an object"
        )
    if set(snapshot_providers) != set(raw_providers):
        raise RawProviderError(
            "ja raw provider source snapshot targets do not match providers"
        )

    providers: Dict[int, RawProvider] = {}
    source_ranges = []
    used_source_paths = set()
    used_additional_source_paths: Dict[str, set[str]] = {
        source_id: set() for source_id in additional_git_sources
    }
    for target, raw_provider in raw_providers.items():
        if not isinstance(target, str) or not _TARGET_ID_RE.fullmatch(target):
            raise RawProviderError(
                f"ja raw provider target {target!r} must use canonical 0xNNNN form"
            )
        if not isinstance(raw_provider, dict):
            raise RawProviderError(f"ja raw provider {target} must be an object")
        symbol = raw_provider.get("symbol")
        text = raw_provider.get("text")
        if not isinstance(symbol, str) or not symbol:
            raise RawProviderError(f"ja raw provider {target}.symbol must be non-empty")
        if not isinstance(text, str) or not text:
            raise RawProviderError(f"ja raw provider {target}.text must be non-empty")
        snapshot_provider = snapshot_providers[target]
        base_provider_fields = {
            "byte_length",
            "offset",
            "source_anchor",
            "source_path",
            "source_value_index",
            "symbol",
            "value_sha256",
        }
        if not isinstance(snapshot_provider, dict) or set(snapshot_provider) not in (
            base_provider_fields,
            base_provider_fields | {"source_format", "source_id"},
        ):
            raise RawProviderError(
                f"ja raw provider source snapshot {target} must contain "
                "byte_length, offset, source_anchor, source_path, "
                "source_value_index, symbol, and value_sha256, with optional "
                "source_format and source_id"
            )
        if snapshot_provider["symbol"] != symbol:
            raise RawProviderError(
                f"ja raw provider {target} source symbol mismatch"
            )
        offset = snapshot_provider["offset"]
        byte_length = snapshot_provider["byte_length"]
        value_sha256 = snapshot_provider["value_sha256"]
        source_path = snapshot_provider["source_path"]
        source_anchor = snapshot_provider["source_anchor"]
        source_value_index = snapshot_provider["source_value_index"]
        source_id = snapshot_provider.get("source_id")
        source_format = snapshot_provider.get("source_format")
        if (
            not isinstance(offset, int)
            or isinstance(offset, bool)
            or offset < 0
            or not isinstance(byte_length, int)
            or isinstance(byte_length, bool)
            or byte_length < 2
        ):
            raise RawProviderError(
                f"ja raw provider {target} source blob range is invalid"
            )
        if not isinstance(value_sha256, str) or not _SHA256_RE.fullmatch(
            value_sha256
        ):
            raise RawProviderError(
                f"ja raw provider {target} value_sha256 is invalid"
            )
        if source_id is None:
            provider_git_source = git_source
        elif (
            isinstance(source_id, str)
            and source_id in additional_git_sources
        ):
            provider_git_source = additional_git_sources[source_id]
        else:
            raise RawProviderError(
                f"ja raw provider {target} source_id is not pinned"
            )
        if (
            source_format is not None
            and source_format != "raw-provider-manifest"
        ):
            raise RawProviderError(
                f"ja raw provider {target} source_format is unsupported"
            )
        if target in PINNED_GOAL_TARGETS:
            if (
                source_id != PINNED_GOAL_SOURCE_ID
                or source_format != "raw-provider-manifest"
            ):
                raise RawProviderError(
                    f"ja raw provider {target} must use the pinned goal manifest"
                )
        elif source_id is not None or source_format is not None:
            raise RawProviderError(
                f"ja raw provider {target} cannot use goal-only source metadata"
            )
        if (
            not isinstance(source_path, str)
            or source_path not in provider_git_source.blobs
        ):
            raise RawProviderError(
                f"ja raw provider {target} source_path is not pinned"
            )
        if not isinstance(source_anchor, str) or not source_anchor:
            raise RawProviderError(
                f"ja raw provider {target} source_anchor must be non-empty"
            )
        if (
            not isinstance(source_value_index, int)
            or isinstance(source_value_index, bool)
            or source_value_index < 0
        ):
            raise RawProviderError(
                f"ja raw provider {target} source_value_index must be "
                "a non-negative integer"
            )
        source_git_blob = provider_git_source.blobs[source_path]
        if source_id is None:
            used_source_paths.add(source_path)
        else:
            used_additional_source_paths[source_id].add(source_path)
        if source_anchor.encode("utf-8") not in source_git_blob.raw:
            raise RawProviderError(
                f"ja raw provider {target} source_anchor is absent from "
                f"{source_path}"
            )
        end = offset + byte_length
        if end > len(source_blob):
            raise RawProviderError(
                f"ja raw provider {target} source blob range is out of bounds"
            )
        raw_value = source_blob[offset:end]
        if hashlib.sha256(raw_value).hexdigest() != value_sha256:
            raise RawProviderError(
                f"ja raw provider {target} source value SHA-256 mismatch"
            )
        if not raw_value.endswith(b"\0") or b"\0" in raw_value[:-1]:
            raise RawProviderError(
                f"ja raw provider {target} source value is not one CP932 string"
            )
        try:
            source_text = raw_value[:-1].decode("cp932")
        except UnicodeDecodeError as error:
            raise RawProviderError(
                f"ja raw provider {target} source value is not valid CP932"
            ) from error
        if source_text != text:
            raise RawProviderError(
                f"ja raw provider {target} source value does not match catalog text"
            )
        provider_artifact_path = git_source.artifact_path
        provider_artifact_sha256 = git_source.artifact_sha256
        if source_format == "raw-provider-manifest":
            source_values, artifact_source_path = (
                _extract_raw_provider_manifest_value(
                    provider_git_source,
                    source_git_blob,
                    source_anchor=source_anchor,
                    expected_symbol=symbol,
                )
            )
            used_additional_source_paths[source_id].add(artifact_source_path)
            provider_artifact_path = artifact_source_path
            provider_artifact_sha256 = provider_git_source.blobs[
                artifact_source_path
            ].sha256
        else:
            source_values = _extract_source_anchor_values(
                source_git_blob,
                source_anchor=source_anchor,
            )
        if source_value_index >= len(source_values):
            raise RawProviderError(
                f"ja raw provider {target} source_value_index is out of range "
                f"for {source_path}:{source_anchor}"
            )
        if raw_value != source_values[source_value_index]:
            raise RawProviderError(
                f"ja raw provider {target} artifact value differs from exact "
                f"{source_path}:{source_anchor}[{source_value_index}]"
            )
        target_id = int(target, 16)
        if target_id in providers:
            raise RawProviderError(f"duplicate ja raw provider target {target}")
        providers[target_id] = RawProvider(
            symbol=symbol,
            text=text,
            source_repository=provider_git_source.repository,
            source_revision=provider_git_source.revision,
            source_path=source_path,
            source_blob_oid=source_git_blob.oid,
            source_anchor=source_anchor,
            source_artifact_path=provider_artifact_path,
            source_artifact_sha256=provider_artifact_sha256,
            source_value_index=source_value_index,
            value_offset=offset,
            value_length=byte_length,
            value_sha256=value_sha256,
        )
        source_ranges.append((offset, end, target))
    expected_offset = 0
    for offset, end, target in sorted(source_ranges):
        if offset != expected_offset:
            raise RawProviderError(
                f"ja raw provider {target} source blob ranges overlap or leave gaps"
            )
        expected_offset = end
    if expected_offset != len(source_blob):
        raise RawProviderError(
            "ja raw provider source blob has unreferenced trailing bytes"
        )
    if used_source_paths != set(git_source.generated_from_paths):
        raise RawProviderError(
            "ja raw provider entries do not use every generated source path"
        )
    for source_id, source in additional_git_sources.items():
        if used_additional_source_paths[source_id] != set(source.blobs):
            raise RawProviderError(
                f"ja raw provider entries do not use every {source_id} source path"
            )
    return providers


def _git_output(repository: Path, *args: str) -> bytes:
    command = ["git", "-C", str(repository), *args]
    try:
        result = subprocess.run(
            command,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except OSError as error:
        raise RawProviderError("git is unavailable for source verification") from error
    if result.returncode != 0:
        diagnostic = result.stderr.decode("utf-8", errors="replace").strip()
        raise RawProviderError(
            f"git source verification failed for {' '.join(args)}: {diagnostic}"
        )
    return result.stdout


def verify_ja_raw_provider_git_source(
    data: Any,
    *,
    source_root: Path,
    repository: Path,
) -> None:
    if not isinstance(data, dict):
        raise RawProviderError("ja raw provider catalog root must be an object")
    snapshot, snapshot_path = _load_source_snapshot(
        data,
        source_root=source_root,
    )
    git_source = _load_git_source(
        snapshot,
        snapshot_path=snapshot_path,
        expected_repository=None,
        expected_revision=None,
    )
    catalog_revision = _require_git_oid(
        data.get("source_revision"),
        "ja raw provider source_revision",
    )
    if catalog_revision != git_source.revision:
        raise RawProviderError(
            "ja raw provider Git source revision does not match catalog"
        )

    revision = git_source.revision
    _git_output(Path(repository), "cat-file", "-e", f"{revision}^{{commit}}")
    actual_commit = _git_output(
        Path(repository),
        "cat-file",
        "commit",
        revision,
    )
    if _git_object_oid("commit", actual_commit) != revision:
        raise RawProviderError(
            "git source repository returned a mismatched commit object"
        )

    for source_path, source_blob in sorted(git_source.blobs.items()):
        actual_oid = _git_output(
            Path(repository),
            "rev-parse",
            f"{revision}:{source_path}",
        ).decode("ascii", errors="strict").strip()
        if actual_oid != source_blob.oid:
            raise RawProviderError(
                f"git source blob OID mismatch for {source_path}: "
                f"{actual_oid} != {source_blob.oid}"
            )
        actual_blob = _git_output(
            Path(repository),
            "cat-file",
            "blob",
            source_blob.oid,
        )
        if actual_blob != source_blob.raw:
            raise RawProviderError(
                f"git source blob bytes mismatch for {source_path}"
            )


def resolve_ja_raw_provider(
    *,
    target_id: int,
    ja_source: Mapping[str, Any],
    providers: Mapping[int, RawProvider],
) -> RawProvider:
    if ja_source.get("kind") != "symbol":
        raise RawProviderError(
            f"0x{target_id:04X} Japanese raw provider is not a symbol"
        )
    symbol = ja_source.get("symbol")
    provider_target = ja_source.get("provider_target_id")
    if provider_target is None:
        provider_target_id = target_id
    elif (
        isinstance(provider_target, str)
        and re.fullmatch(r"0x[0-9A-F]{4}", provider_target)
    ):
        provider_target_id = int(provider_target, 16)
    else:
        raise RawProviderError(
            f"0x{target_id:04X} Japanese raw provider_target_id is invalid"
        )
    provider = providers.get(provider_target_id)
    if provider is None:
        raise RawProviderError(
            f"0x{target_id:04X} Japanese raw symbol provider is missing "
            f"at 0x{provider_target_id:04X}"
        )
    if provider.symbol != symbol:
        raise RawProviderError(
            f"0x{target_id:04X} Japanese raw symbol mismatch: "
            f"{provider.symbol!r} != {symbol!r}"
        )
    return provider


def resolve_ja_raw_text(
    *,
    target_id: int,
    ja_source: Mapping[str, Any],
    providers: Mapping[int, RawProvider],
) -> str:
    kind = ja_source.get("kind")
    if kind == "literal":
        text = ja_source.get("text")
        if isinstance(text, str) and text:
            return text
        raise RawProviderError(
            f"0x{target_id:04X} Japanese literal provider is empty"
        )
    if kind != "symbol":
        raise RawProviderError(
            f"0x{target_id:04X} Japanese raw provider kind is unsupported: {kind!r}"
        )
    return resolve_ja_raw_provider(
        target_id=target_id,
        ja_source=ja_source,
        providers=providers,
    ).text
