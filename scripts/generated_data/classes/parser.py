"""Round-trip checker: parse the hand-written ``gClassData[]`` (index-
designated, ``[CLASS_X - 1] = { ... }``, ``struct ClassData[]``) array out
of ``src/data_classes.c`` and compare it, field for field, against the
generated model built from the JSON source.

Every field is resolved to the same symbol-blind canonical representation
:meth:`~.schema.ClassRecord.as_tuple` produces (bitmask/enum fields
reduced to their live integer value; the ``baseRanks`` sparse map reduced
to a fixed-capacity tuple keyed by each weapon type's own index) -- this
is a *semantic* round trip: JSON authoring order of ``attributes``
flags/``baseRanks`` entries, or which of two equal-valued symbolic
constants is used, never causes a false mismatch. Only the actual
encoded bytes matter. C-symbol-reference fields (``battleAnim``,
``movCostTable`` entries, the three terrain lookups,
``reservedTerrainTable``) are compared as bare token strings, since they
have no further numeric encoding to resolve.
"""

from __future__ import annotations

import re

from ..cparse import extract_designated_fields, find_matching_brace, line_of, strip_outer_braces
from ..diagnostics import GeneratedDataError, SourceLocation
from ..validators import extract_enum_constants
from .schema import (
    BMITEM_HEADER,
    BMUNIT_HEADER,
    CLASSES_HEADER,
    CLASS_NONE_DEFAULT,
    MOV_COST_NULL_LITERAL,
    read_class_attributes,
    read_class_data_array_capacity,
)

_DECL_RE = re.compile(r"CONST_DATA\s+struct\s+ClassData\s+gClassData\s*\[\s*\]\s*=\s*", re.MULTILINE)
_DESIGNATOR_RE = re.compile(r"^\[\s*([A-Za-z_][A-Za-z0-9_]*)\s*-\s*1\s*\]\s*=\s*(.*)$", re.S)
_ITYPE_DESIGNATOR_RE = re.compile(r"^\[\s*([A-Za-z_][A-Za-z0-9_]*)\s*\]\s*=\s*(.*)$", re.S)


def _split_entries_with_offsets(body, body_start):
    """Like :func:`~..cparse.split_top_level_entries`, but keeps each
    entry's absolute character offset in the original text (needed for
    accurate ``line:column`` diagnostics per ``[CLASS_X - 1] = { ... }``
    entry -- ``gClassData`` is one large array, so a single offset for the
    whole block would be far too coarse)."""
    entries = []
    depth = 0
    current_start = 0
    length = len(body)
    i = 0
    while i < length:
        ch = body[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
        elif ch == "," and depth == 0:
            entries.append((body[current_start:i], body_start + current_start))
            current_start = i + 1
        i += 1
    tail = body[current_start:]
    if tail.strip():
        entries.append((tail, body_start + current_start))
    return entries


def _parse_int_token(raw):
    raw = raw.strip()
    if raw.lower().startswith("0x") or raw.lower().startswith("-0x"):
        return int(raw, 16)
    return int(raw, 10)


def _resolve_ca_flags(raw, attribute_flags):
    value = 0
    for name in (t.strip() for t in raw.split("|")):
        if name not in attribute_flags:
            raise GeneratedDataError("unknown CA_* flag '{}' in hand-written attributes".format(name))
        value |= attribute_flags[name]
    return value


def _resolve_pointer(raw):
    raw = raw.strip()
    if raw.startswith("&"):
        return raw[1:].strip()
    return raw


def _parse_base_ranks(raw, weapon_types, wexp_thresholds, capacity):
    slots = [0] * capacity
    body = strip_outer_braces(raw.strip())
    for entry in _split_entries_with_offsets(body, 0):
        entry_text = entry[0].strip()
        if not entry_text:
            continue
        match = _ITYPE_DESIGNATOR_RE.match(entry_text)
        if not match:
            raise GeneratedDataError("malformed baseRanks entry '{}'".format(entry_text))
        itype_name = match.group(1)
        wexp_name = match.group(2).strip()
        if itype_name not in weapon_types:
            raise GeneratedDataError("unknown ITYPE_* '{}' in hand-written baseRanks".format(itype_name))
        if wexp_name not in wexp_thresholds:
            raise GeneratedDataError("unknown WPN_EXP_* '{}' in hand-written baseRanks".format(wexp_name))
        index = weapon_types[itype_name][0]
        if 0 <= index < capacity:
            slots[index] = wexp_thresholds[wexp_name][0]
    return tuple(slots)


def _parse_mov_cost_table(raw):
    body = strip_outer_braces(raw.strip())
    entries = [entry[0].strip() for entry in _split_entries_with_offsets(body, 0) if entry[0].strip()]
    return tuple(None if entry == MOV_COST_NULL_LITERAL else entry for entry in entries)


def parse_hand_written(path, class_names):
    """Parse only the ``class_names`` designated entries out of the
    ``gClassData[]`` array in ``path``.

    Returns ``{class_name: {"tuple": (...), "line": N}}`` -- ``tuple`` is
    the same canonical, symbol-blind representation
    :meth:`~.schema.ClassRecord.as_tuple` produces, so callers can compare
    the two directly with ``==``.
    """
    with open(path, "r", encoding="utf-8") as handle:
        text = handle.read()

    decl_match = _DECL_RE.search(text)
    if not decl_match:
        raise GeneratedDataError(
            "could not find 'CONST_DATA struct ClassData gClassData[] = { ... };' in {}".format(path)
        )
    outer_brace_start = text.index("{", decl_match.end())
    outer_brace_end = find_matching_brace(text, outer_brace_start)
    body_start = outer_brace_start + 1
    body = text[body_start:outer_brace_end]

    attribute_flags = read_class_attributes(BMUNIT_HEADER)
    classes_enum = extract_enum_constants(CLASSES_HEADER, name_prefix="CLASS_")
    weapon_types = extract_enum_constants(BMITEM_HEADER, name_prefix="ITYPE_")
    wexp_thresholds = extract_enum_constants(BMITEM_HEADER, name_prefix="WPN_EXP_")
    base_ranks_capacity = read_class_data_array_capacity("baseRanks", BMUNIT_HEADER)
    mov_cost_table_capacity = read_class_data_array_capacity("pMovCostTable", BMUNIT_HEADER)

    wanted = set(class_names)
    records = {}
    for raw_entry, entry_offset in _split_entries_with_offsets(body, body_start):
        stripped = raw_entry.strip()
        if not stripped:
            continue
        leading_ws = len(raw_entry) - len(raw_entry.lstrip())
        entry_abs_start = entry_offset + leading_ws
        line = line_of(text, entry_abs_start)

        designator_match = _DESIGNATOR_RE.match(stripped)
        if not designator_match:
            raise GeneratedDataError(
                "gClassData entry is not a '[CLASS_X - 1] = { ... }' designated initializer",
                SourceLocation(path, line, 1),
            )
        class_name = designator_match.group(1)
        if class_name not in wanted:
            continue

        struct_body = strip_outer_braces(designator_match.group(2).strip())
        fields = extract_designated_fields(struct_body)

        if "number" in fields and fields["number"].strip() != class_name:
            raise GeneratedDataError(
                "gClassData['{}'].number is '{}', expected the designator's own symbol".format(
                    class_name, fields["number"].strip()
                ),
                SourceLocation(path, line, 1),
            )
        for required_field in (
            "SMSId", "baseHP", "basePow", "baseSkl", "baseSpd", "baseDef",
            "baseRes", "baseCon", "baseMov", "maxHP", "maxPow", "maxSkl", "maxSpd", "maxDef",
            "maxRes", "maxCon", "growthHP", "growthPow", "growthSkl", "growthSpd", "growthDef",
            "growthRes", "growthLck", "promotionHp", "promotionPow", "promotionSkl", "promotionSpd",
            "promotionDef", "promotionRes", "pMovCostTable", "pTerrainAvoidLookup",
            "pTerrainDefenseLookup", "pTerrainResistanceLookup",
        ):
            if required_field not in fields:
                raise GeneratedDataError(
                    "gClassData['{}'] has no .{} (a required field)".format(class_name, required_field),
                    SourceLocation(path, line, 1),
                )

        promotion_token = fields["promotion"].strip() if "promotion" in fields else CLASS_NONE_DEFAULT
        slow_walking = 1 if ("slowWalking" in fields and "SLOW" in fields["slowWalking"]) else 0
        attributes_value = _resolve_ca_flags(fields["attributes"], attribute_flags) if "attributes" in fields else 0
        base_ranks_tuple = (
            _parse_base_ranks(fields["baseRanks"], weapon_types, wexp_thresholds, base_ranks_capacity)
            if "baseRanks" in fields else tuple([0] * base_ranks_capacity)
        )
        battle_anim = fields["pBattleAnimDef"].strip() if "pBattleAnimDef" in fields else None
        mov_cost_table = _parse_mov_cost_table(fields["pMovCostTable"])
        if len(mov_cost_table) != mov_cost_table_capacity:
            raise GeneratedDataError(
                "gClassData['{}'].pMovCostTable has {} entries, expected {}".format(
                    class_name, len(mov_cost_table), mov_cost_table_capacity
                ),
                SourceLocation(path, line, 1),
            )
        reserved_terrain_table = _resolve_pointer(fields["_pU50"]) if "_pU50" in fields else None

        record_tuple = (
            _parse_int_token(fields["nameTextId"]) if "nameTextId" in fields else 0,
            _parse_int_token(fields["descTextId"]) if "descTextId" in fields else 0,
            classes_enum[promotion_token][0],
            _parse_int_token(fields["SMSId"]),
            slow_walking,
            _parse_int_token(fields["defaultPortraitId"]) if "defaultPortraitId" in fields else 0,
            _parse_int_token(fields["sort_order"]) if "sort_order" in fields else 0,
            tuple(_parse_int_token(fields[f]) for f in
                  ("baseHP", "basePow", "baseSkl", "baseSpd", "baseDef", "baseRes", "baseCon", "baseMov")),
            tuple(_parse_int_token(fields[f]) for f in
                  ("maxHP", "maxPow", "maxSkl", "maxSpd", "maxDef", "maxRes", "maxCon")),
            _parse_int_token(fields["classRelativePower"]) if "classRelativePower" in fields else 0,
            tuple(_parse_int_token(fields[f]) for f in
                  ("growthHP", "growthPow", "growthSkl", "growthSpd", "growthDef", "growthRes", "growthLck")),
            tuple(_parse_int_token(fields[f]) for f in
                  ("promotionHp", "promotionPow", "promotionSkl", "promotionSpd", "promotionDef", "promotionRes")),
            attributes_value,
            base_ranks_tuple,
            battle_anim,
            mov_cost_table,
            fields["pTerrainAvoidLookup"].strip(),
            fields["pTerrainDefenseLookup"].strip(),
            fields["pTerrainResistanceLookup"].strip(),
            reserved_terrain_table,
        )
        records[class_name] = {"tuple": record_tuple, "line": line}

    missing = wanted - set(records)
    if missing:
        raise GeneratedDataError(
            "expected class(es) {} not found in gClassData[] in {}".format(sorted(missing), path),
            SourceLocation(path, 1, 1),
        )
    return records


def compare_records(generated_records, hand_records, hand_path):
    """Compare generated :class:`~.schema.ClassRecord` objects against
    parsed hand records. Returns a list of :class:`GeneratedDataError`."""
    classes_enum = extract_enum_constants(CLASSES_HEADER, name_prefix="CLASS_")
    attribute_flags = read_class_attributes(BMUNIT_HEADER)
    weapon_types = extract_enum_constants(BMITEM_HEADER, name_prefix="ITYPE_")
    wexp_thresholds = extract_enum_constants(BMITEM_HEADER, name_prefix="WPN_EXP_")
    base_ranks_capacity = read_class_data_array_capacity("baseRanks", BMUNIT_HEADER)

    errors = []
    gen_by_class = {r.class_name: r for r in generated_records}

    for class_name in sorted(set(gen_by_class) - set(hand_records)):
        record = gen_by_class[class_name]
        errors.append(
            GeneratedDataError(
                "generated class '{}' has no counterpart in {}".format(class_name, hand_path),
                record.loc,
            )
        )
    for class_name in sorted(set(hand_records) - set(gen_by_class)):
        hand = hand_records[class_name]
        errors.append(
            GeneratedDataError(
                "hand-written class '{}' has no counterpart in generated source".format(class_name),
                SourceLocation(hand_path, hand["line"], 1),
            )
        )
    for class_name in sorted(set(gen_by_class) & set(hand_records)):
        gen = gen_by_class[class_name]
        hand = hand_records[class_name]
        loc = SourceLocation(hand_path, hand["line"], 1)
        gen_tuple = gen.as_tuple(classes_enum, attribute_flags, weapon_types, wexp_thresholds, base_ranks_capacity)
        hand_tuple = hand["tuple"]
        if gen_tuple != hand_tuple:
            errors.append(
                GeneratedDataError(
                    "fields mismatch for {}: generated={!r} hand-written={!r}".format(
                        class_name, gen_tuple, hand_tuple
                    ),
                    loc, class_name,
                )
            )

    return errors
