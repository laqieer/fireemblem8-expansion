"""Round-trip checker: parse the hand-written ``gItemData[]`` (index-
designated ``struct ItemData[]``) array out of ``src/data_items.c`` and
compare it, field for field, against the generated model built from the
JSON source.

Every field is resolved to the same symbol-blind canonical representation
:meth:`~.schema.ItemRecord.as_tuple` produces (bitmask/enum fields reduced
to their live integer value) -- this is a *semantic* round trip: JSON
authoring order of ``attributes`` flags, or which of two equal-valued
symbolic constants is used, never causes a false mismatch. Only the actual
encoded bytes matter.
"""

from __future__ import annotations

import re

from ..cparse import extract_designated_fields, line_of, strip_outer_braces
from ..diagnostics import GeneratedDataError, SourceLocation
from ..validators import extract_enum_constants
from .schema import BMITEM_HEADER, read_item_attributes

_DECL_RE = re.compile(r"CONST_DATA\s+struct\s+ItemData\s+gItemData\s*\[\s*\]\s*=\s*", re.MULTILINE)
_DESIGNATOR_RE = re.compile(r"^\[\s*([A-Za-z_][A-Za-z0-9_]*)\s*\]\s*=\s*(.*)$", re.S)


def _find_matching_brace(text, open_index):
    assert text[open_index] == "{"
    depth = 0
    for i in range(open_index, len(text)):
        ch = text[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return i
    raise GeneratedDataError("unbalanced braces starting at index {}".format(open_index))


def _split_entries_with_offsets(body, body_start):
    """Like :func:`~..cparse.split_top_level_entries`, but keeps each
    entry's absolute character offset in the original text (needed for
    accurate ``line:column`` diagnostics per ``[ITEM_X] = { ... }``
    entry -- ``gItemData`` is one large array, so a single offset for the
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


def _resolve_attributes(raw, attribute_flags):
    value = 0
    for name in (t.strip() for t in raw.split("|")):
        if name not in attribute_flags:
            raise GeneratedDataError("unknown IA_* flag '{}' in hand-written attributes".format(name))
        value |= attribute_flags[name]
    return value


def _resolve_pointer(raw):
    raw = raw.strip()
    if raw.startswith("&"):
        return raw[1:].strip()
    return raw


def parse_hand_written(path, item_names):
    """Parse only the ``item_names`` designated entries out of the
    ``gItemData[]`` array in ``path``.

    Returns ``{item_name: {"tuple": (...), "line": N}}`` -- ``tuple`` is
    the same canonical, symbol-blind representation
    :meth:`~.schema.ItemRecord.as_tuple` produces, so callers can compare
    the two directly with ``==``.
    """
    with open(path, "r", encoding="utf-8") as handle:
        text = handle.read()

    decl_match = _DECL_RE.search(text)
    if not decl_match:
        raise GeneratedDataError(
            "could not find 'CONST_DATA struct ItemData gItemData[] = { ... };' in {}".format(path)
        )
    outer_brace_start = text.index("{", decl_match.end())
    outer_brace_end = _find_matching_brace(text, outer_brace_start)
    body_start = outer_brace_start + 1
    body = text[body_start:outer_brace_end]

    attribute_flags = read_item_attributes(BMITEM_HEADER)
    weapon_types = extract_enum_constants(BMITEM_HEADER, name_prefix="ITYPE_")
    wexp_thresholds = extract_enum_constants(BMITEM_HEADER, name_prefix="WPN_EXP_")
    weapon_effects = extract_enum_constants(BMITEM_HEADER, name_prefix="WPN_EFFECT_")

    wanted = set(item_names)
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
                "gItemData entry is not a '[ITEM_X] = { ... }' designated initializer",
                SourceLocation(path, line, 1),
            )
        item_name = designator_match.group(1)
        if item_name not in wanted:
            continue

        struct_body = strip_outer_braces(designator_match.group(2).strip())
        fields = extract_designated_fields(struct_body)

        if "number" in fields and fields["number"].strip() != item_name:
            raise GeneratedDataError(
                "gItemData['{}'].number is '{}', expected the designator's own symbol".format(
                    item_name, fields["number"].strip()
                ),
                SourceLocation(path, line, 1),
            )
        if "weaponType" not in fields:
            raise GeneratedDataError(
                "gItemData['{}'] has no .weaponType (a required field)".format(item_name),
                SourceLocation(path, line, 1),
            )

        weapon_type_token = fields["weaponType"].strip()
        attributes_value = _resolve_attributes(fields["attributes"], attribute_flags) if "attributes" in fields else 0
        stat_bonuses = _resolve_pointer(fields["pStatBonuses"]) if "pStatBonuses" in fields else None
        effectiveness = fields["pEffectiveness"].strip() if "pEffectiveness" in fields else None
        required_wexp_token = fields["weaponRank"].strip() if "weaponRank" in fields else "WPN_EXP_0"
        weapon_effect_token = fields["weaponEffectId"].strip() if "weaponEffectId" in fields else "WPN_EFFECT_NONE"

        record_tuple = (
            _parse_int_token(fields["nameTextId"]) if "nameTextId" in fields else 0,
            _parse_int_token(fields["descTextId"]) if "descTextId" in fields else 0,
            _parse_int_token(fields["useDescTextId"]) if "useDescTextId" in fields else 0,
            weapon_types[weapon_type_token][0],
            attributes_value,
            stat_bonuses,
            effectiveness,
            _parse_int_token(fields["maxUses"]) if "maxUses" in fields else 0,
            _parse_int_token(fields["might"]) if "might" in fields else 0,
            _parse_int_token(fields["hit"]) if "hit" in fields else 0,
            _parse_int_token(fields["weight"]) if "weight" in fields else 0,
            _parse_int_token(fields["crit"]) if "crit" in fields else 0,
            _parse_int_token(fields["encodedRange"]) if "encodedRange" in fields else 0,
            _parse_int_token(fields["costPerUse"]) if "costPerUse" in fields else 0,
            wexp_thresholds[required_wexp_token][0],
            _parse_int_token(fields["iconId"]) if "iconId" in fields else 0,
            _parse_int_token(fields["useEffectId"]) if "useEffectId" in fields else 0,
            weapon_effects[weapon_effect_token][0],
            _parse_int_token(fields["weaponExp"]) if "weaponExp" in fields else 0,
        )
        records[item_name] = {"tuple": record_tuple, "line": line}

    missing = wanted - set(records)
    if missing:
        raise GeneratedDataError(
            "expected item(s) {} not found in gItemData[] in {}".format(sorted(missing), path),
            SourceLocation(path, 1, 1),
        )
    return records


def compare_records(generated_records, hand_records, hand_path):
    """Compare generated :class:`~.schema.ItemRecord` objects against
    parsed hand records. Returns a list of :class:`GeneratedDataError`."""
    attribute_flags = read_item_attributes(BMITEM_HEADER)
    weapon_types = extract_enum_constants(BMITEM_HEADER, name_prefix="ITYPE_")
    wexp_thresholds = extract_enum_constants(BMITEM_HEADER, name_prefix="WPN_EXP_")
    weapon_effects = extract_enum_constants(BMITEM_HEADER, name_prefix="WPN_EFFECT_")
    errors = []
    gen_by_item = {r.item: r for r in generated_records}

    for item in sorted(set(gen_by_item) - set(hand_records)):
        record = gen_by_item[item]
        errors.append(
            GeneratedDataError(
                "generated item '{}' has no counterpart in {}".format(item, hand_path),
                record.loc,
            )
        )
    for item in sorted(set(hand_records) - set(gen_by_item)):
        hand = hand_records[item]
        errors.append(
            GeneratedDataError(
                "hand-written item '{}' has no counterpart in generated source".format(item),
                SourceLocation(hand_path, hand["line"], 1),
            )
        )
    for item in sorted(set(gen_by_item) & set(hand_records)):
        gen = gen_by_item[item]
        hand = hand_records[item]
        loc = SourceLocation(hand_path, hand["line"], 1)
        gen_tuple = gen.as_tuple(weapon_types, attribute_flags, wexp_thresholds, weapon_effects)
        hand_tuple = hand["tuple"]
        if gen_tuple != hand_tuple:
            errors.append(
                GeneratedDataError(
                    "fields mismatch for {}: generated={!r} hand-written={!r}".format(item, gen_tuple, hand_tuple),
                    loc,
                    item,
                )
            )

    return errors
