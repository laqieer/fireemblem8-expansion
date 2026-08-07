"""Audit final materialized locale payloads for untranslated Latin copies."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Sequence, Tuple

from .model import GameCatalogBuild, GameCatalogError

LEAKAGE_SCHEMA_VERSION = 1
LEAKAGE_KIND = "runtime-locale-english-leakage-audit"
ALLOWLIST_SCHEMA_VERSION = 1
ALLOWLIST_KIND = "runtime-locale-latin-payload-allowlist"
DEFAULT_ALLOWLIST_PATH = Path("texts/locales/runtime_latin_allowlist.json")
DEFAULT_RAW_CLOSURE_PATH = Path(
    "texts/locales/mapping/raw_surface_closure.json"
)
DEFAULT_REPORT_PATH = Path(
    "texts/locales/mapping/runtime_english_leakage.json"
)
OUTPUT_REPORT_NAME = "game_localization_leakage_report.json"

_BRACKET_TOKEN_RE = re.compile(r"\[[^\[\]\r\n]+\]")
_LATIN_RE = re.compile(r"[A-Za-z]")
_LOCALIZED_SCRIPT_RE = re.compile(
    r"[\u3040-\u30FF\u3400-\u9FFF\uF900-\uFAFF]"
)
_ALLOWLIST_KEY_RE = re.compile(
    r"(game)/(ja|zh-Hans)/(0x[0-9A-F]{4})"
    r"|(raw)/(ja|zh-Hans)/(fe8cn\.raw\.import-[0-9]{4})"
)
_ALLOWED_CATEGORIES = frozenset(
    (
        "locale-neutral-abbreviation",
        "locale-neutral-build-identity",
        "locale-neutral-currency-code",
        "locale-neutral-player-code",
        "locale-neutral-rank-code",
        "locale-neutral-state-code",
    )
)


@dataclass(frozen=True)
class Approval:
    key: str
    payload: str
    category: str
    reason: str


@dataclass(frozen=True)
class Allowlist:
    path: str
    sha256: str
    byte_count: int
    approvals: Mapping[str, Approval]


def canonical_json_bytes(data: Any) -> bytes:
    return (
        json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _load_json(path: Path) -> Tuple[Any, bytes]:
    path = Path(path)
    data = path.read_bytes()
    try:
        return json.loads(data.decode("utf-8")), data
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise GameCatalogError(f"{path}: expected valid UTF-8 JSON") from error


def load_allowlist(path: Path = DEFAULT_ALLOWLIST_PATH) -> Allowlist:
    path = Path(path)
    data, data_bytes = _load_json(path)
    if not isinstance(data, dict):
        raise GameCatalogError(f"{path}: allowlist root must be an object")
    if data.get("schema_version") != ALLOWLIST_SCHEMA_VERSION:
        raise GameCatalogError(
            f"{path}: allowlist schema_version must be {ALLOWLIST_SCHEMA_VERSION}"
        )
    if data.get("kind") != ALLOWLIST_KIND:
        raise GameCatalogError(f"{path}: allowlist kind must be {ALLOWLIST_KIND!r}")
    raw_approvals = data.get("approvals")
    if not isinstance(raw_approvals, dict):
        raise GameCatalogError(f"{path}: approvals must be an object keyed per payload")

    approvals: Dict[str, Approval] = {}
    for key, raw in sorted(raw_approvals.items()):
        if not isinstance(key, str) or not _ALLOWLIST_KEY_RE.fullmatch(key):
            raise GameCatalogError(
                f"{path}: approval key {key!r} must identify one exact game/raw payload"
            )
        if not isinstance(raw, dict):
            raise GameCatalogError(f"{path}: approval {key!r} must be an object")
        payload = raw.get("payload")
        category = raw.get("category")
        reason = raw.get("reason")
        if not isinstance(payload, str) or not payload:
            raise GameCatalogError(f"{path}: approval {key!r} payload must be non-empty")
        if category not in _ALLOWED_CATEGORIES:
            raise GameCatalogError(
                f"{path}: approval {key!r} category must be an explicit "
                "locale-neutral class"
            )
        if not isinstance(reason, str) or not reason.strip():
            raise GameCatalogError(f"{path}: approval {key!r} reason must be factual")
        approvals[key] = Approval(
            key=key,
            payload=payload,
            category=category,
            reason=reason,
        )
    return Allowlist(
        path=path.as_posix(),
        sha256=sha256_bytes(data_bytes),
        byte_count=len(data_bytes),
        approvals=approvals,
    )


def _visible_text(text: str) -> str:
    without_tokens = _BRACKET_TOKEN_RE.sub("", text)
    normalized = unicodedata.normalize("NFKC", without_tokens)
    return " ".join(normalized.split()).strip()


def _copy_key(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", _visible_text(text).casefold())


def _ascii_letter_count(text: str) -> int:
    return sum(character.isascii() and character.isalpha() for character in text)


def _classify_candidate(payload: str, english: str) -> Tuple[Tuple[str, ...], float]:
    visible = _visible_text(payload)
    english_visible = _visible_text(english)
    if not _LATIN_RE.search(visible):
        return (), 0.0

    classifications = []
    payload_key = _copy_key(payload)
    english_key = _copy_key(english)
    similarity = 0.0
    if payload_key and english_key and payload_key == english_key:
        classifications.append("exact-english-copy")
        similarity = 1.0
    elif (
        _ascii_letter_count(visible) >= 4
        and _ascii_letter_count(english_visible) >= 4
        and payload_key
        and english_key
    ):
        similarity = SequenceMatcher(None, payload_key, english_key).ratio()
        if similarity >= 0.80:
            classifications.append("near-english-copy")

    if not _LOCALIZED_SCRIPT_RE.search(visible):
        classifications.append("latin-only-payload")
    return tuple(classifications), similarity


def _audit_entries(
    entries: Iterable[Mapping[str, Any]],
    *,
    locale: str,
    scope: str,
    allowlist: Allowlist,
) -> Tuple[Dict[str, Any], Tuple[str, ...]]:
    candidates = []
    used_approvals = []
    exact_count = 0
    near_count = 0
    latin_only_count = 0
    audited_count = 0
    for entry in entries:
        audited_count += 1
        payload = entry["payload"]
        english = entry["english"]
        classifications, similarity = _classify_candidate(payload, english)
        if not classifications:
            continue
        exact_count += "exact-english-copy" in classifications
        near_count += "near-english-copy" in classifications
        latin_only_count += "latin-only-payload" in classifications
        approval_key = f"{scope}/{locale}/{entry['id']}"
        approval = allowlist.approvals.get(approval_key)
        approved = approval is not None and approval.payload == payload
        candidate = {
            "approval_key": approval_key,
            "approved": approved,
            "classifications": list(classifications),
            "english_visible": _visible_text(english),
            "id": entry["id"],
            "payload": payload,
            "payload_visible": _visible_text(payload),
            "similarity": round(similarity, 6),
            "user_facing": entry["user_facing"],
        }
        if "metadata" in entry:
            candidate["metadata"] = entry["metadata"]
        if approval is not None:
            candidate["approval"] = {
                "category": approval.category,
                "payload_matches": approval.payload == payload,
                "reason": approval.reason,
            }
        if approved:
            used_approvals.append(approval_key)
        candidates.append(candidate)

    return (
        {
            "audited_count": audited_count,
            "candidate_count": len(candidates),
            "exact_copy_count": exact_count,
            "near_copy_count": near_count,
            "latin_only_count": latin_only_count,
            "approved_count": sum(candidate["approved"] for candidate in candidates),
            "unapproved_count": sum(
                not candidate["approved"] for candidate in candidates
            ),
            "candidates": candidates,
        },
        tuple(used_approvals),
    )


def _game_entries(build: GameCatalogBuild, locale: str) -> Iterable[Dict[str, Any]]:
    english_by_id = {entry.target_id: entry for entry in build.english.entries}
    for entry in build.locale_bundle(locale).entries:
        if not entry.present or entry.source_text is None:
            raise GameCatalogError(
                f"{locale}: target 0x{entry.target_id:04X} is not materialized"
            )
        yield {
            "english": english_by_id[entry.target_id].source_text,
            "id": f"0x{entry.target_id:04X}",
            "metadata": {
                "mapping_source": entry.mapping_source,
                "mapping_source_kind": entry.mapping_source_kind,
                "provider_kind": entry.locale_provider_kind,
            },
            "payload": entry.source_text,
            "user_facing": True,
        }


def _raw_entries(
    build: GameCatalogBuild,
    locale: str,
    *,
    raw_closure: Mapping[str, Any],
    expansion_catalogs: Mapping[str, Mapping[str, str]],
) -> Iterable[Dict[str, Any]]:
    locale_by_id = {
        entry.target_id: entry for entry in build.locale_bundle(locale).entries
    }
    english_by_id = {entry.target_id: entry for entry in build.english.entries}
    for row in raw_closure["rows"]:
        if row["classification"] == "game_message":
            target_ids = row.get("target_ids")
            if not isinstance(target_ids, list) or not target_ids:
                raise GameCatalogError(
                    f"{row['import_id']}: raw closure target_ids are missing"
                )
            numeric_ids = [int(target_id, 16) for target_id in target_ids]
            payloads = []
            for target_id in numeric_ids:
                entry = locale_by_id[target_id]
                if not entry.present or entry.source_text is None:
                    raise GameCatalogError(
                        f"{row['import_id']}:{locale}: target is not materialized"
                    )
                payloads.append(entry.source_text)
            if len(set(payloads)) != 1:
                raise GameCatalogError(
                    f"{row['import_id']}:{locale}: mapped targets disagree"
                )
            payload = payloads[0]
            english = english_by_id[numeric_ids[0]].source_text
            metadata = {"target_ids": target_ids}
        elif row["classification"] == "expansion_message":
            key = row.get("expansion_key")
            if not isinstance(key, str) or not key:
                raise GameCatalogError(
                    f"{row['import_id']}: expansion_key is missing"
                )
            payload = expansion_catalogs[locale][key]
            english = expansion_catalogs["en"][key]
            metadata = {"expansion_key": key}
        else:
            raise GameCatalogError(
                f"{row['import_id']}: unsupported raw closure classification"
            )

        expected_hash = row["providers"][locale]["text_sha256"]
        actual_hash = sha256_bytes(payload.encode("utf-8"))
        if actual_hash != expected_hash:
            raise GameCatalogError(
                f"{row['import_id']}:{locale}: materialized payload hash differs "
                "from raw closure"
            )
        yield {
            "english": english,
            "id": row["import_id"],
            "metadata": metadata,
            "payload": payload,
            "user_facing": row["user_facing"],
        }


def _validate_raw_closure(raw_closure: Any) -> Mapping[str, Any]:
    if not isinstance(raw_closure, dict):
        raise GameCatalogError("raw closure root must be an object")
    summary = raw_closure.get("summary")
    rows = raw_closure.get("rows")
    if not isinstance(summary, dict) or not isinstance(rows, list):
        raise GameCatalogError("raw closure summary/rows are missing")
    if summary.get("total_count") != 143 or len(rows) != 143:
        raise GameCatalogError("raw closure must contain exactly 143 rows")
    for locale, key in (
        ("ja", "ja_materialized_count"),
        ("zh-Hans", "zh_hans_materialized_count"),
    ):
        if summary.get(key) != 143:
            raise GameCatalogError(
                f"raw closure must materialize all 143 {locale} payloads"
            )
    if any(
        summary.get(key) != 0
        for key in (
            "diagnostic_exclusion_count",
            "english_fallback_count",
            "non_user_facing_exclusion_count",
            "unresolved_count",
        )
    ):
        raise GameCatalogError("raw closure contains fallback, exclusion, or unresolved rows")
    return raw_closure


def build_leakage_report(
    build: GameCatalogBuild,
    *,
    allowlist: Allowlist,
    raw_closure: Mapping[str, Any],
    expansion_catalogs: Mapping[str, Mapping[str, str]],
    inputs: Mapping[str, Mapping[str, Any]],
) -> Dict[str, Any]:
    raw_closure = _validate_raw_closure(raw_closure)
    game_reports = {}
    raw_reports = {}
    used_approvals = set()
    for locale in build.enabled_locales:
        game_report, game_used = _audit_entries(
            _game_entries(build, locale),
            locale=locale,
            scope="game",
            allowlist=allowlist,
        )
        raw_report, raw_used = _audit_entries(
            _raw_entries(
                build,
                locale,
                raw_closure=raw_closure,
                expansion_catalogs=expansion_catalogs,
            ),
            locale=locale,
            scope="raw",
            allowlist=allowlist,
        )
        game_reports[locale] = game_report
        raw_reports[locale] = raw_report
        used_approvals.update(game_used)
        used_approvals.update(raw_used)

    relevant_approvals = {
        key
        for key in allowlist.approvals
        if any(f"/{locale}/" in key for locale in build.enabled_locales)
    }
    stale_approvals = sorted(relevant_approvals - used_approvals)
    unapproved = sum(
        report["unapproved_count"]
        for report in (*game_reports.values(), *raw_reports.values())
    )
    report = {
        "schema_version": LEAKAGE_SCHEMA_VERSION,
        "kind": LEAKAGE_KIND,
        "policy": {
            "allowlist_is_explicit_per_payload": True,
            "broad_regex_whitelist": False,
            "exact_copy_detection": "NFKC/casefold/alphanumeric equality",
            "near_copy_detection": "SequenceMatcher >= 0.80 with >=4 Latin letters",
            "latin_only_detection": "Latin present and no Han/kana in visible payload",
        },
        "inputs": {
            **dict(sorted(inputs.items())),
            "allowlist": {
                "path": allowlist.path,
                "sha256": allowlist.sha256,
                "byte_count": allowlist.byte_count,
            },
        },
        "game_catalog": {
            "target_count": build.target_count,
            "mapping_source_counts": {
                **dict(build.mapping_source_counts),
                "unresolved": 0,
            },
            "locales": game_reports,
        },
        "raw_surface": {
            "provider_count": 143,
            "locales": raw_reports,
        },
        "summary": {
            "enabled_locales": list(build.enabled_locales),
            "game_payload_count": sum(
                report["audited_count"] for report in game_reports.values()
            ),
            "raw_surface_payload_count": sum(
                report["audited_count"] for report in raw_reports.values()
            ),
            "approved_candidate_count": len(used_approvals),
            "unapproved_candidate_count": unapproved,
            "stale_approval_count": len(stale_approvals),
            "stale_approvals": stale_approvals,
        },
    }
    if unapproved or stale_approvals:
        problems = []
        if unapproved:
            problems.append(f"{unapproved} unapproved Latin/English payload(s)")
        if stale_approvals:
            problems.append(f"{len(stale_approvals)} stale approval(s)")
        raise GameCatalogError("runtime locale leakage gate failed: " + ", ".join(problems))
    return report


def input_record(path: Path, *, repo_root: Path = Path(".")) -> Dict[str, Any]:
    path = Path(path)
    data = path.read_bytes()
    try:
        logical_path = path.relative_to(repo_root).as_posix()
    except ValueError:
        logical_path = path.as_posix()
    return {
        "path": logical_path,
        "sha256": sha256_bytes(data),
        "byte_count": len(data),
    }


def load_expansion_catalogs(
    catalog_root: Path,
    locales: Sequence[str],
) -> Dict[str, Dict[str, str]]:
    catalogs = {}
    for locale in ("en", *locales):
        path = Path(catalog_root) / f"catalog.{locale}.json"
        data, _ = _load_json(path)
        strings = data.get("strings") if isinstance(data, dict) else None
        if not isinstance(strings, dict) or any(
            not isinstance(key, str) or not isinstance(value, str)
            for key, value in strings.items()
        ):
            raise GameCatalogError(f"{path}: expansion strings are malformed")
        catalogs[locale] = strings
    return catalogs


def load_raw_closure(path: Path = DEFAULT_RAW_CLOSURE_PATH) -> Mapping[str, Any]:
    data, _ = _load_json(path)
    return _validate_raw_closure(data)
