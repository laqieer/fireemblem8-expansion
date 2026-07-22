"""``eventscripts`` metadata schema: the Chapter 2 event-script inventory
vertical slice.

This table is deliberately **metadata-only**: it does not generate,
reinterpret, or reimplement any event-script/macro body (see
``docs/generated_data.md`` for why the event DSL itself stays out of
scope for Issue #5 Batch A). It records *which* ``EventListScr``/
``EventScr`` symbols the Chapter 2 vertical slice depends on -- owner
(the ``struct ChapterEventGroup`` field/category it belongs to, see
``src/events/ch2-eventinfo.h``'s ``Ch2Events``), the C symbol itself, its
declared type ("kind"), and the header it must be declared in -- and
validates each symbol is genuinely ``extern``-declared there, using
:class:`~..escape_hatch.CSymbolRefField` as the first real production
consumer of that escape hatch (rather than the higher-level
``Ch2Events``/``EventListScr_Ch2_*`` constructs, which are themselves
``.c``-file-only, not header-declared, and so are out of scope here).

``default_output_name`` is intentionally left ``None`` -- there is no
meaningful C to (re)generate for a table that only proves symbols exist;
the CLI (see ``cli.py``) explicitly prints a skip message instead of
emitting an empty/meaningless C file.

JSON source shape (see ``src/data/ch2_eventscripts.json``)::

    {
      "$schema": "fe8.eventscripts.v1",
      "scripts": [
        {
          "owner": "turn_based",
          "symbol": "EventScr_Ch2_Turn1Player",
          "kind": "event_list_scr",
          "header": "include/eventcall.h"
        },
        ...
      ]
    }
"""

from __future__ import annotations

import os

from ..diagnostics import GeneratedDataError
from ..escape_hatch import CSymbolRefField
from ..json_loader import load_json_file
from ..schema import DependencyGraph, TableSchema
from ..validators import validate_unique

SCHEMA_NAME = "eventscripts"
SCHEMA_VERSION = 1
SCHEMA_ID = "fe8.eventscripts.v1"

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))

# The `struct ChapterEventGroup` field/category each script belongs to
# (see `src/events/ch2-eventinfo.h`'s `Ch2Events` for the authoritative
# grouping); `world_map` scripts aren't part of `ChapterEventGroup` at all
# (they're wired from `src/events/ch2-wm.h`/worldmap navigation instead).
VALID_OWNERS = frozenset(
    {
        "turn_based",
        "character_based",
        "location_based",
        "misc_based",
        "tutorial",
        "beginning_scene",
        "world_map",
    }
)

# `EventListScr` (chapter turn/character/location/misc/tutorial/beginning
# events) vs. `EventScr` (raw event-script bytecode arrays, used directly
# by world-map scripts). Both are `extern CONST_DATA <Type> NAME[];` in
# `include/eventcall.h`, just with a different element type.
VALID_KINDS = frozenset({"event_list_scr", "event_scr_wm"})


class EventScriptRecord:
    def __init__(self, owner, owner_loc, symbol, symbol_loc, kind, kind_loc, header, header_loc, loc):
        self.owner = owner
        self.owner_loc = owner_loc
        self.symbol = symbol
        self.symbol_loc = symbol_loc
        self.kind = kind
        self.kind_loc = kind_loc
        self.header = header
        self.header_loc = header_loc
        self.loc = loc


def load_records(source_path):
    root = load_json_file(source_path)
    schema_node = root.require("$schema")
    if schema_node.as_str() != SCHEMA_ID:
        raise GeneratedDataError(
            "unexpected $schema '{}', expected '{}'".format(schema_node.as_str(), SCHEMA_ID),
            schema_node.loc,
        )
    scripts_node = root.require("scripts")
    records = []
    for script_node in scripts_node.as_list():
        owner_node = script_node.require("owner")
        symbol_node = script_node.require("symbol")
        kind_node = script_node.require("kind")
        header_node = script_node.require("header")
        records.append(
            EventScriptRecord(
                owner=owner_node.as_str(), owner_loc=owner_node.loc,
                symbol=symbol_node.as_str(), symbol_loc=symbol_node.loc,
                kind=kind_node.as_str(), kind_loc=kind_node.loc,
                header=header_node.as_str(), header_loc=header_node.loc,
                loc=script_node.loc,
            )
        )
    return records


def validate(records, diagnostics, repo_root=REPO_ROOT):
    """Validate owner/kind enums, unique symbols, and that every symbol is
    genuinely declared (``extern``) in its referenced header -- using
    :class:`CSymbolRefField` (this table's escape-hatch consumer)."""
    diagnostics.extend(
        validate_unique(
            ((r.symbol, r.symbol_loc) for r in records),
            "duplicate event-script symbol '{key}' (first defined at {first_loc})",
            "scripts[symbol={key}]",
        )
    )

    header_fields = {}
    for record in records:
        ref_prefix = "scripts[symbol={}]".format(record.symbol)

        if record.owner not in VALID_OWNERS:
            diagnostics.add(
                GeneratedDataError(
                    "unknown owner '{}', expected one of {}".format(record.owner, sorted(VALID_OWNERS)),
                    record.owner_loc,
                    "{}.owner".format(ref_prefix),
                )
            )
        if record.kind not in VALID_KINDS:
            diagnostics.add(
                GeneratedDataError(
                    "unknown kind '{}', expected one of {}".format(record.kind, sorted(VALID_KINDS)),
                    record.kind_loc,
                    "{}.kind".format(ref_prefix),
                )
            )

        header_path = record.header
        if not os.path.isabs(header_path):
            header_path = os.path.join(repo_root, header_path)
        if not os.path.exists(header_path):
            diagnostics.add(
                GeneratedDataError(
                    "header '{}' does not exist".format(record.header),
                    record.header_loc,
                    "{}.header".format(ref_prefix),
                )
            )
            continue

        field = header_fields.setdefault(header_path, CSymbolRefField(header_path, description="event script"))
        diagnostics.extend(field.validate(record.symbol, record.symbol_loc, "{}.symbol".format(ref_prefix)))


class EventScriptsTableSchema(TableSchema):
    name = SCHEMA_NAME
    version = SCHEMA_VERSION

    default_source = "src/data/ch2_eventscripts.json"
    # Metadata-only: no hand-written C file to round-trip against, no C
    # output to generate. `cli.py` explicitly skips both instead of
    # emitting anything meaningless.
    default_hand_source = None
    default_output_name = None
    default_inventory_path = "reports/generated_data_eventscripts_inventory.md"

    def dependencies(self):
        return ("eventcall.EventListScr", "eventcall.EventScr")

    def load_records(self, source_path):
        return load_records(source_path)

    def validate(self, records, diagnostics):
        validate(records, diagnostics)

    def build_inventory(self, records):
        from . import inventory as eventscripts_inventory
        return eventscripts_inventory.build_inventory(records)


def dependency_graph():
    graph = DependencyGraph()
    for dep in EventScriptsTableSchema().dependencies():
        graph.add_dependency(SCHEMA_NAME, dep)
    return graph
