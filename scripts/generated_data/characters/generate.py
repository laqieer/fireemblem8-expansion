"""C89 generation for the ``gCharacterData[]`` (dense 1-based-designator,
``struct CharacterData[]``) table -- see ``schema.py``'s module docstring
for the designator/``.number`` derivation model this mirrors exactly.
"""

from __future__ import annotations

from ..cgen import render_banner
from .. import character_refs
from ..validators import extract_enum_constants
from .schema import (
    BMITEM_HEADER,
    BMUNIT_HEADER,
    read_character_attributes,
)

_BASE_STATS = ("hp", "pow", "skl", "spd", "def", "res", "lck", "con")
_BASE_FIELDS = ("baseHP", "basePow", "baseSkl", "baseSpd", "baseDef", "baseRes", "baseLck", "baseCon")
_GROWTH_STATS = ("hp", "pow", "skl", "spd", "def", "res", "lck")
_GROWTH_FIELDS = ("growthHP", "growthPow", "growthSkl", "growthSpd", "growthDef", "growthRes", "growthLck")


def _sorted_attributes(attributes, attribute_flags):
    """Emit flags in ascending bit-value order -- matches the vanilla
    hand file's own convention field-for-field (mirrors
    ``classes.generate._sorted_attributes``)."""
    return sorted(attributes, key=lambda name: attribute_flags.get(name, 0))


def _sorted_base_ranks(base_ranks, weapon_types):
    """Emit ``baseRanks`` entries in ascending ``ITYPE_*`` index order --
    matches the vanilla hand file's own convention (mirrors
    ``classes.generate._sorted_base_ranks``)."""
    return sorted(base_ranks.items(), key=lambda kv: weapon_types.get(kv[0], (1 << 30, None))[0])


def _designator_literal(record):
    """The array-index designator token: the bare ``CHARACTER_*`` symbol
    for a symbolic record, or a lowercase-hex raw literal (e.g. ``0x1b``,
    ``0x100``) for a raw/generic-template record -- matches the hand
    file's own convention exactly (confirmed against every one of its
    256 ``[... - 1] = { ... }`` entries)."""
    if record.character is not None:
        return record.character
    return "0x{:02x}".format(record.character_id)


def _number_literal(record, characters_enum):
    """The derived ``.number`` field's own literal token: the designator
    symbol itself for a symbolic record, or the designator truncated to
    ``u8`` as a lowercase-hex literal (``0`` -- not ``0x00`` -- for the
    single designator-256 padding slot, matching the hand file's own
    ``.number = 0,``) for a raw/generic-template record."""
    if record.character is not None:
        return record.character
    number = record.resolved_number(characters_enum)
    return "0x{:02x}".format(number) if number else "0"


def generate_c_source(records, source_path):
    characters_enum = character_refs.read_character_designators()
    attribute_flags = read_character_attributes(BMUNIT_HEADER)
    weapon_types = extract_enum_constants(BMITEM_HEADER, name_prefix="ITYPE_")

    ordered = sorted(
        records,
        key=lambda r: r.designator(characters_enum) if r.designator(characters_enum) is not None else (1 << 30),
    )

    parts = [render_banner(source=source_path, table="characters")]
    parts.append('#include "global.h"\n')
    parts.append('#include "bmunit.h"\n')
    parts.append('#include "bmitem.h"\n')
    parts.append('#include "bmreliance.h"\n')
    parts.append('#include "constants/characters.h"\n')
    parts.append('#include "constants/classes.h"\n')
    parts.append('#include "constants/items.h"\n\n')
    parts.append("CONST_DATA struct CharacterData gCharacterData[] = {\n")

    for record in ordered:
        parts.append("\t[{} - 1] = {{\n".format(_designator_literal(record)))

        if record.name_text_id:
            parts.append("\t\t.nameTextId = {},\n".format(record.name_text_id))
        if record.desc_text_id:
            parts.append("\t\t.descTextId = {},\n".format(record.desc_text_id))

        parts.append("\t\t.number = {},\n".format(_number_literal(record, characters_enum)))
        parts.append("\t\t.defaultClass = {},\n".format(record.default_class))

        if record.portrait:
            parts.append("\t\t.portraitId = {},\n".format(record.portrait))
        if record.mini_portrait:
            parts.append("\t\t.miniPortrait = {},\n".format(record.mini_portrait))
        if record.affinity:
            parts.append("\t\t.affinity = {},\n".format(record.affinity))
        if record.sort_order:
            parts.append("\t\t.sort_order = {},\n".format(record.sort_order))
        if record.base_level:
            parts.append("\t\t.baseLevel = {},\n".format(record.base_level))

        for stat_key, field_name in zip(_BASE_STATS, _BASE_FIELDS):
            parts.append("\t\t.{} = {},\n".format(field_name, record.base[stat_key]))

        if record.base_ranks:
            parts.append("\t\t.baseRanks = {\n")
            for itype_name, wexp_name in _sorted_base_ranks(record.base_ranks, weapon_types):
                parts.append("\t\t\t[{}] = {},\n".format(itype_name, wexp_name))
            parts.append("\t\t},\n")

        for stat_key, field_name in zip(_GROWTH_STATS, _GROWTH_FIELDS):
            parts.append("\t\t.{} = {},\n".format(field_name, record.growth[stat_key]))

        if record.attributes:
            flags = _sorted_attributes(record.attributes, attribute_flags)
            parts.append("\t\t.attributes = {},\n".format(" | ".join(flags)))

        if record.support_data:
            parts.append("\t\t.pSupportData = &{},\n".format(record.support_data))

        if record.visit_group:
            parts.append("\t\t.visit_group = {},\n".format(record.visit_group))

        parts.append("\t},\n")

    parts.append("};\n")
    return "".join(parts)
