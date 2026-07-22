"""C89 generation for ``SupportData`` (build/generated/data only)."""

from __future__ import annotations

from ..cgen import render_banner, render_u8_array_field


def generate_c_source(records, source_path):
    """Render C89 ``struct SupportData`` definitions for every record.

    Field order and array layout match ``include/bmreliance.h`` exactly so
    the output is a drop-in replacement candidate for
    ``src/data_supports.c`` once a later slice decides to link it -- this
    canary intentionally does not link it yet.
    """
    parts = [render_banner(source=source_path, table="supports")]
    parts.append('#include "global.h"\n')
    parts.append('#include "bmreliance.h"\n')
    parts.append('#include "constants/characters.h"\n\n')

    for record in records:
        parts.append("CONST_DATA struct SupportData {} =\n{{\n".format(record.symbol))
        parts.append("    .characters = {{ {} }},\n".format(", ".join(record.characters)))
        parts.append(render_u8_array_field("supportExpBase", record.support_exp_base, indent="    "))
        parts.append(render_u8_array_field("supportExpGrowth", record.support_exp_growth, indent="    "))
        parts.append("    .supportCount = {},\n".format(record.support_count))
        parts.append("};\n\n")

    return "".join(parts)
