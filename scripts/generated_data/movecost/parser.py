"""Round-trip checker: parse hand-written ``TerrainTable_MovCost_*``/
``TerrainMoveCost_Ballista`` arrays out of ``src/data_terrains.c`` and
compare them, entry for entry, against the generated model built from
the JSON source.

Scoped to a caller-supplied set of symbols (the 47 arrays in this
table's JSON source) -- ``src/data_terrains.c`` also defines ~18 other
arrays (the ``Unk_TerrainTable_N`` escape hatches, the 8 ``terrainstats``
combat/heal arrays, and the ``BanimTerrainGround_*``/``gBanimBGLut*``
graphics tables) that this round-trip checker must ignore rather than
misinterpret.
"""

from __future__ import annotations

import re

from ..diagnostics import GeneratedDataError, SourceLocation

_RECORD_RE = re.compile(
    r"CONST_DATA\s+s8\s+(\w+)\s*\[\s*\]\s*=\s*\{(?P<body>.*?)\}\s*;",
    re.S,
)
_ENTRY_RE = re.compile(r"\[\s*(TERRAIN_\w+)\s*\]\s*=\s*(-?\d+)")


def parse_hand_written(path, symbols):
    """Parse only the ``symbols`` movement-cost arrays out of ``path``.
    Returns ``{symbol: {"symbol":..., "values": {TERRAIN_*: int, ...}, "line": N}}``.

    Text-based (plain regex), so it is blind to the ``#if
    !GENERATED_DATA_MOVECOST_LINKED`` preprocessor guards wrapped around
    these arrays once linked (see docs/generated_data.md's "movecost"
    section) -- it keeps reading the exact preserved source text either
    way.
    """
    with open(path, "r", encoding="utf-8") as handle:
        text = handle.read()

    wanted = set(symbols)
    records = {}
    for match in _RECORD_RE.finditer(text):
        symbol = match.group(1)
        if symbol not in wanted:
            continue
        line = text.count("\n", 0, match.start()) + 1
        values = {}
        for entry_match in _ENTRY_RE.finditer(match.group("body")):
            values[entry_match.group(1)] = int(entry_match.group(2))
        records[symbol] = {"symbol": symbol, "values": values, "line": line}

    missing = wanted - set(records)
    if missing:
        raise GeneratedDataError(
            "expected movecost array symbol(s) {} not found in {}".format(sorted(missing), path),
            SourceLocation(path, 1, 1),
        )
    return records


def compare_records(generated_records, hand_records, hand_path):
    """Compare generated :class:`~.schema.MovecostRecord` objects (via
    their authored slots) against parsed hand records. Returns a list of
    :class:`GeneratedDataError` -- empty means every array matches
    semantically, terrain key for terrain key."""
    errors = []
    gen_by_symbol = {}
    for record in generated_records:
        for slot_name, symbol in record.symbols():
            gen_by_symbol[symbol] = record.slots[slot_name]

    for symbol in sorted(set(gen_by_symbol) - set(hand_records)):
        slot = gen_by_symbol[symbol]
        errors.append(
            GeneratedDataError(
                "generated movecost array '{}' has no counterpart in {}".format(symbol, hand_path),
                slot.loc,
            )
        )
    for symbol in sorted(set(hand_records) - set(gen_by_symbol)):
        hand = hand_records[symbol]
        errors.append(
            GeneratedDataError(
                "hand-written movecost array '{}' has no counterpart in generated source".format(symbol),
                SourceLocation(hand_path, hand["line"], 1),
            )
        )

    for symbol in sorted(set(gen_by_symbol) & set(hand_records)):
        slot = gen_by_symbol[symbol]
        hand = hand_records[symbol]
        loc = SourceLocation(hand_path, hand["line"], 1)
        if slot.values != hand["values"]:
            errors.append(
                GeneratedDataError(
                    "values mismatch for {}: generated={!r} hand-written={!r}".format(
                        symbol, slot.values, hand["values"]
                    ),
                    loc,
                    "{}.values".format(symbol),
                )
            )

    return errors
