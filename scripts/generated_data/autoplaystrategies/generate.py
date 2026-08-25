"""C89 generation for bounded autoplay strategy registries and assignments."""

from __future__ import annotations

from ..cgen import render_banner
from ..chapterobjectives.schema import stable_id_value
from .schema import REFERENCE_STRATEGIES


OBJECTIVE_CAPABILITY_TO_C = {
    "protect": "EXPANSION_AUTOPLAY_STRATEGY_OBJECTIVE_PROTECT",
    "reach_area": "EXPANSION_AUTOPLAY_STRATEGY_OBJECTIVE_REACH_AREA",
    "defeat_group": "EXPANSION_AUTOPLAY_STRATEGY_OBJECTIVE_DEFEAT_GROUP",
    "event_flag": "EXPANSION_AUTOPLAY_STRATEGY_OBJECTIVE_EVENT_FLAG",
    "hold_until_turn": "EXPANSION_AUTOPLAY_STRATEGY_OBJECTIVE_HOLD_UNTIL_TURN",
}

ACTION_CAPABILITY_TO_C = {
    "combat": "EXPANSION_AUTOPLAY_STRATEGY_ACTION_COMBAT",
    "objective_move": "EXPANSION_AUTOPLAY_STRATEGY_ACTION_OBJECTIVE_MOVE",
}


def _capabilities(values, mapping):
    return " | ".join(mapping[value] for value in values) if values else "0"


def _flag(flag):
    return flag if flag is not None else "EXPANSION_AUTOPLAY_STRATEGY_FLAG_NONE"


def _groups_name(record):
    return "s{}GroupAssignments".format(record.symbol)


def _units_name(record):
    return "s{}UnitAssignments".format(record.symbol)


def generate_c_source(records, source_path):
    reference_profiles_enabled = records.get("reference_profiles_enabled", False)
    strategies = [
        strategy
        for strategy in records["strategies"]
        if reference_profiles_enabled or strategy.id not in REFERENCE_STRATEGIES
    ]
    strategy_ids = {strategy.id for strategy in strategies}
    chapters = []
    for chapter in records["chapters"]:
        chapter_assignment = chapter.chapter_assignment
        if chapter_assignment is not None and chapter_assignment.strategy not in strategy_ids:
            chapter_assignment = None
        group_assignments = [
            assignment
            for assignment in chapter.group_assignments
            if assignment.strategy in strategy_ids
        ]
        unit_assignments = [
            assignment
            for assignment in chapter.unit_assignments
            if assignment.strategy in strategy_ids
        ]
        if chapter_assignment is not None or group_assignments or unit_assignments:
            chapters.append((chapter, chapter_assignment, group_assignments, unit_assignments))
    parts = [render_banner(source=source_path, table="autoplaystrategies")]
    parts.append('#include "global.h"\n')
    parts.append('#include "constants/chapters.h"\n')
    parts.append('#include "constants/characters.h"\n')
    parts.append('#include "constants/event-flags.h"\n')
    parts.append('#include "expansion_autoplay_strategies.h"\n\n')

    for strategy in strategies:
        parts.append(
            "extern bool {}(const struct ExpansionAutoplayStrategyContext* context);\n".format(
                strategy.callback
            )
        )
    if strategies:
        parts.append("\n")

    parts.append("CONST_DATA const struct ExpansionAutoplayStrategy gExpansionAutoplayStrategies[] = {\n")
    for strategy in strategies:
        flags = "EXPANSION_AUTOPLAY_STRATEGY_FLAG_REFERENCE_PROFILE" if strategy.id in REFERENCE_STRATEGIES else "0"
        parts.append("    {\n")
        parts.append("        .id = 0x{:08X},\n".format(stable_id_value(strategy.id)))
        parts.append(
            "        .objectiveCapabilities = {},\n".format(
                _capabilities(strategy.objectives, OBJECTIVE_CAPABILITY_TO_C)
            )
        )
        parts.append(
            "        .actionCapabilities = {},\n".format(_capabilities(strategy.actions, ACTION_CAPABILITY_TO_C))
        )
        parts.append("        .callback = {},\n".format(strategy.callback))
        parts.append("        .flags = {},\n".format(flags))
        parts.append("    },\n")
    parts.append("    { 0 },\n")
    parts.append("};\n\n")

    for chapter, _chapter_assignment, group_assignments, _unit_assignments in chapters:
        if group_assignments:
            parts.append(
                "static CONST_DATA const struct ExpansionAutoplayStrategyGroupAssignment {}[] = {{\n".format(
                    _groups_name(chapter)
                )
            )
            for assignment in group_assignments:
                parts.append("    {\n")
                parts.append("        .groupId = 0x{:08X},\n".format(stable_id_value(assignment.group)))
                parts.append("        .strategyId = 0x{:08X},\n".format(stable_id_value(assignment.strategy)))
                parts.append("        .activationFlag = {},\n".format(_flag(assignment.activation_flag)))
                parts.append("    },\n")
            parts.append("};\n\n")

        if _unit_assignments:
            parts.append(
                "static CONST_DATA const struct ExpansionAutoplayStrategyUnitAssignment {}[] = {{\n".format(
                    _units_name(chapter)
                )
            )
            for assignment in _unit_assignments:
                parts.append("    {\n")
                parts.append("        .character = {},\n".format(assignment.character))
                parts.append("        .strategyId = 0x{:08X},\n".format(stable_id_value(assignment.strategy)))
                parts.append("        .activationFlag = {},\n".format(_flag(assignment.activation_flag)))
                parts.append("    },\n")
            parts.append("};\n\n")

    parts.append("CONST_DATA const struct ExpansionAutoplayStrategyBundle gExpansionAutoplayStrategyBundles[] = {\n")
    for chapter, assignment, group_assignments, unit_assignments in chapters:
        parts.append("    {\n")
        parts.append("        .chapterId = {},\n".format(chapter.chapter))
        parts.append("        .groupAssignmentCount = {},\n".format(len(group_assignments)))
        parts.append("        .unitAssignmentCount = {},\n".format(len(unit_assignments)))
        parts.append(
            "        .chapterStrategyId = {},\n".format(
                "0x{:08X}".format(stable_id_value(assignment.strategy)) if assignment else "0"
            )
        )
        parts.append(
            "        .chapterActivationFlag = {},\n".format(
                _flag(assignment.activation_flag) if assignment else "EXPANSION_AUTOPLAY_STRATEGY_FLAG_NONE"
            )
        )
        parts.append(
            "        .groupAssignments = {},\n".format(
                _groups_name(chapter) if group_assignments else "NULL"
            )
        )
        parts.append(
            "        .unitAssignments = {},\n".format(
                _units_name(chapter) if unit_assignments else "NULL"
            )
        )
        parts.append("    },\n")
    parts.append("    { .chapterId = EXPANSION_AUTOPLAY_STRATEGY_CHAPTER_NONE },\n")
    parts.append("};\n")
    return "".join(parts)
