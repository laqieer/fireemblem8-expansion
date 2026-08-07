"""Materialized locale payloads for verified raw-symbol providers."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, Mapping

JA_RAW_PROVIDER_KIND = "fe8j-raw-provider-catalog"
JA_RAW_PROVIDER_SCHEMA_VERSION = 1
_TARGET_ID_RE = re.compile(r"0x[0-9A-F]{4}")


class RawProviderError(ValueError):
    """Raised when a raw-symbol provider catalog is invalid or incomplete."""


@dataclass(frozen=True)
class RawProvider:
    symbol: str
    text: str


def load_ja_raw_providers(data: Any) -> Dict[int, RawProvider]:
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
    if not isinstance(source_revision, str) or not source_revision:
        raise RawProviderError("ja raw provider source_revision must be non-empty")

    raw_providers = data.get("providers")
    if not isinstance(raw_providers, dict):
        raise RawProviderError("ja raw provider providers must be an object")
    if data.get("provider_count") != len(raw_providers):
        raise RawProviderError("ja raw provider provider_count does not match providers")

    providers: Dict[int, RawProvider] = {}
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
        target_id = int(target, 16)
        if target_id in providers:
            raise RawProviderError(f"duplicate ja raw provider target {target}")
        providers[target_id] = RawProvider(symbol=symbol, text=text)
    return providers


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
    return provider.text
