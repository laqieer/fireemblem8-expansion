"""Schema for bounded generated autoplay strategy registries and assignments."""

from __future__ import annotations

import os
import re

from .. import character_refs
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
from ..chapterobjectives.schema import (
    BUNDLE_CAPACITY,
    KIND_TO_C,
    stable_id_value,
)

SCHEMA_NAME = "autoplaystrategies"
SCHEMA_VERSION = 1
SCHEMA_ID = "fe8.autoplaystrategies.v1"

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
CHAPTERS_HEADER = os.path.join(REPO_ROOT, "include", "constants", "chapters.h")
EVENT_FLAGS_HEADER = os.path.join(REPO_ROOT, "include", "constants", "event-flags.h")

STRATEGY_CAPACITY = 8
ASSIGNMENT_CAPACITY = 8

_ID_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")
_C_SYMBOL_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

OBJECTIVE_CAPABILITIES = tuple(KIND_TO_C)
ACTION_CAPABILITIES = ("combat", "objective_move")
REFERENCE_STRATEGIES = {
    "AUTOPLAY_STRATEGY_AGGRESSIVE": {
        "callback": "ExpansionAutoplayStrategy_Aggressive",
        "objectives": (
            "protect",
            "reach_area",
            "defeat_group",
            "event_flag",
            "hold_until_turn",
        ),
        "actions": ("combat",),
    },
    "AUTOPLAY_STRATEGY_OBJECTIVE_FIRST": {
        "callback": "ExpansionAutoplayStrategy_ObjectiveFirst",
        "objectives": ("reach_area", "hold_until_turn"),
        "actions": ("combat", "objective_move"),
    },
}


class Strategy:
    __slots__ = ("id", "id_loc", "callback", "callback_loc", "objectives", "objective_locs",
                 "actions", "action_locs", "loc")

    def __init__(self, id_, id_loc, callback, callback_loc, objectives, objective_locs,
                 actions, action_locs, loc):
        self.id = id_
        self.id_loc = id_loc
        self.callback = callback
        self.callback_loc = callback_loc
        self.objectives = objectives
        self.objective_locs = objective_locs
        self.actions = actions
        self.action_locs = action_locs
        self.loc = loc


class Assignment:
    __slots__ = ("strategy", "strategy_loc", "activation_flag", "activation_flag_loc", "loc")

    def __init__(self, strategy, strategy_loc, activation_flag, activation_flag_loc, loc):
        self.strategy = strategy
        self.strategy_loc = strategy_loc
        self.activation_flag = activation_flag
        self.activation_flag_loc = activation_flag_loc
        self.loc = loc


class GroupAssignment(Assignment):
    __slots__ = ("group", "group_loc")

    def __init__(self, group, group_loc, strategy, strategy_loc, activation_flag, activation_flag_loc, loc):
        Assignment.__init__(self, strategy, strategy_loc, activation_flag, activation_flag_loc, loc)
        self.group = group
        self.group_loc = group_loc


class UnitAssignment(Assignment):
    __slots__ = ("character", "character_loc")

    def __init__(self, character, character_loc, strategy, strategy_loc,
                 activation_flag, activation_flag_loc, loc):
        Assignment.__init__(self, strategy, strategy_loc, activation_flag, activation_flag_loc, loc)
        self.character = character
        self.character_loc = character_loc


class Chapter:
    __slots__ = ("chapter", "chapter_loc", "symbol", "symbol_loc", "chapter_assignment",
                 "group_assignments", "unit_assignments", "loc")

    def __init__(self, chapter, chapter_loc, symbol, symbol_loc, chapter_assignment,
                 group_assignments, unit_assignments, loc):
        self.chapter = chapter
        self.chapter_loc = chapter_loc
        self.symbol = symbol
        self.symbol_loc = symbol_loc
        self.chapter_assignment = chapter_assignment
        self.group_assignments = group_assignments
        self.unit_assignments = unit_assignments
        self.loc = loc


def _optional_string(node, key):
    value = node.get(key)
    return (value.as_str(), value.loc) if value is not None else (None, None)


def _parse_assignment(node):
    strategy = node.require("strategy")
    activation_flag, activation_flag_loc = _optional_string(node, "activationFlag")
    return Assignment(strategy.as_str(), strategy.loc, activation_flag, activation_flag_loc, node.loc)


def load_records(source_path):
    root = load_json_file(source_path)
    schema_node = root.require("$schema")
    if schema_node.as_str() != SCHEMA_ID:
        raise GeneratedDataError(
            "unexpected $schema '{}', expected '{}'".format(schema_node.as_str(), SCHEMA_ID),
            schema_node.loc,
        )

    strategies = []
    chapters = []

    for node in root.require("strategies").as_list():
        id_node = node.require("id")
        callback_node = node.require("callback")
        objectives = node.require("objectiveKinds").as_list()
        actions = node.require("actionKinds").as_list()
        strategies.append(
            Strategy(
                id_node.as_str(), id_node.loc, callback_node.as_str(), callback_node.loc,
                [item.as_str() for item in objectives], [item.loc for item in objectives],
                [item.as_str() for item in actions], [item.loc for item in actions], node.loc,
            )
        )

    for node in root.require("chapters").as_list():
        chapter_node = node.require("chapter")
        symbol_node = node.require("symbol")
        chapter_assignment_node = node.get("chapterAssignment")
        chapter_assignment = _parse_assignment(chapter_assignment_node) if chapter_assignment_node else None
        group_assignments = []
        unit_assignments = []

        for assignment_node in node.require("groupAssignments").as_list():
            group_node = assignment_node.require("group")
            assignment = _parse_assignment(assignment_node)
            group_assignments.append(
                GroupAssignment(
                    group_node.as_str(), group_node.loc, assignment.strategy, assignment.strategy_loc,
                    assignment.activation_flag, assignment.activation_flag_loc, assignment_node.loc,
                )
            )

        for assignment_node in node.require("unitAssignments").as_list():
            character_node = assignment_node.require("character")
            assignment = _parse_assignment(assignment_node)
            unit_assignments.append(
                UnitAssignment(
                    character_node.as_str(), character_node.loc, assignment.strategy, assignment.strategy_loc,
                    assignment.activation_flag, assignment.activation_flag_loc, assignment_node.loc,
                )
            )

        chapters.append(
            Chapter(
                chapter_node.as_str(), chapter_node.loc, symbol_node.as_str(), symbol_node.loc,
                chapter_assignment, group_assignments, unit_assignments, node.loc,
            )
        )

    return {"strategies": strategies, "chapters": chapters}


def _error(message, loc, ref):
    return GeneratedDataError(message, loc, ref)


def _validate_assignment(assignment, ref, strategies, event_flags, diagnostics):
    diagnostics.extend(
        validate_reference(
            assignment.strategy, strategies, assignment.strategy_loc, ref + ".strategy", kind="strategy"
        )
    )
    if assignment.activation_flag is not None:
        diagnostics.extend(
            validate_reference(
                assignment.activation_flag, event_flags, assignment.activation_flag_loc,
                ref + ".activationFlag", kind="event flag",
            )
        )
        if assignment.activation_flag == "EVFLAG_ALWAYS_FALSE":
            diagnostics.add(
                _error(
                    "activationFlag must not be EVFLAG_ALWAYS_FALSE",
                    assignment.activation_flag_loc,
                    ref + ".activationFlag",
                )
            )


def validate(records, diagnostics, dependency_records=None,
             chapters_header=CHAPTERS_HEADER, event_flags_header=EVENT_FLAGS_HEADER,
             characters_header=character_refs.CHARACTERS_HEADER):
    dependency_records = dependency_records or {}
    strategies = records["strategies"]
    chapters = records["chapters"]
    strategy_ids = {strategy.id: strategy for strategy in strategies}
    chapter_ids = extract_enum_constants(chapters_header, name_prefix="CHAPTER_")
    event_flags = extract_enum_constants(event_flags_header, name_prefix="EVFLAG_")
    characters = character_refs.read_character_designators(characters_header)
    objectives = {
        record.chapter: record for record in dependency_records.get("chapterobjectives", ())
    }
    chapter_bundle = dependency_records.get("chapterbundle")

    diagnostics.extend(
        validate_fixed_capacity(
            len(strategies), STRATEGY_CAPACITY, None, "strategies", what="strategy registry entries"
        )
    )
    diagnostics.extend(
        validate_fixed_capacity(
            len(chapters), BUNDLE_CAPACITY, None, "chapters", what="strategy assignment bundles"
        )
    )
    diagnostics.extend(
        validate_unique(
            ((strategy.id, strategy.id_loc) for strategy in strategies),
            "duplicate strategy ID '{key}' (first defined at {first_loc})",
            "strategies[id={key}]",
        )
    )
    diagnostics.extend(
        validate_unique(
            ((chapter.chapter, chapter.chapter_loc) for chapter in chapters),
            "duplicate strategy assignment bundle for '{key}' (first defined at {first_loc})",
            "chapters[chapter={key}]",
        )
    )
    diagnostics.extend(
        validate_unique(
            ((chapter.symbol, chapter.symbol_loc) for chapter in chapters),
            "duplicate strategy assignment bundle symbol '{key}' (first defined at {first_loc})",
            "chapters[symbol={key}]",
        )
    )

    hashes = {}
    for strategy in strategies:
        ref = "strategies[id={}]".format(strategy.id)
        if not _ID_RE.match(strategy.id):
            diagnostics.add(
                _error(
                    "strategy ID '{}' must use uppercase stable identifier spelling".format(strategy.id),
                    strategy.id_loc,
                    ref + ".id",
                )
            )
        if not _C_SYMBOL_RE.match(strategy.callback):
            diagnostics.add(
                _error(
                    "strategy callback '{}' must be a C identifier".format(strategy.callback),
                    strategy.callback_loc,
                    ref + ".callback",
                )
            )
        diagnostics.extend(
            validate_unique(
                zip(strategy.objectives, strategy.objective_locs),
                "duplicate objective capability '{key}' (first defined at {first_loc})",
                ref + ".objectiveKinds[{key}]",
            )
        )
        diagnostics.extend(
            validate_unique(
                zip(strategy.actions, strategy.action_locs),
                "duplicate action capability '{key}' (first defined at {first_loc})",
                ref + ".actionKinds[{key}]",
            )
        )
        for capability, loc in zip(strategy.objectives, strategy.objective_locs):
            if capability not in OBJECTIVE_CAPABILITIES:
                diagnostics.add(
                    _error(
                        "unknown objective capability '{}', expected one of {}".format(
                            capability, OBJECTIVE_CAPABILITIES
                        ),
                        loc,
                        ref + ".objectiveKinds",
                    )
                )
        for capability, loc in zip(strategy.actions, strategy.action_locs):
            if capability not in ACTION_CAPABILITIES:
                diagnostics.add(
                    _error(
                        "unknown action capability '{}', expected one of {}".format(
                            capability, ACTION_CAPABILITIES
                        ),
                        loc,
                        ref + ".actionKinds",
                    )
                )
        if not strategy.actions:
            diagnostics.add(_error("strategy requires at least one action capability", strategy.loc, ref))

        contract = REFERENCE_STRATEGIES.get(strategy.id)
        if contract is not None and (
            strategy.callback != contract["callback"]
            or tuple(strategy.objectives) != contract["objectives"]
            or tuple(strategy.actions) != contract["actions"]
        ):
            diagnostics.add(
                _error(
                    "reference strategy '{}' must retain its frozen callback and capabilities".format(
                        strategy.id
                    ),
                    strategy.loc,
                    ref,
                )
            )

        value = stable_id_value(strategy.id)
        if value in hashes and hashes[value] != strategy.id:
            diagnostics.add(
                _error(
                    "strategy ID '{}' collides with '{}' at runtime hash 0x{:08X}".format(
                        strategy.id, hashes[value], value
                    ),
                    strategy.id_loc,
                    ref + ".id",
                )
            )
        else:
            hashes[value] = strategy.id

    for chapter in chapters:
        ref = "chapters[symbol={}]".format(chapter.symbol)
        diagnostics.extend(
            validate_reference(chapter.chapter, chapter_ids, chapter.chapter_loc, ref + ".chapter", kind="chapter")
        )
        if not _C_SYMBOL_RE.match(chapter.symbol):
            diagnostics.add(
                _error(
                    "strategy assignment bundle symbol '{}' must be a C identifier".format(chapter.symbol),
                    chapter.symbol_loc,
                    ref + ".symbol",
                )
            )
        if chapter_bundle is None or chapter_bundle.chapter.id != chapter.chapter:
            diagnostics.add(
                _error(
                    "strategy assignment bundle '{}' for chapter '{}' has no owning chapter bundle".format(
                        chapter.symbol, chapter.chapter
                    ),
                    chapter.chapter_loc,
                    ref + ".chapter",
                )
            )
        elif chapter_bundle.autoplay_strategies is None:
            diagnostics.add(
                _error(
                    "strategy assignment bundle '{}' has no autoplayStrategies ownership declaration".format(
                        chapter.symbol
                    ),
                    chapter.symbol_loc,
                    ref + ".symbol",
                )
            )
        elif chapter.symbol not in chapter_bundle.autoplay_strategies.symbols:
            diagnostics.add(
                _error(
                    "strategy assignment bundle '{}' is not declared by its owning chapter bundle".format(
                        chapter.symbol
                    ),
                    chapter.symbol_loc,
                    ref + ".symbol",
                )
            )
        diagnostics.extend(
            validate_fixed_capacity(
                len(chapter.group_assignments), ASSIGNMENT_CAPACITY, chapter.loc,
                ref + ".groupAssignments", what="group strategy assignments",
            )
        )
        diagnostics.extend(
            validate_fixed_capacity(
                len(chapter.unit_assignments), ASSIGNMENT_CAPACITY, chapter.loc,
                ref + ".unitAssignments", what="unit strategy assignments",
            )
        )
        diagnostics.extend(
            validate_unique(
                ((assignment.group, assignment.group_loc) for assignment in chapter.group_assignments),
                "duplicate group assignment '{key}' (first defined at {first_loc})",
                ref + ".groupAssignments[group={key}]",
            )
        )
        diagnostics.extend(
            validate_unique(
                ((assignment.character, assignment.character_loc) for assignment in chapter.unit_assignments),
                "duplicate unit assignment '{key}' (first defined at {first_loc})",
                ref + ".unitAssignments[character={key}]",
            )
        )

        if chapter.chapter_assignment is not None:
            _validate_assignment(
                chapter.chapter_assignment, ref + ".chapterAssignment", strategy_ids, event_flags, diagnostics
            )

        objective_record = objectives.get(chapter.chapter)
        groups = {group.id: group for group in objective_record.groups} if objective_record else {}
        for assignment in chapter.group_assignments:
            assignment_ref = ref + ".groupAssignments[group={}]".format(assignment.group)
            diagnostics.extend(
                validate_reference(assignment.group, groups, assignment.group_loc, assignment_ref + ".group",
                                   kind="chapter AI group")
            )
            _validate_assignment(assignment, assignment_ref, strategy_ids, event_flags, diagnostics)

        for assignment in chapter.unit_assignments:
            assignment_ref = ref + ".unitAssignments[character={}]".format(assignment.character)
            diagnostics.extend(
                validate_reference(assignment.character, characters, assignment.character_loc,
                                   assignment_ref + ".character", kind="character")
            )
            _validate_assignment(assignment, assignment_ref, strategy_ids, event_flags, diagnostics)

    if chapter_bundle is not None and chapter_bundle.autoplay_strategies is not None:
        actual_symbols = {
            chapter.symbol for chapter in chapters if chapter.chapter == chapter_bundle.chapter.id
        }
        owner = chapter_bundle.autoplay_strategies
        diagnostics.extend(
            validate_unique(
                zip(owner.symbols, owner.symbol_locs),
                "duplicate symbol '{key}' declared in autoplayStrategies.symbols "
                "(first at {first_loc})",
                "autoplayStrategies.symbols[{key}]",
            )
        )
        for symbol, loc in zip(owner.symbols, owner.symbol_locs):
            if symbol not in actual_symbols:
                diagnostics.add(
                    _error(
                        "strategy assignment bundle '{}' is declared by chapter '{}' but absent from "
                        "its strategy source".format(symbol, chapter_bundle.chapter.id),
                        loc,
                        "autoplayStrategies.symbols[{}]".format(symbol),
                    )
                )


class AutoplayStrategiesTableSchema(TableSchema):
    name = SCHEMA_NAME
    version = SCHEMA_VERSION

    default_source = "src/data/autoplay_strategies.json"
    default_hand_source = None
    default_output_name = "data_autoplay_strategies.c"
    default_inventory_path = "reports/generated_data_autoplaystrategies_inventory.md"
    record_budget = STRATEGY_CAPACITY
    record_budget_reason = "the runtime registry has a fixed eight-strategy capacity"

    def dependencies(self):
        return (
            "constants.chapters",
            "constants.characters",
            "constants.event-flags",
            "chapterobjectives",
            "chapterbundle",
        )

    def dependency_tables(self):
        return ("chapterobjectives", "chapterbundle")

    def load_records(self, source_path):
        return load_records(source_path)

    def validate(self, records, diagnostics, dependency_records=None):
        validate(records, diagnostics, dependency_records)

    def generate_c(self, records, source_path):
        from . import generate
        return generate.generate_c_source(records, source_path)

    def build_inventory(self, records):
        from . import inventory
        return inventory.build_inventory(records)


def dependency_graph():
    graph = DependencyGraph()
    for dependency in AutoplayStrategiesTableSchema().dependencies():
        graph.add_dependency(SCHEMA_NAME, dependency)
    return graph
