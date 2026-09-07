/*
 * Issue #42 native driver. It links the real expansion_aoe.c and
 * expansion_aoe_reference.c, while providing only the engine storage/accessor
 * stubs needed to exercise the public API.
 */
#include <stdio.h>
#include <string.h>

#include "global.h"
#include "bmmap.h"
#include "bmunit.h"
#include "expansion_aoe.h"
#include "expansion_aoe_reference.h"
#include "uiselecttarget.h"

#define CHECK(cond, msg) \
    do { \
        if (!(cond)) { \
            fprintf(stderr, "AOE_HOST_TEST: FAIL: %s\n", msg); \
            return 1; \
        } \
    } while (0)

#define MAP_W 12
#define MAP_H 12
#define UNIT_TABLE_SIZE 0xC0

struct Vec2 gBmMapSize;
static u8 sMapUnitData[MAP_H][MAP_W];
static u8 sMapRangeData[MAP_H][MAP_W];
static u8* sMapUnitRows[MAP_H];
static u8* sMapRangeRows[MAP_H];
u8** gBmMapUnit = sMapUnitRows;
u8** gBmMapRange = sMapRangeRows;

static struct Unit sUnits[UNIT_TABLE_SIZE];
static struct CharacterData sCharacters[UNIT_TABLE_SIZE];
static struct ClassData sClasses[UNIT_TABLE_SIZE];
static struct Unit* sUnitLookup[UNIT_TABLE_SIZE];
static struct SelectTarget sLegacyTargets[MAX_TARGET_LIST_COUNT];
static int sLegacyTargetCount;

void BmMapFill(u8** map, int value)
{
    int x;
    int y;

    for (y = 0; y < gBmMapSize.y; y++)
        for (x = 0; x < gBmMapSize.x; x++)
            map[y][x] = (u8)value;
}

struct Unit* GetUnit(int id)
{
    if (id < 0 || id >= UNIT_TABLE_SIZE)
        return NULL;

    return sUnitLookup[id];
}

s8 AreUnitsAllied(int left, int right)
{
    return (left & 0xC0) == (right & 0xC0);
}

int GetUnitCurrentHp(struct Unit* unit)
{
    return unit->curHP;
}

int GetUnitMaxHp(struct Unit* unit)
{
    return unit->maxHP;
}

void AddUnitHp(struct Unit* unit, int amount)
{
    int hp = unit->curHP + amount;

    if (hp > unit->maxHP)
        hp = unit->maxHP;

    if (hp < 0)
        hp = 0;

    unit->curHP = hp;
}

void SetUnitHp(struct Unit* unit, int hp)
{
    unit->curHP = hp;
}

int GetItemIndex(int item)
{
    return item & 0xFF;
}

void InitTargets(int xRoot, int yRoot)
{
    (void)xRoot;
    (void)yRoot;
    sLegacyTargetCount = 0;
}

void AddTarget(int x, int y, int unitId, int extra)
{
    struct SelectTarget* target = &sLegacyTargets[sLegacyTargetCount++];

    target->x = x;
    target->y = y;
    target->uid = unitId;
    target->extra = extra;
}

int GetSelectTargetCount(void)
{
    return sLegacyTargetCount;
}

struct SelectTarget* GetTarget(int index)
{
    return &sLegacyTargets[index];
}

static void ResetWorld(void)
{
    int index;

    memset(sMapUnitData, 0, sizeof(sMapUnitData));
    memset(sMapRangeData, 0, sizeof(sMapRangeData));
    memset(sUnits, 0, sizeof(sUnits));
    memset(sCharacters, 0, sizeof(sCharacters));
    memset(sClasses, 0, sizeof(sClasses));
    memset(sLegacyTargets, 0, sizeof(sLegacyTargets));

    gBmMapSize.x = MAP_W;
    gBmMapSize.y = MAP_H;
    sLegacyTargetCount = 0;

    for (index = 0; index < MAP_H; index++)
    {
        sMapUnitRows[index] = sMapUnitData[index];
        sMapRangeRows[index] = sMapRangeData[index];
    }

    for (index = 0; index < UNIT_TABLE_SIZE; index++)
        sUnitLookup[index] = &sUnits[index];
}

static void PutUnit(int id, int x, int y, int hp, int maxHp)
{
    struct Unit* unit = &sUnits[id];

    memset(unit, 0, sizeof(*unit));
    unit->pCharacterData = &sCharacters[id];
    unit->pClassData = &sClasses[id];
    unit->index = id;
    unit->xPos = x;
    unit->yPos = y;
    unit->curHP = hp;
    unit->maxHP = maxHp;
    if (x >= 0 && y >= 0 && x < MAP_W && y < MAP_H)
        sMapUnitData[y][x] = id;
}

static int sEffectCalls;
static int sExpCalls;
static int sExpAmount;
static int sAnimationCalls;
static int sEventCalls;
static int sCallbackFailure;

static enum ExpansionAoEFilterDecision InvalidFilter(
    ExpansionAoEUnitId sourceUnitId,
    ExpansionAoEUnitId candidateUnitId)
{
    (void)sourceUnitId;
    (void)candidateUnitId;
    return (enum ExpansionAoEFilterDecision)-1;
}

static enum ExpansionAoETargetEffectResult TestEffect(
    const struct ExpansionAoEEffectContext* context,
    const struct ExpansionAoETarget* target)
{
    (void)context;
    sEffectCalls++;

    if (target->unitId == 2)
        return EXPANSION_AOE_TARGET_FAILED;

    if (target->unitId == 3)
        return EXPANSION_AOE_TARGET_SKIPPED;

    return EXPANSION_AOE_TARGET_APPLIED;
}

static void TestExp(ExpansionAoEUnitId sourceUnitId, u16 amount)
{
    if (sourceUnitId != 1)
        sCallbackFailure = 1;
    sExpCalls++;
    sExpAmount = amount;
}

static void TestAnimation(
    const struct ExpansionAoEEffectContext* context,
    ExpansionAoEUnitId targetUnitId,
    u8 ordinal)
{
    (void)context;
    if (targetUnitId == EXPANSION_AOE_UNIT_NONE
        || ordinal == EXPANSION_AOE_INDEX_NONE)
        sCallbackFailure = 1;
    sAnimationCalls++;
}

static void TestEvent(
    const struct ExpansionAoEEffectContext* context,
    const struct ExpansionAoEExecutionResult* result)
{
    (void)context;
    if (result->appliedCount == 0
        || result->outcome != EXPANSION_AOE_EXECUTION_PARTIAL)
        sCallbackFailure = 1;
    sEventCalls++;
}

static int sRouteCalls;
static enum ExpansionAoEItemDispatchResult sNestedDispatchResult;
static enum ExpansionAoEItemDispatchResult TestRoute(
    const struct ExpansionAoEItemContext* context);

static const struct ExpansionAoEItemRoute sItemRoutes[] =
{
    {
        "test.radius",
        ITEM_ID_CONFIGURED_CAP,
        EXPANSION_AOE_AI_NEVER,
        EXPANSION_AOE_SAVE_ATOMIC_REBUILD,
        0,
        TestRoute,
    },
};

static const struct ExpansionAoEItemRouteTable sItemRouteTable =
{
    sItemRoutes,
    ARRAY_COUNT(sItemRoutes),
    {0, 0, 0},
};

const struct ExpansionAoEItemRouteTable* ExpansionAoE_GetItemRouteTable(void)
{
    return &sItemRouteTable;
}

static enum ExpansionAoEItemDispatchResult TestRoute(
    const struct ExpansionAoEItemContext* context)
{
    sRouteCalls++;
    if (context->phase == EXPANSION_AOE_ITEM_EXECUTE)
        sNestedDispatchResult = ExpansionAoE_DispatchItem(context);

    return EXPANSION_AOE_ITEM_HANDLED;
}

static int TestTargetBuilding(void)
{
    struct ExpansionAoEShape shape;
    struct ExpansionAoEOrigin origin;
    struct ExpansionAoETargetFilter filter;
    struct ExpansionAoETargetSet targets;
    enum ExpansionAoEResult result;
    int index;

    ResetWorld();
    PutUnit(1, 4, 4, 10, 20);
    PutUnit(2, 5, 4, 5, 20);
    PutUnit(3, 3, 4, 20, 20);
    PutUnit(0x81, 4, 5, 4, 20);

    memset(&shape, 0, sizeof(shape));
    shape.kind = EXPANSION_AOE_SHAPE_DIAMOND;
    shape.maxRange = 2;

    memset(&origin, 0, sizeof(origin));
    origin.kind = EXPANSION_AOE_ORIGIN_UNIT;
    origin.sourceUnitId = 1;

    memset(&filter, 0, sizeof(filter));
    filter.relationMask = EXPANSION_AOE_TARGET_SOURCE | EXPANSION_AOE_TARGET_ALLIES;
    filter.conditionMask = EXPANSION_AOE_CONDITION_REQUIRE_DAMAGED;

    result = ExpansionAoE_BuildTargetSet(
        &shape,
        &origin,
        &filter,
        EXPANSION_AOE_BUILD_RANGE_MAP | EXPANSION_AOE_BUILD_LEGACY_TARGET_LIST,
        &targets);
    CHECK(result == EXPANSION_AOE_OK, "valid diamond target build must succeed");
    CHECK(targets.count == 2 && targets.totalCount == 2, "damaged source+ally count");
    CHECK(targets.targets[0].unitId == 1, "source must sort first at distance zero");
    CHECK(targets.targets[1].unitId == 2, "near ally must sort second");
    CHECK(targets.rangeTileCount == 13, "radius-2 diamond has exactly 13 tiles");
    CHECK(GetSelectTargetCount() == 2, "legacy target list mirrors bounded target set");
    CHECK(GetTarget(0)->extra == 0 && GetTarget(1)->extra == 1,
          "legacy target extras carry deterministic distances");

    shape.maxRange = EXPANSION_AOE_MAX_RADIUS + 1;
    CHECK(ExpansionAoE_ValidateShape(&shape) == EXPANSION_AOE_ERR_SHAPE,
          "radius above the public bound must fail");
    shape.maxRange = 2;
    filter.conditionMask = 0x80;
    CHECK(ExpansionAoE_ValidateFilter(&origin, &filter) == EXPANSION_AOE_ERR_FILTER,
          "unknown filter flags must fail");
    filter.conditionMask = EXPANSION_AOE_CONDITION_REQUIRE_DAMAGED;
    CHECK(ExpansionAoE_BuildTargetSet(&shape, &origin, &filter, 0x80, &targets)
              == EXPANSION_AOE_ERR_FILTER,
          "unknown build flags must fail");
    filter.predicate = InvalidFilter;
    CHECK(ExpansionAoE_BuildTargetSet(&shape, &origin, &filter, 0, &targets)
              == EXPANSION_AOE_ERR_FILTER,
          "invalid custom-filter decisions must fail");

    ResetWorld();
    memset(&shape, 0, sizeof(shape));
    shape.kind = EXPANSION_AOE_SHAPE_CROSS;
    shape.minRange = 1;
    shape.maxRange = 2;
    memset(&origin, 0, sizeof(origin));
    origin.kind = EXPANSION_AOE_ORIGIN_POSITION;
    origin.x = 5;
    origin.y = 5;
    memset(&filter, 0, sizeof(filter));
    filter.relationMask = EXPANSION_AOE_TARGET_ANY;
    result = ExpansionAoE_BuildTargetSet(
        &shape, &origin, &filter, EXPANSION_AOE_BUILD_RANGE_MAP, &targets);
    CHECK(result == EXPANSION_AOE_OK && targets.rangeTileCount == 8,
          "cross shape and inclusive minimum range must be validated");

    ResetWorld();
    memset(&shape, 0, sizeof(shape));
    shape.kind = EXPANSION_AOE_SHAPE_SQUARE;
    shape.maxRange = 5;
    memset(&origin, 0, sizeof(origin));
    origin.kind = EXPANSION_AOE_ORIGIN_POSITION;
    origin.x = 5;
    origin.y = 5;
    memset(&filter, 0, sizeof(filter));
    filter.relationMask = EXPANSION_AOE_TARGET_ANY;

    index = 1;
    while (index <= EXPANSION_AOE_MAX_TARGETS + 1)
    {
        int x = (index - 1) % 6;
        int y = (index - 1) / 6;
        PutUnit(index, x, y, 10, 20);
        index++;
    }

    result = ExpansionAoE_BuildTargetSet(&shape, &origin, &filter, 0, &targets);
    CHECK(result == EXPANSION_AOE_ERR_CAPACITY, "17 targets must hit fixed capacity");
    CHECK(targets.count == EXPANSION_AOE_MAX_TARGETS, "bounded set retains only MAX");
    CHECK(targets.totalCount == EXPANSION_AOE_MAX_TARGETS + 1,
          "total target count reports overflow");
    CHECK(targets.complete == 0, "overflow set must be marked incomplete");

    return 0;
}

static int TestExecution(void)
{
    struct ExpansionAoETargetSet targets;
    struct ExpansionAoEEffectContext context;
    struct ExpansionAoEEffectSpec effect;
    struct ExpansionAoEExecutionResult result;
    enum ExpansionAoEResult executeResult;
    int index;

    memset(&targets, 0, sizeof(targets));
    targets.complete = 1;
    targets.count = 4;
    targets.totalCount = 4;
    for (index = 0; index < 4; index++)
        targets.targets[index].unitId = index + 1;

    memset(&context, 0, sizeof(context));
    context.origin.kind = EXPANSION_AOE_ORIGIN_UNIT;
    context.origin.sourceUnitId = 1;

    memset(&effect, 0, sizeof(effect));
    effect.apply = TestEffect;
    effect.awardExp = TestExp;
    effect.animate = TestAnimation;
    effect.invokeEvent = TestEvent;
    effect.partialFailurePolicy = EXPANSION_AOE_PARTIAL_CONTINUE;
    effect.expPolicy = EXPANSION_AOE_EXP_PER_APPLIED_CAPPED;
    effect.animationPolicy = EXPANSION_AOE_ANIMATION_PER_TARGET;
    effect.eventPolicy = EXPANSION_AOE_EVENT_ON_ANY_APPLIED;
    effect.expPerApplied = 7;
    effect.expCap = 10;

    sEffectCalls = 0;
    sExpCalls = 0;
    sExpAmount = 0;
    sAnimationCalls = 0;
    sEventCalls = 0;
    sCallbackFailure = 0;
    executeResult = ExpansionAoE_Execute(&targets, &context, &effect, &result);
    CHECK(executeResult == EXPANSION_AOE_OK, "valid effect execution must succeed");
    CHECK(result.outcome == EXPANSION_AOE_EXECUTION_PARTIAL, "continue failure is partial");
    CHECK(result.attemptedCount == 4, "continue policy attempts every target");
    CHECK(result.appliedCount == 2 && result.skippedCount == 1 && result.failedCount == 1,
          "effect result counts");
    CHECK(result.firstFailureIndex == 1, "first failure index");
    CHECK(result.expAwarded == 10 && sExpCalls == 1 && sExpAmount == 10,
          "per-applied EXP is aggregated once and capped");
    CHECK(sAnimationCalls == 2 && result.animationCallCount == 2,
          "per-target animations follow applied target order");
    CHECK(sEventCalls == 1 && result.eventCallCount == 1,
          "event callback runs once after a partially successful batch");
    CHECK(sCallbackFailure == 0,
          "callbacks receive source, target ordinal, and final batch outcome");

    effect.partialFailurePolicy = EXPANSION_AOE_PARTIAL_STOP;
    sEffectCalls = 0;
    sExpCalls = 0;
    sAnimationCalls = 0;
    sEventCalls = 0;
    executeResult = ExpansionAoE_Execute(&targets, &context, &effect, &result);
    CHECK(executeResult == EXPANSION_AOE_OK, "stop policy execution must succeed");
    CHECK(result.outcome == EXPANSION_AOE_EXECUTION_STOPPED, "stop failure outcome");
    CHECK(result.attemptedCount == 2 && result.appliedCount == 1 && result.failedCount == 1,
          "stop policy preserves earlier success and stops at failure");
    CHECK(result.expAwarded == 7, "stop policy aggregates only applied targets");

    targets.complete = 0;
    CHECK(ExpansionAoE_Execute(&targets, &context, &effect, &result)
              == EXPANSION_AOE_ERR_INCOMPLETE,
          "capacity-truncated targets must never execute");

    return 0;
}

static int TestStableSlotDiscovery(void)
{
    struct ExpansionAoEShape shape;
    struct ExpansionAoEOrigin origin;
    struct ExpansionAoETargetFilter filter;
    struct ExpansionAoETargetSet targets;
    enum ExpansionAoEResult result;

    memset(&shape, 0, sizeof(shape));
    shape.kind = EXPANSION_AOE_SHAPE_DIAMOND;
    shape.maxRange = 2;

    memset(&origin, 0, sizeof(origin));
    origin.kind = EXPANSION_AOE_ORIGIN_POSITION;
    origin.x = 4;
    origin.y = 4;

    memset(&filter, 0, sizeof(filter));
    filter.relationMask = EXPANSION_AOE_TARGET_ANY;

    ResetWorld();
    PutUnit(1, 4, 4, 10, 20);
    sUnits[1].state = US_HIDDEN;
    origin.kind = EXPANSION_AOE_ORIGIN_UNIT;
    origin.sourceUnitId = 1;
    filter.relationMask = EXPANSION_AOE_TARGET_SOURCE;
    result = ExpansionAoE_BuildTargetSet(&shape, &origin, &filter, 0, &targets);
    CHECK(result == EXPANSION_AOE_OK && targets.count == 0,
          "hidden actor is absent without the include-hidden flag");
    filter.conditionMask = EXPANSION_AOE_CONDITION_INCLUDE_HIDDEN;
    result = ExpansionAoE_BuildTargetSet(&shape, &origin, &filter, 0, &targets);
    CHECK(result == EXPANSION_AOE_OK && targets.count == 1
              && targets.targets[0].unitId == 1
              && targets.targets[0].x == 4 && targets.targets[0].y == 4,
          "stable slot discovery includes a hidden actor at its retained position");

    ResetWorld();
    PutUnit(1, 4, 4, 10, 20);
    PutUnit(2, 4, 5, 10, 20);
    PutUnit(3, 0, 0, 10, 20);
    sUnits[2].state = US_RESCUING;
    sUnits[2].rescue = 3;
    sUnits[3].state = US_RESCUED | US_HIDDEN;
    sUnits[3].rescue = 2;
    sUnits[3].xPos = -1;
    sUnits[3].yPos = -1;
    origin.kind = EXPANSION_AOE_ORIGIN_POSITION;
    origin.sourceUnitId = EXPANSION_AOE_UNIT_NONE;
    filter.relationMask = EXPANSION_AOE_TARGET_ANY;
    filter.conditionMask = 0;
    result = ExpansionAoE_BuildTargetSet(&shape, &origin, &filter, 0, &targets);
    CHECK(result == EXPANSION_AOE_OK && targets.count == 2
              && targets.targets[0].unitId == 1
              && targets.targets[1].unitId == 2,
          "rescued units are excluded unless both hidden and rescued are allowed");
    filter.conditionMask = EXPANSION_AOE_CONDITION_INCLUDE_HIDDEN
        | EXPANSION_AOE_CONDITION_INCLUDE_RESCUED;
    result = ExpansionAoE_BuildTargetSet(&shape, &origin, &filter, 0, &targets);
    CHECK(result == EXPANSION_AOE_OK && targets.count == 3
              && targets.targets[1].unitId == 2
              && targets.targets[2].unitId == 3
              && targets.targets[1].x == targets.targets[2].x
              && targets.targets[1].y == targets.targets[2].y,
          "rescued units use a validated carrier position");
    sUnits[2].xPos = -1;
    sUnits[2].yPos = -1;
    result = ExpansionAoE_BuildTargetSet(&shape, &origin, &filter, 0, &targets);
    CHECK(result == EXPANSION_AOE_OK && targets.count == 1
              && targets.targets[0].unitId == 1,
          "rescued units with an off-map carrier are excluded safely");

    ResetWorld();
    PutUnit(1, 4, 4, 10, 20);
    PutUnit(2, 5, 4, 10, 20);
    PutUnit(3, 3, 4, 10, 20);
    PutUnit(4, 4, 3, 10, 20);
    PutUnit(5, 4, 5, 10, 20);
    sUnits[2].state = US_HIDDEN;
    sUnits[3].state = US_DEAD;
    sUnits[4].state = US_BIT16;
    sUnits[5].state = US_NOT_DEPLOYED;
    origin.x = 4;
    origin.y = 4;
    filter.conditionMask = 0;
    result = ExpansionAoE_BuildTargetSet(&shape, &origin, &filter, 0, &targets);
    CHECK(result == EXPANSION_AOE_OK && targets.count == 1
              && targets.targets[0].unitId == 1,
          "hidden, dead, undeployed, and unavailable units are excluded by default");
    filter.conditionMask = EXPANSION_AOE_CONDITION_INCLUDE_HIDDEN
        | EXPANSION_AOE_CONDITION_INCLUDE_DEAD
        | EXPANSION_AOE_CONDITION_INCLUDE_UNDEPLOYED;
    result = ExpansionAoE_BuildTargetSet(&shape, &origin, &filter, 0, &targets);
    CHECK(result == EXPANSION_AOE_OK && targets.count == 4
              && targets.targets[0].unitId == 1
              && targets.targets[1].unitId == 3
              && targets.targets[2].unitId == 2
              && targets.targets[3].unitId == 5,
          "explicit inclusion flags restore eligible hidden/dead/undeployed units");

    ResetWorld();
    PutUnit(1, 4, 4, 10, 20);
    PutUnit(2, 5, 4, 10, 20);
    PutUnit(3, 5, 4, 10, 20);
    PutUnit(4, 4, 5, 10, 20);
    sUnitLookup[5] = &sUnits[3];
    result = ExpansionAoE_BuildTargetSet(&shape, &origin, &filter, 0, &targets);
    CHECK(result == EXPANSION_AOE_OK && targets.count == 4
              && targets.totalCount == 4
              && targets.targets[0].unitId == 1
              && targets.targets[1].unitId == 2
              && targets.targets[2].unitId == 3
              && targets.targets[3].unitId == 4,
          "stable slot ordering is distance/Y/X/unit and duplicate aliases are removed");

    return 0;
}

static int TestItemRoutes(void)
{
    struct ExpansionAoEItemRoute invalidRoutes[2];
    struct ExpansionAoEItemRouteTable invalidTable;
    struct ExpansionAoEItemContext context;
    enum ExpansionAoEItemDispatchResult dispatch;

    CHECK(ExpansionAoE_ValidateItemRouteTable(&sItemRouteTable)
              == EXPANSION_AOE_ROUTE_OK,
          "ROM-authored route table must validate");
    CHECK(ExpansionAoE_ItemRouteCount() == 1, "route count");
    CHECK(ExpansionAoE_ItemRouteItemAt(0) == ITEM_ID_CONFIGURED_CAP,
          "route item introspection");
    CHECK(strcmp(ExpansionAoE_ItemRouteKeyAt(0), "test.radius") == 0,
          "ROM-authored route key is introspectable");

    invalidRoutes[0] = sItemRoutes[0];
    invalidRoutes[1] = sItemRoutes[0];
    invalidTable.routes = invalidRoutes;
    invalidTable.count = 2;
    CHECK(ExpansionAoE_ValidateItemRouteTable(&invalidTable)
              == EXPANSION_AOE_ROUTE_ERR_DUPLICATE,
          "duplicate item/key route must fail deterministic validation");
    invalidTable.count = EXPANSION_AOE_ITEM_ROUTE_MAX + 1;
    CHECK(ExpansionAoE_ValidateItemRouteTable(&invalidTable)
              == EXPANSION_AOE_ROUTE_ERR_CAPACITY,
          "oversized ROM-authored route table must fail");

    invalidTable.count = 1;
    invalidRoutes[0] = sItemRoutes[0];
    invalidRoutes[0].itemId = ITEM_ID_SENTINEL;
    CHECK(ExpansionAoE_ValidateItemRouteTable(&invalidTable)
              == EXPANSION_AOE_ROUTE_ERR_ITEM,
          "zero item ID must fail");
    invalidRoutes[0].itemId = (ItemId)(ITEM_ID_CONFIGURED_CAP + 1);
    CHECK(ExpansionAoE_ValidateItemRouteTable(&invalidTable)
              == EXPANSION_AOE_ROUTE_ERR_ITEM,
          "item ID above configured cap must fail");

    memset(&context, 0, sizeof(context));
    context.phase = EXPANSION_AOE_ITEM_CAN_USE;
    context.itemId = ITEM_ID_CONFIGURED_CAP;
    sRouteCalls = 0;
    dispatch = ExpansionAoE_DispatchItem(&context);
    CHECK(dispatch == EXPANSION_AOE_ITEM_HANDLED && sRouteCalls == 1,
          "registered item dispatches through the narrow seam");

    context.phase = EXPANSION_AOE_ITEM_AI_SELECT;
    dispatch = ExpansionAoE_DispatchItem(&context);
    CHECK(dispatch == EXPANSION_AOE_ITEM_REJECTED && sRouteCalls == 1,
          "AI_NEVER rejects selection without invoking the route");

    context.phase = EXPANSION_AOE_ITEM_EXECUTE;
    sNestedDispatchResult = EXPANSION_AOE_ITEM_NOT_HANDLED;
    dispatch = ExpansionAoE_DispatchItem(&context);
    CHECK(dispatch == EXPANSION_AOE_ITEM_HANDLED, "execute dispatch");
    CHECK(sNestedDispatchResult == EXPANSION_AOE_ITEM_ERROR,
          "recursive dispatch must be rejected");
    CHECK(ExpansionAoE_ItemRouteCount() == 1,
          "ROM-authored route table remains stable after dispatch");

    context.itemId = (ItemId)(ITEM_ID_CONFIGURED_CAP - 1);
    CHECK(ExpansionAoE_DispatchItem(&context) == EXPANSION_AOE_ITEM_NOT_HANDLED,
          "unregistered vanilla item remains on the vanilla path");

    return 0;
}

static int TestReference(void)
{
    struct ExpansionAoETargetSet targets;
    struct ExpansionAoEExecutionResult result;
    int sourceBefore;
    int allyBefore;
    int enemyBefore;

    ResetWorld();
    PutUnit(1, 4, 4, 10, 20);
    PutUnit(2, 5, 4, 5, 20);
    PutUnit(3, 3, 4, 20, 20);
    PutUnit(0x81, 4, 5, 4, 20);
    sourceBefore = sUnits[1].curHP;
    allyBefore = sUnits[2].curHP;
    enemyBefore = sUnits[0x81].curHP;

    CHECK(ExpansionAoEReference_IsEnabled() == 1, "reference flag enabled");
    CHECK(ExpansionAoEReference_Apply(1, &targets, &result) == EXPANSION_AOE_OK,
          "reference apply");
    CHECK(targets.count == 2, "reference targets damaged source and ally only");
    CHECK(result.appliedCount == 2 && result.failedCount == 0,
          "reference applies to both targets");
    CHECK(sUnits[1].curHP == sourceBefore + EXPANSION_AOE_REFERENCE_HEAL_AMOUNT,
          "reference heals source by fixed amount");
    CHECK(sUnits[2].curHP == allyBefore + EXPANSION_AOE_REFERENCE_HEAL_AMOUNT,
          "reference heals ally by fixed amount");
    CHECK(sUnits[0x81].curHP == enemyBefore, "reference never heals enemy");
    CHECK(result.expAwarded == 0 && result.animationCallCount == 0
              && result.eventCallCount == 0,
          "reference has explicit no-EXP/no-animation/no-event policy");

    return 0;
}

static int TestItemPhase(enum ExpansionAoEItemPhase phase)
{
    struct ExpansionAoEItemContext context;
    enum ExpansionAoEItemDispatchResult result;
    int expectedCalls;

    memset(&context, 0, sizeof(context));
    context.phase = phase;
    context.itemId = ITEM_ID_CONFIGURED_CAP;
    sRouteCalls = 0;
    result = ExpansionAoE_DispatchItem(&context);
    expectedCalls = phase == EXPANSION_AOE_ITEM_AI_SELECT ? 0 : 1;
    CHECK(result == (expectedCalls ? EXPANSION_AOE_ITEM_HANDLED : EXPANSION_AOE_ITEM_REJECTED),
          "phase must follow its registered route policy");
    CHECK(sRouteCalls == expectedCalls, "phase callback count");
    context.phase = EXPANSION_AOE_ITEM_PHASE_COUNT;
    CHECK(ExpansionAoE_DispatchItem(&context) == EXPANSION_AOE_ITEM_ERROR,
          "unknown phase must fail before callback");
    CHECK(sRouteCalls == expectedCalls, "invalid phase must not execute");
    return 0;
}

static int TestShape(enum ExpansionAoEShapeKind kind)
{
    static const u8 sExpectedGeometry[3][5][5] =
    {
        {
            { 0, 0, 1, 0, 0 },
            { 0, 1, 1, 1, 0 },
            { 1, 1, 1, 1, 1 },
            { 0, 1, 1, 1, 0 },
            { 0, 0, 1, 0, 0 },
        },
        {
            { 1, 1, 1, 1, 1 },
            { 1, 1, 1, 1, 1 },
            { 1, 1, 1, 1, 1 },
            { 1, 1, 1, 1, 1 },
            { 1, 1, 1, 1, 1 },
        },
        {
            { 0, 0, 1, 0, 0 },
            { 0, 0, 1, 0, 0 },
            { 1, 1, 1, 1, 1 },
            { 0, 0, 1, 0, 0 },
            { 0, 0, 1, 0, 0 },
        },
    };
    struct ExpansionAoEShape shape;
    struct ExpansionAoEOrigin origin;
    struct ExpansionAoETargetFilter filter;
    struct ExpansionAoETargetSet targets;
    int expectedTiles;
    int geometryIndex;
    int expected;
    int x;
    int y;

    ResetWorld();
    PutUnit(1, 4, 4, 10, 20);
    memset(&shape, 0, sizeof(shape));
    shape.kind = kind;
    shape.maxRange = 2;
    memset(&origin, 0, sizeof(origin));
    origin.kind = EXPANSION_AOE_ORIGIN_UNIT;
    origin.sourceUnitId = 1;
    memset(&filter, 0, sizeof(filter));
    filter.relationMask = EXPANSION_AOE_TARGET_SOURCE;
    expectedTiles = kind == EXPANSION_AOE_SHAPE_DIAMOND ? 13
        : kind == EXPANSION_AOE_SHAPE_SQUARE ? 25 : 9;
    geometryIndex = kind == EXPANSION_AOE_SHAPE_DIAMOND ? 0
        : kind == EXPANSION_AOE_SHAPE_SQUARE ? 1 : 2;
    BmMapFill(gBmMapRange, 0x7F);
    CHECK(ExpansionAoE_BuildTargetSet(&shape, &origin, &filter,
                                    EXPANSION_AOE_BUILD_RANGE_MAP, &targets)
              == EXPANSION_AOE_OK, "valid shape must build");
    CHECK(targets.count == 1 && targets.targets[0].unitId == 1,
          "shape preserves its valid source target");
    CHECK(targets.rangeTileCount == expectedTiles, "shape geometry");
    for (y = 0; y < gBmMapSize.y; y++)
    {
        for (x = 0; x < gBmMapSize.x; x++)
        {
            expected = 0;
            if (x >= 2 && x <= 6 && y >= 2 && y <= 6)
                expected = sExpectedGeometry[geometryIndex][y - 2][x - 2];
            CHECK(gBmMapRange[y][x] == expected, "selected shape range-map cell");
        }
    }
    shape.maxRange = EXPANSION_AOE_MAX_RADIUS + 1;
    CHECK(ExpansionAoE_BuildTargetSet(&shape, &origin, &filter, 0, &targets)
              == EXPANSION_AOE_ERR_SHAPE, "over-range shape must reject");
    return 0;
}

int main(int argc, char** argv)
{
    CHECK(EXPANSION_AOE_MAX_TARGETS == 16, "target capacity contract");
    CHECK(EXPANSION_AOE_MAX_RADIUS == 5, "shape radius contract");
    CHECK(sizeof(struct ExpansionAoETargetSet) <= 80, "target set stack budget");

    if (argc == 2)
    {
        if (strcmp(argv[1], "items") == 0)
            return TestItemRoutes();
        if (strcmp(argv[1], "targets") == 0)
            return TestTargetBuilding();
        if (strcmp(argv[1], "execution") == 0)
            return TestExecution();
        if (strcmp(argv[1], "slots") == 0)
            return TestStableSlotDiscovery();
        if (strcmp(argv[1], "reference") == 0)
            return TestReference();
        if (strcmp(argv[1], "phase:CAN_USE") == 0)
            return TestItemPhase(EXPANSION_AOE_ITEM_CAN_USE);
        if (strcmp(argv[1], "phase:BEGIN_USE") == 0)
            return TestItemPhase(EXPANSION_AOE_ITEM_BEGIN_USE);
        if (strcmp(argv[1], "phase:EXECUTE") == 0)
            return TestItemPhase(EXPANSION_AOE_ITEM_EXECUTE);
        if (strcmp(argv[1], "phase:AI_SELECT") == 0)
            return TestItemPhase(EXPANSION_AOE_ITEM_AI_SELECT);
        if (strcmp(argv[1], "shape:DIAMOND") == 0)
            return TestShape(EXPANSION_AOE_SHAPE_DIAMOND);
        if (strcmp(argv[1], "shape:SQUARE") == 0)
            return TestShape(EXPANSION_AOE_SHAPE_SQUARE);
        if (strcmp(argv[1], "shape:CROSS") == 0)
            return TestShape(EXPANSION_AOE_SHAPE_CROSS);
        return 64;
    }
    if (argc != 1)
        return 64;

    if (TestTargetBuilding() != 0)
        return 1;
    if (TestExecution() != 0)
        return 1;
    if (TestStableSlotDiscovery() != 0)
        return 1;
    if (TestItemRoutes() != 0)
        return 1;
    if (TestReference() != 0)
        return 1;

    printf("AOE_HOST_TEST: PASS\n");
    return 0;
}
