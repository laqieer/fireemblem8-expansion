"""Round-trip checker: parse hand-written ``TrapData_*`` (flat ``u8[]``)
arrays out of ``src/events_trapdata.c`` and compare them, entry for entry
and field for field, against the generated model built from the JSON
source.

Scoped to a caller-supplied set of trap array symbols (the ones present in
the JSON source) -- this table only covers ``TrapData_Event_Ch2`` and
``TrapData_Event_Ch2Hard`` per Issue #5 Batch A scope, not the other
chapters' trap arrays that happen to live in the same file.
"""

from __future__ import annotations

import re

from ..diagnostics import GeneratedDataError, SourceLocation
from .schema import TRAP_NONE

_RECORD_RE = re.compile(
    r"CONST_DATA\s+u8\s+(\w+)\s*\[\s*\]\s*=\s*\{(?P<body>.*?)\}\s*;",
    re.S,
)
_COMMENT_RE = re.compile(r"/\*.*?\*/", re.S)
_IDENTIFIER_RE = re.compile(r"^[A-Za-z_]\w*$")


def _parse_int_token(raw):
    raw = raw.strip()
    if raw.lower().startswith("0x") or raw.lower().startswith("-0x"):
        return int(raw, 16)
    return int(raw, 10)


def _resolve_subtype(token):
    if _IDENTIFIER_RE.match(token):
        return token
    return _parse_int_token(token)


def parse_hand_written(path, symbols):
    """Parse only the ``symbols`` ``TrapData_*`` (``u8[]``) arrays out of
    ``path``. Returns ``{symbol: {"symbol":..., "entries": [tuple, ...], "line": N}}``
    with the trailing bare ``TRAP_NONE`` terminator stripped."""
    with open(path, "r", encoding="utf-8") as handle:
        text = handle.read()

    wanted = set(symbols)
    records = {}
    for match in _RECORD_RE.finditer(text):
        symbol = match.group(1)
        if symbol not in wanted:
            continue
        line = text.count("\n", 0, match.start()) + 1
        body = _COMMENT_RE.sub("", match.group("body"))
        tokens = [t.strip() for t in body.split(",") if t.strip()]

        if not tokens or tokens[-1] != TRAP_NONE:
            raise GeneratedDataError(
                "trap array '{}' does not end with a bare {} terminator in {}".format(symbol, TRAP_NONE, path),
                SourceLocation(path, line, 1),
            )

        entry_tokens = tokens[:-1]
        if len(entry_tokens) % 6 != 0:
            raise GeneratedDataError(
                "trap array '{}' has {} pre-terminator token(s), not a multiple of 6 in {}".format(
                    symbol, len(entry_tokens), path
                ),
                SourceLocation(path, line, 1),
            )

        entries = []
        for i in range(0, len(entry_tokens), 6):
            trap_type, x_pos, y_pos, subtype, counter, turn = entry_tokens[i:i + 6]
            entries.append(
                (
                    trap_type.strip(),
                    _parse_int_token(x_pos),
                    _parse_int_token(y_pos),
                    _resolve_subtype(subtype.strip()),
                    _parse_int_token(counter),
                    _parse_int_token(turn),
                )
            )
        records[symbol] = {"symbol": symbol, "entries": entries, "line": line}

    missing = wanted - set(records)
    if missing:
        raise GeneratedDataError(
            "expected trap array symbol(s) {} not found in {}".format(sorted(missing), path),
            SourceLocation(path, 1, 1),
        )
    return records


def compare_records(generated_records, hand_records, hand_path):
    """Compare generated :class:`~.schema.TrapArray` objects against parsed
    hand records. Returns a list of :class:`GeneratedDataError`."""
    errors = []
    gen_by_symbol = {r.symbol: r for r in generated_records}

    for symbol in sorted(set(gen_by_symbol) - set(hand_records)):
        record = gen_by_symbol[symbol]
        errors.append(
            GeneratedDataError(
                "generated trap array '{}' has no counterpart in {}".format(symbol, hand_path),
                record.loc,
            )
        )
    for symbol in sorted(set(hand_records) - set(gen_by_symbol)):
        hand = hand_records[symbol]
        errors.append(
            GeneratedDataError(
                "hand-written trap array '{}' has no counterpart in generated source".format(symbol),
                SourceLocation(hand_path, hand["line"], 1),
            )
        )

    for symbol in sorted(set(gen_by_symbol) & set(hand_records)):
        gen = gen_by_symbol[symbol]
        hand = hand_records[symbol]
        loc = SourceLocation(hand_path, hand["line"], 1)
        gen_tuples = [e.as_tuple() for e in gen.entries]
        hand_tuples = hand["entries"]
        if gen_tuples != hand_tuples:
            errors.append(
                GeneratedDataError(
                    "entries mismatch for {}: generated={!r} hand-written={!r}".format(
                        symbol, gen_tuples, hand_tuples
                    ),
                    loc,
                    "{}.entries".format(symbol),
                )
            )

    return errors
