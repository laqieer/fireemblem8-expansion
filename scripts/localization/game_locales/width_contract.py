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
import os
import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, Iterable, Mapping, Sequence, Tuple

from scripts.localization.legacy_spacing import (
    LEGACY_SJIS_SPACE_SCALAR,
    LEGACY_SJIS_SPACE_WIDTH,
)

from .ending_metrics import EndingLayoutError, _ascii_widths, _cjk_widths
from .fixed_width_labels import SURFACES, _message_ids

if TYPE_CHECKING:
    from scripts.localization.game_catalog.compiled_fonts import SelectedFontMetrics
    from scripts.localization.game_catalog.control_streams import StreamToken


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


def _event_models(event_root: Path, message_ids: Mapping[str, int] | None = None):
    from scripts.localization.game_catalog.control_streams import (
        build_event_continuation_models,
    )

    return build_event_continuation_models(event_root, message_ids=message_ids)


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
    box_context: str
    cg_context: str
    schema_sha256: str


class TextRenderer(str, Enum):
    TEXT = "text"
    TALK = "talk"
    BOX = "box"
    BOX_MEASURE = "box_dimensions"
    CG = "cg"
    CG_MEASURE = "cg_box_dimensions"
    SUBTITLE = "subtitle"


@dataclass(frozen=True)
class RendererRequirement:
    context: str
    renderer: TextRenderer
    style: str
    sources: Tuple[str, ...]
    measure_only: bool = False


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
        or data.get("schema_version") != 2
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
        "box_event_context",
        "cg_event_context",
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
        box_context=classification["box_event_context"],
        cg_context=classification["cg_event_context"],
        schema_sha256=hashlib.sha256(raw).hexdigest(),
    )


def load_width_metrics(
    repo_root: Path,
    *,
    locale: str,
    style: str,
    font_metrics: SelectedFontMetrics | None = None,
) -> WidthMetrics:
    """Read exactly the VWF assets that the selected runtime font uses."""

    try:
        if font_metrics is None:
            ascii_widths = _ascii_widths(repo_root, style=style)
            cjk_widths = {} if locale == "en" else _cjk_widths(repo_root, locale, style=style)[0]
        else:
            ascii_widths = font_metrics["ascii"][style]
            cjk_widths = {} if locale == "en" else font_metrics["localized"][locale][style]
    except (
        EndingLayoutError,
        OSError,
        UnicodeDecodeError,
        json.JSONDecodeError,
        KeyError,
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
    if scalar == LEGACY_SJIS_SPACE_SCALAR:
        return LEGACY_SJIS_SPACE_WIDTH
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


def _hard_line_boundary(token, *, renderer: TextRenderer) -> bool:
    return (
        token.kind == "end"
        or (token.kind == "control" and (
            token.control == 0x01
            or (renderer != TextRenderer.TEXT and token.control == 0x02)
            or (renderer in (TextRenderer.BOX_MEASURE, TextRenderer.CG_MEASURE)
                and token.control == 0x03)
            or (renderer == TextRenderer.TALK and token.control == 0x15)
            or (renderer in (TextRenderer.BOX_MEASURE, TextRenderer.CG, TextRenderer.CG_MEASURE)
                and token.control in (0x18, 0x19))
            or (renderer in (TextRenderer.BOX, TextRenderer.BOX_MEASURE)
                and token.control in (0x12, 0x13, 0x14))
        ))
        or (renderer == TextRenderer.TALK and token.kind == "extended" and token.scalar == 0x04)
    )


def _renderer_line_boundaries(
    tokens: Sequence[StreamToken], renderer: TextRenderer,
) -> frozenset[int]:
    boundaries = set()
    active_speaker = None
    bubble_speaker = None
    bubble_open = False
    pending_selection = None
    for token in tokens:
        if _hard_line_boundary(token, renderer=renderer):
            boundaries.add(token.offset)
        if renderer != TextRenderer.TALK:
            continue
        if token.kind == "control" and token.control is not None:
            if 0x08 <= token.control <= 0x0F:
                active_speaker = token.control - 8
                pending_selection = token.offset
            elif (token.control == 0x11 and bubble_open
                    and active_speaker == bubble_speaker):
                boundaries.add(token.offset)
                bubble_open = False
            elif token.control == 0x15:
                bubble_open = False
        elif _visible_scalar(token):
            if not bubble_open or active_speaker != bubble_speaker:
                if pending_selection is not None:
                    boundaries.add(pending_selection)
                bubble_speaker = active_speaker
                bubble_open = True
            pending_selection = None
    return frozenset(boundaries)


def _ends_rendering(token, renderer: TextRenderer) -> bool:
    return token.kind == "end" or (
        renderer == TextRenderer.BOX_MEASURE
        and token.kind == "control" and token.control in (0x12, 0x13, 0x14)
    )


def _caret_padding(token, renderer: TextRenderer) -> int:
    return 8 if (
        renderer in (
            TextRenderer.BOX, TextRenderer.BOX_MEASURE,
            TextRenderer.CG, TextRenderer.CG_MEASURE,
        )
        and token.kind == "control" and token.control == 0x03
    ) else 0


def _safe_break(left, right) -> bool:
    """Return whether an inserted NL preserves CJK and ASCII word semantics."""

    assert left.scalar is not None and right.scalar is not None
    if (
        left.scalar == LEGACY_SJIS_SPACE_SCALAR
        or right.scalar == LEGACY_SJIS_SPACE_SCALAR
    ):
        return True
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
    renderer: TextRenderer,
    line_boundaries: frozenset[int],
) -> Tuple[bytes, int | None]:
    """Insert at most one safe NL, returning unchanged bytes when it fits."""

    tokens = _tokenize_payload(payload, source_name=source_name)
    width = 0
    last_safe: Tuple[int, Any] | None = None
    previous_visible: Any | None = None
    for token in tokens:
        if _visible_scalar(token):
            assert token.scalar is not None
            if previous_visible is not None and _safe_break(previous_visible, token):
                last_safe = (token.offset, token)
            width += _scalar_width(token.scalar, metrics)
            previous_visible = token
        if width + _caret_padding(token, renderer) <= max_pixels:
            if token.offset in line_boundaries:
                return payload, None
            continue
        if last_safe is None:
            raise TextWidthContractError(
                f"{source_name}: unbreakable rendered span exceeds {max_pixels}px"
            )
        offset, _ = last_safe
        # The contract inserts an existing engine control and nothing else:
        # even a wrap-adjacent ASCII space is retained byte-for-byte.
        return _encode_inserted_newline(payload, offset), offset
    return payload, None


def insert_safe_line_breaks(
    payload: bytes,
    *,
    metrics: WidthMetrics,
    max_pixels: int,
    source_name: str,
    renderer: TextRenderer = TextRenderer.TEXT,
) -> Tuple[bytes, int]:
    """Wrap every physical rendering line without modifying authored controls."""

    if not payload or payload[-1] != 0:
        raise TextWidthContractError(f"{source_name}: payload must be NUL terminated")
    inserted = 0
    cursor = 0
    while cursor < len(payload) - 1:
        boundaries = frozenset(
            offset - cursor
            for offset in _renderer_line_boundaries(
                _tokenize_payload(payload, source_name=source_name), renderer,
            )
            if offset >= cursor
        )
        suffix = payload[cursor:]
        rewritten, offset = _wrap_one_line(
            suffix,
            metrics=metrics,
            max_pixels=max_pixels,
            source_name=source_name,
            renderer=renderer,
            line_boundaries=boundaries,
        )
        if offset is not None:
            payload = payload[:cursor] + rewritten
            inserted += 1
            cursor += offset + 1
            continue
        tokens = _tokenize_payload(suffix, source_name=source_name)
        boundary = next(
            (token for token in tokens if token.offset in boundaries), None,
        )
        if boundary is None or _ends_rendering(boundary, renderer):
            break
        cursor += boundary.offset + boundary.length
    return payload, inserted


def validate_payload_width(
    payload: bytes,
    *,
    metrics: WidthMetrics,
    max_pixels: int | None,
    source_name: str,
    renderer: TextRenderer = TextRenderer.TEXT,
) -> Dict[str, int]:
    """Reject any control-delimited rendered line exceeding its VWF limit."""

    return validate_token_widths(
        _tokenize_payload(payload, source_name=source_name),
        metrics=metrics, max_pixels=max_pixels, source_name=source_name, renderer=renderer,
    )


def validate_token_widths(
    tokens: Sequence[StreamToken],
    *,
    metrics: WidthMetrics,
    max_pixels: int | None,
    source_name: str,
    renderer: TextRenderer,
) -> Dict[str, int]:
    boundaries = _renderer_line_boundaries(tokens, renderer)
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
        padding = _caret_padding(token, renderer)
        if padding:
            saw_visible = True
        if not padding and token.offset not in boundaries:
            continue
        measured_width = width + padding
        if saw_visible:
            max_width = max(max_width, measured_width)
            if max_pixels is not None and measured_width > max_pixels:
                raise TextWidthContractError(
                    f"{source_name}: rendered line is {measured_width}px, exceeding "
                    f"{max_pixels}px"
                )
        if token.offset not in boundaries:
            continue
        if saw_visible:
            line_count += 1
        width = 0
        saw_visible = False
        if _ends_rendering(token, renderer):
            break
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


def _direct_talk_target_ids(
    repo_root: Path, target_count: int, message_ids: Mapping[str, int] | None = None,
) -> Mapping[int, Tuple[str, ...]]:
    """Resolve literal and table-driven non-event talk-text callers."""

    from .structural_completion import _iter_named_calls, _mask_c_comments_and_strings

    found: Dict[int, set[str]] = {}
    message_ids = {} if message_ids is None else message_ids

    def add(value: int, reason: str) -> None:
        if value < target_count:
            found.setdefault(value, set()).add(reason)

    arena = _mask_c_comments_and_strings(
        (repo_root / "src/uiarena.c").read_text(encoding="utf-8"),
    )
    for value in re.findall(
        r"\bStartArenaDialogue\s*\(\s*(0x[0-9A-Fa-f]+|\d+)", arena
    ):
        add(int(value, 0), "arena direct talk consumer")

    shop = _mask_c_comments_and_strings(
        (repo_root / "src/bmshop.c").read_text(encoding="utf-8"),
    )
    offsets = [
        int(value, 0)
        for value in re.findall(
            r"\[SHOP_TYPE_[A-Z_]+\]\s*=\s*(0x[0-9A-Fa-f]+|\d+)", shop
        )
    ]
    shop_starts = re.findall(
        r"\bStartShopDialogue\s*\(\s*(0x[0-9A-Fa-f]+|\d+)", shop,
    )
    if shop_starts and not offsets:
        raise TextWidthContractError(f"{repo_root}/src/bmshop.c: shop offset table is absent")
    for value in shop_starts:
        for offset in offsets:
            add(int(value, 0) + offset, "shop table-driven talk consumer")
    for path in sorted((repo_root / "src").glob("*.c")):
        source = path.read_text(encoding="utf-8")
        if not any(name in source for name in ("StartTalkMsg", "StartTalkExt")):
            continue
        source = _mask_c_comments_and_strings(source)
        for kind, _, _, arguments in _iter_named_calls(
            source, ("StartTalkMsg", "StartTalkMsgExt", "StartTalkExt"),
        ):
            if len(arguments) < 3:
                continue
            operands = (
                [values[0] for _, _, _, values in _iter_named_calls(
                    arguments[2], ("GetStringFromIndex",),
                ) if len(values) == 1]
                if kind == "StartTalkExt" else [arguments[2]]
            )
            for operand in operands:
                if re.fullmatch(r"0x[0-9A-Fa-f]+|\d+", operand):
                    target = int(operand, 0)
                elif operand in message_ids:
                    target = message_ids[operand]
                else:
                    continue
                add(target, f"{kind} message consumer in {path.name}")
    return {target: tuple(sorted(reasons)) for target, reasons in sorted(found.items())}


def _subtitle_help_target_ids(repo_root: Path, target_count: int) -> Mapping[int, str]:
    found: Dict[int, str] = {}
    for path in sorted((repo_root / "src").glob("*.c")):
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


def _description_target_ids(repo_root: Path, target_count: int) -> frozenset[int]:
    found = set()
    for relative in ("src/data_characters.c", "src/data_classes.c", "src/data_items.c"):
        source = (repo_root / relative).read_text(encoding="utf-8")
        found.update(
            int(value, 0)
            for value in re.findall(
                r"\.(?:descTextId|useDescTextId)\s*=\s*(0x[0-9A-Fa-f]+|\d+)", source,
            )
            if int(value, 0) < target_count
        )
    return frozenset(found)


def classify_targets(
    repo_root: Path,
    *,
    target_count: int,
    registry: WidthRegistry,
    event_root: Path | None = None,
    message_ids: Mapping[str, int] | None = None,
    fixed_label_ids: Iterable[int] | None = None,
    target_payloads: Sequence[bytes] | None = None,
) -> Tuple[Mapping[int, Tuple[RendererRequirement, ...]], Dict[str, int]]:
    """Retain every known native renderer/resource requirement for each target."""

    from scripts.localization.game_catalog.control_streams import is_dynamic_event_message

    if target_payloads is not None and len(target_payloads) != target_count:
        raise TextWidthContractError("renderer payloads differ from the active target set")
    uses: Dict[int, Dict[Tuple[str, TextRenderer, str, bool], set[str]]] = {
        target: {} for target in range(target_count)
    }

    def add(
        target: int, context: str, renderer: TextRenderer, source: str, *,
        style: str | None = None, measure_only: bool = False,
    ) -> None:
        if target not in uses:
            raise TextWidthContractError(
                f"{source}: target 0x{target:04X} is outside the active target set"
            )
        key = (context, renderer, registry.contexts[context].style if style is None else style,
               measure_only)
        uses[target].setdefault(key, set()).add(source)

    for target_id in _ending_target_ids(repo_root) & uses.keys():
        add(target_id, registry.ending_context, TextRenderer.TEXT,
            "ending-details source allocation; exact geometry belongs to ending_layout_metrics")
    label_ids = (
        _fixed_label_target_ids(repo_root) & uses.keys()
        if fixed_label_ids is None else fixed_label_ids
    )
    for target_id in label_ids:
        add(target_id, registry.fixed_context, TextRenderer.TEXT,
            "fixed-width label allocation; surface aliases belong to fixed_width_label_metrics")
    for target_id in _description_target_ids(repo_root, target_count):
        add(target_id, registry.default_context, TextRenderer.TEXT,
            "data description UI/help consumer")
        add(target_id, registry.talk_context, TextRenderer.TALK,
            "data description talk/text consumer")
    for target_id, models in _event_models(
        repo_root / "src/events" if event_root is None else event_root,
        message_ids,
    ).items():
        for model in models:
            if is_dynamic_event_message(model.start_kind, target_id):
                continue
            source = (
                f"event mode {model.text_mode.value}: {model.start_kind}@"
                f"{Path(os.path.relpath(model.source_path, repo_root)).as_posix()}"
            )
            if model.text_mode.value >= 3:
                add(target_id, registry.box_context, TextRenderer.BOX, source)
                add(target_id, registry.box_context, TextRenderer.BOX_MEASURE,
                    source + " Talk-font box measurement", measure_only=True)
                add(target_id, registry.box_context, TextRenderer.BOX_MEASURE,
                    source + " inherited-font premeasurement", style="system", measure_only=True)
            elif model.text_mode.value == 2:
                add(target_id, registry.cg_context, TextRenderer.CG, source)
                add(target_id, registry.cg_context, TextRenderer.CG_MEASURE,
                    source + " Talk-font box measurement", measure_only=True)
            else:
                add(target_id, registry.talk_context, TextRenderer.TALK, source)
    for target_id, reasons in _direct_talk_target_ids(
        repo_root, target_count, message_ids,
    ).items():
        for reason in reasons:
            add(target_id, registry.talk_context, TextRenderer.TALK, reason)
    for target_id, reason in _subtitle_help_target_ids(repo_root, target_count).items():
        add(target_id, registry.subtitle_context, TextRenderer.SUBTITLE, reason)
    if target_payloads is not None:
        for target_id, payload in enumerate(target_payloads):
            if any(key[1] in (TextRenderer.BOX, TextRenderer.CG) for key in uses[target_id]):
                continue
            if any(
                token.kind == "control" and token.control is not None
                and 0x08 <= token.control <= 0x11
                for token in _tokenize_payload(payload, source_name=f"target 0x{target_id:04X}")
            ):
                add(target_id, registry.talk_context, TextRenderer.TALK,
                    "talk control stream (face/speaker geometry)")
    for target_id, requirements in uses.items():
        if not requirements:
            add(target_id, registry.default_context, TextRenderer.TEXT,
                "default 30-tile system Text field")
    assigned = {
        target_id: tuple(
            RendererRequirement(context, renderer, style, tuple(sorted(sources)), measure_only)
            for (context, renderer, style, measure_only), sources in sorted(requirements.items())
        )
        for target_id, requirements in uses.items()
    }
    counts: Dict[str, int] = {name: 0 for name in registry.contexts}
    for requirements in assigned.values():
        for context in {requirement.context for requirement in requirements}:
            counts[context] += 1
    return assigned, counts


def apply_width_contract(
    *,
    repo_root: Path,
    locale: str,
    target_payloads: Sequence[bytes | None],
    registry: WidthRegistry,
    fallback_payloads: Sequence[bytes] | None = None,
    event_root: Path | None = None,
    message_ids: Mapping[str, int] | None = None,
    fixed_label_ids: Iterable[int] | None = None,
    font_metrics: SelectedFontMetrics | None = None,
) -> Tuple[Tuple[bytes | None, ...], Dict[str, Any]]:
    """Wrap then validate a complete locale payload table against all contexts."""

    effective = []
    for target_id, payload in enumerate(target_payloads):
        if payload is None:
            if fallback_payloads is None or len(fallback_payloads) != len(target_payloads):
                raise TextWidthContractError("missing effective English fallback payloads")
            payload = fallback_payloads[target_id]
        effective.append(payload)
    assignments, counts = classify_targets(
        repo_root,
        target_count=len(target_payloads),
        registry=registry,
        event_root=event_root,
        message_ids=message_ids,
        fixed_label_ids=fixed_label_ids,
        target_payloads=effective,
    )
    metrics_by_style = {
        style: load_width_metrics(
            repo_root, locale=locale, style=style, font_metrics=font_metrics,
        )
        for style in ("system", "talk")
    }
    output = []
    inserted_total = 0
    max_width = 0
    line_total = 0
    records = []
    for target_id, payload in enumerate(target_payloads):
        fallback = payload is None
        requirements = assignments[target_id]
        rewritten = effective[target_id]
        inserted = 0
        may_wrap = not fallback and all(
            registry.contexts[item.context].allow_generated_wrap
            for item in requirements if not item.measure_only
        )
        if may_wrap:
            for item in requirements:
                if item.measure_only:
                    continue
                context = registry.contexts[item.context]
                rewritten, added = insert_safe_line_breaks(
                    rewritten, metrics=metrics_by_style[item.style],
                    max_pixels=context.max_pixels,
                    source_name=f"{locale} target 0x{target_id:04X} ({item.context})",
                    renderer=item.renderer,
                )
                inserted += added
        observations = []
        target_width = 0
        for item in requirements:
            context = registry.contexts[item.context]
            if item.renderer == TextRenderer.SUBTITLE:
                stats = {"line_count": 0, "max_line_width": 0}
            else:
                stats = validate_payload_width(
                    rewritten, metrics=metrics_by_style[item.style],
                    max_pixels=None if item.measure_only else context.max_pixels,
                    source_name=f"{locale} target 0x{target_id:04X} "
                                f"({item.context}/{item.style}/{item.renderer.value})",
                    renderer=item.renderer,
                )
            observations.append({
                "context": item.context, "renderer": item.renderer.value,
                "style": item.style, "measure_only": item.measure_only,
                "sources": list(item.sources), **stats,
            })
            if not item.measure_only:
                target_width = max(target_width, stats["max_line_width"])
                line_total += stats["line_count"]
        inserted_total += inserted
        max_width = max(max_width, target_width)
        output.append(None if fallback else rewritten)
        records.append(
            {
                "contexts": sorted({item.context for item in requirements}),
                "generated_line_break_count": inserted,
                "max_line_width": target_width,
                "requirements": observations,
                "target_id": f"0x{target_id:04X}",
            }
        )
    return tuple(output), {
        "context_counts": counts,
        "generated_line_break_count": inserted_total,
        "line_count": line_total,
        "locale": locale,
        "max_line_width": max_width,
        "records": records,
        "registry_sha256": registry.schema_sha256,
        "schema_version": 2,
        "target_count": len(target_payloads),
        "unclassified_target_count": 0,
        "unclassified_targets": [],
    }
