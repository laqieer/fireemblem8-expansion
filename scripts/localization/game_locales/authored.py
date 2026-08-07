"""Deterministic validation and merging for full-game authored translations."""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Mapping, Tuple

from scripts.localization.game_catalog.english_source import (
    load_english_source_entries,
)

from .mapping import validate_mapping_document

AUTHORED_CATALOG_KIND = "fe8u-game-authored-catalog"
AUTHORED_CATALOG_SCHEMA_VERSION = 1
AUTHORED_MANIFEST_KIND = "fe8u-authored-translation-manifest"
AUTHORED_MANIFEST_SCHEMA_VERSION = 1
AUTHORED_SHARD_KIND = "fe8u-authored-translation-shard"
AUTHORED_SHARD_SCHEMA_VERSION = 1
DEFAULT_MANIFEST_PATH = Path("texts/locales/authored/manifest.json")
LOCALES = ("ja", "zh-Hans")

_SHARD_FIELDS = {
    "kind",
    "locale",
    "schema_version",
    "shard",
    "source_map_sha256",
    "source_queue",
    "subsystem_counts",
    "target_count",
    "terminology_sources",
    "translations",
}
_TRANSLATION_FIELDS = {
    "english_payload_sha256",
    "key",
    "source_text_sha256",
    "subsystem",
    "target_id",
    "text",
}
_TOKEN_OR_NEWLINE_RE = re.compile(r"\[[^\[\]\r\n]+\]|\r\n|\r|\n")
_TOKEN_RE = re.compile(r"\[[^\[\]\r\n]+\]")
_ASCII_WORD_RE = re.compile(r"[A-Za-z]{2,}")
_ALLOWED_ASCII_TERMS = {"CP", "CPU", "LV", "Pt", "SRAM", "START"}


class AuthoredCatalogError(ValueError):
    """Raised when authored shards or generated catalogs violate their contract."""


def canonical_json_bytes(data: Any) -> bytes:
    return (
        json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _load_json(path: Path) -> Tuple[bytes, Any]:
    raw = path.read_bytes()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise AuthoredCatalogError(f"{path}: file is not strict UTF-8") from error
    if text.startswith("\ufeff"):
        raise AuthoredCatalogError(f"{path}: UTF-8 BOM is not allowed")
    try:
        return raw, json.loads(text)
    except json.JSONDecodeError as error:
        raise AuthoredCatalogError(f"{path}: invalid JSON: {error}") from error


def _require_digest(value: Any, *, field: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise AuthoredCatalogError(f"{field} must be a lowercase SHA-256 digest")
    return value


def _normalize_legacy_shard(
    data: Mapping[str, Any],
    *,
    shard_name: str,
    queue_source: Mapping[str, str],
    source_map_sha256: str,
    queue_by_id: Mapping[str, Mapping[str, Any]],
) -> Dict[str, Any]:
    """Normalize the three reviewed legacy shard shapes without weakening validation."""

    kind = data.get("kind")
    if kind == "fe8-authored-translation-shard":
        locale = data.get("locale_id")
        subsystem = data.get("subsystem")
        source_rows = data.get("entries")
        translations = []
        if not isinstance(source_rows, list):
            raise AuthoredCatalogError(f"{shard_name}: legacy entries must be a list")
        for row in source_rows:
            target = queue_by_id.get(row.get("target_id"))
            if target is None:
                raise AuthoredCatalogError(
                    f"{shard_name}: unknown target {row.get('target_id')!r}"
                )
            translations.append(
                {
                    "english_payload_sha256": target["english_payload_sha256"],
                    "key": row.get("suggested_key"),
                    "source_text_sha256": sha256_bytes(
                        target["source_text"].encode("utf-8")
                    ),
                    "subsystem": subsystem,
                    "target_id": row.get("target_id"),
                    "text": row.get("text"),
                }
            )
        subsystem_counts = {subsystem: len(translations)}
        terminology_sources: List[Mapping[str, Any]] = []
    elif kind == AUTHORED_SHARD_KIND and "source_queue" in data:
        locale = data.get("locale")
        translations = data.get("translations")
        subsystem_counts = data.get("subsystem_counts")
        terminology_sources = data.get("terminology_sources", [])
    elif data.get("$schema") == "fe8u-authored-translation-shard-v1":
        locale = data.get("locale")
        source_rows = data.get("translations")
        translations = []
        if not isinstance(source_rows, list):
            raise AuthoredCatalogError(
                f"{shard_name}: legacy translations must be a list"
            )
        for row in source_rows:
            target = queue_by_id.get(row.get("target_id"))
            if target is None:
                raise AuthoredCatalogError(
                    f"{shard_name}: unknown target {row.get('target_id')!r}"
                )
            translations.append(
                {
                    **row,
                    "source_text_sha256": sha256_bytes(
                        target["source_text"].encode("utf-8")
                    ),
                }
            )
        subsystem_counts = data.get("subsystem_counts")
        terminology_sources = []
    else:
        raise AuthoredCatalogError(
            f"{shard_name}: unsupported authored shard schema"
        )

    return {
        "kind": AUTHORED_SHARD_KIND,
        "locale": locale,
        "schema_version": AUTHORED_SHARD_SCHEMA_VERSION,
        "shard": shard_name,
        "source_map_sha256": source_map_sha256,
        "source_queue": dict(queue_source),
        "subsystem_counts": subsystem_counts,
        "target_count": len(translations) if isinstance(translations, list) else None,
        "terminology_sources": terminology_sources,
        "translations": translations,
    }


def normalize_authored_shard(
    data: Any,
    *,
    shard_name: str,
    queue_source: Mapping[str, str],
    source_map_sha256: str,
    queue_by_id: Mapping[str, Mapping[str, Any]],
) -> Dict[str, Any]:
    if not isinstance(data, dict):
        raise AuthoredCatalogError(f"{shard_name}: shard root must be an object")
    if set(data) == _SHARD_FIELDS:
        return dict(data)
    return _normalize_legacy_shard(
        data,
        shard_name=shard_name,
        queue_source=queue_source,
        source_map_sha256=source_map_sha256,
        queue_by_id=queue_by_id,
    )


def _structure(text: str) -> List[str]:
    return _TOKEN_OR_NEWLINE_RE.findall(text)


def _validate_translation_text(
    *,
    locale: str,
    target_id: str,
    source_text: str,
    translated_text: str,
    preserve_structure: bool = True,
) -> None:
    if not isinstance(translated_text, str) or not translated_text:
        raise AuthoredCatalogError(
            f"{locale} {target_id}: translation must be a non-empty string"
        )
    if "\0" in translated_text:
        raise AuthoredCatalogError(f"{locale} {target_id}: embedded NUL is forbidden")
    if preserve_structure and _structure(translated_text) != _structure(source_text):
        raise AuthoredCatalogError(
            f"{locale} {target_id}: controls, placeholders, or newlines drifted"
        )

    source_visible = _TOKEN_RE.sub("", source_text).strip()
    translated_visible = _TOKEN_RE.sub("", translated_text).strip()
    if source_visible and translated_visible.casefold() == source_visible.casefold():
        source_words = set(_ASCII_WORD_RE.findall(source_visible))
        if source_words - _ALLOWED_ASCII_TERMS:
            raise AuthoredCatalogError(
                f"{locale} {target_id}: untranslated English payload is forbidden"
            )
    unexpected = set(_ASCII_WORD_RE.findall(translated_visible)) - _ALLOWED_ASCII_TERMS
    if unexpected:
        raise AuthoredCatalogError(
            f"{locale} {target_id}: unexpected English prose remains: "
            + ", ".join(sorted(unexpected))
        )


def _expected_record(
    *,
    target_id: str,
    queue_by_id: Mapping[str, Mapping[str, Any]],
    existing_by_id: Mapping[str, Mapping[str, Any]],
    english_entries: Tuple[Any, ...],
) -> Dict[str, Any]:
    queued = queue_by_id.get(target_id)
    if queued is not None:
        return {
            "english_payload_sha256": queued["english_payload_sha256"],
            "key": queued["suggested_key"],
            "source_text": queued["source_text"],
            "source_text_sha256": sha256_bytes(
                queued["source_text"].encode("utf-8")
            ),
            "subsystem": queued["subsystem"],
        }

    existing = existing_by_id[target_id]
    english = english_entries[int(target_id, 16)]
    return {
        "english_payload_sha256": sha256_bytes(english.encoded_bytes),
        "key": existing["translation_key"],
        "source_text": english.source_text,
        "source_text_sha256": sha256_bytes(english.source_text.encode("utf-8")),
        "subsystem": existing["subsystem"],
    }


def build_authored_catalogs(
    repo_root: Path,
    *,
    manifest_path: Path = DEFAULT_MANIFEST_PATH,
) -> Dict[str, Dict[str, Any]]:
    repo_root = Path(repo_root)
    manifest_file = repo_root / manifest_path
    _, manifest = _load_json(manifest_file)
    if not isinstance(manifest, dict):
        raise AuthoredCatalogError(f"{manifest_file}: manifest root must be an object")
    if manifest.get("kind") != AUTHORED_MANIFEST_KIND:
        raise AuthoredCatalogError(f"{manifest_file}: invalid manifest kind")
    if manifest.get("schema_version") != AUTHORED_MANIFEST_SCHEMA_VERSION:
        raise AuthoredCatalogError(f"{manifest_file}: invalid manifest schema version")

    queue_source = manifest.get("source_queue")
    if not isinstance(queue_source, dict):
        raise AuthoredCatalogError(f"{manifest_file}: source_queue must be an object")
    if set(queue_source) != {"path", "revision", "sha256"}:
        raise AuthoredCatalogError(f"{manifest_file}: source_queue fields drifted")
    _require_digest(queue_source.get("sha256"), field="source_queue.sha256")
    queue_path = repo_root / queue_source["path"]
    queue_bytes, queue = _load_json(queue_path)
    if sha256_bytes(queue_bytes) != queue_source["sha256"]:
        raise AuthoredCatalogError(f"{queue_path}: source queue SHA-256 drift")
    if not isinstance(queue, dict) or not isinstance(queue.get("targets"), list):
        raise AuthoredCatalogError(f"{queue_path}: authored queue is malformed")
    queue_by_id = {row["target_id"]: row for row in queue["targets"]}
    if len(queue_by_id) != len(queue["targets"]):
        raise AuthoredCatalogError(f"{queue_path}: duplicate target ID")
    if len(set(row["suggested_key"] for row in queue["targets"])) != len(queue_by_id):
        raise AuthoredCatalogError(f"{queue_path}: duplicate suggested key")
    source_map_sha256 = queue.get("authoritative_target_map_sha256")
    _require_digest(source_map_sha256, field="authoritative_target_map_sha256")

    target_header = repo_root / "include/constants/msg.h"
    match = re.search(
        r"#define\s+MSG_COUNT\s+0x([0-9A-Fa-f]+)",
        target_header.read_text(encoding="utf-8"),
    )
    if match is None:
        raise AuthoredCatalogError(f"{target_header}: MSG_COUNT is missing")
    target_count = int(match.group(1), 16)
    mapping_path = repo_root / manifest.get(
        "mapping_path", "texts/locales/mapping/fe8u_target_map.json"
    )
    _, mapping_data = _load_json(mapping_path)
    mapping = validate_mapping_document(
        mapping_data, target_count=target_count, repo_root=repo_root
    )
    mapping_by_id = {
        f"0x{row.target_id:04X}": row for row in mapping.rows
    }

    existing_targets = manifest.get("existing_authored_targets")
    if not isinstance(existing_targets, list):
        raise AuthoredCatalogError(
            f"{manifest_file}: existing_authored_targets must be a list"
        )
    existing_by_id: Dict[str, Dict[str, Any]] = {}
    for record in existing_targets:
        if not isinstance(record, dict) or set(record) != {
            "subsystem",
            "target_id",
            "translation_key",
        }:
            raise AuthoredCatalogError(
                f"{manifest_file}: existing authored target schema drifted"
            )
        target_id = record["target_id"]
        if target_id in queue_by_id or target_id in existing_by_id:
            raise AuthoredCatalogError(
                f"{manifest_file}: duplicate/queued existing target {target_id}"
            )
        row = mapping_by_id.get(target_id)
        if row is None or row.source_kind != "authored":
            raise AuthoredCatalogError(
                f"{target_id}: existing authored target is not mapped as authored"
            )
        if row.source["translation_key"] != record["translation_key"]:
            raise AuthoredCatalogError(
                f"{target_id}: existing authored translation key drifted"
            )
        if row.verification["subsystem"] != record["subsystem"]:
            raise AuthoredCatalogError(
                f"{target_id}: existing authored subsystem drifted"
            )
        existing_by_id[target_id] = dict(record)

    english_entries = load_english_source_entries(
        repo_root / "texts/texts.txt",
        repo_root / "texts/textdefs.txt",
        target_count=target_count,
    )
    expected_ids = set(queue_by_id) | set(existing_by_id)
    expected_keys = {
        _expected_record(
            target_id=target_id,
            queue_by_id=queue_by_id,
            existing_by_id=existing_by_id,
            english_entries=english_entries,
        )["key"]
        for target_id in expected_ids
    }

    locale_manifest = manifest.get("locales")
    if not isinstance(locale_manifest, dict) or tuple(sorted(locale_manifest)) != tuple(
        sorted(LOCALES)
    ):
        raise AuthoredCatalogError(
            f"{manifest_file}: locales must be exactly {LOCALES!r}"
        )

    catalogs: Dict[str, Dict[str, Any]] = {}
    locale_records: Dict[str, Dict[str, Dict[str, Any]]] = {}
    for locale in LOCALES:
        locale_data = locale_manifest[locale]
        if not isinstance(locale_data, dict) or set(locale_data) != {
            "catalog_path",
            "shards",
        }:
            raise AuthoredCatalogError(
                f"{manifest_file}: {locale} manifest schema drifted"
            )
        shard_specs = locale_data["shards"]
        if not isinstance(shard_specs, list) or not shard_specs:
            raise AuthoredCatalogError(f"{manifest_file}: {locale} shards are missing")

        records: Dict[str, Dict[str, Any]] = {}
        catalog_shards = []
        for spec in shard_specs:
            if not isinstance(spec, dict) or set(spec) != {
                "path",
                "sha256",
                "target_count",
            }:
                raise AuthoredCatalogError(
                    f"{manifest_file}: {locale} shard pin schema drifted"
                )
            _require_digest(spec["sha256"], field=f"{locale} shard sha256")
            shard_path = repo_root / spec["path"]
            shard_bytes, shard_data = _load_json(shard_path)
            if sha256_bytes(shard_bytes) != spec["sha256"]:
                raise AuthoredCatalogError(f"{shard_path}: shard SHA-256 drift")
            shard_name = shard_path.name[: -len(f".{locale}.json")]
            shard = normalize_authored_shard(
                shard_data,
                shard_name=shard_name,
                queue_source=queue_source,
                source_map_sha256=source_map_sha256,
                queue_by_id=queue_by_id,
            )
            if set(shard) != _SHARD_FIELDS:
                raise AuthoredCatalogError(f"{shard_path}: shard fields drifted")
            if shard != shard_data:
                raise AuthoredCatalogError(
                    f"{shard_path}: legacy shard must be normalized before commit"
                )
            if shard["kind"] != AUTHORED_SHARD_KIND:
                raise AuthoredCatalogError(f"{shard_path}: invalid shard kind")
            if shard["schema_version"] != AUTHORED_SHARD_SCHEMA_VERSION:
                raise AuthoredCatalogError(f"{shard_path}: invalid schema version")
            if shard["locale"] != locale or shard["shard"] != shard_name:
                raise AuthoredCatalogError(f"{shard_path}: locale/shard identity drift")
            if shard["source_queue"] != queue_source:
                raise AuthoredCatalogError(f"{shard_path}: source queue pin drift")
            if shard["source_map_sha256"] != source_map_sha256:
                raise AuthoredCatalogError(f"{shard_path}: source map pin drift")
            translations = shard["translations"]
            if not isinstance(translations, list):
                raise AuthoredCatalogError(f"{shard_path}: translations must be a list")
            if shard["target_count"] != len(translations):
                raise AuthoredCatalogError(f"{shard_path}: target_count drift")
            if spec["target_count"] != len(translations):
                raise AuthoredCatalogError(f"{shard_path}: manifest target_count drift")
            subsystem_counts = Counter()
            for translation in translations:
                if not isinstance(translation, dict) or set(translation) != _TRANSLATION_FIELDS:
                    raise AuthoredCatalogError(
                        f"{shard_path}: translation schema drifted"
                    )
                target_id = translation["target_id"]
                if target_id not in expected_ids:
                    raise AuthoredCatalogError(
                        f"{shard_path}: unexpected target {target_id}"
                    )
                if target_id in records:
                    raise AuthoredCatalogError(
                        f"{locale}: target {target_id} appears in multiple shards"
                    )
                expected = _expected_record(
                    target_id=target_id,
                    queue_by_id=queue_by_id,
                    existing_by_id=existing_by_id,
                    english_entries=english_entries,
                )
                for field in (
                    "english_payload_sha256",
                    "key",
                    "source_text_sha256",
                    "subsystem",
                ):
                    if translation[field] != expected[field]:
                        raise AuthoredCatalogError(
                            f"{shard_path}: {target_id} {field} drifted"
                        )
                _validate_translation_text(
                    locale=locale,
                    target_id=target_id,
                    source_text=expected["source_text"],
                    translated_text=translation["text"],
                    preserve_structure=target_id in queue_by_id,
                )
                subsystem_counts[translation["subsystem"]] += 1
                records[target_id] = translation
            if dict(sorted(subsystem_counts.items())) != shard["subsystem_counts"]:
                raise AuthoredCatalogError(f"{shard_path}: subsystem_counts drift")
            catalog_shards.append(dict(spec))

        if set(records) != expected_ids:
            missing = sorted(expected_ids - set(records))
            extra = sorted(set(records) - expected_ids)
            raise AuthoredCatalogError(
                f"{locale}: authored target union mismatch; missing={missing} extra={extra}"
            )
        strings = {
            record["key"]: record["text"]
            for _, record in sorted(records.items())
        }
        if set(strings) != expected_keys or len(strings) != len(records):
            raise AuthoredCatalogError(
                f"{locale}: missing, extra, or duplicate authored translation keys"
            )
        catalogs[locale] = {
            "kind": AUTHORED_CATALOG_KIND,
            "locale": locale,
            "schema_version": AUTHORED_CATALOG_SCHEMA_VERSION,
            "shards": catalog_shards,
            "source_queue": dict(queue_source),
            "strings": dict(sorted(strings.items())),
            "target_count": len(records),
        }
        locale_records[locale] = records

    for target_id in sorted(expected_ids):
        if locale_records["ja"][target_id]["key"] != locale_records["zh-Hans"][
            target_id
        ]["key"]:
            raise AuthoredCatalogError(f"{target_id}: locale key parity drift")
    return catalogs


def write_authored_catalogs(
    repo_root: Path,
    *,
    manifest_path: Path = DEFAULT_MANIFEST_PATH,
) -> Dict[str, Path]:
    repo_root = Path(repo_root)
    catalogs = build_authored_catalogs(repo_root, manifest_path=manifest_path)
    _, manifest = _load_json(repo_root / manifest_path)
    written = {}
    for locale, catalog in catalogs.items():
        path = repo_root / manifest["locales"][locale]["catalog_path"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(canonical_json_bytes(catalog))
        written[locale] = path
    return written


def check_authored_catalogs(
    repo_root: Path,
    *,
    manifest_path: Path = DEFAULT_MANIFEST_PATH,
) -> Dict[str, Path]:
    repo_root = Path(repo_root)
    catalogs = build_authored_catalogs(repo_root, manifest_path=manifest_path)
    _, manifest = _load_json(repo_root / manifest_path)
    checked = {}
    mismatches = []
    for locale, catalog in catalogs.items():
        path = repo_root / manifest["locales"][locale]["catalog_path"]
        expected = canonical_json_bytes(catalog)
        if not path.is_file() or path.read_bytes() != expected:
            mismatches.append(path.as_posix())
        checked[locale] = path
    if mismatches:
        raise AuthoredCatalogError(
            "authored catalogs differ from deterministic shard merge: "
            + ", ".join(mismatches)
        )
    return checked
