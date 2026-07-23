"""C89 generation for the 8 ``TerrainTable_*`` combat/heal stat arrays."""

from __future__ import annotations

from ..cgen import render_banner
from ..validators import extract_enum_constants
from .schema import EXPECTED_SYMBOLS, TERRAINS_HEADER

# Issue #5 Batch 1: TerrainTable_HealAmount/TerrainTable_HealsStatus's
# hand-written home in src/data_terrains.c is separated from the other 6
# arrays by 5 still-hand Unk_TerrainTable_N escape-hatch arrays (see
# schema.py's module docstring) -- not a suffix of the first 6. A single
# object's default `.data` section can only be spliced into ldscript.txt
# at one contiguous address, so these two symbols are placed in their own
# dedicated section here, letting ldscript.txt slot this same generated
# object in at two independent points (immediately after the first 6
# arrays' original address, and again immediately after the 5 Unk_*
# arrays' original address) with zero shift anywhere. See the guard/
# section comments in src/data_terrains.c and docs/generated_data.md's
# "terrainstats" section for the full rationale.
_HEAL_SECTION_SYMBOLS = frozenset({"TerrainTable_HealAmount", "TerrainTable_HealsStatus"})


def _storage_class(symbol):
    if symbol in _HEAL_SECTION_SYMBOLS:
        return 'SECTION(".data.terrainheal")'
    return "CONST_DATA"


def generate_c_source(records, source_path):
    terrain_enum = extract_enum_constants(TERRAINS_HEADER, name_prefix="TERRAIN_")
    terrain_order = [
        name for name, _ in sorted(
            ((n, v) for n, (v, _) in terrain_enum.items() if n != "TERRAIN_COUNT"),
            key=lambda item: item[1],
        )
    ]

    records_by_symbol = {r.symbol: r for r in records}

    parts = [render_banner(source=source_path, table="terrainstats")]
    parts.append('#include "global.h"\n')
    parts.append('#include "constants/terrains.h"\n\n')

    # Emit in the fixed original declaration order (see EXPECTED_SYMBOLS),
    # not JSON authoring order, matching src/data_terrains.c field-for-field.
    for symbol in EXPECTED_SYMBOLS:
        record = records_by_symbol.get(symbol)
        if record is None:
            # Missing records are already reported by schema.validate();
            # skip emission rather than crash so `generate` still surfaces
            # every diagnostic (cmd_generate refuses to write on error).
            continue
        parts.append("{} s8 {}[] = {{\n".format(_storage_class(symbol), symbol))
        for name, value in record.ordered_values(terrain_order):
            parts.append("    [{}] = {},\n".format(name, value))
        parts.append("};\n\n")

    return "".join(parts)
