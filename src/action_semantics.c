#include "global.h"

#if FE8_EXPANSION_AUTOPLAY_PLANNER && FE8_EXPANSION_DEBUG

#include "action_semantics.h"
#include "bmbattle.h"
#include "bmitem.h"
#include "bmmap.h"
#include "bmtrap.h"
#include "bmtrick.h"
#include "bmunit.h"
#include "eventinfo.h"

#include "constants/terrains.h"

s8 CanUnitCrossTerrain(struct Unit* unit, int terrain);

static bool IsMapPosition(int x, int y)
{
    return x >= 0 && x < gBmMapSize.x && y >= 0 && y < gBmMapSize.y;
}

static int Distance(int xA, int yA, int xB, int yB)
{
    return ABS(xA - xB) + ABS(yA - yB);
}

bool ActionSemantics_IsStandingReachPosition(
    struct Unit* unit,
    int originX,
    int originY,
    int reach,
    int targetX,
    int targetY)
{
    int distance;

    if (unit == NULL || !IsMapPosition(targetX, targetY))
        return false;
    distance = Distance(originX, originY, targetX, targetY);
    switch (reach)
    {
    case REACH_RANGE1:
        return distance == 1;

    case REACH_RANGE1 | REACH_RANGE2:
        return distance >= 1 && distance <= 2;

    case REACH_RANGE1 | REACH_RANGE2 | REACH_RANGE3:
        return distance >= 1 && distance <= 3;

    case REACH_RANGE2:
        return distance == 2;

    case REACH_RANGE2 | REACH_RANGE3:
        return distance >= 2 && distance <= 3;

    case REACH_RANGE3:
        return distance == 3;

    case REACH_RANGE3 | REACH_TO10:
        return distance >= 3 && distance <= 10;

    case REACH_RANGE1 | REACH_RANGE3:
        return distance == 1 || distance == 3;

    case REACH_RANGE1 | REACH_RANGE3 | REACH_TO10:
        return distance == 1 || (distance >= 3 && distance <= 10);

    case REACH_RANGE1 | REACH_RANGE2 | REACH_RANGE3 | REACH_TO10:
        return distance >= 1 && distance <= 10;

    case REACH_RANGE1 | REACH_TO10:
        return distance >= 1 && distance <= 4;

    case REACH_MAGBY2:
        return distance >= 1 && distance <= GetUnitMagBy2Range(unit);

    default:
        return false;
    }
}

bool ActionSemantics_IsWarpDestination(
    struct Unit* caster,
    struct Unit* target,
    int casterX,
    int casterY,
    int targetX,
    int targetY)
{
    if (caster == NULL || target == NULL || !IsMapPosition(targetX, targetY))
        return false;
    if (targetX == casterX && targetY == casterY)
        return false;
    if (Distance(target->xPos, target->yPos, targetX, targetY) < 1
        || Distance(target->xPos, target->yPos, targetX, targetY)
            > GetUnitMagBy2Range(caster))
        return false;
    if (gBmMapUnit[targetY][targetX] != 0
        && (targetX != caster->xPos || targetY != caster->yPos))
        return false;
    if (!CanUnitCrossTerrain(target, gBmMapTerrain[targetY][targetX]))
        return false;
    if (gPlaySt.chapterVisionRange != 0
        && gBmMapFog != NULL
        && gBmMapFog[targetY][targetX] == 0)
        return false;
    return true;
}

bool ActionSemantics_IsUnlockStaffTarget(
    struct Unit* caster,
    int casterX,
    int casterY,
    int targetX,
    int targetY)
{
    int distance;

    if (caster == NULL || !IsMapPosition(targetX, targetY))
        return false;
    distance = Distance(casterX, casterY, targetX, targetY);
    return distance >= 1
        && distance <= 2
        && gBmMapTerrain[targetY][targetX] == TERRAIN_DOOR
        && IsThereClosedDoorAt(targetX, targetY);
}

bool ActionSemantics_IsPickTarget(
    int originX,
    int originY,
    int targetX,
    int targetY)
{
    int terrain;

    if (!IsMapPosition(targetX, targetY))
        return false;
    terrain = gBmMapTerrain[targetY][targetX];
    if (targetX == originX && targetY == originY)
        return terrain == TERRAIN_CHEST_FULL;
    return Distance(originX, originY, targetX, targetY) == 1
        && (terrain == TERRAIN_DOOR
            || terrain == TERRAIN_BRIDGE_14);
}

bool ActionSemantics_IsKeyTarget(
    int originX,
    int originY,
    int targetX,
    int targetY)
{
    int terrain;

    if (!ActionSemantics_IsPickTarget(
            originX,
            originY,
            targetX,
            targetY))
        return false;
    terrain = gBmMapTerrain[targetY][targetX];
    if (terrain == TERRAIN_CHEST_FULL)
        return IsThereClosedChestAt(targetX, targetY);
    return IsThereClosedDoorAt(targetX, targetY);
}

bool ActionSemantics_ApplyTorchTarget(int targetX, int targetY)
{
    if (!IsMapPosition(targetX, targetY))
        return false;
    AddTrap(targetX, targetY, TRAP_TORCHLIGHT, 8);
    return true;
}

bool ActionSemantics_ApplyWarpTarget(
    struct Unit* target,
    int targetX,
    int targetY)
{
    if (target == NULL || !IsMapPosition(targetX, targetY))
        return false;
    target->xPos = targetX;
    target->yPos = targetY;
    return true;
}

bool ActionSemantics_ApplyUnlockTarget(int targetX, int targetY)
{
    if (!IsMapPosition(targetX, targetY))
        return false;
    gBattleTarget.unit.xPos = targetX;
    gBattleTarget.unit.yPos = targetY;
    gBattleTarget.changeHP = targetX;
    gBattleTarget.changePow = targetY;
    return true;
}

bool ActionSemantics_ApplyHammerneTarget(
    struct Unit* target,
    int targetItemSlot)
{
    if (target == NULL
        || targetItemSlot < 0
        || targetItemSlot >= UNIT_ITEM_COUNT
        || !IsItemHammernable(target->items[targetItemSlot]))
        return false;
    target->items[targetItemSlot] =
        MakeNewItem(target->items[targetItemSlot]);
    return true;
}

bool ActionSemantics_ConsumePickKey(
    struct Unit* unit,
    int itemSlot)
{
    if (unit == NULL)
        return false;
    if (itemSlot == 0xFF)
        return true;
    if (itemSlot < 0
        || itemSlot >= UNIT_ITEM_COUNT
        || unit->items[itemSlot] == 0)
        return false;
    UnitUpdateUsedItem(unit, itemSlot);
    return true;
}

#endif
