#include "global.h"

#include <stdio.h>

#include "action_semantics.h"
#include "bmbattle.h"
#include "bmmap.h"
#include "bmtrap.h"
#include "bmtrick.h"
#include "bmunit.h"
#include "constants/characters.h"
#include "constants/classes.h"
#include "constants/items.h"

#define CHECK(condition, message) \
    do \
    { \
        if (!(condition)) \
        { \
            fprintf(stderr, "ACTION_SEMANTICS_HOST_TEST: FAIL: %s\n", message); \
            return 1; \
        } \
    } while (0)

struct PlaySt gPlaySt;
struct Vec2 gBmMapSize;
u8** gBmMapUnit;
u8** gBmMapTerrain;
u8** gBmMapFog;
struct BattleUnit gBattleTarget;
u8 gSummonConfig[4][2] = {
    { CHARACTER_EWAN, CHARACTER_SUMMON_EWAN },
    { 0, 0 },
    { 0, 0 },
    { 0, 0 },
};

static u8 sUnitData[8][8];
static u8* sUnitRows[8];
static u8 sTerrainData[8][8];
static u8* sTerrainRows[8];
static u8 sFogData[8][8];
static u8* sFogRows[8];
static int sTrapX;
static int sTrapY;
static int sTrapCount;
static bool sTrapContractFailed;
static int sConsumedSlot;
static struct Unit* sCaster;
static struct Unit* sExistingSummon;
static struct Unit sRedUnit;
static int sRedUnitCount;

struct Unit* GetUnit(int id)
{
    if (id == 1)
        return sCaster;
    if (id == 2)
        return sExistingSummon;
    if (id > FACTION_RED
        && id < FACTION_PURPLE
        && id - FACTION_RED <= sRedUnitCount)
        return &sRedUnit;
    return NULL;
}

int GetUnitMagBy2Range(struct Unit* unit)
{
    (void)unit;
    return 3;
}

s8 CanUnitCrossTerrain(struct Unit* unit, int terrain)
{
    (void)unit;
    return terrain != 0xFF;
}

bool IsThereClosedDoorAt(s8 x, s8 y)
{
    return gBmMapTerrain[y][x] == 3;
}

bool IsThereClosedChestAt(s8 x, s8 y)
{
    return gBmMapTerrain[y][x] == 0x21;
}

struct Trap* AddTrap(int x, int y, int trapType, int meta)
{
    static struct Trap trap;

    if (trapType != TRAP_TORCHLIGHT || meta != 8)
    {
        sTrapContractFailed = true;
        return NULL;
    }
    sTrapX = x;
    sTrapY = y;
    sTrapCount++;
    return &trap;
}

s8 IsItemHammernable(int item)
{
    return item != 0 && (item & 0xFF00) != 0xFF00;
}

int MakeNewItem(int item)
{
    return (item & 0xFF) | 0xFF00;
}

void UnitUpdateUsedItem(struct Unit* unit, int itemSlot)
{
    sConsumedSlot = itemSlot;
    unit->items[itemSlot] -= 0x100;
}

int main(void)
{
    struct CharacterData character = { 0 };
    struct CharacterData existingCharacter = { 0 };
    struct ClassData unitClass = { 0 };
    struct Unit caster = { 0 };
    struct Unit target = { 0 };
    int y;
    int x;

    gBmMapSize.x = 8;
    gBmMapSize.y = 8;
    for (y = 0; y < 8; y++)
    {
        sUnitRows[y] = sUnitData[y];
        sTerrainRows[y] = sTerrainData[y];
        sFogRows[y] = sFogData[y];
        for (x = 0; x < 8; x++)
        {
            sUnitData[y][x] = 0;
            sTerrainData[y][x] = 1;
            sFogData[y][x] = 1;
        }
    }
    gBmMapUnit = sUnitRows;
    gBmMapTerrain = sTerrainRows;
    gBmMapFog = sFogRows;
    caster.pCharacterData = &character;
    caster.pClassData = &unitClass;
    caster.xPos = 2;
    caster.yPos = 2;
    target.pCharacterData = &existingCharacter;
    target.pClassData = &unitClass;
    target.xPos = 4;
    target.yPos = 4;
    caster.index = 1;
    character.number = CHARACTER_EWAN;
    unitClass.attributes = CA_SUMMON;
    sCaster = &caster;

    CHECK(ActionSemantics_ApplyTorchTarget(1, 6),
          "first Torch coordinate must apply");
    CHECK(ActionSemantics_ApplyTorchTarget(6, 1),
          "second Torch coordinate must apply");
    CHECK(!sTrapContractFailed
              && sTrapCount == 2 && sTrapX == 6 && sTrapY == 1,
          "Torch effect must use the selected coordinate");
    CHECK(!ActionSemantics_ApplyTorchTarget(8, 1) && sTrapCount == 2,
          "out-of-bounds Torch coordinate must not apply");

    CHECK(ActionSemantics_ApplyWarpTarget(&target, 1, 5)
              && target.xPos == 1 && target.yPos == 5,
          "Warp must move the target to the first selected tile");
    CHECK(ActionSemantics_ApplyWarpTarget(&target, 6, 2)
              && target.xPos == 6 && target.yPos == 2,
          "Warp must move the target to the second selected tile");
    CHECK(!ActionSemantics_ApplyWarpTarget(&target, -1, 2)
              && target.xPos == 6 && target.yPos == 2,
          "invalid Warp coordinates must preserve the target");

    CHECK(ActionSemantics_ApplyUnlockTarget(3, 4)
              && gBattleTarget.unit.xPos == 3
              && gBattleTarget.unit.yPos == 4
              && gBattleTarget.changeHP == 3
              && gBattleTarget.changePow == 4,
          "Unlock must lower the first selected door coordinate");
    CHECK(ActionSemantics_ApplyUnlockTarget(5, 1)
              && gBattleTarget.unit.xPos == 5
              && gBattleTarget.unit.yPos == 1
              && gBattleTarget.changeHP == 5
              && gBattleTarget.changePow == 1,
          "Unlock must lower the second selected door coordinate");
    CHECK(!ActionSemantics_ApplyUnlockTarget(3, 9)
              && gBattleTarget.unit.xPos == 5
              && gBattleTarget.unit.yPos == 1,
          "invalid Unlock coordinates must preserve the target");

    target.items[0] = 0x0101;
    target.items[1] = 0x0202;
    CHECK(ActionSemantics_ApplyHammerneTarget(&target, 1)
              && target.items[0] == 0x0101
              && target.items[1] == 0xFF02,
          "Hammerne must repair only the selected target slot");
    CHECK(ActionSemantics_ApplyHammerneTarget(&target, 0)
              && target.items[0] == 0xFF01,
          "Hammerne must support another repairable target slot");
    CHECK(!ActionSemantics_ApplyHammerneTarget(&target, 4)
              && target.items[4] == 0,
          "stale Hammerne slot must fail without mutation");

    caster.items[2] = ITEM_CHESTKEY | (3 << 8);
    sConsumedSlot = -1;
    CHECK(ActionSemantics_ConsumePickKey(&caster, 2)
              && caster.items[2] == (ITEM_CHESTKEY | (2 << 8))
              && sConsumedSlot == 2,
          "Pick key path must consume the selected item slot");
    sConsumedSlot = -1;
    CHECK(ActionSemantics_ConsumePickKey(&caster, 0xFF)
              && sConsumedSlot == -1,
          "Rogue Pick must not consume a stale key slot");
    CHECK(!ActionSemantics_ConsumePickKey(&caster, UNIT_ITEM_COUNT),
          "invalid Pick key slot must reject");

    CHECK(ActionSemantics_IsNormalSummonAvailable(&caster, false),
          "configured unmoved summoner must be available");
    CHECK(ActionSemantics_IsNormalSummonTarget(&caster, 2, 2, 2, 1)
              && ActionSemantics_IsNormalSummonTarget(
                  &caster,
                  2,
                  2,
                  3,
                  2),
          "normal Summon must accept multiple adjacent legal tiles");
    sUnitData[2][2] = 1;
    CHECK(ActionSemantics_IsNormalSummonTarget(&caster, 2, 3, 2, 2),
          "normal Summon must allow the summoner's vacated origin tile");
    sUnitData[2][2] = 0;
    CHECK(!ActionSemantics_IsNormalSummonTarget(&caster, 2, 2, 4, 2),
          "normal Summon must reject non-adjacent tiles");
    sUnitData[1][2] = 2;
    CHECK(!ActionSemantics_IsNormalSummonTarget(&caster, 2, 2, 2, 1),
          "normal Summon must reject an occupied tile");
    sUnitData[1][2] = 0;
    sFogData[1][2] = 0;
    gPlaySt.chapterVisionRange = 3;
    CHECK(!ActionSemantics_IsNormalSummonTarget(&caster, 2, 2, 2, 1),
          "normal Summon must reject a hidden tile");
    sFogData[1][2] = 1;

    gSummonConfig[0][0] = 0;
    gSummonConfig[0][1] = 0;
    CHECK(!ActionSemantics_IsNormalSummonAvailable(&caster, false),
          "missing gSummonConfig entry must reject");
    gSummonConfig[0][0] = CHARACTER_EWAN;
    gSummonConfig[0][1] = CHARACTER_SUMMON_EWAN;
    target.pCharacterData = &existingCharacter;
    target.pClassData = &unitClass;
    existingCharacter.number = CHARACTER_SUMMON_EWAN;
    target.state = 0;
    sExistingSummon = &target;
    CHECK(!ActionSemantics_IsNormalSummonAvailable(&caster, false),
          "available existing summon must reject");
    target.state = US_NOT_DEPLOYED;
    CHECK(ActionSemantics_IsNormalSummonAvailable(&caster, false)
              && target.state == US_NOT_DEPLOYED,
          "planner availability must not reactivate an unavailable summon");
    CHECK(ActionSemantics_IsNormalSummonAvailable(&caster, true)
              && !(target.state & US_UNAVAILABLE),
          "player usability must preserve existing summon reactivation");
    sExistingSummon = NULL;
    caster.state = US_HAS_MOVED;
    CHECK(!ActionSemantics_IsNormalSummonAvailable(&caster, false),
          "moved summoner must reject");
    caster.state = 0;
    unitClass.attributes = 0;
    CHECK(!ActionSemantics_IsNormalSummonAvailable(&caster, false),
          "unit without CA_SUMMON must reject");
    unitClass.attributes = CA_SUMMON;
    caster.index = FACTION_RED + 1;
    CHECK(!ActionSemantics_IsNormalSummonAvailable(&caster, false),
          "non-player summoner must not receive the player command");
    caster.index = 1;

    unitClass.number = CLASS_DEMON_KING;
    CHECK(ActionSemantics_IsDarkSummonAvailable(&caster),
          "Demon King with capacity must retain dark summon");
    sRedUnit.pCharacterData = &character;
    sRedUnit.pClassData = &unitClass;
    sRedUnitCount = 40;
    CHECK(ActionSemantics_IsDarkSummonAvailable(&caster),
          "dark summon must allow the exact forty-unit boundary");
    sRedUnitCount = 41;
    CHECK(!ActionSemantics_IsDarkSummonAvailable(&caster),
          "dark summon must reject a forty-first red unit");
    sRedUnitCount = 0;
    unitClass.number = 1;
    CHECK(!ActionSemantics_IsDarkSummonAvailable(&caster),
          "non-Demon-King unit must reject dark summon");

    puts("ACTION_SEMANTICS_HOST_TEST: PASS");
    return 0;
}
