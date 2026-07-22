"""``UnitDefinition``/``REDA`` schema: the Chapter 2 units vertical slice.

Mirrors the hand-written C layout in ``include/bmunit.h``
(``struct UnitDefinition``) and ``include/muctrl.h`` (``struct REDA``)
field-for-field so the round-trip checker (``parser.py``) can compare
generated output against ``src/events_udefs.c`` with no semantic
reinterpretation.

JSON source shape (see ``src/data/ch2_units.json``)::

    {
      "$schema": "fe8.units.v1",
      "groups": [
        {
          "symbol": "UnitDef_Event_Ch2Ally",
          "units": [
            {
              "charIndex": "CHARACTER_EIRIKA",
              "classIndex": "CLASS_EIRIKA_LORD",
              "allegiance": "FACTION_ID_BLUE",
              "level": 1,
              "xPosition": 1,
              "yPosition": 0,
              "redas": [ { "x": 2, "y": 2, "b": 65535 } ],
              "items": ["ITEM_SWORD_RAPIER", "ITEM_VULNERARY"]
            },
            ...
          ]
        },
        ...
      ]
    }

Optional ``UnitDefinition`` fields (``leaderCharIndex``, ``autolevel``,
``genMonster``, ``itemDrop``, ``sumFlag``, ``extraData``, ``ai``) default to
``0``/``[0, 0, 0, 0]`` when omitted, exactly like the hand-written
designated initializers that only set the fields they need. ``redaCount``
is never stored -- it's always derived as ``len(redas)``.
"""

from __future__ import annotations

import os
import re

from ..diagnostics import GeneratedDataError
from ..json_loader import load_json_file
from ..schema import DependencyGraph, TableSchema
from .. import character_refs
from ..validators import (
    extract_enum_constants,
    validate_fixed_capacity,
    validate_range,
    validate_reference,
    validate_unique,
)

SCHEMA_NAME = "units"
SCHEMA_VERSION = 1
SCHEMA_ID = "fe8.units.v1"

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
CHARACTERS_HEADER = character_refs.CHARACTERS_HEADER
CLASSES_HEADER = os.path.join(REPO_ROOT, "include", "constants", "classes.h")
ITEMS_HEADER = os.path.join(REPO_ROOT, "include", "constants", "items.h")
BMUNIT_HEADER = os.path.join(REPO_ROOT, "include", "bmunit.h")

_U8_MIN, _U8_MAX = 0, 255
_U6_MIN, _U6_MAX = 0, 63    # 6-bit bitfields: xPosition/yPosition, REDA x/y/flags
_U16_MIN, _U16_MAX = 0, 65535
_LEVEL_MIN, _LEVEL_MAX = 1, 31  # 5-bit level bitfield; 0 is not a meaningful unit level
_BOOL_MIN, _BOOL_MAX = 0, 1

_ITEM_COUNT_RE = re.compile(r"UNIT_DEFINITION_ITEM_COUNT\s*=\s*(\d+)")
_AIIDX_ENUM_RE = re.compile(r"enum\s+udef_ai_index\s*\{(.*?)\}", re.S)


def read_unit_definition_item_count(header=BMUNIT_HEADER):
    """Read ``UNIT_DEFINITION_ITEM_COUNT`` live from ``include/bmunit.h``."""
    with open(header, "r", encoding="utf-8") as handle:
        text = handle.read()
    match = _ITEM_COUNT_RE.search(text)
    if not match:
        raise GeneratedDataError("could not find UNIT_DEFINITION_ITEM_COUNT in {}".format(header))
    return int(match.group(1))


def read_udef_aiidx_max(header=BMUNIT_HEADER):
    """Derive ``UDEF_AIIDX_MAX`` (an unvalued trailing enumerator) by
    counting the named entries before it in ``enum udef_ai_index`` -- read
    live from the header instead of a bare hardcoded ``4``."""
    with open(header, "r", encoding="utf-8") as handle:
        text = handle.read()
    match = _AIIDX_ENUM_RE.search(text)
    if not match:
        raise GeneratedDataError("could not find 'enum udef_ai_index' in {}".format(header))
    entries = [e.strip() for e in match.group(1).split(",") if e.strip()]
    if "UDEF_AIIDX_MAX" not in entries:
        raise GeneratedDataError("UDEF_AIIDX_MAX not found in enum udef_ai_index in {}".format(header))
    return entries.index("UDEF_AIIDX_MAX")


class RedaPoint:
    __slots__ = ("x", "y", "flags", "a", "b", "delay_frames", "loc")

    def __init__(self, x, y, flags, a, b, delay_frames, loc):
        self.x = x
        self.y = y
        self.flags = flags
        self.a = a
        self.b = b
        self.delay_frames = delay_frames
        self.loc = loc

    def as_tuple(self):
        return (self.x, self.y, self.flags, self.a, self.b, self.delay_frames)


class UnitRecord:
    """One ``UnitDefinition`` entry, with per-field source locations."""

    def __init__(self, char_index, char_index_loc, class_index, class_index_loc,
                 leader_char_index, allegiance, allegiance_loc, level, level_loc,
                 x_position, x_position_loc, y_position, y_position_loc,
                 autolevel, gen_monster, item_drop, sum_flag, extra_data,
                 redas, items, item_locs, ai, loc):
        self.char_index = char_index
        self.char_index_loc = char_index_loc
        self.class_index = class_index
        self.class_index_loc = class_index_loc
        self.leader_char_index = leader_char_index
        self.allegiance = allegiance
        self.allegiance_loc = allegiance_loc
        self.level = level
        self.level_loc = level_loc
        self.x_position = x_position
        self.x_position_loc = x_position_loc
        self.y_position = y_position
        self.y_position_loc = y_position_loc
        self.autolevel = autolevel
        self.gen_monster = gen_monster
        self.item_drop = item_drop
        self.sum_flag = sum_flag
        self.extra_data = extra_data
        self.redas = redas
        self.items = items
        self.item_locs = item_locs
        self.ai = ai
        self.loc = loc

    @property
    def reda_count(self):
        return len(self.redas)


class UnitGroup:
    def __init__(self, symbol, symbol_loc, units, loc):
        self.symbol = symbol
        self.symbol_loc = symbol_loc
        self.units = units
        self.loc = loc


def _as_char_or_int(node):
    """``charIndex``/``classIndex``/``leaderCharIndex`` accept either a
    symbolic ``CHARACTER_*``/``CLASS_*`` string or a raw ``u8`` integer (the
    vanilla data uses raw hex for generic/filler enemy units that have no
    named character constant, e.g. ``0x8e``)."""
    if node.is_scalar() and isinstance(node.value, str):
        return node.value
    return node.as_int()


def load_records(source_path):
    """Parse the JSON source into a list of :class:`UnitGroup`."""
    root = load_json_file(source_path)
    schema_node = root.require("$schema")
    if schema_node.as_str() != SCHEMA_ID:
        raise GeneratedDataError(
            "unexpected $schema '{}', expected '{}'".format(schema_node.as_str(), SCHEMA_ID),
            schema_node.loc,
        )
    groups_node = root.require("groups")
    groups = []
    for group_node in groups_node.as_list():
        symbol_node = group_node.require("symbol")
        units_node = group_node.require("units")
        units = []
        for unit_node in units_node.as_list():
            char_index_node = unit_node.require("charIndex")
            class_index_node = unit_node.require("classIndex")
            allegiance_node = unit_node.require("allegiance")
            level_node = unit_node.require("level")
            x_node = unit_node.require("xPosition")
            y_node = unit_node.require("yPosition")

            leader_char_node = unit_node.get("leaderCharIndex")
            autolevel_node = unit_node.get("autolevel")
            gen_monster_node = unit_node.get("genMonster")
            item_drop_node = unit_node.get("itemDrop")
            sum_flag_node = unit_node.get("sumFlag")
            extra_data_node = unit_node.get("extraData")
            ai_node = unit_node.get("ai")

            redas = []
            redas_node = unit_node.get("redas")
            for reda_node in (redas_node.as_list() if redas_node is not None else []):
                x_n = reda_node.require("x")
                y_n = reda_node.require("y")
                flags_n = reda_node.get("flags")
                a_n = reda_node.get("a")
                b_n = reda_node.get("b")
                delay_n = reda_node.get("delayFrames")
                redas.append(
                    RedaPoint(
                        x=x_n.as_int(), y=y_n.as_int(),
                        flags=flags_n.as_int() if flags_n is not None else 0,
                        a=a_n.as_int() if a_n is not None else 0,
                        b=b_n.as_int() if b_n is not None else 0,
                        delay_frames=delay_n.as_int() if delay_n is not None else 0,
                        loc=reda_node.loc,
                    )
                )

            items_node = unit_node.require("items")
            item_nodes = items_node.as_list()

            units.append(
                UnitRecord(
                    char_index=_as_char_or_int(char_index_node), char_index_loc=char_index_node.loc,
                    class_index=_as_char_or_int(class_index_node), class_index_loc=class_index_node.loc,
                    leader_char_index=_as_char_or_int(leader_char_node) if leader_char_node is not None else 0,
                    allegiance=allegiance_node.as_str(), allegiance_loc=allegiance_node.loc,
                    level=level_node.as_int(), level_loc=level_node.loc,
                    x_position=x_node.as_int(), x_position_loc=x_node.loc,
                    y_position=y_node.as_int(), y_position_loc=y_node.loc,
                    autolevel=autolevel_node.as_int() if autolevel_node is not None else 0,
                    gen_monster=gen_monster_node.as_int() if gen_monster_node is not None else 0,
                    item_drop=item_drop_node.as_int() if item_drop_node is not None else 0,
                    sum_flag=sum_flag_node.as_int() if sum_flag_node is not None else 0,
                    extra_data=extra_data_node.as_int() if extra_data_node is not None else 0,
                    redas=redas,
                    items=[n.as_str() for n in item_nodes],
                    item_locs=[n.loc for n in item_nodes],
                    ai=[n.as_int() for n in ai_node.as_list()] if ai_node is not None else [0, 0, 0, 0],
                    loc=unit_node.loc,
                )
            )
        groups.append(UnitGroup(symbol=symbol_node.as_str(), symbol_loc=symbol_node.loc, units=units, loc=group_node.loc))
    return groups


def _validate_char_or_int(value, chars, loc, ref_path, kind, field_name):
    if isinstance(value, str):
        return validate_reference(value, chars, loc, ref_path, kind=kind)
    return validate_range(value, _U8_MIN, _U8_MAX, loc, ref_path, field_name=field_name)


def validate(groups, diagnostics, characters_header=CHARACTERS_HEADER, classes_header=CLASSES_HEADER,
             items_header=ITEMS_HEADER, item_capacity=None, ai_length=None):
    """Run all UnitDefinition/REDA validations, appending errors to ``diagnostics``.

    Checks: unique group symbols, valid character/class/item/allegiance
    references (or raw ``u8`` for generic filler units where applicable),
    level/position/REDA ranges, fixed item capacity
    (``UNIT_DEFINITION_ITEM_COUNT``), exact ``ai`` length
    (``UDEF_AIIDX_MAX``), and boolean-bitfield ranges.
    """
    if item_capacity is None:
        item_capacity = read_unit_definition_item_count()
    if ai_length is None:
        ai_length = read_udef_aiidx_max()

    characters = character_refs.read_character_designators(characters_header)
    classes = extract_enum_constants(classes_header, name_prefix="CLASS_")
    items = extract_enum_constants(items_header, name_prefix="ITEM_")
    factions = extract_enum_constants(BMUNIT_HEADER, name_prefix="FACTION_ID_")

    diagnostics.extend(
        validate_unique(
            ((g.symbol, g.symbol_loc) for g in groups),
            "duplicate unit group symbol '{key}' (first defined at {first_loc})",
            "groups[symbol={key}]",
        )
    )

    for group in groups:
        for index, unit in enumerate(group.units):
            ref_prefix = "groups[symbol={}].units[{}]".format(group.symbol, index)

            diagnostics.extend(
                _validate_char_or_int(
                    unit.char_index, characters, unit.char_index_loc,
                    "{}.charIndex".format(ref_prefix), "character", "charIndex",
                )
            )
            diagnostics.extend(
                _validate_char_or_int(
                    unit.class_index, classes, unit.class_index_loc,
                    "{}.classIndex".format(ref_prefix), "class", "classIndex",
                )
            )
            diagnostics.extend(
                validate_reference(
                    unit.allegiance, factions, unit.allegiance_loc,
                    "{}.allegiance".format(ref_prefix), kind="faction",
                )
            )
            diagnostics.extend(
                validate_range(unit.level, _LEVEL_MIN, _LEVEL_MAX, unit.level_loc,
                                "{}.level".format(ref_prefix), field_name="level")
            )
            diagnostics.extend(
                validate_range(unit.x_position, _U6_MIN, _U6_MAX, unit.x_position_loc,
                                "{}.xPosition".format(ref_prefix), field_name="xPosition")
            )
            diagnostics.extend(
                validate_range(unit.y_position, _U6_MIN, _U6_MAX, unit.y_position_loc,
                                "{}.yPosition".format(ref_prefix), field_name="yPosition")
            )
            for flag_name, flag_value in (
                ("autolevel", unit.autolevel), ("genMonster", unit.gen_monster),
                ("itemDrop", unit.item_drop), ("sumFlag", unit.sum_flag),
            ):
                diagnostics.extend(
                    validate_range(flag_value, _BOOL_MIN, _BOOL_MAX, unit.loc,
                                    "{}.{}".format(ref_prefix, flag_name), field_name=flag_name)
                )
            diagnostics.extend(
                validate_range(unit.extra_data, _U8_MIN, _U8_MAX, unit.loc,
                                "{}.extraData".format(ref_prefix), field_name="extraData")
            )

            for reda_index, reda in enumerate(unit.redas):
                reda_ref = "{}.redas[{}]".format(ref_prefix, reda_index)
                diagnostics.extend(validate_range(reda.x, _U6_MIN, _U6_MAX, reda.loc, reda_ref, field_name="x"))
                diagnostics.extend(validate_range(reda.y, _U6_MIN, _U6_MAX, reda.loc, reda_ref, field_name="y"))
                diagnostics.extend(validate_range(reda.flags, _U6_MIN, _U6_MAX, reda.loc, reda_ref, field_name="flags"))
                diagnostics.extend(validate_range(reda.a, _U8_MIN, _U8_MAX, reda.loc, reda_ref, field_name="a"))
                diagnostics.extend(validate_range(reda.b, _U16_MIN, _U16_MAX, reda.loc, reda_ref, field_name="b"))
                diagnostics.extend(
                    validate_range(reda.delay_frames, _U16_MIN, _U16_MAX, reda.loc, reda_ref, field_name="delayFrames")
                )

            diagnostics.extend(
                validate_fixed_capacity(
                    len(unit.items), item_capacity, unit.loc,
                    "{}.items".format(ref_prefix), what="items",
                )
            )
            for item_index, (item, item_loc) in enumerate(zip(unit.items, unit.item_locs)):
                diagnostics.extend(
                    validate_reference(item, items, item_loc, "{}.items[{}]".format(ref_prefix, item_index), kind="item")
                )

            if len(unit.ai) != ai_length:
                diagnostics.add(
                    GeneratedDataError(
                        "ai array has {} entries, expected exactly {} (UDEF_AIIDX_MAX)".format(
                            len(unit.ai), ai_length
                        ),
                        unit.loc,
                        "{}.ai".format(ref_prefix),
                    )
                )
            for ai_index, ai_value in enumerate(unit.ai):
                diagnostics.extend(
                    validate_range(ai_value, _U8_MIN, _U8_MAX, unit.loc,
                                    "{}.ai[{}]".format(ref_prefix, ai_index), field_name="ai")
                )


class UnitsTableSchema(TableSchema):
    name = SCHEMA_NAME
    version = SCHEMA_VERSION

    default_source = "src/data/ch2_units.json"
    default_hand_source = "src/events_udefs.c"
    default_output_name = "data_ch2_units.c"
    default_inventory_path = "reports/generated_data_units_inventory.md"

    def dependencies(self):
        return (
            "constants.characters", "constants.classes", "constants.items",
            "bmunit.FACTION_ID", "bmunit.UNIT_DEFINITION_ITEM_COUNT", "bmunit.UDEF_AIIDX_MAX",
        )

    def load_records(self, source_path):
        return load_records(source_path)

    def validate(self, records, diagnostics):
        validate(records, diagnostics)

    def generate_c(self, records, source_path):
        from . import generate as units_generate
        return units_generate.generate_c_source(records, source_path)

    def build_inventory(self, records):
        from . import inventory as units_inventory
        return units_inventory.build_inventory(records)

    def round_trip_errors(self, records, hand_source):
        if not hand_source or not os.path.exists(hand_source):
            return []
        from . import parser as units_roundtrip
        group_symbols = [g.symbol for g in records]
        hand_groups = units_roundtrip.parse_hand_written(hand_source, group_symbols)
        return units_roundtrip.compare_groups(records, hand_groups, hand_path=hand_source)


def dependency_graph():
    graph = DependencyGraph()
    for dep in UnitsTableSchema().dependencies():
        graph.add_dependency(SCHEMA_NAME, dep)
    return graph
