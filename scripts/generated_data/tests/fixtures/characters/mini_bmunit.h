#ifndef GUARD_FIXTURE_BMUNIT_H
#define GUARD_FIXTURE_BMUNIT_H

struct CharacterData
{
    u16 nameTextId;
    u16 descTextId;
    u8 number;
    u8 defaultClass;
    u16 portraitId;
    u8 miniPortrait;
    u8 affinity;
    u8 sort_order;

    s8 baseLevel;
    s8 baseHP, basePow, baseSkl, baseSpd, baseDef, baseRes, baseLck, baseCon;

    u8 baseRanks[8];

    u8 growthHP, growthPow, growthSkl, growthSpd, growthDef, growthRes, growthLck;

    u8 _u23;
    u8 _u24;
    u8 _u25[2];
    u8 _u27;

    u32 attributes;

    const struct SupportData* pSupportData;
    u8 visit_group;
};

enum
{
    // Character/Class attributes
    CA_NONE           = 0,

    CA_LORD           = (1 << 0),
    CA_FEMALE         = (1 << 1),
    CA_PROMOTED       = (1 << 2),

    // Helpers
    CA_MOUNTEDAID = (CA_LORD),
};

enum unit_affinity_index {
    UNIT_AFFIN_FIRE = 1,
    UNIT_AFFIN_THUNDER,
    UNIT_AFFIN_WIND,
    UNIT_AFFIN_ICE,
    UNIT_AFFIN_DARK,
    UNIT_AFFIN_LIGHT,
    UNIT_AFFIN_ANIMA,
};

#endif
