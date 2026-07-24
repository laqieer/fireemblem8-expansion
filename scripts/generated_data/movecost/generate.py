"""C89 generation for the 47 ``TerrainTable_MovCost_*``/
``TerrainMoveCost_Ballista`` movement-cost arrays."""

from __future__ import annotations

from ..cgen import render_banner
from ..validators import extract_enum_constants
from .schema import NAMED_PROFILES, SINGLE_VARIANT_PROFILES, TERRAINS_HEADER

# Issue #5 Batch 2: the 15 named profiles' "snow" slot is separated from
# the rest of the movement-cost arrays by Unk_TerrainTable_1 (an
# unresearched escape-hatch array that sits between the Rain block and
# the Snow block in src/data_terrains.c, and must stay hand-linked) -- not
# a suffix of the "normal"+DemonKing+Ballista+"rain" block. A single
# object's default `.data` section can only be spliced into ldscript.txt
# at one contiguous address, so the 15 snow arrays are placed in their
# own dedicated section here, letting ldscript.txt slot this same
# generated object in at two independent points (immediately at the very
# start of src/data_terrains.o's original .data, and again immediately
# after Unk_TerrainTable_1's original address) with zero shift anywhere.
# See the guard/section comments in src/data_terrains.c and
# docs/generated_data.md's "movecost" section for the full rationale.
_SNOW_SECTION_SYMBOLS = frozenset(
    "TerrainTable_MovCost_{}Snow".format(profile) for profile in NAMED_PROFILES
)


def _storage_class(symbol):
    if symbol in _SNOW_SECTION_SYMBOLS:
        return 'SECTION(".data.movecostsnow")'
    return "CONST_DATA"


def _ordered_slot_emissions(records_by_profile):
    """Yield ``(symbol, slot)`` in the fixed original declaration order
    (see the module docstring in schema.py): all 15 named profiles'
    "normal" slot, then DemonKing's and Ballista's lone "normal" slot,
    then all 15 named profiles' "rain" slot, then all 15 named profiles'
    "snow" slot -- matching src/data_terrains.c field-for-field, not JSON
    authoring order."""
    for profile in NAMED_PROFILES:
        record = records_by_profile.get(profile)
        if record is None:
            continue
        slot = record.slots.get("normal")
        if slot is not None:
            yield slot.symbol, slot
    for profile in SINGLE_VARIANT_PROFILES:
        record = records_by_profile.get(profile)
        if record is None:
            continue
        slot = record.slots.get("normal")
        if slot is not None:
            yield slot.symbol, slot
    for profile in NAMED_PROFILES:
        record = records_by_profile.get(profile)
        if record is None:
            continue
        slot = record.slots.get("rain")
        if slot is not None:
            yield slot.symbol, slot
    for profile in NAMED_PROFILES:
        record = records_by_profile.get(profile)
        if record is None:
            continue
        slot = record.slots.get("snow")
        if slot is not None:
            yield slot.symbol, slot


def generate_c_source(records, source_path):
    terrain_enum = extract_enum_constants(TERRAINS_HEADER, name_prefix="TERRAIN_")
    terrain_order = [
        name for name, _ in sorted(
            ((n, v) for n, (v, _) in terrain_enum.items() if n != "TERRAIN_COUNT"),
            key=lambda item: item[1],
        )
    ]

    records_by_profile = {r.profile: r for r in records}

    parts = [render_banner(source=source_path, table="movecost")]
    parts.append('#include "global.h"\n')
    parts.append('#include "constants/terrains.h"\n\n')

    for symbol, slot in _ordered_slot_emissions(records_by_profile):
        parts.append("{} s8 {}[] = {{\n".format(_storage_class(symbol), symbol))
        for name, value in slot.ordered_values(terrain_order):
            parts.append("    [{}] = {},\n".format(name, value))
        parts.append("};\n\n")

    return "".join(parts)
