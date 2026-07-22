"""``ShopList`` (``u16[]`` item-symbol) schema: the Chapter 2 shops vertical
slice (``ShopList_Event_Ch2Armory`` only, per Issue #5 Batch A scope).

Mirrors the hand-written format in ``src/events_shoplist.c``: an ordered
``u16[]`` array of plain ``ITEM_*`` symbols (no per-item price -- unlike the
older ``u8[]`` shop lists that pair ``item, price``), terminated by a single
``ITEM_NONE`` sentinel. The JSON source never stores the terminator -- it
is always auto-appended at generation time and stripped for comparison
during the hand round-trip.

JSON source shape (see ``src/data/ch2_shops.json``)::

    {
      "$schema": "fe8.shops.v1",
      "shops": [
        {
          "symbol": "ShopList_Event_Ch2Armory",
          "items": ["ITEM_SWORD_SLIM", "ITEM_SWORD_IRON", ...]
        }
      ]
    }
"""

from __future__ import annotations

import os

from ..diagnostics import GeneratedDataError
from ..json_loader import load_json_file
from ..schema import DependencyGraph, TableSchema
from ..validators import extract_enum_constants, validate_reference, validate_unique

SCHEMA_NAME = "shops"
SCHEMA_VERSION = 1
SCHEMA_ID = "fe8.shops.v1"

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
ITEMS_HEADER = os.path.join(REPO_ROOT, "include", "constants", "items.h")

ITEM_NONE = "ITEM_NONE"


class ShopRecord:
    def __init__(self, symbol, symbol_loc, items, item_locs, loc):
        self.symbol = symbol
        self.symbol_loc = symbol_loc
        self.items = items
        self.item_locs = item_locs
        self.loc = loc


def load_records(source_path):
    """Parse the JSON source into a list of :class:`ShopRecord`."""
    root = load_json_file(source_path)
    schema_node = root.require("$schema")
    if schema_node.as_str() != SCHEMA_ID:
        raise GeneratedDataError(
            "unexpected $schema '{}', expected '{}'".format(schema_node.as_str(), SCHEMA_ID),
            schema_node.loc,
        )
    shops_node = root.require("shops")
    records = []
    for shop_node in shops_node.as_list():
        symbol_node = shop_node.require("symbol")
        items_node = shop_node.require("items")
        item_nodes = items_node.as_list()
        records.append(
            ShopRecord(
                symbol=symbol_node.as_str(), symbol_loc=symbol_node.loc,
                items=[n.as_str() for n in item_nodes],
                item_locs=[n.loc for n in item_nodes],
                loc=shop_node.loc,
            )
        )
    return records


def validate(records, diagnostics, items_header=ITEMS_HEADER):
    """Validate unique shop symbols, non-empty ordered item lists, valid
    ``ITEM_*`` references, and that ``ITEM_NONE`` (the terminator sentinel)
    is never stored explicitly in the JSON item list."""
    items = extract_enum_constants(items_header, name_prefix="ITEM_")

    diagnostics.extend(
        validate_unique(
            ((r.symbol, r.symbol_loc) for r in records),
            "duplicate shop symbol '{key}' (first defined at {first_loc})",
            "shops[symbol={key}]",
        )
    )

    for record in records:
        ref_prefix = "shops[symbol={}]".format(record.symbol)
        if not record.items:
            diagnostics.add(
                GeneratedDataError(
                    "shop '{}' has no items".format(record.symbol),
                    record.loc,
                    "{}.items".format(ref_prefix),
                )
            )
        for index, (item, item_loc) in enumerate(zip(record.items, record.item_locs)):
            if item == ITEM_NONE:
                diagnostics.add(
                    GeneratedDataError(
                        "shop item list must not include the {} terminator explicitly "
                        "(it is auto-appended at generation)".format(ITEM_NONE),
                        item_loc,
                        "{}.items[{}]".format(ref_prefix, index),
                    )
                )
                continue
            diagnostics.extend(
                validate_reference(item, items, item_loc, "{}.items[{}]".format(ref_prefix, index), kind="item")
            )


class ShopsTableSchema(TableSchema):
    name = SCHEMA_NAME
    version = SCHEMA_VERSION

    default_source = "src/data/ch2_shops.json"
    default_hand_source = "src/events_shoplist.c"
    default_output_name = "data_ch2_shops.c"
    default_inventory_path = "reports/generated_data_shops_inventory.md"

    def dependencies(self):
        return ("constants.items",)

    def load_records(self, source_path):
        return load_records(source_path)

    def validate(self, records, diagnostics):
        validate(records, diagnostics)

    def generate_c(self, records, source_path):
        from . import generate as shops_generate
        return shops_generate.generate_c_source(records, source_path)

    def build_inventory(self, records):
        from . import inventory as shops_inventory
        return shops_inventory.build_inventory(records)

    def round_trip_errors(self, records, hand_source):
        if not hand_source or not os.path.exists(hand_source):
            return []
        from . import parser as shops_roundtrip
        symbols = [r.symbol for r in records]
        hand_records = shops_roundtrip.parse_hand_written(hand_source, symbols)
        return shops_roundtrip.compare_records(records, hand_records, hand_path=hand_source)


def dependency_graph():
    graph = DependencyGraph()
    for dep in ShopsTableSchema().dependencies():
        graph.add_dependency(SCHEMA_NAME, dep)
    return graph
