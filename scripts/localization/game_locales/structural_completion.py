"""Deterministic evidence for FE8U targets not covered by the release crosswalk."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

from .crosswalk import (
    CrosswalkError,
    _parse_script_definitions,
    canonical_json_bytes,
)
from .mapping import format_message_id
from .parsers import FE8J_MAX_INDEXED_ID, parse_hash_indexed

COMPLETION_SCHEMA_VERSION = 1
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
_SOURCE_TOKEN_RE = re.compile(
    r"(?<![A-Za-z0-9_])(?:MSG_([0-9A-Fa-f]+)|0[xX]([0-9A-Fa-f]+))(?![A-Za-z0-9_])"
)
_DEFINITION_RE = re.compile(
    r"\b([A-Za-z_][A-Za-z0-9_]*)\s*"
    r"(?:\[[^\]]*\]\s*(?:[^=;{}]*)=|\([^;{}]*\)\s*)\s*\{"
)
_MEANINGFUL_SITE_RE = re.compile(
    r"(?:msg|text|help|tutorial|guide|shop|arena|chapter|title|desc|name|"
    r"talk|support|trainee|prep|objective|goal|string|GetStringFromIndex|"
    r"StartPrepErrorHelpbox|StartCgText|PutSioText|TEXTSHOW|BROWNBOXTEXT|"
    r"Text_BG|WM_TEXT)",
    re.IGNORECASE,
)
_EVENT_CALL_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*\((.*)\)\s*$")
_INTEGER_RE = re.compile(
    r"^(?:MSG_([0-9A-Fa-f]+)|0[xX]([0-9A-Fa-f]+)|([0-9]+))$"
)
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
    opcode_path: Tuple[str, ...]
    source_symbol: str
    source_path: str
    source_line: int


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


def _token_value(match: re.Match[str]) -> int:
    return int(match.group(1) or match.group(2), 16)


def _normalized_site_line(line: str) -> str:
    line = re.sub(r"/\*.*?\*/", "", line)
    line = line.split("//", 1)[0]
    line = _SOURCE_TOKEN_RE.sub("<MSG>", line)
    return re.sub(r"\s+", " ", line).strip()


def _site_index(root: Path, wanted: Set[int]) -> Dict[int, List[Dict[str, Any]]]:
    result: Dict[int, List[Dict[str, Any]]] = defaultdict(list)
    slot_counts: Counter[Tuple[str, str]] = Counter()
    for base in ("src", "include"):
        directory = root / base
        if not directory.is_dir():
            continue
        for path in sorted(directory.rglob("*")):
            if path.suffix not in (".c", ".h") or path.stat().st_size > 600_000:
                continue
            if path.name == "msg.h" and path.parent.name == "constants":
                continue
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
            except UnicodeDecodeError:
                continue
            symbol = path.stem
            for line_number, line in enumerate(lines, 1):
                if definition := _DEFINITION_RE.search(line):
                    candidate = definition.group(1)
                    if candidate not in ("for", "if", "switch", "while"):
                        symbol = candidate
                matches = list(_SOURCE_TOKEN_RE.finditer(line))
                if not matches:
                    continue
                normalized = _normalized_site_line(line)
                context = f"{path.name} {symbol} {line}"
                if not _MEANINGFUL_SITE_RE.search(context):
                    continue
                for literal_index, match in enumerate(matches):
                    message_id = _token_value(match)
                    if message_id not in wanted:
                        continue
                    count_key = (symbol, normalized)
                    ordinal = slot_counts[count_key]
                    slot_counts[count_key] += 1
                    opcode_hash = _sha256_bytes(normalized.encode("utf-8"))
                    result[message_id].append(
                        {
                            "line": line_number,
                            "opcode_path": normalized[:160],
                            "opcode_path_sha256": opcode_hash,
                            "path": str(path.relative_to(root)),
                            "slot": (
                                f"{symbol}/opcode={opcode_hash[:16]}"
                                f"/literal={literal_index}/occurrence={ordinal}"
                            ),
                            "symbol": symbol,
                            "literal_index": literal_index,
                        }
                    )
    return {
        message_id: sorted(
            sites,
            key=lambda site: (
                site["path"],
                site["line"],
                site["literal_index"],
            ),
        )[:3]
        for message_id, sites in result.items()
    }


def _parse_event_int(value: str) -> Optional[int]:
    match = _INTEGER_RE.fullmatch(value.strip())
    if not match:
        return None
    if match.group(1) or match.group(2):
        return int(match.group(1) or match.group(2), 16)
    return int(match.group(3), 10)


def _canonical_event_ops(body: str) -> List[Tuple[str, Optional[int], int]]:
    raw: List[Tuple[str, List[str], int]] = []
    for line_number, line in enumerate(body.splitlines(), 1):
        match = _EVENT_CALL_RE.fullmatch(line.strip())
        if not match:
            continue
        raw.append(
            (
                match.group(1),
                [field.strip() for field in match.group(2).split(",")],
                line_number,
            )
        )

    result: List[Tuple[str, Optional[int], int]] = []
    index = 0
    while index < len(raw):
        opcode, args, line = raw[index]
        if opcode == "Text_BG" and len(args) >= 2:
            result.append(("SET_BACKGROUND", None, line))
            result.append(("MESSAGE", _parse_event_int(args[1]), line))
            index += 1
            continue
        if opcode == "SetBackground":
            result.append(("SET_BACKGROUND", None, line))
            index += 1
            continue
        if opcode == "SVAL" and len(args) >= 2 and args[0] == "EVT_SLOT_2":
            if (
                index + 2 < len(raw)
                and raw[index + 1][0] == "SVAL"
                and raw[index + 1][1][0] == "EVT_SLOT_3"
                and raw[index + 2][0] == "CALL"
                and "Event_TextWithBG" in ",".join(raw[index + 2][1])
            ):
                result.append(("SET_BACKGROUND", None, line))
                result.append(
                    (
                        "MESSAGE",
                        _parse_event_int(raw[index + 1][1][1]),
                        raw[index + 1][2],
                    )
                )
                index += 3
                continue
            if (
                index + 1 < len(raw)
                and raw[index + 1][0] == "CALL"
                and "SetBackground" in ",".join(raw[index + 1][1])
            ):
                result.append(("SET_BACKGROUND", None, line))
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
            result.append(("MESSAGE", _parse_event_int(args[1]), line))
            index += 2
            continue
        if (
            opcode == "SVAL"
            and len(args) >= 2
            and args[0] == "EVT_SLOT_1"
            and index + 1 < len(raw)
            and raw[index + 1][0] == "SENQUEUE1"
        ):
            result.append(("MESSAGE_QUEUE", _parse_event_int(args[1]), line))
            index += 2
            continue
        message_id = None
        if opcode in _DIRECT_EVENT_MESSAGES:
            argument = _DIRECT_EVENT_MESSAGES[opcode]
            if len(args) > argument:
                message_id = _parse_event_int(args[argument])
            opcode = "MESSAGE"
        result.append((_EVENT_OPCODE_ALIASES.get(opcode, opcode), message_id, line))
        index += 1
    return result


def _event_nodes(
    definitions: Mapping[str, Tuple[str, Path]],
    *,
    root: Path,
    wanted: Set[int],
) -> Tuple[EventMessageNode, ...]:
    nodes = []
    for symbol, (body, path) in definitions.items():
        operations = _canonical_event_ops(body)
        for index, (opcode, message_id, line) in enumerate(operations):
            if message_id not in wanted or opcode not in ("MESSAGE", "MESSAGE_QUEUE"):
                continue
            window = operations[max(0, index - 5) : index + 6]
            opcode_path = tuple(
                "MESSAGE" if offset == index else item[0]
                for offset, item in enumerate(
                    window,
                    start=max(0, index - 5),
                )
            )
            nodes.append(
                EventMessageNode(
                    message_id=message_id,
                    opcode_path=opcode_path,
                    source_symbol=symbol,
                    source_path=str(path.relative_to(root)),
                    source_line=line,
                )
            )
    return tuple(nodes)


def align_event_subgroups(
    target_nodes: Sequence[EventMessageNode],
    source_nodes: Sequence[EventMessageNode],
    accepted_pairs: Set[Tuple[int, int]],
) -> Dict[Tuple[int, int], Tuple[EventMessageNode, EventMessageNode]]:
    """Align matching opcode-path subgroups without requiring equal symbol counts."""

    by_target_path: Dict[Tuple[str, ...], List[EventMessageNode]] = defaultdict(list)
    by_source_path: Dict[Tuple[str, ...], List[EventMessageNode]] = defaultdict(list)
    for node in target_nodes:
        by_target_path[node.opcode_path].append(node)
    for node in source_nodes:
        by_source_path[node.opcode_path].append(node)

    result = {}
    for opcode_path in sorted(set(by_target_path) & set(by_source_path)):
        for target in by_target_path[opcode_path]:
            for source in by_source_path[opcode_path]:
                pair = (target.message_id, source.message_id)
                if pair not in accepted_pairs:
                    continue
                previous = result.get(pair)
                candidate = (target, source)
                if previous is None or (
                    candidate[0].source_path,
                    candidate[0].source_line,
                    candidate[1].source_path,
                    candidate[1].source_line,
                ) < (
                    previous[0].source_path,
                    previous[0].source_line,
                    previous[1].source_path,
                    previous[1].source_line,
                ):
                    result[pair] = candidate
    return result


def _event_alignment(
    fe8u_root: Path,
    fe8j_root: Path,
    *,
    fallback_targets: Set[int],
    accepted_pairs: Set[Tuple[int, int]],
) -> Dict[Tuple[int, int], Tuple[EventMessageNode, EventMessageNode]]:
    fe8u_paths = [
        path
        for path in (fe8u_root / "src/events").rglob("*")
        if path.suffix in (".c", ".h")
    ]
    fe8j_paths = [
        path
        for path in (fe8j_root / "src/data").glob("EventScr_*_ref/*.c")
    ]
    for relative in (
        "src/data/frontier_df3_eventscr_ch/frontier_df3_eventscr_ch.c",
        "src/events_wm.c",
    ):
        path = fe8j_root / relative
        if path.is_file():
            fe8j_paths.append(path)
    target_nodes = _event_nodes(
        _parse_script_definitions(fe8u_paths),
        root=fe8u_root,
        wanted=fallback_targets,
    )
    source_nodes = _event_nodes(
        _parse_script_definitions(fe8j_paths),
        root=fe8j_root,
        wanted={source for _, source in accepted_pairs},
    )
    return align_event_subgroups(target_nodes, source_nodes, accepted_pairs)


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


def _trainee_slot(target_id: int) -> Optional[str]:
    if 0x0C44 <= target_id <= 0x0C47:
        return f"trainee/ross/talk[{target_id - 0x0C43}]"
    if 0x0C48 <= target_id <= 0x0C4C:
        return f"trainee/amelia/talk[{target_id - 0x0C47}]"
    if 0x0C4D <= target_id <= 0x0C51:
        return f"trainee/ewan/talk[{target_id - 0x0C4C}]"
    return None


def _ch14b_slot(target_id: int) -> Optional[str]:
    if 0x0AFA <= target_id <= 0x0AFF:
        return f"chapter=Ch14B/table=beginning/opcode-message[{target_id - 0x0AF9}]"
    if target_id == 0x0B00:
        return "chapter=Ch14B/table=location/slot=rennac/opcode-message[1]"
    if 0x0B05 <= target_id <= 0x0B10:
        return f"chapter=Ch14B/table=ending/opcode-message[{target_id - 0x0B04}]"
    return None


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
    event_alignment = _event_alignment(
        fe8u_root,
        fe8j_root,
        fallback_targets=fallback_targets,
        accepted_pairs=accepted_pairs,
    )

    proposals = []
    collisions = []
    proposed_targets: Set[int] = set()
    context_targets: Set[int] = set()
    ch14b_pairs = {
        **{target: 0x0ABB + (target - 0x0AFA) for target in range(0x0AFA, 0x0B00)},
        0x0B00: 0x0AC1,
        **{target: 0x0AC6 + (target - 0x0B05) for target in range(0x0B05, 0x0B11)},
    }
    trainee_pairs = {
        target: 0x0C04 + (target - 0x0C44)
        for target in range(0x0C44, 0x0C52)
    }

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
        aligned = event_alignment.get((target_id, source_id))
        trainee_slot = _trainee_slot(target_id)
        ch14b_slot = _ch14b_slot(target_id)
        if target_id in ch14b_pairs and ch14b_pairs[target_id] != source_id:
            raise StructuralCompletionError(
                f"{format_message_id(target_id)} violates the pinned Ch14B pair"
            )
        if target_id in trainee_pairs and trainee_pairs[target_id] != source_id:
            raise StructuralCompletionError(
                f"{format_message_id(target_id)} violates the trainee correction"
            )

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
        if aligned is not None:
            target_node, source_node = aligned
            confidence = "high"
            basis = "event-opcode-path"
            evidence.update(
                {
                    "basis": basis,
                    "opcode_path": list(target_node.opcode_path),
                    "source_event_site": {
                        "line": source_node.source_line,
                        "path": source_node.source_path,
                        "symbol": source_node.source_symbol,
                    },
                    "target_event_site": {
                        "line": target_node.source_line,
                        "path": target_node.source_path,
                        "symbol": target_node.source_symbol,
                    },
                }
            )
            semantic_key = (
                f"{target_node.source_symbol}/opcode-path/"
                f"{_sha256_bytes('|'.join(target_node.opcode_path).encode())[:16]}"
            )
            family = "chapter-event"
        if ch14b_slot:
            confidence = "high"
            evidence["basis"] = "pinned-event-table-opcode-path"
            evidence["pinned_group"] = "Ch14B"
            evidence["source_event_table"] = {
                "path": (
                    "src/data/frontier_df3_eventscr_ch/"
                    "frontier_df3_eventscr_ch.c"
                ),
                "slot": ch14b_slot,
            }
            evidence["target_event_table"] = {
                "path": "src/events/ch14b-eventscript.h",
                "slot": ch14b_slot,
            }
            semantic_key = ch14b_slot
            family = "chapter-event"
        if trainee_slot:
            confidence = "high"
            evidence["basis"] = "trainee-function-message-array"
            evidence["source_table"] = {
                "path": "FE8J indexed payload plus authorized table map",
                "slot": trainee_slot,
            }
            evidence["target_table"] = {
                "path": "src/classchg-event.c",
                "slot": trainee_slot,
            }
            semantic_key = trainee_slot
            family = "trainee-prep"

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
                    "reference_site_evidence": True,
                    "target_in_bounds": 0 <= target_id < target_count,
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
        validation = proposal.get("validation")
        expected_validation = {
            "reference_site_evidence": True,
            "target_in_bounds": True,
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

    residual_ids = []
    for index, row in enumerate(residual):
        target = row.get("target_id")
        if not isinstance(target, str) or not re.fullmatch(r"0x[0-9A-F]{4}", target):
            raise StructuralCompletionError(
                f"completion.residual_targets[{index}].target_id is invalid"
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
