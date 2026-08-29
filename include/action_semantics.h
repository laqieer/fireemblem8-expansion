#ifndef GUARD_ACTION_SEMANTICS_H
#define GUARD_ACTION_SEMANTICS_H

#include "global.h"

struct Unit;

bool ActionSemantics_IsStandingReachPosition(
    struct Unit* unit,
    int originX,
    int originY,
    int reach,
    int targetX,
    int targetY);
bool ActionSemantics_IsWarpDestination(
    struct Unit* caster,
    struct Unit* target,
    int casterX,
    int casterY,
    int targetX,
    int targetY);
bool ActionSemantics_IsUnlockStaffTarget(
    struct Unit* caster,
    int casterX,
    int casterY,
    int targetX,
    int targetY);
bool ActionSemantics_IsPickTarget(
    int originX,
    int originY,
    int targetX,
    int targetY);
bool ActionSemantics_IsKeyTarget(
    int originX,
    int originY,
    int targetX,
    int targetY);
bool ActionSemantics_IsTargetedItemTarget(
    struct Unit* unit,
    struct Unit* target,
    int item,
    int originX,
    int originY,
    int targetX,
    int targetY);
bool ActionSemantics_IsNormalSummonAvailable(
    struct Unit* unit,
    bool restoreUnavailable);
bool ActionSemantics_IsNormalSummonTarget(
    struct Unit* unit,
    int originX,
    int originY,
    int targetX,
    int targetY);
bool ActionSemantics_IsDarkSummonAvailable(struct Unit* unit);
bool ActionSemantics_ApplyTorchTarget(int targetX, int targetY);
bool ActionSemantics_ApplyWarpTarget(
    struct Unit* target,
    int targetX,
    int targetY);
bool ActionSemantics_ApplyUnlockTarget(int targetX, int targetY);
bool ActionSemantics_ApplyHammerneTarget(
    struct Unit* target,
    int targetItemSlot);
bool ActionSemantics_ConsumePickKey(
    struct Unit* unit,
    int itemSlot);

#endif /* GUARD_ACTION_SEMANTICS_H */
