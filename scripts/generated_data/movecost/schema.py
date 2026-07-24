"""``TerrainTable_MovCost_*``/``TerrainMoveCost_Ballista`` schema: the 47
vanilla terrain movement-cost arrays consumed by ``ClassData``'s
``pMovCostTable[3]`` (``normal``/``rain``/``snow`` slots, see
``classes/schema.py``).

Issue #5 Batch 2's other clean terrain mechanics domain (alongside the
already-linked Batch 1 ``terrainstats`` combat/heal stat arrays): 15
named unit-mobility profiles (``CommonT2``/``CommonT1``/``Armor``/
``Fighter``/``Berserker``/``Brigand``/``Pirate``/``Thief``/``Magic``/
``Civilian``/``HorseT1``/``HorseT2``/``AnimalT1``/``AnimalT2``/``Fly``),
each authored as a ``normal``/``rain``/``snow`` weather triplet (45
arrays total, confirmed field-for-field against ``src/data_terrains.c``),
plus two single-variant tables that have no weather-specific
variants at all: ``TerrainTable_MovCost_DemonKing`` (the final-boss
class, whose ``ClassData`` record repeats this one symbol in all three
``pMovCostTable`` slots -- see ``classes/schema.py``'s cross-table
validation) and ``TerrainMoveCost_Ballista`` (referenced only outside
``ClassData``, not by any of the 127 vanilla class records). 45 + 1 + 1
= 47 arrays, exactly the count named in Issue #5 Batch 2.

Deliberately scoped to *exactly* these 47 arrays -- it does **not** cover
``Unk_TerrainTable_1``/``Unk_TerrainTable_2`` (two unresearched
escape-hatch arrays that sit between the movement-cost groups in
``src/data_terrains.c``, see the module docstring in that file and
``ldscript.txt``) or the ``BanimTerrainGround_*``/``gBanimBGLut*``
graphics tables / the 6 ``TerrainTable_Avo_*``/``Def_*``/``Res_*`` +
2 heal arrays already owned by the ``terrainstats`` table (Batch 1).
Those remain out of scope and untouched by this schema/generator/link.

JSON source shape (see ``src/data/movecost.json``)::

    {
      "$schema": "fe8.movecost.v1",
      "profiles": [
        {
          "profile": "CommonT2",
          "normal": {
            "symbol": "TerrainTable_MovCost_CommonT2Normal",
            "values": { "TERRAIN_NONE": -1, ..., "TERRAIN_MAST": -1 }
          },
          "rain": { "symbol": "TerrainTable_MovCost_CommonT2Rain", "values": {...} },
          "snow": { "symbol": "TerrainTable_MovCost_CommonT2Snow", "values": {...} }
        },
        ...
        {
          "profile": "DemonKing",
          "normal": { "symbol": "TerrainTable_MovCost_DemonKing", "values": {...} },
          "rain": null,
          "snow": null
        },
        {
          "profile": "Ballista",
          "normal": { "symbol": "TerrainMoveCost_Ballista", "values": {...} },
          "rain": null,
          "snow": null
        }
      ]
    }

Each of the 3 weather slots (``normal``/``rain``/``snow``) is either
``null`` (no array authored for that slot -- valid *only* for the
``DemonKing``/``Ballista`` single-variant profiles, and then only the
``rain``/``snow`` slots; ``normal`` is always required) or an object
naming the flat ``CONST_DATA s8 SYMBOL[]`` array (indexed by the full
``TERRAIN_*`` enum, ``include/constants/terrains.h``, ``TERRAIN_NONE``
(0) through ``TERRAIN_MAST`` (0x40), ``TERRAIN_COUNT`` = 0x41 entries)
exactly like ``src/data_terrains.c`` already authors them.

``symbol`` is validated against a closed, self-describing expectation
derived from ``profile``/slot rather than an open ``CSymbolRefField``
header scan: named profiles must spell
``TerrainTable_MovCost_{profile}{Normal,Rain,Snow}``;
``DemonKing``'s lone slot must be ``TerrainTable_MovCost_DemonKing``
(no weather suffix); ``Ballista``'s lone slot must be
``TerrainMoveCost_Ballista`` (note the divergent ``TerrainMoveCost_``
prefix -- confirmed verbatim against ``src/data_terrains.c``, *not* a
typo to normalize away). This closed-list approach mirrors
``terrainstats/schema.py``'s ``EXPECTED_SYMBOLS``.

``values`` must cover *exactly* the ``TERRAIN_COUNT`` (0x41 = 65) real
terrain keys -- every ``TERRAIN_*`` enum entry except the ``TERRAIN_COUNT``
sentinel itself -- with no gaps and no unknown extra keys, and every
value must fit the field's real C type, ``s8`` (-128..127).
"""

from __future__ import annotations

import os

from ..diagnostics import GeneratedDataError
from ..json_loader import load_json_file
from ..schema import DependencyGraph, TableSchema
from ..validators import (
    extract_enum_constants,
    validate_range,
    validate_reference,
    validate_unique,
)

SCHEMA_NAME = "movecost"
SCHEMA_VERSION = 1
SCHEMA_ID = "fe8.movecost.v1"

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
TERRAINS_HEADER = os.path.join(REPO_ROOT, "include", "constants", "terrains.h")

_S8_MIN, _S8_MAX = -128, 127

_TERRAIN_COUNT_SENTINEL = "TERRAIN_COUNT"

# The 3 weather slots, in the fixed order ClassData::pMovCostTable[3]
# itself uses (see src/bmunit.c's GetUnitMovCostTable(): index 0 is the
# unconditional fallback/"normal" slot, 1 is rain, 2 is snow).
SLOT_NAMES = ("normal", "rain", "snow")
_WEATHER_SUFFIX = {"normal": "Normal", "rain": "Rain", "snow": "Snow"}

# The 15 named profiles, in the order they are declared in
# src/data_terrains.c's Normal block (the order the generator/round-trip
# parser also use) -- every one of these authors all 3 weather slots.
NAMED_PROFILES = (
    "CommonT2", "CommonT1", "Armor", "Fighter", "Berserker", "Brigand",
    "Pirate", "Thief", "Magic", "Civilian", "HorseT1", "HorseT2",
    "AnimalT1", "AnimalT2", "Fly",
)

# The 2 single-variant profiles: only the "normal" slot is authored (no
# weather-specific rain/snow variant exists at all), so "rain"/"snow"
# must be explicit JSON null. See the module docstring for how
# ClassData's DemonKing record reuses the lone symbol in all 3 slots.
SINGLE_VARIANT_PROFILES = ("DemonKing", "Ballista")

# The exact 17 profiles in scope, in src/data_terrains.c declaration
# order (Normal block: 15 named + DemonKing + Ballista) -- see the
# module docstring for why this list is closed rather than derived from
# a wildcard/prefix scan of the hand file.
EXPECTED_PROFILES = NAMED_PROFILES + SINGLE_VARIANT_PROFILES

# Symbols that diverge from the ordinary "TerrainTable_MovCost_{profile}
# {WeatherSuffix}" naming scheme -- confirmed verbatim against
# src/data_terrains.c, not typos to normalize away.
_SPECIAL_SYMBOLS = {
    ("DemonKing", "normal"): "TerrainTable_MovCost_DemonKing",
    ("Ballista", "normal"): "TerrainMoveCost_Ballista",
}


def expected_symbol(profile, slot):
    """Return the single expected symbol for ``(profile, slot)``, or
    ``None`` if that profile/slot combination should not be authored at
    all (the ``rain``/``snow`` slots of the 2 single-variant profiles)."""
    special = _SPECIAL_SYMBOLS.get((profile, slot))
    if special is not None:
        return special
    if profile in SINGLE_VARIANT_PROFILES:
        return None
    return "TerrainTable_MovCost_{}{}".format(profile, _WEATHER_SUFFIX[slot])


def read_terrain_constants(header=TERRAINS_HEADER):
    """Read the full ``TERRAIN_*`` enum (including the ``TERRAIN_COUNT``
    sentinel) live from ``include/constants/terrains.h``."""
    return extract_enum_constants(header, name_prefix="TERRAIN_")


def real_terrain_names(header=TERRAINS_HEADER):
    """Return the set of real terrain designator names -- every
    ``TERRAIN_*`` enum entry except the ``TERRAIN_COUNT`` sentinel."""
    constants = read_terrain_constants(header)
    return {name for name in constants if name != _TERRAIN_COUNT_SENTINEL}


class MovecostSlot:
    """One authored weather slot (``normal``/``rain``/``snow``) of a
    profile: a symbol plus its per-entry terrain values."""

    def __init__(self, symbol, symbol_loc, values, value_locs, loc):
        self.symbol = symbol
        self.symbol_loc = symbol_loc
        self.values = values  # OrderedDict: TERRAIN_* name -> int
        self.value_locs = value_locs
        self.loc = loc

    def ordered_values(self, terrain_order):
        for name in terrain_order:
            if name in self.values:
                yield name, self.values[name]


class MovecostRecord:
    """One profile (a row of ``src/data/movecost.json``'s ``profiles``
    list), with up to 3 :class:`MovecostSlot` children keyed by
    ``SLOT_NAMES``. A missing/null slot is ``None`` -- valid only for the
    ``rain``/``snow`` slots of a single-variant profile (see
    ``validate()``)."""

    def __init__(self, profile, profile_loc, slots, loc):
        self.profile = profile
        self.profile_loc = profile_loc
        self.slots = slots  # dict: slot_name -> MovecostSlot or None
        self.loc = loc

    def symbols(self):
        """Yield every non-null ``(slot_name, symbol)`` this profile
        authors, in ``SLOT_NAMES`` order."""
        for slot_name in SLOT_NAMES:
            slot = self.slots.get(slot_name)
            if slot is not None:
                yield slot_name, slot.symbol


def load_records(source_path):
    """Parse the JSON source into a list of :class:`MovecostRecord`.

    Raises :class:`GeneratedDataError` immediately for structural problems
    (wrong top-level shape, missing required fields, wrong types).
    Content-level problems (duplicates, bad references, out-of-range
    values, missing/extra terrain coverage) are left to :func:`validate`.
    """
    root = load_json_file(source_path)
    schema_node = root.require("$schema")
    if schema_node.as_str() != SCHEMA_ID:
        raise GeneratedDataError(
            "unexpected $schema '{}', expected '{}'".format(schema_node.as_str(), SCHEMA_ID),
            schema_node.loc,
        )
    profiles_node = root.require("profiles")
    records = []
    for profile_node in profiles_node.as_list():
        profile_name_node = profile_node.require("profile")
        slots = {}
        for slot_name in SLOT_NAMES:
            slot_node = profile_node.get(slot_name)
            if slot_node is None or (slot_node.is_scalar() and slot_node.value is None):
                slots[slot_name] = None
                continue
            symbol_node = slot_node.require("symbol")
            values_node = slot_node.require("values")
            values = {}
            value_locs = {}
            for key, value_node in values_node.items():
                values[key] = value_node.as_int()
                value_locs[key] = value_node.loc
            slots[slot_name] = MovecostSlot(
                symbol=symbol_node.as_str(), symbol_loc=symbol_node.loc,
                values=values, value_locs=value_locs, loc=slot_node.loc,
            )
        records.append(
            MovecostRecord(
                profile=profile_name_node.as_str(), profile_loc=profile_name_node.loc,
                slots=slots, loc=profile_node.loc,
            )
        )
    return records


def build_slot_symbol_index(records):
    """Build a ``{slot_name: {symbol: profile_name}}`` lookup used by
    ``classes/schema.py``'s cross-table ``movCostTable`` validation.

    Single-variant profiles (``DemonKing``/``Ballista``) only author a
    ``normal`` slot, but their ``ClassData`` records repeat that one
    symbol in the ``rain``/``snow`` slots too (see the module docstring)
    -- so this index also registers the ``normal`` symbol under the
    ``rain``/``snow`` slot keys for any profile whose ``rain``/``snow``
    slot is ``None``, letting the same symbol resolve consistently no
    matter which ``pMovCostTable`` index it appears at.
    """
    index = {slot_name: {} for slot_name in SLOT_NAMES}
    for record in records:
        normal_slot = record.slots.get("normal")
        for slot_name in SLOT_NAMES:
            slot = record.slots.get(slot_name)
            if slot is not None:
                index[slot_name][slot.symbol] = record.profile
            elif normal_slot is not None:
                # Weather-invariant fallback (DemonKing/Ballista): the
                # lone normal symbol also resolves in the rain/snow slots.
                index[slot_name].setdefault(normal_slot.symbol, record.profile)
    return index


def validate(records, diagnostics, terrains_header=TERRAINS_HEADER):
    """Run all movecost validations, appending errors to ``diagnostics``.

    Checks: unique profile names, exactly the 17 expected profiles (no
    missing, no unrecognized extra entries), each authored slot's
    ``symbol`` matches the closed expected name for its ``(profile,
    slot)``, named profiles author all 3 slots (none null) while
    single-variant profiles author only ``normal`` (``rain``/``snow``
    must be null), full ``TERRAIN_COUNT`` coverage per authored slot (no
    gaps, no unknown extra terrain keys), every value is a valid ``s8``
    (-128..127), and every authored symbol is globally unique.
    """
    real_terrains = real_terrain_names(terrains_header)
    expected_set = set(EXPECTED_PROFILES)

    diagnostics.extend(
        validate_unique(
            ((r.profile, r.profile_loc) for r in records),
            "duplicate movecost profile '{key}' (first defined at {first_loc})",
            "profiles[profile={key}]",
        )
    )

    all_symbols = []  # (symbol, loc) across every authored slot, for global-uniqueness check
    seen_profiles = set()
    for record in records:
        ref = "profiles[profile={}]".format(record.profile)
        seen_profiles.add(record.profile)

        if record.profile not in expected_set:
            diagnostics.add(
                GeneratedDataError(
                    "unexpected movecost profile '{}' -- not one of the 17 profiles in scope "
                    "(expected one of {})".format(record.profile, sorted(expected_set)),
                    record.profile_loc, ref,
                )
            )
            continue

        is_single_variant = record.profile in SINGLE_VARIANT_PROFILES
        for slot_name in SLOT_NAMES:
            slot = record.slots.get(slot_name)
            slot_ref = "{}.{}".format(ref, slot_name)
            must_be_present = not (is_single_variant and slot_name != "normal")

            if slot is None:
                if must_be_present:
                    diagnostics.add(
                        GeneratedDataError(
                            "missing required '{}' slot for movecost profile '{}'".format(
                                slot_name, record.profile
                            ),
                            record.loc, slot_ref,
                        )
                    )
                continue

            if not must_be_present:
                diagnostics.add(
                    GeneratedDataError(
                        "movecost profile '{}' is a single-variant profile -- '{}' slot must be "
                        "null (no weather-specific variant exists)".format(record.profile, slot_name),
                        slot.loc, slot_ref,
                    )
                )
                continue

            expected_sym = expected_symbol(record.profile, slot_name)
            if slot.symbol != expected_sym:
                diagnostics.add(
                    GeneratedDataError(
                        "symbol '{}' does not match expected '{}' for profile '{}' slot '{}'".format(
                            slot.symbol, expected_sym, record.profile, slot_name
                        ),
                        slot.symbol_loc, "{}.symbol".format(slot_ref),
                    )
                )

            all_symbols.append((slot.symbol, slot.symbol_loc))

            seen_terrains = set(slot.values)
            missing_terrains = sorted(real_terrains - seen_terrains)
            extra_terrains = sorted(seen_terrains - real_terrains)
            for name in missing_terrains:
                diagnostics.add(
                    GeneratedDataError(
                        "missing terrain coverage for '{}' in '{}'".format(name, slot.symbol),
                        slot.loc, "{}.values[{}]".format(slot_ref, name),
                    )
                )
            for name in extra_terrains:
                diagnostics.extend(
                    validate_reference(
                        name, real_terrains, slot.value_locs.get(name, slot.loc),
                        "{}.values[{}]".format(slot_ref, name), kind="terrain",
                    )
                )
            for name, value in slot.values.items():
                if name not in real_terrains:
                    continue
                diagnostics.extend(
                    validate_range(
                        value, _S8_MIN, _S8_MAX, slot.value_locs.get(name, slot.loc),
                        "{}.values[{}]".format(slot_ref, name), field_name=name,
                    )
                )

    missing_profiles = sorted(expected_set - seen_profiles)
    for profile in missing_profiles:
        diagnostics.add(
            GeneratedDataError(
                "missing movecost profile '{}' -- all 17 profiles in scope must be authored".format(profile),
                getattr(records, "loc", None), "profiles[profile={}]".format(profile),
            )
        )

    diagnostics.extend(
        validate_unique(
            all_symbols,
            "duplicate movecost array symbol '{key}' (first defined at {first_loc})",
            "profiles[symbol={key}]",
        )
    )


class MovecostTableSchema(TableSchema):
    name = SCHEMA_NAME
    version = SCHEMA_VERSION

    default_source = "src/data/movecost.json"
    default_hand_source = "src/data_terrains.c"
    default_output_name = "data_movecost.c"
    default_inventory_path = "reports/generated_data_movecost_inventory.md"

    def dependencies(self):
        return ("constants.terrains.TERRAIN",)

    def load_records(self, source_path):
        return load_records(source_path)

    def validate(self, records, diagnostics):
        validate(records, diagnostics)

    def generate_c(self, records, source_path):
        from . import generate as movecost_generate
        return movecost_generate.generate_c_source(records, source_path)

    def build_inventory(self, records):
        from . import inventory as movecost_inventory
        return movecost_inventory.build_inventory(records)

    def round_trip_errors(self, records, hand_source):
        if not hand_source or not os.path.exists(hand_source):
            return []
        from . import parser as movecost_roundtrip
        symbols = [symbol for r in records for _, symbol in r.symbols()]
        hand_records = movecost_roundtrip.parse_hand_written(hand_source, symbols)
        return movecost_roundtrip.compare_records(records, hand_records, hand_path=hand_source)


def dependency_graph():
    graph = DependencyGraph()
    for dep in MovecostTableSchema().dependencies():
        graph.add_dependency(SCHEMA_NAME, dep)
    return graph
