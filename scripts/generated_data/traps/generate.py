"""C89 generation for ``TrapData_*`` (flat ``u8[]`` initializer) arrays."""

from __future__ import annotations

from ..cgen import render_banner
from .schema import TRAP_NONE

# Issue #5 Batch 3b: TrapData_Event_Ch2Hard's hand-written home in
# src/events_trapdata.c is a separate, non-adjacent hard-mode block --
# roughly 1850 lines after TrapData_Event_Ch2's own normal-mode block, not
# a suffix of it. A single object's default ``.data`` section can only be
# spliced into ldscript.txt at one address, so this symbol alone is
# placed in its own dedicated section here, letting ldscript.txt slot it
# in separately from TrapData_Event_Ch2 at each symbol's own exact
# original address (zero shift either side). See the guard/section
# comments around both symbols in src/events_trapdata.c and
# docs/generated_data.md's "traps" section for the full rationale.
_HARD_SECTION_SYMBOLS = frozenset({"TrapData_Event_Ch2Hard"})


def _storage_class(symbol):
    if symbol in _HARD_SECTION_SYMBOLS:
        return 'SECTION(".data.trapch2hard")'
    return "CONST_DATA"


def generate_c_source(records, source_path):
    parts = [render_banner(source=source_path, table="traps")]
    parts.append('#include "global.h"\n')
    parts.append('#include "bmtrick.h"\n')
    parts.append('#include "constants/items.h"\n\n')

    for record in records:
        parts.append("{} u8 {}[] = {{\n".format(_storage_class(record.symbol), record.symbol))
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
