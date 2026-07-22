"""``EventListScr_Ch2_*`` / ``Ch2Events`` schema: the Chapter 2 event-list
composition vertical slice (Issue #5 Batch B).

Unlike Batch A's tables, ``EventListScr_Ch2_*`` arrays are not lists of
plain values or designated-initializer structs -- they are ordered
sequences of **event-list macro calls** (``TURN(...)``, ``CHAR(...)``,
``Village(...)``, ``Armory(...)``, ``DefeatAll(...)``,
``CauseGameOverIfLordDies``, terminated by ``END_MAIN``; see
``include/EAstdlib.h`` and ``include/EA_Standard_Library/Main_Code_Helpers.h``
for the macro definitions and ``src/events/ch2-eventinfo.h`` for the hand
source this batch round-trips against).

This schema represents each macro call **structurally** -- a macro name
plus its ordered, typed arguments (``MACRO_SPECS`` below) -- and never
expands/reimplements the macro bodies themselves (no event bytecode is
generated; the generator emits the identical C macro-call text, see
``generate.py``).

JSON source shape (see ``src/data/ch2_eventlists.json``)::

    {
      "$schema": "fe8.eventlists.v1",
      "lists": [
        {
          "field": "turnBasedEvents",
          "symbol": "EventListScr_Ch2_Turn",
          "entries": [
            { "macro": "TURN", "args": [0, "EventScr_Ch2_Turn1Player", 1, 0, "FACTION_ID_BLUE"] },
            ...
          ]
        },
        ...
      ],
      "tutorial": {
        "field": "tutorialEvents",
        "symbol": "EventListScr_Ch2_Tutorial",
        "entries": ["EventScr_Ch2Tutorial1", ..., "EventScr_Ch2Tutorial30"]
      },
      "manifest": {
        "symbol": "Ch2Events",
        "fields": { "turnBasedEvents": "EventListScr_Ch2_Turn", ..., "playerUnitsChoice1InEncounter": null, ... }
      }
    }

A macro argument is either a plain JSON integer (a bare C integer
literal), a plain JSON string (a bare C symbol/token), or a nested
``{"macro": NAME, "args": [...]}`` object (a nested macro call, e.g.
``EVFLAG_TMP(7)`` for the free chapter-scoped temp-flag range documented
in ``include/constants/event-flags.h``) -- this is what "typed ordered
args" means: every argument keeps its shape (int/symbol/nested call)
instead of collapsing to an opaque string.

The ``END_MAIN`` list terminator and the tutorial array's ``NULL``
pointer terminator are never stored explicitly in the JSON -- both are
auto-appended at generation time, exactly like the ``ITEM_NONE``/
``TRAP_NONE`` convention in the ``shops``/``traps`` tables.

Cross-table validation (the reason this table declares
``dependency_tables()``) resolves every reference against the *other*
Batch A Chapter 2 tables' actual JSON records (not just "is this
declared somewhere in a header", which would trivially accept any
symbol from any chapter): ``units``/``shops``/``traps`` group/list/array
symbols, and ``eventscripts`` leaf symbols (further constrained by the
matching ``owner``/``kind`` for the list they're used from).
"""

from __future__ import annotations

import os
import re

from ..diagnostics import GeneratedDataError
from ..json_loader import load_json_file
from ..schema import DependencyGraph, TableSchema
from .. import character_refs
from ..validators import extract_enum_constants, validate_range, validate_reference, validate_unique

SCHEMA_NAME = "eventlists"
SCHEMA_VERSION = 1
SCHEMA_ID = "fe8.eventlists.v1"

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
CHARACTERS_HEADER = character_refs.CHARACTERS_HEADER
BMUNIT_HEADER = os.path.join(REPO_ROOT, "include", "bmunit.h")
EVENT_FLAGS_HEADER = os.path.join(REPO_ROOT, "include", "constants", "event-flags.h")

_U8_MIN, _U8_MAX = 0, 255

END_MAIN = "END_MAIN"
NULL_TOKEN = "NULL"
TUTORIAL_COUNT = 30

# The 7 `EventListScr_Ch2_*` array fields this batch covers (matching
# `struct ChapterEventGroup`'s event-list members 1:1; `tutorialEvents`
# is handled separately below since it's a `EventListScr *[]` pointer
# array, not a macro-call array).
LIST_FIELDS = (
    "turnBasedEvents",
    "characterBasedEvents",
    "locationBasedEvents",
    "miscBasedEvents",
    "specialEventsWhenUnitSelected",
    "specialEventsWhenDestSelected",
    "specialEventsAfterUnitMoved",
)

TUTORIAL_FIELD = "tutorialEvents"

# Which macro calls are structurally valid in which list (per Ch2's own
# hand source -- select-unit/select-destination/unit-move stay empty for
# this vertical slice, so no macros are allowed there yet).
ALLOWED_MACROS_BY_FIELD = {
    "turnBasedEvents": frozenset({"TURN"}),
    "characterBasedEvents": frozenset({"CHAR"}),
    "locationBasedEvents": frozenset({"Village", "Armory"}),
    "miscBasedEvents": frozenset({"DefeatAll", "CauseGameOverIfLordDies"}),
    "specialEventsWhenUnitSelected": frozenset(),
    "specialEventsWhenDestSelected": frozenset(),
    "specialEventsAfterUnitMoved": frozenset(),
}

# The `eventscripts` table `owner` every `event_scr`-kind argument in a
# given list must have (see `src/data/ch2_eventscripts.json`). Lists with
# no `event_scr` argument (or that carry no owner constraint) are absent.
EVENT_SCR_OWNER_BY_FIELD = {
    "turnBasedEvents": "turn_based",
    "characterBasedEvents": "character_based",
    "locationBasedEvents": "location_based",
    "miscBasedEvents": "misc_based",
}

# macro name -> ordered tuple of (arg_name, kind). `kind` is one of:
#   "flag"        -- 0 / EVFLAG_* / EVFLAG_TMP(7..40)
#   "event_scr"   -- an `eventscripts` table symbol (owner checked per-list)
#   "character"   -- a CHARACTER_* constant
#   "faction"     -- a FACTION_ID_* constant
#   "shop_symbol" -- a `shops` table symbol
#   "int"         -- a plain u8 integer literal
# A macro with an empty arg tuple is rendered *bare* (no parens), matching
# `CauseGameOverIfLordDies`'s object-like-macro convention.
MACRO_SPECS = {
    "TURN": (
        ("ent_flag", "flag"),
        ("scr", "event_scr"),
        ("turn", "int"),
        ("turn_max", "int"),
        ("faction", "faction"),
    ),
    "CHAR": (
        ("eid", "flag"),
        ("scr", "event_scr"),
        ("pid1", "character"),
        ("pid2", "character"),
    ),
    "Village": (
        ("eid", "flag"),
        ("scr", "event_scr"),
        ("x", "int"),
        ("y", "int"),
    ),
    "Armory": (
        ("list", "shop_symbol"),
        ("x", "int"),
        ("y", "int"),
    ),
    "DefeatAll": (
        ("event_scr", "event_scr"),
    ),
    "CauseGameOverIfLordDies": (),
}

# `struct ChapterEventGroup` field order (see `include/chapterdata.h`) and
# how each field's value should be cross-checked. "list_ref"/"tutorial_ref"
# must be non-NULL and match the corresponding `lists`/`tutorial` symbol
# exactly; the rest may be NULL (this Batch B's Ch2 data never leaves
# traps/units/beginning/ending NULL, but the schema stays general).
MANIFEST_FIELD_SPECS = (
    ("turnBasedEvents", "list_ref"),
    ("characterBasedEvents", "list_ref"),
    ("locationBasedEvents", "list_ref"),
    ("miscBasedEvents", "list_ref"),
    ("specialEventsWhenUnitSelected", "list_ref"),
    ("specialEventsWhenDestSelected", "list_ref"),
    ("specialEventsAfterUnitMoved", "list_ref"),
    ("tutorialEvents", "tutorial_ref"),
    ("traps", "trap_ref"),
    ("extraTrapsInHard", "trap_ref"),
    ("playerUnitsInNormal", "unit_ref"),
    ("playerUnitsInHard", "unit_ref"),
    ("playerUnitsChoice1InEncounter", "unit_ref"),
    ("playerUnitsChoice2InEncounter", "unit_ref"),
    ("playerUnitsChoice3InEncounter", "unit_ref"),
    ("enemyUnitsChoice1InEncounter", "unit_ref"),
    ("enemyUnitsChoice2InEncounter", "unit_ref"),
    ("enemyUnitsChoice3InEncounter", "unit_ref"),
    ("beginningSceneEvents", "beginning_scene_ref"),
    ("endingSceneEvents", "ending_scene_ref"),
)
MANIFEST_FIELD_NAMES = tuple(name for name, _ in MANIFEST_FIELD_SPECS)

_EVFLAG_TMP_RANGE_RE = re.compile(r"Flag\s+(\d+)\s*-\s*(\d+)\s+is\s+free", re.I)


def read_evflag_tmp_range(header=EVENT_FLAGS_HEADER):
    """Read the documented ``EVFLAG_TMP`` free-flag range (``"Flag 7 - 40
    is free"``) live from ``include/constants/event-flags.h`` instead of
    hardcoding ``7``/``40`` here."""
    with open(header, "r", encoding="utf-8") as handle:
        text = handle.read()
    match = _EVFLAG_TMP_RANGE_RE.search(text)
    if not match:
        raise GeneratedDataError("could not find the 'Flag N - M is free' comment in {}".format(header))
    return int(match.group(1)), int(match.group(2))


class MacroArg:
    """One structural macro-call argument: ``kind`` is ``"int"``,
    ``"symbol"`` (a bare C token/reference), ``"call"`` (a nested
    :class:`MacroCall`, e.g. ``EVFLAG_TMP(7)``), or ``"null"``."""

    __slots__ = ("kind", "value", "loc")

    def __init__(self, kind, value, loc):
        self.kind = kind
        self.value = value
        self.loc = loc

    def as_tuple(self):
        if self.kind == "call":
            return ("call", self.value.macro, tuple(a.as_tuple() for a in self.value.args))
        return (self.kind, self.value)


class MacroCall:
    __slots__ = ("macro", "macro_loc", "args", "loc")

    def __init__(self, macro, macro_loc, args, loc):
        self.macro = macro
        self.macro_loc = macro_loc
        self.args = args
        self.loc = loc

    def as_tuple(self):
        return (self.macro, tuple(a.as_tuple() for a in self.args))


class EventList:
    __slots__ = ("field", "field_loc", "symbol", "symbol_loc", "entries", "loc")

    def __init__(self, field, field_loc, symbol, symbol_loc, entries, loc):
        self.field = field
        self.field_loc = field_loc
        self.symbol = symbol
        self.symbol_loc = symbol_loc
        self.entries = entries
        self.loc = loc


class TutorialList:
    __slots__ = ("field", "field_loc", "symbol", "symbol_loc", "entries", "entry_locs", "loc")

    def __init__(self, field, field_loc, symbol, symbol_loc, entries, entry_locs, loc):
        self.field = field
        self.field_loc = field_loc
        self.symbol = symbol
        self.symbol_loc = symbol_loc
        self.entries = entries
        self.entry_locs = entry_locs
        self.loc = loc


class ManifestField:
    __slots__ = ("value", "loc")

    def __init__(self, value, loc):
        self.value = value
        self.loc = loc


class Manifest:
    __slots__ = ("symbol", "symbol_loc", "fields", "loc")

    def __init__(self, symbol, symbol_loc, fields, loc):
        self.symbol = symbol
        self.symbol_loc = symbol_loc
        self.fields = fields
        self.loc = loc


class EventListsRecords:
    """The full parsed ``ch2_eventlists.json`` document: the 7 event
    lists, the tutorial pointer array, and the ``Ch2Events`` manifest."""

    def __init__(self, lists, tutorial, manifest, loc):
        self.lists = lists
        self.lists_by_field = {lst.field: lst for lst in lists}
        self.tutorial = tutorial
        self.manifest = manifest
        self.loc = loc

    def __len__(self):
        # 7 event lists + the tutorial array + the Ch2Events manifest.
        return len(self.lists) + 2


def _parse_arg_node(node):
    if node.is_object():
        macro_node = node.require("macro")
        args_node = node.get("args")
        args = [_parse_arg_node(n) for n in (args_node.as_list() if args_node is not None else [])]
        call = MacroCall(macro=macro_node.as_str(), macro_loc=macro_node.loc, args=args, loc=node.loc)
        return MacroArg("call", call, node.loc)
    if node.is_scalar():
        value = node.value
        if isinstance(value, bool):
            raise GeneratedDataError("boolean is not a valid macro argument", node.loc)
        if isinstance(value, int):
            return MacroArg("int", value, node.loc)
        if isinstance(value, str):
            return MacroArg("symbol", value, node.loc)
        if value is None:
            return MacroArg("null", None, node.loc)
        raise GeneratedDataError("unsupported macro argument literal", node.loc)
    raise GeneratedDataError("macro argument must be an int, string, or nested macro-call object", node.loc)


def _parse_macro_call(node):
    macro_node = node.require("macro")
    args_node = node.get("args")
    args = [_parse_arg_node(n) for n in (args_node.as_list() if args_node is not None else [])]
    return MacroCall(macro=macro_node.as_str(), macro_loc=macro_node.loc, args=args, loc=node.loc)


def load_records(source_path):
    root = load_json_file(source_path)
    schema_node = root.require("$schema")
    if schema_node.as_str() != SCHEMA_ID:
        raise GeneratedDataError(
            "unexpected $schema '{}', expected '{}'".format(schema_node.as_str(), SCHEMA_ID),
            schema_node.loc,
        )

    lists = []
    for list_node in root.require("lists").as_list():
        field_node = list_node.require("field")
        symbol_node = list_node.require("symbol")
        entries_node = list_node.require("entries")
        entries = [_parse_macro_call(n) for n in entries_node.as_list()]
        lists.append(
            EventList(
                field=field_node.as_str(), field_loc=field_node.loc,
                symbol=symbol_node.as_str(), symbol_loc=symbol_node.loc,
                entries=entries, loc=list_node.loc,
            )
        )

    tutorial_node = root.require("tutorial")
    tut_field_node = tutorial_node.require("field")
    tut_symbol_node = tutorial_node.require("symbol")
    tut_entries_node = tutorial_node.require("entries")
    tut_entry_nodes = tut_entries_node.as_list()
    tutorial = TutorialList(
        field=tut_field_node.as_str(), field_loc=tut_field_node.loc,
        symbol=tut_symbol_node.as_str(), symbol_loc=tut_symbol_node.loc,
        entries=[n.as_str() for n in tut_entry_nodes],
        entry_locs=[n.loc for n in tut_entry_nodes],
        loc=tutorial_node.loc,
    )

    manifest_node = root.require("manifest")
    manifest_symbol_node = manifest_node.require("symbol")
    fields_node = manifest_node.require("fields")
    fields = {}
    for key, value_node in fields_node.items():
        if value_node.is_scalar() and value_node.value is None:
            fields[key] = ManifestField(value=None, loc=value_node.loc)
        else:
            fields[key] = ManifestField(value=value_node.as_str(), loc=value_node.loc)
    manifest = Manifest(
        symbol=manifest_symbol_node.as_str(), symbol_loc=manifest_symbol_node.loc,
        fields=fields, loc=manifest_node.loc,
    )

    return EventListsRecords(lists=lists, tutorial=tutorial, manifest=manifest, loc=root.loc)


def _err(message, loc, ref):
    return GeneratedDataError(message, loc, ref)


def _validate_flag_arg(arg, evflags, low, high, ref):
    """Returns ``(errors, temp_flag_uses)`` where ``temp_flag_uses`` is a
    list of ``(n, loc)`` for every ``EVFLAG_TMP(n)`` use found (fed into
    the chapter-wide duplicate-temp-flag check by the caller)."""
    if arg.kind == "int":
        if arg.value != 0:
            return [
                _err(
                    "flag literal must be 0 (no flag) or a symbolic EVFLAG_*/EVFLAG_TMP(n) "
                    "reference, got {}".format(arg.value),
                    arg.loc, ref,
                )
            ], []
        return [], []
    if arg.kind == "symbol":
        return validate_reference(arg.value, evflags, arg.loc, ref, kind="event flag"), []
    if arg.kind == "call":
        call = arg.value
        if call.macro != "EVFLAG_TMP":
            return [_err("unsupported flag macro '{}' (expected EVFLAG_TMP)".format(call.macro), call.macro_loc, ref)], []
        if len(call.args) != 1 or call.args[0].kind != "int":
            return [_err("EVFLAG_TMP expects exactly one integer argument", arg.loc, ref)], []
        n = call.args[0].value
        if not (low <= n <= high):
            return [
                _err(
                    "EVFLAG_TMP({}) is out of the documented free temp-flag range [{}, {}]".format(n, low, high),
                    call.args[0].loc, ref,
                )
            ], []
        return [], [(n, call.args[0].loc)]
    return [_err("invalid flag argument", arg.loc, ref)], []


def _validate_event_scr_arg(arg, eventscripts_by_symbol, owner, ref):
    if arg.kind != "symbol":
        return [_err("expected an event-script symbol reference", arg.loc, ref)]
    record = eventscripts_by_symbol.get(arg.value)
    if record is None:
        return [
            _err(
                "undefined event-script reference '{}' (not found in the eventscripts table, "
                "src/data/ch2_eventscripts.json)".format(arg.value),
                arg.loc, ref,
            )
        ]
    errors = []
    if record.kind != "event_list_scr":
        errors.append(
            _err(
                "event-script '{}' has kind '{}', expected 'event_list_scr'".format(arg.value, record.kind),
                arg.loc, ref,
            )
        )
    if owner is not None and record.owner != owner:
        errors.append(
            _err(
                "event-script '{}' has owner '{}', expected '{}' for this list".format(
                    arg.value, record.owner, owner
                ),
                arg.loc, ref,
            )
        )
    return errors


def _validate_symbol_arg(arg, allowed, ref, kind_name):
    if arg.kind != "symbol":
        return [_err("expected a {} symbol reference".format(kind_name), arg.loc, ref)]
    return validate_reference(arg.value, allowed, arg.loc, ref, kind=kind_name)


def _validate_table_symbol_arg(arg, allowed, ref, table_name, source_path):
    if arg.kind != "symbol":
        return [_err("expected a {} table symbol reference".format(table_name), arg.loc, ref)]
    if arg.value not in allowed:
        return [
            _err(
                "undefined {} reference '{}' (not found in the {} table, {})".format(
                    table_name, arg.value, table_name, source_path
                ),
                arg.loc, ref,
            )
        ]
    return []


def _validate_int_arg(arg, ref):
    if arg.kind != "int":
        return [_err("expected an integer literal", arg.loc, ref)]
    return validate_range(arg.value, _U8_MIN, _U8_MAX, arg.loc, ref, field_name="value")


def validate(records, diagnostics, dependency_records=None, characters_header=CHARACTERS_HEADER):
    """Validate the 7 event lists, the tutorial pointer array, and the
    ``Ch2Events`` manifest, cross-referencing ``dependency_records``
    (``{"units": [...], "shops": [...], "traps": [...], "eventscripts": [...]}``,
    each the *other* table's own parsed records -- see
    ``dependency_tables()``/``cli.py``)."""
    dependency_records = dependency_records or {}
    unit_symbols = {g.symbol for g in dependency_records.get("units", ())}
    shop_symbols = {r.symbol for r in dependency_records.get("shops", ())}
    trap_symbols = {r.symbol for r in dependency_records.get("traps", ())}
    eventscripts_by_symbol = {r.symbol: r for r in dependency_records.get("eventscripts", ())}

    characters = character_refs.read_character_designators(characters_header)
    factions = extract_enum_constants(BMUNIT_HEADER, name_prefix="FACTION_ID_")
    evflags = extract_enum_constants(EVENT_FLAGS_HEADER, name_prefix="EVFLAG_")
    evflag_tmp_low, evflag_tmp_high = read_evflag_tmp_range()

    # -- 1. exactly the 7 known list fields, no duplicates/missing/extra --
    seen_fields = {}
    for lst in records.lists:
        ref_prefix = "lists[field={}]".format(lst.field)
        if lst.field not in LIST_FIELDS:
            diagnostics.add(
                _err(
                    "unknown event list field '{}', expected one of {}".format(lst.field, sorted(LIST_FIELDS)),
                    lst.field_loc, "{}.field".format(ref_prefix),
                )
            )
            continue
        if lst.field in seen_fields:
            diagnostics.add(
                _err(
                    "duplicate event list field '{}' (first defined at {})".format(
                        lst.field, seen_fields[lst.field].field_loc
                    ),
                    lst.field_loc, "{}.field".format(ref_prefix),
                )
            )
            continue
        seen_fields[lst.field] = lst
    for missing_field in sorted(set(LIST_FIELDS) - set(seen_fields)):
        diagnostics.add(
            _err(
                "missing required event list field '{}'".format(missing_field),
                records.loc, "lists[field={}]".format(missing_field),
            )
        )

    # -- 2. every list/tutorial symbol must be pairwise unique --
    diagnostics.extend(
        validate_unique(
            [(lst.symbol, lst.symbol_loc) for lst in records.lists]
            + [(records.tutorial.symbol, records.tutorial.symbol_loc)],
            "duplicate event-list symbol '{key}' (first defined at {first_loc})",
            "lists[symbol={key}]",
        )
    )

    temp_flag_uses = []
    location_coords = {}

    for lst in records.lists:
        ref_prefix = "lists[field={}]".format(lst.field)
        allowed_macros = ALLOWED_MACROS_BY_FIELD.get(lst.field, frozenset())
        owner = EVENT_SCR_OWNER_BY_FIELD.get(lst.field)

        for index, call in enumerate(lst.entries):
            entry_ref = "{}.entries[{}]".format(ref_prefix, index)

            if call.macro == END_MAIN:
                diagnostics.add(
                    _err(
                        "event list entries must not include the {} terminator explicitly "
                        "(it is auto-appended at generation)".format(END_MAIN),
                        call.loc, entry_ref,
                    )
                )
                continue
            if call.macro not in allowed_macros:
                diagnostics.add(
                    _err(
                        "macro '{}' is not allowed in list '{}' (allowed: {})".format(
                            call.macro, lst.field, sorted(allowed_macros) or "<none -- list must stay empty>"
                        ),
                        call.macro_loc, entry_ref,
                    )
                )
                continue
            spec = MACRO_SPECS.get(call.macro)
            if spec is None:
                diagnostics.add(
                    _err(
                        "unsupported macro '{}' (this generator only structurally supports {})".format(
                            call.macro, sorted(MACRO_SPECS)
                        ),
                        call.macro_loc, entry_ref,
                    )
                )
                continue
            if len(call.args) != len(spec):
                diagnostics.add(
                    _err(
                        "macro '{}' expects {} argument(s), got {}".format(call.macro, len(spec), len(call.args)),
                        call.loc, entry_ref,
                    )
                )
                continue

            coord = {}
            for (arg_name, kind), arg in zip(spec, call.args):
                arg_ref = "{}.{}".format(entry_ref, arg_name)
                if kind == "flag":
                    errors, flag_uses = _validate_flag_arg(arg, evflags, evflag_tmp_low, evflag_tmp_high, arg_ref)
                    diagnostics.extend(errors)
                    temp_flag_uses.extend(flag_uses)
                elif kind == "event_scr":
                    diagnostics.extend(_validate_event_scr_arg(arg, eventscripts_by_symbol, owner, arg_ref))
                elif kind == "character":
                    diagnostics.extend(_validate_symbol_arg(arg, characters, arg_ref, "character"))
                elif kind == "faction":
                    diagnostics.extend(_validate_symbol_arg(arg, factions, arg_ref, "faction"))
                elif kind == "shop_symbol":
                    diagnostics.extend(
                        _validate_table_symbol_arg(arg, shop_symbols, arg_ref, "shops", "src/data/ch2_shops.json")
                    )
                elif kind == "int":
                    diagnostics.extend(_validate_int_arg(arg, arg_ref))
                    if arg_name in ("x", "y") and arg.kind == "int":
                        coord[arg_name] = arg.value
                else:  # pragma: no cover -- defensive, MACRO_SPECS is closed
                    raise AssertionError("unknown arg kind '{}'".format(kind))

            if lst.field == "locationBasedEvents" and "x" in coord and "y" in coord:
                key = (coord["x"], coord["y"])
                if key in location_coords:
                    diagnostics.add(
                        _err(
                            "duplicate location coordinate {} (first used at {})".format(
                                key, location_coords[key]
                            ),
                            call.loc, entry_ref,
                        )
                    )
                else:
                    location_coords[key] = call.loc

    diagnostics.extend(
        validate_unique(
            temp_flag_uses,
            "duplicate temporary event flag EVFLAG_TMP({key}) (first used at {first_loc})",
            "EVFLAG_TMP({key})",
        )
    )

    # -- 3. tutorial: exactly 30, unique, no explicit NULL, owner=tutorial --
    tutorial = records.tutorial
    if tutorial.field != TUTORIAL_FIELD:
        diagnostics.add(
            _err(
                "unexpected tutorial field '{}', expected '{}'".format(tutorial.field, TUTORIAL_FIELD),
                tutorial.field_loc, "tutorial.field",
            )
        )
    if len(tutorial.entries) != TUTORIAL_COUNT:
        diagnostics.add(
            _err(
                "tutorial event list must have exactly {} entries, got {}".format(
                    TUTORIAL_COUNT, len(tutorial.entries)
                ),
                tutorial.loc, "tutorial.entries",
            )
        )
    diagnostics.extend(
        validate_unique(
            zip(tutorial.entries, tutorial.entry_locs),
            "duplicate tutorial event-script symbol '{key}' (first defined at {first_loc})",
            "tutorial.entries[symbol={key}]",
        )
    )
    for index, (symbol, loc) in enumerate(zip(tutorial.entries, tutorial.entry_locs)):
        entry_ref = "tutorial.entries[{}]".format(index)
        if symbol == NULL_TOKEN:
            diagnostics.add(
                _err(
                    "tutorial entries must not include the {} terminator explicitly "
                    "(it is auto-appended at generation)".format(NULL_TOKEN),
                    loc, entry_ref,
                )
            )
            continue
        record = eventscripts_by_symbol.get(symbol)
        if record is None:
            diagnostics.add(
                _err(
                    "undefined event-script reference '{}' (not found in the eventscripts table, "
                    "src/data/ch2_eventscripts.json)".format(symbol),
                    loc, entry_ref,
                )
            )
            continue
        if record.kind != "event_list_scr":
            diagnostics.add(
                _err(
                    "event-script '{}' has kind '{}', expected 'event_list_scr'".format(symbol, record.kind),
                    loc, entry_ref,
                )
            )
        if record.owner != "tutorial":
            diagnostics.add(
                _err(
                    "event-script '{}' has owner '{}', expected 'tutorial'".format(symbol, record.owner),
                    loc, entry_ref,
                )
            )

    # -- 4. Ch2Events manifest: exactly the known fields, each resolved --
    manifest = records.manifest
    present = set(manifest.fields)
    for missing_field in sorted(set(MANIFEST_FIELD_NAMES) - present):
        diagnostics.add(
            _err(
                "manifest is missing required field '{}'".format(missing_field),
                manifest.loc, "manifest.fields[{}]".format(missing_field),
            )
        )
    for extra_field in sorted(present - set(MANIFEST_FIELD_NAMES)):
        diagnostics.add(
            _err(
                "manifest has unknown field '{}'".format(extra_field),
                manifest.fields[extra_field].loc, "manifest.fields[{}]".format(extra_field),
            )
        )

    for field_name, kind in MANIFEST_FIELD_SPECS:
        if field_name not in manifest.fields:
            continue
        mf = manifest.fields[field_name]
        ref = "manifest.fields[{}]".format(field_name)

        if kind == "list_ref":
            if mf.value is None:
                diagnostics.add(_err("manifest field '{}' must not be NULL".format(field_name), mf.loc, ref))
                continue
            declared = seen_fields.get(field_name)
            if declared is None:
                continue  # already reported as a missing `lists` entry
            if mf.value != declared.symbol:
                diagnostics.add(
                    _err(
                        "manifest field '{}' references '{}' but the declared list symbol is '{}'".format(
                            field_name, mf.value, declared.symbol
                        ),
                        mf.loc, ref,
                    )
                )
        elif kind == "tutorial_ref":
            if mf.value is None:
                diagnostics.add(_err("manifest field '{}' must not be NULL".format(field_name), mf.loc, ref))
            elif mf.value != tutorial.symbol:
                diagnostics.add(
                    _err(
                        "manifest field '{}' references '{}' but the declared tutorial symbol is '{}'".format(
                            field_name, mf.value, tutorial.symbol
                        ),
                        mf.loc, ref,
                    )
                )
        elif kind == "trap_ref":
            if mf.value is not None and mf.value not in trap_symbols:
                diagnostics.add(
                    _err(
                        "undefined trap reference '{}' (not found in the traps table, "
                        "src/data/ch2_traps.json)".format(mf.value),
                        mf.loc, ref,
                    )
                )
        elif kind == "unit_ref":
            if mf.value is not None and mf.value not in unit_symbols:
                diagnostics.add(
                    _err(
                        "undefined unit group reference '{}' (not found in the units table, "
                        "src/data/ch2_units.json)".format(mf.value),
                        mf.loc, ref,
                    )
                )
        elif kind == "beginning_scene_ref":
            if mf.value is not None:
                record = eventscripts_by_symbol.get(mf.value)
                if record is None:
                    diagnostics.add(
                        _err(
                            "undefined event-script reference '{}' (not found in the eventscripts "
                            "table, src/data/ch2_eventscripts.json)".format(mf.value),
                            mf.loc, ref,
                        )
                    )
                elif record.kind != "event_list_scr" or record.owner != "beginning_scene":
                    diagnostics.add(
                        _err(
                            "event-script '{}' has owner '{}'/kind '{}', expected owner "
                            "'beginning_scene'/kind 'event_list_scr'".format(mf.value, record.owner, record.kind),
                            mf.loc, ref,
                        )
                    )
        elif kind == "ending_scene_ref":
            if mf.value is not None:
                record = eventscripts_by_symbol.get(mf.value)
                if record is None:
                    diagnostics.add(
                        _err(
                            "undefined event-script reference '{}' (not found in the eventscripts "
                            "table, src/data/ch2_eventscripts.json)".format(mf.value),
                            mf.loc, ref,
                        )
                    )
                elif record.kind != "event_list_scr":
                    diagnostics.add(
                        _err(
                            "event-script '{}' has kind '{}', expected 'event_list_scr'".format(
                                mf.value, record.kind
                            ),
                            mf.loc, ref,
                        )
                    )
        else:  # pragma: no cover -- defensive, MANIFEST_FIELD_SPECS is closed
            raise AssertionError("unknown manifest field kind '{}'".format(kind))


class EventListsTableSchema(TableSchema):
    name = SCHEMA_NAME
    version = SCHEMA_VERSION

    default_source = "src/data/ch2_eventlists.json"
    default_hand_source = "src/events/ch2-eventinfo.h"
    default_output_name = "data_ch2_eventlists.c"
    default_inventory_path = "reports/generated_data_eventlists_inventory.md"

    def dependencies(self):
        return (
            "units", "shops", "traps", "eventscripts",
            "constants.characters", "bmunit.FACTION_ID", "constants.event-flags.EVFLAG_TMP",
        )

    def dependency_tables(self):
        # Loaded (in this order) via each table's own registered schema
        # and `default_source` (overridable per-table with
        # `--dep-source NAME=PATH`) -- see `cli.py`'s
        # `_load_dependency_records()`.
        return ("units", "shops", "traps", "eventscripts")

    def load_records(self, source_path):
        return load_records(source_path)

    def validate(self, records, diagnostics, dependency_records=None):
        validate(records, diagnostics, dependency_records)

    def generate_c(self, records, source_path):
        from . import generate as eventlists_generate
        return eventlists_generate.generate_c_source(records, source_path)

    def build_inventory(self, records):
        from . import inventory as eventlists_inventory
        return eventlists_inventory.build_inventory(records)

    def round_trip_errors(self, records, hand_source):
        if not hand_source or not os.path.exists(hand_source):
            return []
        from . import parser as eventlists_roundtrip
        hand_data = eventlists_roundtrip.parse_hand_written(hand_source, records)
        return eventlists_roundtrip.compare_records(records, hand_data, hand_path=hand_source)


def dependency_graph():
    graph = DependencyGraph()
    for dep in EventListsTableSchema().dependencies():
        graph.add_dependency(SCHEMA_NAME, dep)
    return graph
