"""``TerrainTable_*`` schema: the 8 vanilla terrain combat/heal stat
arrays consumed by ``ClassData``'s ``pTerrainAvoidLookup``/
``pTerrainDefenseLookup``/``pTerrainResistanceLookup`` pointers (see
``classes/schema.py``) and by ``src/bmmap.c``'s heal-tile lookups
(``TerrainTable_HealAmount``/``TerrainTable_HealsStatus``).

Each array is a flat ``CONST_DATA s8 SYMBOL[]`` indexed by the full
``TERRAIN_*`` enum (``include/constants/terrains.h``, ``TERRAIN_NONE``
(0) through ``TERRAIN_MAST`` (0x40), ``TERRAIN_COUNT`` = 0x41 entries)
exactly like ``src/data_terrains.c`` already authors them (designated
initializers keyed by symbolic terrain name, see that file's own
``TerrainTable_Avo_Common``/etc. definitions).

This is deliberately scoped to *exactly* the 8 combat/heal stat arrays
named in Issue #5 Batch 1 -- it does **not** cover the ~50 other arrays
``src/data_terrains.c`` also defines (movement-cost tables, the 5
unresearched ``Unk_TerrainTable_N`` escape hatches ``ClassData``'s
``_pU50`` field optionally references, or the ``BanimTerrainGround_*``/
``gBanimBGLut*`` graphics tables) -- those remain out of scope and
untouched by this schema/generator/link.

JSON source shape (see ``src/data/terrainstats.json``)::

    {
      "$schema": "fe8.terrainstats.v1",
      "tables": [
        {
          "symbol": "TerrainTable_Avo_Common",
          "field": "terrainAvoid",
          "mobility": "common",
          "values": { "TERRAIN_NONE": 0, "TERRAIN_PLAINS": 0, ..., "TERRAIN_MAST": 20 }
        },
        ...
      ]
    }

``field`` identifies which ``ClassData`` role (if any) the array fills --
one of ``terrainAvoid``/``terrainDefense``/``terrainResistance`` (the 6
``_Common``/``_Fly`` arrays, paired with ``mobility``) or
``healAmount``/``healsStatus`` (``TerrainTable_HealAmount``/
``TerrainTable_HealsStatus``, which have no ``mobility``). This is purely
descriptive/self-documenting metadata (used by ``inventory.py`` and by
``classes/schema.py``'s cross-table validation, see
``symbols_for_role()`` below) -- the generator does not need it to emit
correct C, since the record already carries its own ``symbol``.

``values`` must cover *exactly* the ``TERRAIN_COUNT`` (0x41 = 65) real
terrain keys -- every ``TERRAIN_*`` enum entry except the ``TERRAIN_COUNT``
sentinel itself, which marks the enum's upper bound rather than a real
terrain -- with no gaps and no unknown extra keys, and every value must
fit the field's real C type, ``s8`` (-128..127).
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

SCHEMA_NAME = "terrainstats"
SCHEMA_VERSION = 1
SCHEMA_ID = "fe8.terrainstats.v1"

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
TERRAINS_HEADER = os.path.join(REPO_ROOT, "include", "constants", "terrains.h")

_S8_MIN, _S8_MAX = -128, 127

_TERRAIN_COUNT_SENTINEL = "TERRAIN_COUNT"

# The exact 8 arrays in scope, in the order they are declared in
# src/data_terrains.c (the order the generator/round-trip parser also
# use) -- see the module docstring for why this list is closed rather
# than derived from a wildcard/prefix scan of the hand file.
EXPECTED_SYMBOLS = (
    "TerrainTable_Avo_Common",
    "TerrainTable_Def_Common",
    "TerrainTable_Res_Common",
    "TerrainTable_Avo_Fly",
    "TerrainTable_Def_Fly",
    "TerrainTable_Res_Fly",
    "TerrainTable_HealAmount",
    "TerrainTable_HealsStatus",
)

# Expected (field, mobility) metadata per symbol, used only to validate
# the JSON source's own self-description is internally consistent (see
# the module docstring) -- the C generator never reads this table.
_EXPECTED_FIELD_AND_MOBILITY = {
    "TerrainTable_Avo_Common": ("terrainAvoid", "common"),
    "TerrainTable_Def_Common": ("terrainDefense", "common"),
    "TerrainTable_Res_Common": ("terrainResistance", "common"),
    "TerrainTable_Avo_Fly": ("terrainAvoid", "fly"),
    "TerrainTable_Def_Fly": ("terrainDefense", "fly"),
    "TerrainTable_Res_Fly": ("terrainResistance", "fly"),
    "TerrainTable_HealAmount": ("healAmount", None),
    "TerrainTable_HealsStatus": ("healsStatus", None),
}

# Role -> the symbol(s) that satisfy a ClassData terrainAvoid/
# terrainDefense/terrainResistance reference, keyed by mobility.
# Consumed by classes/schema.py's cross-table validation instead of that
# schema hardcoding the symbol names itself.
ROLE_MOBILITY_SYMBOLS = {
    ("terrainAvoid", "common"): "TerrainTable_Avo_Common",
    ("terrainAvoid", "fly"): "TerrainTable_Avo_Fly",
    ("terrainDefense", "common"): "TerrainTable_Def_Common",
    ("terrainDefense", "fly"): "TerrainTable_Def_Fly",
    ("terrainResistance", "common"): "TerrainTable_Res_Common",
    ("terrainResistance", "fly"): "TerrainTable_Res_Fly",
}


def read_terrain_constants(header=TERRAINS_HEADER):
    """Read the full ``TERRAIN_*`` enum (including the ``TERRAIN_COUNT``
    sentinel) live from ``include/constants/terrains.h``."""
    return extract_enum_constants(header, name_prefix="TERRAIN_")


def real_terrain_names(header=TERRAINS_HEADER):
    """Return the set of real terrain designator names -- every
    ``TERRAIN_*`` enum entry except the ``TERRAIN_COUNT`` sentinel."""
    constants = read_terrain_constants(header)
    return {name for name in constants if name != _TERRAIN_COUNT_SENTINEL}


def symbols_for_role(field, mobility):
    """Return the single expected ``TerrainTable_*`` symbol for a
    ``ClassData`` ``terrainAvoid``/``terrainDefense``/``terrainResistance``
    reference, or ``None`` if ``(field, mobility)`` is not a recognized
    combination."""
    return ROLE_MOBILITY_SYMBOLS.get((field, mobility))


class TerrainStatsRecord:
    """One ``TerrainTable_*`` array, with per-entry source locations."""

    def __init__(self, symbol, symbol_loc, field, field_loc, mobility, mobility_loc,
                 values, value_locs, loc):
        self.symbol = symbol
        self.symbol_loc = symbol_loc
        self.field = field
        self.field_loc = field_loc
        self.mobility = mobility
        self.mobility_loc = mobility_loc
        self.values = values  # OrderedDict: TERRAIN_* name -> int
        self.value_locs = value_locs  # TERRAIN_* name -> SourceLocation
        self.loc = loc

    def ordered_values(self, terrain_order):
        """Yield ``(terrain_name, value)`` in ``terrain_order`` (an
        iterable of ``TERRAIN_*`` names, typically ascending enum-value
        order), skipping any name absent from ``self.values`` (a missing
        key is already reported as a validation diagnostic elsewhere)."""
        for name in terrain_order:
            if name in self.values:
                yield name, self.values[name]


def load_records(source_path):
    """Parse the JSON source into a list of :class:`TerrainStatsRecord`.

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
    tables_node = root.require("tables")
    records = []
    for table_node in tables_node.as_list():
        symbol_node = table_node.require("symbol")
        field_node = table_node.require("field")
        mobility_node = table_node.get("mobility")
        values_node = table_node.require("values")

        values = {}
        value_locs = {}
        for key, value_node in values_node.items():
            values[key] = value_node.as_int()
            value_locs[key] = value_node.loc

        records.append(
            TerrainStatsRecord(
                symbol=symbol_node.as_str(), symbol_loc=symbol_node.loc,
                field=field_node.as_str(), field_loc=field_node.loc,
                mobility=mobility_node.as_str() if mobility_node is not None else None,
                mobility_loc=mobility_node.loc if mobility_node is not None else None,
                values=values, value_locs=value_locs,
                loc=table_node.loc,
            )
        )
    return records


def validate(records, diagnostics, terrains_header=TERRAINS_HEADER):
    """Run all ``TerrainTable_*`` validations, appending errors to
    ``diagnostics``.

    Checks: unique symbols, exactly the 8 expected symbols (no missing,
    no unrecognized extra entries), each record's ``field``/``mobility``
    self-description matches what that symbol is expected to declare,
    full ``TERRAIN_COUNT`` coverage per array (no gaps, no unknown extra
    terrain keys), and every value is a valid ``s8`` (-128..127).
    """
    real_terrains = real_terrain_names(terrains_header)
    expected_set = set(EXPECTED_SYMBOLS)

    diagnostics.extend(
        validate_unique(
            ((r.symbol, r.symbol_loc) for r in records),
            "duplicate terrain-stats array '{key}' (first defined at {first_loc})",
            "tables[symbol={key}]",
        )
    )

    seen_symbols = set()
    for record in records:
        ref = "tables[symbol={}]".format(record.symbol)
        seen_symbols.add(record.symbol)

        if record.symbol not in expected_set:
            diagnostics.add(
                GeneratedDataError(
                    "unexpected terrain-stats symbol '{}' -- not one of the 8 arrays in scope "
                    "(expected one of {})".format(record.symbol, sorted(expected_set)),
                    record.symbol_loc, ref,
                )
            )
        else:
            expected_field, expected_mobility = _EXPECTED_FIELD_AND_MOBILITY[record.symbol]
            if record.field != expected_field:
                diagnostics.add(
                    GeneratedDataError(
                        "field '{}' does not match expected '{}' for symbol '{}'".format(
                            record.field, expected_field, record.symbol
                        ),
                        record.field_loc or record.loc, "{}.field".format(ref),
                    )
                )
            if record.mobility != expected_mobility:
                diagnostics.add(
                    GeneratedDataError(
                        "mobility '{}' does not match expected '{}' for symbol '{}'".format(
                            record.mobility, expected_mobility, record.symbol
                        ),
                        record.mobility_loc or record.loc, "{}.mobility".format(ref),
                    )
                )

        seen_terrains = set(record.values)
        missing_terrains = sorted(real_terrains - seen_terrains)
        extra_terrains = sorted(seen_terrains - real_terrains)
        for name in missing_terrains:
            diagnostics.add(
                GeneratedDataError(
                    "missing terrain coverage for '{}' in '{}'".format(name, record.symbol),
                    record.loc, "{}.values[{}]".format(ref, name),
                )
            )
        for name in extra_terrains:
            diagnostics.extend(
                validate_reference(
                    name, real_terrains, record.value_locs.get(name, record.loc),
                    "{}.values[{}]".format(ref, name), kind="terrain",
                )
            )

        for name, value in record.values.items():
            if name not in real_terrains:
                continue
            diagnostics.extend(
                validate_range(
                    value, _S8_MIN, _S8_MAX, record.value_locs.get(name, record.loc),
                    "{}.values[{}]".format(ref, name), field_name=name,
                )
            )

    missing_symbols = sorted(expected_set - seen_symbols)
    for symbol in missing_symbols:
        diagnostics.add(
            GeneratedDataError(
                "missing terrain-stats array '{}' -- all 8 arrays in scope must be authored".format(symbol),
                getattr(records, "loc", None), "tables[symbol={}]".format(symbol),
            )
        )


class TerrainStatsTableSchema(TableSchema):
    name = SCHEMA_NAME
    version = SCHEMA_VERSION

    default_source = "src/data/terrainstats.json"
    default_hand_source = "src/data_terrains.c"
    default_output_name = "data_terrainstats.c"
    default_inventory_path = "reports/generated_data_terrainstats_inventory.md"

    def dependencies(self):
        return ("constants.terrains.TERRAIN",)

    def load_records(self, source_path):
        return load_records(source_path)

    def validate(self, records, diagnostics):
        validate(records, diagnostics)

    def generate_c(self, records, source_path):
        from . import generate as terrainstats_generate
        return terrainstats_generate.generate_c_source(records, source_path)

    def build_inventory(self, records):
        from . import inventory as terrainstats_inventory
        return terrainstats_inventory.build_inventory(records)

    def round_trip_errors(self, records, hand_source):
        if not hand_source or not os.path.exists(hand_source):
            return []
        from . import parser as terrainstats_roundtrip
        symbols = [r.symbol for r in records]
        hand_records = terrainstats_roundtrip.parse_hand_written(hand_source, symbols)
        return terrainstats_roundtrip.compare_records(records, hand_records, hand_path=hand_source)


def dependency_graph():
    graph = DependencyGraph()
    for dep in TerrainStatsTableSchema().dependencies():
        graph.add_dependency(SCHEMA_NAME, dep)
    return graph
