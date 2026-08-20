#include "global.h"

#include <string.h>

#include "bmitem.h"
#include "bmmap.h"
#include "bmphase.h"
#include "bmunit.h"
#include "uiselecttarget.h"

#include "expansion_aoe.h"

EWRAM_DATA static u8 sItemDispatchActive = 0;

static int ExpansionAoE_BoundedLen(const char* text, int capacity)
{
    int length = 0;

    while (length < capacity && text[length] != '\0')
        length++;

    return length;
}

static int ExpansionAoE_Abs(int value)
{
    return value < 0 ? -value : value;
}

static int ExpansionAoE_MarkUnitSeen(
    u8* seen,
    ExpansionAoEUnitId unitId)
{
    u8 mask = (u8)(1 << (unitId & 7));
    u8* slot = &seen[unitId >> 3];

    if ((*slot & mask) != 0)
        return 0;

    *slot |= mask;
    return 1;
}

static int ExpansionAoE_TargetCompare(
    const struct ExpansionAoETarget* left,
    const struct ExpansionAoETarget* right)
{
    if (left->distance != right->distance)
        return (int)left->distance - (int)right->distance;

    if (left->y != right->y)
        return (int)left->y - (int)right->y;

    if (left->x != right->x)
        return (int)left->x - (int)right->x;

    return (int)left->unitId - (int)right->unitId;
}

static void ExpansionAoE_InsertTarget(
    struct ExpansionAoETargetSet* out,
    const struct ExpansionAoETarget* target)
{
    int index;
    int limit;

    if (out->count < EXPANSION_AOE_MAX_TARGETS)
    {
        index = out->count;
        out->count++;
    }
    else
    {
        if (ExpansionAoE_TargetCompare(
                target, &out->targets[EXPANSION_AOE_MAX_TARGETS - 1]) >= 0)
            return;

        index = EXPANSION_AOE_MAX_TARGETS - 1;
    }

    limit = index;
    while (limit > 0
        && ExpansionAoE_TargetCompare(target, &out->targets[limit - 1]) < 0)
    {
        out->targets[limit] = out->targets[limit - 1];
        limit--;
    }

    out->targets[limit] = *target;
}

static int ExpansionAoE_Distance(
    const struct ExpansionAoEShape* shape,
    int xOrigin,
    int yOrigin,
    int x,
    int y)
{
    int dx = ExpansionAoE_Abs(x - xOrigin);
    int dy = ExpansionAoE_Abs(y - yOrigin);

    switch (shape->kind)
    {
    case EXPANSION_AOE_SHAPE_DIAMOND:
        return dx + dy;

    case EXPANSION_AOE_SHAPE_SQUARE:
        return dx > dy ? dx : dy;

    case EXPANSION_AOE_SHAPE_CROSS:
        if (dx != 0 && dy != 0)
            return -1;

        return dx + dy;
    }

    return -1;
}

static int ExpansionAoE_ResolveUnitPosition(
    struct Unit* unit,
    int* xOut,
    int* yOut)
{
    struct Unit* carrier;
    int x;
    int y;

    if (!UNIT_IS_VALID(unit) || xOut == NULL || yOut == NULL)
        return 0;

    if (unit->state & US_RESCUED)
    {
        carrier = GetUnit(unit->rescue);
        if (!UNIT_IS_VALID(carrier)
            || !(carrier->state & US_RESCUING)
            || (ExpansionAoEUnitId)carrier->rescue
                != (ExpansionAoEUnitId)unit->index)
            return 0;
        if (carrier->xPos < 0 || carrier->yPos < 0
            || carrier->xPos >= gBmMapSize.x
            || carrier->yPos >= gBmMapSize.y)
            return 0;

        x = carrier->xPos;
        y = carrier->yPos;
    }
    else
    {
        x = unit->xPos;
        y = unit->yPos;
    }

    if (x < 0 || y < 0 || x >= gBmMapSize.x || y >= gBmMapSize.y)
        return 0;

    *xOut = x;
    *yOut = y;
    return 1;
}

static enum ExpansionAoEResult ExpansionAoE_ResolveOrigin(
    const struct ExpansionAoEOrigin* origin,
    int* xOut,
    int* yOut)
{
    struct Unit* unit;
    int x;
    int y;

    if (origin == NULL || xOut == NULL || yOut == NULL)
        return EXPANSION_AOE_ERR_NULL_ARG;

    if (origin->kind >= EXPANSION_AOE_ORIGIN_COUNT)
        return EXPANSION_AOE_ERR_ORIGIN;

    if (origin->kind == EXPANSION_AOE_ORIGIN_UNIT)
    {
        if (origin->sourceUnitId == EXPANSION_AOE_UNIT_NONE)
            return EXPANSION_AOE_ERR_ORIGIN;

        unit = GetUnit(origin->sourceUnitId);
        if (!ExpansionAoE_ResolveUnitPosition(unit, &x, &y))
            return EXPANSION_AOE_ERR_ORIGIN;
    }
    else
    {
        x = origin->x;
        y = origin->y;
    }

    if (x < 0 || y < 0 || x >= gBmMapSize.x || y >= gBmMapSize.y)
        return EXPANSION_AOE_ERR_ORIGIN;

    *xOut = x;
    *yOut = y;
    return EXPANSION_AOE_OK;
}

enum ExpansionAoEResult ExpansionAoE_ValidateShape(const struct ExpansionAoEShape* shape)
{
    if (shape == NULL)
        return EXPANSION_AOE_ERR_NULL_ARG;

    if (shape->kind >= EXPANSION_AOE_SHAPE_COUNT)
        return EXPANSION_AOE_ERR_SHAPE;

    if (shape->minRange > shape->maxRange
        || shape->maxRange > EXPANSION_AOE_MAX_RADIUS)
        return EXPANSION_AOE_ERR_SHAPE;

    return EXPANSION_AOE_OK;
}

enum ExpansionAoEResult ExpansionAoE_ValidateFilter(
    const struct ExpansionAoEOrigin* origin,
    const struct ExpansionAoETargetFilter* filter)
{
    u8 relations;

    if (origin == NULL || filter == NULL)
        return EXPANSION_AOE_ERR_NULL_ARG;

    relations = filter->relationMask;
    if (relations == 0
        || (relations & ~(EXPANSION_AOE_TARGET_ANY
            | EXPANSION_AOE_TARGET_SOURCE
            | EXPANSION_AOE_TARGET_ALLIES
            | EXPANSION_AOE_TARGET_ENEMIES)) != 0)
        return EXPANSION_AOE_ERR_FILTER;

    if ((relations & EXPANSION_AOE_TARGET_ANY) != 0
        && relations != EXPANSION_AOE_TARGET_ANY)
        return EXPANSION_AOE_ERR_FILTER;

    if ((filter->conditionMask & ~EXPANSION_AOE_CONDITION_ALL) != 0)
        return EXPANSION_AOE_ERR_FILTER;

    if (relations != EXPANSION_AOE_TARGET_ANY
        && origin->sourceUnitId == EXPANSION_AOE_UNIT_NONE)
        return EXPANSION_AOE_ERR_FILTER;

    if ((filter->conditionMask & EXPANSION_AOE_CONDITION_REQUIRE_STATUS)
        && (filter->conditionMask & EXPANSION_AOE_CONDITION_REQUIRE_NO_STATUS))
        return EXPANSION_AOE_ERR_FILTER;

    return EXPANSION_AOE_OK;
}

static int ExpansionAoE_RelationMatches(
    const struct ExpansionAoEOrigin* origin,
    const struct ExpansionAoETargetFilter* filter,
    ExpansionAoEUnitId candidateUnitId)
{
    if (filter->relationMask == EXPANSION_AOE_TARGET_ANY)
        return 1;

    if (candidateUnitId == origin->sourceUnitId)
        return (filter->relationMask & EXPANSION_AOE_TARGET_SOURCE) != 0;

    if (AreUnitsAllied(origin->sourceUnitId, candidateUnitId))
        return (filter->relationMask & EXPANSION_AOE_TARGET_ALLIES) != 0;

    return (filter->relationMask & EXPANSION_AOE_TARGET_ENEMIES) != 0;
}

static enum ExpansionAoEFilterDecision ExpansionAoE_UnitMatches(
    const struct ExpansionAoEOrigin* origin,
    const struct ExpansionAoETargetFilter* filter,
    struct Unit* unit)
{
    enum ExpansionAoEFilterDecision decision;
    int decisionValue;
    u32 state;

    if (!UNIT_IS_VALID(unit))
        return EXPANSION_AOE_FILTER_REJECT;

    state = unit->state;

    if (!(filter->conditionMask & EXPANSION_AOE_CONDITION_INCLUDE_HIDDEN)
        && (state & US_HIDDEN))
        return EXPANSION_AOE_FILTER_REJECT;

    if (!(filter->conditionMask & EXPANSION_AOE_CONDITION_INCLUDE_DEAD)
        && (state & US_DEAD))
        return EXPANSION_AOE_FILTER_REJECT;

    if (!(filter->conditionMask & EXPANSION_AOE_CONDITION_INCLUDE_UNDEPLOYED)
        && (state & US_NOT_DEPLOYED))
        return EXPANSION_AOE_FILTER_REJECT;

    if (state & US_BIT16)
        return EXPANSION_AOE_FILTER_REJECT;

    if (!(filter->conditionMask & EXPANSION_AOE_CONDITION_INCLUDE_RESCUED)
        && (state & US_RESCUED))
        return EXPANSION_AOE_FILTER_REJECT;

    if (!ExpansionAoE_RelationMatches(origin, filter, (ExpansionAoEUnitId)unit->index))
        return EXPANSION_AOE_FILTER_REJECT;

    if ((filter->conditionMask & EXPANSION_AOE_CONDITION_REQUIRE_DAMAGED)
        && GetUnitCurrentHp(unit) >= GetUnitMaxHp(unit))
        return EXPANSION_AOE_FILTER_REJECT;

    if ((filter->conditionMask & EXPANSION_AOE_CONDITION_REQUIRE_STATUS)
        && unit->statusIndex == UNIT_STATUS_NONE)
        return EXPANSION_AOE_FILTER_REJECT;

    if ((filter->conditionMask & EXPANSION_AOE_CONDITION_REQUIRE_NO_STATUS)
        && unit->statusIndex != UNIT_STATUS_NONE)
        return EXPANSION_AOE_FILTER_REJECT;

    if (filter->predicate == NULL)
        return EXPANSION_AOE_FILTER_ACCEPT;

    decision = filter->predicate(
        origin->sourceUnitId, (ExpansionAoEUnitId)unit->index);
    decisionValue = decision;

    if (decisionValue < EXPANSION_AOE_FILTER_REJECT
        || decisionValue > EXPANSION_AOE_FILTER_ERROR)
        return EXPANSION_AOE_FILTER_ERROR;

    return decision;
}

enum ExpansionAoEResult ExpansionAoE_BuildTargetSet(
    const struct ExpansionAoEShape* shape,
    const struct ExpansionAoEOrigin* origin,
    const struct ExpansionAoETargetFilter* filter,
    u8 buildFlags,
    struct ExpansionAoETargetSet* out)
{
    enum ExpansionAoEResult result;
    enum ExpansionAoEFilterDecision decision;
    struct ExpansionAoETarget target;
    struct Unit* unit;
    u8 seen[0x20];
    int xOrigin;
    int yOrigin;
    int x;
    int y;
    int distance;
    int unitSlot;
    int unitX;
    int unitY;
    ExpansionAoEUnitId unitId;

    if (out == NULL)
        return EXPANSION_AOE_ERR_NULL_ARG;

    memset(out, 0, sizeof(*out));
    memset(seen, 0, sizeof(seen));
    out->complete = 1;
    out->order = EXPANSION_AOE_ORDER_DISTANCE_Y_X_UNIT;

    if ((buildFlags & ~EXPANSION_AOE_BUILD_ALL) != 0)
        return EXPANSION_AOE_ERR_FILTER;

    result = ExpansionAoE_ValidateShape(shape);
    if (result != EXPANSION_AOE_OK)
        return result;

    result = ExpansionAoE_ValidateFilter(origin, filter);
    if (result != EXPANSION_AOE_OK)
        return result;

    result = ExpansionAoE_ResolveOrigin(origin, &xOrigin, &yOrigin);
    if (result != EXPANSION_AOE_OK)
        return result;

    if (buildFlags & EXPANSION_AOE_BUILD_RANGE_MAP)
        BmMapFill(gBmMapRange, 0);

    if (buildFlags & EXPANSION_AOE_BUILD_LEGACY_TARGET_LIST)
        InitTargets(xOrigin, yOrigin);

    for (y = 0; y < gBmMapSize.y; y++)
    {
        for (x = 0; x < gBmMapSize.x; x++)
        {
            distance = ExpansionAoE_Distance(shape, xOrigin, yOrigin, x, y);
            if (distance < shape->minRange || distance > shape->maxRange)
                continue;

            out->rangeTileCount++;

            if (buildFlags & EXPANSION_AOE_BUILD_RANGE_MAP)
                gBmMapRange[y][x] = 1;

        }
    }

    /*
     * The unit map is a presentation/indexing aid, not the authoritative
     * unit census: UnitBeginAction removes the actor and rescued/hidden units
     * are intentionally absent. Iterate stable unit slots instead, resolving
     * rescued units at their carrier's position and deduplicating aliases by
     * stable unit ID before applying filters.
     */
    for (unitSlot = 1; unitSlot < 0x100; unitSlot++)
    {
        unit = GetUnit(unitSlot);
        if (!UNIT_IS_VALID(unit))
            continue;

        unitId = (ExpansionAoEUnitId)unit->index;
        if (unitId == EXPANSION_AOE_UNIT_NONE
            || !ExpansionAoE_MarkUnitSeen(seen, unitId))
            continue;

        decision = ExpansionAoE_UnitMatches(origin, filter, unit);
        if (decision == EXPANSION_AOE_FILTER_ERROR)
        {
            out->complete = 0;
            if (buildFlags & EXPANSION_AOE_BUILD_RANGE_MAP)
                BmMapFill(gBmMapRange, 0);
            return EXPANSION_AOE_ERR_FILTER;
        }

        if (decision != EXPANSION_AOE_FILTER_ACCEPT
            || !ExpansionAoE_ResolveUnitPosition(unit, &unitX, &unitY))
            continue;

        distance = ExpansionAoE_Distance(shape, xOrigin, yOrigin, unitX, unitY);
        if (distance < shape->minRange || distance > shape->maxRange)
            continue;

        target.unitId = unitId;
        target.distance = (u8)distance;
        target.x = (s8)unitX;
        target.y = (s8)unitY;
        out->totalCount++;
        ExpansionAoE_InsertTarget(out, &target);
    }

    if (out->totalCount > EXPANSION_AOE_MAX_TARGETS)
        out->complete = 0;

    if ((buildFlags & EXPANSION_AOE_BUILD_LEGACY_TARGET_LIST)
        && out->complete)
    {
        for (unitSlot = 0; unitSlot < out->count; unitSlot++)
        {
            AddTarget(
                out->targets[unitSlot].x,
                out->targets[unitSlot].y,
                out->targets[unitSlot].unitId,
                out->targets[unitSlot].distance);
        }
    }

    return out->complete ? EXPANSION_AOE_OK : EXPANSION_AOE_ERR_CAPACITY;
}

static enum ExpansionAoEResult ExpansionAoE_ValidateEffect(
    const struct ExpansionAoEEffectSpec* effect)
{
    if (effect == NULL || effect->apply == NULL)
        return EXPANSION_AOE_ERR_NULL_ARG;

    if (effect->partialFailurePolicy >= EXPANSION_AOE_PARTIAL_COUNT
        || effect->expPolicy >= EXPANSION_AOE_EXP_COUNT
        || effect->animationPolicy >= EXPANSION_AOE_ANIMATION_COUNT
        || effect->eventPolicy >= EXPANSION_AOE_EVENT_COUNT)
        return EXPANSION_AOE_ERR_EFFECT;

    if (effect->expPolicy == EXPANSION_AOE_EXP_NONE)
    {
        if (effect->awardExp != NULL || effect->expPerApplied != 0 || effect->expCap != 0)
            return EXPANSION_AOE_ERR_EFFECT;
    }
    else if (effect->awardExp == NULL || effect->expPerApplied == 0 || effect->expCap == 0)
    {
        return EXPANSION_AOE_ERR_EFFECT;
    }

    if ((effect->animationPolicy == EXPANSION_AOE_ANIMATION_NONE)
        != (effect->animate == NULL))
        return EXPANSION_AOE_ERR_EFFECT;

    if ((effect->eventPolicy == EXPANSION_AOE_EVENT_NONE)
        != (effect->invokeEvent == NULL))
        return EXPANSION_AOE_ERR_EFFECT;

    return EXPANSION_AOE_OK;
}

enum ExpansionAoEResult ExpansionAoE_Execute(
    const struct ExpansionAoETargetSet* targets,
    const struct ExpansionAoEEffectContext* context,
    const struct ExpansionAoEEffectSpec* effect,
    struct ExpansionAoEExecutionResult* out)
{
    enum ExpansionAoEResult validation;
    enum ExpansionAoETargetEffectResult targetResult;
    unsigned int exp;
    int index;

    if (targets == NULL || context == NULL || out == NULL)
        return EXPANSION_AOE_ERR_NULL_ARG;

    memset(out, 0, sizeof(*out));
    out->firstFailureIndex = EXPANSION_AOE_INDEX_NONE;

    validation = ExpansionAoE_ValidateEffect(effect);
    if (validation != EXPANSION_AOE_OK)
        return validation;

    if (!targets->complete || targets->count > EXPANSION_AOE_MAX_TARGETS)
        return EXPANSION_AOE_ERR_INCOMPLETE;

    for (index = 0; index < targets->count; index++)
    {
        out->attemptedCount++;
        targetResult = effect->apply(context, &targets->targets[index]);

        if (targetResult == EXPANSION_AOE_TARGET_APPLIED)
        {
            out->appliedCount++;

            if (effect->animationPolicy == EXPANSION_AOE_ANIMATION_PER_TARGET)
            {
                effect->animate(context, targets->targets[index].unitId, (u8)index);
                out->animationCallCount++;
            }
        }
        else if (targetResult == EXPANSION_AOE_TARGET_SKIPPED)
        {
            out->skippedCount++;
        }
        else
        {
            out->failedCount++;
            if (out->firstFailureIndex == EXPANSION_AOE_INDEX_NONE)
                out->firstFailureIndex = (u8)index;

            if (effect->partialFailurePolicy == EXPANSION_AOE_PARTIAL_STOP)
            {
                out->outcome = EXPANSION_AOE_EXECUTION_STOPPED;
                break;
            }
        }
    }

    if (out->outcome != EXPANSION_AOE_EXECUTION_STOPPED)
    {
        if (out->failedCount == 0)
            out->outcome = EXPANSION_AOE_EXECUTION_OK;
        else if (out->appliedCount != 0 || out->skippedCount != 0)
            out->outcome = EXPANSION_AOE_EXECUTION_PARTIAL;
        else
            out->outcome = EXPANSION_AOE_EXECUTION_FAILED;
    }

    if (effect->expPolicy != EXPANSION_AOE_EXP_NONE && out->appliedCount != 0)
    {
        if (effect->expPolicy == EXPANSION_AOE_EXP_ONCE)
            exp = effect->expPerApplied;
        else
            exp = (unsigned int)out->appliedCount * effect->expPerApplied;

        if (exp > effect->expCap)
            exp = effect->expCap;

        out->expAwarded = (u16)exp;
        effect->awardExp(context->origin.sourceUnitId, out->expAwarded);
    }

    if (effect->animationPolicy == EXPANSION_AOE_ANIMATION_BATCH
        && out->appliedCount != 0)
    {
        effect->animate(context, EXPANSION_AOE_UNIT_NONE, EXPANSION_AOE_INDEX_NONE);
        out->animationCallCount++;
    }

    if (effect->eventPolicy == EXPANSION_AOE_EVENT_ON_ANY_APPLIED
        && out->appliedCount != 0)
    {
        effect->invokeEvent(context, out);
        out->eventCallCount++;
    }
    else if (effect->eventPolicy == EXPANSION_AOE_EVENT_ON_COMPLETE_SUCCESS
        && out->appliedCount != 0
        && out->failedCount == 0
        && out->attemptedCount == targets->count)
    {
        effect->invokeEvent(context, out);
        out->eventCallCount++;
    }

    return EXPANSION_AOE_OK;
}

const struct ExpansionAoEItemRouteTable* __attribute__((weak))
ExpansionAoE_GetItemRouteTable(void)
{
    static const struct ExpansionAoEItemRouteTable sEmptyTable = {NULL, 0, {0, 0, 0}};

    return &sEmptyTable;
}

enum ExpansionAoERouteResult ExpansionAoE_ValidateItemRouteTable(
    const struct ExpansionAoEItemRouteTable* table)
{
    const struct ExpansionAoEItemRoute* route;
    int keyLength;
    int index;
    int prior;

    if (table == NULL)
        return EXPANSION_AOE_ROUTE_ERR_NULL_ARG;

    if (table->count > EXPANSION_AOE_ITEM_ROUTE_MAX)
        return EXPANSION_AOE_ROUTE_ERR_CAPACITY;

    if (table->count != 0 && table->routes == NULL)
        return EXPANSION_AOE_ROUTE_ERR_NULL_ARG;

    for (index = 0; index < table->count; index++)
    {
        route = &table->routes[index];
        if (route->key == NULL || route->handler == NULL)
            return EXPANSION_AOE_ROUTE_ERR_NULL_ARG;

        keyLength = ExpansionAoE_BoundedLen(route->key, EXPANSION_AOE_ROUTE_KEY_SIZE);
        if (keyLength == 0 || keyLength >= EXPANSION_AOE_ROUTE_KEY_SIZE)
            return EXPANSION_AOE_ROUTE_ERR_KEY_LENGTH;

        if (route->itemId == ITEM_ID_SENTINEL
            || route->itemId > ITEM_ID_CONFIGURED_CAP)
            return EXPANSION_AOE_ROUTE_ERR_ITEM;

        if (route->aiPolicy >= EXPANSION_AOE_AI_POLICY_COUNT
            || route->savePolicy != EXPANSION_AOE_SAVE_ATOMIC_REBUILD)
            return EXPANSION_AOE_ROUTE_ERR_POLICY;

        for (prior = 0; prior < index; prior++)
        {
            if (table->routes[prior].itemId == route->itemId
                || strcmp(table->routes[prior].key, route->key) == 0)
                return EXPANSION_AOE_ROUTE_ERR_DUPLICATE;
        }
    }

    return EXPANSION_AOE_ROUTE_OK;
}

static const struct ExpansionAoEItemRouteTable* ExpansionAoE_LoadItemRouteTable(void)
{
    const struct ExpansionAoEItemRouteTable* table;

    table = ExpansionAoE_GetItemRouteTable();
    if (ExpansionAoE_ValidateItemRouteTable(table) != EXPANSION_AOE_ROUTE_OK)
        return NULL;

    return table;
}

int ExpansionAoE_ItemRouteCount(void)
{
    const struct ExpansionAoEItemRouteTable* table = ExpansionAoE_LoadItemRouteTable();

    return table == NULL ? 0 : table->count;
}

ItemId ExpansionAoE_ItemRouteItemAt(int index)
{
    const struct ExpansionAoEItemRouteTable* table = ExpansionAoE_LoadItemRouteTable();

    if (table == NULL || index < 0 || index >= table->count)
        return ITEM_ID_SENTINEL;

    return table->routes[index].itemId;
}

const char* ExpansionAoE_ItemRouteKeyAt(int index)
{
    const struct ExpansionAoEItemRouteTable* table = ExpansionAoE_LoadItemRouteTable();

    if (table == NULL || index < 0 || index >= table->count)
        return NULL;

    return table->routes[index].key;
}

enum ExpansionAoEItemDispatchResult ExpansionAoE_DispatchItem(
    const struct ExpansionAoEItemContext* context)
{
    const struct ExpansionAoEItemRouteTable* table;
    const struct ExpansionAoEItemRoute* route;
    enum ExpansionAoEItemDispatchResult result;
    int index;

    if (context == NULL || context->phase >= EXPANSION_AOE_ITEM_PHASE_COUNT)
        return EXPANSION_AOE_ITEM_ERROR;

    if (sItemDispatchActive)
        return EXPANSION_AOE_ITEM_ERROR;

    table = ExpansionAoE_LoadItemRouteTable();
    if (table == NULL)
        return EXPANSION_AOE_ITEM_ERROR;

    for (index = 0; index < table->count; index++)
    {
        route = &table->routes[index];
        if (route->itemId != context->itemId)
            continue;

        if (context->phase == EXPANSION_AOE_ITEM_AI_SELECT
            && route->aiPolicy == EXPANSION_AOE_AI_NEVER)
            return EXPANSION_AOE_ITEM_REJECTED;

        sItemDispatchActive = 1;
        result = route->handler(context);
        sItemDispatchActive = 0;

        if (result == EXPANSION_AOE_ITEM_NOT_HANDLED)
            return EXPANSION_AOE_ITEM_ERROR;

        return result;
    }

    return EXPANSION_AOE_ITEM_NOT_HANDLED;
}

void ExpansionAoE_InitItemContext(
    struct ExpansionAoEItemContext* context,
    enum ExpansionAoEItemPhase phase,
    struct Unit* actor,
    int item,
    int itemSlot)
{
    if (context == NULL)
        return;

    memset(context, 0, sizeof(*context));
    context->phase = (u8)phase;
    context->actorUnitId = actor == NULL
        ? EXPANSION_AOE_UNIT_NONE
        : (ExpansionAoEUnitId)actor->index;
    context->targetUnitId = EXPANSION_AOE_UNIT_NONE;
    context->itemSlot = itemSlot < 0 ? EXPANSION_AOE_INDEX_NONE : (u8)itemSlot;
    context->itemId = (ItemId)GetItemIndex(item);
    context->item = (u16)item;
    context->originX = actor == NULL ? -1 : actor->xPos;
    context->originY = actor == NULL ? -1 : actor->yPos;
}
