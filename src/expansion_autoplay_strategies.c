#include "global.h"

#include "bm.h"
#include "bmunit.h"
#include "cp_common.h"
#include "cp_script.h"
#include "cp_utility.h"
#include "eventinfo.h"

#include "expansion_autoplay.h"
#include "expansion_autoplay_internal.h"
#include "expansion_autoplay_strategies.h"

static const struct ExpansionAutoplayStrategyBundle* GetCurrentBundle(void)
{
    const struct ExpansionAutoplayStrategyBundle* bundle;

    for (bundle = gExpansionAutoplayStrategyBundles;
         bundle->chapterId != EXPANSION_AUTOPLAY_STRATEGY_CHAPTER_NONE;
         bundle++)
    {
        if (bundle->chapterId == (u16)(u8)gPlaySt.chapterIndex)
            return bundle;
    }

    return NULL;
}

static u8 GetRegistryCount(void)
{
    u8 count;

    for (count = 0; count <= EXPANSION_AUTOPLAY_STRATEGY_CAPACITY; count++)
        if (gExpansionAutoplayStrategies[count].id == 0)
            return count;

    return EXPANSION_AUTOPLAY_STRATEGY_CAPACITY + 1;
}

static const struct ExpansionAutoplayStrategy* FindStrategy(u32 id)
{
    u8 count = GetRegistryCount();
    u8 index;

    if (count > EXPANSION_AUTOPLAY_STRATEGY_CAPACITY)
        return NULL;

    for (index = 0; index < count; index++)
        if (gExpansionAutoplayStrategies[index].id == id)
            return &gExpansionAutoplayStrategies[index];

    return NULL;
}

static bool IsAssignmentActive(u16 activationFlag)
{
    return activationFlag == EXPANSION_AUTOPLAY_STRATEGY_FLAG_NONE || CheckFlag(activationFlag);
}

static enum ExpansionAutoplayStrategyResult ResolveAssignment(
    const struct ExpansionAutoplayStrategyBundle* bundle,
    u32* strategyIdOut)
{
    u8 character;
    u8 index;

    if (strategyIdOut != NULL)
        *strategyIdOut = 0;

    if (bundle == NULL || gActiveUnit == NULL || gActiveUnit->pCharacterData == NULL)
        return EXPANSION_AUTOPLAY_STRATEGY_FALLBACK;

    character = gActiveUnit->pCharacterData->number;

    for (index = 0; index < bundle->unitAssignmentCount; index++)
    {
        const struct ExpansionAutoplayStrategyUnitAssignment* assignment =
            &bundle->unitAssignments[index];

        if (assignment->character == character && IsAssignmentActive(assignment->activationFlag))
        {
            *strategyIdOut = assignment->strategyId;
            return EXPANSION_AUTOPLAY_STRATEGY_OK;
        }
    }

    for (index = 0; index < bundle->groupAssignmentCount; index++)
    {
        const struct ExpansionAutoplayStrategyGroupAssignment* assignment =
            &bundle->groupAssignments[index];

        if (ExpansionChapterObjectives_GroupContains(assignment->groupId, character)
            && IsAssignmentActive(assignment->activationFlag))
        {
            *strategyIdOut = assignment->strategyId;
            return EXPANSION_AUTOPLAY_STRATEGY_OK;
        }
    }

    if (bundle->chapterStrategyId != 0 && IsAssignmentActive(bundle->chapterActivationFlag))
    {
        *strategyIdOut = bundle->chapterStrategyId;
        return EXPANSION_AUTOPLAY_STRATEGY_OK;
    }

    return EXPANSION_AUTOPLAY_STRATEGY_FALLBACK;
}

static u32 ObjectiveCapabilityForKind(enum ExpansionChapterObjectiveKind kind)
{
    if (kind < EXPANSION_CHAPTER_OBJECTIVE_PROTECT
        || kind > EXPANSION_CHAPTER_OBJECTIVE_HOLD_UNTIL_TURN)
        return 0;

    return 1u << (kind - EXPANSION_CHAPTER_OBJECTIVE_PROTECT);
}

static enum ExpansionAutoplayStrategyResult ValidateStrategyForObjective(
    const struct ExpansionAutoplayStrategy* strategy,
    const struct ExpansionChapterObjective* objective)
{
    u32 capability;

    if (strategy == NULL)
        return EXPANSION_AUTOPLAY_STRATEGY_ERR_UNKNOWN_ID;

    if ((strategy->flags & EXPANSION_AUTOPLAY_STRATEGY_FLAG_REFERENCE_PROFILE)
        && !FE8_EXPANSION_AUTOPLAY_STRATEGIES)
    {
        return EXPANSION_AUTOPLAY_STRATEGY_ERR_PROFILE_DISABLED;
    }

    if (objective == NULL)
        return EXPANSION_AUTOPLAY_STRATEGY_OK;

    capability = ObjectiveCapabilityForKind(objective->kind);
    if (capability == 0 || !(strategy->objectiveCapabilities & capability))
        return EXPANSION_AUTOPLAY_STRATEGY_ERR_UNSUPPORTED_OBJECTIVE;

    return EXPANSION_AUTOPLAY_STRATEGY_OK;
}

enum ExpansionAutoplayStrategyResult ExpansionAutoplayStrategies_ValidateObjectiveSupport(
    u32 strategyId,
    enum ExpansionChapterObjectiveKind kind)
{
    const struct ExpansionAutoplayStrategy* strategy = FindStrategy(strategyId);
    u32 capability = ObjectiveCapabilityForKind(kind);
    enum ExpansionAutoplayStrategyResult result;

    result = ExpansionAutoplayStrategies_ValidateRegistry(
        gExpansionAutoplayStrategies, GetRegistryCount());
    if (result != EXPANSION_AUTOPLAY_STRATEGY_OK)
        return result;

    if (strategy == NULL)
        return EXPANSION_AUTOPLAY_STRATEGY_ERR_UNKNOWN_ID;

    if ((strategy->flags & EXPANSION_AUTOPLAY_STRATEGY_FLAG_REFERENCE_PROFILE)
        && !FE8_EXPANSION_AUTOPLAY_STRATEGIES)
    {
        return EXPANSION_AUTOPLAY_STRATEGY_ERR_PROFILE_DISABLED;
    }

    if (capability == 0 || !(strategy->objectiveCapabilities & capability))
        return EXPANSION_AUTOPLAY_STRATEGY_ERR_UNSUPPORTED_OBJECTIVE;

    return EXPANSION_AUTOPLAY_STRATEGY_OK;
}

enum ExpansionAutoplayStrategyResult ExpansionAutoplayStrategies_ValidateRegistry(
    const struct ExpansionAutoplayStrategy* registry,
    u8 count)
{
    u8 index;
    u8 other;

    if (count > EXPANSION_AUTOPLAY_STRATEGY_CAPACITY)
        return EXPANSION_AUTOPLAY_STRATEGY_ERR_CAPACITY;

    for (index = 0; index < count; index++)
    {
        const struct ExpansionAutoplayStrategy* strategy = &registry[index];

        if (strategy->id == 0)
            return EXPANSION_AUTOPLAY_STRATEGY_ERR_UNKNOWN_ID;

        if (strategy->callback == NULL)
            return EXPANSION_AUTOPLAY_STRATEGY_ERR_MISSING_CALLBACK;

        if (strategy->objectiveCapabilities & ~EXPANSION_AUTOPLAY_STRATEGY_OBJECTIVE_ALL
            || strategy->actionCapabilities & ~EXPANSION_AUTOPLAY_STRATEGY_ACTION_ALL
            || strategy->flags & ~EXPANSION_AUTOPLAY_STRATEGY_FLAG_ALL
            || strategy->actionCapabilities == 0)
        {
            return EXPANSION_AUTOPLAY_STRATEGY_ERR_UNSUPPORTED_CAPABILITY;
        }

        for (other = 0; other < index; other++)
            if (registry[other].id == strategy->id)
                return EXPANSION_AUTOPLAY_STRATEGY_ERR_DUPLICATE_ID;
    }

    return EXPANSION_AUTOPLAY_STRATEGY_OK;
}

enum ExpansionAutoplayStrategyResult ExpansionAutoplayStrategies_ValidateCurrentChapter(void)
{
    const struct ExpansionAutoplayStrategyBundle* bundle = GetCurrentBundle();
    const struct ExpansionAutoplayStrategy* strategy;
    const struct ExpansionChapterObjective* objective;
    enum ExpansionAutoplayStrategyResult result;
    u32 strategyId;
    u8 count = GetRegistryCount();

    result = ExpansionAutoplayStrategies_ValidateRegistry(gExpansionAutoplayStrategies, count);
    if (result != EXPANSION_AUTOPLAY_STRATEGY_OK)
        return result;

    result = ResolveAssignment(bundle, &strategyId);
    if (result != EXPANSION_AUTOPLAY_STRATEGY_OK)
        return result;

    strategy = FindStrategy(strategyId);
    objective = ExpansionChapterObjectives_GetActiveObjective();
    return ValidateStrategyForObjective(strategy, objective);
}

enum ExpansionAutoplayStrategyResult ExpansionAutoplayStrategies_TryDecide(void)
{
    const struct ExpansionAutoplayStrategyBundle* bundle = GetCurrentBundle();
    const struct ExpansionAutoplayStrategy* strategy;
    const struct ExpansionChapterObjective* objective;
    struct ExpansionAutoplayStrategyContext context;
    enum ExpansionAutoplayStrategyResult result;
    u32 strategyId;

    result = ExpansionAutoplayStrategies_ValidateRegistry(
        gExpansionAutoplayStrategies, GetRegistryCount());
    if (result != EXPANSION_AUTOPLAY_STRATEGY_OK)
        return result;

    result = ResolveAssignment(bundle, &strategyId);
    if (result != EXPANSION_AUTOPLAY_STRATEGY_OK)
        return result;

    strategy = FindStrategy(strategyId);
    ExpansionChapterObjectives_RefreshTelemetry();
    objective = ExpansionChapterObjectives_GetActiveObjective();
    result = ValidateStrategyForObjective(strategy, objective);
    if (result != EXPANSION_AUTOPLAY_STRATEGY_OK)
        return result;

    context.objective = objective;
    if (strategy->callback(&context))
        return EXPANSION_AUTOPLAY_STRATEGY_OK;

    return EXPANSION_AUTOPLAY_STRATEGY_FALLBACK;
}

enum ExpansionAutoplayStrategyResult ExpansionAutoplayStrategies_ActivateAssignment(
    u32 strategyId,
    u16 activationFlag)
{
    const struct ExpansionAutoplayStrategyBundle* bundle = GetCurrentBundle();
    const struct ExpansionAutoplayStrategy* strategy;
    const struct ExpansionChapterObjective* objective;
    enum ExpansionAutoplayStrategyResult result;
    u8 index;

    if (ExpansionAutoplay_IsBlueComputerPhase())
        return EXPANSION_AUTOPLAY_STRATEGY_ERR_PHASE_ACTIVE;

    if (bundle == NULL || activationFlag == EXPANSION_AUTOPLAY_STRATEGY_FLAG_NONE)
        return EXPANSION_AUTOPLAY_STRATEGY_ERR_INVALID_EVENT_ASSIGNMENT;

    result = ExpansionAutoplayStrategies_ValidateRegistry(
        gExpansionAutoplayStrategies, GetRegistryCount());
    if (result != EXPANSION_AUTOPLAY_STRATEGY_OK)
        return result;

    strategy = FindStrategy(strategyId);
    objective = ExpansionChapterObjectives_GetActiveObjective();
    {
        result = ValidateStrategyForObjective(strategy, objective);

        if (result != EXPANSION_AUTOPLAY_STRATEGY_OK)
            return result;
    }

    if (bundle->chapterStrategyId == strategyId && bundle->chapterActivationFlag == activationFlag)
    {
        SetFlag(activationFlag);
        return EXPANSION_AUTOPLAY_STRATEGY_OK;
    }

    for (index = 0; index < bundle->groupAssignmentCount; index++)
        if (bundle->groupAssignments[index].strategyId == strategyId
            && bundle->groupAssignments[index].activationFlag == activationFlag)
        {
            SetFlag(activationFlag);
            return EXPANSION_AUTOPLAY_STRATEGY_OK;
        }

    for (index = 0; index < bundle->unitAssignmentCount; index++)
        if (bundle->unitAssignments[index].strategyId == strategyId
            && bundle->unitAssignments[index].activationFlag == activationFlag)
        {
            SetFlag(activationFlag);
            return EXPANSION_AUTOPLAY_STRATEGY_OK;
        }

    return EXPANSION_AUTOPLAY_STRATEGY_ERR_INVALID_EVENT_ASSIGNMENT;
}

#if FE8_EXPANSION_AUTOPLAY_STRATEGIES
bool ExpansionAutoplayStrategy_Aggressive(const struct ExpansionAutoplayStrategyContext* context)
{
    (void)context;
    AiAttemptCombatWithinMovement(AiIsUnitEnemy);
    return gAiDecision.actionPerformed && gAiDecision.actionId == AI_ACTION_COMBAT;
}

bool ExpansionAutoplayStrategy_ObjectiveFirst(
    const struct ExpansionAutoplayStrategyContext* context)
{
    const struct ExpansionChapterObjective* objective = context->objective;
    int xTarget;
    int yTarget;

    if (objective != NULL
        && (objective->kind == EXPANSION_CHAPTER_OBJECTIVE_REACH_AREA
            || objective->kind == EXPANSION_CHAPTER_OBJECTIVE_HOLD_UNTIL_TURN)
        && ExpansionChapterObjectives_GroupContains(
            objective->group->id, gActiveUnit->pCharacterData->number))
    {
        xTarget = gActiveUnit->xPos;
        yTarget = gActiveUnit->yPos;

        if (xTarget < objective->xMin)
            xTarget = objective->xMin;
        else if (xTarget > objective->xMax)
            xTarget = objective->xMax;

        if (yTarget < objective->yMin)
            yTarget = objective->yMin;
        else if (yTarget > objective->yMax)
            yTarget = objective->yMax;

        if (xTarget != gActiveUnit->xPos || yTarget != gActiveUnit->yPos)
        {
            AiTryMoveTowards(xTarget, yTarget, 0, 0, 1);
            if (gAiDecision.actionPerformed
                && gAiDecision.xMove >= objective->xMin
                && gAiDecision.xMove <= objective->xMax
                && gAiDecision.yMove >= objective->yMin
                && gAiDecision.yMove <= objective->yMax)
                return true;
        }

        if (objective->kind == EXPANSION_CHAPTER_OBJECTIVE_HOLD_UNTIL_TURN)
        {
            AiAttemptCombatWithinMovement(AiIsUnitEnemy);
            if (gAiDecision.actionPerformed
                && gAiDecision.xMove >= objective->xMin
                && gAiDecision.xMove <= objective->xMax
                && gAiDecision.yMove >= objective->yMin
                && gAiDecision.yMove <= objective->yMax)
            {
                return true;
            }

            gAiDecision.actionPerformed = false;
            return true;
        }
    }

    return ExpansionAutoplayStrategy_Aggressive(context);
}
#endif
