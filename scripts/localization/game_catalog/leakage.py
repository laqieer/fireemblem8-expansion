"""Audit final materialized locale payloads for untranslated Latin copies."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Optional, Sequence, Tuple

from .model import GameCatalogBuild, GameCatalogError

LEAKAGE_SCHEMA_VERSION = 2
LEAKAGE_KIND = "runtime-locale-latin-span-audit"
REVIEW_SCHEMA_VERSION = 1
REVIEW_KIND = "runtime-locale-latin-span-review"
DEFAULT_REVIEW_PATH = Path("texts/locales/runtime_latin_span_review.json")
DEFAULT_RAW_CLOSURE_PATH = Path(
    "texts/locales/mapping/raw_surface_closure.json"
)
DEFAULT_REPORT_PATH = Path(
    "texts/locales/mapping/runtime_english_leakage.json"
)
OUTPUT_REPORT_NAME = "game_localization_latin_span_audit.json"

_BRACKET_TOKEN_RE = re.compile(r"\[[^\[\]\r\n]+\]")
_LATIN_RE = re.compile(r"[A-Za-z]")
_LATIN_SPAN_RE = re.compile(r"[A-Za-z]+")
_LOCALIZED_SCRIPT_RE = re.compile(
    r"[\u3040-\u30FF\u3400-\u9FFF\uF900-\uFAFF]"
)
_REVIEW_KEY_RE = re.compile(
    r"(game)/(ja|zh-Hans)/(0x[0-9A-F]{4})"
    r"|(raw)/(ja|zh-Hans)/(fe8cn\.raw\.import-[0-9]{4})"
)
_COMMIT_RE = re.compile(r"[0-9a-f]{40}")


@dataclass(frozen=True)
class SpanDecision:
    key: str
    span: str
    baseline_occurrences: int
    current_occurrences: int
    decision: str
    reason: str
    source: str
    category: Optional[str]


@dataclass(frozen=True)
class TargetReview:
    key: str
    baseline_classification: str
    baseline_payload_sha256: str
    current_payload_sha256: str
    english_payload_sha256: str
    payload_source: Mapping[str, Any]
    spans: Mapping[str, SpanDecision]


@dataclass(frozen=True)
class ReviewCatalog:
    path: str
    sha256: str
    byte_count: int
    baseline_commit: str
    summary: Mapping[str, Any]
    reviews: Mapping[str, TargetReview]


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


def _latin_span_counts(text: str) -> Counter:
    return Counter(_LATIN_SPAN_RE.findall(_visible_text(text)))


def _span_decision_key(target_key: str, span: str) -> str:
    return f"{target_key}#{span}"


def load_review(path: Path = DEFAULT_REVIEW_PATH) -> ReviewCatalog:
    path = Path(path)
    data, data_bytes = _load_json(path)
    if not isinstance(data, dict):
        raise GameCatalogError(f"{path}: review root must be an object")
    if data.get("schema_version") != REVIEW_SCHEMA_VERSION:
        raise GameCatalogError(
            f"{path}: review schema_version must be {REVIEW_SCHEMA_VERSION}"
        )
    if data.get("kind") != REVIEW_KIND:
        raise GameCatalogError(f"{path}: review kind must be {REVIEW_KIND!r}")
    baseline_commit = data.get("baseline_commit")
    if not isinstance(baseline_commit, str) or not _COMMIT_RE.fullmatch(
        baseline_commit
    ):
        raise GameCatalogError(f"{path}: baseline_commit must be a full Git SHA")
    policy = data.get("policy")
    if not isinstance(policy, dict) or policy.get(
        "broad_category_or_regex_exemption"
    ) is not False:
        raise GameCatalogError(
            f"{path}: broad category or regex exemptions must be disabled"
        )
    raw_reviews = data.get("reviews")
    if not isinstance(raw_reviews, dict):
        raise GameCatalogError(f"{path}: reviews must be an object keyed per target")

    reviews: Dict[str, TargetReview] = {}
    computed_summary: Dict[str, Dict[str, int]] = {
        locale: {
            "approved_span_count": 0,
            "game_latin_bearing_payload_count": 0,
            "localized_span_count": 0,
            "mixed_script_bypass_payload_count": 0,
            "raw_latin_bearing_payload_count": 0,
        }
        for locale in ("ja", "zh-Hans")
    }
    for key, raw in sorted(raw_reviews.items()):
        if not isinstance(key, str) or not _REVIEW_KEY_RE.fullmatch(key):
            raise GameCatalogError(
                f"{path}: review key {key!r} must identify one exact game/raw target"
            )
        if not isinstance(raw, dict):
            raise GameCatalogError(f"{path}: review {key!r} must be an object")
        scope, locale, target = key.split("/")
        if (
            raw.get("scope") != scope
            or raw.get("locale") != locale
            or raw.get("target") != target
        ):
            raise GameCatalogError(
                f"{path}: review {key!r} must repeat its exact scope, locale, "
                "and target"
            )
        baseline_payload_sha256 = raw.get("baseline_payload_sha256")
        current_payload_sha256 = raw.get("current_payload_sha256")
        english_payload_sha256 = raw.get("english_payload_sha256")
        baseline_classification = raw.get("baseline_classification")
        payload_source = raw.get("payload_source")
        raw_spans = raw.get("spans")
        if any(
            not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value)
            for value in (
                baseline_payload_sha256,
                current_payload_sha256,
                english_payload_sha256,
            )
        ):
            raise GameCatalogError(
                f"{path}: review {key!r} payload hashes must be SHA-256 values"
            )
        if baseline_classification not in (
            "existing-payload-gate-candidate",
            "mixed-script-bypass",
        ):
            raise GameCatalogError(
                f"{path}: review {key!r} has invalid baseline_classification"
            )
        if not isinstance(payload_source, dict) or not payload_source:
            raise GameCatalogError(
                f"{path}: review {key!r} payload_source must be an object"
            )
        if not isinstance(raw_spans, dict) or not raw_spans:
            raise GameCatalogError(
                f"{path}: review {key!r} spans must be a non-empty object"
            )

        spans: Dict[str, SpanDecision] = {}
        for span, span_raw in sorted(raw_spans.items()):
            if (
                not isinstance(span, str)
                or not _LATIN_SPAN_RE.fullmatch(span)
                or not isinstance(span_raw, dict)
            ):
                raise GameCatalogError(
                    f"{path}: review {key!r} has malformed Latin span {span!r}"
                )
            baseline_occurrences = span_raw.get("baseline_occurrences")
            current_occurrences = span_raw.get("current_occurrences")
            decision = span_raw.get("decision")
            reason = span_raw.get("reason")
            source = span_raw.get("source")
            category = span_raw.get("category")
            if (
                not isinstance(baseline_occurrences, int)
                or baseline_occurrences < 1
                or not isinstance(current_occurrences, int)
                or current_occurrences < 0
            ):
                raise GameCatalogError(
                    f"{path}: review {key!r} span {span!r} occurrence counts "
                    "must be non-negative integers"
                )
            if decision not in ("approved", "localized"):
                raise GameCatalogError(
                    f"{path}: review {key!r} span {span!r} has invalid decision"
                )
            if not isinstance(reason, str) or not reason.strip():
                raise GameCatalogError(
                    f"{path}: review {key!r} span {span!r} needs a factual reason"
                )
            if not isinstance(source, str) or not source.strip():
                raise GameCatalogError(
                    f"{path}: review {key!r} span {span!r} needs a factual source"
                )
            if decision == "approved":
                if (
                    current_occurrences < 1
                    or not isinstance(category, str)
                    or not category.strip()
                ):
                    raise GameCatalogError(
                        f"{path}: approved span {key}#{span} must remain exact "
                        "and have a category"
                    )
            elif current_occurrences != 0 or category is not None:
                raise GameCatalogError(
                    f"{path}: localized span {key}#{span} must be absent "
                    "from the pinned current payload"
                )
            spans[span] = SpanDecision(
                key=_span_decision_key(key, span),
                span=span,
                baseline_occurrences=baseline_occurrences,
                current_occurrences=current_occurrences,
                decision=decision,
                reason=reason,
                source=source,
                category=category,
            )

        reviews[key] = TargetReview(
            key=key,
            baseline_classification=baseline_classification,
            baseline_payload_sha256=baseline_payload_sha256,
            current_payload_sha256=current_payload_sha256,
            english_payload_sha256=english_payload_sha256,
            payload_source=payload_source,
            spans=spans,
        )
        locale_summary = computed_summary[locale]
        if scope == "game":
            locale_summary["game_latin_bearing_payload_count"] += 1
            locale_summary["mixed_script_bypass_payload_count"] += (
                baseline_classification == "mixed-script-bypass"
            )
        else:
            locale_summary["raw_latin_bearing_payload_count"] += 1
        locale_summary["approved_span_count"] += sum(
            decision.decision == "approved" for decision in spans.values()
        )
        locale_summary["localized_span_count"] += sum(
            decision.decision == "localized" for decision in spans.values()
        )

    summary = data.get("summary")
    if summary != computed_summary:
        raise GameCatalogError(f"{path}: review summary differs from exact rows")
    return ReviewCatalog(
        path=path.as_posix(),
        sha256=sha256_bytes(data_bytes),
        byte_count=len(data_bytes),
        baseline_commit=baseline_commit,
        summary=summary,
        reviews=reviews,
    )


def _visible_text(text: str) -> str:
    without_tokens = _BRACKET_TOKEN_RE.sub(" ", text)
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
    review: ReviewCatalog,
) -> Tuple[Dict[str, Any], Tuple[str, ...]]:
    latin_payloads = []
    used_decisions = []
    exact_count = 0
    near_count = 0
    latin_only_count = 0
    audited_count = 0
    latin_span_count = 0
    latin_span_occurrence_count = 0
    approved_span_count = 0
    approved_span_occurrence_count = 0
    unapproved_span_count = 0
    unapproved_span_occurrence_count = 0
    payload_mismatch_count = 0
    for entry in entries:
        audited_count += 1
        payload = entry["payload"]
        english = entry["english"]
        classifications, similarity = _classify_candidate(payload, english)
        exact_count += "exact-english-copy" in classifications
        near_count += "near-english-copy" in classifications
        latin_only_count += "latin-only-payload" in classifications
        target_key = f"{scope}/{locale}/{entry['id']}"
        target_review = review.reviews.get(target_key)
        payload_matches = (
            target_review is not None
            and target_review.current_payload_sha256
            == sha256_bytes(payload.encode("utf-8"))
            and target_review.english_payload_sha256
            == sha256_bytes(english.encode("utf-8"))
        )
        span_counts = _latin_span_counts(payload)
        if target_review is not None and not payload_matches:
            payload_mismatch_count += 1
        elif target_review is not None:
            for decision in target_review.spans.values():
                if (
                    decision.decision == "approved"
                    and span_counts.get(decision.span, 0)
                    == decision.current_occurrences
                ) or (
                    decision.decision == "localized"
                    and span_counts.get(decision.span, 0) == 0
                ):
                    used_decisions.append(decision.key)
        if not span_counts:
            continue
        spans = []
        latin_span_count += len(span_counts)
        latin_span_occurrence_count += sum(span_counts.values())
        for span, occurrences in sorted(span_counts.items()):
            decision = (
                target_review.spans.get(span)
                if target_review is not None
                else None
            )
            approved = (
                payload_matches
                and decision is not None
                and decision.decision == "approved"
                and decision.current_occurrences == occurrences
            )
            if approved:
                approved_span_count += 1
                approved_span_occurrence_count += occurrences
            else:
                unapproved_span_count += 1
                unapproved_span_occurrence_count += occurrences
            span_row = {
                "approved": approved,
                "occurrences": occurrences,
                "span": span,
            }
            if decision is not None:
                span_row["review"] = {
                    "category": decision.category,
                    "decision": decision.decision,
                    "reason": decision.reason,
                    "source": decision.source,
                }
            spans.append(span_row)

        candidate = {
            "classifications": list(classifications),
            "english_sha256": sha256_bytes(english.encode("utf-8")),
            "id": entry["id"],
            "payload_matches_review": payload_matches,
            "payload_sha256": sha256_bytes(payload.encode("utf-8")),
            "review_key": target_key,
            "similarity": round(similarity, 6),
            "spans": spans,
            "user_facing": entry["user_facing"],
        }
        if "metadata" in entry:
            candidate["metadata"] = entry["metadata"]
        latin_payloads.append(candidate)

    return (
        {
            "audited_count": audited_count,
            "latin_bearing_payload_count": len(latin_payloads),
            "latin_span_count": latin_span_count,
            "latin_span_occurrence_count": latin_span_occurrence_count,
            "exact_copy_count": exact_count,
            "near_copy_count": near_count,
            "latin_only_count": latin_only_count,
            "approved_span_count": approved_span_count,
            "approved_span_occurrence_count": approved_span_occurrence_count,
            "unapproved_span_count": unapproved_span_count,
            "unapproved_span_occurrence_count": unapproved_span_occurrence_count,
            "payload_mismatch_count": payload_mismatch_count,
            "latin_bearing_payloads": latin_payloads,
        },
        tuple(used_decisions),
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
    review: ReviewCatalog,
    raw_closure: Mapping[str, Any],
    expansion_catalogs: Mapping[str, Mapping[str, str]],
    inputs: Mapping[str, Mapping[str, Any]],
) -> Dict[str, Any]:
    raw_closure = _validate_raw_closure(raw_closure)
    game_reports = {}
    raw_reports = {}
    used_decisions = set()
    for locale in build.enabled_locales:
        game_report, game_used = _audit_entries(
            _game_entries(build, locale),
            locale=locale,
            scope="game",
            review=review,
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
            review=review,
        )
        game_reports[locale] = game_report
        raw_reports[locale] = raw_report
        used_decisions.update(game_used)
        used_decisions.update(raw_used)

    relevant_decisions = {
        decision.key
        for target in review.reviews.values()
        if any(f"/{locale}/" in target.key for locale in build.enabled_locales)
        for decision in target.spans.values()
    }
    stale_decisions = sorted(relevant_decisions - used_decisions)
    unapproved_spans = sum(
        report["unapproved_span_count"]
        for report in (*game_reports.values(), *raw_reports.values())
    )
    unapproved_occurrences = sum(
        report["unapproved_span_occurrence_count"]
        for report in (*game_reports.values(), *raw_reports.values())
    )
    payload_mismatches = sum(
        report["payload_mismatch_count"]
        for report in (*game_reports.values(), *raw_reports.values())
    )
    approved_spans = sum(
        report["approved_span_count"]
        for report in (*game_reports.values(), *raw_reports.values())
    )
    localized_decisions = sum(
        decision.decision == "localized"
        for target in review.reviews.values()
        if any(f"/{locale}/" in target.key for locale in build.enabled_locales)
        for decision in target.spans.values()
    )
    report = {
        "schema_version": LEAKAGE_SCHEMA_VERSION,
        "kind": LEAKAGE_KIND,
        "policy": {
            "approval_is_exact_target_locale_span": True,
            "broad_category_or_regex_exemption": False,
            "control_stripping_preserves_token_boundaries": True,
            "latin_span_tokenization": "NFKC then contiguous ASCII A-Z/a-z",
            "exact_copy_detection": "NFKC/casefold/alphanumeric equality",
            "near_copy_detection": "SequenceMatcher >= 0.80 with >=4 Latin letters",
            "latin_only_detection": "Latin present and no Han/kana in visible payload",
            "localized_decision_contract": (
                "exact current payload match and reviewed baseline span absent"
            ),
        },
        "inputs": {
            **dict(sorted(inputs.items())),
            "latin_span_review": {
                "path": review.path,
                "sha256": review.sha256,
                "byte_count": review.byte_count,
                "baseline_commit": review.baseline_commit,
            },
        },
        "baseline_review": {
            "summary": review.summary,
            "target_count": len(review.reviews),
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
            "approved_span_count": approved_spans,
            "localized_span_decision_count": localized_decisions,
            "unapproved_span_count": unapproved_spans,
            "unapproved_span_occurrence_count": unapproved_occurrences,
            "payload_mismatch_count": payload_mismatches,
            "stale_decision_count": len(stale_decisions),
            "stale_decisions": stale_decisions,
        },
    }
    if unapproved_spans or payload_mismatches or stale_decisions:
        problems = []
        if unapproved_spans:
            problems.append(
                f"{unapproved_spans} unapproved Latin span(s) "
                f"across {unapproved_occurrences} occurrence(s)"
            )
        if payload_mismatches:
            problems.append(f"{payload_mismatches} review payload mismatch(es)")
        if stale_decisions:
            problems.append(f"{len(stale_decisions)} stale span decision(s)")
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
