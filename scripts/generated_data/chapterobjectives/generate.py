"""C89 generation for bounded chapter objective bundles."""

from __future__ import annotations

from ..cgen import render_banner
from .schema import KIND_TO_C, stable_id_value


def _group_members_name(record, group):
    return "s{}_Group{:08X}Members".format(record.symbol, stable_id_value(group.id))


def _groups_name(record):
    return "s{}Groups".format(record.symbol)


def _objectives_name(record):
    return "s{}Objectives".format(record.symbol)


def _flag(flag):
    return flag if flag is not None else "EXPANSION_CHAPTER_OBJECTIVE_FLAG_NONE"


def _area(area, field):
    return getattr(area, field) if area is not None else 0


def generate_c_source(records, source_path):
    parts = [render_banner(source=source_path, table="chapterobjectives")]
    parts.append('#include "global.h"\n')
    parts.append('#include "constants/chapters.h"\n')
    parts.append('#include "constants/characters.h"\n')
    parts.append('#include "constants/event-flags.h"\n')
    parts.append('#include "expansion_chapter_objectives.h"\n\n')

    for record in records:
        for group in record.groups:
            parts.append("static const u8 {}[] = {{\n".format(_group_members_name(record, group)))
            for member in group.members:
                parts.append("    {},\n".format(member.character))
            parts.append("};\n\n")

        if record.groups:
            parts.append("static CONST_DATA const struct ExpansionChapterAiGroup {}[] = {{\n".format(
                _groups_name(record)
            ))
            for group in record.groups:
                parts.append("    {\n")
                parts.append("        .id = 0x{:08X},\n".format(stable_id_value(group.id)))
                parts.append("        .members = {},\n".format(_group_members_name(record, group)))
                parts.append("        .memberCount = {},\n".format(len(group.members)))
                parts.append("    },\n")
            parts.append("};\n\n")

        groups_by_id = {group.id: index for index, group in enumerate(record.groups)}
        if record.objectives:
            parts.append("static CONST_DATA const struct ExpansionChapterObjective {}[] = {{\n".format(
                _objectives_name(record)
            ))
            for objective in record.objectives:
                group_ref = "NULL"
                if objective.group is not None:
                    group_ref = "&{}[{}]".format(_groups_name(record), groups_by_id[objective.group])
                parts.append("    {\n")
                parts.append("        .id = 0x{:08X},\n".format(stable_id_value(objective.id)))
                parts.append(
                    "        .completionObjectiveId = {},\n".format(
                        "0x{:08X}".format(stable_id_value(objective.completion_objective))
                        if objective.completion_objective is not None else "0"
                    )
                )
                parts.append("        .group = {},\n".format(group_ref))
                parts.append("        .activationFlag = {},\n".format(_flag(objective.activation_flag)))
                parts.append("        .deactivationFlag = {},\n".format(_flag(objective.deactivation_flag)))
                parts.append(
                    "        .eventFlag = {},\n".format(
                        _flag(objective.failure_flag or objective.event_flag)
                    )
                )
                parts.append("        .untilTurn = {},\n".format(objective.until_turn or 0))
                parts.append("        .kind = {},\n".format(KIND_TO_C[objective.kind]))
                parts.append("        .protectedCharacter = {},\n".format(objective.protected_character or 0))
                parts.append("        .xMin = {},\n".format(_area(objective.area, "x_min")))
                parts.append("        .yMin = {},\n".format(_area(objective.area, "y_min")))
                parts.append("        .xMax = {},\n".format(_area(objective.area, "x_max")))
                parts.append("        .yMax = {},\n".format(_area(objective.area, "y_max")))
                parts.append("    },\n")
            parts.append("};\n\n")

    parts.append("CONST_DATA const struct ExpansionChapterObjectiveBundle gExpansionChapterObjectiveBundles[] = {\n")
    for record in records:
        parts.append("    {\n")
        parts.append("        .chapterId = {},\n".format(record.chapter))
        parts.append("        .objectiveCount = {},\n".format(len(record.objectives)))
        parts.append("        .groupCount = {},\n".format(len(record.groups)))
        parts.append("        .objectives = {},\n".format(
            _objectives_name(record) if record.objectives else "NULL"
        ))
        parts.append("        .groups = {},\n".format(_groups_name(record) if record.groups else "NULL"))
        parts.append("    },\n")
    parts.append("    { .chapterId = EXPANSION_CHAPTER_OBJECTIVE_CHAPTER_NONE },\n")
    parts.append("};\n")
    return "".join(parts)
