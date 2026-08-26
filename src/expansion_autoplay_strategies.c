#include "global.h"

#include "bm.h"
#include "bmmap.h"
#include "bmidoten.h"
#include "bmunit.h"
#include "cp_common.h"
#include "cp_script.h"
#include "cp_utility.h"
#include "eventinfo.h"
#include "event.h"

#include "expansion_autoplay.h"
#include "expansion_autoplay_internal.h"
#include "expansion_autoplay_strategies.h"

struct ExpansionAutoplayPendingActivation
{
    u32 strategyId;
    u16 activationFlag;
    u8 chapterId;
    u8 operation;
};

typedef char ExpansionAutoplayPendingActivationSizeCheck[
    sizeof(struct ExpansionAutoplayPendingActivation) == 8 ? 1 : -1];

enum ExpansionAutoplayPendingOperation
{
    EXPANSION_AUTOPLAY_PENDING_NONE,
    EXPANSION_AUTOPLAY_PENDING_ACTIVATE,
    EXPANSION_AUTOPLAY_PENDING_DEACTIVATE,
};

EWRAM_DATA static struct ExpansionAutoplayPendingActivation
    sExpansionAutoplayPendingActivation = { 0 };

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

bool ExpansionAutoplayStrategies_HasStrategies(void)
{
    return GetRegistryCount() != 0;
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

static bool IsDecisionSupported(const struct ExpansionAutoplayStrategy* strategy)
{
    u32 capability;

    if (!gAiDecision.actionPerformed)
        return true;

    switch (gAiDecision.actionId)
    {
    case AI_ACTION_COMBAT:
        capability = EXPANSION_AUTOPLAY_STRATEGY_ACTION_COMBAT;
        break;

    case AI_ACTION_NONE:
        capability = EXPANSION_AUTOPLAY_STRATEGY_ACTION_OBJECTIVE_MOVE;
        break;

    default:
        return false;
    }

    return (strategy->actionCapabilities & capability) != 0;
}

static void PrepareCombatMovementMap(void)
{
    AiGenerateUnitMovementMapRespectStay(gActiveUnit);
    if (UnitHasMagicRank(gActiveUnit))
        GenerateMagicSealMap(-1);
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
    {
        if (!IsDecisionSupported(strategy))
        {
            AiClearDecision();
            return EXPANSION_AUTOPLAY_STRATEGY_ERR_UNSUPPORTED_CAPABILITY;
        }

#if FE8_AUTOPLAY_STRATEGY_RUNTIME_TEST
        if (strategy->id == EXPANSION_AUTOPLAY_STRATEGY_OBJECTIVE_FIRST_ID)
        {
            gExpansionAutoplayStrategyRuntimeProbe.objectiveFirstCount++;
            gExpansionAutoplayStrategyRuntimeProbe.objectiveFirstObjectiveId =
                objective != NULL ? objective->id : 0;
            gExpansionAutoplayStrategyRuntimeProbe.objectiveFirstActionId =
                gAiDecision.actionId;
            gExpansionAutoplayStrategyRuntimeProbe.objectiveFirstX = gAiDecision.xMove;
            gExpansionAutoplayStrategyRuntimeProbe.objectiveFirstY = gAiDecision.yMove;
        }
        else if (strategy->id == EXPANSION_AUTOPLAY_STRATEGY_AGGRESSIVE_ID)
        {
            gExpansionAutoplayStrategyRuntimeProbe.aggressiveCount++;
            gExpansionAutoplayStrategyRuntimeProbe.aggressiveActionId =
                gAiDecision.actionId;
        }

        gExpansionAutoplayStrategyRuntimeProbe.magic = 0x53545254;
#endif
        return EXPANSION_AUTOPLAY_STRATEGY_OK;
    }

    AiClearDecision();
    return EXPANSION_AUTOPLAY_STRATEGY_FALLBACK;
}

static enum ExpansionAutoplayStrategyResult ValidateActivationPair(
    const struct ExpansionAutoplayStrategyBundle* bundle,
    u32 strategyId,
    u16 activationFlag)
{
    const struct ExpansionAutoplayStrategy* strategy;
    const struct ExpansionChapterObjective* objective;
    enum ExpansionAutoplayStrategyResult result;
    u8 index;

    if (bundle == NULL || activationFlag == EXPANSION_AUTOPLAY_STRATEGY_FLAG_NONE)
        return EXPANSION_AUTOPLAY_STRATEGY_ERR_INVALID_EVENT_ASSIGNMENT;

    result = ExpansionAutoplayStrategies_ValidateRegistry(
        gExpansionAutoplayStrategies, GetRegistryCount());
    if (result != EXPANSION_AUTOPLAY_STRATEGY_OK)
        return result;

    strategy = FindStrategy(strategyId);
    objective = ExpansionChapterObjectives_GetActiveObjective();
    result = ValidateStrategyForObjective(strategy, objective);
    if (result != EXPANSION_AUTOPLAY_STRATEGY_OK)
        return result;

    if (bundle->chapterStrategyId == strategyId && bundle->chapterActivationFlag == activationFlag)
        return EXPANSION_AUTOPLAY_STRATEGY_OK;

    for (index = 0; index < bundle->groupAssignmentCount; index++)
        if (bundle->groupAssignments[index].strategyId == strategyId
            && bundle->groupAssignments[index].activationFlag == activationFlag)
            return EXPANSION_AUTOPLAY_STRATEGY_OK;

    for (index = 0; index < bundle->unitAssignmentCount; index++)
        if (bundle->unitAssignments[index].strategyId == strategyId
            && bundle->unitAssignments[index].activationFlag == activationFlag)
            return EXPANSION_AUTOPLAY_STRATEGY_OK;

    return EXPANSION_AUTOPLAY_STRATEGY_ERR_INVALID_EVENT_ASSIGNMENT;
}

static void ApplyActivationFlag(
    u16 activationFlag,
    enum ExpansionAutoplayPendingOperation operation)
{
    if (operation == EXPANSION_AUTOPLAY_PENDING_ACTIVATE)
        SetFlag(activationFlag);
    else if (operation == EXPANSION_AUTOPLAY_PENDING_DEACTIVATE)
        ClearFlag(activationFlag);
}

static enum ExpansionAutoplayStrategyResult ChangeAssignmentActivation(
    u32 strategyId,
    u16 activationFlag,
    enum ExpansionAutoplayPendingOperation operation)
{
    enum ExpansionAutoplayStrategyResult result =
        ValidateActivationPair(GetCurrentBundle(), strategyId, activationFlag);

    if (result != EXPANSION_AUTOPLAY_STRATEGY_OK)
        return result;

    if (ExpansionAutoplay_IsBlueComputerPhase())
        return EXPANSION_AUTOPLAY_STRATEGY_ERR_PHASE_ACTIVE;

    ApplyActivationFlag(activationFlag, operation);
    return EXPANSION_AUTOPLAY_STRATEGY_OK;
}

enum ExpansionAutoplayStrategyResult ExpansionAutoplayStrategies_ActivateAssignment(
    u32 strategyId,
    u16 activationFlag)
{
    return ChangeAssignmentActivation(
        strategyId,
        activationFlag,
        EXPANSION_AUTOPLAY_PENDING_ACTIVATE
    );
}

enum ExpansionAutoplayStrategyResult ExpansionAutoplayStrategies_DeactivateAssignment(
    u32 strategyId,
    u16 activationFlag)
{
    return ChangeAssignmentActivation(
        strategyId,
        activationFlag,
        EXPANSION_AUTOPLAY_PENDING_DEACTIVATE
    );
}

void ExpansionAutoplayStrategies_ResetPendingActivation(void)
{
    sExpansionAutoplayPendingActivation.strategyId = 0;
    sExpansionAutoplayPendingActivation.activationFlag = 0;
    sExpansionAutoplayPendingActivation.chapterId =
        (u8)EXPANSION_AUTOPLAY_STRATEGY_CHAPTER_NONE;
    sExpansionAutoplayPendingActivation.operation =
        EXPANSION_AUTOPLAY_PENDING_NONE;
}

void ExpansionAutoplayStrategies_ApplyPendingActivation(void)
{
    u16 activationFlag;
    enum ExpansionAutoplayPendingOperation operation;

    if (sExpansionAutoplayPendingActivation.strategyId == 0)
        return;

    if (sExpansionAutoplayPendingActivation.chapterId
        != (u8)gPlaySt.chapterIndex)
    {
        ExpansionAutoplayStrategies_ResetPendingActivation();
        return;
    }

    activationFlag = sExpansionAutoplayPendingActivation.activationFlag;
    operation = sExpansionAutoplayPendingActivation.operation;
    ExpansionAutoplayStrategies_ResetPendingActivation();
    ApplyActivationFlag(activationFlag, operation);
}

static void EventChangeAssignmentActivation(
    enum ExpansionAutoplayPendingOperation operation)
{
    u32 strategyId = gEventSlots[EVT_SLOT_B];
    u16 activationFlag = (u16)gEventSlots[EVT_SLOT_C];
    enum ExpansionAutoplayStrategyResult result;

    result = ChangeAssignmentActivation(strategyId, activationFlag, operation);
    if (result != EXPANSION_AUTOPLAY_STRATEGY_ERR_PHASE_ACTIVE)
        return;

    sExpansionAutoplayPendingActivation.strategyId = strategyId;
    sExpansionAutoplayPendingActivation.activationFlag = activationFlag;
    sExpansionAutoplayPendingActivation.chapterId =
        (u8)gPlaySt.chapterIndex;
    sExpansionAutoplayPendingActivation.operation = operation;
}

void ExpansionAutoplayStrategies_EventActivate(struct EventEngineProc* proc)
{
    (void)proc;
    EventChangeAssignmentActivation(EXPANSION_AUTOPLAY_PENDING_ACTIVATE);
}

void ExpansionAutoplayStrategies_EventDeactivate(struct EventEngineProc* proc)
{
    (void)proc;
    EventChangeAssignmentActivation(EXPANSION_AUTOPLAY_PENDING_DEACTIVATE);
}

#if FE8_EXPANSION_AUTOPLAY_STRATEGIES
bool ExpansionAutoplayStrategy_Aggressive(const struct ExpansionAutoplayStrategyContext* context)
{
    (void)context;
    PrepareCombatMovementMap();
    AiAttemptCombatWithinMovement(AiIsUnitEnemy);
    return gAiDecision.actionPerformed && gAiDecision.actionId == AI_ACTION_COMBAT;
}

bool ExpansionAutoplayStrategy_ObjectiveFirst(
    const struct ExpansionAutoplayStrategyContext* context)
{
    const struct ExpansionChapterObjective* objective = context->objective;
    enum ExpansionChapterObjectiveState state;
    u32 progress;
    int xTarget;
    int yTarget;

    if (objective == NULL)
        return ExpansionAutoplayStrategy_Aggressive(context);

    state = ExpansionChapterObjectives_GetStatus(objective->id, &progress);
    if (state != EXPANSION_CHAPTER_OBJECTIVE_PENDING)
        return ExpansionAutoplayStrategy_Aggressive(context);

    if ((objective->kind == EXPANSION_CHAPTER_OBJECTIVE_REACH_AREA
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
            u8 currentRange;
            u8 decisionRange;

            AiTryMoveTowards(xTarget, yTarget, 0, 0, 1);
            if (!gAiDecision.actionPerformed)
                return true;

            if (gAiDecision.xMove < 0 || gAiDecision.xMove >= gBmMapSize.x
                || gAiDecision.yMove < 0 || gAiDecision.yMove >= gBmMapSize.y)
            {
                gAiDecision.actionPerformed = false;
                return true;
            }

            currentRange = gBmMapRange[gActiveUnit->yPos][gActiveUnit->xPos];
            decisionRange = gBmMapRange[gAiDecision.yMove][gAiDecision.xMove];
            if (gAiDecision.actionPerformed
                && currentRange < MAP_MOVEMENT_MAX
                && decisionRange < currentRange)
                return true;

            gAiDecision.actionPerformed = false;
            return true;
        }

        PrepareCombatMovementMap();
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

    return ExpansionAutoplayStrategy_Aggressive(context);
}
#endif

#if FE8_AUTOPLAY_STRATEGY_RUNTIME_TEST
struct ExpansionAutoplayStrategyRuntimeProbe EWRAM_DATA
    gExpansionAutoplayStrategyRuntimeProbe = { 0 };
#endif
