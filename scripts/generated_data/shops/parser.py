"""Round-trip checker: parse hand-written ``ShopList_*`` (``u16[]``)
arrays out of ``src/events_shoplist.c`` and compare them, item for item,
against the generated model built from the JSON source.

Scoped to a caller-supplied set of shop symbols (the ones present in the
JSON source) rather than every shop list in the file -- this table only
covers ``ShopList_Event_Ch2Armory`` per Issue #5 Batch A scope.
"""

from __future__ import annotations

import re

from ..diagnostics import GeneratedDataError, SourceLocation
from .schema import ITEM_NONE

_RECORD_RE = re.compile(
    r"CONST_DATA\s+u16\s+(\w+)\s*\[\s*\]\s*=\s*\{(?P<body>.*?)\}\s*;",
    re.S,
)


def _split_items(body):
    return [item.strip() for item in body.split(",") if item.strip()]


def parse_hand_written(path, symbols):
    """Parse only the ``symbols`` ``ShopList_*`` (``u16[]``) arrays out of
    ``path``. Returns ``{symbol: {"symbol":..., "items": [...], "line": N}}``
    with the trailing ``ITEM_NONE`` terminator stripped."""
    with open(path, "r", encoding="utf-8") as handle:
        text = handle.read()

    wanted = set(symbols)
    records = {}
    for match in _RECORD_RE.finditer(text):
        symbol = match.group(1)
        if symbol not in wanted:
            continue
        line = text.count("\n", 0, match.start()) + 1
        items = _split_items(match.group("body"))
        if not items or items[-1] != ITEM_NONE:
            raise GeneratedDataError(
                "shop list '{}' does not end with the {} terminator in {}".format(symbol, ITEM_NONE, path),
                SourceLocation(path, line, 1),
            )
        records[symbol] = {"symbol": symbol, "items": items[:-1], "line": line}

    missing = wanted - set(records)
    if missing:
        raise GeneratedDataError(
            "expected shop symbol(s) {} not found in {}".format(sorted(missing), path),
            SourceLocation(path, 1, 1),
        )
    return records


def compare_records(generated_records, hand_records, hand_path):
    """Compare generated :class:`~.schema.ShopRecord` objects against
    parsed hand records. Returns a list of :class:`GeneratedDataError`."""
    errors = []
    gen_by_symbol = {r.symbol: r for r in generated_records}

    for symbol in sorted(set(gen_by_symbol) - set(hand_records)):
        record = gen_by_symbol[symbol]
        errors.append(
            GeneratedDataError(
                "generated shop '{}' has no counterpart in {}".format(symbol, hand_path),
                record.loc,
            )
        )
    for symbol in sorted(set(hand_records) - set(gen_by_symbol)):
        hand = hand_records[symbol]
        errors.append(
            GeneratedDataError(
                "hand-written shop '{}' has no counterpart in generated source".format(symbol),
                SourceLocation(hand_path, hand["line"], 1),
            )
        )

    for symbol in sorted(set(gen_by_symbol) & set(hand_records)):
        gen = gen_by_symbol[symbol]
        hand = hand_records[symbol]
        loc = SourceLocation(hand_path, hand["line"], 1)
        if gen.items != hand["items"]:
            errors.append(
                GeneratedDataError(
                    "items mismatch for {}: generated={!r} hand-written={!r}".format(
                        symbol, gen.items, hand["items"]
                    ),
                    loc,
                    "{}.items".format(symbol),
                )
            )

    return errors
