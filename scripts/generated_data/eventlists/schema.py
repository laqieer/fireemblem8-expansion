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
from ..chapterobjectives.schema import stable_id_value
from ..autoplaystrategies import schema as autoplaystrategies_schema
from ..validators import extract_enum_constants, validate_range, validate_reference, validate_unique
from . import helper_specs

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
    "locationBasedEvents": frozenset({"Village", "Armory", "Vendor", "SecretShop", "AREA"}),
    "miscBasedEvents": frozenset({"DefeatAll", "CauseGameOverIfLordDies", "AFEV"}),
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
HELPER_SCRIPT_OWNERS = frozenset(EVENT_SCR_OWNER_BY_FIELD.values()) | {"tutorial"}

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
    "Vendor": (
        ("list", "shop_symbol"),
        ("x", "int"),
        ("y", "int"),
    ),
    "SecretShop": (
        ("list", "shop_symbol"),
        ("x", "int"),
        ("y", "int"),
    ),
    "DefeatAll": (
        ("event_scr", "event_scr"),
    ),
    "AFEV": (
        ("ent_flag", "flag"),
        ("scr", "event_scr"),
        ("trigger_flag", "flag"),
    ),
    "AREA": (
        ("ent_flag", "flag"),
        ("scr", "event_scr"),
        ("x1", "int"),
        ("y1", "int"),
        ("x2", "int"),
        ("y2", "int"),
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


class HelperCall:
    """One bounded structured helper operation before macro lowering."""

    __slots__ = ("family", "family_loc", "operation", "operation_loc", "args", "loc")

    def __init__(self, family, family_loc, operation, operation_loc, args, loc):
        self.family = family
        self.family_loc = family_loc
        self.operation = operation
        self.operation_loc = operation_loc
        self.args = args
        self.loc = loc

    def as_tuple(self):
        return (
            "helper",
            self.family,
            self.operation,
            tuple(a.as_tuple() for a in self.args),
        )


class HelperScript:
    __slots__ = ("owner", "owner_loc", "symbol", "symbol_loc", "entries", "loc")

    def __init__(self, owner, owner_loc, symbol, symbol_loc, entries, loc):
        self.owner = owner
        self.owner_loc = owner_loc
        self.symbol = symbol
        self.symbol_loc = symbol_loc
        self.entries = entries
        self.loc = loc


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

    def __init__(self, lists, tutorial, manifest, loc, helper_scripts=None):
        self.lists = lists
        self.lists_by_field = {lst.field: lst for lst in lists}
        self.tutorial = tutorial
        self.manifest = manifest
        self.loc = loc
        self.helper_scripts = list(helper_scripts or ())

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


def _parse_helper_call(node):
    helper_node = node.require("helper")
    operation_node = node.require("operation")
    args_node = node.get("args")
    args = [_parse_arg_node(n) for n in (args_node.as_list() if args_node is not None else [])]
    return HelperCall(
        family=helper_node.as_str(),
        family_loc=helper_node.loc,
        operation=operation_node.as_str(),
        operation_loc=operation_node.loc,
        args=args,
        loc=node.loc,
    )


def _parse_entry(node):
    if not node.is_object():
        raise GeneratedDataError("event-list entry must be an object", node.loc)
    if node.get("helper") is not None:
        return _parse_helper_call(node)
    return _parse_macro_call(node)


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
        entries = [_parse_entry(n) for n in entries_node.as_list()]
        lists.append(
            EventList(
                field=field_node.as_str(), field_loc=field_node.loc,
                symbol=symbol_node.as_str(), symbol_loc=symbol_node.loc,
                entries=entries, loc=list_node.loc,
            )
        )

    helper_scripts = []
    helper_scripts_node = root.get("helperScripts")
    if helper_scripts_node is not None:
        for script_node in helper_scripts_node.as_list():
            owner_node = script_node.get("owner")
            symbol_node = script_node.require("symbol")
            entries_node = script_node.require("entries")
            entries = [_parse_entry(n) for n in entries_node.as_list()]
            helper_scripts.append(
                HelperScript(
                    owner=owner_node.as_str() if owner_node is not None else None,
                    owner_loc=owner_node.loc if owner_node is not None else None,
                    symbol=symbol_node.as_str(),
                    symbol_loc=symbol_node.loc,
                    entries=entries,
                    loc=script_node.loc,
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

    return EventListsRecords(
        lists=lists,
        tutorial=tutorial,
        manifest=manifest,
        loc=root.loc,
        helper_scripts=helper_scripts,
    )


def _err(message, loc, ref):
    return GeneratedDataError(message, loc, ref)


def _validate_flag_arg(arg, evflags, low, high, ref, track_ownership=False):
    """Returns ``(errors, temp_flag_uses)`` where ``temp_flag_uses`` is a
    list of ``(n, loc)`` for each owned event-list allocation found (fed into
    the chapter-wide duplicate-temp-flag check by the caller). Operational
    ENUT/ENUF references are validated but are not allocations."""
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
        return [], [(n, call.args[0].loc)] if track_ownership else []
    return [_err("invalid flag argument", arg.loc, ref)], []


def _validate_event_scr_arg(arg, eventscripts_by_symbol, helper_scripts_by_symbol, owner, ref):
    if arg.kind != "symbol":
        return [_err("expected an event-script symbol reference", arg.loc, ref)]
    record = eventscripts_by_symbol.get(arg.value)
    if record is None:
        helper_script = helper_scripts_by_symbol.get(arg.value)
        if helper_script is not None:
            if owner is not None and helper_script.owner not in (None, owner):
                return [
                    _err(
                        "helper script '{}' has owner '{}', expected '{}' for this list".format(
                            arg.value, helper_script.owner, owner
                        ),
                        arg.loc,
                        ref,
                    )
                ]
            return []
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


def _lower_helper(call, context):
    """Lower one structured helper to its established macro call.

    The helper catalog is deliberately closed.  Returning diagnostics instead
    of raising keeps validation able to report every malformed helper in one
    pass.
    """
    spec = helper_specs.get_spec(context, call.family, call.operation)
    if spec is None:
        supported = helper_specs.supported_operations(context)
        return None, [
            _err(
                "unsupported {} helper '{}.{}'; supported helpers: {}".format(
                    context,
                    call.family,
                    call.operation,
                    ", ".join(
                        "{}.{}".format(family, operation)
                        for family, operations in supported.items()
                        for operation in operations
                    )
                    or "<none>",
                ),
                call.operation_loc,
                "{}.{}.{}".format(context, call.family, call.operation),
            )
        ]
    if len(call.args) != len(spec.args):
        return None, [
            _err(
                "helper '{}.{}' expects {} argument(s), got {}".format(
                    call.family, call.operation, len(spec.args), len(call.args)
                ),
                call.loc,
                "{}.{}.{}".format(context, call.family, call.operation),
            )
        ]
    args = list(call.args)
    if (
        context == "script"
        and call.family == "strategy"
        and call.operation in ("activate", "deactivate")
        and args[0].kind == "symbol"
    ):
        args[0] = MacroArg(
            "int",
            stable_id_value(args[0].value),
            args[0].loc,
        )
    return MacroCall(
        macro=spec.macro,
        macro_loc=call.operation_loc,
        args=args,
        loc=call.loc,
    ), []


def _validate_helper_script_arg(
    arg,
    kind,
    ref,
    evflags,
    evflag_tmp_low,
    evflag_tmp_high,
    characters,
    songs,
    unit_symbols,
):
    if kind == "flag":
        errors, _ = _validate_flag_arg(
            arg, evflags, evflag_tmp_low, evflag_tmp_high, ref
        )
        return errors
    if kind == "character":
        return _validate_symbol_arg(arg, characters, ref, "character")
    if kind == "song":
        return _validate_symbol_arg(arg, songs, ref, "song")
    if kind == "unit_symbol":
        return _validate_table_symbol_arg(
            arg, unit_symbols, ref, "unit group", "src/data/ch2_units.json"
        )
    if kind == "coord":
        if arg.kind != "int":
            return [_err("expected an integer literal", arg.loc, ref)]
        return validate_range(arg.value, _U8_MIN, _U8_MAX, arg.loc, ref, field_name="coordinate")
    if kind == "speed":
        if arg.kind != "int":
            return [_err("expected an integer literal", arg.loc, ref)]
        return validate_range(arg.value, 0, 0xF, arg.loc, ref, field_name="BGM fade speed")
    if kind == "u32":
        if arg.kind != "int":
            return [_err("expected an integer literal", arg.loc, ref)]
        return validate_range(arg.value, 0, 0xFFFFFFFF, arg.loc, ref, field_name="value")
    return [_err("unsupported helper argument kind '{}'".format(kind), arg.loc, ref)]


def _validate_helper_script(
    script,
    diagnostics,
    evflags,
    evflag_tmp_low,
    evflag_tmp_high,
    characters,
    songs,
    unit_symbols,
    strategy_ids,
    strategy_pairs,
):
    temp_flag_uses = []
    for index, entry in enumerate(script.entries):
        ref = "helperScripts[symbol={}].entries[{}]".format(script.symbol, index)
        if not isinstance(entry, HelperCall):
            diagnostics.add(
                _err(
                    "helper script entries must use structured helper objects "
                    "(raw macro '{}' is not allowed here)".format(entry.macro),
                    entry.loc,
                    ref,
                )
            )
            continue
        lowered, errors = _lower_helper(entry, "script")
        diagnostics.extend(errors)
        if lowered is None:
            continue
        spec = helper_specs.get_spec("script", entry.family, entry.operation)
        if (
            entry.family == "flag"
            and entry.operation in ("set", "clear")
            and entry.args
            and entry.args[0].kind == "symbol"
            and any(flag == entry.args[0].value for _strategy, flag in strategy_pairs)
        ):
            strategy_operation = (
                "activate" if entry.operation == "set" else "deactivate"
            )
            diagnostics.add(
                _err(
                    "flag.{} for strategy activation flag '{}' must use strategy.{}".format(
                        entry.operation,
                        entry.args[0].value,
                        strategy_operation,
                    ),
                    entry.args[0].loc,
                    ref,
                )
            )
        if (
            entry.family == "strategy"
            and entry.operation in ("activate", "deactivate")
        ):
            strategy_arg, flag_arg = entry.args
            diagnostics.extend(
                _validate_symbol_arg(
                    strategy_arg,
                    strategy_ids,
                    "{}.strategy".format(ref),
                    "strategy",
                )
            )
            errors, _flag_uses = _validate_flag_arg(
                flag_arg,
                evflags,
                evflag_tmp_low,
                evflag_tmp_high,
                "{}.flag".format(ref),
            )
            diagnostics.extend(errors)
            if (
                strategy_arg.kind == "symbol"
                and flag_arg.kind == "symbol"
                and (strategy_arg.value, flag_arg.value) not in strategy_pairs
            ):
                diagnostics.add(
                    _err(
                        "strategy activation pair '{}.{}' is not declared by autoplay strategy assignments".format(
                            strategy_arg.value,
                            flag_arg.value,
                        ),
                        entry.loc,
                        ref,
                    )
                )
            continue
        for (arg_name, kind), arg in zip(spec.args, entry.args):
            arg_ref = "{}.{}".format(ref, arg_name)
            if kind == "flag":
                errors, flag_uses = _validate_flag_arg(
                    arg,
                    evflags,
                    evflag_tmp_low,
                    evflag_tmp_high,
                    arg_ref,
                    track_ownership=False,
                )
                diagnostics.extend(errors)
                temp_flag_uses.extend(flag_uses)
            else:
                diagnostics.extend(
                    _validate_helper_script_arg(
                        arg,
                        kind,
                        arg_ref,
                        evflags,
                        evflag_tmp_low,
                        evflag_tmp_high,
                        characters,
                        songs,
                        unit_symbols,
                    )
                )
    return temp_flag_uses


def _chapter_bundle_records(dependency_records):
    bundles = dependency_records.get("chapterbundle")
    if bundles is None:
        return ()
    if hasattr(bundles, "records"):
        return tuple(bundles.records)
    if isinstance(bundles, (list, tuple)):
        return tuple(bundles)
    return (bundles,)


def _strategy_pairs_for_owner(
    records,
    strategy_records,
    chapters,
    dependency_records,
    diagnostics,
):
    if not chapters:
        return set()

    owners = [
        bundle
        for bundle in _chapter_bundle_records(dependency_records)
        if bundle.manifest.table == "eventlists"
        and bundle.manifest.symbol == records.manifest.symbol
    ]
    if len(owners) != 1:
        message = (
            "event-list manifest '{}' has no owning chapter bundle"
            if not owners
            else "event-list manifest '{}' has multiple owning chapter bundles"
        )
        diagnostics.add(
            _err(
                message.format(records.manifest.symbol),
                records.manifest.symbol_loc,
                "manifest.symbol",
            )
        )
        return set()

    owner_bundle = owners[0]
    owner = owner_bundle.autoplay_strategies
    if owner is None:
        diagnostics.add(
            _err(
                "event-list owner has no autoplayStrategies source declaration",
                records.manifest.symbol_loc,
                "dependencies.autoplaystrategies.source",
            )
        )
        return set()

    owner_source = (
        owner.source
        if os.path.isabs(owner.source)
        else os.path.join(owner_bundle.repository_root, owner.source)
    )
    try:
        owner_records = autoplaystrategies_schema.load_records(owner_source)
    except (OSError, GeneratedDataError) as error:
        diagnostics.add(
            _err(
                "could not load event-list owner autoplayStrategies source '{}': {}".format(
                    owner.source,
                    error,
                ),
                records.manifest.symbol_loc,
                "dependencies.autoplaystrategies.source",
            )
        )
        return set()

    selected_source_paths = set(strategy_records.get("source_paths", ()))
    owner_source_paths = set(owner_records.get("source_paths", ()))
    if selected_source_paths != owner_source_paths:
        diagnostics.add(
            _err(
                "selected autoplaystrategies dependency sources {} do not match "
                "event-list owner sources {}".format(
                    sorted(selected_source_paths),
                    sorted(owner_source_paths),
                ),
                records.manifest.symbol_loc,
                "dependencies.autoplaystrategies.source",
            )
        )
        return set()

    owner_chapter = owner_bundle.chapter.id
    strategy_pairs = set()
    for chapter, chapter_assignment, group_assignments, unit_assignments in chapters:
        if chapter.chapter != owner_chapter:
            continue
        if chapter.source_path not in owner_source_paths or chapter.symbol not in owner.symbols:
            diagnostics.add(
                _err(
                    "selected strategy assignment bundle '{}' is not declared by "
                    "the event-list owner's autoplayStrategies symbols".format(
                        chapter.symbol
                    ),
                    records.manifest.symbol_loc,
                    "dependencies.autoplaystrategies.symbols",
                )
            )
            continue
        assignments = [chapter_assignment]
        assignments.extend(group_assignments)
        assignments.extend(unit_assignments)
        for assignment in assignments:
            if assignment is not None and assignment.activation_flag is not None:
                strategy_pairs.add((assignment.strategy, assignment.activation_flag))
    return strategy_pairs


def _selected_strategy_dependency(records, dependency_records, diagnostics):
    strategy_records = dependency_records.get("autoplaystrategies")
    if strategy_records is None:
        diagnostics.add(
            _err(
                "missing required autoplaystrategies validation dependency",
                records.loc,
                "dependencies.autoplaystrategies",
            )
        )
        return [], []
    try:
        return autoplaystrategies_schema.selected_records(strategy_records)
    except (GeneratedDataError, AttributeError, KeyError, TypeError) as error:
        diagnostics.add(
            _err(
                "invalid autoplaystrategies validation dependency: {}".format(
                    error
                ),
                records.loc,
                "dependencies.autoplaystrategies",
            )
        )
        return [], []


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
    helper_scripts_by_symbol = {script.symbol: script for script in records.helper_scripts}
    strategy_records = dependency_records.get("autoplaystrategies", {})
    selected_strategies, selected_chapters = _selected_strategy_dependency(
        records,
        dependency_records,
        diagnostics,
    )
    strategy_ids = {strategy.id for strategy in selected_strategies}
    strategy_pairs = _strategy_pairs_for_owner(
        records,
        strategy_records,
        selected_chapters,
        dependency_records,
        diagnostics,
    )

    characters = character_refs.read_character_designators(characters_header)
    factions = extract_enum_constants(BMUNIT_HEADER, name_prefix="FACTION_ID_")
    evflags = extract_enum_constants(EVENT_FLAGS_HEADER, name_prefix="EVFLAG_")
    songs = extract_enum_constants(os.path.join(REPO_ROOT, "include", "constants", "songs.h"), name_prefix="SONG_")
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
            + [(records.tutorial.symbol, records.tutorial.symbol_loc)]
            + [(script.symbol, script.symbol_loc) for script in records.helper_scripts],
            "duplicate event-list symbol '{key}' (first defined at {first_loc})",
            "lists[symbol={key}]",
        )
    )

    temp_flag_uses = []
    for symbol, record in eventscripts_by_symbol.items():
        helper_script = helper_scripts_by_symbol.get(symbol)
        if helper_script is not None:
            diagnostics.add(
                _err(
                    "helper script symbol '{}' duplicates an eventscripts dependency "
                    "(first dependency record at {})".format(symbol, record.loc),
                    helper_script.symbol_loc,
                    "helperScripts[symbol={}].symbol".format(symbol),
                )
            )

    for script in records.helper_scripts:
        ref = "helperScripts[symbol={}]".format(script.symbol)
        if script.owner is not None and script.owner not in HELPER_SCRIPT_OWNERS:
            diagnostics.add(
                _err(
                    "unknown helper script owner '{}', expected one of {}".format(
                        script.owner, sorted(HELPER_SCRIPT_OWNERS)
                    ),
                    script.owner_loc,
                    "{}.owner".format(ref),
                )
            )
        if not re.match(r"^EventScr_[A-Za-z_][A-Za-z0-9_]*$", script.symbol):
            diagnostics.add(
                _err(
                    "helper script symbol '{}' must be a valid EventScr_ C identifier".format(script.symbol),
                    script.symbol_loc,
                    ref,
                )
            )
        temp_flag_uses.extend(
            _validate_helper_script(
                script,
                diagnostics,
                evflags,
                evflag_tmp_low,
                evflag_tmp_high,
                characters,
                songs,
                unit_symbols,
                strategy_ids,
                strategy_pairs,
            )
        )

    location_coords = {}

    for lst in records.lists:
        ref_prefix = "lists[field={}]".format(lst.field)
        allowed_macros = ALLOWED_MACROS_BY_FIELD.get(lst.field, frozenset())
        owner = EVENT_SCR_OWNER_BY_FIELD.get(lst.field)

        for index, call in enumerate(lst.entries):
            entry_ref = "{}.entries[{}]".format(ref_prefix, index)
            if isinstance(call, HelperCall):
                call, errors = _lower_helper(call, "list")
                diagnostics.extend(errors)
                if call is None:
                    continue

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
                    errors, flag_uses = _validate_flag_arg(
                        arg,
                        evflags,
                        evflag_tmp_low,
                        evflag_tmp_high,
                        arg_ref,
                        track_ownership=arg_name in ("ent_flag", "eid"),
                    )
                    diagnostics.extend(errors)
                    temp_flag_uses.extend(flag_uses)
                elif kind == "event_scr":
                    diagnostics.extend(
                        _validate_event_scr_arg(
                            arg, eventscripts_by_symbol, helper_scripts_by_symbol, owner, arg_ref
                        )
                    )
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
            helper_script = helper_scripts_by_symbol.get(symbol)
            if helper_script is not None:
                if helper_script.owner not in (None, "tutorial"):
                    diagnostics.add(
                        _err(
                            "helper script '{}' has owner '{}', expected 'tutorial' for this list".format(
                                symbol, helper_script.owner
                            ),
                            loc,
                            entry_ref,
                        )
                    )
                continue
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
            "units", "shops", "traps", "eventscripts", "constants.characters",
            "constants.songs", "bmunit.FACTION_ID",
            "constants.event-flags.EVFLAG_TMP",
        )

    def dependency_tables(self):
        # Loaded (in this order) via each table's own registered schema
        # and `default_source` (overridable per-table with
        # `--dep-source NAME=PATH`) -- see `cli.py`'s
        # `_load_dependency_records()`.
        return ("units", "shops", "traps", "eventscripts")

    def optional_dependency_tables(self):
        # Strategy records and their chapter owner validate `strategy.activate`
        # pairs, but cannot be manifest-DAG edges: chapterbundle already owns
        # this event-list manifest.
        return ("autoplaystrategies", "chapterbundle")

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
