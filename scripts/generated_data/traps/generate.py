"""C89 generation for ``TrapData_*`` (flat ``u8[]`` initializer) arrays."""

from __future__ import annotations

from ..cgen import render_banner
from .schema import TRAP_NONE


def generate_c_source(records, source_path):
    parts = [render_banner(source=source_path, table="traps")]
    parts.append('#include "global.h"\n')
    parts.append('#include "bmtrick.h"\n')
    parts.append('#include "constants/items.h"\n\n')

    for record in records:
        parts.append("CONST_DATA u8 {}[] = {{\n".format(record.symbol))
        for entry in record.entries:
            parts.append("    /* type */ {},\n".format(entry.trap_type))
            parts.append("    /* xPos */ {},\n".format(entry.x_position))
            parts.append("    /* yPos */ {},\n".format(entry.y_position))
            parts.append("    /* subt */ {},\n".format(entry.subtype))
            parts.append("    /* cnt  */ {},\n".format(entry.turn_counter))
            parts.append("    /* turn */ {},\n\n".format(entry.turn))
        parts.append("    /* type */ {}\n".format(TRAP_NONE))
        parts.append("};\n\n")

    return "".join(parts)
