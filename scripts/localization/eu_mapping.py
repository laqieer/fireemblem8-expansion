#!/usr/bin/env python3
"""Build the reviewed FE8EU-to-FE8U full-game message mapping."""

from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import re
import unicodedata
from pathlib import Path
from typing import Optional

from scripts.localization.game_catalog.build import encode_canonical_text
from scripts.localization.game_catalog.english_source import (
    load_english_source_entries,
)
from scripts.localization.game_locales.coverage import load_fe8u_target_ids
from scripts.localization.game_locales.parsers import parse_hash_indexed


ROOT = Path(__file__).resolve().parents[2]
EU_ROOT = ROOT / "texts" / "locales" / "eu"
MAPPING_PATH = ROOT / "texts" / "locales" / "mapping" / "fe8eu_to_fe8u.json"
ENGLISH_TEXTS_PATH = ROOT / "texts" / "texts.txt"
ENGLISH_DEFINITIONS_PATH = ROOT / "texts" / "textdefs.txt"
TARGET_HEADER_PATH = ROOT / "include" / "constants" / "msg.h"
EU_MANIFEST_PATH = EU_ROOT / "manifest.json"
EU_MAX_SOURCE_ID = 0x0D35
EU_SOURCE_COUNT = EU_MAX_SOURCE_ID + 1
EU_LOCALES = ("fr", "de", "es", "it")

AUTHORED_TRANSLATION_TARGETS = frozenset(
    {
        0x040C,
        0x07D2,
        0x0D4C,
        0x0D4D,
        0x0D4E,
        0x0D4F,
        0x0D50,
        0x0D51,
        0x0D52,
        0x0D54,
        0x0D55,
    }
)
ENGLISH_PRESERVE_TARGETS = frozenset({0x0884})
CONCAT_TARGETS = {
    0x0B61: (0x0B46, 0x0B47),
    0x0B88: (0x0B6E, 0x0B6F),
    0x0BFF: (0x0BE6, 0x0BE7),
    0x0C00: (0x0BE8, 0x0BE9),
}
LOW_SIMILARITY_REVIEW_THRESHOLD = 0.60


class EuMappingError(ValueError):
    """The EU target mapping or an authored target translation is invalid."""


def canonical_json_bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _load_eu_source(locale: str) -> tuple[str, ...]:
    path = EU_ROOT / f"indexed.{locale}.txt"
    messages = parse_hash_indexed(
        path.read_text(encoding="utf-8"),
        expected_last_id=EU_MAX_SOURCE_ID,
        source_name=str(path),
    )
    return tuple(message.text for message in messages)


def _visible(payload: bytes) -> str:
    output: list[str] = []
    index = 0
    while index < len(payload):
        value = payload[index]
        if value == 0:
            break
        if value == 0x10 and index + 2 < len(payload):
            output.append(" face ")
            index += 3
            continue
        if value == 0x80 and index + 1 < len(payload):
            output.append(" control ")
            index += 2
            continue
        if value < 0x20:
            output.append(" ")
            index += 1
            continue
        try:
            output.append(bytes((value,)).decode("utf-8"))
            index += 1
            continue
        except UnicodeDecodeError:
            pass

        # The committed English source has already normalized almost every
        # legacy printable. This fallback is only diagnostic for any remaining
        # single-byte FE text token.
        try:
            output.append(bytes((value,)).decode("cp1252"))
        except UnicodeDecodeError:
            output.append("?")
        index += 1

    normalized = unicodedata.normalize("NFKD", "".join(output))
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii").lower()
    return re.sub(r"[^a-z0-9]+", " ", ascii_text).strip()


def _similarity(left: str, right: str) -> float:
    if left == right:
        return 1.0
    return difflib.SequenceMatcher(
        None, left, right, autojunk=False
    ).ratio()


def _align_gap(
    target_visible: tuple[str, ...],
    source_visible: tuple[str, ...],
    target_start: int,
    target_end: int,
    source_start: int,
    source_end: int,
) -> list[tuple[Optional[int], Optional[int]]]:
    target_count = target_end - target_start
    source_count = source_end - source_start
    gap_penalty = -0.30
    match_bias = 0.25
    scores = [
        [0.0 for _ in range(source_count + 1)]
        for _ in range(target_count + 1)
    ]
    backtrack = [
        ["" for _ in range(source_count + 1)]
        for _ in range(target_count + 1)
    ]

    for index in range(1, target_count + 1):
        scores[index][0] = scores[index - 1][0] + gap_penalty
        backtrack[index][0] = "target"
    for index in range(1, source_count + 1):
        scores[0][index] = scores[0][index - 1] + gap_penalty
        backtrack[0][index] = "source"

    for target_index in range(1, target_count + 1):
        for source_index in range(1, source_count + 1):
            similarity = _similarity(
                target_visible[target_start + target_index - 1],
                source_visible[source_start + source_index - 1],
            )
            choices = (
                (
                    scores[target_index - 1][source_index - 1]
                    + similarity
                    - match_bias,
                    "match",
                ),
                (
                    scores[target_index - 1][source_index] + gap_penalty,
                    "target",
                ),
                (
                    scores[target_index][source_index - 1] + gap_penalty,
                    "source",
                ),
            )
            scores[target_index][source_index], backtrack[target_index][source_index] = max(
                choices
            )

    result: list[tuple[Optional[int], Optional[int]]] = []
    target_index = target_count
    source_index = source_count
    while target_index or source_index:
        direction = backtrack[target_index][source_index]
        if direction == "match":
            result.append(
                (
                    target_start + target_index - 1,
                    source_start + source_index - 1,
                )
            )
            target_index -= 1
            source_index -= 1
        elif direction == "target":
            result.append((target_start + target_index - 1, None))
            target_index -= 1
        elif direction == "source":
            result.append((None, source_start + source_index - 1))
            source_index -= 1
        else:
            raise EuMappingError("internal alignment traceback failure")
    result.reverse()
    return result


def _initial_alignment(
    target_payloads: tuple[bytes, ...],
    source_payloads: tuple[bytes, ...],
) -> tuple[dict[int, int], set[int]]:
    target_visible = tuple(_visible(payload) for payload in target_payloads)
    source_visible = tuple(_visible(payload) for payload in source_payloads)
    matcher = difflib.SequenceMatcher(
        None, target_payloads, source_payloads, autojunk=False
    )
    pairs: list[tuple[Optional[int], Optional[int]]] = []

    for tag, target_start, target_end, source_start, source_end in matcher.get_opcodes():
        if tag == "equal":
            pairs.extend(
                (target_start + offset, source_start + offset)
                for offset in range(target_end - target_start)
            )
        elif tag == "replace" and target_end - target_start == source_end - source_start:
            pairs.extend(
                (target_start + offset, source_start + offset)
                for offset in range(target_end - target_start)
            )
        else:
            pairs.extend(
                _align_gap(
                    target_visible,
                    source_visible,
                    target_start,
                    target_end,
                    source_start,
                    source_end,
                )
            )

    mapping = {
        target_id: source_id
        for target_id, source_id in pairs
        if target_id is not None and source_id is not None
    }
    unused_source_ids = set(range(len(source_payloads))) - set(mapping.values())

    # Recover exact providers missed by sequence alignment. Reuse is valid:
    # the same locale-neutral placeholder or fragment may back multiple FE8U
    # target IDs, and source identity remains explicit in every row.
    by_payload: dict[bytes, list[int]] = {}
    for source_id, payload in enumerate(source_payloads):
        by_payload.setdefault(payload, []).append(source_id)

    for target_id, payload in enumerate(target_payloads):
        source_id = mapping.get(target_id)
        current_similarity = (
            _similarity(target_visible[target_id], source_visible[source_id])
            if source_id is not None
            else 0.0
        )
        candidates = by_payload.get(payload, ())
        if candidates and (
            source_id is None
            or current_similarity < LOW_SIMILARITY_REVIEW_THRESHOLD
        ):
            replacement = min(candidates, key=lambda candidate: abs(candidate - target_id))
            mapping[target_id] = replacement
            unused_source_ids.discard(replacement)

    source_face_rows = [
        _face_operands(payload) for payload in source_payloads
    ]
    for target_id, payload in enumerate(target_payloads):
        target_faces = _face_operands(payload)
        if not target_faces:
            continue
        source_id = mapping.get(target_id)
        if source_id is not None and _is_subsequence(
            target_faces, source_face_rows[source_id]
        ):
            continue
        candidates = [
            candidate
            for candidate, source_faces in enumerate(source_face_rows)
            if _is_subsequence(target_faces, source_faces)
        ]
        if not candidates:
            continue
        replacement = max(
            candidates,
            key=lambda candidate: (
                _similarity(
                    target_visible[target_id],
                    source_visible[candidate],
                ),
                -abs(candidate - (source_id if source_id is not None else target_id)),
            ),
        )
        mapping[target_id] = replacement
        unused_source_ids.discard(replacement)

    return mapping, unused_source_ids


def _face_operands(payload: bytes) -> tuple[int, ...]:
    operands = []
    index = 0
    while index < len(payload):
        value = payload[index]
        if value == 0:
            break
        if value == 0x10 and index + 2 < len(payload):
            operands.append(payload[index + 1] | (payload[index + 2] << 8))
            index += 3
            continue
        if value == 0x80 and index + 1 < len(payload):
            index += 2
            continue
        index += 1
    return tuple(operands)


def _is_subsequence(needles: tuple[int, ...], values: tuple[int, ...]) -> bool:
    if not needles:
        return True
    index = 0
    for value in values:
        if value == needles[index]:
            index += 1
            if index == len(needles):
                return True
    return False


def _load_authored_catalog(locale: str) -> dict[str, str]:
    path = EU_ROOT / f"authored.{locale}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("kind") != "fe8eu-target-authored-catalog":
        raise EuMappingError(f"{path}: invalid kind")
    if data.get("locale") != locale:
        raise EuMappingError(f"{path}: locale must be {locale!r}")
    strings = data.get("strings")
    if not isinstance(strings, dict):
        raise EuMappingError(f"{path}: strings must be an object")
    expected = {f"0x{target_id:04X}" for target_id in AUTHORED_TRANSLATION_TARGETS}
    if set(strings) != expected:
        raise EuMappingError(
            f"{path}: authored target set mismatch; expected {sorted(expected)!r}"
        )
    for key, text in strings.items():
        if not isinstance(text, str) or not text:
            raise EuMappingError(f"{path}: {key} must be a non-empty string")
        encode_canonical_text(text)
    return strings


def _control_signature(payload: bytes) -> tuple[int, ...]:
    controls = []
    index = 0
    while index < len(payload):
        value = payload[index]
        if value == 0:
            break
        if value == 0x10 and index + 2 < len(payload):
            controls.extend((value, payload[index + 1], payload[index + 2]))
            index += 3
            continue
        if value == 0x80 and index + 1 < len(payload):
            controls.extend((value, payload[index + 1]))
            index += 2
            continue
        if value < 0x20:
            controls.append(value)
        index += 1
    return tuple(controls)


def build_mapping() -> dict[str, object]:
    target_count = len(load_fe8u_target_ids(TARGET_HEADER_PATH))
    english_entries = load_english_source_entries(
        ENGLISH_TEXTS_PATH,
        ENGLISH_DEFINITIONS_PATH,
        target_count=target_count,
    )
    target_payloads = tuple(entry.encoded_bytes for entry in english_entries)
    eu_english_text = _load_eu_source("en")
    source_payloads = tuple(encode_canonical_text(text) for text in eu_english_text)
    mapping, unused_source_ids = _initial_alignment(target_payloads, source_payloads)
    target_visible = tuple(_visible(payload) for payload in target_payloads)
    source_visible = tuple(_visible(payload) for payload in source_payloads)
    authored = {locale: _load_authored_catalog(locale) for locale in EU_LOCALES}

    for target_id in (
        AUTHORED_TRANSLATION_TARGETS
        | ENGLISH_PRESERVE_TARGETS
        | frozenset(CONCAT_TARGETS)
    ):
        mapping.pop(target_id, None)

    rows = []
    source_kind_counts = {
        "indexed": 0,
        "authored": 0,
        "english_preserve": 0,
    }
    low_similarity_rows = []
    for target_id, english_entry in enumerate(english_entries):
        if target_id in AUTHORED_TRANSLATION_TARGETS:
            source = {
                "kind": "authored",
                "key": f"0x{target_id:04X}",
            }
            source_kind_counts["authored"] += 1
        elif target_id in ENGLISH_PRESERVE_TARGETS:
            source = {
                "kind": "english_preserve",
                "reason": "locale-neutral control-only stream",
            }
            source_kind_counts["english_preserve"] += 1
        elif target_id in CONCAT_TARGETS:
            source = {
                "kind": "concat",
                "ids": [
                    f"0x{source_id:04X}"
                    for source_id in CONCAT_TARGETS[target_id]
                ],
            }
            source_kind_counts.setdefault("concat", 0)
            source_kind_counts["concat"] += 1
        else:
            if target_id not in mapping:
                raise EuMappingError(
                    f"target 0x{target_id:04X} has no EU source or reviewed authored provider"
                )
            source_id = mapping[target_id]
            similarity = _similarity(
                target_visible[target_id], source_visible[source_id]
            )
            exact = target_payloads[target_id] == source_payloads[source_id]
            source = {
                "kind": "indexed",
                "id": f"0x{source_id:04X}",
                "english_exact": exact,
                "english_similarity": round(similarity, 6),
            }
            if not exact and similarity < LOW_SIMILARITY_REVIEW_THRESHOLD:
                low_similarity_rows.append(
                    {
                        "target_id": f"0x{target_id:04X}",
                        "source_id": f"0x{source_id:04X}",
                        "target": target_visible[target_id],
                        "source": source_visible[source_id],
                        "similarity": round(similarity, 6),
                    }
                )
            source_kind_counts["indexed"] += 1

        rows.append(
            {
                "target_id": f"0x{target_id:04X}",
                "source": source,
                "english_sha256": sha256(english_entry.encoded_bytes),
            }
        )

    # Authored translations must preserve the exact FE control operand stream.
    for locale, strings in authored.items():
        for key, text in strings.items():
            target_id = int(key, 16)
            if _control_signature(encode_canonical_text(text)) != _control_signature(
                target_payloads[target_id]
            ):
                raise EuMappingError(
                    f"{locale} {key}: control signature differs from FE8U English"
                )

    manifest = json.loads(EU_MANIFEST_PATH.read_text(encoding="utf-8"))
    return {
        "format": 1,
        "kind": "fe8eu-to-fe8u-target-map",
        "authoritative": True,
        "source_rom_sha256": manifest["source"]["sha256"],
        "source_message_count": EU_SOURCE_COUNT,
        "target_count": target_count,
        "locale_ids": list(EU_LOCALES),
        "source_kind_counts": source_kind_counts,
        "unused_source_ids": [
            f"0x{source_id:04X}" for source_id in sorted(unused_source_ids)
        ],
        "low_similarity_review": low_similarity_rows,
        "rows": rows,
    }


def generate() -> None:
    content = canonical_json_bytes(build_mapping())
    MAPPING_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not MAPPING_PATH.exists() or MAPPING_PATH.read_bytes() != content:
        MAPPING_PATH.write_bytes(content)


def check() -> None:
    expected = canonical_json_bytes(build_mapping())
    actual = MAPPING_PATH.read_bytes()
    if actual != expected:
        raise EuMappingError(f"{MAPPING_PATH}: regenerate with generate")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("generate", "check"))
    args = parser.parse_args()
    if args.command == "generate":
        generate()
    else:
        check()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
