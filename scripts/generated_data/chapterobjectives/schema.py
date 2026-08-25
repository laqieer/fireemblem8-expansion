"""Schema for bounded, typed chapter objectives and AI groups.

The table is deliberately declarative.  It owns objective/group membership
and lifecycle references, while chapter event scripts continue to set or
clear the existing ``EVFLAG_*`` values through the established typed helper
catalog.  No objective state is serialized: the runtime derives every status
from the current chapter, units, event flags, and turn counter.
"""

from __future__ import annotations

import glob
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

SCHEMA_NAME = "chapterobjectives"
SCHEMA_VERSION = 1
SCHEMA_ID = "fe8.chapterobjectives.v1"

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
CHAPTERS_HEADER = os.path.join(REPO_ROOT, "include", "constants", "chapters.h")
EVENT_FLAGS_HEADER = os.path.join(REPO_ROOT, "include", "constants", "event-flags.h")

BUNDLE_CAPACITY = 32
OBJECTIVE_CAPACITY = 8
GROUP_CAPACITY = 8
GROUP_MEMBER_CAPACITY = 16
TURN_MAX = 999

_ID_RE = re.compile(r"^[A-Z][A-Z0-9_]*\Z")
_C_SYMBOL_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*\Z")

KIND_TO_C = {
    "protect": "EXPANSION_CHAPTER_OBJECTIVE_PROTECT",
    "reach_area": "EXPANSION_CHAPTER_OBJECTIVE_REACH_AREA",
    "defeat_group": "EXPANSION_CHAPTER_OBJECTIVE_DEFEAT_GROUP",
    "event_flag": "EXPANSION_CHAPTER_OBJECTIVE_EVENT_FLAG",
    "hold_until_turn": "EXPANSION_CHAPTER_OBJECTIVE_HOLD_UNTIL_TURN",
}


def stable_id_value(identifier):
    """Return the stable FNV-1a value carried in runtime telemetry.

    The symbolic source ID remains the public authoring identity.  Hashing it
    gives the target a compact, pointer-free probe value; collisions are a
    validation error, never silently accepted.
    """
    value = 0x811C9DC5
    for byte in identifier.encode("ascii"):
        value ^= byte
        value = (value * 0x01000193) & 0xFFFFFFFF
    return value


class Area:
    __slots__ = ("x_min", "x_min_loc", "y_min", "y_min_loc", "x_max", "x_max_loc", "y_max", "y_max_loc", "loc")

    def __init__(self, x_min, x_min_loc, y_min, y_min_loc, x_max, x_max_loc, y_max, y_max_loc, loc):
        self.x_min = x_min
        self.x_min_loc = x_min_loc
        self.y_min = y_min
        self.y_min_loc = y_min_loc
        self.x_max = x_max
        self.x_max_loc = x_max_loc
        self.y_max = y_max
        self.y_max_loc = y_max_loc
        self.loc = loc


class AiGroupMember:
    __slots__ = ("character", "character_loc", "unit_group", "unit_group_loc", "loc")

    def __init__(self, character, character_loc, unit_group, unit_group_loc, loc):
        self.character = character
        self.character_loc = character_loc
        self.unit_group = unit_group
        self.unit_group_loc = unit_group_loc
        self.loc = loc


class AiGroup:
    __slots__ = ("id", "id_loc", "members", "loc")

    def __init__(self, id_, id_loc, members, loc):
        self.id = id_
        self.id_loc = id_loc
        self.members = members
        self.loc = loc


class Objective:
    __slots__ = (
        "id", "id_loc", "kind", "kind_loc", "activation_flag", "activation_flag_loc",
        "deactivation_flag", "deactivation_flag_loc", "group", "group_loc",
        "protected_character", "protected_character_loc", "completion_objective",
        "completion_objective_loc", "event_flag", "event_flag_loc", "until_turn",
        "until_turn_loc", "failure_flag", "failure_flag_loc", "completion_flag",
        "completion_flag_loc", "area", "loc",
    )

    def __init__(
        self, id_, id_loc, kind, kind_loc, activation_flag, activation_flag_loc,
        deactivation_flag, deactivation_flag_loc, group, group_loc,
        protected_character, protected_character_loc, completion_objective,
        completion_objective_loc, event_flag, event_flag_loc, until_turn,
        until_turn_loc, failure_flag, failure_flag_loc, completion_flag,
        completion_flag_loc, area, loc,
    ):
        self.id = id_
        self.id_loc = id_loc
        self.kind = kind
        self.kind_loc = kind_loc
        self.activation_flag = activation_flag
        self.activation_flag_loc = activation_flag_loc
        self.deactivation_flag = deactivation_flag
        self.deactivation_flag_loc = deactivation_flag_loc
        self.group = group
        self.group_loc = group_loc
        self.protected_character = protected_character
        self.protected_character_loc = protected_character_loc
        self.completion_objective = completion_objective
        self.completion_objective_loc = completion_objective_loc
        self.event_flag = event_flag
        self.event_flag_loc = event_flag_loc
        self.until_turn = until_turn
        self.until_turn_loc = until_turn_loc
        self.failure_flag = failure_flag
        self.failure_flag_loc = failure_flag_loc
        self.completion_flag = completion_flag
        self.completion_flag_loc = completion_flag_loc
        self.area = area
        self.loc = loc


class Dependencies:
    __slots__ = (
        "characters", "character_locs", "event_flags", "event_flag_locs",
        "unit_groups", "unit_group_locs", "loc",
    )

    def __init__(
        self, characters, character_locs, event_flags, event_flag_locs,
        unit_groups, unit_group_locs, loc,
    ):
        self.characters = characters
        self.character_locs = character_locs
        self.event_flags = event_flags
        self.event_flag_locs = event_flag_locs
        self.unit_groups = unit_groups
        self.unit_group_locs = unit_group_locs
        self.loc = loc


class ChapterObjectivesRecord:
    __slots__ = (
        "chapter", "chapter_loc", "symbol", "symbol_loc", "groups", "objectives",
        "dependencies", "source_path", "loc",
    )

    def __init__(
        self, chapter, chapter_loc, symbol, symbol_loc, groups, objectives,
        dependencies, source_path, loc,
    ):
        self.chapter = chapter
        self.chapter_loc = chapter_loc
        self.symbol = symbol
        self.symbol_loc = symbol_loc
        self.groups = groups
        self.objectives = objectives
        self.dependencies = dependencies
        self.source_path = source_path
        self.loc = loc


class ChapterObjectivesRecords(list):
    """List-like records retaining every canonical input path."""

    def __init__(self, records, source_paths):
        super().__init__(records)
        self.source_paths = tuple(source_paths)


def _optional_string(node, key):
    value = node.get(key)
    return (value.as_str(), value.loc) if value is not None else (None, None)


def _optional_int(node, key):
    value = node.get(key)
    return (value.as_int(), value.loc) if value is not None else (None, None)


def _parse_area(node):
    if node is None:
        return None
    x_min = node.require("xMin")
    y_min = node.require("yMin")
    x_max = node.require("xMax")
    y_max = node.require("yMax")
    return Area(
        x_min.as_int(), x_min.loc, y_min.as_int(), y_min.loc,
        x_max.as_int(), x_max.loc, y_max.as_int(), y_max.loc, node.loc,
    )


def _canonical_source_path(source_path):
    return os.path.normcase(os.path.realpath(os.path.abspath(source_path)))


def _load_records_file(source_path):
    root = load_json_file(source_path)
    schema_node = root.require("$schema")
    if schema_node.as_str() != SCHEMA_ID:
        raise GeneratedDataError(
            "unexpected $schema '{}', expected '{}'".format(schema_node.as_str(), SCHEMA_ID),
            schema_node.loc,
        )

    records = []
    for chapter_node in root.require("chapters").as_list():
        chapter = chapter_node.require("chapter")
        symbol = chapter_node.require("symbol")
        groups = []
        objectives = []

        for group_node in chapter_node.require("aiGroups").as_list():
            id_node = group_node.require("id")
            members = []
            for member_node in group_node.require("members").as_list():
                character = member_node.require("character")
                unit_group = member_node.require("unitGroup")
                members.append(
                    AiGroupMember(
                        character.as_str(), character.loc, unit_group.as_str(), unit_group.loc, member_node.loc
                    )
                )
            groups.append(AiGroup(id_node.as_str(), id_node.loc, members, group_node.loc))

        for objective_node in chapter_node.require("objectives").as_list():
            id_node = objective_node.require("id")
            kind_node = objective_node.require("kind")
            activation_flag, activation_flag_loc = _optional_string(objective_node, "activationFlag")
            deactivation_flag, deactivation_flag_loc = _optional_string(objective_node, "deactivationFlag")
            group, group_loc = _optional_string(objective_node, "group")
            protected_character, protected_character_loc = _optional_string(objective_node, "protectedCharacter")
            completion_objective, completion_objective_loc = _optional_string(
                objective_node, "completionObjective"
            )
            event_flag, event_flag_loc = _optional_string(objective_node, "eventFlag")
            until_turn, until_turn_loc = _optional_int(objective_node, "untilTurn")
            failure_flag, failure_flag_loc = _optional_string(objective_node, "failureFlag")
            completion_flag, completion_flag_loc = _optional_string(objective_node, "completionFlag")
            objectives.append(
                Objective(
                    id_node.as_str(), id_node.loc, kind_node.as_str(), kind_node.loc,
                    activation_flag, activation_flag_loc, deactivation_flag, deactivation_flag_loc,
                    group, group_loc, protected_character, protected_character_loc,
                    completion_objective, completion_objective_loc, event_flag, event_flag_loc,
                    until_turn, until_turn_loc, failure_flag, failure_flag_loc,
                    completion_flag, completion_flag_loc,
                    _parse_area(objective_node.get("area")), objective_node.loc,
                )
            )

        dependencies_node = chapter_node.require("dependencies")
        characters_node = dependencies_node.require("characters").as_list()
        flags_node = dependencies_node.require("eventFlags").as_list()
        units_node = dependencies_node.require("unitGroups").as_list()
        dependencies = Dependencies(
            [node.as_str() for node in characters_node], [node.loc for node in characters_node],
            [node.as_str() for node in flags_node], [node.loc for node in flags_node],
            [node.as_str() for node in units_node], [node.loc for node in units_node],
            dependencies_node.loc,
        )
        records.append(
            ChapterObjectivesRecord(
                chapter.as_str(), chapter.loc, symbol.as_str(), symbol.loc,
                groups, objectives, dependencies, _canonical_source_path(source_path), chapter_node.loc,
            )
        )
    return records


def load_records(source_path):
    """Load one objective file or every ``*_objectives.json`` file in a directory."""
    if os.path.isdir(source_path):
        source_paths = sorted(glob.glob(os.path.join(source_path, "*_objectives.json")))
        if not source_paths:
            raise GeneratedDataError(
                "chapter objectives directory '{}' has no *_objectives.json sources".format(source_path)
            )
    else:
        source_paths = [source_path]
    records = []
    for path in source_paths:
        records.extend(_load_records_file(path))
    return ChapterObjectivesRecords(records, [_canonical_source_path(path) for path in source_paths])


def _err(message, loc, ref):
    return GeneratedDataError(message, loc, ref)


def _validate_id(identifier, loc, ref, kind, diagnostics):
    if not _ID_RE.match(identifier):
        diagnostics.add(
            _err(
                "{} ID '{}' must use uppercase stable identifier spelling".format(kind, identifier),
                loc, ref,
            )
        )


def _validate_dependency_set(diagnostics, record, name, values, locations, allowed, used, kind):
    ref = "chapters[symbol={}].dependencies.{}".format(record.symbol, name)
    diagnostics.extend(
        validate_unique(
            zip(values, locations),
            "duplicate dependencies.{} entry '{{key}}' (first defined at {{first_loc}})".format(name),
            "{}[{{key}}]".format(ref),
        )
    )
    for value, loc in zip(values, locations):
        diagnostics.extend(validate_reference(value, allowed, loc, "{}[{}]".format(ref, value), kind=kind))
    for missing in sorted(used - set(values)):
        diagnostics.add(
            _err(
                "{} '{}' is used by this chapter objective bundle but is not declared".format(kind, missing),
                record.dependencies.loc, "{}[{}]".format(ref, missing),
            )
        )
    for extra in sorted(set(values) - used):
        diagnostics.add(
            _err(
                "dependencies.{} declares '{}' but this chapter objective bundle does not use it".format(
                    name, extra
                ),
                record.dependencies.loc, "{}[{}]".format(ref, extra),
            )
        )


def _validate_area(diagnostics, area, ref, map_dimensions):
    if area is None:
        return
    diagnostics.extend(validate_range(area.x_min, 0, 63, area.x_min_loc, ref + ".xMin", "xMin"))
    diagnostics.extend(validate_range(area.y_min, 0, 63, area.y_min_loc, ref + ".yMin", "yMin"))
    diagnostics.extend(validate_range(area.x_max, 0, 63, area.x_max_loc, ref + ".xMax", "xMax"))
    diagnostics.extend(validate_range(area.y_max, 0, 63, area.y_max_loc, ref + ".yMax", "yMax"))
    if area.x_min > area.x_max:
        diagnostics.add(_err("area.xMin must not exceed area.xMax", area.loc, ref + ".area"))
    if area.y_min > area.y_max:
        diagnostics.add(_err("area.yMin must not exceed area.yMax", area.loc, ref + ".area"))
    if map_dimensions is None:
        diagnostics.add(
            _err(
                "could not resolve the owning chapter map dimensions",
                area.loc, ref + ".area",
            )
        )
        return
    map_width, map_height = map_dimensions
    diagnostics.extend(
        validate_range(area.x_min, 0, map_width - 1, area.x_min_loc, ref + ".xMin", "xMin")
    )
    diagnostics.extend(
        validate_range(area.y_min, 0, map_height - 1, area.y_min_loc, ref + ".yMin", "yMin")
    )
    diagnostics.extend(
        validate_range(area.x_max, 0, map_width - 1, area.x_max_loc, ref + ".xMax", "xMax")
    )
    diagnostics.extend(
        validate_range(area.y_max, 0, map_height - 1, area.y_max_loc, ref + ".yMax", "yMax")
    )


def _validate_cycles(record, objectives_by_id, diagnostics):
    visiting = set()
    visited = set()

    def visit(identifier, trail):
        if identifier in visiting:
            objective = objectives_by_id[identifier]
            diagnostics.add(
                _err(
                    "protect completion dependency cycle: {}".format(" -> ".join(trail + [identifier])),
                    objective.id_loc, "chapters[symbol={}].objectives[id={}]".format(record.symbol, identifier),
                )
            )
            return
        if identifier in visited:
            return
        visited.add(identifier)
        objective = objectives_by_id[identifier]
        if objective.kind == "protect" and objective.completion_objective in objectives_by_id:
            visiting.add(identifier)
            visit(objective.completion_objective, trail + [identifier])
            visiting.remove(identifier)

    for identifier in sorted(objectives_by_id):
        visit(identifier, [])


def _validate_protect_flag_chains(record, objectives_by_id, diagnostics):
    adjacency = {identifier: set() for identifier in objectives_by_id}
    for objective in objectives_by_id.values():
        if objective.kind == "protect" and objective.completion_objective in objectives_by_id:
            adjacency[objective.id].add(objective.completion_objective)
            adjacency[objective.completion_objective].add(objective.id)

    seen = set()
    for identifier in sorted(adjacency):
        if identifier in seen:
            continue
        component = set()
        pending = [identifier]
        while pending:
            current = pending.pop()
            if current in component:
                continue
            component.add(current)
            pending.extend(adjacency[current] - component)
        seen.update(component)

        semantic_flags = []
        for target_id in sorted(component):
            target = objectives_by_id[target_id]
            if target.kind == "event_flag" and target.event_flag is not None:
                semantic_flags.append(
                    (target, target.event_flag, "eventFlag", "event completion")
                )
            if target.kind in ("protect", "hold_until_turn") and target.failure_flag is not None:
                semantic_flags.append(
                    (target, target.failure_flag, "failureFlag", "terminal failure")
                )
            if target.kind == "protect" and target.completion_flag is not None:
                semantic_flags.append(
                    (target, target.completion_flag, "completionFlag", "terminal success")
                )

        for source_id in sorted(component):
            source = objectives_by_id[source_id]
            if source.deactivation_flag is None:
                continue
            source_ref = "chapters[symbol={}].objectives[id={}]".format(record.symbol, source.id)
            for target, flag, field, outcome in semantic_flags:
                if source is target or source.deactivation_flag != flag:
                    continue
                diagnostics.add(
                    _err(
                        "deactivationFlag '{}' aliases objectives[id={}] {} and can suppress {}".format(
                            source.deactivation_flag, target.id, field, outcome
                        ),
                        source.deactivation_flag_loc, source_ref + ".deactivationFlag",
                    )
                )

    for objective in objectives_by_id.values():
        if objective.kind != "protect":
            continue
        parent_ref = "chapters[symbol={}].objectives[id={}]".format(record.symbol, objective.id)
        child_id = objective.completion_objective
        visited = set()
        while child_id in objectives_by_id and child_id not in visited:
            visited.add(child_id)
            child = objectives_by_id[child_id]
            child_ref = "objectives[id={}]".format(child.id)
            if child.kind in ("protect", "hold_until_turn") and child.failure_flag is not None:
                if objective.completion_flag == child.failure_flag:
                    diagnostics.add(
                        _err(
                            "protect completionFlag '{}' aliases {} failureFlag and can convert failure to success".format(
                                objective.completion_flag, child_ref
                            ),
                            objective.completion_flag_loc, parent_ref + ".completionFlag",
                        )
                    )
            if child.kind == "protect" and child.completion_flag is not None:
                if objective.failure_flag == child.completion_flag:
                    diagnostics.add(
                        _err(
                            "protect failureFlag '{}' aliases {} completionFlag and can convert success to failure".format(
                                objective.failure_flag, child_ref
                            ),
                            objective.failure_flag_loc, parent_ref + ".failureFlag",
                        )
                    )
            if child.kind == "event_flag" and child.event_flag is not None:
                if objective.failure_flag == child.event_flag:
                    diagnostics.add(
                        _err(
                            "protect failureFlag '{}' aliases {} eventFlag and can convert event success to failure".format(
                                objective.failure_flag, child_ref
                            ),
                            objective.failure_flag_loc, parent_ref + ".failureFlag",
                        )
                    )
                if objective.completion_flag == child.event_flag:
                    diagnostics.add(
                        _err(
                            "protect completionFlag '{}' aliases {} eventFlag".format(
                                objective.completion_flag, child_ref
                            ),
                            objective.completion_flag_loc, parent_ref + ".completionFlag",
                        )
                    )
            if child.kind != "protect":
                break
            child_id = child.completion_objective


def _bundle_records(dependency_records):
    bundles = dependency_records.get("chapterbundle")
    if bundles is None:
        return ()
    if hasattr(bundles, "records"):
        return tuple(bundles.records)
    if isinstance(bundles, (list, tuple)):
        return tuple(bundles)
    return (bundles,)


def _owner_source_path(owner):
    source_path = owner.chapter_objectives.source
    if not os.path.isabs(source_path):
        source_path = os.path.join(REPO_ROOT, source_path)
    return _canonical_source_path(source_path)


def _owner_unit_groups(owner, diagnostics, record):
    from ..chapterbundle import schema as chapterbundle_schema

    dependencies = chapterbundle_schema.resolve_bundle_dependencies(owner, diagnostics)
    return {
        group.symbol: group for group in dependencies.get("units", ())
    }


def _owner_map_dimensions(owner, diagnostics, record):
    from ..chapterbundle import schema as chapterbundle_schema

    try:
        return chapterbundle_schema.read_chapter_map_dimensions(
            owner.chapter.chapter_settings_index
        )
    except GeneratedDataError as error:
        diagnostics.add(
            _err(
                str(error),
                record.chapter_loc,
                "bundles[chapter={}].map".format(record.chapter),
            )
        )
        return None


def validate(records, diagnostics, dependency_records=None,
             chapters_header=CHAPTERS_HEADER, event_flags_header=EVENT_FLAGS_HEADER,
             characters_header=character_refs.CHARACTERS_HEADER):
    dependency_records = dependency_records or {}
    chapters = extract_enum_constants(chapters_header, name_prefix="CHAPTER_")
    characters = character_refs.read_character_designators(characters_header)
    event_flags = extract_enum_constants(event_flags_header, name_prefix="EVFLAG_")
    fallback_unit_groups = {
        group.symbol: group for group in dependency_records.get("units", ())
    }

    capacity_loc = records[BUNDLE_CAPACITY].loc if len(records) > BUNDLE_CAPACITY else None
    capacity_ref = "chapters[{}]".format(BUNDLE_CAPACITY) if capacity_loc is not None else "chapters"
    diagnostics.extend(
        validate_fixed_capacity(
            len(records), BUNDLE_CAPACITY, capacity_loc, capacity_ref, what="chapter objective bundles"
        )
    )
    chapter_bundles = _bundle_records(dependency_records)
    owners_by_chapter = {}
    actual_symbols_by_source_chapter = {}
    for record in records:
        actual_symbols_by_source_chapter.setdefault(
            (record.source_path, record.chapter), set()
        ).add(record.symbol)
    for chapter_bundle in chapter_bundles:
        owners_by_chapter.setdefault(chapter_bundle.chapter.id, []).append(chapter_bundle)
        owner = chapter_bundle.chapter_objectives
        if owner is None:
            continue
        owner_source_path = _owner_source_path(chapter_bundle)
        actual_symbols = actual_symbols_by_source_chapter.get(
            (owner_source_path, chapter_bundle.chapter.id), set()
        )
        diagnostics.extend(
            validate_unique(
                zip(owner.symbols, owner.symbol_locs),
                "duplicate symbol '{key}' declared in chapterObjectives.symbols "
                "(first at {first_loc})",
                "chapterObjectives.symbols[{key}]",
            )
        )
        for symbol, loc in zip(owner.symbols, owner.symbol_locs):
            if symbol not in actual_symbols:
                diagnostics.add(
                    _err(
                        "chapter objective bundle '{}' is declared by chapter '{}' but absent from "
                        "its objective source".format(symbol, chapter_bundle.chapter.id),
                        loc, "chapterObjectives.symbols[{}]".format(symbol),
                    )
                )
    for chapter, owners in owners_by_chapter.items():
        if len(owners) < 2:
            continue
        first = owners[0]
        for duplicate in owners[1:]:
            diagnostics.add(
                _err(
                    "duplicate chapter bundle owner for '{}' (first defined at {})".format(
                        chapter, first.chapter.id_loc
                    ),
                    duplicate.chapter.id_loc, "bundles[chapter={}].chapter".format(chapter),
                )
            )

    diagnostics.extend(
        validate_unique(
            ((record.chapter, record.chapter_loc) for record in records),
            "duplicate chapter objective bundle for '{key}' (first defined at {first_loc})",
            "chapters[chapter={key}]",
        )
    )
    diagnostics.extend(
        validate_unique(
            ((record.symbol, record.symbol_loc) for record in records),
            "duplicate chapter objective bundle symbol '{key}' (first defined at {first_loc})",
            "chapters[symbol={key}]",
        )
    )

    all_ids = []
    for record in records:
        record_ref = "chapters[symbol={}]".format(record.symbol)
        unit_groups = fallback_unit_groups
        owned_unit_groups = set()
        owned_character_counts = {}
        map_dimensions = None
        owners = owners_by_chapter.get(record.chapter, ())
        if not owners:
            diagnostics.add(
                _err(
                    "chapter objective bundle '{}' for chapter '{}' has no owning chapter bundle".format(
                        record.symbol, record.chapter
                    ),
                    record.chapter_loc, record_ref + ".chapter",
                )
            )
        elif len(owners) != 1:
            diagnostics.add(
                _err(
                    "chapter objective bundle '{}' for chapter '{}' has duplicate owning chapter bundles".format(
                        record.symbol, record.chapter
                    ),
                    record.chapter_loc, record_ref + ".chapter",
                )
            )
        else:
            chapter_bundle = owners[0]
            unit_groups = _owner_unit_groups(chapter_bundle, diagnostics, record)
            map_dimensions = _owner_map_dimensions(chapter_bundle, diagnostics, record)
            objective_owner = chapter_bundle.chapter_objectives
            if objective_owner is None:
                diagnostics.add(
                    _err(
                        "chapter objective bundle '{}' is not declared by its owning chapter bundle".format(
                            record.symbol
                        ),
                        record.symbol_loc, record_ref + ".symbol",
                    )
                )
            elif _owner_source_path(chapter_bundle) != record.source_path:
                diagnostics.add(
                    _err(
                        "chapter objective bundle '{}' is declared by '{}' but loaded from '{}'".format(
                            record.symbol, objective_owner.source, record.source_path
                        ),
                        objective_owner.source_loc,
                        "bundles[chapter={}].chapterObjectives.source".format(record.chapter),
                    )
                )
            elif record.symbol not in objective_owner.symbols:
                diagnostics.add(
                    _err(
                        "chapter objective bundle '{}' is not declared by its owning chapter bundle".format(
                            record.symbol
                        ),
                        record.symbol_loc, record_ref + ".symbol",
                    )
                )
            unit_owner = chapter_bundle.tables_by_name.get("units")
            if unit_owner is not None:
                owned_unit_groups = set(unit_owner.symbols)
                for group_name in owned_unit_groups:
                    source_group = unit_groups.get(group_name)
                    if source_group is None:
                        continue
                    for unit in source_group.units:
                        if isinstance(unit.char_index, str):
                            owned_character_counts[unit.char_index] = (
                                owned_character_counts.get(unit.char_index, 0) + 1
                            )

        diagnostics.extend(validate_reference(record.chapter, chapters, record.chapter_loc,
                                               record_ref + ".chapter", kind="chapter"))
        if not _C_SYMBOL_RE.match(record.symbol):
            diagnostics.add(_err("bundle symbol '{}' must be a C identifier".format(record.symbol),
                                 record.symbol_loc, record_ref + ".symbol"))
        diagnostics.extend(
            validate_fixed_capacity(
                len(record.groups), GROUP_CAPACITY, record.loc, record_ref + ".aiGroups", what="AI groups"
            )
        )
        diagnostics.extend(
            validate_fixed_capacity(
                len(record.objectives), OBJECTIVE_CAPACITY, record.loc, record_ref + ".objectives",
                what="objectives",
            )
        )
        diagnostics.extend(
            validate_unique(
                ((group.id, group.id_loc) for group in record.groups),
                "duplicate AI group ID '{key}' (first defined at {first_loc})",
                record_ref + ".aiGroups[id={key}]",
            )
        )
        diagnostics.extend(
            validate_unique(
                ((objective.id, objective.id_loc) for objective in record.objectives),
                "duplicate objective ID '{key}' (first defined at {first_loc})",
                record_ref + ".objectives[id={key}]",
            )
        )

        groups_by_id = {group.id: group for group in record.groups}
        objectives_by_id = {objective.id: objective for objective in record.objectives}
        used_characters = set()
        used_flags = set()
        used_unit_groups = set()
        protected_character_groups = {}
        failure_flags = []
        completion_flags = []

        for group_name, group_loc in zip(
            record.dependencies.unit_groups, record.dependencies.unit_group_locs
        ):
            source_group = unit_groups.get(group_name)
            if source_group is None:
                continue
            if group_name not in owned_unit_groups:
                diagnostics.add(
                    _err(
                        "unit group '{}' is not owned by chapter '{}'".format(group_name, record.chapter),
                        group_loc, record_ref + ".dependencies.unitGroups[{}]".format(group_name),
                    )
                )
                continue
            for unit in source_group.units:
                if isinstance(unit.char_index, str):
                    protected_character_groups.setdefault(unit.char_index, set()).add(group_name)

        for group in record.groups:
            _validate_id(group.id, group.id_loc, record_ref + ".aiGroups[id={}].id".format(group.id),
                         "AI group", diagnostics)
            all_ids.append((group.id, group.id_loc, "AI group"))
            diagnostics.extend(
                validate_fixed_capacity(
                    len(group.members), GROUP_MEMBER_CAPACITY, group.loc,
                    record_ref + ".aiGroups[id={}].members".format(group.id), what="AI group members",
                )
            )
            if not group.members:
                diagnostics.add(
                    _err("AI group '{}' must contain at least one member".format(group.id),
                         group.loc, record_ref + ".aiGroups[id={}].members".format(group.id))
                )
            diagnostics.extend(
                validate_unique(
                    ((member.character, member.character_loc) for member in group.members),
                    "duplicate AI group member '{key}' (first defined at {first_loc})",
                    record_ref + ".aiGroups[id={}].members[character={{key}}]".format(group.id),
                )
            )
            for member in group.members:
                member_ref = record_ref + ".aiGroups[id={}].members[character={}]".format(
                    group.id, member.character
                )
                diagnostics.extend(
                    validate_reference(member.character, characters, member.character_loc,
                                       member_ref + ".character", kind="character")
                )
                if owned_character_counts.get(member.character, 0) != 1:
                    diagnostics.add(
                        _err(
                            "character '{}' resolves to {} unit definitions in the owning chapter data".format(
                                member.character, owned_character_counts.get(member.character, 0)
                            ),
                            member.character_loc, member_ref + ".character",
                        )
                    )
                if member.unit_group not in unit_groups:
                    diagnostics.add(
                        _err(
                            "undefined unit group reference '{}'".format(member.unit_group),
                            member.unit_group_loc, member_ref + ".unitGroup",
                        )
                    )
                elif member.unit_group not in owned_unit_groups:
                    diagnostics.add(
                        _err(
                            "unit group '{}' is not owned by chapter '{}'".format(
                                member.unit_group, record.chapter
                            ),
                            member.unit_group_loc, member_ref + ".unitGroup",
                        )
                    )
                else:
                    source_group = unit_groups[member.unit_group]
                    if member.character not in {
                        unit.char_index for unit in source_group.units if isinstance(unit.char_index, str)
                    }:
                        diagnostics.add(
                            _err(
                                "character '{}' is not a member of unit group '{}'".format(
                                    member.character, member.unit_group
                                ),
                                member.character_loc, member_ref + ".character",
                            )
                        )
                used_characters.add(member.character)
                used_unit_groups.add(member.unit_group)

        for objective in record.objectives:
            objective_ref = record_ref + ".objectives[id={}]".format(objective.id)
            _validate_id(objective.id, objective.id_loc, objective_ref + ".id", "objective", diagnostics)
            all_ids.append((objective.id, objective.id_loc, "objective"))
            if objective.kind not in KIND_TO_C:
                diagnostics.add(
                    _err("unknown objective kind '{}', expected one of {}".format(
                        objective.kind, sorted(KIND_TO_C)
                    ), objective.kind_loc, objective_ref + ".kind")
                )
                continue

            for flag, loc, field in (
                (objective.activation_flag, objective.activation_flag_loc, "activationFlag"),
                (objective.deactivation_flag, objective.deactivation_flag_loc, "deactivationFlag"),
                (objective.event_flag, objective.event_flag_loc, "eventFlag"),
                (objective.failure_flag, objective.failure_flag_loc, "failureFlag"),
                (objective.completion_flag, objective.completion_flag_loc, "completionFlag"),
            ):
                if flag is None:
                    continue
                diagnostics.extend(
                    validate_reference(flag, event_flags, loc, objective_ref + "." + field, kind="event flag")
                )
                used_flags.add(flag)

            if objective.activation_flag == "EVFLAG_ALWAYS_FALSE":
                diagnostics.add(
                    _err("activationFlag must not be EVFLAG_ALWAYS_FALSE", objective.activation_flag_loc,
                         objective_ref + ".activationFlag")
                )
            if objective.activation_flag and objective.activation_flag == objective.deactivation_flag:
                diagnostics.add(
                    _err("activationFlag and deactivationFlag are contradictory", objective.deactivation_flag_loc,
                         objective_ref + ".deactivationFlag")
                )
            if objective.event_flag == "EVFLAG_ALWAYS_FALSE":
                diagnostics.add(
                    _err("eventFlag must not be EVFLAG_ALWAYS_FALSE", objective.event_flag_loc,
                         objective_ref + ".eventFlag")
                )
            if objective.event_flag and objective.event_flag == objective.deactivation_flag:
                diagnostics.add(
                    _err("eventFlag and deactivationFlag are contradictory", objective.deactivation_flag_loc,
                         objective_ref + ".deactivationFlag")
                )
            if objective.failure_flag == "EVFLAG_ALWAYS_FALSE":
                diagnostics.add(
                    _err("failureFlag must not be EVFLAG_ALWAYS_FALSE", objective.failure_flag_loc,
                         objective_ref + ".failureFlag")
                )
            if objective.completion_flag == "EVFLAG_ALWAYS_FALSE":
                diagnostics.add(
                    _err("completionFlag must not be EVFLAG_ALWAYS_FALSE", objective.completion_flag_loc,
                         objective_ref + ".completionFlag")
                )
            if objective.failure_flag is not None and objective.failure_flag in (
                objective.activation_flag, objective.deactivation_flag, objective.event_flag,
                objective.completion_flag,
            ):
                diagnostics.add(
                    _err(
                        "failureFlag must be distinct from activationFlag, deactivationFlag, eventFlag, and completionFlag",
                        objective.failure_flag_loc, objective_ref + ".failureFlag",
                    )
                )
            if objective.completion_flag is not None and objective.completion_flag in (
                objective.activation_flag, objective.deactivation_flag, objective.event_flag,
            ):
                diagnostics.add(
                    _err(
                        "completionFlag must be distinct from activationFlag, deactivationFlag, and eventFlag",
                        objective.completion_flag_loc, objective_ref + ".completionFlag",
                    )
                )
            if objective.kind in ("protect", "hold_until_turn") and objective.failure_flag is not None:
                failure_flags.append((objective.failure_flag, objective.failure_flag_loc))
            if objective.kind == "protect" and objective.completion_flag is not None:
                completion_flags.append((objective.completion_flag, objective.completion_flag_loc))

            if objective.group is not None:
                diagnostics.extend(
                    validate_reference(objective.group, groups_by_id, objective.group_loc,
                                       objective_ref + ".group", kind="AI group")
                )
            if objective.protected_character is not None:
                diagnostics.extend(
                    validate_reference(objective.protected_character, characters,
                                       objective.protected_character_loc,
                                       objective_ref + ".protectedCharacter", kind="character")
                )
                used_characters.add(objective.protected_character)
                if objective.protected_character == "CHARACTER_NONE":
                    diagnostics.add(
                        _err(
                            "protectedCharacter must not be CHARACTER_NONE",
                            objective.protected_character_loc, objective_ref + ".protectedCharacter",
                        )
                    )
                if objective.kind == "protect":
                    if owned_character_counts.get(objective.protected_character, 0) != 1:
                        diagnostics.add(
                            _err(
                                "protectedCharacter '{}' must resolve to exactly one unit definition in the owning chapter data".format(
                                    objective.protected_character
                                ),
                                objective.protected_character_loc,
                                objective_ref + ".protectedCharacter",
                            )
                        )
                    character_groups = protected_character_groups.get(objective.protected_character, set())
                    if not character_groups:
                        diagnostics.add(
                            _err(
                                "protectedCharacter '{}' must belong to a validated chapter unit group".format(
                                    objective.protected_character
                                ),
                                objective.protected_character_loc,
                                objective_ref + ".protectedCharacter",
                            )
                        )
                    used_unit_groups.update(character_groups)
            if objective.completion_objective is not None:
                diagnostics.extend(
                    validate_reference(objective.completion_objective, objectives_by_id,
                                       objective.completion_objective_loc,
                                       objective_ref + ".completionObjective", kind="objective")
                )
            _validate_area(diagnostics, objective.area, objective_ref, map_dimensions)

            if objective.kind == "protect":
                if objective.protected_character is None or objective.completion_objective is None \
                        or objective.failure_flag is None or objective.completion_flag is None:
                    diagnostics.add(
                        _err(
                            "protect objective requires protectedCharacter, completionObjective, failureFlag, and completionFlag",
                             objective.loc, objective_ref)
                    )
                if objective.group is not None or objective.area is not None or objective.event_flag is not None \
                        or objective.until_turn is not None:
                    diagnostics.add(
                        _err(
                            "protect objective accepts only protectedCharacter, completionObjective, failureFlag, and completionFlag",
                             objective.loc, objective_ref)
                    )
            elif objective.kind == "reach_area":
                if objective.group is None or objective.area is None:
                    diagnostics.add(_err("reach_area objective requires group and area",
                                         objective.loc, objective_ref))
                if objective.protected_character is not None or objective.completion_objective is not None \
                        or objective.event_flag is not None or objective.until_turn is not None \
                        or objective.failure_flag is not None or objective.completion_flag is not None:
                    diagnostics.add(
                        _err("reach_area objective accepts only group and area", objective.loc, objective_ref)
                    )
            elif objective.kind == "defeat_group":
                if objective.group is None:
                    diagnostics.add(_err("defeat_group objective requires group", objective.loc, objective_ref))
                if objective.protected_character is not None or objective.completion_objective is not None \
                        or objective.event_flag is not None or objective.until_turn is not None \
                        or objective.area is not None or objective.failure_flag is not None \
                        or objective.completion_flag is not None:
                    diagnostics.add(
                        _err("defeat_group objective accepts only group", objective.loc, objective_ref)
                    )
            elif objective.kind == "event_flag":
                if objective.event_flag is None:
                    diagnostics.add(_err("event_flag objective requires eventFlag", objective.loc, objective_ref))
                if objective.group is not None or objective.protected_character is not None \
                        or objective.completion_objective is not None or objective.area is not None \
                        or objective.until_turn is not None or objective.failure_flag is not None \
                        or objective.completion_flag is not None:
                    diagnostics.add(_err("event_flag objective accepts only eventFlag", objective.loc, objective_ref))
            elif objective.kind == "hold_until_turn":
                if objective.group is None or objective.area is None or objective.until_turn is None \
                        or objective.failure_flag is None:
                    diagnostics.add(_err("hold_until_turn objective requires group, area, untilTurn, and failureFlag",
                                         objective.loc, objective_ref))
                elif objective.until_turn is not None:
                    diagnostics.extend(
                        validate_range(objective.until_turn, 1, TURN_MAX, objective.until_turn_loc,
                                       objective_ref + ".untilTurn", field_name="untilTurn")
                    )
                if objective.protected_character is not None or objective.completion_objective is not None \
                        or objective.event_flag is not None or objective.completion_flag is not None:
                    diagnostics.add(
                        _err("hold_until_turn objective accepts only group, area, untilTurn, and failureFlag",
                             objective.loc, objective_ref)
                    )

            if objective.kind == "protect" and objective.protected_character is not None:
                completion = objectives_by_id.get(objective.completion_objective)
                completion_seen = set()
                while completion is not None and completion.kind == "protect":
                    if completion.id in completion_seen:
                        completion = None
                        break
                    completion_seen.add(completion.id)
                    completion = objectives_by_id.get(completion.completion_objective)
                completion_group = groups_by_id.get(completion.group) if completion is not None else None
                if completion is not None and completion.kind == "defeat_group" and completion_group is not None:
                    if objective.protected_character in {
                        member.character for member in completion_group.members
                    }:
                        diagnostics.add(
                            _err(
                                "protect completion chain reaches a defeat_group containing its protected character",
                                objective.completion_objective_loc, objective_ref + ".completionObjective",
                            )
                        )

        _validate_cycles(record, objectives_by_id, diagnostics)
        _validate_protect_flag_chains(record, objectives_by_id, diagnostics)
        diagnostics.extend(
            validate_unique(
                failure_flags,
                "duplicate objective failureFlag '{key}' (first defined at {first_loc})",
                record_ref + ".failureFlags[{key}]",
            )
        )
        diagnostics.extend(
            validate_unique(
                completion_flags,
                "duplicate protect completionFlag '{key}' (first defined at {first_loc})",
                record_ref + ".completionFlags[{key}]",
            )
        )
        _validate_dependency_set(
            diagnostics, record, "characters", record.dependencies.characters,
            record.dependencies.character_locs, characters, used_characters, "character",
        )
        _validate_dependency_set(
            diagnostics, record, "eventFlags", record.dependencies.event_flags,
            record.dependencies.event_flag_locs, event_flags, used_flags, "event flag",
        )
        _validate_dependency_set(
            diagnostics, record, "unitGroups", record.dependencies.unit_groups,
            record.dependencies.unit_group_locs, unit_groups, used_unit_groups, "unit group",
        )

    diagnostics.extend(
        validate_unique(
            ((identifier, loc) for identifier, loc, _kind in all_ids),
            "duplicate stable objective/group ID '{key}' (first defined at {first_loc})",
            "stableIds[{key}]",
        )
    )
    hashes = {}
    for identifier, loc, kind in all_ids:
        if not _ID_RE.match(identifier):
            continue
        value = stable_id_value(identifier)
        if value in hashes and hashes[value][0] != identifier:
            diagnostics.add(
                _err(
                    "{} ID '{}' collides with '{}' at runtime hash 0x{:08X}".format(
                        kind, identifier, hashes[value][0], value
                    ),
                    loc, "stableIds[{}]".format(identifier),
                )
            )
        else:
            hashes[value] = (identifier, loc)


class ChapterObjectivesTableSchema(TableSchema):
    name = SCHEMA_NAME
    version = SCHEMA_VERSION

    default_source = "src/data/chapter_objectives.json"
    default_hand_source = None
    default_output_name = "data_chapter_objectives.c"
    default_inventory_path = "reports/generated_data_chapterobjectives_inventory.md"
    record_budget = BUNDLE_CAPACITY
    record_budget_reason = "the generated bundle table has a fixed 32-chapter capacity"

    def dependencies(self):
        return ("constants.chapters", "constants.characters", "constants.event-flags", "units", "chapterbundle")

    def dependency_tables(self):
        return ("units", "chapterbundle")

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
    for dependency in ChapterObjectivesTableSchema().dependencies():
        graph.add_dependency(SCHEMA_NAME, dependency)
    return graph
