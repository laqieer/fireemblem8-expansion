"""C89 generation for the ``gClassData[]`` (index-designated ``struct
ClassData[]``) table."""

from __future__ import annotations

from ..cgen import render_banner
from ..validators import extract_enum_constants
from .schema import (
    BMITEM_HEADER,
    BMUNIT_HEADER,
    CLASSES_HEADER,
    CLASS_NONE_DEFAULT,
    MOV_COST_NULL_LITERAL,
    _BASE_STATS,
    _GROWTH_STATS,
    _MAX_STATS,
    _PROMOTION_STATS,
    read_class_attributes,
)

_BASE_FIELDS = ("baseHP", "basePow", "baseSkl", "baseSpd", "baseDef", "baseRes", "baseCon", "baseMov")
_MAX_FIELDS = ("maxHP", "maxPow", "maxSkl", "maxSpd", "maxDef", "maxRes", "maxCon")
_GROWTH_FIELDS = ("growthHP", "growthPow", "growthSkl", "growthSpd", "growthDef", "growthRes", "growthLck")
_PROMOTION_FIELDS = ("promotionHp", "promotionPow", "promotionSkl", "promotionSpd", "promotionDef", "promotionRes")


def _sorted_attributes(attributes, attribute_flags):
    """Emit flags in ascending bit-value order -- matches the vanilla
    hand file's own convention field-for-field (every ``.attributes``
    combo in ``src/data_classes.c`` lists its ``CA_*`` flags in ascending
    bit-position order)."""
    return sorted(attributes, key=lambda name: attribute_flags.get(name, 0))


def _sorted_base_ranks(base_ranks, weapon_types):
    """Emit ``baseRanks`` entries in ascending ``ITYPE_*`` index order --
    matches the vanilla hand file's own convention (every ``.baseRanks``
    block in ``src/data_classes.c`` lists its ``[ITYPE_*]`` designators in
    ascending weapon-type-index order)."""
    return sorted(base_ranks.items(), key=lambda kv: weapon_types.get(kv[0], (1 << 30, None))[0])


def generate_c_source(records, source_path):
    classes_enum = extract_enum_constants(CLASSES_HEADER, name_prefix="CLASS_")
    attribute_flags = read_class_attributes(BMUNIT_HEADER)
    weapon_types = extract_enum_constants(BMITEM_HEADER, name_prefix="ITYPE_")

    ordered = sorted(
        records,
        key=lambda r: classes_enum[r.class_name][0] if r.class_name in classes_enum else 1 << 30,
    )

    parts = [render_banner(source=source_path, table="classes")]
    parts.append('#include "global.h"\n')
    parts.append('#include "bmunit.h"\n')
    parts.append('#include "bmitem.h"\n')
    parts.append('#include "ekrbattle.h"\n')
    parts.append('#include "constants/classes.h"\n\n')
    parts.append("CONST_DATA struct ClassData gClassData[] = {\n")

    for record in ordered:
        parts.append("\t[{} - 1] = {{\n".format(record.class_name))

        if record.name_text_id:
            parts.append("\t\t.nameTextId = {},\n".format(record.name_text_id))
        if record.desc_text_id:
            parts.append("\t\t.descTextId = {},\n".format(record.desc_text_id))

        parts.append("\t\t.number = {},\n".format(record.class_name))
        if record.promotion != CLASS_NONE_DEFAULT:
            parts.append("\t\t.promotion = {},\n".format(record.promotion))

        parts.append("\t\t.SMSId = {},\n".format(record.sms_id))
        if record.slow_walking:
            parts.append("\t\t.slowWalking = UNIT_WALKSPEED_SLOW,\n")
        if record.default_portrait_id:
            parts.append("\t\t.defaultPortraitId = {},\n".format(record.default_portrait_id))
        if record.sort_order:
            parts.append("\t\t.sort_order = {},\n".format(record.sort_order))

        for stat_key, field_name in zip(_BASE_STATS, _BASE_FIELDS):
            parts.append("\t\t.{} = {},\n".format(field_name, record.base[stat_key]))

        for stat_key, field_name in zip(_MAX_STATS, _MAX_FIELDS):
            parts.append("\t\t.{} = {},\n".format(field_name, record.max_stats[stat_key]))
        if record.class_relative_power:
            parts.append("\t\t.classRelativePower = {},\n".format(record.class_relative_power))

        for stat_key, field_name in zip(_GROWTH_STATS, _GROWTH_FIELDS):
            parts.append("\t\t.{} = {},\n".format(field_name, record.growth[stat_key]))

        for stat_key, field_name in zip(_PROMOTION_STATS, _PROMOTION_FIELDS):
            parts.append("\t\t.{} = {},\n".format(field_name, record.promotion_gain[stat_key]))

        if record.attributes:
            flags = _sorted_attributes(record.attributes, attribute_flags)
            parts.append("\t\t.attributes = {},\n".format(" | ".join(flags)))

        if record.base_ranks:
            parts.append("\t\t.baseRanks = {\n")
            for itype_name, wexp_name in _sorted_base_ranks(record.base_ranks, weapon_types):
                parts.append("\t\t\t[{}] = {},\n".format(itype_name, wexp_name))
            parts.append("\t\t},\n")

        if record.battle_anim is not None:
            parts.append("\t\t.pBattleAnimDef = {},\n".format(record.battle_anim))

        parts.append("\t\t.pMovCostTable = {\n")
        for symbol in record.mov_cost_table:
            parts.append("\t\t\t{},\n".format(symbol if symbol is not None else MOV_COST_NULL_LITERAL))
        parts.append("\t\t},\n")

        parts.append("\t\t.pTerrainAvoidLookup = {},\n".format(record.terrain_avoid))
        parts.append("\t\t.pTerrainDefenseLookup = {},\n".format(record.terrain_defense))
        parts.append("\t\t.pTerrainResistanceLookup = {},\n".format(record.terrain_resistance))

        if record.reserved_terrain_table is not None:
            parts.append("\t\t._pU50 = &{},\n".format(record.reserved_terrain_table))

        parts.append("\t},\n")

    parts.append("};\n")
    return "".join(parts)
