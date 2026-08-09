"""Machine-checked closure ledger for FE8CN raw localized string records."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Mapping

from .mapping import MappingError, validate_mapping_document
from .raw_providers import (
    RawProviderError,
    load_ja_raw_providers,
    resolve_ja_raw_text,
)

CLOSURE_SCHEMA_VERSION = 2
DECISIONS_KIND = "fe8cn-raw-surface-decisions"
CLOSURE_KIND = "fe8cn-raw-surface-closure"
DECISION_CLASSES = (
    "game_message",
    "expansion_message",
    "non_user_facing_exclusion",
    "diagnostic_exclusion",
    "english_fallback",
)
_IMPORT_ID_RE = re.compile(r"fe8cn\.raw\.import-[0-9]{4}")
_TARGET_ID_RE = re.compile(r"0x[0-9A-F]{4}")
_IDENTIFIER_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


class RawClosureError(MappingError):
    """Raised when a raw import lacks a durable closure decision."""


def canonical_json_bytes(data: Any) -> bytes:
    return (
        json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def _require_dict(value: Any, field: str) -> Dict[str, Any]:
    if not isinstance(value, dict):
        raise RawClosureError(f"{field} must be an object")
    return value


def _require_string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise RawClosureError(f"{field} must be a non-empty string")
    return value


def _load_raw_records(data: Any) -> Dict[str, Dict[str, Any]]:
    document = _require_dict(data, "raw")
    if document.get("schema_version") != 2:
        raise RawClosureError("raw.schema_version must be 2")
    records = document.get("records")
    if not isinstance(records, list):
        raise RawClosureError("raw.records must be an array")
    result: Dict[str, Dict[str, Any]] = {}
    for index, record in enumerate(records):
        field = f"raw.records[{index}]"
        record = _require_dict(record, field)
        import_id = _require_string(record.get("import_id"), f"{field}.import_id")
        if not _IMPORT_ID_RE.fullmatch(import_id):
            raise RawClosureError(f"{field}.import_id is not canonical")
        _require_string(record.get("text"), f"{field}.text")
        provenance = _require_dict(record.get("provenance"), f"{field}.provenance")
        _require_string(provenance.get("address"), f"{field}.provenance.address")
        if import_id in result:
            raise RawClosureError(f"duplicate raw import {import_id}")
        result[import_id] = record
    return result


def _validate_call_sites(
    call_sites: Any,
    *,
    field: str,
    repo_root: Path,
) -> List[Dict[str, Any]]:
    if not isinstance(call_sites, list) or not call_sites:
        raise RawClosureError(f"{field} must be a non-empty array")
    normalized = []
    for index, raw_site in enumerate(call_sites):
        site_field = f"{field}[{index}]"
        site = _require_dict(raw_site, site_field)
        relative_path = _require_string(site.get("path"), f"{site_field}.path")
        anchors = site.get("anchors")
        if not isinstance(anchors, list) or not anchors:
            raise RawClosureError(f"{site_field}.anchors must be a non-empty array")
        if any(not isinstance(anchor, str) or not anchor for anchor in anchors):
            raise RawClosureError(
                f"{site_field}.anchors must contain non-empty strings"
            )
        path = repo_root / relative_path
        if not path.is_file():
            raise RawClosureError(f"{site_field}.path disappeared: {relative_path}")
        source = path.read_text(encoding="utf-8")
        cursor = 0
        missing = []
        for anchor in anchors:
            position = source.find(anchor, cursor)
            if position < 0:
                missing.append(anchor)
                continue
            cursor = position + len(anchor)
        if missing:
            raise RawClosureError(
                f"{site_field} is missing ordered anchors in {relative_path}: "
                f"{missing}"
            )
        normalized.append({"anchors": list(anchors), "path": relative_path})
    return normalized


def _strip_c_comments(source: str) -> str:
    source = re.sub(r"/\*.*?\*/", " ", source, flags=re.DOTALL)
    return re.sub(r"//[^\n]*", " ", source)


def _function_body(source: str, symbol: str, field: str) -> str:
    source = _strip_c_comments(source)
    match = re.search(
        rf"\b{re.escape(symbol)}\s*\([^;{{}}]*\)\s*\{{",
        source,
        flags=re.DOTALL,
    )
    if match is None:
        raise RawClosureError(f"{field}.symbol is not a function definition")

    start = match.end() - 1
    depth = 0
    for index in range(start, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[start : index + 1]
    raise RawClosureError(f"{field}.symbol has an unterminated function body")


def _validate_runtime_consumers(
    consumers: Any,
    *,
    field: str,
    repo_root: Path,
) -> List[Dict[str, Any]]:
    if not isinstance(consumers, list) or not consumers:
        raise RawClosureError(f"{field} must be a non-empty array")

    normalized = []
    for index, raw_consumer in enumerate(consumers):
        consumer_field = f"{field}[{index}]"
        consumer = _require_dict(raw_consumer, consumer_field)
        relative_path = _require_string(
            consumer.get("path"), f"{consumer_field}.path"
        )
        symbol = _require_string(consumer.get("symbol"), f"{consumer_field}.symbol")
        if not _IDENTIFIER_RE.fullmatch(symbol):
            raise RawClosureError(f"{consumer_field}.symbol must be a C identifier")
        anchors = consumer.get("anchors")
        if not isinstance(anchors, list) or not anchors:
            raise RawClosureError(
                f"{consumer_field}.anchors must be a non-empty array"
            )
        if any(not isinstance(anchor, str) or not anchor for anchor in anchors):
            raise RawClosureError(
                f"{consumer_field}.anchors must contain non-empty strings"
            )

        path = repo_root / relative_path
        if not path.is_file():
            raise RawClosureError(
                f"{consumer_field}.path disappeared: {relative_path}"
            )
        body = _function_body(
            path.read_text(encoding="utf-8"),
            symbol,
            consumer_field,
        )
        missing = []
        cursor = 0
        for anchor in anchors:
            position = body.find(anchor, cursor)
            if position < 0:
                missing.append(anchor)
                continue
            cursor = position + len(anchor)
        if missing:
            raise RawClosureError(
                f"{consumer_field} runtime consumer {symbol} in {relative_path} "
                f"is missing ordered anchors: {missing}"
            )
        normalized.append(
            {
                "anchors": list(anchors),
                "path": relative_path,
                "symbol": symbol,
            }
        )
    return normalized


def _validate_runtime_payload_source(
    source_data: Any,
    *,
    field: str,
    repo_root: Path,
) -> Dict[str, Any]:
    source = _require_dict(source_data, field)
    if source.get("kind") != "c_string_symbol":
        raise RawClosureError(f"{field}.kind must be 'c_string_symbol'")
    relative_path = _require_string(source.get("path"), f"{field}.path")
    symbol = _require_string(source.get("symbol"), f"{field}.symbol")
    if not _IDENTIFIER_RE.fullmatch(symbol):
        raise RawClosureError(f"{field}.symbol must be a C identifier")

    path = repo_root / relative_path
    if not path.is_file():
        raise RawClosureError(f"{field}.path disappeared: {relative_path}")
    source_text = _strip_c_comments(path.read_text(encoding="utf-8"))
    match = re.search(
        rf'\bconst\s+char\s+{re.escape(symbol)}\s*\[\s*\]\s*=\s*"([^"\\]*)"\s*;',
        source_text,
    )
    if match is None:
        raise RawClosureError(
            f"{field} cannot resolve const char {symbol}[] in {relative_path}"
        )
    return {
        "kind": "c_string_symbol",
        "path": relative_path,
        "symbol": symbol,
        "text": match.group(1),
    }


def _validate_decisions(
    data: Any,
    *,
    raw_records: Mapping[str, Mapping[str, Any]],
    repo_root: Path,
) -> Dict[str, Dict[str, Any]]:
    document = _require_dict(data, "decisions")
    if document.get("schema_version") != CLOSURE_SCHEMA_VERSION:
        raise RawClosureError(
            f"decisions.schema_version must be {CLOSURE_SCHEMA_VERSION}"
        )
    if document.get("kind") != DECISIONS_KIND:
        raise RawClosureError(f"decisions.kind must be {DECISIONS_KIND!r}")
    rows = document.get("decisions")
    if not isinstance(rows, list):
        raise RawClosureError("decisions.decisions must be an array")
    if document.get("deferred_record_count") != len(rows):
        raise RawClosureError("decisions.deferred_record_count does not match rows")

    result: Dict[str, Dict[str, Any]] = {}
    for index, raw_decision in enumerate(rows):
        field = f"decisions.decisions[{index}]"
        decision = _require_dict(raw_decision, field)
        import_id = _require_string(decision.get("import_id"), f"{field}.import_id")
        if import_id not in raw_records:
            raise RawClosureError(f"{field}.import_id is absent from raw source")
        if import_id in result:
            raise RawClosureError(f"duplicate deferred decision {import_id}")
        classification = decision.get("classification")
        if classification not in DECISION_CLASSES:
            raise RawClosureError(
                f"{field}.classification must be one of {DECISION_CLASSES}"
            )
        user_facing = decision.get("user_facing")
        if not isinstance(user_facing, bool):
            raise RawClosureError(f"{field}.user_facing must be a boolean")
        if classification.endswith("_exclusion") and user_facing:
            raise RawClosureError(f"{field} exclusions cannot be user-facing")
        _require_string(decision.get("rationale"), f"{field}.rationale")
        normalized = dict(decision)
        normalized["call_sites"] = _validate_call_sites(
            decision.get("call_sites"),
            field=f"{field}.call_sites",
            repo_root=repo_root,
        )
        if classification == "game_message":
            target_id = decision.get("target_id")
            if not isinstance(target_id, str) or not _TARGET_ID_RE.fullmatch(target_id):
                raise RawClosureError(
                    f"{field}.target_id must use canonical 0xNNNN form"
                )
        elif classification == "expansion_message":
            _require_string(decision.get("expansion_key"), f"{field}.expansion_key")
            normalized["runtime_consumers"] = _validate_runtime_consumers(
                decision.get("runtime_consumers"),
                field=f"{field}.runtime_consumers",
                repo_root=repo_root,
            )
            if "runtime_payload_source" in decision:
                normalized["_runtime_payload_source"] = (
                    _validate_runtime_payload_source(
                        decision["runtime_payload_source"],
                        field=f"{field}.runtime_payload_source",
                        repo_root=repo_root,
                    )
                )
        elif classification == "english_fallback":
            _require_string(decision.get("fallback_reason"), f"{field}.fallback_reason")
        result[import_id] = normalized
    return result


def _mapped_imports(
    mapping_data: Any,
    *,
    repo_root: Path,
) -> Dict[str, Dict[str, Any]]:
    try:
        mapping = validate_mapping_document(mapping_data, repo_root=repo_root)
    except MappingError as error:
        raise RawClosureError(f"mapping literal evidence failed: {error}") from error
    result: Dict[str, Dict[str, Any]] = {}
    for row in mapping.rows:
        source = row.source
        if row.source_kind == "raw":
            raw_source = source
        elif row.source_kind == "authored":
            raw_source = (
                (row.verification or {})
                .get("promotion", {})
                .get("details", {})
                .get("incorrect_source", {})
            )
            if raw_source.get("kind") != "raw":
                continue
        else:
            continue
        for import_id in (
            raw_source["import_id"],
            *raw_source.get("alternate_import_ids", []),
        ):
            entry = result.setdefault(import_id, {"rows": [], "target_ids": []})
            entry["rows"].append(row)
            entry["target_ids"].append(f"0x{row.target_id:04X}")
    for entry in result.values():
        paired = sorted(
            zip(entry["target_ids"], entry["rows"]),
            key=lambda pair: pair[0],
        )
        entry["target_ids"] = [target_id for target_id, _ in paired]
        entry["rows"] = [row for _, row in paired]
    return result


def _derived_call_sites(rows: List[Any], repo_root: Path) -> List[Dict[str, Any]]:
    combined = []
    for row in rows:
        combined.extend(_derived_row_call_sites(row, repo_root))
    if not combined:
        raise RawClosureError("raw mapping group lacks an FE8U call-site path")
    unique = {
        (site["path"], tuple(site["anchors"])): site for site in combined
    }
    return [unique[key] for key in sorted(unique)]


def _derived_row_call_sites(row: Any, repo_root: Path) -> List[Dict[str, Any]]:
    verification = row.verification or {}
    if row.source_kind == "authored":
        incorrect_source = (
            verification.get("promotion", {})
            .get("details", {})
            .get("incorrect_source", {})
        )
        if incorrect_source.get("kind") == "raw":
            verification = verification["promotion"]["original_verification"]
    source_paths = verification.get("source_paths", {})
    relative_path = source_paths.get("fe8u")
    if not relative_path:
        raise RawClosureError(
            f"0x{row.target_id:04X} raw mapping lacks an FE8U call-site path"
        )
    source_key = verification.get("source_key", "")
    source_symbol = verification.get("source_symbol", "")
    source = (repo_root / relative_path).read_text(encoding="utf-8")
    target_match = re.search(
        rf"0x0*{row.target_id:X}\b",
        source,
        flags=re.IGNORECASE,
    )
    target_token = target_match.group(0) if target_match is not None else ""
    if source_symbol == "gTerrains_0":
        anchors = [source_symbol, f"[{source_key}]"]
    elif source_symbol == "GoalDisplay_Init":
        anchors = [source_symbol, f"GetStringFromIndex(MSG_{row.target_id:X})"]
    elif source_key and source_key in source:
        anchors = [source_symbol, source_key]
    else:
        override_match = re.search(r"override\[(0x[0-9a-fA-F]+)\]", source_key)
        if override_match is not None:
            override_value = int(override_match.group(1), 16)
            designated_target = f".nameMsgId = 0x{row.target_id:04X}"
            if designated_target in source:
                anchors = [
                    source_symbol,
                    designated_target,
                    f".overrideId = {override_value},",
                ]
            else:
                override_token_match = re.search(
                    rf"0x0*{override_value:X}\b",
                    source,
                    flags=re.IGNORECASE,
                )
                if not target_token or override_token_match is None:
                    raise RawClosureError(
                        f"0x{row.target_id:04X} raw mapping cannot resolve "
                        "its menu target/override relationship"
                    )
                anchors = [
                    source_symbol,
                    target_token,
                    override_token_match.group(0),
                ]
        elif re.search(r"\[[0-9]+\]\.name$", source_key):
            if not target_token:
                raise RawClosureError(
                    f"0x{row.target_id:04X} raw mapping cannot resolve "
                    "its menu target relationship"
                )
            anchors = [source_symbol, target_token]
        else:
            raise RawClosureError(
                f"0x{row.target_id:04X} raw mapping lacks a verifiable "
                "call-site relationship"
            )
    return _validate_call_sites(
        [{"path": relative_path, "anchors": anchors}],
        field=f"mapping.0x{row.target_id:04X}.call_sites",
        repo_root=repo_root,
    )


def _active_registry_keys(registry_data: Any) -> set[str]:
    registry = _require_dict(registry_data, "registry")
    messages = registry.get("messages")
    if not isinstance(messages, list):
        raise RawClosureError("registry.messages must be an array")
    return {
        row["key"]
        for row in messages
        if isinstance(row, dict) and row.get("status") == "active"
    }


def _catalog_strings(data: Any, locale: str) -> Dict[str, str]:
    catalog = _require_dict(data, f"catalog.{locale}")
    if catalog.get("locale") != locale:
        raise RawClosureError(f"catalog.{locale}.locale must be {locale!r}")
    strings = catalog.get("strings")
    if not isinstance(strings, dict):
        raise RawClosureError(f"catalog.{locale}.strings must be an object")
    return strings


def build_raw_surface_closure(
    *,
    raw_data: Any,
    mapping_data: Any,
    decisions_data: Any,
    ja_raw_provider_data: Any,
    registry_data: Any,
    catalog_data: Mapping[str, Any],
    repo_root: Path,
) -> Dict[str, Any]:
    repo_root = Path(repo_root)
    raw_records = _load_raw_records(raw_data)
    decisions = _validate_decisions(
        decisions_data,
        raw_records=raw_records,
        repo_root=repo_root,
    )
    mapped = _mapped_imports(mapping_data, repo_root=repo_root)
    try:
        ja_raw_providers = load_ja_raw_providers(
            ja_raw_provider_data,
            source_root=repo_root / "texts/locales/ja",
        )
    except RawProviderError as error:
        raise RawClosureError(f"Japanese raw provider catalog failed: {error}") from error
    active_keys = _active_registry_keys(registry_data)
    catalogs = {
        locale: _catalog_strings(catalog_data[locale], locale)
        for locale in ("en", "ja", "zh-Hans")
    }

    rows = []
    for import_id, raw_record in sorted(raw_records.items()):
        source_hash = hashlib.sha256(
            raw_record["text"].encode("utf-8")
        ).hexdigest()
        decision = decisions.get(import_id)
        if decision is None:
            mapped_entry = mapped.get(import_id)
            if mapped_entry is None:
                raise RawClosureError(f"{import_id} has no closure decision")
            closure_row = {
                "call_sites": _derived_call_sites(mapped_entry["rows"], repo_root),
                "classification": "game_message",
                "decision_origin": "verified-game-map",
                "import_id": import_id,
                "rationale": mapped_entry["rows"][0].verification["rationale"],
                "source_text_sha256": source_hash,
                "target_ids": mapped_entry["target_ids"],
                "user_facing": True,
            }
            if len(mapped_entry["target_ids"]) == 1:
                closure_row["target_id"] = mapped_entry["target_ids"][0]
        else:
            classification = decision["classification"]
            closure_row = {
                "call_sites": decision["call_sites"],
                "classification": classification,
                "decision_origin": "deferred-surface-audit",
                "import_id": import_id,
                "rationale": decision["rationale"],
                "source_text_sha256": source_hash,
                "user_facing": decision["user_facing"],
            }
            if classification == "game_message":
                mapped_entry = mapped.get(import_id)
                if mapped_entry is None:
                    raise RawClosureError(
                        f"{import_id} game-message decision is absent from verified mapping"
                    )
                if decision["target_id"] not in mapped_entry["target_ids"]:
                    raise RawClosureError(
                        f"{import_id} target mismatch: {decision['target_id']} not in "
                        f"{mapped_entry['target_ids']}"
                    )
                closure_row["target_id"] = decision["target_id"]
                closure_row["target_ids"] = mapped_entry["target_ids"]
            elif classification == "expansion_message":
                key = decision["expansion_key"]
                if key not in active_keys:
                    raise RawClosureError(
                        f"{import_id} expansion key {key!r} is not active"
                    )
                for locale, strings in catalogs.items():
                    if not isinstance(strings.get(key), str) or not strings[key]:
                        raise RawClosureError(
                            f"{import_id} expansion key {key!r} is missing in {locale}"
                        )
                payload_source = decision.get("_runtime_payload_source")
                if payload_source is None:
                    if catalogs["zh-Hans"][key] != raw_record["text"]:
                        raise RawClosureError(
                            f"{import_id} zh-Hans expansion text must equal "
                            "imported raw payload"
                        )
                else:
                    for locale in ("en", "ja", "zh-Hans"):
                        if catalogs[locale][key] != payload_source["text"]:
                            raise RawClosureError(
                                f"{import_id} {locale} expansion text must equal "
                                f"{payload_source['symbol']}"
                            )
                closure_row["expansion_key"] = key
                closure_row["runtime_consumers"] = decision["runtime_consumers"]
                if payload_source is not None:
                    closure_row["runtime_payload_source"] = {
                        "kind": payload_source["kind"],
                        "path": payload_source["path"],
                        "symbol": payload_source["symbol"],
                        "text_sha256": hashlib.sha256(
                            payload_source["text"].encode("utf-8")
                        ).hexdigest(),
                    }
                closure_row["providers"] = {
                    locale: {
                        "kind": "expansion_catalog",
                        "text_sha256": hashlib.sha256(
                            catalogs[locale][key].encode("utf-8")
                        ).hexdigest(),
                    }
                    for locale in ("en", "ja", "zh-Hans")
                }
            elif classification == "english_fallback":
                closure_row["fallback_reason"] = decision["fallback_reason"]

        if closure_row["classification"] == "game_message":
            mapped_entry = mapped.get(import_id)
            if mapped_entry is None:
                raise RawClosureError(
                    f"{import_id} game-message provider is absent from verified mapping"
                )
            mapping_row = mapped_entry["rows"][0]
            if mapping_row.source_kind == "authored":
                payload_sha256 = (
                    mapping_row.verification["promotion"]["details"][
                        "payload_sha256"
                    ]
                )
                closure_row["providers"] = {
                    locale: {
                        "kind": "authored_semantic_correction",
                        "text_sha256": payload_sha256[locale],
                    }
                    for locale in ("ja", "zh-Hans")
                }
            else:
                ja_source = mapping_row.source.get(
                    "regional_sources", {}
                ).get("ja", {})
                try:
                    ja_text = resolve_ja_raw_text(
                        target_id=mapping_row.target_id,
                        ja_source=ja_source,
                        providers=ja_raw_providers,
                    )
                except RawProviderError as error:
                    raise RawClosureError(f"{import_id}: {error}") from error
                zh_text = raw_record["text"]
                if not ja_text or not zh_text:
                    raise RawClosureError(
                        f"{import_id} game-message provider payloads must be non-empty"
                    )
                closure_row["providers"] = {
                    "ja": {
                        "kind": f"raw_{ja_source['kind']}",
                        "text_sha256": hashlib.sha256(
                            ja_text.encode("utf-8")
                        ).hexdigest(),
                    },
                    "zh-Hans": {
                        "kind": "raw_import",
                        "text_sha256": hashlib.sha256(
                            zh_text.encode("utf-8")
                        ).hexdigest(),
                    },
                }
        closure_row["provenance"] = {
            "address": raw_record["provenance"]["address"],
        }
        rows.append(closure_row)

    counts = {
        classification: sum(
            1 for row in rows if row["classification"] == classification
        )
        for classification in DECISION_CLASSES
    }
    unresolved = len(raw_records) - len(rows)
    runtime_consumer_verified_count = sum(
        1
        for row in rows
        if row["classification"] == "expansion_message"
        and row.get("runtime_consumers")
    )
    provider_count = counts["game_message"] + runtime_consumer_verified_count
    ja_materialized_count = sum(
        1
        for row in rows
        if isinstance(row.get("providers", {}).get("ja", {}).get("text_sha256"), str)
    )
    zh_materialized_count = sum(
        1
        for row in rows
        if isinstance(
            row.get("providers", {}).get("zh-Hans", {}).get("text_sha256"), str
        )
    )
    summary = {
        "baseline_game_message_count": sum(
            1 for row in rows if row["decision_origin"] == "verified-game-map"
        ),
        "deferred_decision_count": len(decisions),
        "diagnostic_exclusion_count": counts["diagnostic_exclusion"],
        "english_fallback_count": counts["english_fallback"],
        "expansion_message_count": counts["expansion_message"],
        "game_message_count": counts["game_message"],
        "ja_materialized_count": ja_materialized_count,
        "non_user_facing_exclusion_count": counts["non_user_facing_exclusion"],
        "total_count": len(raw_records),
        "unresolved_count": unresolved,
        "provider_count": provider_count,
        "runtime_consumer_verified_count": runtime_consumer_verified_count,
        "user_facing_deferred_localized_count": sum(
            1
            for row in rows
            if row["decision_origin"] == "deferred-surface-audit"
            and row["user_facing"]
            and row["classification"] in ("game_message", "expansion_message")
        ),
        "zh_hans_materialized_count": zh_materialized_count,
    }
    excluded = counts["non_user_facing_exclusion"] + counts["diagnostic_exclusion"]
    if (
        unresolved
        or counts["english_fallback"]
        or excluded
        or provider_count != len(raw_records)
        or runtime_consumer_verified_count != counts["expansion_message"]
        or ja_materialized_count != len(raw_records)
        or zh_materialized_count != len(raw_records)
    ):
        raise RawClosureError(
            "raw closure strict gate failed: "
            f"total={len(raw_records)} providers={provider_count} "
            f"fallback={counts['english_fallback']} exclusions={excluded} "
            f"unresolved={unresolved} ja={ja_materialized_count} "
            f"zh-Hans={zh_materialized_count}"
        )
    return {
        "kind": CLOSURE_KIND,
        "rows": rows,
        "schema_version": CLOSURE_SCHEMA_VERSION,
        "summary": summary,
    }
