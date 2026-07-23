"""C89 generation for ``UnitDefinition``/``REDA`` (build/generated/data only)."""

from __future__ import annotations

from ..cgen import render_banner, render_u8_array_field


def _reda_symbol(group_symbol, unit_index):
    return "REDA_{}_{}".format(group_symbol, unit_index)


def generate_c_source(groups, source_path):
    """Render C89 ``struct UnitDefinition``/``struct REDA`` definitions.

    Field order matches ``include/bmunit.h`` exactly. REDA sub-arrays are
    emitted with synthetic ``REDA_<group>_<index>`` names -- unlike the
    ``UnitDefinition`` group symbols (which are extern-visible and must
    round-trip byte-for-byte in meaning against ``src/events_udefs.c``),
    the REDA symbol names themselves are private implementation details
    never referenced from outside the translation unit, so this slice does
    not try to reproduce the hand-written file's REDA naming scheme -- only
    the REDA *values* are round-tripped (see ``parser.py``).

    Emission order matters for byte-for-byte ``.data`` layout identity with
    ``src/events_udefs.c``'s Chapter 2 prefix block: the hand file emits
    *every* REDA sub-array across *all* groups first (in group/unit order),
    and only then emits the ``UnitDefinition`` group arrays (again in group
    order) -- it never interleaves a group's REDA arrays with its own
    ``UnitDefinition`` array. This function replicates that two-pass
    ordering exactly (do not go back to a single per-group interleaved
    pass without re-verifying byte identity).
    """
    parts = [render_banner(source=source_path, table="units")]
    parts.append('#include "global.h"\n')
    parts.append('#include "bmunit.h"\n')
    parts.append('#include "muctrl.h"\n')
    parts.append('#include "constants/characters.h"\n')
    parts.append('#include "constants/classes.h"\n')
    parts.append('#include "constants/items.h"\n\n')

    # Pass 1: every REDA sub-array, across all groups, in group/unit order.
    for group in groups:
        for index, unit in enumerate(group.units):
            if not unit.redas:
                continue
            symbol = _reda_symbol(group.symbol, index)
            parts.append("CONST_DATA struct REDA {}[] = {{\n".format(symbol))
            for reda in unit.redas:
                parts.append(
                    "    { .x = %d, .y = %d, .flags = %d, .a = %d, .b = %d, .delayFrames = %d },\n"
                    % (reda.x, reda.y, reda.flags, reda.a, reda.b, reda.delay_frames)
                )
            parts.append("};\n")
    parts.append("\n")

    # Pass 2: every UnitDefinition group array, in group order.
    for group in groups:
        parts.append("CONST_DATA struct UnitDefinition {}[] = {{\n".format(group.symbol))
        for index, unit in enumerate(group.units):
            parts.append("    {\n")
            parts.append("        .charIndex = {},\n".format(unit.char_index))
            parts.append("        .classIndex = {},\n".format(unit.class_index))
            if unit.leader_char_index:
                parts.append("        .leaderCharIndex = {},\n".format(unit.leader_char_index))
            if unit.autolevel:
                parts.append("        .autolevel = {},\n".format(unit.autolevel))
            parts.append("        .allegiance = {},\n".format(unit.allegiance))
            parts.append("        .level = {},\n".format(unit.level))
            parts.append("        .xPosition = {},\n".format(unit.x_position))
            parts.append("        .yPosition = {},\n".format(unit.y_position))
            if unit.gen_monster:
                parts.append("        .genMonster = {},\n".format(unit.gen_monster))
            if unit.item_drop:
                parts.append("        .itemDrop = {},\n".format(unit.item_drop))
            if unit.sum_flag:
                parts.append("        .sumFlag = {},\n".format(unit.sum_flag))
            if unit.extra_data:
                parts.append("        .extraData = {},\n".format(unit.extra_data))
            if unit.redas:
                parts.append("        .redaCount = {},\n".format(unit.reda_count))
                parts.append("        .redas = {},\n".format(_reda_symbol(group.symbol, index)))
            if unit.items:
                parts.append(render_u8_array_field("items", unit.items, indent="        "))
            if any(unit.ai):
                parts.append(render_u8_array_field("ai", unit.ai, indent="        "))
            parts.append("    },\n")
        parts.append("    { 0 },\n")
        parts.append("};\n\n")

    return "".join(parts)
