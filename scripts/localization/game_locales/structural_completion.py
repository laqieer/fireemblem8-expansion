"""Deterministic evidence for FE8U targets not covered by the release crosswalk."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from collections import Counter, defaultdict
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

from .crosswalk import (
    CrosswalkError,
    canonical_json_bytes,
)
from .mapping import format_message_id
from .parsers import FE8J_MAX_INDEXED_ID, parse_hash_indexed

COMPLETION_SCHEMA_VERSION = 2
COMPLETION_KIND = "fe8u-fe8j-structural-completion-evidence"
COMPLETION_AUTHORITY = "evidence-only"
CONFIDENCE_LEVELS = ("high", "reference")
PROTECTED_PATHS = (
    "texts/locales/mapping/fe8u_target_map.json",
    "texts/locales/mapping/fe8u_structural_evidence.json",
)

_REGION_ROW_RE = re.compile(
    r"^\s*([0-9A-Fa-f]+)\s+\{([UJ])\}(?:\s*//.*)?$"
)
_MSG_TOKEN_RE = re.compile(r"\bMSG_([0-9A-Fa-f]{1,4})\b")
_DEFINITION_RE = re.compile(
    r"\b([A-Za-z_][A-Za-z0-9_]*)\s*"
    r"(?:\[[^\]]*\]\s*(?:[^=;{}]*)=|\([^;{}]*\)\s*)\s*\{"
)
_EVENT_DEFINITION_RE = re.compile(
    r"\b(?:EventListScr|EventScr)\s+([A-Za-z_][A-Za-z0-9_]*)"
    r"\s*(?:\[[^\]]*\])?\s*(?:__attribute__\([^=;]*\)\s*)?=\s*\{"
)
_EVENT_CALL_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*\((.*)\)\s*$")
_EVENT_BARE_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*$")
_INTEGER_RE = re.compile(
    r"^(?:MSG_([0-9A-Fa-f]+)|0[xX]([0-9A-Fa-f]+)|([0-9]+))$"
)
_MESSAGE_FIELD_RE = re.compile(
    r"\.(nameTextId|descTextId|helpTextId|textId|msgId|messageId|itemName|"
    r"goalWindowTextId|goalTextId|labelTextId|optionTextId|msg)\s*=\s*"
    r"(MSG_[0-9A-Fa-f]{1,4}|0[xX][0-9A-Fa-f]+|[0-9]+)\b"
)
_MESSAGE_CALL_ARGUMENTS = {
    "GetStringFromIndex": (0,),
    "PutSioText": (0,),
    "StartCgText": (4,),
    "StartHelpBox": (2,),
    "StartPrepErrorHelpbox": (2,),
    "SetPrepScreenMenuItem": (3, 4),
}
_DIRECT_EVENT_MESSAGES = {
    "BROWNBOXTEXT": 0,
    "TEXTSHOW": 0,
    "Text": 0,
    "WM_TEXT": 0,
}
_EVENT_OPCODE_ALIASES = {
    "BACG": "SET_BACKGROUND",
    "CUMO_CHAR": "CURSOR_CHAR",
}
_IGNORED_MESSAGE_CONTROL_OPS = {
    "CLEAN",
    "CLEA",
    "CLEE",
    "CLEN",
    "CURE",
    "ENDFADE",
    "ENUN",
    "EvtBgmFadeIn",
    "EvtColorFadeSetup",
    "FADI",
    "FADU",
    "FAWI",
    "FAWU",
    "MURE",
    "MUSC",
    "MUSCFAST",
    "MUSCMID",
    "MUSCSLOW",
    "MUSS",
    "REMA",
    "REMOVEPORTRAITS",
    "SOUN",
    "STAL",
    "STAL2",
    "STARTFADE",
    "TEXTCONT",
    "TEXTEND",
    "TEXTSTART",
}
_SITE_KIND_PRIORITY = {
    "modeled-message-table": 0,
    "event-message-operand": 1,
    "message-struct-field": 2,
    "message-call-argument": 3,
    "msg-symbol-reference": 4,
    "msg-symbol-definition": 5,
}
_TYPED_SITE_KINDS = frozenset(_SITE_KIND_PRIORITY)


class StructuralCompletionError(CrosswalkError):
    """Raised when completion evidence is incomplete or internally inconsistent."""


@dataclass(frozen=True)
class ReferenceMapRow:
    source_id: int
    target_id: int
    line: int


@dataclass(frozen=True)
class EventMessageNode:
    message_id: int
    chapter: str
    table_symbol: str
    script_key: str
    script_ordinal: int
    message_ordinal: int
    control_flow_path: Tuple[str, ...]
    path: str
    line: int
    context: str
    context_sha256: str
    script_context_sha256: str


@dataclass(frozen=True)
class ParsedDefinition:
    symbol: str
    body: str
    path: Path
    start_line: int


@dataclass(frozen=True)
class EventOperation:
    opcode: str
    message_id: Optional[int]
    line: int
    context: str


@dataclass(frozen=True)
class ParsedEventScript:
    operations: Tuple[EventOperation, ...]
    path: Path
    table_symbol: str
    script_ordinal: int
    context: str
    context_sha256: str


def _sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _git_revision(root: Path) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if result.returncode != 0:
        raise StructuralCompletionError(
            f"{root}: cannot resolve reference revision: {result.stderr.strip()}"
        )
    return result.stdout.strip()


def _parse_reference_map(path: Path) -> Tuple[ReferenceMapRow, ...]:
    rows = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line or line.startswith("//"):
            continue
        fields = line.split("\t")
        if not re.fullmatch(r"[0-9A-Fa-f]+", fields[0]):
            raise StructuralCompletionError(
                f"{path}:{line_number}: malformed reference-map row"
            )
        if len(fields) == 1:
            continue
        target = fields[1].split("|", 1)[0]
        if not target or target == "-":
            continue
        if not re.fullmatch(r"[0-9A-Fa-f]+", target):
            raise StructuralCompletionError(
                f"{path}:{line_number}: malformed reference-map target"
            )
        rows.append(
            ReferenceMapRow(
                source_id=int(fields[0], 16),
                target_id=int(target, 16),
                line=line_number,
            )
        )
    return tuple(rows)


def _parse_region_map(path: Path) -> Dict[int, Tuple[str, ...]]:
    regions: Dict[int, Set[str]] = defaultdict(set)
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line or line.startswith("//"):
            continue
        match = _REGION_ROW_RE.fullmatch(line)
        if match:
            regions[int(match.group(1), 16)].add(match.group(2))
            continue
        if "{" in line or "}" in line:
            raise StructuralCompletionError(
                f"{path}:{line_number}: malformed region-map row"
            )
    return {target: tuple(sorted(values)) for target, values in regions.items()}


def _load_fallback_targets(path: Path, target_count: int) -> Tuple[int, ...]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise StructuralCompletionError(f"{path}: invalid JSON: {error}") from error
    rows = data.get("rows")
    if not isinstance(rows, list) or len(rows) != target_count:
        raise StructuralCompletionError(
            f"{path}: expected {target_count} authoritative target rows"
        )
    fallback = []
    for expected, row in enumerate(rows):
        if row.get("target_id") != format_message_id(expected):
            raise StructuralCompletionError(
                f"{path}: target row {expected} is not canonically ordered"
            )
        source = row.get("source")
        if source == {"kind": "english_fallback", "reason": "not-yet-verified"}:
            fallback.append(expected)
            continue
        promotion = row.get("verification", {}).get("promotion", {})
        original_source = promotion.get("original_source", {})
        if (
            promotion.get("pipeline") == "fe8u-final-mapping-v1"
            and original_source
            == {"kind": "english_fallback", "reason": "not-yet-verified"}
        ):
            fallback.append(expected)
    return tuple(fallback)


def _load_candidate_rows(path: Path) -> Dict[int, Dict[str, Any]]:
    try:
        rows = json.loads(path.read_text(encoding="utf-8")).get("rows", [])
    except json.JSONDecodeError as error:
        raise StructuralCompletionError(f"{path}: invalid JSON: {error}") from error
    result = {}
    for row in rows:
        source = row.get("source", {})
        if source.get("kind") != "indexed":
            continue
        result[int(row["target_id"], 16)] = {
            "source_id": int(source["id"], 16),
            "seed_tag": row["candidate_provenance"]["seed_tag"],
            "source_line": row["candidate_provenance"]["source_line"],
        }
    return result


def _message_payloads(path: Path) -> Dict[int, str]:
    messages = parse_hash_indexed(
        path.read_text(encoding="utf-8"),
        source_name=str(path),
    )
    return {message.id: message.text for message in messages}


def _parse_event_int(value: str) -> Optional[int]:
    match = _INTEGER_RE.fullmatch(value.strip())
    if not match:
        return None
    if match.group(1) or match.group(2):
        return int(match.group(1) or match.group(2), 16)
    return int(match.group(3), 10)


def _normalized_context(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _mask_c_comments_and_strings(text: str) -> str:
    result = list(text)
    index = 0
    state = "code"
    quote = ""
    while index < len(text):
        char = text[index]
        nxt = text[index + 1] if index + 1 < len(text) else ""
        if state == "code":
            if char == "/" and nxt == "/":
                result[index] = result[index + 1] = " "
                index += 2
                state = "line-comment"
                continue
            if char == "/" and nxt == "*":
                result[index] = result[index + 1] = " "
                index += 2
                state = "block-comment"
                continue
            if char in ('"', "'"):
                quote = char
                result[index] = " "
                index += 1
                state = "string"
                continue
        elif state == "line-comment":
            if char == "\n":
                state = "code"
            else:
                result[index] = " "
        elif state == "block-comment":
            if char == "*" and nxt == "/":
                result[index] = result[index + 1] = " "
                index += 2
                state = "code"
                continue
            if char != "\n":
                result[index] = " "
        else:
            if char == "\\" and index + 1 < len(text):
                result[index] = result[index + 1] = " "
                index += 2
                continue
            if char == quote:
                result[index] = " "
                state = "code"
            elif char != "\n":
                result[index] = " "
        index += 1
    return "".join(result)


def _scan_balanced(text: str, opening: int) -> Tuple[str, int]:
    pairs = {"(": ")", "{": "}", "[": "]"}
    if opening < 0 or opening >= len(text) or text[opening] not in pairs:
        raise StructuralCompletionError("cannot scan an unbalanced source structure")
    opening_char = text[opening]
    closing_char = pairs[opening_char]
    depth = 0
    for index in range(opening, len(text)):
        char = text[index]
        if char == opening_char:
            depth += 1
        elif char == closing_char:
            depth -= 1
            if depth == 0:
                return text[opening + 1 : index], index
    raise StructuralCompletionError("unterminated source structure")


def _split_arguments(arguments: str) -> List[str]:
    result = []
    start = 0
    stack = []
    pairs = {")": "(", "]": "[", "}": "{"}
    for index, char in enumerate(arguments):
        if char in "([{":
            stack.append(char)
        elif char in ")]}":
            if stack and stack[-1] == pairs[char]:
                stack.pop()
        elif char == "," and not stack:
            result.append(arguments[start:index].strip())
            start = index + 1
    result.append(arguments[start:].strip())
    return result


def _iter_named_calls(
    masked: str,
    names: Iterable[str],
) -> Iterable[Tuple[str, int, int, List[str]]]:
    pattern = re.compile(
        r"\b(" + "|".join(sorted((re.escape(name) for name in names), key=len, reverse=True)) + r")\s*\("
    )
    for match in pattern.finditer(masked):
        opening = masked.find("(", match.start())
        body, closing = _scan_balanced(masked, opening)
        yield match.group(1), match.start(), closing + 1, _split_arguments(body)


def _definition_spans(masked: str) -> List[Tuple[int, int, str]]:
    result = []
    for match in _DEFINITION_RE.finditer(masked):
        symbol = match.group(1)
        if symbol in ("for", "if", "switch", "while"):
            continue
        opening = masked.find("{", match.start())
        try:
            _, closing = _scan_balanced(masked, opening)
        except StructuralCompletionError:
            continue
        result.append((match.start(), closing + 1, symbol))
    return sorted(result, key=lambda row: (row[1] - row[0], row[0]))


def _symbol_for_offset(
    spans: Sequence[Tuple[int, int, str]],
    offset: int,
    default: str,
) -> str:
    for start, end, symbol in spans:
        if start <= offset < end:
            return symbol
    return default


def _parse_event_definitions(paths: Iterable[Path]) -> Dict[str, ParsedDefinition]:
    definitions = {}
    for path in sorted(paths):
        text = path.read_text(encoding="utf-8")
        masked = _mask_c_comments_and_strings(text)
        for match in _EVENT_DEFINITION_RE.finditer(masked):
            opening = masked.find("{", match.start())
            _, closing = _scan_balanced(masked, opening)
            symbol = match.group(1)
            definitions[symbol] = ParsedDefinition(
                symbol=symbol,
                body=text[opening + 1 : closing],
                path=path,
                start_line=text.count("\n", 0, opening) + 1,
            )
    return definitions


def _raw_event_ops(
    body: str,
    *,
    start_line: int,
) -> List[Tuple[str, List[str], int, str]]:
    raw = []
    for relative_line, original in enumerate(body.splitlines()):
        masked = _mask_c_comments_and_strings(original).strip()
        if not masked:
            continue
        match = _EVENT_CALL_RE.fullmatch(masked)
        if match:
            raw.append(
                (
                    match.group(1),
                    _split_arguments(match.group(2)),
                    start_line + relative_line,
                    _normalized_context(masked),
                )
            )
            continue
        match = _EVENT_BARE_RE.fullmatch(masked)
        if match:
            raw.append(
                (
                    match.group(1),
                    [],
                    start_line + relative_line,
                    match.group(1),
                )
            )
    return raw


def _canonical_event_ops(
    body: str,
    *,
    start_line: int = 1,
) -> List[EventOperation]:
    raw = _raw_event_ops(body, start_line=start_line)
    result: List[EventOperation] = []
    index = 0
    while index < len(raw):
        opcode, args, line, context = raw[index]
        if opcode == "Text_BG" and len(args) >= 2:
            result.append(EventOperation("SET_BACKGROUND", None, line, context))
            result.append(
                EventOperation("MESSAGE", _parse_event_int(args[1]), line, context)
            )
            index += 1
            continue
        if opcode == "SetBackground":
            result.append(EventOperation("SET_BACKGROUND", None, line, context))
            index += 1
            continue
        if opcode == "SVAL" and len(args) >= 2 and args[0] == "EVT_SLOT_2":
            if (
                index + 2 < len(raw)
                and raw[index + 1][0] == "SVAL"
                and len(raw[index + 1][1]) >= 2
                and raw[index + 1][1][0] == "EVT_SLOT_3"
                and raw[index + 2][0] == "CALL"
                and "Event_TextWithBG" in ",".join(raw[index + 2][1])
            ):
                result.append(EventOperation("SET_BACKGROUND", None, line, context))
                result.append(
                    EventOperation(
                        "MESSAGE",
                        _parse_event_int(raw[index + 1][1][1]),
                        raw[index + 1][2],
                        raw[index + 1][3],
                    )
                )
                index += 3
                continue
            if (
                index + 1 < len(raw)
                and raw[index + 1][0] == "CALL"
                and "SetBackground" in ",".join(raw[index + 1][1])
            ):
                result.append(EventOperation("SET_BACKGROUND", None, line, context))
                index += 2
                continue
        if (
            opcode == "SVAL"
            and len(args) >= 2
            and args[0] == "EVT_SLOT_3"
            and index + 1 < len(raw)
            and raw[index + 1][0] == "CALL"
            and "Event_TextWithBG" in ",".join(raw[index + 1][1])
        ):
            result.append(
                EventOperation("MESSAGE", _parse_event_int(args[1]), line, context)
            )
            index += 2
            continue
        message_id = None
        if opcode in _DIRECT_EVENT_MESSAGES:
            argument = _DIRECT_EVENT_MESSAGES[opcode]
            if len(args) > argument:
                message_id = _parse_event_int(args[argument])
            opcode = "MESSAGE"
        result.append(
            EventOperation(
                _EVENT_OPCODE_ALIASES.get(opcode, opcode),
                message_id,
                line,
                context,
            )
        )
        index += 1
    return result


def _parse_event_scripts(definition: ParsedDefinition) -> Tuple[ParsedEventScript, ...]:
    segments = []
    lines = definition.body.splitlines()
    start = 0
    for index, line in enumerate(lines):
        masked = _mask_c_comments_and_strings(line).strip()
        if masked != "ENDA":
            continue
        segment_lines = lines[start : index + 1]
        context = "\n".join(segment_lines)
        operations = tuple(
            _canonical_event_ops(
                context,
                start_line=definition.start_line + start,
            )
        )
        segments.append(
            ParsedEventScript(
                operations=operations,
                path=definition.path,
                table_symbol=definition.symbol,
                script_ordinal=len(segments) + 1,
                context=_normalized_context(
                    _mask_c_comments_and_strings(context)
                ),
                context_sha256=_sha256_bytes(
                    _normalized_context(
                        _mask_c_comments_and_strings(context)
                    ).encode("utf-8")
                ),
            )
        )
        start = index + 1
    return tuple(segments)


def _message_nodes(
    script: ParsedEventScript,
    *,
    root: Path,
    chapter: str,
    script_key: str,
) -> Tuple[EventMessageNode, ...]:
    message_indexes = [
        index
        for index, operation in enumerate(script.operations)
        if operation.opcode in ("MESSAGE", "MESSAGE_QUEUE")
        and operation.message_id is not None
    ]
    nodes = []
    for ordinal, operation_index in enumerate(message_indexes, 1):
        start = message_indexes[ordinal - 2] + 1 if ordinal > 1 else 0
        end = (
            message_indexes[ordinal]
            if ordinal < len(message_indexes)
            else len(script.operations)
        )
        operations = script.operations[start:end]
        control_flow_path = tuple(
            operation.opcode
            for operation in operations
            if operation.opcode not in _IGNORED_MESSAGE_CONTROL_OPS
        )
        context = " | ".join(operation.context for operation in operations)
        current = script.operations[operation_index]
        nodes.append(
            EventMessageNode(
                message_id=current.message_id,
                chapter=chapter,
                table_symbol=script.table_symbol,
                script_key=script_key,
                script_ordinal=script.script_ordinal,
                message_ordinal=ordinal,
                control_flow_path=control_flow_path,
                path=str(script.path.relative_to(root)),
                line=current.line,
                context=context,
                context_sha256=_sha256_bytes(context.encode("utf-8")),
                script_context_sha256=script.context_sha256,
            )
        )
    return tuple(nodes)


def _chapter_from_path(path: Path) -> str:
    match = re.search(r"\bch(?:apter)?[_-]?([0-9]+[ab]?)", path.stem, re.IGNORECASE)
    return f"Ch{match.group(1).upper()}" if match else "unknown"


def _site_record(
    *,
    root: Path,
    path: Path,
    symbol: str,
    line: int,
    kind: str,
    slot_key: str,
    context: str,
    structure: Mapping[str, Any],
) -> Dict[str, Any]:
    normalized = _normalized_context(context)
    return {
        "context": normalized,
        "context_sha256": _sha256_bytes(normalized.encode("utf-8")),
        "kind": kind,
        "line": line,
        "path": str(path.relative_to(root)),
        "slot": slot_key,
        "structure": dict(structure),
        "symbol": symbol,
    }


def _site_index(root: Path, wanted: Set[int]) -> Dict[int, List[Dict[str, Any]]]:
    result: Dict[int, List[Dict[str, Any]]] = defaultdict(list)
    slot_counts: Counter[Tuple[str, str, str, str]] = Counter()
    paths = []
    for base in ("src", "include"):
        directory = root / base
        if not directory.is_dir():
            continue
        paths.extend(
            path
            for path in directory.rglob("*")
            if path.suffix in (".c", ".h") and path.stat().st_size <= 600_000
        )

    for path in sorted(paths):
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        masked = _mask_c_comments_and_strings(text)
        lines = masked.splitlines()
        spans = _definition_spans(masked)

        for match in _MSG_TOKEN_RE.finditer(masked):
            message_id = int(match.group(1), 16)
            if message_id not in wanted:
                continue
            line = masked.count("\n", 0, match.start()) + 1
            context = lines[line - 1] if line <= len(lines) else match.group(0)
            symbol = _symbol_for_offset(spans, match.start(), path.stem)
            is_definition = bool(
                re.match(
                    r"\s*#\s*define\s+" + re.escape(match.group(0)) + r"\b",
                    context,
                )
            )
            kind = "msg-symbol-definition" if is_definition else "msg-symbol-reference"
            slot = (
                f"macro={match.group(0)}"
                if is_definition
                else f"{symbol}/msg-symbol={match.group(0)}"
            )
            result[message_id].append(
                _site_record(
                    root=root,
                    path=path,
                    symbol=match.group(0) if is_definition else symbol,
                    line=line,
                    kind=kind,
                    slot_key=slot,
                    context=context,
                    structure={
                        "macro": match.group(0),
                        "type": "macro-definition" if is_definition else "macro-reference",
                    },
                )
            )

        for match in _MESSAGE_FIELD_RE.finditer(masked):
            message_id = _parse_event_int(match.group(2))
            if message_id not in wanted or message_id == 0:
                continue
            line = masked.count("\n", 0, match.start()) + 1
            context = lines[line - 1]
            symbol = _symbol_for_offset(spans, match.start(), path.stem)
            count_key = (str(path), symbol, "field", match.group(1))
            ordinal = slot_counts[count_key]
            slot_counts[count_key] += 1
            result[message_id].append(
                _site_record(
                    root=root,
                    path=path,
                    symbol=symbol,
                    line=line,
                    kind="message-struct-field",
                    slot_key=(
                        f"{symbol}/field={match.group(1)}/ordinal={ordinal + 1}"
                    ),
                    context=context,
                    structure={
                        "field": match.group(1),
                        "ordinal": ordinal + 1,
                        "type": "designated-initializer-field",
                    },
                )
            )

        for name, start, end, arguments in _iter_named_calls(
            masked, _MESSAGE_CALL_ARGUMENTS
        ):
            symbol = _symbol_for_offset(spans, start, path.stem)
            for argument_index in _MESSAGE_CALL_ARGUMENTS[name]:
                if argument_index >= len(arguments):
                    continue
                message_id = _parse_event_int(arguments[argument_index])
                if message_id not in wanted or message_id == 0:
                    continue
                line = masked.count("\n", 0, start) + 1
                context = masked[start:end]
                count_key = (str(path), symbol, name, str(argument_index))
                ordinal = slot_counts[count_key]
                slot_counts[count_key] += 1
                result[message_id].append(
                    _site_record(
                        root=root,
                        path=path,
                        symbol=symbol,
                        line=line,
                        kind="message-call-argument",
                        slot_key=(
                            f"{symbol}/call={name}/argument={argument_index}"
                            f"/ordinal={ordinal + 1}"
                        ),
                        context=context,
                        structure={
                            "argument_index": argument_index,
                            "call": name,
                            "ordinal": ordinal + 1,
                            "type": "message-consuming-call",
                        },
                    )
                )

        event_definitions = _parse_event_definitions([path])
        for definition in event_definitions.values():
            for script in _parse_event_scripts(definition):
                nodes = _message_nodes(
                    script,
                    root=root,
                    chapter=_chapter_from_path(path),
                    script_key=definition.symbol,
                )
                for node in nodes:
                    if node.message_id not in wanted:
                        continue
                    result[node.message_id].append(
                        _site_record(
                            root=root,
                            path=path,
                            symbol=definition.symbol,
                            line=node.line,
                            kind="event-message-operand",
                            slot_key=(
                                f"{definition.symbol}/script={node.script_ordinal}"
                                f"/message={node.message_ordinal}"
                            ),
                            context=node.context,
                            structure={
                                "chapter": node.chapter,
                                "control_flow_path": list(node.control_flow_path),
                                "message_ordinal": node.message_ordinal,
                                "script_ordinal": node.script_ordinal,
                                "table_symbol": definition.symbol,
                                "type": "parsed-event-message-operand",
                            },
                        )
                    )

    try:
        trainee_tables = _parse_trainee_message_tables(root)
    except StructuralCompletionError:
        trainee_tables = {}
    for structure in trainee_tables.values():
        message_id = structure["message_id_value"]
        if message_id not in wanted:
            continue
        path = root / structure["path"]
        result[message_id].append(
            _site_record(
                root=root,
                path=path,
                symbol=structure["function"],
                line=structure["line"],
                kind="modeled-message-table",
                slot_key=structure["slot_key"],
                context=structure["context"],
                structure={
                    "consumer": structure["consumer"],
                    "function": structure["function"],
                    "index": structure["index"],
                    "key": structure["key"],
                    "table_symbol": structure["table_symbol"],
                    "type": "modeled-message-table",
                },
            )
        )

    deduplicated = {}
    for message_id, sites in result.items():
        unique = {}
        for site in sites:
            key = (
                site["kind"],
                site["path"],
                site["line"],
                site["slot"],
                site["context_sha256"],
            )
            unique[key] = site
        deduplicated[message_id] = sorted(
            unique.values(),
            key=lambda site: (
                _SITE_KIND_PRIORITY[site["kind"]],
                site["path"],
                site["line"],
                site["slot"],
            ),
        )[:5]
    return deduplicated


def _family_for_sites(target_id: int, sites: Sequence[Mapping[str, Any]]) -> str:
    haystack = " ".join(
        f"{site.get('path', '')} {site.get('symbol', '')}" for site in sites
    ).lower()
    if any(word in haystack for word in ("ruin", "tower", "dungeon", "timeline")):
        return "dungeon-timeline"
    if "event" in haystack:
        return "chapter-event"
    if any(word in haystack for word in ("classchg", "prep_", "unitlist")):
        return "trainee-prep"
    if any(word in haystack for word in ("sio", "arena", "shop")):
        return "shop-arena"
    if any(word in haystack for word in ("help", "guide", "tutorial")):
        return "help-tutorial"
    if "chapter" in haystack:
        return "chapter-title"
    if any(word in haystack for word in ("data_char", "data_class", "data_item")):
        return "entity-row"
    if "menu" in haystack:
        return "menu-definition"
    if target_id < 0x0160:
        return "menu-definition"
    if 0x04E4 <= target_id <= 0x0644:
        return "menu-definition"
    if 0x06E6 <= target_id <= 0x08DA:
        return "help-tutorial"
    return "chapter-event"


def _find_named_function(
    root: Path,
    name: str,
) -> Tuple[Path, str, int]:
    pattern = re.compile(r"\b" + re.escape(name) + r"\s*\([^;{}]*\)\s*\{")
    matches = []
    for path in sorted((root / "src").rglob("*.c")):
        if path.stat().st_size > 600_000:
            continue
        text = path.read_text(encoding="utf-8")
        masked = _mask_c_comments_and_strings(text)
        match = pattern.search(masked)
        if match is None:
            continue
        opening = masked.find("{", match.start())
        _, closing = _scan_balanced(masked, opening)
        matches.append(
            (
                path,
                text[opening + 1 : closing],
                text.count("\n", 0, opening) + 1,
            )
        )
    if len(matches) != 1:
        raise StructuralCompletionError(
            f"{root}: expected one parsed definition for {name}, found {len(matches)}"
        )
    return matches[0]


def _parse_trainee_message_tables(
    root: Path,
) -> Dict[str, Dict[str, Any]]:
    result = {}
    characters = ("ross", "amelia", "ewan")
    initializer_re = re.compile(
        r"\bconst\s+u32\s+msgs\s*\[\s*3\s*\]\s*=\s*\{"
    )
    for talk_number in range(1, 6):
        function = f"PromoTrainee_Talk{talk_number}"
        path, body, body_start_line = _find_named_function(root, function)
        masked = _mask_c_comments_and_strings(body)
        initializer = initializer_re.search(masked)
        if initializer is None:
            raise StructuralCompletionError(
                f"{path}: {function} does not define const u32 msgs[3]"
            )
        opening = masked.find("{", initializer.start())
        values_text, closing = _scan_balanced(masked, opening)
        values = _split_arguments(values_text)
        if len(values) != 3:
            raise StructuralCompletionError(
                f"{path}: {function}.msgs must contain exactly three slots"
            )
        consumers = list(_iter_named_calls(masked, ("StartCgText",)))
        if not any(
            len(arguments) > 4
            and re.sub(r"\s+", "", arguments[4]) == "msgs[i]"
            for _, _, _, arguments in consumers
        ):
            raise StructuralCompletionError(
                f"{path}: {function}.msgs is not consumed by StartCgText argument 4"
            )
        initializer_context = _normalized_context(
            masked[initializer.start() : closing + 1]
        )
        search_from = opening + 1
        for index, (character, token) in enumerate(zip(characters, values)):
            normalized_token = re.sub(r"\s+", "", token)
            token_at = masked.find(token, search_from, closing)
            search_from = max(search_from, token_at + len(token))
            if normalized_token in ("-1", "(u32)-1"):
                continue
            message_id = _parse_event_int(normalized_token)
            if message_id is None:
                raise StructuralCompletionError(
                    f"{path}: {function}.msgs[{index}] is not a message literal"
                )
            line = body_start_line + masked.count("\n", 0, max(token_at, 0))
            key = f"trainee/function={function}/character={character}"
            context = (
                f"{initializer_context} | slot[{index}]={normalized_token}"
                f" | consumer=StartCgText.argument[4]"
            )
            result[key] = {
                "consumer": "StartCgText.argument[4]=msgs[i]",
                "context": context,
                "context_sha256": _sha256_bytes(context.encode("utf-8")),
                "function": function,
                "index": index,
                "key": character,
                "kind": "message-table-slot",
                "line": line,
                "message_id": format_message_id(message_id),
                "message_id_value": message_id,
                "parsed": True,
                "path": str(path.relative_to(root)),
                "slot_key": key,
                "table_symbol": f"{function}.msgs",
            }
    return result


def _public_structure(
    structure: Mapping[str, Any],
    *,
    region: str,
) -> Dict[str, Any]:
    return {
        key: value
        for key, value in {
            **structure,
            "region": region,
        }.items()
        if key != "message_id_value"
    }


def _trainee_alignment(
    fe8u_root: Path,
    fe8j_root: Path,
    *,
    accepted_pairs: Set[Tuple[int, int]],
) -> Dict[Tuple[int, int], Dict[str, Any]]:
    target_tables = _parse_trainee_message_tables(fe8u_root)
    source_tables = _parse_trainee_message_tables(fe8j_root)
    result = {}
    for key in sorted(set(target_tables) & set(source_tables)):
        target = target_tables[key]
        source = source_tables[key]
        pair = (target["message_id_value"], source["message_id_value"])
        if pair not in accepted_pairs:
            continue
        result[pair] = {
            "basis": "parsed-trainee-message-table",
            "semantic_key": key,
            "source_structure": _public_structure(source, region="FE8J"),
            "target_structure": _public_structure(target, region="FE8U"),
        }
    return result


def _event_structure(
    node: EventMessageNode,
    *,
    region: str,
    semantic_key: str,
) -> Dict[str, Any]:
    control_flow = "|".join(node.control_flow_path)
    return {
        "chapter": node.chapter,
        "context": node.context,
        "context_sha256": node.context_sha256,
        "control_flow_path": list(node.control_flow_path),
        "control_flow_sha256": _sha256_bytes(control_flow.encode("utf-8")),
        "kind": "event-message-slot",
        "line": node.line,
        "message_id": format_message_id(node.message_id),
        "message_ordinal": node.message_ordinal,
        "opcode": "MESSAGE",
        "parsed": True,
        "path": node.path,
        "region": region,
        "script_context_sha256": node.script_context_sha256,
        "script_key": node.script_key,
        "script_ordinal": node.script_ordinal,
        "slot_key": semantic_key,
        "table_symbol": node.table_symbol,
    }


def _ch14b_alignment(
    fe8u_root: Path,
    fe8j_root: Path,
    *,
    accepted_pairs: Set[Tuple[int, int]],
) -> Dict[Tuple[int, int], Dict[str, Any]]:
    target_path = fe8u_root / "src/events/ch14b-eventscript.h"
    source_path = (
        fe8j_root
        / "src/data/frontier_df3_eventscr_ch/frontier_df3_eventscr_ch.c"
    )
    target_definitions = _parse_event_definitions((target_path,))
    source_definitions = _parse_event_definitions((source_path,))
    source_symbol = "frontier_df3_eventscr_ch_012_A6DE0C"
    if source_symbol not in source_definitions:
        raise StructuralCompletionError(
            f"{source_path}: missing modeled Ch14B event table {source_symbol}"
        )
    source_scripts = _parse_event_scripts(source_definitions[source_symbol])
    modeled = (
        ("beginning", "EventScr_Ch14b_BeginningScene"),
        ("location-rennac", "EventScr_Ch14B_0"),
        ("ending", "EventScr_Ch14b_EndingScene"),
    )
    result = {}
    for script_key, target_symbol in modeled:
        if target_symbol not in target_definitions:
            raise StructuralCompletionError(
                f"{target_path}: missing modeled Ch14B event table {target_symbol}"
            )
        target_scripts = _parse_event_scripts(target_definitions[target_symbol])
        if len(target_scripts) != 1:
            raise StructuralCompletionError(
                f"{target_path}: {target_symbol} must contain exactly one script"
            )
        target_nodes = _message_nodes(
            target_scripts[0],
            root=fe8u_root,
            chapter="Ch14B",
            script_key=script_key,
        )
        target_signature = tuple(
            node.control_flow_path for node in target_nodes
        )
        candidates = []
        for source_script in source_scripts:
            source_nodes = _message_nodes(
                source_script,
                root=fe8j_root,
                chapter="Ch14B",
                script_key=script_key,
            )
            if tuple(node.control_flow_path for node in source_nodes) == target_signature:
                candidates.append(source_nodes)
        if len(candidates) != 1:
            raise StructuralCompletionError(
                f"{source_path}: Ch14B {script_key} matched "
                f"{len(candidates)} parsed source scripts"
            )
        source_nodes = candidates[0]
        if len(target_nodes) != len(source_nodes):
            raise StructuralCompletionError(
                f"Ch14B {script_key}: source/target message counts differ"
            )
        for target_node, source_node in zip(target_nodes, source_nodes):
            pair = (target_node.message_id, source_node.message_id)
            if pair not in accepted_pairs:
                continue
            if target_node.control_flow_path != source_node.control_flow_path:
                raise StructuralCompletionError(
                    f"Ch14B {script_key}: message control paths differ"
                )
            semantic_key = (
                f"chapter=Ch14B/script={script_key}"
                f"/message-ordinal={target_node.message_ordinal}"
            )
            result[pair] = {
                "basis": "parsed-event-structure",
                "semantic_key": semantic_key,
                "source_structure": _event_structure(
                    source_node,
                    region="FE8J",
                    semantic_key=semantic_key,
                ),
                "target_structure": _event_structure(
                    target_node,
                    region="FE8U",
                    semantic_key=semantic_key,
                ),
            }
    return result


def _protected_inputs(repo_root: Path) -> List[Dict[str, str]]:
    return [
        {
            "path": relative,
            "sha256": _sha256_file(repo_root / relative),
        }
        for relative in PROTECTED_PATHS
    ]


def _record_source_validation(source_id: int, payload: str) -> Dict[str, Any]:
    return {
        "source_in_bounds": 0 <= source_id <= FE8J_MAX_INDEXED_ID,
        "source_payload_bytes": len(payload.encode("utf-8")),
        "source_payload_nonempty": bool(payload),
        "source_payload_sha256": _sha256_bytes(payload.encode("utf-8")),
    }


def refresh_structural_completion_payloads(
    data: Any,
    *,
    repo_root: Path,
    target_count: int,
) -> Dict[str, Any]:
    """Refresh only normalized FE8J input and payload-derived hash fields."""

    if not isinstance(data, dict):
        raise StructuralCompletionError("completion evidence must be an object")
    refreshed = deepcopy(data)
    indexed_input = refreshed.get("inputs", {}).get("fe8j_indexed_source")
    if not isinstance(indexed_input, dict):
        raise StructuralCompletionError(
            "completion.inputs.fe8j_indexed_source must be an object"
        )
    indexed_path = Path(repo_root) / indexed_input.get("path", "")
    if not indexed_path.is_file():
        raise StructuralCompletionError(
            "completion FE8J indexed-source path does not exist"
        )
    indexed_input["sha256"] = _sha256_file(indexed_path)
    payloads = _message_payloads(indexed_path)

    for proposal in refreshed.get("proposals", []):
        source_id = int(proposal["source_id"], 16)
        validation = proposal.get("validation")
        if not isinstance(validation, dict):
            raise StructuralCompletionError(
                f"{proposal['target_id']}: proposal validation is missing"
            )
        validation.update(_record_source_validation(source_id, payloads[source_id]))

    for collision in refreshed.get("collisions", []):
        for option in collision.get("source_options", []):
            source_id = int(option["source_id"], 16)
            option["validation"] = _record_source_validation(
                source_id,
                payloads[source_id],
            )

    validate_structural_completion_evidence(
        refreshed,
        repo_root=repo_root,
        target_count=target_count,
    )
    return refreshed


def build_structural_completion_evidence(
    *,
    repo_root: Path,
    fe8u_root: Path,
    fe8j_root: Path,
    reference_map_path: Path,
    region_map_path: Path,
    target_count: int,
    mapping_path: Optional[Path] = None,
    current_evidence_path: Optional[Path] = None,
    candidate_path: Optional[Path] = None,
    indexed_source_path: Optional[Path] = None,
) -> Dict[str, Any]:
    repo_root = Path(repo_root)
    fe8u_root = Path(fe8u_root)
    fe8j_root = Path(fe8j_root)
    mapping_path = mapping_path or (
        repo_root / "texts/locales/mapping/fe8u_target_map.json"
    )
    current_evidence_path = current_evidence_path or (
        repo_root / "texts/locales/mapping/fe8u_structural_evidence.json"
    )
    candidate_path = candidate_path or (
        repo_root / "texts/locales/mapping/fe8j_to_fe8u.candidates.json"
    )
    indexed_source_path = indexed_source_path or (
        repo_root / "texts/locales/ja/indexed.txt"
    )

    fallback_targets = set(
        _load_fallback_targets(mapping_path, target_count)
    )
    source_payloads = _message_payloads(indexed_source_path)
    candidate_rows = _load_candidate_rows(candidate_path)
    region_rows = _parse_region_map(region_map_path)
    reference_rows = _parse_reference_map(reference_map_path)

    reference_by_target: Dict[int, List[ReferenceMapRow]] = defaultdict(list)
    nonindexed_by_target: Dict[int, List[ReferenceMapRow]] = defaultdict(list)
    for row in reference_rows:
        if row.target_id not in fallback_targets:
            continue
        if 0 <= row.source_id <= FE8J_MAX_INDEXED_ID:
            reference_by_target[row.target_id].append(row)
        else:
            nonindexed_by_target[row.target_id].append(row)

    target_sites = _site_index(fe8u_root, fallback_targets)
    bounded_sources = {
        row.source_id for rows in reference_by_target.values() for row in rows
    }
    bounded_sources.add(0x0700)
    source_sites = _site_index(fe8j_root, bounded_sources)
    accepted_pairs = {
        (target, row.source_id)
        for target, rows in reference_by_target.items()
        for row in rows
    }
    parsed_alignment = {}
    for alignment in (
        _ch14b_alignment(
            fe8u_root,
            fe8j_root,
            accepted_pairs=accepted_pairs,
        ),
        _trainee_alignment(
            fe8u_root,
            fe8j_root,
            accepted_pairs=accepted_pairs,
        ),
    ):
        for pair, proof in alignment.items():
            if pair in parsed_alignment:
                raise StructuralCompletionError(
                    f"duplicate parsed structural proof for "
                    f"{format_message_id(pair[0])}/{format_message_id(pair[1])}"
                )
            parsed_alignment[pair] = proof

    proposals = []
    collisions = []
    proposed_targets: Set[int] = set()
    context_targets: Set[int] = set()

    for target_id in sorted(fallback_targets):
        rows = sorted(
            reference_by_target.get(target_id, []),
            key=lambda row: (row.source_id, row.line),
        )
        options: Dict[int, List[ReferenceMapRow]] = defaultdict(list)
        for row in rows:
            options[row.source_id].append(row)
        if target_id == 0x0C52:
            options[0x0700]

        valid_options = {
            source_id: option_rows
            for source_id, option_rows in options.items()
            if source_payloads.get(source_id, "")
        }
        if len(valid_options) > 1:
            option_records = []
            for source_id, option_rows in sorted(valid_options.items()):
                basis = (
                    "live-prep-call-sites"
                    if target_id == 0x0C52 and source_id == 0x0700
                    else "authorized-reference-map"
                )
                option_records.append(
                    {
                        "basis": basis,
                        "reference_map_lines": [
                            row.line for row in option_rows
                        ],
                        "source_id": format_message_id(source_id),
                        "source_sites": source_sites.get(source_id, []),
                        "validation": _record_source_validation(
                            source_id, source_payloads[source_id]
                        ),
                    }
                )
            collisions.append(
                {
                    "relation": {
                        "context_required": True,
                        "kind": "one-to-many",
                    },
                    "requirement": (
                        "Preserve the calling context or introduce a context selector; "
                        "do not choose one FE8J provider for every use."
                    ),
                    "semantic_slot": {
                        "family": _family_for_sites(
                            target_id, target_sites.get(target_id, [])
                        ),
                        "key": (
                            "prep/deploy-unavailable"
                            if target_id == 0x0C52
                            else f"reference-map/context-target={format_message_id(target_id)}"
                        ),
                    },
                    "source_options": option_records,
                    "status": "context-required",
                    "target_id": format_message_id(target_id),
                    "target_sites": target_sites.get(target_id, []),
                }
            )
            context_targets.add(target_id)
            continue
        if len(valid_options) != 1:
            continue

        source_id = next(iter(valid_options))
        option_rows = valid_options[source_id]
        parsed_proof = parsed_alignment.get((target_id, source_id))

        sites = target_sites.get(target_id, [])
        family = _family_for_sites(target_id, sites)
        basis = "authorized-reference-map"
        confidence = "reference"
        semantic_key = (
            sites[0]["slot"]
            if sites
            else (
                f"reference-map/target={format_message_id(target_id)}"
                f"/source={format_message_id(source_id)}"
            )
        )
        evidence: Dict[str, Any] = {
            "basis": basis,
            "reference_map_lines": [row.line for row in option_rows],
            "source_sites": source_sites.get(source_id, []),
            "target_sites": sites,
        }
        if parsed_proof is not None:
            confidence = "high"
            basis = parsed_proof["basis"]
            evidence.update(
                {
                    "basis": basis,
                    "source_structure": parsed_proof["source_structure"],
                    "target_structure": parsed_proof["target_structure"],
                }
            )
            semantic_key = parsed_proof["semantic_key"]
            family = (
                "chapter-event"
                if basis == "parsed-event-structure"
                else "trainee-prep"
            )

        candidate = candidate_rows.get(target_id)
        if candidate is None:
            candidate_comparison = {"present": False}
        else:
            candidate_comparison = {
                "matched": candidate["source_id"] == source_id,
                "present": True,
                "seed_source_id": format_message_id(candidate["source_id"]),
                "seed_tag": candidate["seed_tag"],
                "source_line": candidate["source_line"],
            }
        proposals.append(
            {
                "candidate_seed": candidate_comparison,
                "confidence": confidence,
                "evidence": evidence,
                "relation": {
                    "context_required": False,
                    "kind": "one-to-one",
                },
                "semantic_slot": {
                    "family": family,
                    "key": semantic_key,
                },
                "source_id": format_message_id(source_id),
                "status": "proposed",
                "target_id": format_message_id(target_id),
                "validation": {
                    "parsed_structural_pair": confidence == "high",
                    "target_in_bounds": 0 <= target_id < target_count,
                    "typed_source_site_count": len(
                        source_sites.get(source_id, [])
                    ),
                    "typed_target_site_count": len(sites),
                    **_record_source_validation(
                        source_id, source_payloads[source_id]
                    ),
                },
            }
        )
        proposed_targets.add(target_id)

    source_to_targets: Dict[int, List[int]] = defaultdict(list)
    for proposal in proposals:
        source_to_targets[int(proposal["source_id"], 16)].append(
            int(proposal["target_id"], 16)
        )
    for proposal in proposals:
        source_id = int(proposal["source_id"], 16)
        if len(source_to_targets[source_id]) > 1:
            proposal["relation"]["kind"] = "many-to-one"
            proposal["relation"]["group_targets"] = [
                format_message_id(target)
                for target in sorted(source_to_targets[source_id])
            ]

    residual = []
    for target_id in sorted(fallback_targets - proposed_targets):
        if target_id in context_targets:
            reason = "context-required"
        elif nonindexed_by_target.get(target_id):
            reason = "only-nonindexed-reference"
        elif reference_by_target.get(target_id):
            reason = "source-payload-empty"
        else:
            reason = "no-bounded-reference-pair"
        candidate = candidate_rows.get(target_id)
        residual.append(
            {
                "candidate_seed": (
                    {
                        "present": True,
                        "source_id": format_message_id(candidate["source_id"]),
                        "seed_tag": candidate["seed_tag"],
                        "source_line": candidate["source_line"],
                    }
                    if candidate
                    else {"present": False}
                ),
                "nonindexed_reference_rows": [
                    {
                        "reference_map_line": row.line,
                        "source_value": f"0x{row.source_id:X}",
                    }
                    for row in nonindexed_by_target.get(target_id, [])
                ],
                "reason": reason,
                "region_markers": list(region_rows.get(target_id, ())),
                "target_id": format_message_id(target_id),
                "target_sites": target_sites.get(target_id, []),
            }
        )

    family_counts = Counter(
        proposal["semantic_slot"]["family"] for proposal in proposals
    )
    confidence_counts = Counter(proposal["confidence"] for proposal in proposals)
    basis_counts = Counter(proposal["evidence"]["basis"] for proposal in proposals)
    result = {
        "authority": COMPLETION_AUTHORITY,
        "authoritative": False,
        "collisions": collisions,
        "inputs": {
            "candidate_seed": {
                "path": str(candidate_path.relative_to(repo_root)),
                "sha256": _sha256_file(candidate_path),
            },
            "fe8j_indexed_source": {
                "path": str(indexed_source_path.relative_to(repo_root)),
                "sha256": _sha256_file(indexed_source_path),
            },
            "fe8j_reference_revision": _git_revision(fe8j_root),
            "fe8u_reference_revision": _git_revision(fe8u_root),
            "protected_artifacts": _protected_inputs(repo_root),
            "reference_map": {
                "logical_path": "febuilder:config/data/translate_textid_FE8.txt",
                "sha256": _sha256_file(reference_map_path),
            },
            "region_map": {
                "logical_path": "febuilder:config/data/textid_FE8.txt",
                "sha256": _sha256_file(region_map_path),
            },
        },
        "kind": COMPLETION_KIND,
        "note": (
            "Evidence-only completion proposals for current not-yet-verified "
            "fallbacks. This artifact cannot update the authoritative target map."
        ),
        "proposals": sorted(
            proposals,
            key=lambda row: (
                row["target_id"],
                row["source_id"],
                row["semantic_slot"]["key"],
            ),
        ),
        "residual_targets": residual,
        "schema_version": COMPLETION_SCHEMA_VERSION,
        "summary": {
            "basis_counts": dict(sorted(basis_counts.items())),
            "confidence_counts": dict(sorted(confidence_counts.items())),
            "context_required_count": len(context_targets),
            "fallback_target_count": len(fallback_targets),
            "family_counts": dict(sorted(family_counts.items())),
            "proposed_target_count": len(proposed_targets),
            "residual_target_count": len(fallback_targets - proposed_targets),
            "unmapped_residual_count": len(
                fallback_targets - proposed_targets - context_targets
            ),
        },
        "target_count": target_count,
    }
    validate_structural_completion_evidence(
        result,
        repo_root=repo_root,
        target_count=target_count,
    )
    return result


def _validate_typed_site(site: Any, *, field: str) -> None:
    if not isinstance(site, dict):
        raise StructuralCompletionError(f"{field} must be an object")
    if site.get("kind") not in _TYPED_SITE_KINDS:
        raise StructuralCompletionError(
            f"{field}.kind is not a typed message-site extractor"
        )
    if not isinstance(site.get("path"), str) or not site["path"]:
        raise StructuralCompletionError(f"{field}.path is missing")
    if not isinstance(site.get("symbol"), str) or not site["symbol"]:
        raise StructuralCompletionError(f"{field}.symbol is missing")
    if not isinstance(site.get("slot"), str) or not site["slot"]:
        raise StructuralCompletionError(f"{field}.slot is missing")
    if not isinstance(site.get("line"), int) or site["line"] <= 0:
        raise StructuralCompletionError(f"{field}.line is invalid")
    context = site.get("context")
    if (
        not isinstance(context, str)
        or not context
        or site.get("context_sha256")
        != _sha256_bytes(context.encode("utf-8"))
    ):
        raise StructuralCompletionError(f"{field}.context hash is stale")
    if not isinstance(site.get("structure"), dict) or not site["structure"].get(
        "type"
    ):
        raise StructuralCompletionError(f"{field}.structure is incomplete")


def _validate_parsed_structure(
    structure: Any,
    *,
    field: str,
    expected_message_id: str,
) -> None:
    if not isinstance(structure, dict) or not structure.get("parsed"):
        raise StructuralCompletionError(f"{field} is not a parsed structure")
    required_strings = (
        "context",
        "context_sha256",
        "kind",
        "message_id",
        "path",
        "region",
        "slot_key",
        "table_symbol",
    )
    if any(not isinstance(structure.get(key), str) or not structure[key] for key in required_strings):
        raise StructuralCompletionError(f"{field} is missing named/keyed structure data")
    if structure["message_id"] != expected_message_id:
        raise StructuralCompletionError(f"{field}.message_id does not match its row")
    if structure["context_sha256"] != _sha256_bytes(
        structure["context"].encode("utf-8")
    ):
        raise StructuralCompletionError(f"{field}.context hash is stale")
    if Path(structure["path"]).is_absolute():
        raise StructuralCompletionError(f"{field}.path must be repository-relative")
    if structure["kind"] == "event-message-slot":
        event_fields = (
            "chapter",
            "control_flow_path",
            "control_flow_sha256",
            "message_ordinal",
            "opcode",
            "script_context_sha256",
            "script_key",
            "script_ordinal",
        )
        if any(key not in structure for key in event_fields):
            raise StructuralCompletionError(f"{field} is missing event structure data")
        if structure["chapter"] == "unknown" or structure["opcode"] != "MESSAGE":
            raise StructuralCompletionError(f"{field} lacks chapter/message identity")
        control_flow = structure["control_flow_path"]
        if (
            not isinstance(control_flow, list)
            or "MESSAGE" not in control_flow
            or structure["control_flow_sha256"]
            != _sha256_bytes("|".join(control_flow).encode("utf-8"))
        ):
            raise StructuralCompletionError(f"{field}.control-flow hash is stale")
    elif structure["kind"] == "message-table-slot":
        for key in ("consumer", "function", "index", "key"):
            if key not in structure:
                raise StructuralCompletionError(
                    f"{field} is missing modeled table slot data"
                )
    else:
        raise StructuralCompletionError(f"{field}.kind is not high-confidence proof")


def validate_structural_completion_evidence(
    data: Any,
    *,
    repo_root: Path,
    target_count: int,
) -> None:
    if not isinstance(data, dict):
        raise StructuralCompletionError("completion evidence must be an object")
    if data.get("schema_version") != COMPLETION_SCHEMA_VERSION:
        raise StructuralCompletionError(
            f"completion.schema_version must be {COMPLETION_SCHEMA_VERSION}"
        )
    if data.get("kind") != COMPLETION_KIND:
        raise StructuralCompletionError(
            f"completion.kind must be {COMPLETION_KIND!r}"
        )
    if data.get("authority") != COMPLETION_AUTHORITY or data.get("authoritative"):
        raise StructuralCompletionError(
            "completion evidence must remain non-authoritative evidence-only data"
        )
    if data.get("target_count") != target_count:
        raise StructuralCompletionError(
            f"completion.target_count must be {target_count}"
        )

    inputs = data.get("inputs")
    if not isinstance(inputs, dict):
        raise StructuralCompletionError("completion.inputs must be an object")
    protected = inputs.get("protected_artifacts")
    if not isinstance(protected, list):
        raise StructuralCompletionError(
            "completion.inputs.protected_artifacts must be an array"
        )
    expected_protected = _protected_inputs(Path(repo_root))
    if protected != expected_protected:
        raise StructuralCompletionError(
            "completion protected-artifact hashes do not match the repository"
        )

    indexed_input = inputs.get("fe8j_indexed_source")
    if not isinstance(indexed_input, dict):
        raise StructuralCompletionError(
            "completion.inputs.fe8j_indexed_source must be an object"
        )
    indexed_path = Path(repo_root) / indexed_input.get("path", "")
    if not indexed_path.is_file() or _sha256_file(indexed_path) != indexed_input.get(
        "sha256"
    ):
        raise StructuralCompletionError(
            "completion FE8J indexed-source hash does not match the repository"
        )
    payloads = _message_payloads(indexed_path)

    proposals = data.get("proposals")
    collisions = data.get("collisions")
    residual = data.get("residual_targets")
    if not all(isinstance(value, list) for value in (proposals, collisions, residual)):
        raise StructuralCompletionError(
            "completion proposals, collisions, and residual_targets must be arrays"
        )

    seen_targets = set()
    previous = None
    source_to_targets: Dict[int, List[int]] = defaultdict(list)
    for index, proposal in enumerate(proposals):
        field = f"completion.proposals[{index}]"
        target = proposal.get("target_id")
        source = proposal.get("source_id")
        if not isinstance(target, str) or not re.fullmatch(r"0x[0-9A-F]{4}", target):
            raise StructuralCompletionError(
                f"{field}.target_id must use canonical 0xNNNN form"
            )
        if not isinstance(source, str) or not re.fullmatch(r"0x[0-9A-F]{4}", source):
            raise StructuralCompletionError(
                f"{field}.source_id must use canonical 0xNNNN form"
            )
        target_id = int(target, 16)
        source_id = int(source, 16)
        if previous is not None and target_id <= previous:
            raise StructuralCompletionError(
                "completion proposals must be sorted by ascending target_id"
            )
        previous = target_id
        if target_id in seen_targets:
            raise StructuralCompletionError(f"{field}.target_id is duplicated")
        seen_targets.add(target_id)
        if not 0 <= target_id < target_count:
            raise StructuralCompletionError(f"{field}.target_id is outside bounds")
        if not 0 <= source_id <= FE8J_MAX_INDEXED_ID:
            raise StructuralCompletionError(f"{field}.source_id is outside FE8J bounds")
        if not payloads[source_id]:
            raise StructuralCompletionError(f"{field}.source_id has an empty payload")
        if proposal.get("confidence") not in CONFIDENCE_LEVELS:
            raise StructuralCompletionError(
                f"{field}.confidence must be one of {CONFIDENCE_LEVELS}"
            )
        if proposal.get("status") != "proposed":
            raise StructuralCompletionError(f"{field}.status must be 'proposed'")
        evidence = proposal.get("evidence")
        if not isinstance(evidence, dict) or not evidence.get("basis"):
            raise StructuralCompletionError(f"{field}.evidence is incomplete")
        if any(
            word in evidence["basis"]
            for word in ("interp", "proximity", "shifted", "extrap")
        ):
            raise StructuralCompletionError(
                f"{field}.evidence cannot use numeric interpolation"
            )
        source_sites = evidence.get("source_sites")
        target_sites = evidence.get("target_sites")
        if not isinstance(source_sites, list) or not isinstance(target_sites, list):
            raise StructuralCompletionError(
                f"{field}.evidence typed source/target sites must be arrays"
            )
        for site_index, site in enumerate(source_sites):
            _validate_typed_site(
                site,
                field=f"{field}.evidence.source_sites[{site_index}]",
            )
        for site_index, site in enumerate(target_sites):
            _validate_typed_site(
                site,
                field=f"{field}.evidence.target_sites[{site_index}]",
            )
        validation = proposal.get("validation")
        expected_validation = {
            "parsed_structural_pair": proposal["confidence"] == "high",
            "target_in_bounds": True,
            "typed_source_site_count": len(source_sites),
            "typed_target_site_count": len(target_sites),
            **_record_source_validation(source_id, payloads[source_id]),
        }
        if validation != expected_validation:
            raise StructuralCompletionError(
                f"{field}.validation does not match source payload and bounds"
            )
        semantic_slot = proposal.get("semantic_slot")
        if (
            not isinstance(semantic_slot, dict)
            or not semantic_slot.get("family")
            or not semantic_slot.get("key")
        ):
            raise StructuralCompletionError(
                f"{field}.semantic_slot must contain family and key"
            )
        if proposal["confidence"] == "high":
            if evidence["basis"] not in (
                "parsed-event-structure",
                "parsed-trainee-message-table",
            ):
                raise StructuralCompletionError(
                    f"{field}.evidence basis is not parsed high-confidence proof"
                )
            source_structure = evidence.get("source_structure")
            target_structure = evidence.get("target_structure")
            _validate_parsed_structure(
                source_structure,
                field=f"{field}.evidence.source_structure",
                expected_message_id=source,
            )
            _validate_parsed_structure(
                target_structure,
                field=f"{field}.evidence.target_structure",
                expected_message_id=target,
            )
            if (
                source_structure["slot_key"] != semantic_slot["key"]
                or target_structure["slot_key"] != semantic_slot["key"]
            ):
                raise StructuralCompletionError(
                    f"{field} high-confidence structures do not cite the semantic slot"
                )
            if source_structure["kind"] != target_structure["kind"]:
                raise StructuralCompletionError(
                    f"{field} source/target structure kinds differ"
                )
            if source_structure["kind"] == "event-message-slot":
                matching_fields = (
                    "chapter",
                    "control_flow_path",
                    "message_ordinal",
                    "script_key",
                    "slot_key",
                )
                if any(
                    source_structure[key] != target_structure[key]
                    for key in matching_fields
                ):
                    raise StructuralCompletionError(
                        f"{field} event proof lacks shared chapter/path/key identity"
                    )
            elif (
                source_structure["function"] != target_structure["function"]
                or source_structure["index"] != target_structure["index"]
                or source_structure["key"] != target_structure["key"]
            ):
                raise StructuralCompletionError(
                    f"{field} message-table proof lacks a shared keyed slot"
                )
            target_structure_path = Path(repo_root) / target_structure["path"]
            if not target_structure_path.is_file():
                raise StructuralCompletionError(
                    f"{field} target parsed structure path does not exist"
                )
        elif "source_structure" in evidence or "target_structure" in evidence:
            raise StructuralCompletionError(
                f"{field} reference confidence cannot cite parsed pair proof"
            )
        relation = proposal.get("relation")
        if not isinstance(relation, dict) or relation.get("context_required"):
            raise StructuralCompletionError(
                f"{field}.relation must be an unambiguous proposed relation"
            )
        source_to_targets[source_id].append(target_id)
    for index, proposal in enumerate(proposals):
        source_id = int(proposal["source_id"], 16)
        targets = sorted(source_to_targets[source_id])
        relation = proposal["relation"]
        expected_kind = "many-to-one" if len(targets) > 1 else "one-to-one"
        if relation.get("kind") != expected_kind:
            raise StructuralCompletionError(
                f"completion.proposals[{index}].relation.kind must be "
                f"{expected_kind!r}"
            )
        if len(targets) > 1 and relation.get("group_targets") != [
            format_message_id(target) for target in targets
        ]:
            raise StructuralCompletionError(
                f"completion.proposals[{index}].relation.group_targets is stale"
            )

    collision_targets = set()
    for index, collision in enumerate(collisions):
        field = f"completion.collisions[{index}]"
        target = collision.get("target_id")
        if not isinstance(target, str) or not re.fullmatch(r"0x[0-9A-F]{4}", target):
            raise StructuralCompletionError(
                f"{field}.target_id must use canonical 0xNNNN form"
            )
        target_id = int(target, 16)
        if target_id in seen_targets or target_id in collision_targets:
            raise StructuralCompletionError(f"{field}.target_id is duplicated")
        collision_targets.add(target_id)
        options = collision.get("source_options")
        if not isinstance(options, list) or len(options) < 2:
            raise StructuralCompletionError(
                f"{field}.source_options must contain at least two providers"
            )
        if collision.get("relation") != {
            "context_required": True,
            "kind": "one-to-many",
        }:
            raise StructuralCompletionError(
                f"{field}.relation must describe a one-to-many context requirement"
            )
        target_sites = collision.get("target_sites")
        if not isinstance(target_sites, list):
            raise StructuralCompletionError(f"{field}.target_sites must be an array")
        for site_index, site in enumerate(target_sites):
            _validate_typed_site(
                site,
                field=f"{field}.target_sites[{site_index}]",
            )
        for option_index, option in enumerate(options):
            source = option.get("source_id")
            if not isinstance(source, str) or not re.fullmatch(
                r"0x[0-9A-F]{4}", source
            ):
                raise StructuralCompletionError(
                    f"{field}.source_options[{option_index}].source_id is invalid"
                )
            source_id = int(source, 16)
            if not 0 <= source_id <= FE8J_MAX_INDEXED_ID:
                raise StructuralCompletionError(
                    f"{field}.source_options[{option_index}] is outside FE8J bounds"
                )
            if option.get("validation") != _record_source_validation(
                source_id, payloads[source_id]
            ):
                raise StructuralCompletionError(
                    f"{field}.source_options[{option_index}].validation is stale"
                )
            source_sites = option.get("source_sites")
            if not isinstance(source_sites, list):
                raise StructuralCompletionError(
                    f"{field}.source_options[{option_index}].source_sites "
                    "must be an array"
                )
            for site_index, site in enumerate(source_sites):
                _validate_typed_site(
                    site,
                    field=(
                        f"{field}.source_options[{option_index}]"
                        f".source_sites[{site_index}]"
                    ),
                )

    residual_ids = []
    for index, row in enumerate(residual):
        target = row.get("target_id")
        if not isinstance(target, str) or not re.fullmatch(r"0x[0-9A-F]{4}", target):
            raise StructuralCompletionError(
                f"completion.residual_targets[{index}].target_id is invalid"
            )
        target_sites = row.get("target_sites")
        if not isinstance(target_sites, list):
            raise StructuralCompletionError(
                f"completion.residual_targets[{index}].target_sites must be an array"
            )
        for site_index, site in enumerate(target_sites):
            _validate_typed_site(
                site,
                field=(
                    f"completion.residual_targets[{index}]"
                    f".target_sites[{site_index}]"
                ),
            )
        residual_ids.append(int(target, 16))
    if residual_ids != sorted(residual_ids) or len(residual_ids) != len(
        set(residual_ids)
    ):
        raise StructuralCompletionError(
            "completion residual targets must be unique and sorted"
        )
    if not collision_targets <= set(residual_ids):
        raise StructuralCompletionError(
            "completion collision targets must also be explicit residual targets"
        )
    fallback_targets = set(
        _load_fallback_targets(
            Path(repo_root) / PROTECTED_PATHS[0],
            target_count,
        )
    )
    if seen_targets | set(residual_ids) != fallback_targets:
        raise StructuralCompletionError(
            "completion proposals and residuals must partition current fallbacks"
        )
    if seen_targets & set(residual_ids):
        raise StructuralCompletionError(
            "completion targets cannot be both proposed and residual"
        )

    summary = data.get("summary")
    if not isinstance(summary, dict):
        raise StructuralCompletionError("completion.summary must be an object")
    if summary.get("proposed_target_count") != len(proposals):
        raise StructuralCompletionError(
            "completion.summary.proposed_target_count is stale"
        )
    if summary.get("context_required_count") != len(collisions):
        raise StructuralCompletionError(
            "completion.summary.context_required_count is stale"
        )
    if summary.get("residual_target_count") != len(residual):
        raise StructuralCompletionError(
            "completion.summary.residual_target_count is stale"
        )
    if summary.get("fallback_target_count") != len(proposals) + len(residual):
        raise StructuralCompletionError(
            "completion summary does not partition fallback targets"
        )


def check_structural_completion_evidence(
    path: Path,
    *,
    repo_root: Path,
    target_count: int,
) -> Dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise StructuralCompletionError(f"{path}: invalid JSON: {error}") from error
    validate_structural_completion_evidence(
        data,
        repo_root=repo_root,
        target_count=target_count,
    )
    if path.read_bytes() != canonical_json_bytes(data):
        raise StructuralCompletionError(f"{path}: JSON is not canonical")
    return data


def refresh_structural_completion_protected_inputs(
    path: Path,
    *,
    repo_root: Path,
    target_count: int,
) -> Dict[str, Any]:
    path = Path(path)
    repo_root = Path(repo_root)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise StructuralCompletionError(f"{path}: invalid JSON: {error}") from error
    if not isinstance(data, dict) or not isinstance(data.get("inputs"), dict):
        raise StructuralCompletionError("completion evidence inputs are malformed")
    refreshed = deepcopy(data)
    refreshed["inputs"]["protected_artifacts"] = _protected_inputs(repo_root)
    refreshed = refresh_structural_completion_payloads(
        refreshed,
        repo_root=repo_root,
        target_count=target_count,
    )
    path.write_bytes(canonical_json_bytes(refreshed))
    return refreshed
