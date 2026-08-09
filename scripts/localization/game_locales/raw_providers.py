"""Materialized locale payloads for verified raw-symbol providers."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Mapping

JA_RAW_PROVIDER_KIND = "fe8j-raw-provider-catalog"
JA_RAW_PROVIDER_SCHEMA_VERSION = 2
_TARGET_ID_RE = re.compile(r"0x[0-9A-F]{4}")
_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_REVISION_RE = re.compile(r"[0-9a-f]{40}")


class RawProviderError(ValueError):
    """Raised when a raw-symbol provider catalog is invalid or incomplete."""


@dataclass(frozen=True)
class RawProvider:
    symbol: str
    text: str
    source_blob_path: str
    source_blob_sha256: str
    value_offset: int
    value_length: int
    value_sha256: str


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
    if snapshot.get("schema_version") != 2:
        raise RawProviderError(
            "ja raw provider source snapshot schema_version must be 2"
        )
    if snapshot.get("kind") != "fe8j-raw-symbol-source-snapshot":
        raise RawProviderError("ja raw provider source snapshot kind is invalid")
    return snapshot, path


def _load_source_blob(
    snapshot: Mapping[str, Any],
    *,
    snapshot_path: Path,
) -> tuple[bytes, str, str]:
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
    if not isinstance(revision, str) or not _REVISION_RE.fullmatch(revision):
        raise RawProviderError(
            "ja raw provider source snapshot revision must be a full commit SHA"
        )
    if (
        not isinstance(source_url, str)
        or revision not in source_url
        or not source_url.startswith(repository)
    ):
        raise RawProviderError(
            "ja raw provider source snapshot URL must pin the full revision"
        )

    blob_specification = snapshot.get("source_blob")
    if not isinstance(blob_specification, dict) or set(blob_specification) != {
        "encoding",
        "path",
        "sha256",
    }:
        raise RawProviderError(
            "ja raw provider source_blob must contain encoding, path, and sha256"
        )
    if blob_specification["encoding"] != "cp932-nul-terminated":
        raise RawProviderError(
            "ja raw provider source_blob encoding must be cp932-nul-terminated"
        )
    relative_path = blob_specification["path"]
    expected_sha256 = blob_specification["sha256"]
    if not isinstance(relative_path, str) or not relative_path:
        raise RawProviderError("ja raw provider source_blob.path is invalid")
    if not isinstance(expected_sha256, str) or not _SHA256_RE.fullmatch(
        expected_sha256
    ):
        raise RawProviderError(
            "ja raw provider source_blob.sha256 must be a lowercase SHA-256"
        )
    path = snapshot_path.parent / relative_path
    try:
        raw = path.read_bytes()
    except OSError as error:
        raise RawProviderError(
            f"ja raw provider exact source blob is unavailable: {path}"
        ) from error
    if hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise RawProviderError("ja raw provider exact source blob SHA-256 mismatch")
    return raw, relative_path, expected_sha256


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
    if not isinstance(source_revision, str) or not _REVISION_RE.fullmatch(
        source_revision
    ):
        raise RawProviderError(
            "ja raw provider source_revision must be a full commit SHA"
        )
    snapshot, snapshot_path = _load_source_snapshot(
        data,
        source_root=source_root,
    )
    if snapshot.get("source_revision") != source_revision:
        raise RawProviderError(
            "ja raw provider source snapshot revision does not match catalog"
        )
    source_blob, source_blob_path, source_blob_sha256 = _load_source_blob(
        snapshot,
        snapshot_path=snapshot_path,
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
    source_ranges = []
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
            "symbol",
            "value_sha256",
        }:
            raise RawProviderError(
                f"ja raw provider source snapshot {target} must contain "
                "byte_length, offset, symbol, and value_sha256"
            )
        if snapshot_provider["symbol"] != symbol:
            raise RawProviderError(
                f"ja raw provider {target} source symbol mismatch"
            )
        offset = snapshot_provider["offset"]
        byte_length = snapshot_provider["byte_length"]
        value_sha256 = snapshot_provider["value_sha256"]
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
        target_id = int(target, 16)
        if target_id in providers:
            raise RawProviderError(f"duplicate ja raw provider target {target}")
        providers[target_id] = RawProvider(
            symbol=symbol,
            text=text,
            source_blob_path=source_blob_path,
            source_blob_sha256=source_blob_sha256,
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
    return providers


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
