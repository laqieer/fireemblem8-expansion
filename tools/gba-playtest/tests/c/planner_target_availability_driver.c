#include "global.h"

#include <stdio.h>
#include <string.h>

#include "bmidoten.h"
#include "bmmap.h"
#include "bmphase.h"
#include "bmtarget.h"
#include "bmtrick.h"
#include "bmunit.h"
#include "uiselecttarget.h"
#include "constants/items.h"
#include "constants/terrains.h"

#define CHECK(condition, message) \
    do \
    { \
        if (!(condition)) \
        { \
            fprintf(stderr, "PLANNER_TARGET_AVAILABILITY_TEST: FAIL: %s\n", message); \
            return 1; \
        } \
    } while (0)

struct Vec2 gBmMapSize;
u8** gBmMapUnit;
u8** gBmMapTerrain;
u8** gBmMapRange;
u8** gWorkingBmMap;

static struct CharacterData sCharacters[5];
static struct ClassData sClasses[5];
static struct Unit sActive;
static struct Unit sBlue;
static struct Unit sSecondBlue;
static struct Unit sGreen;
static struct Unit sEnemy;
static struct Trap sTraps[TRAP_MAX_COUNT];
static u8 sUnitData[8][8];
static u8* sUnitRows[8];
static u8 sTerrainData[8][8];
static u8* sTerrainRows[8];
static u8 sRangeData[8][8];
static u8* sRangeRows[8];
static struct SelectTarget sTargets[16];
static int sTargetCount;

int GetCurrentPhase(void)
{
    return FACTION_BLUE;
}

struct Unit* GetUnit(int id)
{
    switch (id)
    {
    case 1:
        return &sActive;
    case 2:
        return &sBlue;
    case 3:
        return &sSecondBlue;
    case 0x41:
        return &sGreen;
    case 0x81:
        return &sEnemy;
    default:
        return NULL;
    }
}

int GetUnitCurrentHp(struct Unit* unit)
{
    return unit->curHP;
}

int GetUnitMaxHp(struct Unit* unit)
{
    return unit->maxHP;
}

int GetUnitMagBy2Range(struct Unit* unit)
{
    (void)unit;
    return 3;
}

int GetItemIndex(int item)
{
    return item & 0xFF;
}

int GetItemMinRange(int item)
{
    (void)item;
    return 1;
}

int GetItemMaxRange(int item)
{
    (void)item;
    return 1;
}

s8 AreUnitsAllied(int left, int right)
{
    return (left & 0x80) == (right & 0x80);
}

s8 IsSameAllegiance(int left, int right)
{
    return (left & 0xC0) == (right & 0xC0);
}

s8 IsItemHammernable(int item)
{
    return item != 0 && (item & 0xFF00) != 0xFF00;
}

struct Trap* GetTrap(int id)
{
    return &sTraps[id];
}

struct Trap* GetTrapAt(int x, int y)
{
    int index;

    for (index = 0; index < TRAP_MAX_COUNT; index++)
    {
        if (sTraps[index].type == TRAP_NONE)
            break;
        if (sTraps[index].xPos == x && sTraps[index].yPos == y)
            return &sTraps[index];
    }
    return NULL;
}

void BmMapFill(u8** map, int value)
{
    int y;
    int x;

    for (y = 0; y < gBmMapSize.y; y++)
        for (x = 0; x < gBmMapSize.x; x++)
            map[y][x] = value;
}

void MapAddInRange(int x, int y, int range, int value)
{
    int iy;
    int ix;

    for (iy = 0; iy < gBmMapSize.y; iy++)
        for (ix = 0; ix < gBmMapSize.x; ix++)
            if (ABS(ix - x) + ABS(iy - y) <= range)
                gWorkingBmMap[iy][ix] += value;
}

void MapAddInBoundedRange(
    short x,
    short y,
    short minRange,
    short maxRange)
{
    int iy;
    int ix;

    for (iy = 0; iy < gBmMapSize.y; iy++)
    {
        for (ix = 0; ix < gBmMapSize.x; ix++)
        {
            int distance = ABS(ix - x) + ABS(iy - y);

            if (distance >= minRange && distance <= maxRange)
                gWorkingBmMap[iy][ix] = 1;
        }
    }
}

void InitTargets(int xRoot, int yRoot)
{
    (void)xRoot;
    (void)yRoot;
    sTargetCount = 0;
    gWorkingBmMap = gBmMapRange;
}

void AddTarget(int x, int y, int unitId, int targetId)
{
    struct SelectTarget* target = &sTargets[sTargetCount++];

    target->x = x;
    target->y = y;
    target->uid = unitId;
    target->extra = targetId;
}

static void PlaceUnit(struct Unit* unit, int id, int x, int y)
{
    unit->pCharacterData = &sCharacters[id == 0x41 ? 3 : id == 0x81 ? 4 : id - 1];
    unit->pClassData = &sClasses[id == 0x41 ? 3 : id == 0x81 ? 4 : id - 1];
    unit->index = id;
    unit->xPos = x;
    unit->yPos = y;
    unit->maxHP = 20;
    unit->curHP = 20;
    if (unit != &sActive)
        sUnitData[y][x] = id;
}

static void ResetFixture(void)
{
    int y;

    memset(&sActive, 0, sizeof(sActive));
    memset(&sBlue, 0, sizeof(sBlue));
    memset(&sSecondBlue, 0, sizeof(sSecondBlue));
    memset(&sGreen, 0, sizeof(sGreen));
    memset(&sEnemy, 0, sizeof(sEnemy));
    memset(sTraps, 0, sizeof(sTraps));
    memset(sUnitData, 0, sizeof(sUnitData));
    memset(sTerrainData, 0, sizeof(sTerrainData));
    memset(sRangeData, 0, sizeof(sRangeData));
    gBmMapSize.x = 8;
    gBmMapSize.y = 8;
    for (y = 0; y < 8; y++)
    {
        sUnitRows[y] = sUnitData[y];
        sTerrainRows[y] = sTerrainData[y];
        sRangeRows[y] = sRangeData[y];
    }
    gBmMapUnit = sUnitRows;
    gBmMapTerrain = sTerrainRows;
    gBmMapRange = sRangeRows;
    gWorkingBmMap = gBmMapRange;
    PlaceUnit(&sActive, 1, 2, 2);
}

int main(void)
{
    ResetFixture();
    PlaceUnit(&sBlue, 2, 3, 2);
    sBlue.curHP = 10;
    sBlue.state = US_NOT_DEPLOYED;
    MakeTargetListForLatona(&sActive);
    CHECK(sTargetCount == 0 && !HasLatonaTarget(&sActive),
          "Latona builder must exclude a not-deployed unit");
    sBlue.state = 0;
    MakeTargetListForLatona(&sActive);
    CHECK(sTargetCount == 1 && HasLatonaTarget(&sActive),
          "Latona builder must retain the available non-caster");
    sActive.curHP = 10;
    sBlue.curHP = sBlue.maxHP;
    MakeTargetListForLatona(&sActive);
    CHECK(sTargetCount == 0 && !HasLatonaTarget(&sActive),
          "Latona builder must exclude an injured caster");
    PlaceUnit(&sSecondBlue, 3, 4, 2);
    sBlue.curHP = 10;
    sSecondBlue.curHP = 10;
    MakeTargetListForLatona(&sActive);
    CHECK(sTargetCount == 2 && HasLatonaTarget(&sActive),
          "Latona builder must retain multiple eligible non-casters");

    ResetFixture();
    PlaceUnit(&sBlue, 2, 6, 2);
    sBlue.curHP = 10;
    MakeTargetListForRangedHeal(&sActive);
    CHECK(sTargetCount == 0
              && !HasRangedHealTargetAt(&sActive, 2, 2),
          "ranged heal builder must reject an ally outside MAG/2");
    sUnitData[2][6] = 0;
    sBlue.xPos = 5;
    sUnitData[2][5] = 2;
    MakeTargetListForRangedHeal(&sActive);
    CHECK(sTargetCount == 1
              && HasRangedHealTargetAt(&sActive, 2, 2),
          "ranged heal builder must retain an in-range injured ally");
    PlaceUnit(&sSecondBlue, 3, 2, 5);
    sSecondBlue.curHP = 10;
    MakeTargetListForRangedHeal(&sActive);
    CHECK(sTargetCount == 2
              && HasRangedHealTargetAt(&sActive, 2, 2),
          "ranged heal builder must retain multiple in-range allies");

    ResetFixture();
    PlaceUnit(&sBlue, 2, 3, 2);
    PlaceUnit(&sGreen, 0x41, 2, 3);
    PlaceUnit(&sEnemy, 0x81, 1, 2);
    sBlue.items[0] = ITEM_SWORD_IRON | (1 << 8);
    sGreen.items[0] = ITEM_SWORD_IRON | (1 << 8);
    sEnemy.items[0] = ITEM_SWORD_IRON | (1 << 8);
    MakeTargetListForHammerne(&sActive);
    CHECK(sTargetCount == 1 && sTargets[0].uid == 2,
          "Hammerne builder must retain only same-faction targets");
    CHECK(!IsUnitInHammerneTargetList(&sActive, &sGreen)
              && !IsUnitInHammerneTargetList(&sActive, &sEnemy),
          "green allied and enemy Hammerne targets must reject");

    ResetFixture();
    PlaceUnit(&sEnemy, 0x81, 1, 2);
    sTraps[0].type = TRAP_OBSTACLE;
    sTraps[0].xPos = 3;
    sTraps[0].yPos = 2;
    sTraps[0].extra = 20;
    sTraps[1].type = TRAP_OBSTACLE;
    sTraps[1].xPos = 2;
    sTraps[1].yPos = 3;
    sTraps[1].extra = 20;
    sTerrainData[2][3] = TERRAIN_SNAG;
    sTerrainData[3][2] = TERRAIN_SNAG;
    MakeTargetListForWeapon(&sActive, ITEM_SWORD_IRON);
    CHECK(sTargetCount == 3
              && (u8)sTargets[0].uid == 0x81
              && sTargets[1].uid == 0
              && sTargets[1].x == 3
              && sTargets[1].y == 2
              && sTargets[2].uid == 0
              && sTargets[2].x == 2
              && sTargets[2].y == 3,
          "weapon builder must retain unit and snag targets together");

    puts("PLANNER_TARGET_AVAILABILITY_TEST: PASS");
    return 0;
}
