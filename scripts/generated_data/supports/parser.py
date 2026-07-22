"""Round-trip checker: parse hand-written ``src/data_supports.c`` and
compare it, record for record and field for field, against the generated
model built from the JSON source.

This is a small regex-based parser tailored specifically to the
designated-initializer style used in ``src/data_supports.c`` (see
``include/bmreliance.h`` for the authoritative struct layout). It is not a
general C parser -- it only needs to recognize this file's shape well
enough to prove the generated model is semantically identical to every
hand-written record.
"""

from __future__ import annotations

import re

from ..diagnostics import GeneratedDataError, SourceLocation

_RECORD_RE = re.compile(
    r"CONST_DATA\s+struct\s+SupportData\s+(SupportData_(\w+))\s*=\s*\{\s*"
    r"\.characters\s*=\s*\{(?P<chars>.*?)\}\s*,\s*"
    r"\.supportExpBase\s*=\s*\{(?P<base>.*?)\}\s*,\s*"
    r"\.supportExpGrowth\s*=\s*\{(?P<growth>.*?)\}\s*,\s*"
    r"\.supportCount\s*=\s*(?P<count>\d+)\s*,?\s*"
    r"\}\s*;",
    re.S,
)


def _split_csv_ints(blob):
    return [int(item.strip()) for item in blob.split(",") if item.strip()]


def _split_csv_symbols(blob):
    return [item.strip() for item in blob.split(",") if item.strip()]


def parse_hand_written(path):
    """Parse every ``SupportData_*`` record out of a hand-written C file.

    Returns a list of plain dicts (not :class:`SupportRecord`, to keep this
    module decoupled from the JSON-loader's location bookkeeping): each has
    keys ``owner``, ``symbol``, ``characters``, ``supportExpBase``,
    ``supportExpGrowth``, ``supportCount``, ``line``, ``path``.
    """
    with open(path, "r", encoding="utf-8") as handle:
        text = handle.read()

    records = []
    for match in _RECORD_RE.finditer(text):
        line = text.count("\n", 0, match.start()) + 1
        symbol = match.group(1)
        suffix = match.group(2)
        owner = "CHARACTER_" + suffix.upper()
        records.append(
            {
                "owner": owner,
                "symbol": symbol,
                "characters": _split_csv_symbols(match.group("chars")),
                "supportExpBase": _split_csv_ints(match.group("base")),
                "supportExpGrowth": _split_csv_ints(match.group("growth")),
                "supportCount": int(match.group("count")),
                "line": line,
                "path": path,
            }
        )

    if not records:
        raise GeneratedDataError(
            "no SupportData_* records recognized in {} -- round-trip parser "
            "may be out of sync with the hand-written file's format".format(path),
            SourceLocation(path, 1, 1),
        )
    return records


def compare_records(generated_records, hand_records, hand_path):
    """Compare generated :class:`SupportRecord` objects against parsed hand records.

    Returns a list of :class:`GeneratedDataError` -- empty means every
    vanilla record matches semantically (owner, characters, exp base/growth,
    and count), field for field.
    """
    errors = []
    gen_by_symbol = {record.symbol: record for record in generated_records}
    hand_by_symbol = {record["symbol"]: record for record in hand_records}

    for symbol in sorted(set(gen_by_symbol) - set(hand_by_symbol)):
        record = gen_by_symbol[symbol]
        errors.append(
            GeneratedDataError(
                "generated record '{}' has no counterpart in {}".format(symbol, hand_path),
                record.record_loc,
            )
        )

    for symbol in sorted(set(hand_by_symbol) - set(gen_by_symbol)):
        hand = hand_by_symbol[symbol]
        errors.append(
            GeneratedDataError(
                "hand-written record '{}' has no counterpart in generated source".format(symbol),
                SourceLocation(hand_path, hand["line"], 1),
            )
        )

    for symbol in sorted(set(gen_by_symbol) & set(hand_by_symbol)):
        gen = gen_by_symbol[symbol]
        hand = hand_by_symbol[symbol]
        loc = SourceLocation(hand_path, hand["line"], 1)
        fields = (
            ("owner", gen.owner, hand["owner"]),
            ("characters", gen.characters, hand["characters"]),
            ("supportExpBase", gen.support_exp_base, hand["supportExpBase"]),
            ("supportExpGrowth", gen.support_exp_growth, hand["supportExpGrowth"]),
            ("supportCount", gen.support_count, hand["supportCount"]),
        )
        for field_name, gen_value, hand_value in fields:
            if gen_value != hand_value:
                errors.append(
                    GeneratedDataError(
                        "{} mismatch for {}: generated={!r} hand-written={!r}".format(
                            field_name, symbol, gen_value, hand_value
                        ),
                        loc,
                        "{}.{}".format(symbol, field_name),
                    )
                )

    return errors
