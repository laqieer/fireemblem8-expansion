"""C89 generation for the ``sWeaponTriangleRules[]`` (index-ordered
``struct WeaponTriangleRule[]``) table."""

from __future__ import annotations

from ..cgen import render_banner


def generate_c_source(records, source_path):
    parts = [render_banner(source=source_path, table="weapontriangle")]
    parts.append('#include "global.h"\n')
    parts.append('#include "bmitem.h"\n')
    parts.append('#include "bmbattle.h"\n\n')
    parts.append("CONST_DATA struct WeaponTriangleRule sWeaponTriangleRules[] = {\n")

    for record in records:
        parts.append(
            "    {{ {}, {}, {}, {} }},\n".format(
                record.attacker, record.defender, record.hit_bonus, record.atk_bonus
            )
        )

    # Implicit generated terminator -- never authored in JSON. See the
    # module docstring in schema.py: BattleApplyWeaponTriangleEffect
    # scans until attackerWeaponType < 0.
    parts.append("\n    { -1 },\n")
    parts.append("};\n")

    return "".join(parts)
