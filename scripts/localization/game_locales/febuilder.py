"""Deterministic FEBuilder FE8 text-ID alignment evidence.

The generated ledger is evidence-only. It deliberately cannot modify or
promote rows into the authoritative FE8U target map.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from .coverage import load_fe8u_target_ids
from .crosswalk import validate_evidence_document
from .parsers import FE8J_MAX_INDEXED_ID, parse_hash_indexed

FEBUILDER_SOURCE_SHA256 = (
    "d9f0fc8ede5820bb4b93299ad08286055a56037bb1fbdb6bf589ad1f7af16734"
)
FEBUILDER_UPSTREAM_COMMIT = "2e4396efd14638ee03ada051eedfa40b66ff0ea3"
FEBUILDER_PARSER_REFERENCES = (
    {
        "logical_path": "FEBuilderGBA/TranslateTextUtil.cs",
        "sha256": "0b965691a819133705c0b94e4474da68c25b2a133b01615c69cc99f3385faec4",
    },
    {
        "logical_path": "FEBuilderGBA/U.cs",
        "sha256": "fc1dc7ccd0ff3af089f428f94eaa8fa8419cd0368ec11756b965f94e429d895f",
    },
)

FEBUILDER_INDEXED_ROW_COUNT = 3339
FEBUILDER_NUMERIC_PAIR_COUNT = 3006
FEBUILDER_POINTER_ROW_COUNT = 110
FEBUILDER_FE8_ROM_START = 0x08000200
FEBUILDER_FE8_ROM_END = 0x09000000

EVIDENCE_SCHEMA_VERSION = 1
EVIDENCE_KIND = "febuilder-fe8-alignment-evidence"
MARK_AGREES = "agrees-with-structural"
MARK_CONFLICTS = "conflicts"
MARK_UNIQUE = "unique-uncontested"
MARK_COLLISION = "collision-needs-context"
EVIDENCE_MARKS = (MARK_AGREES, MARK_CONFLICTS, MARK_UNIQUE, MARK_COLLISION)

PINNED_STRUCTURAL_CONFLICT_TARGETS = (
    0x01A0,
    0x01A1,
    0x01BC,
    0x0647,
    0x093E,
    0x094E,
    0x0973,
    0x097D,
    0x097E,
    0x0980,
    0x0988,
    0x0989,
)
PINNED_UNRESOLVED_COLLISION_TARGETS = (
    0x0010,
    0x053B,
    0x053D,
    0x0581,
    0x0601,
    0x060C,
    0x0647,
    0x0924,
    0x0925,
    0x0928,
    0x093B,
    0x099C,
    0x099D,
    0x09A2,
    0x0AAF,
    0x0ACA,
    0x0AF9,
)


class FeBuilderEvidenceError(ValueError):
    """Raised when FEBuilder evidence violates the pinned contract."""


@dataclass(frozen=True)
class FeBuilderMapRow:
    source_line: int
    source_line_sha256: str
    row_type: str
    source_token: str
    target_token: str
    source_key: int
    target_key: int
    parser_action: str
    replacement_text: Optional[str]


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_text(text: str) -> str:
    return _sha256_bytes(text.encode("utf-8"))


def _sha256_path(path: Path) -> str:
    return _sha256_bytes(Path(path).read_bytes())


def _format_id(value: int) -> str:
    return f"0x{value:04X}"


def _format_raw_key(value: int) -> str:
    return f"0x{value:08X}"


def _canonical_record_hash(record: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        record,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return _sha256_bytes(encoded)


def canonical_json_bytes(data: Any) -> bytes:
    return (
        json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def _is_comment(line: str) -> bool:
    if not line:
        return True
    if line[0] in ("#", ";"):
        return True
    return line.startswith("//") or line.startswith("--")


def _clip_comment_index(text: str, marker: str) -> int:
    index = text.find(marker)
    if index < 0:
        return -1
    if index == 0:
        return 0
    if text[index - 1] in (" ", "\t"):
        return index - 1
    return -1


def _clip_comment(text: str) -> str:
    for marker in ("{J}", "{U}", "//"):
        end = _clip_comment_index(text, marker)
        if end >= 0:
            text = text[:end]
    return text


def _atoh(text: str) -> int:
    end = 0
    while end < len(text) and text[end] in "0123456789abcdefABCDEF":
        end += 1
    if end == 0:
        return 0
    return int(text[:end], 16)


def _is_safety_pointer(value: int) -> bool:
    return FEBUILDER_FE8_ROM_START <= value < FEBUILDER_FE8_ROM_END


def _row_type(source_key: int) -> str:
    if 0 <= source_key <= FE8J_MAX_INDEXED_ID:
        return "indexed"
    if _is_safety_pointer(source_key):
        return "pointer"
    return "unsupported"


def parse_febuilder_text_id_map(
    data: bytes,
    *,
    source_name: str = "translate_textid_FE8.txt",
) -> Tuple[FeBuilderMapRow, ...]:
    """Parse FEBuilder's map with its comment, tab, and leading-hex rules."""

    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise FeBuilderEvidenceError(f"{source_name}: source is not UTF-8") from error

    rows: List[FeBuilderMapRow] = []
    for line_number, raw_line in enumerate(text.splitlines(), 1):
        if _is_comment(raw_line):
            continue

        line = _clip_comment(raw_line)
        fields = line.split("\t")
        source_token = fields[0]
        source_key = _atoh(source_token)
        row_type = _row_type(source_key)
        target_token = fields[1] if len(fields) >= 2 else ""
        target_key = _atoh(target_token)
        replacement_pos = target_token.find("|")
        replacement_text = (
            target_token[replacement_pos + 1 :] if replacement_pos >= 0 else None
        )

        if len(fields) < 2:
            parser_action = "skip-missing-columns"
        elif source_key <= 0:
            parser_action = "skip-source-zero"
        elif replacement_pos >= 0:
            parser_action = "literal-substitution"
        elif target_key <= 0:
            parser_action = "notfound"
        elif _is_safety_pointer(target_key):
            parser_action = "decode-target-pointer"
        else:
            parser_action = "decode-target"

        rows.append(
            FeBuilderMapRow(
                source_line=line_number,
                source_line_sha256=_sha256_text(raw_line),
                row_type=row_type,
                source_token=source_token,
                target_token=target_token,
                source_key=source_key,
                target_key=target_key,
                parser_action=parser_action,
                replacement_text=replacement_text,
            )
        )

    _validate_source_profile(rows, data, source_name=source_name)
    return tuple(rows)


def _validate_source_profile(
    rows: Sequence[FeBuilderMapRow],
    data: bytes,
    *,
    source_name: str,
) -> None:
    sha256 = _sha256_bytes(data)
    if sha256 != FEBUILDER_SOURCE_SHA256:
        raise FeBuilderEvidenceError(
            f"{source_name}: expected SHA-256 {FEBUILDER_SOURCE_SHA256}, got {sha256}"
        )

    indexed_rows = [row for row in rows if row.row_type == "indexed"]
    pointer_rows = [row for row in rows if row.row_type == "pointer"]
    numeric_pairs = sum(row.target_key > 0 for row in indexed_rows)
    if len(indexed_rows) != FEBUILDER_INDEXED_ROW_COUNT:
        raise FeBuilderEvidenceError(
            f"{source_name}: expected {FEBUILDER_INDEXED_ROW_COUNT} indexed rows, "
            f"got {len(indexed_rows)}"
        )
    if numeric_pairs != FEBUILDER_NUMERIC_PAIR_COUNT:
        raise FeBuilderEvidenceError(
            f"{source_name}: expected {FEBUILDER_NUMERIC_PAIR_COUNT} numeric pairs, "
            f"got {numeric_pairs}"
        )
    if len(pointer_rows) != FEBUILDER_POINTER_ROW_COUNT:
        raise FeBuilderEvidenceError(
            f"{source_name}: expected {FEBUILDER_POINTER_ROW_COUNT} pointer rows, "
            f"got {len(pointer_rows)}"
        )
    if any(row.row_type == "unsupported" for row in rows):
        raise FeBuilderEvidenceError(f"{source_name}: unsupported source-key row found")

    expected_ids = list(range(FE8J_MAX_INDEXED_ID + 1))
    actual_ids = [row.source_key for row in indexed_rows]
    if actual_ids != expected_ids:
        raise FeBuilderEvidenceError(
            f"{source_name}: indexed source keys are not sequential through "
            f"{_format_id(FE8J_MAX_INDEXED_ID)}"
        )


def import_febuilder_source(source_path: Path, vendored_path: Path) -> bytes:
    """Validate and vendor the exact pinned FEBuilder map source."""

    data = Path(source_path).read_bytes()
    parse_febuilder_text_id_map(data, source_name=str(source_path))
    vendored_path = Path(vendored_path)
    vendored_path.parent.mkdir(parents=True, exist_ok=True)
    vendored_path.write_bytes(data)
    return data


def _load_indexed_payloads(path: Path, locale_id: str) -> Dict[int, str]:
    messages = parse_hash_indexed(
        Path(path).read_text(encoding="utf-8"),
        source_name=f"{locale_id} indexed payload",
    )
    return {message.id: message.text for message in messages}


def _load_raw_payloads(path: Path) -> Tuple[Dict[int, Dict[str, Any]], Dict[str, Dict[str, Any]]]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise FeBuilderEvidenceError(f"{path}: invalid raw payload JSON") from error
    records = data.get("records")
    if not isinstance(records, list):
        raise FeBuilderEvidenceError(f"{path}: records must be an array")

    by_address: Dict[int, Dict[str, Any]] = {}
    by_import_id: Dict[str, Dict[str, Any]] = {}
    for index, record in enumerate(records):
        try:
            import_id = record["import_id"]
            address = int(record["provenance"]["address"], 16)
            text = record["text"]
        except (KeyError, TypeError, ValueError) as error:
            raise FeBuilderEvidenceError(
                f"{path}: malformed raw record at index {index}"
            ) from error
        if not isinstance(import_id, str) or not isinstance(text, str):
            raise FeBuilderEvidenceError(
                f"{path}: malformed raw record at index {index}"
            )
        if address in by_address or import_id in by_import_id:
            raise FeBuilderEvidenceError(f"{path}: duplicate raw payload identity")
        by_address[address] = record
        by_import_id[import_id] = record
    return by_address, by_import_id


def _structural_identity(source: Mapping[str, Any]) -> Tuple[str, str]:
    kind = source.get("kind")
    if kind == "indexed":
        return ("indexed", source["id"])
    if kind == "raw":
        return ("raw", source["import_id"])
    raise FeBuilderEvidenceError(f"unsupported structural source kind {kind!r}")


def _identity_document(identity: Tuple[str, str]) -> Dict[str, str]:
    if identity[0] == "indexed":
        return {"id": identity[1], "kind": "indexed"}
    return {"import_id": identity[1], "kind": "raw"}


def _load_structural_sources(
    path: Path,
    *,
    target_count: int,
    repo_root: Path,
) -> Tuple[Dict[int, Tuple[str, str]], Dict[int, List[Dict[str, Any]]]]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise FeBuilderEvidenceError(f"{path}: invalid structural JSON") from error
    validate_evidence_document(data, target_count=target_count, repo_root=repo_root)

    identities: Dict[int, Tuple[str, str]] = {}
    records_by_target: Dict[int, List[Dict[str, Any]]] = defaultdict(list)
    for record in data["records"]:
        target_id = int(record["target_id"], 16)
        identity = _structural_identity(record["source"])
        previous = identities.setdefault(target_id, identity)
        if previous != identity:
            raise FeBuilderEvidenceError(
                f"{path}: target {_format_id(target_id)} has conflicting identities"
            )
        records_by_target[target_id].append(record)
    return identities, records_by_target


def _source_key_document(row: FeBuilderMapRow) -> str:
    if row.row_type == "pointer":
        return _format_raw_key(row.source_key)
    return _format_id(row.source_key)


def _non_candidate_document(
    row: FeBuilderMapRow,
    *,
    exclusion_reason: str,
) -> Dict[str, Any]:
    document: Dict[str, Any] = {
        "exclusion_reason": exclusion_reason,
        "parser_action": row.parser_action,
        "row_type": row.row_type,
        "source_key": _source_key_document(row),
        "source_line": row.source_line,
        "source_line_sha256": row.source_line_sha256,
        "source_token": row.source_token,
        "target_token": row.target_token,
    }
    if row.target_key > 0:
        document["parsed_target_key"] = (
            _format_raw_key(row.target_key)
            if _is_safety_pointer(row.target_key)
            else _format_id(row.target_key)
        )
    if row.replacement_text is not None:
        document["replacement_text_sha256"] = _sha256_text(row.replacement_text)
    return document


def _candidate_from_indexed(
    row: FeBuilderMapRow,
    ja_payloads: Mapping[int, str],
    zh_payloads: Mapping[int, str],
) -> Tuple[Dict[str, Any], Tuple[str, str], str]:
    try:
        ja_text = ja_payloads[row.source_key]
        zh_text = zh_payloads[row.source_key]
    except KeyError as error:
        raise FeBuilderEvidenceError(
            f"line {row.source_line}: missing indexed payload "
            f"{_format_id(row.source_key)}"
        ) from error
    source_id = _format_id(row.source_key)
    zh_hash = _sha256_text(zh_text)
    return (
        {
            "alignment_payload": {
                "locale_id": "zh-Hans",
                "sha256": zh_hash,
            },
            "parser_action": row.parser_action,
            "payloads": {
                "ja": {
                    "id": source_id,
                    "kind": "indexed",
                    "sha256": _sha256_text(ja_text),
                },
                "zh-Hans": {
                    "id": source_id,
                    "kind": "indexed",
                    "sha256": zh_hash,
                },
            },
            "row_type": "indexed",
            "source": {
                "id": source_id,
                "kind": "indexed",
                "layout": "FE8J",
            },
            "source_line": row.source_line,
            "source_line_sha256": row.source_line_sha256,
            "source_token": row.source_token,
            "target_token": row.target_token,
        },
        ("indexed", source_id),
        zh_hash,
    )


def _candidate_from_pointer(
    row: FeBuilderMapRow,
    raw_record: Mapping[str, Any],
) -> Tuple[Dict[str, Any], Tuple[str, str], str]:
    import_id = raw_record["import_id"]
    raw_key = _format_raw_key(row.source_key)
    zh_hash = _sha256_text(raw_record["text"])
    return (
        {
            "alignment_payload": {
                "locale_id": "zh-Hans",
                "sha256": zh_hash,
            },
            "parser_action": row.parser_action,
            "payloads": {
                "zh-Hans": {
                    "address": raw_key,
                    "import_id": import_id,
                    "kind": "raw",
                    "sha256": zh_hash,
                }
            },
            "row_type": "pointer",
            "source": {
                "fe_builder_semantics": "dereference-source-pointer-slot",
                "kind": "pointer",
                "raw_import_id": import_id,
                "raw_key": raw_key,
            },
            "source_line": row.source_line,
            "source_line_sha256": row.source_line_sha256,
            "source_token": row.source_token,
            "target_token": row.target_token,
        },
        ("raw", import_id),
        zh_hash,
    )


def _structural_document(
    identity: Tuple[str, str],
    records: Sequence[Mapping[str, Any]],
) -> Dict[str, Any]:
    return {
        "confidence": sorted({record["confidence"] for record in records}),
        "evidence_kinds": sorted({record["evidence_kind"] for record in records}),
        "record_count": len(records),
        "record_sha256": sorted(_canonical_record_hash(record) for record in records),
        "source": _identity_document(identity),
        "source_keys": sorted({record["source_key"] for record in records}),
        "subsystems": sorted({record["subsystem"] for record in records}),
    }


def _input_document(logical_path: str, path: Path) -> Dict[str, str]:
    return {"logical_path": logical_path, "sha256": _sha256_path(path)}


def build_febuilder_alignment_evidence(
    *,
    source_path: Path,
    ja_indexed_path: Path,
    zh_indexed_path: Path,
    raw_path: Path,
    structural_path: Path,
    target_header_path: Path,
    repo_root: Path,
) -> Dict[str, Any]:
    """Build the deterministic, non-authoritative FEBuilder evidence ledger."""

    source_path = Path(source_path)
    source_data = source_path.read_bytes()
    rows = parse_febuilder_text_id_map(source_data, source_name=str(source_path))
    ja_payloads = _load_indexed_payloads(ja_indexed_path, "ja")
    zh_payloads = _load_indexed_payloads(zh_indexed_path, "zh-Hans")
    raw_by_address, _ = _load_raw_payloads(raw_path)
    target_ids = load_fe8u_target_ids(target_header_path)
    target_count = len(target_ids)
    structural_identities, structural_records = _load_structural_sources(
        structural_path,
        target_count=target_count,
        repo_root=repo_root,
    )

    candidate_rows: Dict[int, List[Dict[str, Any]]] = defaultdict(list)
    candidate_identities: Dict[int, List[Tuple[str, str]]] = defaultdict(list)
    candidate_payload_hashes: Dict[int, List[str]] = defaultdict(list)
    non_candidates: List[Dict[str, Any]] = []

    for row in rows:
        if row.parser_action != "decode-target":
            non_candidates.append(
                _non_candidate_document(
                    row,
                    exclusion_reason={
                        "skip-missing-columns": "no-target-column",
                        "skip-source-zero": "FEBuilder-skips-source-zero",
                        "literal-substitution": "FEBuilder-uses-literal-not-target-payload",
                        "notfound": "FEBuilder-records-NOTFOUND",
                        "decode-target-pointer": "destination-pointer-is-not-a-target-id",
                    }[row.parser_action],
                )
            )
            continue
        if row.target_key >= target_count:
            non_candidates.append(
                _non_candidate_document(
                    row,
                    exclusion_reason="target-out-of-bounds",
                )
            )
            continue

        if row.row_type == "indexed":
            candidate, identity, payload_hash = _candidate_from_indexed(
                row, ja_payloads, zh_payloads
            )
        elif row.row_type == "pointer":
            raw_record = raw_by_address.get(row.source_key)
            if raw_record is None:
                non_candidates.append(
                    _non_candidate_document(
                        row,
                        exclusion_reason="missing-normalized-raw-address",
                    )
                )
                continue
            candidate, identity, payload_hash = _candidate_from_pointer(
                row, raw_record
            )
        else:
            raise FeBuilderEvidenceError(
                f"line {row.source_line}: unsupported row type {row.row_type}"
            )

        candidate_rows[row.target_key].append(candidate)
        candidate_identities[row.target_key].append(identity)
        candidate_payload_hashes[row.target_key].append(payload_hash)

    targets = []
    mark_counts: Counter[str] = Counter()
    differing_payload_collision_count = 0
    structurally_resolved_collision_count = 0
    target_duplicate_group_count = 0
    same_payload_duplicate_group_count = 0

    for target_id in sorted(candidate_rows):
        candidates = candidate_rows[target_id]
        identities = candidate_identities[target_id]
        payload_hashes = candidate_payload_hashes[target_id]
        identity_set = set(identities)
        payload_set = set(payload_hashes)
        structural_identity = structural_identities.get(target_id)
        agrees = structural_identity is not None and structural_identity in identity_set
        comparable_identities = (
            {
                identity
                for identity in identity_set
                if structural_identity is not None
                and identity[0] == structural_identity[0]
            }
            if structural_identity is not None
            else set()
        )
        conflicts = bool(
            structural_identity is not None
            and comparable_identities
            and not agrees
        )
        differing_payloads = len(payload_set) > 1
        unresolved_collision = differing_payloads and not agrees

        marks = []
        if agrees:
            marks.append(MARK_AGREES)
        if conflicts:
            marks.append(MARK_CONFLICTS)
        if unresolved_collision:
            marks.append(MARK_COLLISION)
        if not marks:
            marks.append(MARK_UNIQUE)
        for mark in marks:
            mark_counts[mark] += 1

        if len(candidates) > 1:
            target_duplicate_group_count += 1
            if not differing_payloads:
                same_payload_duplicate_group_count += 1
        if differing_payloads:
            differing_payload_collision_count += 1
            if agrees:
                structurally_resolved_collision_count += 1

        target_document: Dict[str, Any] = {
            "candidates": candidates,
            "collision_group": {
                "candidate_count": len(candidates),
                "differing_payloads": differing_payloads,
                "distinct_alignment_payload_count": len(payload_set),
                "distinct_source_identity_count": len(identity_set),
                "resolved_by_structural_identity": bool(
                    differing_payloads and agrees
                ),
            },
            "marks": marks,
            "promotion_eligible": False,
            "target_id": _format_id(target_id),
        }
        if structural_identity is not None:
            target_document["structural_evidence"] = _structural_document(
                structural_identity,
                structural_records[target_id],
            )
        targets.append(target_document)

    structural_conflicts = tuple(
        int(target["target_id"], 16)
        for target in targets
        if MARK_CONFLICTS in target["marks"]
    )
    unresolved_collisions = tuple(
        int(target["target_id"], 16)
        for target in targets
        if MARK_COLLISION in target["marks"]
    )
    if structural_conflicts != PINNED_STRUCTURAL_CONFLICT_TARGETS:
        raise FeBuilderEvidenceError(
            "structural conflict targets changed: "
            + ", ".join(_format_id(value) for value in structural_conflicts)
        )
    if unresolved_collisions != PINNED_UNRESOLVED_COLLISION_TARGETS:
        raise FeBuilderEvidenceError(
            "unresolved differing-payload collision targets changed: "
            + ", ".join(_format_id(value) for value in unresolved_collisions)
        )

    source_key_groups: Dict[Tuple[str, int], List[FeBuilderMapRow]] = defaultdict(list)
    for row in rows:
        source_key_groups[(row.row_type, row.source_key)].append(row)
    source_duplicate_groups = []
    for (row_type, source_key), group in sorted(source_key_groups.items()):
        if len(group) < 2:
            continue
        source_duplicate_groups.append(
            {
                "row_type": row_type,
                "rows": [
                    {
                        "parser_action": row.parser_action,
                        "source_line": row.source_line,
                        "target_token": row.target_token,
                    }
                    for row in group
                ],
                "source_key": (
                    _format_raw_key(source_key)
                    if row_type == "pointer"
                    else _format_id(source_key)
                ),
            }
        )

    action_counts = Counter(row.parser_action for row in rows)
    evidence = {
        "authoritative": False,
        "inputs": {
            "febuilder_map": _input_document(
                "texts/locales/source/febuilder/translate_textid_FE8.txt",
                source_path,
            ),
            "normalized_ja": _input_document(
                "texts/locales/ja/indexed.txt", ja_indexed_path
            ),
            "normalized_zh_hans": _input_document(
                "texts/locales/zh-Hans/indexed.txt", zh_indexed_path
            ),
            "raw_zh_hans": _input_document(
                "texts/locales/zh-Hans/raw.json", raw_path
            ),
            "structural_evidence": _input_document(
                "texts/locales/mapping/fe8u_structural_evidence.json",
                structural_path,
            ),
            "target_header": _input_document(
                "include/constants/msg.h", target_header_path
            ),
        },
        "kind": EVIDENCE_KIND,
        "non_candidate_rows": non_candidates,
        "note": (
            "Independent FEBuilder evidence only. Conflicts and unresolved "
            "collisions are never promotion-eligible and this document does "
            "not modify the authoritative FE8U target map."
        ),
        "parser_provenance": {
            "fe8_rom_pointer_range": {
                "end_exclusive": _format_raw_key(FEBUILDER_FE8_ROM_END),
                "start": _format_raw_key(FEBUILDER_FE8_ROM_START),
            },
            "reference_files": list(FEBUILDER_PARSER_REFERENCES),
            "semantics": [
                "U.IsComment",
                "U.ClipComment",
                "U.atoh-leading-hex-prefix",
                "TranslateTextUtil-tab-columns",
                "TranslateTextUtil-source-pointer-dereference",
                "TranslateTextUtil-literal-substitution",
                "TranslateTextUtil-NOTFOUND",
            ],
            "upstream_commit": FEBUILDER_UPSTREAM_COMMIT,
        },
        "promotion_policy": {
            "auto_promote": False,
            "conflicts_may_promote": False,
            "unresolved_collisions_may_promote": False,
        },
        "schema_version": EVIDENCE_SCHEMA_VERSION,
        "source_duplicate_groups": source_duplicate_groups,
        "source_profile": {
            "indexed_numeric_pair_count": FEBUILDER_NUMERIC_PAIR_COUNT,
            "indexed_row_count": FEBUILDER_INDEXED_ROW_COUNT,
            "map_sha256": FEBUILDER_SOURCE_SHA256,
            "parser_action_counts": dict(sorted(action_counts.items())),
            "pointer_row_count": FEBUILDER_POINTER_ROW_COUNT,
            "source_row_count": len(rows),
        },
        "summary": {
            "candidate_row_count": sum(len(value) for value in candidate_rows.values()),
            "differing_payload_collision_count": differing_payload_collision_count,
            "mark_counts": dict(sorted(mark_counts.items())),
            "non_candidate_row_count": len(non_candidates),
            "same_payload_duplicate_group_count": same_payload_duplicate_group_count,
            "structural_conflict_count": len(structural_conflicts),
            "structural_conflict_targets": [
                _format_id(value) for value in structural_conflicts
            ],
            "structurally_resolved_collision_count": structurally_resolved_collision_count,
            "target_candidate_count": len(targets),
            "target_count": target_count,
            "target_duplicate_group_count": target_duplicate_group_count,
            "unresolved_differing_payload_collision_count": len(
                unresolved_collisions
            ),
            "unresolved_differing_payload_collision_targets": [
                _format_id(value) for value in unresolved_collisions
            ],
        },
        "targets": targets,
    }
    validate_febuilder_evidence_document(evidence, target_count=target_count)
    return evidence


def _require_dict(value: Any, field: str) -> Dict[str, Any]:
    if not isinstance(value, dict):
        raise FeBuilderEvidenceError(f"{field} must be an object")
    return value


def validate_febuilder_evidence_document(
    data: Any,
    *,
    target_count: int,
) -> None:
    """Validate the evidence-only authority and target/payload contract."""

    document = _require_dict(data, "evidence")
    if document.get("schema_version") != EVIDENCE_SCHEMA_VERSION:
        raise FeBuilderEvidenceError(
            f"evidence.schema_version must be {EVIDENCE_SCHEMA_VERSION}"
        )
    if document.get("kind") != EVIDENCE_KIND:
        raise FeBuilderEvidenceError(f"evidence.kind must be {EVIDENCE_KIND!r}")
    if document.get("authoritative") is not False:
        raise FeBuilderEvidenceError("evidence.authoritative must be false")

    promotion_policy = _require_dict(
        document.get("promotion_policy"), "evidence.promotion_policy"
    )
    if any(
        promotion_policy.get(field) is not False
        for field in (
            "auto_promote",
            "conflicts_may_promote",
            "unresolved_collisions_may_promote",
        )
    ):
        raise FeBuilderEvidenceError("evidence promotion policy must remain disabled")

    targets = document.get("targets")
    if not isinstance(targets, list):
        raise FeBuilderEvidenceError("evidence.targets must be an array")
    previous_target = -1
    conflict_targets = []
    collision_targets = []
    for index, target in enumerate(targets):
        target = _require_dict(target, f"evidence.targets[{index}]")
        target_token = target.get("target_id")
        if (
            not isinstance(target_token, str)
            or not target_token.startswith("0x")
            or len(target_token) != 6
        ):
            raise FeBuilderEvidenceError(
                f"evidence.targets[{index}].target_id must use 0xNNNN form"
            )
        try:
            target_id = int(target_token, 16)
        except ValueError as error:
            raise FeBuilderEvidenceError(
                f"evidence.targets[{index}].target_id is invalid"
            ) from error
        if target_id <= previous_target or target_id >= target_count:
            raise FeBuilderEvidenceError(
                "evidence targets must be unique, sorted, and in bounds"
            )
        previous_target = target_id
        if target.get("promotion_eligible") is not False:
            raise FeBuilderEvidenceError(
                f"evidence.targets[{index}].promotion_eligible must be false"
            )

        marks = target.get("marks")
        if (
            not isinstance(marks, list)
            or not marks
            or len(set(marks)) != len(marks)
            or any(mark not in EVIDENCE_MARKS for mark in marks)
        ):
            raise FeBuilderEvidenceError(
                f"evidence.targets[{index}].marks are invalid"
            )
        if MARK_UNIQUE in marks and len(marks) != 1:
            raise FeBuilderEvidenceError(
                f"evidence.targets[{index}].unique-uncontested must stand alone"
            )
        if MARK_AGREES in marks and MARK_CONFLICTS in marks:
            raise FeBuilderEvidenceError(
                f"evidence.targets[{index}] cannot agree and conflict"
            )
        if MARK_CONFLICTS in marks:
            conflict_targets.append(target_id)
        if MARK_COLLISION in marks:
            collision_targets.append(target_id)

        candidates = target.get("candidates")
        if not isinstance(candidates, list) or not candidates:
            raise FeBuilderEvidenceError(
                f"evidence.targets[{index}].candidates must be non-empty"
            )
        for candidate_index, candidate in enumerate(candidates):
            candidate = _require_dict(
                candidate,
                f"evidence.targets[{index}].candidates[{candidate_index}]",
            )
            payloads = _require_dict(
                candidate.get("payloads"),
                f"evidence.targets[{index}].candidates[{candidate_index}].payloads",
            )
            if not payloads:
                raise FeBuilderEvidenceError("candidate payloads must not be empty")
            for locale_id, payload in payloads.items():
                payload = _require_dict(payload, f"candidate payload {locale_id}")
                sha256 = payload.get("sha256")
                if (
                    not isinstance(sha256, str)
                    or len(sha256) != 64
                    or any(char not in "0123456789abcdef" for char in sha256)
                ):
                    raise FeBuilderEvidenceError(
                        f"candidate payload {locale_id} has invalid SHA-256"
                    )
                if payload.get("kind") == "indexed" and "id" not in payload:
                    raise FeBuilderEvidenceError(
                        f"candidate indexed payload {locale_id} lacks id"
                    )
                if payload.get("kind") == "raw" and (
                    "import_id" not in payload or "address" not in payload
                ):
                    raise FeBuilderEvidenceError(
                        f"candidate raw payload {locale_id} lacks identity"
                    )

    if tuple(conflict_targets) != PINNED_STRUCTURAL_CONFLICT_TARGETS:
        raise FeBuilderEvidenceError("pinned structural conflict targets changed")
    if tuple(collision_targets) != PINNED_UNRESOLVED_COLLISION_TARGETS:
        raise FeBuilderEvidenceError("pinned unresolved collision targets changed")
