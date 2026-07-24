"""Round-trip checker: parse the hand-written ``sWeaponTriangleRules[]``
array out of ``src/bmbattle.c`` and compare it, rule for rule (plus the
terminator shape), against the generated model built from the JSON
source.

Text-based (plain regex), scoped to only the
``sWeaponTriangleRules[]`` initializer -- it must never match
``BattleApplyWeaponTriangleEffect``/``BattleApplyReaverEffect`` (the
procedural functions that *consume* this table), which this parser never
even looks at.
"""

from __future__ import annotations

import re

from ..diagnostics import GeneratedDataError, SourceLocation

_TABLE_RE = re.compile(
    r"static\s+CONST_DATA\s+struct\s+WeaponTriangleRule\s+sWeaponTriangleRules\s*\[\s*\]\s*=\s*\{(?P<body>.*?)\}\s*;",
    re.S,
)
# Every top-level `{ ... }` entry within the table body (no nested braces
# in this struct, so a non-greedy single-level match is sufficient).
_ENTRY_RE = re.compile(r"\{([^{}]*)\}")


def parse_hand_written(path):
    """Parse ``sWeaponTriangleRules[]`` out of ``path``.

    Returns ``(records, errors)`` where ``records`` is a
    ``{(attacker, defender): {"hitBonus": int, "atkBonus": int, "line": int}}``
    dict (empty on any structural failure) and ``errors`` is a list of
    :class:`GeneratedDataError` -- non-empty only for structural problems
    (table not found, malformed/misplaced/duplicated/missing terminator),
    which the caller should treat as fatal (skip the semantic value
    comparison rather than reporting misleading mismatches).
    """
    with open(path, "r", encoding="utf-8") as handle:
        text = handle.read()

    match = _TABLE_RE.search(text)
    if match is None:
        return {}, [
            GeneratedDataError(
                "sWeaponTriangleRules[] not found in {} (hand block guarded/renamed/removed?)".format(path),
                SourceLocation(path, 1, 1),
            )
        ]

    body = match.group("body")
    body_start_line = text.count("\n", 0, match.start("body")) + 1

    entries = list(_ENTRY_RE.finditer(body))
    if not entries:
        return {}, [
            GeneratedDataError(
                "sWeaponTriangleRules[] body has no entries in {}".format(path),
                SourceLocation(path, body_start_line, 1),
            )
        ]

    errors = []
    records = {}
    terminator_seen = False
    for index, entry_match in enumerate(entries):
        line = body_start_line + body.count("\n", 0, entry_match.start())
        fields = [f.strip() for f in entry_match.group(1).split(",")]
        loc = SourceLocation(path, line, 1)

        if len(fields) == 1:
            # Only a bare `{ -1 }` (exactly this shape, exactly one
            # field, value -1) is a valid terminator, and only as the
            # table's final entry.
            if terminator_seen:
                errors.append(
                    GeneratedDataError(
                        "duplicate {{ -1 }} terminator entry in sWeaponTriangleRules[]", loc,
                    )
                )
                continue
            if fields[0] != "-1":
                errors.append(
                    GeneratedDataError(
                        "invalid terminator shape '{{ {} }}' -- expected exactly '{{ -1 }}'".format(fields[0]), loc,
                    )
                )
                continue
            if index != len(entries) - 1:
                errors.append(
                    GeneratedDataError(
                        "{ -1 } terminator is not the final entry in sWeaponTriangleRules[]", loc,
                    )
                )
                continue
            terminator_seen = True
            continue

        if terminator_seen:
            errors.append(
                GeneratedDataError(
                    "data entry found after the {{ -1 }} terminator in sWeaponTriangleRules[]", loc,
                )
            )
            continue

        if len(fields) != 4:
            errors.append(
                GeneratedDataError(
                    "expected exactly 4 fields per rule entry, found {} in '{{ {} }}'".format(
                        len(fields), entry_match.group(1).strip()
                    ),
                    loc,
                )
            )
            continue

        attacker, defender, hit_bonus, atk_bonus = fields
        try:
            hit_bonus_value = int(hit_bonus.lstrip("+"))
            atk_bonus_value = int(atk_bonus.lstrip("+"))
        except ValueError:
            errors.append(
                GeneratedDataError(
                    "non-integer bonus field(s) in rule entry '{{ {} }}'".format(entry_match.group(1).strip()), loc,
                )
            )
            continue

        key = (attacker, defender)
        if key in records:
            errors.append(
                GeneratedDataError(
                    "duplicate hand-written rule for ({}, {}) in {}".format(attacker, defender, path), loc,
                )
            )
            continue

        records[key] = {"hitBonus": hit_bonus_value, "atkBonus": atk_bonus_value, "line": line}

    if not terminator_seen:
        errors.append(
            GeneratedDataError(
                "sWeaponTriangleRules[] is missing its required {{ -1 }} terminator entry in {}".format(path),
                SourceLocation(path, body_start_line, 1),
            )
        )

    return records, errors


def compare_records(generated_records, hand_records, hand_path):
    """Compare generated :class:`~.schema.WeaponTriangleRule` objects
    against parsed hand records. Returns a list of
    :class:`GeneratedDataError` -- empty means every rule matches
    semantically, field for field."""
    errors = []
    gen_by_key = {record.key: record for record in generated_records}

    for key in sorted(set(gen_by_key) - set(hand_records)):
        record = gen_by_key[key]
        errors.append(
            GeneratedDataError(
                "generated weapontriangle rule '{}' has no counterpart in {}".format(key, hand_path),
                record.loc,
            )
        )
    for key in sorted(set(hand_records) - set(gen_by_key)):
        hand = hand_records[key]
        errors.append(
            GeneratedDataError(
                "hand-written weapontriangle rule '{}' has no counterpart in generated source".format(key),
                SourceLocation(hand_path, hand["line"], 1),
            )
        )

    for key in sorted(set(gen_by_key) & set(hand_records)):
        record = gen_by_key[key]
        hand = hand_records[key]
        loc = SourceLocation(hand_path, hand["line"], 1)
        if record.hit_bonus != hand["hitBonus"] or record.atk_bonus != hand["atkBonus"]:
            errors.append(
                GeneratedDataError(
                    "values mismatch for {}: generated=(hitBonus={}, atkBonus={}) "
                    "hand-written=(hitBonus={}, atkBonus={})".format(
                        key, record.hit_bonus, record.atk_bonus, hand["hitBonus"], hand["atkBonus"]
                    ),
                    loc,
                    "{}".format(key),
                )
            )

    return errors
