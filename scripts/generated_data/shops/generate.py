"""C89 generation for ``ShopList_*`` (``u16[]`` item-symbol) arrays."""

from __future__ import annotations

from ..cgen import render_banner
from .schema import ITEM_NONE


def generate_c_source(records, source_path):
    parts = [render_banner(source=source_path, table="shops")]
    parts.append('#include "global.h"\n')
    parts.append('#include "constants/items.h"\n\n')

    for record in records:
        parts.append("CONST_DATA u16 {}[] = {{\n".format(record.symbol))
        for item in record.items:
            parts.append("    {},\n".format(item))
        parts.append("    {},\n".format(ITEM_NONE))
        parts.append("};\n\n")

    return "".join(parts)
