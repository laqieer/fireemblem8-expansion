#ifndef GUARD_EXPANSION_AOE_H
#define GUARD_EXPANSION_AOE_H

/*
 * Typed, bounded area-of-effect targeting/effect API (issue #42).
 *
 * The core API is available in every modern framework build. Optional item
 * routes plug into one immutable ROM-authored table and the existing
 * item/action/AI pipelines; the bundled reference effect is configured
 * separately through FE8_EXPANSION_AOE_REFERENCE.
 *
 * Target sets contain stable unit indices and coordinates only. They never
 * retain struct Unit or Proc pointers, and incomplete (capacity-truncated)
 * sets cannot be executed.
 */

#include "expansion_config.h"
#include "id_space.h"

struct Unit;

typedef u8 ExpansionAoEUnitId;

#define EXPANSION_AOE_UNIT_NONE ((ExpansionAoEUnitId)0)
#define EXPANSION_AOE_MAX_TARGETS 16
#define EXPANSION_AOE_MAX_RADIUS 5
#define EXPANSION_AOE_ITEM_ROUTE_MAX 8
#define EXPANSION_AOE_ROUTE_KEY_SIZE 24
#define EXPANSION_AOE_INDEX_NONE 0xFF

enum ExpansionAoEResult
{
    EXPANSION_AOE_OK = 0,
    EXPANSION_AOE_ERR_DISABLED,
    EXPANSION_AOE_ERR_NULL_ARG,
    EXPANSION_AOE_ERR_SHAPE,
    EXPANSION_AOE_ERR_ORIGIN,
    EXPANSION_AOE_ERR_FILTER,
    EXPANSION_AOE_ERR_CAPACITY,
    EXPANSION_AOE_ERR_INCOMPLETE,
    EXPANSION_AOE_ERR_EFFECT
};

enum ExpansionAoEShapeKind
{
    EXPANSION_AOE_SHAPE_DIAMOND = 0,
    EXPANSION_AOE_SHAPE_SQUARE,
    EXPANSION_AOE_SHAPE_CROSS,
    EXPANSION_AOE_SHAPE_COUNT
};

struct ExpansionAoEShape
{
    /* 00 */ u8 kind;
    /* 01 */ u8 minRange;
    /* 02 */ u8 maxRange;
    /* 03 */ u8 _pad;
};

enum ExpansionAoEOriginKind
{
    EXPANSION_AOE_ORIGIN_POSITION = 0,
    EXPANSION_AOE_ORIGIN_UNIT,
    EXPANSION_AOE_ORIGIN_COUNT
};

struct ExpansionAoEOrigin
{
    /* 00 */ u8 kind;
    /* 01 */ ExpansionAoEUnitId sourceUnitId;
    /* 02 */ s8 x;
    /* 03 */ s8 y;
};

enum ExpansionAoETargetRelation
{
    EXPANSION_AOE_TARGET_ANY = 1 << 0,
    EXPANSION_AOE_TARGET_SOURCE = 1 << 1,
    EXPANSION_AOE_TARGET_ALLIES = 1 << 2,
    EXPANSION_AOE_TARGET_ENEMIES = 1 << 3
};

enum ExpansionAoETargetCondition
{
    EXPANSION_AOE_CONDITION_REQUIRE_DAMAGED = 1 << 0,
    EXPANSION_AOE_CONDITION_REQUIRE_STATUS = 1 << 1,
    EXPANSION_AOE_CONDITION_REQUIRE_NO_STATUS = 1 << 2,
    EXPANSION_AOE_CONDITION_INCLUDE_HIDDEN = 1 << 3,
    EXPANSION_AOE_CONDITION_INCLUDE_DEAD = 1 << 4,
    EXPANSION_AOE_CONDITION_INCLUDE_UNDEPLOYED = 1 << 5,
    EXPANSION_AOE_CONDITION_INCLUDE_RESCUED = 1 << 6
};

#define EXPANSION_AOE_CONDITION_ALL \
    (EXPANSION_AOE_CONDITION_REQUIRE_DAMAGED \
        | EXPANSION_AOE_CONDITION_REQUIRE_STATUS \
        | EXPANSION_AOE_CONDITION_REQUIRE_NO_STATUS \
        | EXPANSION_AOE_CONDITION_INCLUDE_HIDDEN \
        | EXPANSION_AOE_CONDITION_INCLUDE_DEAD \
        | EXPANSION_AOE_CONDITION_INCLUDE_UNDEPLOYED \
        | EXPANSION_AOE_CONDITION_INCLUDE_RESCUED)

enum ExpansionAoEFilterDecision
{
    EXPANSION_AOE_FILTER_REJECT = 0,
    EXPANSION_AOE_FILTER_ACCEPT,
    EXPANSION_AOE_FILTER_ERROR
};

typedef enum ExpansionAoEFilterDecision (*ExpansionAoEFilterFunc)(
    ExpansionAoEUnitId sourceUnitId,
    ExpansionAoEUnitId candidateUnitId);

struct ExpansionAoETargetFilter
{
    /* 00 */ u8 relationMask;
    /* 01 */ u8 conditionMask;
    /* 02 */ u8 _pad[2];
    /* 04 */ ExpansionAoEFilterFunc predicate;
};

enum ExpansionAoETargetOrder
{
    EXPANSION_AOE_ORDER_DISTANCE_Y_X_UNIT = 0
};

struct ExpansionAoETarget
{
    /* 00 */ ExpansionAoEUnitId unitId;
    /* 01 */ u8 distance;
    /* 02 */ s8 x;
    /* 03 */ s8 y;
};

struct ExpansionAoETargetSet
{
    /* 00 */ u8 count;
    /* 01 */ u8 complete;
    /* 02 */ u16 totalCount;
    /* 04 */ u16 rangeTileCount;
    /* 06 */ u8 order;
    /* 07 */ u8 _pad;
    /* 08 */ struct ExpansionAoETarget targets[EXPANSION_AOE_MAX_TARGETS];
};

enum ExpansionAoEBuildFlag
{
    EXPANSION_AOE_BUILD_RANGE_MAP = 1 << 0,
    EXPANSION_AOE_BUILD_LEGACY_TARGET_LIST = 1 << 1
};

#define EXPANSION_AOE_BUILD_ALL \
    (EXPANSION_AOE_BUILD_RANGE_MAP | EXPANSION_AOE_BUILD_LEGACY_TARGET_LIST)

enum ExpansionAoETargetEffectResult
{
    EXPANSION_AOE_TARGET_APPLIED = 0,
    EXPANSION_AOE_TARGET_SKIPPED,
    EXPANSION_AOE_TARGET_FAILED
};

enum ExpansionAoEPartialFailurePolicy
{
    EXPANSION_AOE_PARTIAL_CONTINUE = 0,
    EXPANSION_AOE_PARTIAL_STOP,
    EXPANSION_AOE_PARTIAL_COUNT
};

enum ExpansionAoEExpPolicy
{
    EXPANSION_AOE_EXP_NONE = 0,
    EXPANSION_AOE_EXP_ONCE,
    EXPANSION_AOE_EXP_PER_APPLIED_CAPPED,
    EXPANSION_AOE_EXP_COUNT
};

enum ExpansionAoEAnimationPolicy
{
    EXPANSION_AOE_ANIMATION_NONE = 0,
    EXPANSION_AOE_ANIMATION_PER_TARGET,
    EXPANSION_AOE_ANIMATION_BATCH,
    EXPANSION_AOE_ANIMATION_COUNT
};

enum ExpansionAoEEventPolicy
{
    EXPANSION_AOE_EVENT_NONE = 0,
    EXPANSION_AOE_EVENT_ON_ANY_APPLIED,
    EXPANSION_AOE_EVENT_ON_COMPLETE_SUCCESS,
    EXPANSION_AOE_EVENT_COUNT
};

enum ExpansionAoEExecutionOutcome
{
    EXPANSION_AOE_EXECUTION_OK = 0,
    EXPANSION_AOE_EXECUTION_PARTIAL,
    EXPANSION_AOE_EXECUTION_FAILED,
    EXPANSION_AOE_EXECUTION_STOPPED
};

struct ExpansionAoEEffectContext
{
    /* 00 */ struct ExpansionAoEOrigin origin;
    /* 04 */ ItemId itemId;
    /* 05 */ u8 _pad;
    /* 06 */ s16 effectValue;
    /* 08 */ u32 effectFlags;
};

struct ExpansionAoEExecutionResult
{
    /* 00 */ u8 outcome;
    /* 01 */ u8 attemptedCount;
    /* 02 */ u8 appliedCount;
    /* 03 */ u8 skippedCount;
    /* 04 */ u8 failedCount;
    /* 05 */ u8 firstFailureIndex;
    /* 06 */ u16 expAwarded;
    /* 08 */ u8 animationCallCount;
    /* 09 */ u8 eventCallCount;
    /* 0A */ u8 _pad[2];
};

typedef enum ExpansionAoETargetEffectResult (*ExpansionAoEEffectFunc)(
    const struct ExpansionAoEEffectContext* context,
    const struct ExpansionAoETarget* target);

typedef void (*ExpansionAoEExpFunc)(ExpansionAoEUnitId sourceUnitId, u16 amount);

typedef void (*ExpansionAoEAnimationFunc)(
    const struct ExpansionAoEEffectContext* context,
    ExpansionAoEUnitId targetUnitId,
    u8 ordinal);

typedef void (*ExpansionAoEEventFunc)(
    const struct ExpansionAoEEffectContext* context,
    const struct ExpansionAoEExecutionResult* result);

struct ExpansionAoEEffectSpec
{
    /* 00 */ ExpansionAoEEffectFunc apply;
    /* 04 */ ExpansionAoEExpFunc awardExp;
    /* 08 */ ExpansionAoEAnimationFunc animate;
    /* 0C */ ExpansionAoEEventFunc invokeEvent;
    /* 10 */ u8 partialFailurePolicy;
    /* 11 */ u8 expPolicy;
    /* 12 */ u8 animationPolicy;
    /* 13 */ u8 eventPolicy;
    /* 14 */ u8 expPerApplied;
    /* 15 */ u8 expCap;
    /* 16 */ u8 _pad[2];
};

enum ExpansionAoEItemPhase
{
    EXPANSION_AOE_ITEM_CAN_USE = 0,
    EXPANSION_AOE_ITEM_BEGIN_USE,
    EXPANSION_AOE_ITEM_EXECUTE,
    EXPANSION_AOE_ITEM_AI_SELECT,
    EXPANSION_AOE_ITEM_PHASE_COUNT
};

enum ExpansionAoEItemDispatchResult
{
    EXPANSION_AOE_ITEM_NOT_HANDLED = 0,
    EXPANSION_AOE_ITEM_HANDLED,
    EXPANSION_AOE_ITEM_REJECTED,
    EXPANSION_AOE_ITEM_ERROR
};

enum ExpansionAoEAiPolicy
{
    EXPANSION_AOE_AI_NEVER = 0,
    EXPANSION_AOE_AI_CALLBACK,
    EXPANSION_AOE_AI_POLICY_COUNT
};

enum ExpansionAoESavePolicy
{
    EXPANSION_AOE_SAVE_ATOMIC_REBUILD = 0,
    EXPANSION_AOE_SAVE_POLICY_COUNT
};

typedef s8 (*ExpansionAoEAiRelationFunc)(struct Unit* unit);

struct ExpansionAoEItemContext
{
    /* 00 */ u8 phase;
    /* 01 */ ExpansionAoEUnitId actorUnitId;
    /* 02 */ ExpansionAoEUnitId targetUnitId;
    /* 03 */ u8 itemSlot;
    /* 04 */ ItemId itemId;
    /* 05 */ s8 originX;
    /* 06 */ s8 originY;
    /* 07 */ u8 _pad;
    /* 08 */ u16 item;
    /* 0A */ u16 _pad2;
    /* 0C */ ProcPtr parent;
    /* 10 */ ExpansionAoEAiRelationFunc aiRelation;
};

typedef enum ExpansionAoEItemDispatchResult (*ExpansionAoEItemHandler)(
    const struct ExpansionAoEItemContext* context);

struct ExpansionAoEItemRoute
{
    /* 00 */ const char* key;
    /* 04 */ ItemId itemId;
    /* 05 */ u8 aiPolicy;
    /* 06 */ u8 savePolicy;
    /* 07 */ u8 _pad;
    /* 08 */ ExpansionAoEItemHandler handler;
};

struct ExpansionAoEItemRouteTable
{
    /* 00 */ const struct ExpansionAoEItemRoute* routes;
    /* 04 */ u8 count;
    /* 05 */ u8 _pad[3];
};

enum ExpansionAoERouteResult
{
    EXPANSION_AOE_ROUTE_OK = 0,
    EXPANSION_AOE_ROUTE_ERR_NULL_ARG,
    EXPANSION_AOE_ROUTE_ERR_KEY_LENGTH,
    EXPANSION_AOE_ROUTE_ERR_ITEM,
    EXPANSION_AOE_ROUTE_ERR_POLICY,
    EXPANSION_AOE_ROUTE_ERR_DUPLICATE,
    EXPANSION_AOE_ROUTE_ERR_CAPACITY,
    EXPANSION_AOE_ROUTE_ERR_REENTRANT
};

enum ExpansionAoEResult ExpansionAoE_ValidateShape(const struct ExpansionAoEShape* shape);
enum ExpansionAoEResult ExpansionAoE_ValidateFilter(
    const struct ExpansionAoEOrigin* origin,
    const struct ExpansionAoETargetFilter* filter);
enum ExpansionAoEResult ExpansionAoE_BuildTargetSet(
    const struct ExpansionAoEShape* shape,
    const struct ExpansionAoEOrigin* origin,
    const struct ExpansionAoETargetFilter* filter,
    u8 buildFlags,
    struct ExpansionAoETargetSet* out);
enum ExpansionAoEResult ExpansionAoE_Execute(
    const struct ExpansionAoETargetSet* targets,
    const struct ExpansionAoEEffectContext* context,
    const struct ExpansionAoEEffectSpec* effect,
    struct ExpansionAoEExecutionResult* out);

/*
 * Downstream projects provide a strong override returning a const table.
 * The framework's weak default returns an empty table. Both the table and
 * route keys remain in ROM; no registration copy is retained in EWRAM.
 */
const struct ExpansionAoEItemRouteTable* ExpansionAoE_GetItemRouteTable(void);
enum ExpansionAoERouteResult ExpansionAoE_ValidateItemRouteTable(
    const struct ExpansionAoEItemRouteTable* table);
int ExpansionAoE_ItemRouteCount(void);
ItemId ExpansionAoE_ItemRouteItemAt(int index);
const char* ExpansionAoE_ItemRouteKeyAt(int index);
enum ExpansionAoEItemDispatchResult ExpansionAoE_DispatchItem(
    const struct ExpansionAoEItemContext* context);
void ExpansionAoE_InitItemContext(
    struct ExpansionAoEItemContext* context,
    enum ExpansionAoEItemPhase phase,
    struct Unit* actor,
    int item,
    int itemSlot);

#endif /* GUARD_EXPANSION_AOE_H */
