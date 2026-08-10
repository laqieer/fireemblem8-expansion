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
JA_RAW_PROVIDER_SCHEMA_VERSION = 6
PINNED_SOURCE_REPOSITORY = "https://github.com/laqieer/fireemblem8j"
PINNED_SOURCE_REVISION = "bf424414d075789d757e2f4cd0cea823bfb2862e"
PINNED_FE8J_ROM_SHA256 = (
    "44fd343625ab9e6b90f63a80758c15066d526e6873fae91474006314a5ead464"
)
PINNED_FE8J_ROM_SIZE = 0x1000000
PINNED_GOAL_SOURCE_FORMAT = "baserom-slice"
PINNED_GOAL_OFFSET_SOURCE_PATH = (
    "layout/baseline_syms.d/GoalDisplay_Init-134e6b42.tsv"
)
PINNED_GOAL_OFFSET_SOURCE_BLOB_OID = (
    "4325b593a941ce95e3821e3746564b2311fe8142"
)
PINNED_GOAL_TARGETS = frozenset({"0x01C1", "0x01C2", "0x01C3"})
PINNED_GOAL_SYMBOLS = {
    "0x01C1": "GoalString_UnitsLeft",
    "0x01C2": "GoalString_Turn",
    "0x01C3": "GoalString_LastTurn",
}
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
    provenance_kind: str = "pinned_git_source_artifact"
    rom_sha256: str | None = None
    rom_address: str | None = None
    rom_offset: int | None = None
    decoded_value: str | None = None


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


@dataclass(frozen=True)
class BaseromSlice:
    target: str
    symbol: str
    rom_address: str
    rom_offset: int
    artifact_offset: int
    length: int
    bytes_sha256: str
    decoded_value: str
    raw: bytes


@dataclass(frozen=True)
class BaseromSource:
    rom_sha256: str
    rom_size: int
    offset_source_path: str
    offset_source_blob_oid: str
    artifact_path: str
    artifact_sha256: str
    artifact_raw: bytes
    slices: Mapping[str, BaseromSlice]


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


def _baseline_symbols(
    raw: bytes,
    *,
    source_path: str,
) -> Mapping[str, tuple[int, str, str]]:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise RawProviderError(
            f"ja raw provider baseline symbol map is not UTF-8: {source_path}"
        ) from error
    symbols: Dict[str, tuple[int, str, str]] = {}
    for line_number, line in enumerate(text.splitlines(), start=1):
        fields = line.split("\t")
        if len(fields) != 4 or not fields[0] or not re.fullmatch(
            r"[0-9A-F]{8}", fields[1]
        ):
            raise RawProviderError(
                f"ja raw provider baseline symbol map is malformed at "
                f"{source_path}:{line_number}"
            )
        symbol, address, symbol_kind, owner = fields
        if symbol in symbols:
            raise RawProviderError(
                f"ja raw provider baseline symbol map duplicates {symbol}"
            )
        symbols[symbol] = (int(address, 16), symbol_kind, owner)
    return symbols


def _load_baserom_source(
    snapshot: Mapping[str, Any],
    *,
    snapshot_path: Path,
) -> BaseromSource | None:
    if "additional_git_sources" in snapshot:
        raise RawProviderError(
            "ja raw provider nested generated manifests are not accepted"
        )
    snapshot_providers = snapshot.get("providers")
    has_goal_targets = isinstance(snapshot_providers, dict) and bool(
        PINNED_GOAL_TARGETS.intersection(snapshot_providers)
    )
    if not has_goal_targets:
        if "baserom_source" in snapshot:
            raise RawProviderError(
                "ja raw provider baserom_source is only valid for goal targets"
            )
        return None
    specification = snapshot.get("baserom_source")
    if not isinstance(specification, dict) or set(specification) != {
        "artifact",
        "offset_source",
        "records",
        "rom",
    }:
        raise RawProviderError(
            "ja raw provider baserom_source must contain artifact, "
            "offset_source, records, and rom"
        )
    rom = specification["rom"]
    if not isinstance(rom, dict) or set(rom) != {"sha256", "size"}:
        raise RawProviderError(
            "ja raw provider baserom_source.rom must contain sha256 and size"
        )
    if rom["sha256"] != PINNED_FE8J_ROM_SHA256:
        raise RawProviderError(
            "ja raw provider baserom SHA-256 differs from the independently "
            "pinned FE8J ROM"
        )
    if rom["size"] != PINNED_FE8J_ROM_SIZE:
        raise RawProviderError(
            "ja raw provider baserom size differs from the pinned FE8J ROM"
        )
    offset_source = specification["offset_source"]
    if not isinstance(offset_source, dict) or set(offset_source) != {
        "blob_oid",
        "path",
        "repository",
        "revision",
        "sha256",
    }:
        raise RawProviderError(
            "ja raw provider baserom offset_source metadata is invalid"
        )
    if offset_source["repository"] != PINNED_SOURCE_REPOSITORY:
        raise RawProviderError(
            "ja raw provider baserom offset source repository is not pinned"
        )
    if offset_source["revision"] != PINNED_SOURCE_REVISION:
        raise RawProviderError(
            "ja raw provider baserom offset source revision is not pinned"
        )
    if offset_source["path"] != PINNED_GOAL_OFFSET_SOURCE_PATH:
        raise RawProviderError(
            "ja raw provider baserom offset source path is not pinned"
        )
    if offset_source["blob_oid"] != PINNED_GOAL_OFFSET_SOURCE_BLOB_OID:
        raise RawProviderError(
            "ja raw provider baserom offset source blob OID is not pinned"
        )
    artifact = specification["artifact"]
    if (
        not isinstance(artifact, dict)
        or set(artifact) != {"encoding", "path", "sha256"}
        or artifact["encoding"] != "cp932-nul-terminated"
    ):
        raise RawProviderError(
            "ja raw provider baserom artifact metadata is invalid"
        )
    artifact_path = _require_relative_path(
        artifact["path"],
        "ja raw provider baserom artifact path",
    )
    artifact_sha256 = artifact["sha256"]
    if not isinstance(artifact_sha256, str) or not _SHA256_RE.fullmatch(
        artifact_sha256
    ):
        raise RawProviderError(
            "ja raw provider baserom artifact SHA-256 is invalid"
        )
    try:
        artifact_raw = (snapshot_path.parent / artifact_path).read_bytes()
    except OSError as error:
        raise RawProviderError(
            "ja raw provider baserom artifact is unavailable"
        ) from error
    if hashlib.sha256(artifact_raw).hexdigest() != artifact_sha256:
        raise RawProviderError(
            "ja raw provider baserom artifact SHA-256 mismatch"
        )

    source_blob = next(
        (
            raw_blob
            for raw_blob in snapshot.get("source_blobs", [])
            if isinstance(raw_blob, dict)
            and raw_blob.get("path") == PINNED_GOAL_OFFSET_SOURCE_PATH
        ),
        None,
    )
    if not isinstance(source_blob, dict):
        raise RawProviderError(
            "ja raw provider pinned baseline symbol map is missing"
        )
    if (
        source_blob.get("oid") != offset_source["blob_oid"]
        or source_blob.get("sha256") != offset_source["sha256"]
    ):
        raise RawProviderError(
            "ja raw provider baserom offset source metadata does not match "
            "the pinned Git blob"
        )
    try:
        offset_source_raw = (
            snapshot_path.parent / source_blob["vendored_path"]
        ).read_bytes()
    except (KeyError, OSError) as error:
        raise RawProviderError(
            "ja raw provider pinned baseline symbol map is unavailable"
        ) from error
    if hashlib.sha256(offset_source_raw).hexdigest() != offset_source["sha256"]:
        raise RawProviderError(
            "ja raw provider baserom offset source SHA-256 mismatch"
        )
    symbols = _baseline_symbols(
        offset_source_raw,
        source_path=PINNED_GOAL_OFFSET_SOURCE_PATH,
    )

    raw_records = specification["records"]
    if not isinstance(raw_records, dict) or set(raw_records) != PINNED_GOAL_TARGETS:
        raise RawProviderError(
            "ja raw provider baserom records must exactly cover goal targets"
        )
    slices: Dict[str, BaseromSlice] = {}
    expected_artifact_offset = 0
    for target in sorted(PINNED_GOAL_TARGETS):
        record = raw_records[target]
        if not isinstance(record, dict) or set(record) != {
            "artifact_offset",
            "bytes_sha256",
            "decoded_value",
            "length",
            "rom_address",
            "rom_offset",
            "symbol",
        }:
            raise RawProviderError(
                f"ja raw provider baserom record {target} metadata is invalid"
            )
        symbol = record["symbol"]
        if symbol != PINNED_GOAL_SYMBOLS[target]:
            raise RawProviderError(
                f"ja raw provider baserom record {target} symbol mismatch"
            )
        baseline = symbols.get(symbol)
        if baseline is None:
            raise RawProviderError(
                f"ja raw provider baseline symbol map has no {symbol}"
            )
        baseline_address, symbol_kind, owner = baseline
        if symbol_kind != "data" or owner != "GoalDisplay_Init":
            raise RawProviderError(
                f"ja raw provider baseline symbol {symbol} metadata is invalid"
            )
        rom_address = record["rom_address"]
        if (
            not isinstance(rom_address, str)
            or not re.fullmatch(r"0x08[0-9A-F]{6}", rom_address)
            or int(rom_address, 16) != baseline_address
        ):
            raise RawProviderError(
                f"ja raw provider baserom record {target} ROM address "
                "differs from the pinned baseline map"
            )
        rom_offset = record["rom_offset"]
        if (
            not isinstance(rom_offset, int)
            or isinstance(rom_offset, bool)
            or rom_offset != baseline_address - 0x08000000
        ):
            raise RawProviderError(
                f"ja raw provider baserom record {target} ROM offset "
                "differs from the pinned baseline map"
            )
        artifact_offset = record["artifact_offset"]
        length = record["length"]
        if (
            not isinstance(artifact_offset, int)
            or isinstance(artifact_offset, bool)
            or artifact_offset != expected_artifact_offset
            or not isinstance(length, int)
            or isinstance(length, bool)
            or length < 2
            or rom_offset + length > PINNED_FE8J_ROM_SIZE
        ):
            raise RawProviderError(
                f"ja raw provider baserom record {target} range is invalid"
            )
        end = artifact_offset + length
        if end > len(artifact_raw):
            raise RawProviderError(
                f"ja raw provider baserom record {target} artifact range "
                "is out of bounds"
            )
        raw_value = artifact_raw[artifact_offset:end]
        bytes_sha256 = record["bytes_sha256"]
        if (
            not isinstance(bytes_sha256, str)
            or not _SHA256_RE.fullmatch(bytes_sha256)
            or hashlib.sha256(raw_value).hexdigest() != bytes_sha256
        ):
            raise RawProviderError(
                f"ja raw provider baserom record {target} bytes hash mismatch"
            )
        if not raw_value.endswith(b"\0") or b"\0" in raw_value[:-1]:
            raise RawProviderError(
                f"ja raw provider baserom record {target} is not one "
                "NUL-terminated string"
            )
        try:
            decoded_value = raw_value[:-1].decode("cp932")
        except UnicodeDecodeError as error:
            raise RawProviderError(
                f"ja raw provider baserom record {target} is not valid CP932"
            ) from error
        if (
            not isinstance(record["decoded_value"], str)
            or record["decoded_value"] != decoded_value
        ):
            raise RawProviderError(
                f"ja raw provider baserom record {target} decoded value mismatch"
            )
        slices[target] = BaseromSlice(
            target=target,
            symbol=symbol,
            rom_address=rom_address,
            rom_offset=rom_offset,
            artifact_offset=artifact_offset,
            length=length,
            bytes_sha256=bytes_sha256,
            decoded_value=decoded_value,
            raw=raw_value,
        )
        expected_artifact_offset = end
    if expected_artifact_offset != len(artifact_raw):
        raise RawProviderError(
            "ja raw provider baserom artifact has unreferenced trailing bytes"
        )
    return BaseromSource(
        rom_sha256=rom["sha256"],
        rom_size=rom["size"],
        offset_source_path=offset_source["path"],
        offset_source_blob_oid=offset_source["blob_oid"],
        artifact_path=artifact_path,
        artifact_sha256=artifact_sha256,
        artifact_raw=artifact_raw,
        slices=slices,
    )


def _verify_baserom_bytes(
    baserom_source: BaseromSource,
    *,
    baserom_path: Path,
) -> None:
    try:
        raw = Path(baserom_path).read_bytes()
    except OSError as error:
        raise RawProviderError(
            f"FE8J baserom is unavailable: {baserom_path}"
        ) from error
    if len(raw) != baserom_source.rom_size:
        raise RawProviderError("FE8J baserom size mismatch")
    if hashlib.sha256(raw).hexdigest() != baserom_source.rom_sha256:
        raise RawProviderError("FE8J baserom SHA-256 mismatch")
    for target, source_slice in baserom_source.slices.items():
        actual = raw[
            source_slice.rom_offset : source_slice.rom_offset
            + source_slice.length
        ]
        if actual != source_slice.raw:
            raise RawProviderError(
                f"FE8J baserom bytes mismatch for {target}"
            )


def load_ja_raw_providers(
    data: Any,
    *,
    source_root: Path = Path("."),
    expected_repository: str | None = PINNED_SOURCE_REPOSITORY,
    expected_revision: str | None = PINNED_SOURCE_REVISION,
    baserom_path: Path | None = None,
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
    baserom_source = _load_baserom_source(
        snapshot,
        snapshot_path=snapshot_path,
    )
    if baserom_path is not None:
        if baserom_source is None:
            raise RawProviderError(
                "ja raw provider catalog has no baserom-backed targets"
            )
        _verify_baserom_bytes(
            baserom_source,
            baserom_path=baserom_path,
        )
    if git_source.revision != source_revision:
        raise RawProviderError(
            "ja raw provider Git source revision does not match catalog"
        )
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
    source_ranges: Dict[str, list[tuple[int, int, str]]] = {
        git_source.artifact_path: [],
    }
    if baserom_source is not None:
        source_ranges[baserom_source.artifact_path] = []
    used_source_paths = set()
    used_generated_source_paths = set()
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
            base_provider_fields | {"source_format"},
        ):
            raise RawProviderError(
                f"ja raw provider source snapshot {target} must contain "
                "byte_length, offset, source_anchor, source_path, "
                "source_value_index, symbol, and value_sha256, with optional "
                "source_format"
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
        if source_format not in (None, PINNED_GOAL_SOURCE_FORMAT):
            raise RawProviderError(
                f"ja raw provider {target} source_format is unsupported"
            )
        if target in PINNED_GOAL_TARGETS:
            if source_format != PINNED_GOAL_SOURCE_FORMAT:
                raise RawProviderError(
                    f"ja raw provider {target} must use the pinned baserom slice"
                )
        elif source_format is not None:
            raise RawProviderError(
                f"ja raw provider {target} cannot use goal-only source metadata"
            )
        if (
            not isinstance(source_path, str)
            or source_path not in git_source.blobs
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
        source_git_blob = git_source.blobs[source_path]
        used_source_paths.add(source_path)
        if source_format is None:
            used_generated_source_paths.add(source_path)
            source_blob = git_source.artifact_raw
            provider_artifact_path = git_source.artifact_path
            provider_artifact_sha256 = git_source.artifact_sha256
            provenance_kind = "pinned_git_source_artifact"
            rom_sha256 = None
            rom_address = None
            rom_offset = None
            decoded_value = None
        else:
            if baserom_source is None:
                raise RawProviderError(
                    f"ja raw provider {target} has no pinned baserom source"
                )
            source_slice = baserom_source.slices[target]
            if (
                source_path != baserom_source.offset_source_path
                or source_git_blob.oid
                != baserom_source.offset_source_blob_oid
                or source_anchor != source_slice.symbol
                or source_value_index != 0
                or offset != source_slice.artifact_offset
                or byte_length != source_slice.length
                or value_sha256 != source_slice.bytes_sha256
            ):
                raise RawProviderError(
                    f"ja raw provider {target} baserom metadata differs "
                    "from the pinned source manifest"
                )
            source_blob = baserom_source.artifact_raw
            provider_artifact_path = baserom_source.artifact_path
            provider_artifact_sha256 = baserom_source.artifact_sha256
            provenance_kind = "pinned_baserom_slice"
            rom_sha256 = baserom_source.rom_sha256
            rom_address = source_slice.rom_address
            rom_offset = source_slice.rom_offset
            decoded_value = source_slice.decoded_value
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
        if source_format == PINNED_GOAL_SOURCE_FORMAT:
            assert baserom_source is not None
            source_values = (baserom_source.slices[target].raw,)
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
            source_repository=git_source.repository,
            source_revision=git_source.revision,
            source_path=source_path,
            source_blob_oid=source_git_blob.oid,
            source_anchor=source_anchor,
            source_artifact_path=provider_artifact_path,
            source_artifact_sha256=provider_artifact_sha256,
            source_value_index=source_value_index,
            value_offset=offset,
            value_length=byte_length,
            value_sha256=value_sha256,
            provenance_kind=provenance_kind,
            rom_sha256=rom_sha256,
            rom_address=rom_address,
            rom_offset=rom_offset,
            decoded_value=decoded_value,
        )
        source_ranges[provider_artifact_path].append((offset, end, target))
    artifact_lengths = {
        git_source.artifact_path: len(git_source.artifact_raw),
    }
    if baserom_source is not None:
        artifact_lengths[baserom_source.artifact_path] = len(
            baserom_source.artifact_raw
        )
    for artifact_path, ranges in source_ranges.items():
        expected_offset = 0
        for offset, end, target in sorted(ranges):
            if offset != expected_offset:
                raise RawProviderError(
                    f"ja raw provider {target} source blob ranges overlap "
                    f"or leave gaps in {artifact_path}"
                )
            expected_offset = end
        if expected_offset != artifact_lengths[artifact_path]:
            raise RawProviderError(
                f"ja raw provider source blob has unreferenced trailing "
                f"bytes: {artifact_path}"
            )
    if used_generated_source_paths != set(git_source.generated_from_paths):
        raise RawProviderError(
            "ja raw provider entries do not use every generated source path"
        )
    if used_source_paths != set(git_source.blobs):
        raise RawProviderError(
            "ja raw provider entries do not use every pinned source blob"
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


def verify_ja_raw_provider_baserom(
    data: Any,
    *,
    source_root: Path,
    baserom_path: Path,
) -> None:
    load_ja_raw_providers(
        data,
        source_root=source_root,
        baserom_path=baserom_path,
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
