"""Bounded validation and regional operand composition for game text."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

CONTROL_DOMAIN_FE8J = "fe8j"
CONTROL_DOMAIN_FE8U = "fe8u"
DEFAULT_PORTRAIT_MAP_PATH = Path(
    "texts/locales/mapping/fe8j_to_fe8u_portrait_operands.json"
)
DEFAULT_EVENT_ROOT = Path("src/events")
AUDITED_MOUTH_BALANCE_TARGETS = frozenset(
    (0x0BA9, 0x0BC0, 0x0BFF, 0x0C00, 0x0C10, 0x0CB6, 0x0CB7, 0x0CB8)
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
        if first == 0x81 and offset + 2 <= limit - 1 and payload[offset + 1] == 0x40:
            tokens.append(StreamToken("scalar", offset, 2, scalar=0x3000))
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


def _speakers_after_break_talk(
    tokens: Sequence[StreamToken],
) -> Tuple[Optional[int], ...]:
    speakers = []
    for index, token in enumerate(tokens):
        if token.kind != "extended" or token.scalar != 0x04:
            continue
        speaker = None
        for following in tokens[index + 1 :]:
            if (
                following.kind == "control"
                and following.control in (0x01, 0x02, 0x03, 0x1F)
            ):
                continue
            if (
                following.kind == "control"
                and following.control is not None
                and 0x08 <= following.control <= 0x0D
            ):
                speaker = following.control
            break
        speakers.append(speaker)
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
                and token.control in (
                    0x03,
                    0x08,
                    0x09,
                    0x0A,
                    0x0B,
                    0x0C,
                    0x0D,
                    0x10,
                    0x11,
                    0x15,
                )
            )
        )
        if active_offset is not None and boundary:
            raise ControlStreamError(
                f"{source_name}: ToggleMouthMove at byte {active_offset} "
                f"is not paired before byte {token.offset}"
            )


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
    portrait_map: PortraitOperandMap,
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
    if faces != english_faces:
        raise ControlStreamError(
            f"{name}: FID operands do not match FE8U target context: "
            f"{tuple(f'0x{x:04X}' for x in faces)} != "
            f"{tuple(f'0x{x:04X}' for x in english_faces)}"
        )
    speakers_after_break = _speakers_after_break_talk(tokens)
    english_speakers_after_break = _speakers_after_break_talk(english_tokens)
    if speakers_after_break != english_speakers_after_break:
        raise ControlStreamError(
            f"{name}: speakers after BreakTalk do not match FE8U target: "
            f"{speakers_after_break} != {english_speakers_after_break}"
        )
    if target_id in AUDITED_MOUTH_BALANCE_TARGETS:
        validate_mouth_toggle_balance(payload, source_name=name)
    localized_double_nl = _consecutive_newline_count(tokens)
    english_double_nl = _consecutive_newline_count(english_tokens)
    if localized_double_nl > english_double_nl:
        raise ControlStreamError(
            f"{name}: {localized_double_nl} consecutive-NL pair(s) exceed "
            f"English target context count {english_double_nl}"
        )
    return {
        "break_talk_count": _break_talk_count(tokens),
        "face_operand_count": len(faces),
        "token_count": len(tokens),
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
