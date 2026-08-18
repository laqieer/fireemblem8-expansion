"""Bounded validation and regional operand composition for game text."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

from scripts.localization.game_locales.ending_metrics import (
    EndingLayoutError,
    _ascii_widths,
    _cjk_widths,
)
from scripts.localization.legacy_spacing import (
    LEGACY_SJIS_SPACE_BYTES,
    LEGACY_SJIS_SPACE_SCALAR,
    LEGACY_SJIS_SPACE_WIDTH,
)

CONTROL_DOMAIN_FE8J = "fe8j"
CONTROL_DOMAIN_FE8U = "fe8u"
DEFAULT_PORTRAIT_MAP_PATH = Path(
    "texts/locales/mapping/fe8j_to_fe8u_portrait_operands.json"
)
DEFAULT_EVENT_ROOT = Path("src/events")
TALK_LINE_WIDTH_PIXELS = 240
_TALK_FACE_CONTROLS = frozenset(range(0x08, 0x12))
_TALK_SPEAKER_CONTROLS = frozenset(range(0x08, 0x10))
_TALK_SPEAKER_RESET_CONTROLS = frozenset((0x11, 0x14, 0x15))
_TALK_TRANSPARENT_EXTENDED_CONTROLS = (
    frozenset(range(0x0A, 0x12))
    | frozenset(range(0x16, 0x1A))
    | frozenset(range(0x1B, 0x20))
    | frozenset((0x21, 0x25))
)
_TALK_LINE_BOUNDARY_CONTROLS = _TALK_FACE_CONTROLS | frozenset(
    (0x01, 0x02, 0x03, 0x14, 0x15)
)
_MOUTH_TOPOLOGY_BOUNDARY_CONTROLS = _TALK_FACE_CONTROLS | frozenset(
    (0x03, 0x14, 0x15)
)

_MESSAGE_START_RE = re.compile(
    r"\b(?P<kind>TEXTSHOW|WM_TEXT|EvtTextShow2)\s*\(\s*"
    r"(?P<target>0x[0-9A-Fa-f]+)"
)
_LABEL_RE = re.compile(r"^\s*LABEL\s*\(\s*(0x[0-9A-Fa-f]+|\d+)\s*\)")
_GOTO_RE = re.compile(r"^\s*GOTO\s*\(\s*(0x[0-9A-Fa-f]+|\d+)\s*\)")
_BRANCH_RE = re.compile(
    r"^\s*(?:BEQ|BNE|BGE|BGT|BLE|BLT|BEQ_EVFLAG|BNE_EVFLAG)"
    r"\s*\(\s*(0x[0-9A-Fa-f]+|\d+)"
)
_ARRAY_START_RE = re.compile(
    r"^\s*(?:CONST_DATA\s+)?(?:EventListScr|EventScr)\s+\w+\s*\[\]\s*=\s*\{"
)


class ControlStreamError(ValueError):
    """Raised when a final localized payload is unsafe or semantically invalid."""


@dataclass(frozen=True)
class StreamToken:
    kind: str
    offset: int
    length: int
    control: Optional[int] = None
    argument: Optional[int] = None
    scalar: Optional[int] = None


@dataclass(frozen=True)
class PortraitOperandMap:
    path: Path
    source_to_target: Mapping[int, int]
    valid_target_operands: Set[int]
    target_overrides: Mapping[Tuple[int, int], int]
    expected_affected_target_count: int


@dataclass(frozen=True)
class EventContinuationModel:
    target_id: int
    start_kind: str
    continuation_count: int
    source_path: str


@dataclass(frozen=True)
class TalkFontMetrics:
    locale: str
    ascii_widths: Mapping[int, int]
    cjk_widths: Mapping[int, int]
    allocation_pixels: int = TALK_LINE_WIDTH_PIXELS


def _require_hex_u16(value: object, *, field: str) -> int:
    if not isinstance(value, str) or not re.fullmatch(r"0x[0-9A-F]{4}", value):
        raise ControlStreamError(f"{field} must use canonical 0xNNNN form")
    return int(value, 16)


def load_portrait_operand_map(path: Path = DEFAULT_PORTRAIT_MAP_PATH) -> PortraitOperandMap:
    path = Path(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ControlStreamError(f"{path}: portrait operand map is unavailable") from error
    if (
        not isinstance(data, dict)
        or data.get("kind") != "fe8j-to-fe8u-portrait-operands"
        or data.get("schema_version") != 1
        or not isinstance(data.get("entries"), list)
        or not isinstance(data.get("target_overrides"), list)
        or not isinstance(data.get("expected"), dict)
    ):
        raise ControlStreamError(f"{path}: portrait operand map schema is invalid")
    derivation = data.get("derivation")
    if (
        not isinstance(derivation, dict)
        or derivation.get("no_numeric_offset_assumption") is not True
        or not isinstance(derivation.get("method"), str)
        or not derivation["method"]
        or not isinstance(derivation.get("sources"), dict)
        or set(derivation["sources"]) != {"fe8j", "fe8u"}
    ):
        raise ControlStreamError(f"{path}: portrait derivation evidence is invalid")
    for region, source in derivation["sources"].items():
        if (
            not isinstance(source, dict)
            or not isinstance(source.get("path"), str)
            or not source["path"]
            or not isinstance(source.get("revision"), str)
            or not re.fullmatch(r"[0-9a-f]{40}", source["revision"])
            or not isinstance(source.get("sha256"), str)
            or not re.fullmatch(r"[0-9a-f]{64}", source["sha256"])
        ):
            raise ControlStreamError(f"{path}: {region} portrait source pin is invalid")
    fe8u_source = Path(derivation["sources"]["fe8u"]["path"])
    if fe8u_source.is_file() and (
        hashlib.sha256(fe8u_source.read_bytes()).hexdigest()
        != derivation["sources"]["fe8u"]["sha256"]
    ):
        raise ControlStreamError(f"{path}: pinned FE8U portrait table changed")

    source_to_target: Dict[int, int] = {}
    valid_target_operands: Set[int] = set()
    for index, entry in enumerate(data["entries"]):
        if not isinstance(entry, dict):
            raise ControlStreamError(f"{path}: entries[{index}] must be an object")
        if (
            not isinstance(entry.get("name"), str)
            or not entry["name"]
            or not isinstance(entry.get("target_name"), str)
            or not entry["target_name"]
            or entry.get("match_kind") not in (
                "engine-sentinel",
                "named-duplicate-resolution",
                "unique-named-signature",
            )
        ):
            raise ControlStreamError(f"{path}: entries[{index}] evidence is malformed")
        source = _require_hex_u16(
            entry.get("source_operand"), field=f"entries[{index}].source_operand"
        )
        target = _require_hex_u16(
            entry.get("target_operand"), field=f"entries[{index}].target_operand"
        )
        if source in source_to_target:
            raise ControlStreamError(f"{path}: duplicate source operand 0x{source:04X}")
        source_to_target[source] = target
        valid_target_operands.add(target)
    if source_to_target.get(0xFFFF) != 0xFFFF:
        raise ControlStreamError(f"{path}: FID_Active must map 0xFFFF to itself")

    target_overrides: Dict[Tuple[int, int], int] = {}
    for index, override in enumerate(data["target_overrides"]):
        if not isinstance(override, dict):
            raise ControlStreamError(
                f"{path}: target_overrides[{index}] must be an object"
            )
        target_id = _require_hex_u16(
            override.get("target_id"), field=f"target_overrides[{index}].target_id"
        )
        source = _require_hex_u16(
            override.get("source_operand"),
            field=f"target_overrides[{index}].source_operand",
        )
        target = _require_hex_u16(
            override.get("target_operand"),
            field=f"target_overrides[{index}].target_operand",
        )
        mapped = _require_hex_u16(
            override.get("mapped_operand"),
            field=f"target_overrides[{index}].mapped_operand",
        )
        key = (target_id, source)
        if key in target_overrides:
            raise ControlStreamError(
                f"{path}: duplicate target override for 0x{target_id:04X}/"
                f"0x{source:04X}"
            )
        if (
            source not in source_to_target
            or source_to_target[source] != mapped
            or target not in valid_target_operands
        ):
            raise ControlStreamError(
                f"{path}: target override operands are outside the pinned map"
            )
        target_overrides[key] = target

    affected = data["expected"].get("affected_target_count")
    if isinstance(affected, bool) or not isinstance(affected, int) or affected < 0:
        raise ControlStreamError(
            f"{path}: expected.affected_target_count must be non-negative"
        )
    if data["expected"].get("entry_count") != len(data["entries"]):
        raise ControlStreamError(f"{path}: expected.entry_count does not match entries")
    return PortraitOperandMap(
        path=path,
        source_to_target=source_to_target,
        valid_target_operands=valid_target_operands,
        target_overrides=target_overrides,
        expected_affected_target_count=affected,
    )


def tokenize_payload(payload: bytes, *, source_name: str) -> Tuple[StreamToken, ...]:
    """Tokenize one NUL-terminated UTF-8/control stream without over-reading."""

    if not isinstance(payload, bytes) or not payload or payload[-1] != 0:
        raise ControlStreamError(f"{source_name}: payload must end in one NUL")
    tokens: List[StreamToken] = []
    offset = 0
    limit = len(payload)
    while offset < limit:
        first = payload[offset]
        if first == 0:
            if offset != limit - 1:
                raise ControlStreamError(f"{source_name}: embedded NUL at byte {offset}")
            tokens.append(StreamToken("end", offset, 1))
            break
        if first < 0x20:
            if first == 0x10:
                if offset + 3 > limit - 1:
                    raise ControlStreamError(
                        f"{source_name}: truncated LoadFace at byte {offset}"
                    )
                argument = payload[offset + 1] | (payload[offset + 2] << 8)
                tokens.append(
                    StreamToken(
                        "control", offset, 3, control=first, argument=argument
                    )
                )
                offset += 3
                continue
            tokens.append(StreamToken("control", offset, 1, control=first))
            offset += 1
            continue
        if first < 0x80:
            tokens.append(StreamToken("scalar", offset, 1, scalar=first))
            offset += 1
            continue
        if first == 0x80:
            if offset + 2 > limit - 1:
                raise ControlStreamError(
                    f"{source_name}: truncated extended control at byte {offset}"
                )
            payload_byte = payload[offset + 1]
            length = 3 if payload_byte <= 3 else 2
            if offset + length > limit - 1:
                raise ControlStreamError(
                    f"{source_name}: truncated extended argument at byte {offset}"
                )
            argument = payload[offset + 2] if length == 3 else None
            tokens.append(
                StreamToken(
                    "extended",
                    offset,
                    length,
                    control=first,
                    argument=argument,
                    scalar=payload_byte,
                )
            )
            offset += length
            continue
        if (
            first == LEGACY_SJIS_SPACE_BYTES[0]
            and offset + 2 <= limit - 1
            and payload[offset + 1] == LEGACY_SJIS_SPACE_BYTES[1]
        ):
            tokens.append(
                StreamToken(
                    "scalar",
                    offset,
                    2,
                    scalar=LEGACY_SJIS_SPACE_SCALAR,
                )
            )
            offset += 2
            continue

        if 0xC2 <= first <= 0xDF:
            length = 2
        elif 0xE0 <= first <= 0xEF:
            length = 3
        elif 0xF0 <= first <= 0xF4:
            length = 4
        else:
            raise ControlStreamError(
                f"{source_name}: invalid UTF-8 lead byte 0x{first:02X} at {offset}"
            )
        if offset + length > limit - 1:
            raise ControlStreamError(
                f"{source_name}: truncated UTF-8 scalar at byte {offset}"
            )
        chunk = payload[offset : offset + length]
        try:
            decoded = chunk.decode("utf-8")
        except UnicodeDecodeError as error:
            raise ControlStreamError(
                f"{source_name}: invalid UTF-8 scalar at byte {offset}"
            ) from error
        if len(decoded) != 1:
            raise ControlStreamError(
                f"{source_name}: UTF-8 token at byte {offset} is not one scalar"
            )
        tokens.append(StreamToken("scalar", offset, length, scalar=ord(decoded)))
        offset += length
    return tuple(tokens)


def _face_operands(tokens: Iterable[StreamToken]) -> Tuple[int, ...]:
    return tuple(
        token.argument
        for token in tokens
        if token.kind == "control" and token.control == 0x10
        and token.argument is not None
    )


def _break_talk_count(tokens: Iterable[StreamToken]) -> int:
    return sum(
        token.kind == "extended" and token.scalar == 0x04
        for token in tokens
    )


def _consecutive_newline_count(tokens: Sequence[StreamToken]) -> int:
    controls = [
        token.control
        for token in tokens
        if token.kind in ("control", "extended", "scalar")
    ]
    return sum(left == 0x01 and right == 0x01 for left, right in zip(controls, controls[1:]))


def _continuation_speakers(
    tokens: Sequence[StreamToken],
) -> Tuple[Optional[int], ...]:
    speakers: List[Optional[int]] = []
    active_slot = None
    pending_continuation = False
    for token in tokens:
        if token.kind == "extended" and token.scalar == 0x04:
            if pending_continuation:
                speakers.append(None)
            pending_continuation = True
            continue

        if token.kind == "control":
            if token.control in _TALK_SPEAKER_CONTROLS:
                active_slot = token.control
            elif token.control in _TALK_SPEAKER_RESET_CONTROLS:
                active_slot = None
            continue

        if (
            token.kind == "extended"
            and token.scalar in _TALK_TRANSPARENT_EXTENDED_CONTROLS
        ):
            continue

        if token.kind == "end":
            if pending_continuation:
                speakers.append(None)
            break

        if pending_continuation:
            speakers.append(active_slot)
            pending_continuation = False

    return tuple(speakers)


def validate_mouth_toggle_balance(payload: bytes, *, source_name: str) -> None:
    tokens = tokenize_payload(payload, source_name=source_name)
    active_offset = None
    for token in tokens:
        if token.kind == "control" and token.control == 0x16:
            active_offset = None if active_offset is not None else token.offset
            continue
        boundary = (
            token.kind == "end"
            or (
                token.kind == "extended"
                and token.scalar == 0x04
            )
            or (
                token.kind == "control"
                and token.control in _MOUTH_TOPOLOGY_BOUNDARY_CONTROLS
            )
        )
        if active_offset is not None and boundary:
            raise ControlStreamError(
                f"{source_name}: ToggleMouthMove at byte {active_offset} "
                f"is not paired before byte {token.offset}"
            )


def load_talk_font_metrics(
    locale: str,
    *,
    repo_root: Path = Path("."),
) -> TalkFontMetrics:
    try:
        ascii_widths = _ascii_widths(repo_root, style="talk")
        if locale in ("fr", "de", "es", "it"):
            codepoint_data = (
                repo_root / "graphics/fonts/eu/eu.talk.codepoints.u32le"
            ).read_bytes()
            width_data = (
                repo_root / "graphics/fonts/eu/eu.talk.widths.u8"
            ).read_bytes()
            if len(codepoint_data) != len(width_data) * 4:
                raise ControlStreamError(
                    f"{locale}: EU talk-font codepoint/width count mismatch"
                )
            cjk_widths = {
                int.from_bytes(
                    codepoint_data[index * 4 : index * 4 + 4], "little"
                ): width
                for index, width in enumerate(width_data)
            }
        else:
            cjk_widths, _ = _cjk_widths(repo_root, locale, style="talk")
    except (EndingLayoutError, OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ControlStreamError(
            f"{locale}: runtime system-font metrics are unavailable"
        ) from error
    return TalkFontMetrics(
        locale=locale,
        ascii_widths=ascii_widths,
        cjk_widths=cjk_widths,
    )


def _is_talk_payload(tokens: Sequence[StreamToken]) -> bool:
    return any(
        token.kind == "control" and token.control in _TALK_FACE_CONTROLS
        for token in tokens
    )


def _scalar_width(scalar: int, *, metrics: TalkFontMetrics) -> int:
    if scalar < 0x20:
        return 0
    if scalar == LEGACY_SJIS_SPACE_SCALAR:
        return LEGACY_SJIS_SPACE_WIDTH
    if scalar < 0x80:
        try:
            return metrics.ascii_widths[scalar]
        except KeyError as error:
            raise ControlStreamError(
                f"{metrics.locale}: ASCII U+{scalar:04X} is absent from the system font"
            ) from error
    if scalar == 0x3000:
        return 16
    try:
        return metrics.cjk_widths[scalar]
    except KeyError as error:
        raise ControlStreamError(
            f"{metrics.locale}: U+{scalar:04X} is absent from committed system metrics"
        ) from error


def validate_talk_line_widths(
    tokens: Sequence[StreamToken],
    *,
    source_name: str,
    metrics: TalkFontMetrics,
) -> Dict[str, int]:
    if not _is_talk_payload(tokens):
        return {
            "talk_line_count": 0,
            "talk_payload_count": 0,
            "max_talk_line_width": 0,
        }

    line_width = 0
    line_scalars: List[int] = []
    line_count = 0
    max_line_width = 0
    for token in tokens:
        if token.kind == "scalar":
            assert token.scalar is not None
            line_width += _scalar_width(token.scalar, metrics=metrics)
            line_scalars.append(token.scalar)
            continue
        boundary = (
            token.kind == "end"
            or (token.kind == "extended" and token.scalar == 0x04)
            or (
                token.kind == "control"
                and token.control in _TALK_LINE_BOUNDARY_CONTROLS
            )
        )
        if not boundary:
            continue
        if line_scalars:
            line_count += 1
            max_line_width = max(max_line_width, line_width)
            if line_width > metrics.allocation_pixels:
                preview = "".join(
                    " " if scalar == LEGACY_SJIS_SPACE_SCALAR else chr(scalar)
                    for scalar in line_scalars
                )
                raise ControlStreamError(
                    f"{source_name}: talk line {preview!r} is {line_width}px, "
                    f"exceeding the {metrics.allocation_pixels}px allocation"
                )
        line_width = 0
        line_scalars = []

    return {
        "talk_line_count": line_count,
        "talk_payload_count": 1,
        "max_talk_line_width": max_line_width,
    }


def _mouth_topology(
    tokens: Sequence[StreamToken],
) -> Tuple[Tuple[Tuple[str, int], ...], Tuple[int, ...]]:
    boundaries = []
    toggle_counts = []
    toggles = 0
    for token in tokens:
        if token.kind == "control" and token.control == 0x16:
            toggles += 1
            continue
        boundary = None
        if token.kind == "end":
            boundary = ("end", 0)
        elif token.kind == "extended" and token.scalar == 0x04:
            boundary = ("extended", 0x04)
        elif (
            token.kind == "control"
            and token.control in _MOUTH_TOPOLOGY_BOUNDARY_CONTROLS
        ):
            assert token.control is not None
            boundary = ("control", token.control)
        if boundary is not None:
            boundaries.append(boundary)
            toggle_counts.append(toggles)
            toggles = 0
    return tuple(boundaries), tuple(toggle_counts)


def remap_fe8j_portrait_operands(
    payload: bytes,
    *,
    target_id: int,
    portrait_map: PortraitOperandMap,
    source_name: str,
) -> Tuple[bytes, int]:
    tokens = tokenize_payload(payload, source_name=source_name)
    output = bytearray(payload)
    changed = 0
    for token in tokens:
        if token.kind != "control" or token.control != 0x10:
            continue
        assert token.argument is not None
        source_operand = token.argument
        try:
            target_operand = portrait_map.target_overrides.get(
                (target_id, source_operand),
                portrait_map.source_to_target[source_operand],
            )
        except KeyError as error:
            raise ControlStreamError(
                f"{source_name}: unmapped FE8J FID operand 0x{source_operand:04X}"
            ) from error
        if target_operand != source_operand:
            changed += 1
        output[token.offset + 1] = target_operand & 0xFF
        output[token.offset + 2] = target_operand >> 8
    return bytes(output), changed


def validate_final_payload(
    payload: bytes,
    *,
    english_payload: bytes,
    target_id: int,
    locale: str,
    control_domain: str,
    portrait_map: PortraitOperandMap,
    talk_metrics: TalkFontMetrics,
) -> Dict[str, int]:
    name = f"{locale} target 0x{target_id:04X}"
    tokens = tokenize_payload(payload, source_name=name)
    english_tokens = tokenize_payload(
        english_payload, source_name=f"English target 0x{target_id:04X}"
    )
    faces = _face_operands(tokens)
    english_faces = _face_operands(english_tokens)
    invalid_faces = [
        operand
        for operand in faces
        if operand not in portrait_map.valid_target_operands
    ]
    if invalid_faces:
        raise ControlStreamError(
            f"{name}: invalid FE8U FID operand(s) "
            + ", ".join(f"0x{operand:04X}" for operand in invalid_faces)
        )
    if locale in ("fr", "de", "es", "it"):
        face_index = 0
        for operand in faces:
            if (
                face_index < len(english_faces)
                and operand == english_faces[face_index]
            ):
                face_index += 1
        if face_index != len(english_faces):
            raise ControlStreamError(
                f"{name}: FE8U target FID operands are not preserved in order"
            )
    elif faces != english_faces:
        raise ControlStreamError(
            f"{name}: FID operands do not match FE8U target context: "
            f"{tuple(f'0x{x:04X}' for x in faces)} != "
            f"{tuple(f'0x{x:04X}' for x in english_faces)}"
        )
    speakers_after_break = _continuation_speakers(tokens)
    english_speakers_after_break = _continuation_speakers(english_tokens)
    if speakers_after_break != english_speakers_after_break:
        raise ControlStreamError(
            f"{name}: continuation speakers after BreakTalk do not match "
            "FE8U target: "
            f"{speakers_after_break} != {english_speakers_after_break}"
        )
    validate_mouth_toggle_balance(payload, source_name=name)
    mouth_topology_comparable = 0
    if control_domain == CONTROL_DOMAIN_FE8U and locale not in (
        "fr",
        "de",
        "es",
        "it",
    ):
        localized_topology = _mouth_topology(tokens)
        english_topology = _mouth_topology(english_tokens)
        if localized_topology[0] == english_topology[0]:
            mouth_topology_comparable = 1
            if localized_topology[1] != english_topology[1]:
                raise ControlStreamError(
                    f"{name}: ToggleMouthMove topology does not match the "
                    "FE8U target control structure"
                )
    localized_double_nl = _consecutive_newline_count(tokens)
    english_double_nl = _consecutive_newline_count(english_tokens)
    if (
        locale not in ("fr", "de", "es", "it")
        and localized_double_nl > english_double_nl
    ):
        raise ControlStreamError(
            f"{name}: {localized_double_nl} consecutive-NL pair(s) exceed "
            f"English target context count {english_double_nl}"
        )
    talk_stats = validate_talk_line_widths(
        tokens,
        source_name=name,
        metrics=talk_metrics,
    )
    return {
        "break_talk_count": _break_talk_count(tokens),
        "fe8u_mouth_topology_validated_payload_count": mouth_topology_comparable,
        "face_operand_count": len(faces),
        "mouth_balance_validated_payload_count": 1,
        "token_count": len(tokens),
        **talk_stats,
    }


def _event_arrays(event_root: Path) -> Iterable[Tuple[Path, List[str]]]:
    for path in sorted(Path(event_root).rglob("*")):
        if path.suffix not in (".c", ".h") or not path.is_file():
            continue
        lines = path.read_text(encoding="utf-8").splitlines()
        current: Optional[List[str]] = None
        for line in lines:
            if current is None:
                if _ARRAY_START_RE.search(line):
                    current = []
                continue
            if line.strip() == "};":
                yield path, current
                current = None
                continue
            current.append(line)


def _array_continuations(path: Path, lines: Sequence[str]) -> Iterable[EventContinuationModel]:
    labels = {}
    for index, line in enumerate(lines):
        match = _LABEL_RE.search(line)
        if match:
            labels[int(match.group(1), 0)] = index

    initial = (0, None, None, 0)
    stack = [initial]
    visited = set()
    results = set()
    max_steps = max(64, len(lines) * 32)
    steps = 0
    while stack:
        pc, target_id, start_kind, continuations = stack.pop()
        state = (pc, target_id, start_kind, continuations)
        if state in visited:
            continue
        visited.add(state)
        steps += 1
        if steps > max_steps:
            raise ControlStreamError(f"{path}: event continuation analysis exceeded bound")
        if pc >= len(lines):
            if target_id is not None and start_kind is not None:
                results.add((target_id, start_kind, continuations))
            continue
        line = lines[pc]
        message = _MESSAGE_START_RE.search(line)
        if message:
            if target_id is not None and start_kind is not None:
                results.add((target_id, start_kind, continuations))
            target_id = int(message.group("target"), 0)
            start_kind = message.group("kind")
            continuations = 0
        elif re.search(r"\bTEXTCONT\b", line) and target_id is not None:
            continuations += 1

        if re.search(r"\b(REMA|WM_REMOVETEXT|ENDA)\b", line):
            if target_id is not None and start_kind is not None:
                results.add((target_id, start_kind, continuations))
            continue

        goto = _GOTO_RE.search(line)
        if goto:
            destination = labels.get(int(goto.group(1), 0))
            if destination is not None:
                stack.append((destination + 1, target_id, start_kind, continuations))
            continue
        branch = _BRANCH_RE.search(line)
        if branch:
            destination = labels.get(int(branch.group(1), 0))
            if destination is not None:
                stack.append((destination + 1, target_id, start_kind, continuations))
        stack.append((pc + 1, target_id, start_kind, continuations))

    for target_id, start_kind, continuations in sorted(results):
        yield EventContinuationModel(
            target_id=target_id,
            start_kind=start_kind,
            continuation_count=continuations,
            source_path=path.as_posix(),
        )


def build_event_continuation_models(
    event_root: Path = DEFAULT_EVENT_ROOT,
) -> Mapping[int, Tuple[EventContinuationModel, ...]]:
    by_target: Dict[int, List[EventContinuationModel]] = {}
    for path, lines in _event_arrays(event_root):
        for model in _array_continuations(path, lines):
            by_target.setdefault(model.target_id, []).append(model)
    return {
        target_id: tuple(
            sorted(
                {
                    (model.start_kind, model.continuation_count, model.source_path): model
                    for model in models
                }.values(),
                key=lambda model: (
                    model.start_kind,
                    model.continuation_count,
                    model.source_path,
                ),
            )
        )
        for target_id, models in by_target.items()
    }


def validate_event_continuations(
    *,
    english_payloads: Sequence[bytes],
    localized_payloads: Mapping[str, Sequence[Optional[bytes]]],
    event_root: Path = DEFAULT_EVENT_ROOT,
) -> Dict[str, int]:
    models = build_event_continuation_models(event_root)
    checked = 0
    for target_id, target_models in sorted(models.items()):
        if target_id >= len(english_payloads):
            continue
        english_tokens = tokenize_payload(
            english_payloads[target_id],
            source_name=f"English target 0x{target_id:04X}",
        )
        english_breaks = _break_talk_count(english_tokens)
        for model in target_models:
            if model.start_kind == "WM_TEXT":
                compatible = english_breaks in (
                    model.continuation_count,
                    model.continuation_count + 1,
                )
            else:
                compatible = english_breaks == model.continuation_count
            if not compatible:
                raise ControlStreamError(
                    f"English target 0x{target_id:04X}: {english_breaks} "
                    f"BreakTalk(s) incompatible with {model.start_kind} "
                    f"TEXTCONT count {model.continuation_count} in "
                    f"{model.source_path}"
                )
        for locale, payloads in sorted(localized_payloads.items()):
            payload = payloads[target_id]
            if payload is None:
                continue
            localized_breaks = _break_talk_count(
                tokenize_payload(
                    payload, source_name=f"{locale} target 0x{target_id:04X}"
                )
            )
            if localized_breaks != english_breaks:
                raise ControlStreamError(
                    f"{locale} target 0x{target_id:04X}: {localized_breaks} "
                    f"BreakTalk(s) != FE8U target count {english_breaks}"
                )
        checked += 1
    return {
        "modeled_target_count": checked,
        "model_count": sum(len(value) for value in models.values()),
    }
