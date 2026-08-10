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

LEAKAGE_SCHEMA_VERSION = 4
LEAKAGE_KIND = "runtime-locale-latin-span-audit"
REVIEW_SCHEMA_VERSION = 1
REVIEW_KIND = "runtime-locale-latin-span-review"
SCRIPT_REVIEW_SCHEMA_VERSION = 1
SCRIPT_REVIEW_KIND = "runtime-locale-unicode-script-review"
DEFAULT_REVIEW_PATH = Path("texts/locales/runtime_latin_span_review.json")
DEFAULT_SCRIPT_REVIEW_PATH = Path(
    "texts/locales/runtime_unicode_script_review.json"
)
DEFAULT_RAW_CLOSURE_PATH = Path(
    "texts/locales/mapping/raw_surface_closure.json"
)
DEFAULT_REPORT_PATH = Path(
    "texts/locales/mapping/runtime_english_leakage.json"
)
OUTPUT_REPORT_NAME = "game_localization_latin_span_audit.json"

_BRACKET_TOKEN_RE = re.compile(r"\[[^\[\]\r\n]+\]")
_LOCALIZED_SCRIPT_RE = re.compile(
    r"[\u3040-\u30FF\u3400-\u9FFF\uF900-\uFAFF]"
)
_REVIEW_KEY_RE = re.compile(
    r"(game)/(ja|zh-Hans)/(0x[0-9A-F]{4})"
    r"|(raw)/(ja|zh-Hans)/(fe8cn\.raw\.import-[0-9]{4})"
)
_COMMIT_RE = re.compile(r"[0-9a-f]{40}")
_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_SCRIPT_REVIEW_KEY_RE = re.compile(
    r"(game|raw|display)/(ja|zh-Hans)/([^/\r\n]+)"
)
_CODEPOINT_RE = re.compile(r"U\+[0-9A-F]{4,6}")
_ALLOWED_SCRIPTS = {
    "ja": frozenset(
        {"Bopomofo", "Common", "Han", "Hiragana", "Inherited", "Katakana", "Latin"}
    ),
    "zh-Hans": frozenset(
        {"Bopomofo", "Common", "Han", "Hiragana", "Inherited", "Katakana", "Latin"}
    ),
}


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


@dataclass(frozen=True)
class ScriptSymbolApproval:
    key: str
    character: str
    codepoint: str
    occurrences: int
    reason: str
    source: str


@dataclass(frozen=True)
class ScriptTargetApproval:
    key: str
    current_payload_sha256: str
    symbols: Mapping[str, ScriptSymbolApproval]


@dataclass(frozen=True)
class ScriptReviewCatalog:
    path: str
    sha256: str
    byte_count: int
    approvals: Mapping[str, ScriptTargetApproval]


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
    return Counter(_latin_spans(_visible_text(text)))


def _is_latin_letter(character: str) -> bool:
    return (
        unicodedata.category(character).startswith("L")
        and "LATIN" in unicodedata.name(character, "")
    )


def _unicode_script(character: str) -> str:
    value = ord(character)
    name = unicodedata.name(character, "")
    if "CYRILLIC" in name:
        return "Cyrillic"
    if "GREEK" in name:
        return "Greek"
    if (
        0x3400 <= value <= 0x4DBF
        or 0x4E00 <= value <= 0x9FFF
        or 0xF900 <= value <= 0xFAFF
        or 0x20000 <= value <= 0x3134F
        or "IDEOGRAPH" in name
    ):
        return "Han"
    if 0x3040 <= value <= 0x309F or "HIRAGANA" in name:
        return "Hiragana"
    if (
        0x30A0 <= value <= 0x30FF
        or 0x31F0 <= value <= 0x31FF
        or "KATAKANA" in name
    ):
        return "Katakana"
    if (
        0x3100 <= value <= 0x312F
        or 0x31A0 <= value <= 0x31BF
        or "BOPOMOFO" in name
    ):
        return "Bopomofo"
    if "LATIN" in name:
        return "Latin"
    category = unicodedata.category(character)
    if category.startswith("M"):
        return "Inherited"
    if not category.startswith("L"):
        return "Common"
    return "Other"


def _script_approval_required(character: str) -> bool:
    return (
        _unicode_script(character) == "Greek"
        or unicodedata.category(character) == "Sm"
    )


def load_script_review(
    path: Path = DEFAULT_SCRIPT_REVIEW_PATH,
) -> ScriptReviewCatalog:
    path = Path(path)
    data, data_bytes = _load_json(path)
    if not isinstance(data, dict):
        raise GameCatalogError(f"{path}: script review root must be an object")
    if data.get("schema_version") != SCRIPT_REVIEW_SCHEMA_VERSION:
        raise GameCatalogError(
            f"{path}: script review schema_version must be "
            f"{SCRIPT_REVIEW_SCHEMA_VERSION}"
        )
    if data.get("kind") != SCRIPT_REVIEW_KIND:
        raise GameCatalogError(
            f"{path}: script review kind must be {SCRIPT_REVIEW_KIND!r}"
        )
    if data.get("policy") != {
        "cyrillic_approvals_forbidden": True,
        "greek_and_math_require_exact_target_approval": True,
        "script_allowlist_is_locale_scoped": True,
    }:
        raise GameCatalogError(f"{path}: script review policy drifted")
    raw_approvals = data.get("approvals")
    if not isinstance(raw_approvals, dict):
        raise GameCatalogError(f"{path}: script approvals must be an object")

    approvals: Dict[str, ScriptTargetApproval] = {}
    for key, raw in sorted(raw_approvals.items()):
        if not isinstance(key, str) or not _SCRIPT_REVIEW_KEY_RE.fullmatch(key):
            raise GameCatalogError(
                f"{path}: script approval key {key!r} is invalid"
            )
        if not isinstance(raw, dict):
            raise GameCatalogError(
                f"{path}: script approval {key!r} must be an object"
            )
        scope, locale, target = key.split("/", 2)
        if (
            raw.get("scope") != scope
            or raw.get("locale") != locale
            or raw.get("target") != target
        ):
            raise GameCatalogError(
                f"{path}: script approval {key!r} must repeat exact scope, "
                "locale, and target"
            )
        current_payload_sha256 = raw.get("current_payload_sha256")
        raw_symbols = raw.get("symbols")
        if (
            not isinstance(current_payload_sha256, str)
            or not _SHA256_RE.fullmatch(current_payload_sha256)
            or not isinstance(raw_symbols, dict)
            or not raw_symbols
        ):
            raise GameCatalogError(
                f"{path}: script approval {key!r} has invalid payload pin "
                "or symbols"
            )
        symbols: Dict[str, ScriptSymbolApproval] = {}
        for codepoint, symbol_raw in sorted(raw_symbols.items()):
            if (
                not isinstance(codepoint, str)
                or not _CODEPOINT_RE.fullmatch(codepoint)
                or not isinstance(symbol_raw, dict)
            ):
                raise GameCatalogError(
                    f"{path}: script approval {key!r}/{codepoint!r} is malformed"
                )
            character = symbol_raw.get("character")
            occurrences = symbol_raw.get("occurrences")
            reason = symbol_raw.get("reason")
            source = symbol_raw.get("source")
            if (
                not isinstance(character, str)
                or len(character) != 1
                or codepoint != f"U+{ord(character):04X}"
                or not isinstance(occurrences, int)
                or isinstance(occurrences, bool)
                or occurrences < 1
                or not isinstance(reason, str)
                or not reason.strip()
                or not isinstance(source, str)
                or not source.strip()
            ):
                raise GameCatalogError(
                    f"{path}: script approval {key!r}/{codepoint} is invalid"
                )
            if _unicode_script(character) == "Cyrillic":
                raise GameCatalogError(
                    f"{path}: Cyrillic approval is forbidden for "
                    f"{key}/{codepoint}"
                )
            if not _script_approval_required(character):
                raise GameCatalogError(
                    f"{path}: {key}/{codepoint} is not Greek or mathematical"
                )
            symbols[codepoint] = ScriptSymbolApproval(
                key=f"{key}#{codepoint}",
                character=character,
                codepoint=codepoint,
                occurrences=occurrences,
                reason=reason,
                source=source,
            )
        approvals[key] = ScriptTargetApproval(
            key=key,
            current_payload_sha256=current_payload_sha256,
            symbols=symbols,
        )
    return ScriptReviewCatalog(
        path=path.as_posix(),
        sha256=sha256_bytes(data_bytes),
        byte_count=len(data_bytes),
        approvals=approvals,
    )


def _latin_spans(text: str) -> Tuple[str, ...]:
    spans = []
    current = []
    for character in text:
        if _is_latin_letter(character):
            current.append(character)
        elif current:
            spans.append("".join(current))
            current = []
    if current:
        spans.append("".join(current))
    return tuple(spans)


def _is_single_latin_span(value: str) -> bool:
    return bool(value) and _latin_spans(value) == (value,)


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
                or not _is_single_latin_span(span)
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
    normalized = unicodedata.normalize("NFKD", _visible_text(text).casefold())
    return "".join(
        character
        for character in normalized
        if character.isdigit() or _is_latin_letter(character)
    )


def _latin_letter_count(text: str) -> int:
    return sum(_is_latin_letter(character) for character in text)


def _mojibake_spans(text: str) -> Tuple[str, ...]:
    spans = []
    seen = set()
    for start in range(len(text)):
        for end in range(start + 2, min(len(text), start + 4) + 1):
            candidate = text[start:end]
            try:
                raw = candidate.encode("cp1252")
                decoded = raw.decode("utf-8")
            except (UnicodeEncodeError, UnicodeDecodeError):
                continue
            if decoded == candidate or not any(ord(character) > 0x7F for character in decoded):
                continue
            if candidate not in seen:
                spans.append(candidate)
                seen.add(candidate)
    return tuple(spans)


def _payload_artifacts(text: str) -> Dict[str, Any]:
    visible = _BRACKET_TOKEN_RE.sub("", text)
    replacement_count = visible.count("\uFFFD")
    c1_controls = tuple(
        f"U+{ord(character):04X}"
        for character in visible
        if 0x80 <= ord(character) <= 0x9F
    )
    mojibake = _mojibake_spans(visible)
    return {
        "c1_control_count": len(c1_controls),
        "c1_controls": list(c1_controls),
        "mojibake_occurrence_count": len(mojibake),
        "mojibake_spans": list(mojibake),
        "replacement_character_count": replacement_count,
    }


def _classify_candidate(payload: str, english: str) -> Tuple[Tuple[str, ...], float]:
    visible = _visible_text(payload)
    english_visible = _visible_text(english)
    if not _latin_spans(visible):
        return (), 0.0

    classifications = []
    payload_key = _copy_key(payload)
    english_key = _copy_key(english)
    similarity = 0.0
    if payload_key and english_key and payload_key == english_key:
        classifications.append("exact-english-copy")
        similarity = 1.0
    elif (
        _latin_letter_count(visible) >= 4
        and _latin_letter_count(english_visible) >= 4
        and payload_key
        and english_key
    ):
        similarity = SequenceMatcher(None, payload_key, english_key).ratio()
        if similarity >= 0.80:
            classifications.append("near-english-copy")

    if not _LOCALIZED_SCRIPT_RE.search(visible):
        classifications.append("latin-only-payload")
    return tuple(classifications), similarity


def _audit_script_payload(
    payload: str,
    *,
    locale: str,
    target_key: str,
    review: ScriptReviewCatalog,
) -> Tuple[Dict[str, Any], Tuple[str, ...]]:
    visible = _BRACKET_TOKEN_RE.sub("", payload)
    counts = Counter(visible)
    payload_sha256 = sha256_bytes(payload.encode("utf-8"))
    target_review = review.approvals.get(target_key)
    payload_matches = (
        target_review is not None
        and target_review.current_payload_sha256 == payload_sha256
    )
    findings = []
    used_approvals = []
    approved_count = 0
    approved_occurrences = 0
    disallowed_count = 0
    disallowed_occurrences = 0
    unapproved_count = 0
    unapproved_occurrences = 0
    for character, occurrences in sorted(
        counts.items(),
        key=lambda item: ord(item[0]),
    ):
        codepoint = f"U+{ord(character):04X}"
        script = _unicode_script(character)
        category = unicodedata.category(character)
        disallowed = (
            script == "Cyrillic"
            or script not in _ALLOWED_SCRIPTS[locale]
            or 0x2500 <= ord(character) <= 0x257F
            or category in ("Co", "Cs", "Cn")
        )
        requires_approval = _script_approval_required(character)
        approval = (
            target_review.symbols.get(codepoint)
            if target_review is not None
            else None
        )
        approved = (
            not disallowed
            and requires_approval
            and payload_matches
            and approval is not None
            and approval.character == character
            and approval.occurrences == occurrences
        )
        if approved:
            approved_count += 1
            approved_occurrences += occurrences
            used_approvals.append(approval.key)
            continue
        if not disallowed and not requires_approval:
            continue

        if disallowed:
            disallowed_count += 1
            disallowed_occurrences += occurrences
        else:
            unapproved_count += 1
            unapproved_occurrences += occurrences
        finding = {
            "category": category,
            "character": character,
            "codepoint": codepoint,
            "occurrences": occurrences,
            "script": script,
            "status": "disallowed-script" if disallowed else "approval-required",
        }
        if approval is not None:
            finding["approval"] = {
                "occurrences": approval.occurrences,
                "payload_matches": payload_matches,
                "reason": approval.reason,
                "source": approval.source,
            }
        findings.append(finding)
    return (
        {
            "approved_symbol_count": approved_count,
            "approved_symbol_occurrence_count": approved_occurrences,
            "disallowed_symbol_count": disallowed_count,
            "disallowed_symbol_occurrence_count": disallowed_occurrences,
            "findings": findings,
            "payload_matches_review": payload_matches,
            "payload_sha256": payload_sha256,
            "unapproved_symbol_count": unapproved_count,
            "unapproved_symbol_occurrence_count": unapproved_occurrences,
        },
        tuple(used_approvals),
    )


def _audit_entries(
    entries: Iterable[Mapping[str, Any]],
    *,
    locale: str,
    scope: str,
    review: ReviewCatalog,
    script_review: ScriptReviewCatalog,
) -> Tuple[Dict[str, Any], Tuple[str, ...], Tuple[str, ...]]:
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
    artifact_payloads = []
    replacement_character_count = 0
    c1_control_count = 0
    mojibake_occurrence_count = 0
    used_script_approvals = []
    script_payloads = []
    approved_script_symbol_count = 0
    approved_script_symbol_occurrence_count = 0
    disallowed_script_symbol_count = 0
    disallowed_script_symbol_occurrence_count = 0
    unapproved_script_symbol_count = 0
    unapproved_script_symbol_occurrence_count = 0
    script_payload_mismatch_count = 0
    for entry in entries:
        audited_count += 1
        payload = entry["payload"]
        english = entry["english"]
        classifications, similarity = _classify_candidate(payload, english)
        exact_count += "exact-english-copy" in classifications
        near_count += "near-english-copy" in classifications
        latin_only_count += "latin-only-payload" in classifications
        target_key = f"{scope}/{locale}/{entry['id']}"
        script_audit, script_used = _audit_script_payload(
            payload,
            locale=locale,
            target_key=target_key,
            review=script_review,
        )
        approved_script_symbol_count += script_audit[
            "approved_symbol_count"
        ]
        approved_script_symbol_occurrence_count += script_audit[
            "approved_symbol_occurrence_count"
        ]
        disallowed_script_symbol_count += script_audit[
            "disallowed_symbol_count"
        ]
        disallowed_script_symbol_occurrence_count += script_audit[
            "disallowed_symbol_occurrence_count"
        ]
        unapproved_script_symbol_count += script_audit[
            "unapproved_symbol_count"
        ]
        unapproved_script_symbol_occurrence_count += script_audit[
            "unapproved_symbol_occurrence_count"
        ]
        used_script_approvals.extend(script_used)
        if (
            target_key in script_review.approvals
            and not script_audit["payload_matches_review"]
        ):
            script_payload_mismatch_count += 1
        if script_audit["findings"] or script_used:
            script_payloads.append(
                {
                    "id": entry["id"],
                    **script_audit,
                }
            )
        artifacts = _payload_artifacts(payload)
        replacement_character_count += artifacts["replacement_character_count"]
        c1_control_count += artifacts["c1_control_count"]
        mojibake_occurrence_count += artifacts["mojibake_occurrence_count"]
        if (
            artifacts["replacement_character_count"]
            or artifacts["c1_control_count"]
            or artifacts["mojibake_occurrence_count"]
        ):
            artifact_payloads.append(
                {
                    **artifacts,
                    "id": entry["id"],
                    "payload_sha256": sha256_bytes(payload.encode("utf-8")),
                    "user_facing": entry["user_facing"],
                }
            )
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
            "artifact_payload_count": len(artifact_payloads),
            "replacement_character_count": replacement_character_count,
            "c1_control_count": c1_control_count,
            "mojibake_occurrence_count": mojibake_occurrence_count,
            "artifact_payloads": artifact_payloads,
            "latin_bearing_payloads": latin_payloads,
            "script_approved_symbol_count": approved_script_symbol_count,
            "script_approved_symbol_occurrence_count": (
                approved_script_symbol_occurrence_count
            ),
            "script_disallowed_symbol_count": disallowed_script_symbol_count,
            "script_disallowed_symbol_occurrence_count": (
                disallowed_script_symbol_occurrence_count
            ),
            "script_payload_mismatch_count": script_payload_mismatch_count,
            "script_payloads": script_payloads,
            "script_unapproved_symbol_count": unapproved_script_symbol_count,
            "script_unapproved_symbol_occurrence_count": (
                unapproved_script_symbol_occurrence_count
            ),
        },
        tuple(used_decisions),
        tuple(used_script_approvals),
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


def _display_alias_entries(
    build: GameCatalogBuild,
    locale: str,
) -> Iterable[Dict[str, Any]]:
    english_by_id = {entry.target_id: entry for entry in build.english.entries}
    for surface, entries in sorted(
        build.display_aliases.get(locale, {}).items()
    ):
        for target_id, payload in sorted(entries.items()):
            yield {
                "english": english_by_id[target_id].source_text,
                "id": f"{surface}:0x{target_id:04X}",
                "metadata": {
                    "canonical_payload": build.locale_bundle(locale)
                    .entries[target_id]
                    .source_text,
                    "surface": surface,
                    "target_id": f"0x{target_id:04X}",
                },
                "payload": payload,
                "user_facing": True,
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
    script_review: ScriptReviewCatalog,
    raw_closure: Mapping[str, Any],
    expansion_catalogs: Mapping[str, Mapping[str, str]],
    inputs: Mapping[str, Mapping[str, Any]],
) -> Dict[str, Any]:
    raw_closure = _validate_raw_closure(raw_closure)
    game_reports = {}
    raw_reports = {}
    display_alias_reports = {}
    used_decisions = set()
    used_script_approvals = set()
    for locale in build.enabled_locales:
        game_report, game_used, game_script_used = _audit_entries(
            _game_entries(build, locale),
            locale=locale,
            scope="game",
            review=review,
            script_review=script_review,
        )
        raw_report, raw_used, raw_script_used = _audit_entries(
            _raw_entries(
                build,
                locale,
                raw_closure=raw_closure,
                expansion_catalogs=expansion_catalogs,
            ),
            locale=locale,
            scope="raw",
            review=review,
            script_review=script_review,
        )
        (
            display_alias_report,
            display_alias_used,
            display_alias_script_used,
        ) = _audit_entries(
            _display_alias_entries(build, locale),
            locale=locale,
            scope="display",
            review=review,
            script_review=script_review,
        )
        game_reports[locale] = game_report
        raw_reports[locale] = raw_report
        display_alias_reports[locale] = display_alias_report
        used_decisions.update(game_used)
        used_decisions.update(raw_used)
        used_decisions.update(display_alias_used)
        used_script_approvals.update(game_script_used)
        used_script_approvals.update(raw_script_used)
        used_script_approvals.update(display_alias_script_used)

    relevant_decisions = {
        decision.key
        for target in review.reviews.values()
        if any(f"/{locale}/" in target.key for locale in build.enabled_locales)
        for decision in target.spans.values()
    }
    stale_decisions = sorted(relevant_decisions - used_decisions)
    relevant_script_approvals = {
        approval.key
        for target in script_review.approvals.values()
        if any(f"/{locale}/" in target.key for locale in build.enabled_locales)
        for approval in target.symbols.values()
    }
    stale_script_approvals = sorted(
        relevant_script_approvals - used_script_approvals
    )
    unapproved_spans = sum(
        report["unapproved_span_count"]
        for report in (
            *game_reports.values(),
            *raw_reports.values(),
            *display_alias_reports.values(),
        )
    )
    unapproved_occurrences = sum(
        report["unapproved_span_occurrence_count"]
        for report in (
            *game_reports.values(),
            *raw_reports.values(),
            *display_alias_reports.values(),
        )
    )
    payload_mismatches = sum(
        report["payload_mismatch_count"]
        for report in (
            *game_reports.values(),
            *raw_reports.values(),
            *display_alias_reports.values(),
        )
    )
    artifact_payloads = sum(
        report["artifact_payload_count"]
        for report in (
            *game_reports.values(),
            *raw_reports.values(),
            *display_alias_reports.values(),
        )
    )
    replacement_characters = sum(
        report["replacement_character_count"]
        for report in (
            *game_reports.values(),
            *raw_reports.values(),
            *display_alias_reports.values(),
        )
    )
    c1_controls = sum(
        report["c1_control_count"]
        for report in (
            *game_reports.values(),
            *raw_reports.values(),
            *display_alias_reports.values(),
        )
    )
    mojibake_occurrences = sum(
        report["mojibake_occurrence_count"]
        for report in (
            *game_reports.values(),
            *raw_reports.values(),
            *display_alias_reports.values(),
        )
    )
    approved_spans = sum(
        report["approved_span_count"]
        for report in (
            *game_reports.values(),
            *raw_reports.values(),
            *display_alias_reports.values(),
        )
    )
    approved_script_symbols = sum(
        report["script_approved_symbol_count"]
        for report in (
            *game_reports.values(),
            *raw_reports.values(),
            *display_alias_reports.values(),
        )
    )
    disallowed_script_symbols = sum(
        report["script_disallowed_symbol_count"]
        for report in (
            *game_reports.values(),
            *raw_reports.values(),
            *display_alias_reports.values(),
        )
    )
    disallowed_script_occurrences = sum(
        report["script_disallowed_symbol_occurrence_count"]
        for report in (
            *game_reports.values(),
            *raw_reports.values(),
            *display_alias_reports.values(),
        )
    )
    unapproved_script_symbols = sum(
        report["script_unapproved_symbol_count"]
        for report in (
            *game_reports.values(),
            *raw_reports.values(),
            *display_alias_reports.values(),
        )
    )
    unapproved_script_occurrences = sum(
        report["script_unapproved_symbol_occurrence_count"]
        for report in (
            *game_reports.values(),
            *raw_reports.values(),
            *display_alias_reports.values(),
        )
    )
    script_payload_mismatches = sum(
        report["script_payload_mismatch_count"]
        for report in (
            *game_reports.values(),
            *raw_reports.values(),
            *display_alias_reports.values(),
        )
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
            "latin_span_tokenization": (
                "NFKC then contiguous Unicode letters whose names contain LATIN"
            ),
            "exact_copy_detection": (
                "NFKC/casefold/NFKD Latin-letter and digit equality"
            ),
            "near_copy_detection": "SequenceMatcher >= 0.80 with >=4 Latin-script letters",
            "latin_only_detection": "Latin present and no Han/kana in visible payload",
            "replacement_c1_mojibake_policy": "correction required; no broad exemption",
            "mojibake_detection": (
                "2..4 scalar CP1252 runs that decode as distinct valid UTF-8"
            ),
            "localized_decision_contract": (
                "exact current payload match and reviewed baseline span absent"
            ),
            "unicode_script_allowlist": {
                locale: sorted(_ALLOWED_SCRIPTS[locale])
                for locale in ("ja", "zh-Hans")
            },
            "unicode_script_policy": (
                "Cyrillic, box drawing, private/unassigned scalars, and "
                "non-allowlisted scripts are forbidden; Greek and Unicode "
                "math symbols require exact target approvals"
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
            "unicode_script_review": {
                "path": script_review.path,
                "sha256": script_review.sha256,
                "byte_count": script_review.byte_count,
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
        "display_aliases": {
            "locales": display_alias_reports,
            "policy": "Latin/artifact-bearing aliases require correction",
        },
        "summary": {
            "enabled_locales": list(build.enabled_locales),
            "game_payload_count": sum(
                report["audited_count"] for report in game_reports.values()
            ),
            "raw_surface_payload_count": sum(
                report["audited_count"] for report in raw_reports.values()
            ),
            "display_alias_payload_count": sum(
                report["audited_count"]
                for report in display_alias_reports.values()
            ),
            "approved_span_count": approved_spans,
            "localized_span_decision_count": localized_decisions,
            "unapproved_span_count": unapproved_spans,
            "unapproved_span_occurrence_count": unapproved_occurrences,
            "payload_mismatch_count": payload_mismatches,
            "artifact_payload_count": artifact_payloads,
            "replacement_character_count": replacement_characters,
            "c1_control_count": c1_controls,
            "mojibake_occurrence_count": mojibake_occurrences,
            "stale_decision_count": len(stale_decisions),
            "stale_decisions": stale_decisions,
            "approved_script_symbol_count": approved_script_symbols,
            "disallowed_script_symbol_count": disallowed_script_symbols,
            "disallowed_script_symbol_occurrence_count": (
                disallowed_script_occurrences
            ),
            "unapproved_script_symbol_count": unapproved_script_symbols,
            "unapproved_script_symbol_occurrence_count": (
                unapproved_script_occurrences
            ),
            "script_payload_mismatch_count": script_payload_mismatches,
            "stale_script_approval_count": len(stale_script_approvals),
            "stale_script_approvals": stale_script_approvals,
        },
    }
    if (
        unapproved_spans
        or payload_mismatches
        or artifact_payloads
        or stale_decisions
        or disallowed_script_symbols
        or unapproved_script_symbols
        or script_payload_mismatches
        or stale_script_approvals
    ):
        problems = []
        if unapproved_spans:
            problems.append(
                f"{unapproved_spans} unapproved Latin span(s) "
                f"across {unapproved_occurrences} occurrence(s)"
            )
        if payload_mismatches:
            problems.append(f"{payload_mismatches} review payload mismatch(es)")
        if artifact_payloads:
            problems.append(
                f"{artifact_payloads} replacement/C1/mojibake payload(s)"
            )
        if stale_decisions:
            problems.append(f"{len(stale_decisions)} stale span decision(s)")
        if disallowed_script_symbols:
            problems.append(
                f"{disallowed_script_symbols} disallowed Unicode script "
                f"symbol(s) across {disallowed_script_occurrences} "
                "occurrence(s)"
            )
        if unapproved_script_symbols:
            problems.append(
                f"{unapproved_script_symbols} unapproved Greek/math symbol(s) "
                f"across {unapproved_script_occurrences} occurrence(s)"
            )
        if script_payload_mismatches:
            problems.append(
                f"{script_payload_mismatches} script-review payload "
                "mismatch(es)"
            )
        if stale_script_approvals:
            problems.append(
                f"{len(stale_script_approvals)} stale script approval(s)"
            )
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
