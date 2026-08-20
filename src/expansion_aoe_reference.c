#include "global.h"

#include <string.h>

#include "bmmap.h"
#include "bmunit.h"
#include "uiselecttarget.h"

#include "expansion_aoe_reference.h"

#if FE8_EXPANSION_MODERN_BUILD && FE8_EXPANSION_AOE_REFERENCE
EWRAM_DATA struct ExpansionAoEReferenceProbe gExpansionAoEReferenceProbe = {0};
#endif

#if FE8_EXPANSION_AOE_REFERENCE

static enum ExpansionAoETargetEffectResult ExpansionAoEReference_Heal(
    const struct ExpansionAoEEffectContext* context,
    const struct ExpansionAoETarget* target)
{
    struct Unit* unit;

    unit = GetUnit(target->unitId);
    if (!UNIT_IS_VALID(unit))
        return EXPANSION_AOE_TARGET_FAILED;

    if (GetUnitCurrentHp(unit) >= GetUnitMaxHp(unit))
        return EXPANSION_AOE_TARGET_SKIPPED;

    AddUnitHp(unit, context->effectValue);
    return EXPANSION_AOE_TARGET_APPLIED;
}

int ExpansionAoEReference_IsEnabled(void)
{
    return 1;
}

enum ExpansionAoEResult ExpansionAoEReference_Apply(
    ExpansionAoEUnitId sourceUnitId,
    struct ExpansionAoETargetSet* targets,
    struct ExpansionAoEExecutionResult* result)
{
    struct ExpansionAoEShape shape;
    struct ExpansionAoEOrigin origin;
    struct ExpansionAoETargetFilter filter;
    struct ExpansionAoEEffectContext context;
    struct ExpansionAoEEffectSpec effect;
    enum ExpansionAoEResult buildResult;

    if (targets == NULL || result == NULL)
        return EXPANSION_AOE_ERR_NULL_ARG;

    memset(&shape, 0, sizeof(shape));
    shape.kind = EXPANSION_AOE_SHAPE_DIAMOND;
    shape.minRange = 0;
    shape.maxRange = EXPANSION_AOE_REFERENCE_RADIUS;

    memset(&origin, 0, sizeof(origin));
    origin.kind = EXPANSION_AOE_ORIGIN_UNIT;
    origin.sourceUnitId = sourceUnitId;

    memset(&filter, 0, sizeof(filter));
    filter.relationMask = EXPANSION_AOE_TARGET_SOURCE | EXPANSION_AOE_TARGET_ALLIES;
    filter.conditionMask = EXPANSION_AOE_CONDITION_REQUIRE_DAMAGED;

    buildResult = ExpansionAoE_BuildTargetSet(
        &shape,
        &origin,
        &filter,
        EXPANSION_AOE_BUILD_RANGE_MAP | EXPANSION_AOE_BUILD_LEGACY_TARGET_LIST,
        targets);
    if (buildResult != EXPANSION_AOE_OK)
        return buildResult;

    memset(&context, 0, sizeof(context));
    context.origin = origin;
    context.itemId = ITEM_ID_SENTINEL;
    context.effectValue = EXPANSION_AOE_REFERENCE_HEAL_AMOUNT;

    memset(&effect, 0, sizeof(effect));
    effect.apply = ExpansionAoEReference_Heal;
    effect.partialFailurePolicy = EXPANSION_AOE_PARTIAL_CONTINUE;
    effect.expPolicy = EXPANSION_AOE_EXP_NONE;
    effect.animationPolicy = EXPANSION_AOE_ANIMATION_NONE;
    effect.eventPolicy = EXPANSION_AOE_EVENT_NONE;

    return ExpansionAoE_Execute(targets, &context, &effect, result);
}

static int ExpansionAoEReference_FindTarget(
    const struct ExpansionAoETargetSet* targets,
    ExpansionAoEUnitId unitId)
{
    int index;

    for (index = 0; index < targets->count; index++)
    {
        if (targets->targets[index].unitId == unitId)
            return index;
    }

    return -1;
}

void ExpansionAoEReference_RunProbe(void)
{
#if FE8_EXPANSION_MODERN_BUILD
    struct ExpansionAoETargetSet nearby;
    struct ExpansionAoETargetSet affected;
    struct ExpansionAoEExecutionResult execution;
    struct ExpansionAoEShape shape;
    struct ExpansionAoEOrigin origin;
    struct ExpansionAoETargetFilter filter;
    struct Unit* unit;
    ExpansionAoEUnitId selected[2];
    u8 originalHp[EXPANSION_AOE_MAX_TARGETS];
    enum ExpansionAoEResult buildResult;
    enum ExpansionAoEResult applyResult;
    int selectedCount;
    int firstIndex;
    int secondIndex;
    int index;
    int unitIndex;
    int restored;

    if (gExpansionAoEReferenceProbe.runCount != 0)
        return;

    gExpansionAoEReferenceProbe.enabled = 1;
    gExpansionAoEReferenceProbe.runCount = 1;
    gExpansionAoEReferenceProbe.aiPolicy = EXPANSION_AOE_AI_NEVER;
    gExpansionAoEReferenceProbe.animationPolicy = EXPANSION_AOE_ANIMATION_NONE;
    gExpansionAoEReferenceProbe.eventPolicy = EXPANSION_AOE_EVENT_NONE;
    gExpansionAoEReferenceProbe.savePolicy = EXPANSION_AOE_SAVE_ATOMIC_REBUILD;

    unit = NULL;
    for (unitIndex = 1; unitIndex < 0x40; unitIndex++)
    {
        unit = GetUnit(unitIndex);
        if (UNIT_IS_VALID(unit)
            && !(unit->state & (US_HIDDEN | US_DEAD | US_NOT_DEPLOYED | US_RESCUED))
            && unit->xPos >= 0
            && unit->yPos >= 0)
            break;

        unit = NULL;
    }

    if (unit == NULL)
    {
        gExpansionAoEReferenceProbe.buildResult = EXPANSION_AOE_ERR_ORIGIN;
        return;
    }

    memset(&shape, 0, sizeof(shape));
    shape.kind = EXPANSION_AOE_SHAPE_DIAMOND;
    shape.maxRange = EXPANSION_AOE_REFERENCE_RADIUS;

    memset(&origin, 0, sizeof(origin));
    origin.kind = EXPANSION_AOE_ORIGIN_UNIT;
    origin.sourceUnitId = (ExpansionAoEUnitId)unit->index;

    memset(&filter, 0, sizeof(filter));
    filter.relationMask = EXPANSION_AOE_TARGET_SOURCE | EXPANSION_AOE_TARGET_ALLIES;

    buildResult = ExpansionAoE_BuildTargetSet(&shape, &origin, &filter, 0, &nearby);
    gExpansionAoEReferenceProbe.buildResult = buildResult;
    gExpansionAoEReferenceProbe.sourceUnitId = origin.sourceUnitId;
    if (buildResult != EXPANSION_AOE_OK)
        return;

    selectedCount = 0;
    for (index = 0; index < nearby.count; index++)
    {
        unit = GetUnit(nearby.targets[index].unitId);
        originalHp[index] = (u8)GetUnitCurrentHp(unit);

        if (selectedCount < 2 && GetUnitCurrentHp(unit) > EXPANSION_AOE_REFERENCE_HEAL_AMOUNT)
        {
            selected[selectedCount] = nearby.targets[index].unitId;
            selectedCount++;
        }
    }

    if (selectedCount < 2)
    {
        gExpansionAoEReferenceProbe.buildResult = EXPANSION_AOE_ERR_FILTER;
        return;
    }

    for (index = 0; index < selectedCount; index++)
    {
        unit = GetUnit(selected[index]);
        SetUnitHp(unit, GetUnitCurrentHp(unit) - EXPANSION_AOE_REFERENCE_HEAL_AMOUNT);
    }

    gExpansionAoEReferenceProbe.firstTargetUnitId = selected[0];
    gExpansionAoEReferenceProbe.secondTargetUnitId = selected[1];
    gExpansionAoEReferenceProbe.firstHpBefore = GetUnitCurrentHp(GetUnit(selected[0]));
    gExpansionAoEReferenceProbe.secondHpBefore = GetUnitCurrentHp(GetUnit(selected[1]));

    applyResult = ExpansionAoEReference_Apply(origin.sourceUnitId, &affected, &execution);
    gExpansionAoEReferenceProbe.buildResult = applyResult;
    gExpansionAoEReferenceProbe.targetCount = affected.count;
    gExpansionAoEReferenceProbe.totalTargetCount = affected.totalCount;
    gExpansionAoEReferenceProbe.executionOutcome = execution.outcome;
    gExpansionAoEReferenceProbe.appliedCount = execution.appliedCount;
    gExpansionAoEReferenceProbe.skippedCount = execution.skippedCount;
    gExpansionAoEReferenceProbe.failedCount = execution.failedCount;
    gExpansionAoEReferenceProbe.expAwarded = execution.expAwarded;
    gExpansionAoEReferenceProbe.rangeTileCount = affected.rangeTileCount;
    gExpansionAoEReferenceProbe.legacyTargetCount = GetSelectTargetCount();
    gExpansionAoEReferenceProbe.firstHpAfter = GetUnitCurrentHp(GetUnit(selected[0]));
    gExpansionAoEReferenceProbe.secondHpAfter = GetUnitCurrentHp(GetUnit(selected[1]));

    firstIndex = ExpansionAoEReference_FindTarget(&affected, selected[0]);
    secondIndex = ExpansionAoEReference_FindTarget(&affected, selected[1]);

    restored = 1;
    for (index = 0; index < nearby.count; index++)
    {
        unit = GetUnit(nearby.targets[index].unitId);
        SetUnitHp(unit, originalHp[index]);
        if (GetUnitCurrentHp(unit) != originalHp[index])
            restored = 0;
    }

    BmMapFill(gBmMapRange, 0);
    InitTargets(0, 0);
    gExpansionAoEReferenceProbe.restoredOriginalHp = restored;

    if (applyResult == EXPANSION_AOE_OK
        && execution.outcome == EXPANSION_AOE_EXECUTION_OK
        && execution.appliedCount >= 2
        && firstIndex >= 0
        && secondIndex >= 0
        && firstIndex < secondIndex
        && gExpansionAoEReferenceProbe.firstHpAfter
            == gExpansionAoEReferenceProbe.firstHpBefore
                + EXPANSION_AOE_REFERENCE_HEAL_AMOUNT
        && gExpansionAoEReferenceProbe.secondHpAfter
            == gExpansionAoEReferenceProbe.secondHpBefore
                + EXPANSION_AOE_REFERENCE_HEAL_AMOUNT
        && restored)
        gExpansionAoEReferenceProbe.magic = EXPANSION_AOE_REFERENCE_PROBE_MAGIC;
#endif
}

#else /* !FE8_EXPANSION_AOE_REFERENCE */

int ExpansionAoEReference_IsEnabled(void)
{
    return 0;
}

enum ExpansionAoEResult ExpansionAoEReference_Apply(
    ExpansionAoEUnitId sourceUnitId,
    struct ExpansionAoETargetSet* targets,
    struct ExpansionAoEExecutionResult* result)
{
    (void)sourceUnitId;

    if (targets != NULL)
        memset(targets, 0, sizeof(*targets));

    if (result != NULL)
        memset(result, 0, sizeof(*result));

    return EXPANSION_AOE_ERR_DISABLED;
}

void ExpansionAoEReference_RunProbe(void)
{
}

#endif /* FE8_EXPANSION_AOE_REFERENCE */
