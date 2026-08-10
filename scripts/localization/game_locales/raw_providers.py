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
JA_RAW_PROVIDER_SCHEMA_VERSION = 3
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
class GitSource:
    repository: str
    revision: str
    blobs: Mapping[str, GitSourceBlob]
    generated_from_paths: tuple[str, ...]
    artifact_path: str
    artifact_sha256: str
    artifact_raw: bytes


_C_STRING_RE = re.compile(r'"(?:\\.|[^"\\])*"', re.DOTALL)


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
    anchor = re.escape(source_anchor)
    initializer = re.search(
        rf"\b{anchor}\b[^;]*=\s*(.*?);",
        source,
        flags=re.DOTALL,
    )
    if initializer is not None:
        body = initializer.group(1)
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

    for line in source.splitlines():
        if re.search(rf"\b{anchor}\b", line) is None:
            continue
        string_tokens = _C_STRING_RE.findall(line)
        if string_tokens:
            return b"".join(
                _decode_c_string(token, source_path=source_path)
                for token in string_tokens
            )
    return None


def _extract_asm_anchor_bytes(
    source: str,
    *,
    source_path: str,
    source_anchor: str,
) -> bytes | None:
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
        byte_directive = re.search(r"\.byte\s+([^/]+)", line)
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
        asciz = re.search(r"\.asciz\s+(" + _C_STRING_RE.pattern + r")", line)
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
        generated_from_paths=tuple(generated_from_paths),
        artifact_path=artifact_path,
        artifact_sha256=expected_sha256,
        artifact_raw=raw,
    )


def load_ja_raw_providers(
    data: Any,
    *,
    source_root: Path = Path("."),
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
        if not isinstance(snapshot_provider, dict) or set(snapshot_provider) != {
            "byte_length",
            "offset",
            "source_anchor",
            "source_path",
            "symbol",
            "value_sha256",
        }:
            raise RawProviderError(
                f"ja raw provider source snapshot {target} must contain "
                "byte_length, offset, source_anchor, source_path, symbol, "
                "and value_sha256"
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
        if not isinstance(source_path, str) or source_path not in git_source.blobs:
            raise RawProviderError(
                f"ja raw provider {target} source_path is not pinned"
            )
        if not isinstance(source_anchor, str) or not source_anchor:
            raise RawProviderError(
                f"ja raw provider {target} source_anchor must be non-empty"
            )
        source_git_blob = git_source.blobs[source_path]
        used_source_paths.add(source_path)
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
        source_values = _extract_source_anchor_values(
            source_git_blob,
            source_anchor=source_anchor,
        )
        if raw_value not in source_values:
            raise RawProviderError(
                f"ja raw provider {target} artifact value is not extractable "
                f"from {source_path}:{source_anchor}"
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
            source_artifact_path=git_source.artifact_path,
            source_artifact_sha256=git_source.artifact_sha256,
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
    git_source = _load_git_source(snapshot, snapshot_path=snapshot_path)
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
