"""Round-trip checker: parse hand-written ``TerrainTable_*`` arrays out of
``src/data_terrains.c`` and compare them, entry for entry, against the
generated model built from the JSON source.

Scoped to a caller-supplied set of symbols (the 8 arrays in this table's
JSON source) -- ``src/data_terrains.c`` also defines ~50 other arrays
(movement-cost tables, the ``Unk_TerrainTable_N`` escape hatches, and the
``BanimTerrainGround_*``/``gBanimBGLut*`` graphics tables) that this
round-trip checker must ignore rather than misinterpret.
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
    """Parse only the ``symbols`` ``TerrainTable_*`` arrays out of
    ``path``. Returns ``{symbol: {"symbol":..., "values": {TERRAIN_*: int, ...}, "line": N}}``.

    Text-based (plain regex), so it is blind to the ``#if
    !GENERATED_DATA_TERRAINSTATS_LINKED`` preprocessor guards wrapped
    around these arrays once linked (see docs/generated_data.md's
    "terrainstats" section) -- it keeps reading the exact preserved
    source text either way.
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
            "expected terrain-stats array symbol(s) {} not found in {}".format(sorted(missing), path),
            SourceLocation(path, 1, 1),
        )
    return records


def compare_records(generated_records, hand_records, hand_path):
    """Compare generated :class:`~.schema.TerrainStatsRecord` objects
    against parsed hand records. Returns a list of
    :class:`GeneratedDataError` -- empty means every array matches
    semantically, terrain key for terrain key."""
    errors = []
    gen_by_symbol = {r.symbol: r for r in generated_records}

    for symbol in sorted(set(gen_by_symbol) - set(hand_records)):
        record = gen_by_symbol[symbol]
        errors.append(
            GeneratedDataError(
                "generated terrain-stats array '{}' has no counterpart in {}".format(symbol, hand_path),
                record.loc,
            )
        )
    for symbol in sorted(set(hand_records) - set(gen_by_symbol)):
        hand = hand_records[symbol]
        errors.append(
            GeneratedDataError(
                "hand-written terrain-stats array '{}' has no counterpart in generated source".format(symbol),
                SourceLocation(hand_path, hand["line"], 1),
            )
        )

    for symbol in sorted(set(gen_by_symbol) & set(hand_records)):
        gen = gen_by_symbol[symbol]
        hand = hand_records[symbol]
        loc = SourceLocation(hand_path, hand["line"], 1)
        if gen.values != hand["values"]:
            errors.append(
                GeneratedDataError(
                    "values mismatch for {}: generated={!r} hand-written={!r}".format(
                        symbol, gen.values, hand["values"]
                    ),
                    loc,
                    "{}.values".format(symbol),
                )
            )

    return errors
