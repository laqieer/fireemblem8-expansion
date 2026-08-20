#!/usr/bin/env python3
"""Validate and generate the typed expansion BGM registry.

The committed default registry is empty and therefore inert. Projects may
author chapter/flag variants or unit/class/item/action selectors in the JSON
without changing the runtime router. Generated C is intentionally C89-safe.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SONGS_H = ROOT / "include/constants/songs.h"
ID_SPACE_H = ROOT / "include/id_space.h"
EVENTINFO_C = ROOT / "src/eventinfo.c"
SCHEMA = "fe8.bgm_registry.v1"
CONTEXTS = {
    "map_phase": 0,
    "battle": 1,
    "preparation": 2,
    "menu": 3,
    "world_map": 4,
    "event": 5,
    "support": 6,
    "shop": 7,
    "staff": 8,
    "dance": 9,
    "title": 10,
    "victory": 11,
    "game_over": 12,
}
ACTIONS = {"dance": 0, "staff": 1}
VARIANT_MATCH_CHAPTER = 1 << 0
VARIANT_MATCH_FLAG = 1 << 1
SELECTOR_MATCH_STAFF_KIND = 1 << 0
SELECTOR_MATCH_CHARACTER = 1 << 1
SELECTOR_MATCH_CLASS = 1 << 2
SELECTOR_MATCH_ITEM = 1 << 3
STAFF_KINDS = {"none": 0, "heal": 1, "cure": 2}


class RegistryError(ValueError):
    pass


def _songs() -> set[int]:
    text = SONGS_H.read_text(encoding="utf-8")
    return {int(value, 0) for value in re.findall(r"=\s*(0x[0-9A-Fa-f]+|\d+)", text)}


SONG_IDS = _songs()


def _header_macro(name: str) -> int:
    text = ID_SPACE_H.read_text(encoding="utf-8")
    match = re.search(
        r"^#define\s+{}\s+(0x[0-9A-Fa-f]+|\d+)\s*$".format(re.escape(name)),
        text,
        re.MULTILINE,
    )
    if not match:
        raise RegistryError(f"missing {name} in {ID_SPACE_H}")
    return int(match.group(1), 0)


def _runtime_flag_bits(function: str) -> int:
    text = EVENTINFO_C.read_text(encoding="utf-8")
    match = re.search(
        r"{}\s*\(void\)\s*\{{\s*return\s+(0x[0-9A-Fa-f]+|\d+)\s*;".format(
            re.escape(function)
        ),
        text,
        re.MULTILINE,
    )
    if not match:
        raise RegistryError(f"missing {function} in {EVENTINFO_C}")
    return int(match.group(1), 0) * 8


CHAPTER_ID_MAX = _header_macro("CHAPTER_ID_CONFIGURED_CAP")
CHARACTER_ID_MAX = _header_macro("CHARACTER_ID_CONFIGURED_CAP")
CLASS_ID_MAX = _header_macro("CLASS_ID_CONFIGURED_CAP")
CHAPTER_FLAG_MAX = _runtime_flag_bits("GetChapterFlagBitsSize")
PERMANENT_FLAG_MAX = 100 + _runtime_flag_bits("GetPermanentFlagBitsSize")


def _int(value, name: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool):
        raise RegistryError(f"{name} must be an integer")
    try:
        result = int(str(value), 0)
    except (TypeError, ValueError) as exc:
        raise RegistryError(f"{name} must be an integer") from exc
    if not minimum <= result <= maximum:
        raise RegistryError(f"{name}={result} outside [{minimum}, {maximum}]")
    return result


def _song(value, name: str) -> int:
    result = _int(value, name, 1, 0x7F)
    if result not in SONG_IDS:
        raise RegistryError(f"{name}={result} is not a declared music ID")
    return result


def _chapter(value, name: str) -> int:
    if value in (None, "*", "any"):
        return 0, 0
    return _int(value, name, 0, CHAPTER_ID_MAX), VARIANT_MATCH_CHAPTER


def _flag(value, name: str) -> int:
    if value in (None, "*", "any"):
        return 0, 0
    result = _int(value, name, 1, PERMANENT_FLAG_MAX)
    if not (result <= CHAPTER_FLAG_MAX or 101 <= result <= PERMANENT_FLAG_MAX):
        raise RegistryError(
            f"{name}={result} is outside CheckFlag's legal flag spaces "
            f"[1, {CHAPTER_FLAG_MAX}] or [101, {PERMANENT_FLAG_MAX}]"
        )
    return result, VARIANT_MATCH_FLAG


def _selector_id(value, name: str, maximum: int, match_bit: int) -> tuple[int, int]:
    if value in (None, "*", "any"):
        return 0, 0
    return _int(value, name, 0, maximum), match_bit


def validate(data: dict) -> dict:
    if data.get("$schema") != SCHEMA:
        raise RegistryError(f"expected $schema {SCHEMA!r}")
    variants = data.get("variants", [])
    selectors = data.get("selectors", [])
    if not isinstance(variants, list) or not isinstance(selectors, list):
        raise RegistryError("variants and selectors must be arrays")

    normalized_variants = []
    seen_variants = set()
    for index, row in enumerate(variants):
        if not isinstance(row, dict):
            raise RegistryError(f"variants[{index}] must be an object")
        context_name = row.get("context")
        if context_name not in CONTEXTS:
            raise RegistryError(f"variants[{index}].context is unknown")
        chapter, chapter_match = _chapter(
            row.get("chapter"), f"variants[{index}].chapter"
        )
        flag, flag_match = _flag(row.get("flag"), f"variants[{index}].flag")
        when_flag_set = row.get("whenFlagSet", True)
        if isinstance(when_flag_set, bool):
            when_flag_set = int(when_flag_set)
        when_set = _int(
            when_flag_set, f"variants[{index}].whenFlagSet", 0, 1
        )
        song = _song(row.get("song"), f"variants[{index}].song")
        priority = _int(row.get("priority", 0), f"variants[{index}].priority", 0, 255)
        key = (
            chapter,
            context_name,
            flag,
            when_set,
            priority,
            chapter_match | flag_match,
        )
        if key in seen_variants:
            raise RegistryError(f"duplicate variant key at index {index}")
        seen_variants.add(key)
        normalized_variants.append(
            (
                chapter,
                flag,
                song,
                CONTEXTS[context_name],
                when_set,
                priority,
                chapter_match | flag_match,
            )
        )

    normalized_selectors = []
    seen_selectors = set()
    for index, row in enumerate(selectors):
        if not isinstance(row, dict):
            raise RegistryError(f"selectors[{index}] must be an object")
        action_name = row.get("action")
        if action_name not in ACTIONS:
            raise RegistryError(f"selectors[{index}].action is unknown")
        staff_kind_name = row.get("staffKind")
        if staff_kind_name not in (*STAFF_KINDS, None, "*", "any"):
            raise RegistryError(f"selectors[{index}].staffKind is unknown")
        if action_name != "staff" and staff_kind_name not in (None, "*", "any"):
            raise RegistryError(
                f"selectors[{index}].staffKind is only valid for staff selectors"
            )
        staff_kind = STAFF_KINDS.get(staff_kind_name, 0)
        match_mask = (
            SELECTOR_MATCH_STAFF_KIND
            if staff_kind_name not in (None, "*", "any")
            else 0
        )
        character_id, character_match = _selector_id(
            row.get("character"),
            f"selectors[{index}].character",
            CHARACTER_ID_MAX,
            SELECTOR_MATCH_CHARACTER,
        )
        class_id, class_match = _selector_id(
            row.get("class"),
            f"selectors[{index}].class",
            CLASS_ID_MAX,
            SELECTOR_MATCH_CLASS,
        )
        item_id, item_match = _selector_id(
            row.get("item"),
            f"selectors[{index}].item",
            0xFF,
            SELECTOR_MATCH_ITEM,
        )
        match_mask |= character_match | class_match | item_match
        song = _song(row.get("song"), f"selectors[{index}].song")
        priority = _int(row.get("priority", 0), f"selectors[{index}].priority", 0, 255)
        key = (
            ACTIONS[action_name],
            staff_kind,
            character_id,
            class_id,
            item_id,
            match_mask,
            priority,
        )
        if key in seen_selectors:
            raise RegistryError(f"duplicate selector key at index {index}")
        seen_selectors.add(key)
        normalized_selectors.append(
            (
                ACTIONS[action_name],
                priority,
                staff_kind,
                character_id,
                class_id,
                match_mask,
                item_id,
                song,
            )
        )

    return {"variants": normalized_variants, "selectors": normalized_selectors}


def generate_c(normalized: dict) -> str:
    lines = [
        '#include "global.h"',
        '#include "expansion_bgm.h"',
        "",
        "const struct ExpansionBgmVariant gExpansionBgmVariants[] =",
        "{",
    ]
    if normalized["variants"]:
        for row in normalized["variants"]:
            lines.append("    { %d, %d, %d, %d, %d, %d, %d, { 0, 0 } }," % row)
    else:
        lines.append("    { 0, 0, 0, 0, 0, 0, 0, { 0, 0 } },")
    lines += [
        "};",
        "",
        f"const u32 gExpansionBgmVariantCount = {len(normalized['variants'])};",
        "",
        "const struct ExpansionBgmActionSelector gExpansionBgmActionSelectors[] =",
        "{",
    ]
    if normalized["selectors"]:
        for row in normalized["selectors"]:
            lines.append("    { %d, %d, %d, %d, %d, %d, %d, %d }," % row)
    else:
        lines.append("    { 0, 0, 0, 0, 0, 0, 0, 0 },")
    lines += [
        "};",
        "",
        f"const u32 gExpansionBgmActionSelectorCount = {len(normalized['selectors'])};",
        "",
    ]
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("validate", "generate", "check"))
    parser.add_argument("--input", type=Path, default=ROOT / "src/data/bgm_registry.json")
    parser.add_argument("--output", type=Path, default=ROOT / "src/expansion_bgm_data.c")
    args = parser.parse_args(argv)
    try:
        data = json.loads(args.input.read_text(encoding="utf-8"))
        generated = generate_c(validate(data))
    except (OSError, json.JSONDecodeError, RegistryError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if args.command == "validate":
        print("OK: BGM registry validated")
        return 0
    if args.command == "check":
        if not args.output.exists() or args.output.read_text(encoding="utf-8") != generated:
            print(f"error: {args.output} is stale; run bgm_registry.py generate", file=sys.stderr)
            return 1
        print("OK: BGM registry generated source is current")
        return 0
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if not args.output.exists() or args.output.read_text(encoding="utf-8") != generated:
        args.output.write_text(generated, encoding="utf-8")
    print(f"wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
