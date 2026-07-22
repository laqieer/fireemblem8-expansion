"""``TrapData`` (flat ``u8[]`` initializer) schema: the Chapter 2 traps
vertical slice (``TrapData_Event_Ch2``/``TrapData_Event_Ch2Hard``).

Mirrors the hand-written flat byte-stream format in
``src/events_trapdata.c``: each non-terminal entry is 6 bytes --
``type, xPos, yPos, subtype, turnCounter, turn`` (see the inline
``/* type */``/``/* xPos */``/... comments in that file for the field
order this schema follows) -- and the array is terminated by a single
bare ``TRAP_NONE`` byte (not a full 6-byte zeroed entry -- confirmed via
``TrapData_Event_Ch7``, which has 2 full ballista entries followed by a
lone ``TRAP_NONE``).

Chapter 2's own trap data is trivial (both arrays are just
``{ TRAP_NONE }`` -- no real traps), but the schema/generator/round-trip
parser support the full 6-field format generally (exercised by this
table's test fixtures), per Issue #5 Batch A scope.

``subtype`` accepts either a symbolic reference (``ITEM_*`` for
``TRAP_BALLISTA``) or a raw ``u8`` integer (e.g. plain ``0`` for
``TRAP_GORGON_EGG``), exactly like ``UnitDefinition.charIndex`` --
the meaning is trap-type-dependent and not validated beyond range.
"""

from __future__ import annotations

import os
import re

from ..diagnostics import GeneratedDataError
from ..json_loader import load_json_file
from ..schema import DependencyGraph, TableSchema
from ..validators import (
    extract_enum_constants,
    validate_fixed_capacity,
    validate_range,
    validate_reference,
    validate_unique,
)

SCHEMA_NAME = "traps"
SCHEMA_VERSION = 1
SCHEMA_ID = "fe8.traps.v1"

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
BMTRICK_HEADER = os.path.join(REPO_ROOT, "include", "bmtrick.h")
ITEMS_HEADER = os.path.join(REPO_ROOT, "include", "constants", "items.h")

_U8_MIN, _U8_MAX = 0, 255
TRAP_NONE = "TRAP_NONE"

_TRAP_TYPE_ENUM_RE = re.compile(r"enum\s*\{\s*(TRAP_NONE\s*=.*?)\}", re.S)
_TRAP_TYPE_ENTRY_RE = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(-?0[xX][0-9A-Fa-f]+|-?\d+)")


def read_trap_types(header=BMTRICK_HEADER):
    """Read the ``TRAP_*`` trap-type enum (the block starting at
    ``TRAP_NONE = 0``) live from ``include/bmtrick.h``, scoped precisely
    to that one enum -- not the unrelated ``TRAP_MAX_COUNT``/
    ``TRAP_EXTDATA_*`` enums that happen to share the ``TRAP_`` prefix."""
    with open(header, "r", encoding="utf-8") as handle:
        text = handle.read()
    match = _TRAP_TYPE_ENUM_RE.search(text)
    if not match:
        raise GeneratedDataError("could not find the TRAP_* trap-type enum in {}".format(header))
    types = {}
    for entry_match in _TRAP_TYPE_ENTRY_RE.finditer(match.group(1)):
        name = entry_match.group(1)
        value_text = entry_match.group(2)
        value = int(value_text, 16) if value_text.lower().startswith(("0x", "-0x")) else int(value_text)
        types[name] = (value, 0)
    return types


def read_trap_max_count(header=BMTRICK_HEADER):
    with open(header, "r", encoding="utf-8") as handle:
        text = handle.read()
    match = re.search(r"TRAP_MAX_COUNT\s*=\s*(\d+)", text)
    if not match:
        raise GeneratedDataError("could not find TRAP_MAX_COUNT in {}".format(header))
    return int(match.group(1))


def _as_symbol_or_int(node):
    if node.is_scalar() and isinstance(node.value, str):
        return node.value
    return node.as_int()


class TrapEntry:
    def __init__(self, trap_type, trap_type_loc, x_position, y_position, subtype, subtype_loc,
                 turn_counter, turn, loc):
        self.trap_type = trap_type
        self.trap_type_loc = trap_type_loc
        self.x_position = x_position
        self.y_position = y_position
        self.subtype = subtype
        self.subtype_loc = subtype_loc
        self.turn_counter = turn_counter
        self.turn = turn
        self.loc = loc

    def as_tuple(self):
        return (self.trap_type, self.x_position, self.y_position, self.subtype, self.turn_counter, self.turn)


class TrapArray:
    def __init__(self, symbol, symbol_loc, entries, loc):
        self.symbol = symbol
        self.symbol_loc = symbol_loc
        self.entries = entries
        self.loc = loc


def load_records(source_path):
    root = load_json_file(source_path)
    schema_node = root.require("$schema")
    if schema_node.as_str() != SCHEMA_ID:
        raise GeneratedDataError(
            "unexpected $schema '{}', expected '{}'".format(schema_node.as_str(), SCHEMA_ID),
            schema_node.loc,
        )
    traps_node = root.require("traps")
    records = []
    for trap_node in traps_node.as_list():
        symbol_node = trap_node.require("symbol")
        entries_node = trap_node.require("entries")
        entries = []
        for entry_node in entries_node.as_list():
            type_node = entry_node.require("type")
            x_node = entry_node.require("xPosition")
            y_node = entry_node.require("yPosition")
            subtype_node = entry_node.get("subtype")
            counter_node = entry_node.get("turnCounter")
            turn_node = entry_node.get("turn")
            entries.append(
                TrapEntry(
                    trap_type=type_node.as_str(), trap_type_loc=type_node.loc,
                    x_position=x_node.as_int(), y_position=y_node.as_int(),
                    subtype=_as_symbol_or_int(subtype_node) if subtype_node is not None else 0,
                    subtype_loc=subtype_node.loc if subtype_node is not None else entry_node.loc,
                    turn_counter=counter_node.as_int() if counter_node is not None else 0,
                    turn=turn_node.as_int() if turn_node is not None else 0,
                    loc=entry_node.loc,
                )
            )
        records.append(
            TrapArray(symbol=symbol_node.as_str(), symbol_loc=symbol_node.loc, entries=entries, loc=trap_node.loc)
        )
    return records


def validate(records, diagnostics, trap_types_header=BMTRICK_HEADER, items_header=ITEMS_HEADER, capacity=None):
    if capacity is None:
        capacity = read_trap_max_count(trap_types_header)
    trap_types = read_trap_types(trap_types_header)
    items = extract_enum_constants(items_header, name_prefix="ITEM_")

    diagnostics.extend(
        validate_unique(
            ((r.symbol, r.symbol_loc) for r in records),
            "duplicate trap array symbol '{key}' (first defined at {first_loc})",
            "traps[symbol={key}]",
        )
    )

    for record in records:
        ref_prefix = "traps[symbol={}]".format(record.symbol)
        diagnostics.extend(
            validate_fixed_capacity(len(record.entries), capacity, record.loc, "{}.entries".format(ref_prefix),
                                     what="trap entries")
        )
        for index, entry in enumerate(record.entries):
            entry_ref = "{}.entries[{}]".format(ref_prefix, index)
            diagnostics.extend(
                validate_reference(entry.trap_type, trap_types, entry.trap_type_loc,
                                    "{}.type".format(entry_ref), kind="trap type")
            )
            if entry.trap_type == TRAP_NONE:
                diagnostics.add(
                    GeneratedDataError(
                        "trap entries must not include the {} terminator explicitly "
                        "(it is auto-appended at generation)".format(TRAP_NONE),
                        entry.loc,
                        "{}.type".format(entry_ref),
                    )
                )
                continue
            diagnostics.extend(
                validate_range(entry.x_position, _U8_MIN, _U8_MAX, entry.loc, "{}.xPosition".format(entry_ref),
                                field_name="xPosition")
            )
            diagnostics.extend(
                validate_range(entry.y_position, _U8_MIN, _U8_MAX, entry.loc, "{}.yPosition".format(entry_ref),
                                field_name="yPosition")
            )
            if isinstance(entry.subtype, str):
                diagnostics.extend(
                    validate_reference(entry.subtype, items, entry.subtype_loc, "{}.subtype".format(entry_ref),
                                        kind="item")
                )
            else:
                diagnostics.extend(
                    validate_range(entry.subtype, _U8_MIN, _U8_MAX, entry.subtype_loc,
                                    "{}.subtype".format(entry_ref), field_name="subtype")
                )
            diagnostics.extend(
                validate_range(entry.turn_counter, _U8_MIN, _U8_MAX, entry.loc, "{}.turnCounter".format(entry_ref),
                                field_name="turnCounter")
            )
            diagnostics.extend(
                validate_range(entry.turn, _U8_MIN, _U8_MAX, entry.loc, "{}.turn".format(entry_ref),
                                field_name="turn")
            )


class TrapsTableSchema(TableSchema):
    name = SCHEMA_NAME
    version = SCHEMA_VERSION

    default_source = "src/data/ch2_traps.json"
    default_hand_source = "src/events_trapdata.c"
    default_output_name = "data_ch2_traps.c"
    default_inventory_path = "reports/generated_data_traps_inventory.md"

    def dependencies(self):
        return ("bmtrick.TRAP_TYPE", "bmtrick.TRAP_MAX_COUNT", "constants.items")

    def load_records(self, source_path):
        return load_records(source_path)

    def validate(self, records, diagnostics):
        validate(records, diagnostics)

    def generate_c(self, records, source_path):
        from . import generate as traps_generate
        return traps_generate.generate_c_source(records, source_path)

    def build_inventory(self, records):
        from . import inventory as traps_inventory
        return traps_inventory.build_inventory(records)

    def round_trip_errors(self, records, hand_source):
        if not hand_source or not os.path.exists(hand_source):
            return []
        from . import parser as traps_roundtrip
        symbols = [r.symbol for r in records]
        hand_records = traps_roundtrip.parse_hand_written(hand_source, symbols)
        return traps_roundtrip.compare_records(records, hand_records, hand_path=hand_source)


def dependency_graph():
    graph = DependencyGraph()
    for dep in TrapsTableSchema().dependencies():
        graph.add_dependency(SCHEMA_NAME, dep)
    return graph
