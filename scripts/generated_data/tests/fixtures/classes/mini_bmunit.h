#ifndef GUARD_FIXTURE_BMUNIT_H
#define GUARD_FIXTURE_BMUNIT_H

struct ClassData
{
    u16 nameTextId;
    u16 descTextId;
    u8 number;
    u8 promotion;
    u8 SMSId;
    u8 slowWalking;
    u16 defaultPortraitId;
    u8 sort_order;

    s8 baseHP, basePow, baseSkl, baseSpd, baseDef, baseRes, baseCon, baseMov;
    s8 maxHP, maxPow, maxSkl, maxSpd, maxDef, maxRes, maxCon;
    s8 classRelativePower;
    s8 growthHP, growthPow, growthSkl, growthSpd, growthDef, growthRes, growthLck;
    u8 promotionHp, promotionPow, promotionSkl, promotionSpd, promotionDef, promotionRes;

    u32 attributes;
    u8 baseRanks[8];

    struct BattleAnimDef *pBattleAnimDef;
    const s8 *pMovCostTable[3];
    const s8 *pTerrainAvoidLookup;
    const s8 *pTerrainDefenseLookup;
    const s8 *pTerrainResistanceLookup;
    const void *_pU50;
};

enum
{
    // Character/Class attributes
    CA_NONE           = 0,

    CA_LORD           = (1 << 0),
    CA_MOUNTED        = (1 << 1),
    CA_PROMOTED       = (1 << 2),

    // Helpers
    CA_MOUNTEDAID = (CA_MOUNTED),
};

#endif
