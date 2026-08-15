"""Typed width limits and deterministic CJK/Latin-safe line breaking.

The catalog's source controls are part of the FE8 ABI.  This module therefore
never rewrites a control token, and only emits the existing ``[NL]`` byte
(``0x01``) between two visible scalars.  It is deliberately shared by the
catalog generator and its CI validation path so a generated catalog cannot
pass with a different interpretation from the line-break tool.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Sequence, Tuple

from .ending_metrics import EndingLayoutError, _ascii_widths, _cjk_widths
from .fixed_width_labels import SURFACES, _message_ids


DEFAULT_WIDTH_REGISTRY_PATH = Path("texts/locales/mapping/text_width_contexts.json")
_CONTROL_TOKEN_RE = re.compile(r"\[CTRL:([0-9A-F]{4})\]")
_CJK_OPENING_PUNCTUATION = frozenset("([<{「『（［｛〈《【〔〖〘〚“‘")
_CJK_CLOSING_PUNCTUATION = frozenset(")]}>，、。．！？：；％）］｝〉》、】【〕〗〙〛”’")


class TextWidthContractError(ValueError):
    """Raised when a catalog string has no safe, in-bounds rendering layout."""


def _tokenize_payload(payload: bytes, *, source_name: str):
    """Import lazily: game_catalog.__init__ imports build, which imports us."""

    from scripts.localization.game_catalog.control_streams import tokenize_payload

    return tokenize_payload(payload, source_name=source_name)


def _event_models(event_root: Path):
    from scripts.localization.game_catalog.control_streams import (
        build_event_continuation_models,
    )

    return build_event_continuation_models(event_root)


@dataclass(frozen=True)
class WidthContext:
    name: str
    style: str
    max_pixels: int
    allow_generated_wrap: bool
    source: str


@dataclass(frozen=True)
class WidthRegistry:
    path: Path
    contexts: Mapping[str, WidthContext]
    default_context: str
    ending_context: str
    fixed_context: str
    subtitle_context: str
    talk_context: str
    schema_sha256: str


@dataclass(frozen=True)
class WidthMetrics:
    locale: str
    style: str
    ascii_widths: Mapping[int, int]
    cjk_widths: Mapping[int, int]


def _canonical_json_bytes(data: Any) -> bytes:
    return (
        json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def load_width_registry(path: Path = DEFAULT_WIDTH_REGISTRY_PATH) -> WidthRegistry:
    """Load the maintained UI/scene width policy and reject schema drift."""

    path = Path(path)
    try:
        raw = path.read_bytes()
        data = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise TextWidthContractError(
            f"{path}: width context registry is unavailable or invalid"
        ) from error
    if raw != _canonical_json_bytes(data):
        raise TextWidthContractError(f"{path}: registry JSON must be canonical")
    if (
        not isinstance(data, dict)
        or data.get("kind") != "fe8u-text-width-context-registry"
        or data.get("schema_version") != 1
        or not isinstance(data.get("contexts"), dict)
        or not isinstance(data.get("classification"), dict)
    ):
        raise TextWidthContractError(f"{path}: registry schema is invalid")

    contexts: Dict[str, WidthContext] = {}
    for name, record in data["contexts"].items():
        if (
            not isinstance(name, str)
            or not isinstance(record, dict)
            or set(record) != {
                "allow_generated_wrap",
                "max_pixels",
                "source",
                "style",
            }
            or record["style"] not in ("system", "talk")
            or not isinstance(record["max_pixels"], int)
            or not 1 <= record["max_pixels"] <= 240
            or not isinstance(record["allow_generated_wrap"], bool)
            or not isinstance(record["source"], str)
            or not record["source"]
        ):
            raise TextWidthContractError(f"{path}: context {name!r} is invalid")
        contexts[name] = WidthContext(
            name=name,
            style=record["style"],
            max_pixels=record["max_pixels"],
            allow_generated_wrap=record["allow_generated_wrap"],
            source=record["source"],
        )

    classification = data["classification"]
    required = {
        "default_context",
        "ending_context",
        "fixed_label_context",
        "subtitle_help_context",
        "talk_event_context",
    }
    if set(classification) != required:
        raise TextWidthContractError(f"{path}: classification keys drifted")
    for name, value in classification.items():
        if not isinstance(value, str) or value not in contexts:
            raise TextWidthContractError(
                f"{path}: classification {name!r} references no context"
            )
    if data.get("policy") != {
        "all_targets_must_be_classified": True,
        "default_context_is_conservative_30_tile_system_text": True,
        "explicit_controls_are_preserved": True,
        "unmapped_runtime_usage_is_not_silently_exempted": True,
    }:
        raise TextWidthContractError(f"{path}: policy drifted")

    return WidthRegistry(
        path=path,
        contexts=contexts,
        default_context=classification["default_context"],
        ending_context=classification["ending_context"],
        fixed_context=classification["fixed_label_context"],
        subtitle_context=classification["subtitle_help_context"],
        talk_context=classification["talk_event_context"],
        schema_sha256=hashlib.sha256(raw).hexdigest(),
    )


def load_width_metrics(
    repo_root: Path,
    *,
    locale: str,
    style: str,
) -> WidthMetrics:
    """Read exactly the VWF assets that the selected runtime font uses."""

    try:
        ascii_widths = _ascii_widths(repo_root, style=style)
        cjk_widths, _ = _cjk_widths(repo_root, locale, style=style)
    except (
        EndingLayoutError,
        OSError,
        UnicodeDecodeError,
        json.JSONDecodeError,
    ) as error:
        raise TextWidthContractError(
            f"{locale}/{style}: runtime VWF metrics are unavailable"
        ) from error
    return WidthMetrics(
        locale=locale,
        style=style,
        ascii_widths=ascii_widths,
        cjk_widths=cjk_widths,
    )


def _scalar_width(scalar: int, metrics: WidthMetrics) -> int:
    if scalar < 0x20:
        return 0
    if scalar < 0x80:
        try:
            return metrics.ascii_widths[scalar]
        except KeyError as error:
            raise TextWidthContractError(
                f"{metrics.locale}/{metrics.style}: ASCII U+{scalar:04X} "
                "is absent from the runtime font"
            ) from error
    if scalar == 0x3000:
        return 16
    try:
        return metrics.cjk_widths[scalar]
    except KeyError as error:
        raise TextWidthContractError(
            f"{metrics.locale}/{metrics.style}: U+{scalar:04X} "
            "is absent from the runtime font"
        ) from error


def _visible_scalar(token) -> bool:
    return token.kind == "scalar" and token.scalar is not None


def _hard_line_boundary(token) -> bool:
    return (
        token.kind == "end"
        or (token.kind == "control" and token.control == 0x01)
        or (token.kind == "extended" and token.scalar == 0x04)
    )


def _safe_break(left, right) -> bool:
    """Return whether an inserted NL preserves CJK and ASCII word semantics."""

    assert left.scalar is not None and right.scalar is not None
    left_char = chr(left.scalar)
    right_char = chr(right.scalar)
    if left_char.isspace() or right_char.isspace():
        return True
    if left_char in _CJK_OPENING_PUNCTUATION:
        return False
    if right_char in _CJK_CLOSING_PUNCTUATION:
        return False
    # Latin/digit words never split in the middle. CJK has no mandatory
    # whitespace, so scalar boundaries are legal after kinsoku filtering.
    if left_char.isascii() and right_char.isascii():
        return not (left_char.isalnum() and right_char.isalnum())
    return True


def _encode_inserted_newline(payload: bytes, offset: int) -> bytes:
    return payload[:offset] + b"\x01" + payload[offset:]


def _wrap_one_line(
    payload: bytes,
    *,
    metrics: WidthMetrics,
    max_pixels: int,
    source_name: str,
) -> Tuple[bytes, int]:
    """Insert at most one safe NL, returning unchanged bytes when it fits."""

    tokens = _tokenize_payload(payload, source_name=source_name)
    width = 0
    last_safe: Tuple[int, Any] | None = None
    previous_visible: Any | None = None
    for token in tokens:
        if _hard_line_boundary(token):
            return payload, 0
        if not _visible_scalar(token):
            continue
        assert token.scalar is not None
        scalar_width = _scalar_width(token.scalar, metrics)
        if previous_visible is not None and _safe_break(previous_visible, token):
            last_safe = (token.offset, token)
        if width + scalar_width <= max_pixels:
            width += scalar_width
            previous_visible = token
            continue
        if last_safe is None:
            raise TextWidthContractError(
                f"{source_name}: unbreakable rendered span exceeds {max_pixels}px"
            )
        offset, _ = last_safe
        # The contract inserts an existing engine control and nothing else:
        # even a wrap-adjacent ASCII space is retained byte-for-byte.
        return _encode_inserted_newline(payload, offset), 1
    return payload, 0


def insert_safe_line_breaks(
    payload: bytes,
    *,
    metrics: WidthMetrics,
    max_pixels: int,
    source_name: str,
) -> Tuple[bytes, int]:
    """Wrap every physical rendering line without modifying authored controls."""

    if not payload or payload[-1] != 0:
        raise TextWidthContractError(f"{source_name}: payload must be NUL terminated")
    inserted = 0
    cursor = 0
    while cursor < len(payload) - 1:
        suffix = payload[cursor:]
        rewritten, count = _wrap_one_line(
            suffix,
            metrics=metrics,
            max_pixels=max_pixels,
            source_name=source_name,
        )
        if count:
            payload = payload[:cursor] + rewritten
            inserted += count
            cursor += rewritten.index(b"\x01") + 1
            continue
        tokens = _tokenize_payload(suffix, source_name=source_name)
        boundary = next((token for token in tokens if _hard_line_boundary(token)), None)
        if boundary is None or boundary.kind == "end":
            break
        cursor += boundary.offset + boundary.length
    return payload, inserted


def validate_payload_width(
    payload: bytes,
    *,
    metrics: WidthMetrics,
    max_pixels: int,
    source_name: str,
) -> Dict[str, int]:
    """Reject any control-delimited rendered line exceeding its VWF limit."""

    tokens = _tokenize_payload(payload, source_name=source_name)
    width = 0
    line_count = 0
    max_width = 0
    saw_visible = False
    for token in tokens:
        if _visible_scalar(token):
            assert token.scalar is not None
            width += _scalar_width(token.scalar, metrics)
            saw_visible = True
            continue
        if not _hard_line_boundary(token):
            continue
        if saw_visible:
            line_count += 1
            max_width = max(max_width, width)
            if width > max_pixels:
                raise TextWidthContractError(
                    f"{source_name}: rendered line is {width}px, exceeding "
                    f"{max_pixels}px"
                )
        width = 0
        saw_visible = False
    return {
        "line_count": line_count,
        "max_line_width": max_width,
    }


def _ending_target_ids(repo_root: Path) -> frozenset[int]:
    # The existing ending metric verifier owns exact title/solo/paired
    # geometry.  This range is only the registry's scene classification.
    return frozenset(range(0x07D6, 0x0839))


def _fixed_label_target_ids(repo_root: Path) -> frozenset[int]:
    return frozenset(
        target
        for specification in SURFACES.values()
        for target in _message_ids(repo_root / specification["data_path"])
    )


def _direct_talk_target_ids(repo_root: Path, target_count: int) -> Mapping[int, str]:
    """Resolve literal and table-driven non-event talk-text callers."""

    found: Dict[int, str] = {}

    def add(value: int, reason: str) -> None:
        if value < target_count:
            found[value] = reason

    arena = (repo_root / "src/uiarena.c").read_text(encoding="utf-8")
    for value in re.findall(
        r"\bStartArenaDialogue\s*\(\s*(0x[0-9A-Fa-f]+|\d+)", arena
    ):
        add(int(value, 0), "arena direct talk consumer")

    shop = (repo_root / "src/bmshop.c").read_text(encoding="utf-8")
    offsets = [
        int(value, 0)
        for value in re.findall(
            r"\[SHOP_TYPE_[A-Z_]+\]\s*=\s*(0x[0-9A-Fa-f]+|\d+)", shop
        )
    ]
    for value in re.findall(
        r"\bStartShopDialogue\s*\(\s*(0x[0-9A-Fa-f]+|\d+)", shop
    ):
        for offset in offsets:
            add(int(value, 0) + offset, "shop table-driven talk consumer")
    return found


def _subtitle_help_target_ids(repo_root: Path, target_count: int) -> Mapping[int, str]:
    found: Dict[int, str] = {}
    for path in (repo_root / "src").glob("*.c"):
        source = path.read_text(encoding="utf-8")
        for value in re.findall(
            r"\bStartSubtitleHelp\s*\([^;{}]*?GetStringFromIndex\s*\(\s*"
            r"(0x[0-9A-Fa-f]+|\d+)\s*\)",
            source,
        ):
            target = int(value, 0)
            if target < target_count:
                found[target] = f"SubtitleHelp stream consumer in {path.name}"
    return found


def classify_targets(
    repo_root: Path,
    *,
    target_count: int,
    registry: WidthRegistry,
) -> Tuple[Mapping[int, Tuple[str, str]], Dict[str, int]]:
    """Assign each target one audited scene/usage context without exemptions."""

    assigned: Dict[int, Tuple[str, str]] = {
        target_id: (registry.default_context, "default 30-tile system Text field")
        for target_id in range(target_count)
    }
    for target_id in _ending_target_ids(repo_root):
        if target_id < target_count:
            assigned[target_id] = (
                registry.ending_context,
                "ending-details source allocation; exact line/page rules are "
                "validated by ending_layout_metrics",
            )
    for target_id in _fixed_label_target_ids(repo_root):
        if target_id < target_count:
            assigned[target_id] = (
                registry.fixed_context,
                "fixed-width label source allocation; aliases are validated "
                "by fixed_width_label_metrics",
            )
    for target_id, models in _event_models(
        repo_root / "src/events"
    ).items():
        if target_id < target_count:
            assigned[target_id] = (
                registry.talk_context,
                "event dialogue: "
                + ", ".join(
                    f"{model.start_kind}@"
                    f"{Path(model.source_path).relative_to(repo_root).as_posix()}"
                    for model in models
                ),
            )
    for target_id, reason in _direct_talk_target_ids(repo_root, target_count).items():
        assigned[target_id] = (registry.talk_context, reason)
    for target_id, reason in _subtitle_help_target_ids(repo_root, target_count).items():
        assigned[target_id] = (registry.subtitle_context, reason)
    counts: Dict[str, int] = {name: 0 for name in registry.contexts}
    for context, _ in assigned.values():
        counts[context] += 1
    return assigned, counts


def apply_width_contract(
    *,
    repo_root: Path,
    locale: str,
    target_payloads: Sequence[bytes],
    registry: WidthRegistry,
) -> Tuple[Tuple[bytes, ...], Dict[str, Any]]:
    """Wrap then validate a complete locale payload table against all contexts."""

    assignments, counts = classify_targets(
        repo_root,
        target_count=len(target_payloads),
        registry=registry,
    )
    metrics_by_style = {
        style: load_width_metrics(repo_root, locale=locale, style=style)
        for style in ("system", "talk")
    }
    output = []
    inserted_total = 0
    max_width = 0
    line_total = 0
    records = []
    observed_counts: Dict[str, int] = {name: 0 for name in registry.contexts}
    for target_id, payload in enumerate(target_payloads):
        context_name, usage = assignments[target_id]
        tokens = _tokenize_payload(
            payload,
            source_name=f"{locale} target 0x{target_id:04X}",
        )
        if any(
            token.kind == "control"
            and token.control is not None
            and 0x08 <= token.control <= 0x11
            for token in tokens
        ):
            context_name = registry.talk_context
            usage = "talk control stream (face/speaker geometry)"
        context = registry.contexts[context_name]
        observed_counts[context_name] += 1
        source_name = f"{locale} target 0x{target_id:04X} ({context_name})"
        rewritten = payload
        inserted = 0
        if context.allow_generated_wrap:
            rewritten, inserted = insert_safe_line_breaks(
                payload,
                metrics=metrics_by_style[context.style],
                max_pixels=context.max_pixels,
                source_name=source_name,
            )
        if context_name == registry.subtitle_context:
            # SubtitleHelp has its own visual splitter in bb.c. A stream NL is
            # a terminator for that loop, not a line break, so deliberately do
            # not apply generic Text-line validation or generation here.
            stats = {"line_count": 0, "max_line_width": 0}
        else:
            stats = validate_payload_width(
                rewritten,
                metrics=metrics_by_style[context.style],
                max_pixels=context.max_pixels,
                source_name=source_name,
            )
        inserted_total += inserted
        max_width = max(max_width, stats["max_line_width"])
        line_total += stats["line_count"]
        output.append(rewritten)
        records.append(
            {
                "context": context_name,
                "generated_line_break_count": inserted,
                "max_line_width": stats["max_line_width"],
                "target_id": f"0x{target_id:04X}",
                "usage": usage,
            }
        )
    return tuple(output), {
        "context_counts": observed_counts,
        "generated_line_break_count": inserted_total,
        "line_count": line_total,
        "locale": locale,
        "max_line_width": max_width,
        "records": records,
        "registry_sha256": registry.schema_sha256,
        "target_count": len(target_payloads),
        "unclassified_target_count": 0,
        "unclassified_targets": [],
    }
