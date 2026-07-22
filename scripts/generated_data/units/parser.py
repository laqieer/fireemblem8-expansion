"""Round-trip checker: parse the Chapter 2 ``UnitDefinition``/``REDA``
groups out of the hand-written ``src/events_udefs.c`` and compare them,
unit for unit and field for field, against the generated model built from
``src/data/ch2_units.json``.

Scoped deliberately to a caller-supplied set of group symbols (the ones
present in the JSON source) rather than scanning the whole
multi-chapter file -- this is a Chapter 2 vertical slice, not a full-game
migration; see ``docs/generated_data.md`` for what's explicitly out of
scope. Symbol references (``.redas = SOME_REDA_SYMBOL``) are resolved
against every ``REDA`` array found in the file, and ``ai[]`` initializers
that use the ``EA_Standard_Library/AI_Helpers.h`` macro shorthand (e.g.
``DefaultAI``, ``AttackInRangeAI``) are expanded via a table read live from
that header, so comparison happens on the same resolved byte values the
compiler would actually emit.
"""

from __future__ import annotations

import os
import re

from ..cparse import extract_designated_fields, find_c_array_blocks, line_of, split_top_level_entries, strip_outer_braces
from ..diagnostics import GeneratedDataError, SourceLocation
from ..validators import extract_enum_constants
from .. import character_refs
from .schema import CHARACTERS_HEADER, CLASSES_HEADER

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
AI_HELPERS_HEADER = os.path.join(REPO_ROOT, "include", "EA_Standard_Library", "AI_Helpers.h")

_IDENTIFIER_RE = re.compile(r"^[A-Za-z_]\w*$")
_DEFINE_RE = re.compile(r"^#define\s+([A-Za-z_]\w*)\s+([0-9xXA-Fa-f,\s]+?)\s*$", re.M)


def _parse_int_token(raw):
    raw = re.sub(r"//.*$", "", raw, flags=re.M).strip().rstrip(",").strip()
    if raw.lower().startswith("0x") or raw.lower().startswith("-0x"):
        return int(raw, 16)
    return int(raw, 10)


def _load_ai_macro_table(header=AI_HELPERS_HEADER):
    with open(header, "r", encoding="utf-8") as handle:
        text = handle.read()
    table = {}
    for match in _DEFINE_RE.finditer(text):
        name = match.group(1)
        raw_values = match.group(2)
        try:
            values = [_parse_int_token(v) for v in raw_values.split(",") if v.strip()]
        except ValueError:
            continue
        if values:
            table.setdefault(name, values)
    return table


def _resolve_ref_token(raw, table, path):
    raw = re.sub(r"//.*$", "", raw, flags=re.M).strip().rstrip(",").strip()
    if _IDENTIFIER_RE.match(raw):
        if raw not in table:
            raise GeneratedDataError("unknown symbol '{}' referenced in {}".format(raw, path))
        return table[raw][0]
    return _parse_int_token(raw)


def _expand_ai_tokens(tokens, macro_table, path):
    result = []
    for token in tokens:
        token = token.strip()
        if not token:
            continue
        if _IDENTIFIER_RE.match(token):
            if token not in macro_table:
                raise GeneratedDataError(
                    "unknown AI macro '{}' in ai[] initializer in {}".format(token, path)
                )
            result.extend(macro_table[token])
        else:
            result.append(_parse_int_token(token))
    return result


def _parse_reda_blocks(text):
    """Return ``{symbol: [(x, y, flags, a, b, delayFrames), ...]}`` for
    every ``struct REDA`` array found anywhere in ``text``."""
    table = {}
    for name, body, _idx in find_c_array_blocks(text, r"CONST_DATA\s+struct\s+REDA"):
        points = []
        for entry in split_top_level_entries(body):
            fields = extract_designated_fields(strip_outer_braces(entry))
            if not fields:
                continue
            points.append(
                (
                    _parse_int_token(fields.get("x", "0")),
                    _parse_int_token(fields.get("y", "0")),
                    _parse_int_token(fields.get("flags", "0")),
                    _parse_int_token(fields.get("a", "0")),
                    _parse_int_token(fields.get("b", "0")),
                    _parse_int_token(fields.get("delayFrames", "0")),
                )
            )
        table[name] = points
    return table


def _parse_unit_entry(entry_text, reda_table, path):
    body = strip_outer_braces(entry_text)
    if body.strip().rstrip(",").strip() == "0":
        return None  # `{ 0 },` terminator sentinel
    fields = extract_designated_fields(body)
    if not fields:
        return None

    characters = character_refs.read_character_designators(CHARACTERS_HEADER)
    classes = extract_enum_constants(CLASSES_HEADER, name_prefix="CLASS_")

    items = []
    if "items" in fields:
        items = [t.strip() for t in split_top_level_entries(strip_outer_braces(fields["items"])) if t.strip()]

    ai = [0, 0, 0, 0]
    if "ai" in fields:
        macro_table = _load_ai_macro_table()
        tokens = split_top_level_entries(strip_outer_braces(fields["ai"]))
        ai = _expand_ai_tokens(tokens, macro_table, path)

    redas = []
    reda_count = 0
    if "redas" in fields:
        reda_symbol = fields["redas"].strip().rstrip(",").strip()
        if reda_symbol not in reda_table:
            raise GeneratedDataError("REDA symbol '{}' referenced but not defined in {}".format(reda_symbol, path))
        redas = reda_table[reda_symbol]
        reda_count = _parse_int_token(fields.get("redaCount", str(len(redas))))

    return {
        "char_index": _resolve_ref_token(fields["charIndex"], characters, path),
        "class_index": _resolve_ref_token(fields["classIndex"], classes, path),
        "leader_char_index": _resolve_ref_token(fields.get("leaderCharIndex", "0"), characters, path),
        "allegiance": fields.get("allegiance", "FACTION_ID_BLUE").strip().rstrip(","),
        "level": _parse_int_token(fields.get("level", "0")),
        "x_position": _parse_int_token(fields.get("xPosition", "0")),
        "y_position": _parse_int_token(fields.get("yPosition", "0")),
        "autolevel": _parse_int_token(fields.get("autolevel", "0")),
        "gen_monster": _parse_int_token(fields.get("genMonster", "0")),
        "item_drop": _parse_int_token(fields.get("itemDrop", "0")),
        "sum_flag": _parse_int_token(fields.get("sumFlag", "0")),
        "extra_data": _parse_int_token(fields.get("extraData", "0")),
        "reda_count": reda_count,
        "redas": redas,
        "items": items,
        "ai": ai,
    }


def parse_hand_written(path, group_symbols):
    """Parse only the ``group_symbols`` ``UnitDefinition`` arrays out of
    ``path``. Returns ``{symbol: {"symbol":..., "units": [...], "line": N}}``.
    """
    with open(path, "r", encoding="utf-8") as handle:
        text = handle.read()

    reda_table = _parse_reda_blocks(text)

    wanted = set(group_symbols)
    groups = {}
    for name, body, idx in find_c_array_blocks(text, r"CONST_DATA\s+struct\s+UnitDefinition"):
        if name not in wanted:
            continue
        units = []
        for entry in split_top_level_entries(body):
            parsed = _parse_unit_entry(entry, reda_table, path)
            if parsed is not None:
                units.append(parsed)
        groups[name] = {"symbol": name, "units": units, "line": line_of(text, idx)}

    missing = wanted - set(groups)
    if missing:
        raise GeneratedDataError(
            "expected UnitDefinition group symbol(s) {} not found in {}".format(sorted(missing), path),
            SourceLocation(path, 1, 1),
        )
    return groups


def _resolve_generated_ref(value, table):
    if isinstance(value, str):
        return table[value][0]
    return value


def compare_groups(generated_groups, hand_groups, hand_path):
    """Compare generated :class:`~.schema.UnitGroup` objects against parsed
    hand-written groups (from :func:`parse_hand_written`).

    Returns a list of :class:`GeneratedDataError` -- empty means every
    Chapter 2 unit matches semantically.
    """
    characters = character_refs.read_character_designators(CHARACTERS_HEADER)
    classes = extract_enum_constants(CLASSES_HEADER, name_prefix="CLASS_")

    errors = []
    gen_by_symbol = {g.symbol: g for g in generated_groups}

    for symbol in sorted(set(gen_by_symbol) - set(hand_groups)):
        errors.append(
            GeneratedDataError(
                "generated group '{}' has no counterpart in {}".format(symbol, hand_path),
                gen_by_symbol[symbol].loc,
            )
        )
    for symbol in sorted(set(hand_groups) - set(gen_by_symbol)):
        hand = hand_groups[symbol]
        errors.append(
            GeneratedDataError(
                "hand-written group '{}' has no counterpart in generated source".format(symbol),
                SourceLocation(hand_path, hand["line"], 1),
            )
        )

    for symbol in sorted(set(gen_by_symbol) & set(hand_groups)):
        gen_group = gen_by_symbol[symbol]
        hand_group = hand_groups[symbol]
        loc = SourceLocation(hand_path, hand_group["line"], 1)

        if len(gen_group.units) != len(hand_group["units"]):
            errors.append(
                GeneratedDataError(
                    "unit count mismatch for {}: generated={} hand-written={}".format(
                        symbol, len(gen_group.units), len(hand_group["units"])
                    ),
                    loc,
                )
            )
            continue

        for index, (gen_unit, hand_unit) in enumerate(zip(gen_group.units, hand_group["units"])):
            ref = "{}[{}]".format(symbol, index)
            fields = (
                ("charIndex", _resolve_generated_ref(gen_unit.char_index, characters), hand_unit["char_index"]),
                ("classIndex", _resolve_generated_ref(gen_unit.class_index, classes), hand_unit["class_index"]),
                (
                    "leaderCharIndex",
                    _resolve_generated_ref(gen_unit.leader_char_index, characters),
                    hand_unit["leader_char_index"],
                ),
                ("allegiance", gen_unit.allegiance, hand_unit["allegiance"]),
                ("level", gen_unit.level, hand_unit["level"]),
                ("xPosition", gen_unit.x_position, hand_unit["x_position"]),
                ("yPosition", gen_unit.y_position, hand_unit["y_position"]),
                ("autolevel", gen_unit.autolevel, hand_unit["autolevel"]),
                ("genMonster", gen_unit.gen_monster, hand_unit["gen_monster"]),
                ("itemDrop", gen_unit.item_drop, hand_unit["item_drop"]),
                ("sumFlag", gen_unit.sum_flag, hand_unit["sum_flag"]),
                ("extraData", gen_unit.extra_data, hand_unit["extra_data"]),
                ("redaCount", gen_unit.reda_count, hand_unit["reda_count"]),
                ("redas", [r.as_tuple() for r in gen_unit.redas], hand_unit["redas"]),
                ("items", gen_unit.items, hand_unit["items"]),
                ("ai", gen_unit.ai, hand_unit["ai"]),
            )
            for field_name, gen_value, hand_value in fields:
                if gen_value != hand_value:
                    errors.append(
                        GeneratedDataError(
                            "{} mismatch for {}: generated={!r} hand-written={!r}".format(
                                field_name, ref, gen_value, hand_value
                            ),
                            loc,
                            "{}.{}".format(ref, field_name),
                        )
                    )

    return errors
