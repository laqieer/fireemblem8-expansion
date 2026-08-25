#include "global.h"

#include "bm.h"
#include "bmunit.h"
#include "eventinfo.h"

#include "expansion_chapter_objectives.h"

struct ExpansionChapterObjectiveTelemetry EWRAM_DATA
    gExpansionChapterObjectiveTelemetry = { 0 };
static bool8 EWRAM_DATA sExpansionChapterObjectivesReady = FALSE;

struct ObjectiveResult
{
    enum ExpansionChapterObjectiveState state;
    u32 progress;
};

enum UnitObjectiveState
{
    UNIT_OBJECTIVE_MISSING,
    UNIT_OBJECTIVE_ALIVE,
    UNIT_OBJECTIVE_DEAD,
    UNIT_OBJECTIVE_RESCUED,
};

enum
{
    EXPANSION_CHAPTER_OBJECTIVE_UNIT_SLOT_COUNT = 0x100,
};

struct ObjectiveEvaluationContext
{
    struct Unit* unitByCharacter[EXPANSION_CHAPTER_OBJECTIVE_UNIT_SLOT_COUNT];
};

static const struct ExpansionChapterObjectiveBundle* GetCurrentBundle(void)
{
    const struct ExpansionChapterObjectiveBundle* bundle;

    for (bundle = gExpansionChapterObjectiveBundles;
         bundle->chapterId != EXPANSION_CHAPTER_OBJECTIVE_CHAPTER_NONE;
         bundle++)
    {
        if (bundle->chapterId == (u16)(u8)gPlaySt.chapterIndex)
            return bundle;
    }

    return NULL;
}

static enum UnitObjectiveState GetUnitObjectiveStateFromUnit(const struct Unit* unit)
{
    if (unit == NULL || unit->pCharacterData == NULL)
        return UNIT_OBJECTIVE_MISSING;

    if (unit->state & US_DEAD)
        return UNIT_OBJECTIVE_DEAD;

    if (unit->state & US_RESCUED)
        return UNIT_OBJECTIVE_RESCUED;

    if (unit->state & US_UNAVAILABLE)
        return UNIT_OBJECTIVE_MISSING;

    return UNIT_OBJECTIVE_ALIVE;
}

static void BuildObjectiveEvaluationContext(struct ObjectiveEvaluationContext* context)
{
    int unitId;

    /*
     * GetUnitFromCharId scans all 255 slots. Index the current roster once
     * for the map-task refresh so every authored group member is O(1).
     */
    for (unitId = 1; unitId < EXPANSION_CHAPTER_OBJECTIVE_UNIT_SLOT_COUNT; unitId++)
    {
        struct Unit* unit = GetUnit(unitId);

        if (unit != NULL && unit->pCharacterData != NULL
            && context->unitByCharacter[unit->pCharacterData->number] == NULL)
        {
            context->unitByCharacter[unit->pCharacterData->number] = unit;
        }
    }
}

static struct Unit* GetObjectiveUnit(
    const struct ObjectiveEvaluationContext* context, u8 character)
{
    if (context != NULL)
        return context->unitByCharacter[character];

    return GetUnitFromCharId(character);
}

static enum UnitObjectiveState GetUnitObjectiveState(
    const struct ObjectiveEvaluationContext* context, u8 character)
{
    return GetUnitObjectiveStateFromUnit(GetObjectiveUnit(context, character));
}

static struct ObjectiveResult GetGroupAreaResult(
    const struct ObjectiveEvaluationContext* context,
    const struct ExpansionChapterAiGroup* group,
    const struct ExpansionChapterObjective* objective)
{
    struct ObjectiveResult result = { EXPANSION_CHAPTER_OBJECTIVE_PENDING, 0 };
    int index;

    if (group == NULL)
        return result;

    for (index = 0; index < group->memberCount; index++)
    {
        struct Unit* unit = GetObjectiveUnit(context, group->members[index]);
        enum UnitObjectiveState unitState = GetUnitObjectiveStateFromUnit(unit);

        if (unitState == UNIT_OBJECTIVE_DEAD)
        {
            result.state = EXPANSION_CHAPTER_OBJECTIVE_FAILURE;
            return result;
        }

        if (unitState != UNIT_OBJECTIVE_ALIVE)
            continue;

        if (unit->xPos >= objective->xMin && unit->xPos <= objective->xMax
            && unit->yPos >= objective->yMin && unit->yPos <= objective->yMax)
        {
            result.progress++;
        }
    }

    if (result.progress == group->memberCount)
        result.state = EXPANSION_CHAPTER_OBJECTIVE_SUCCESS;

    return result;
}

static struct ObjectiveResult EvaluateObjective(
    const struct ObjectiveEvaluationContext* context,
    const struct ExpansionChapterObjectiveBundle* bundle,
    const struct ExpansionChapterObjective* objective,
    int depth)
{
    struct ObjectiveResult result = { EXPANSION_CHAPTER_OBJECTIVE_PENDING, 0 };

    if (objective->activationFlag != EXPANSION_CHAPTER_OBJECTIVE_FLAG_NONE
        && !CheckFlag(objective->activationFlag))
    {
        result.state = EXPANSION_CHAPTER_OBJECTIVE_INACTIVE;
        return result;
    }

    if (objective->deactivationFlag != EXPANSION_CHAPTER_OBJECTIVE_FLAG_NONE
        && CheckFlag(objective->deactivationFlag))
    {
        result.state = EXPANSION_CHAPTER_OBJECTIVE_INACTIVE;
        return result;
    }

    switch (objective->kind)
    {
    case EXPANSION_CHAPTER_OBJECTIVE_PROTECT:
    {
        const struct ExpansionChapterObjective* completion = NULL;
        enum UnitObjectiveState protectedState;
        int index;

        if (CheckFlag(objective->eventFlag))
        {
            result.state = EXPANSION_CHAPTER_OBJECTIVE_FAILURE;
            return result;
        }

        if (CheckFlag(objective->completionFlag))
        {
            result.state = EXPANSION_CHAPTER_OBJECTIVE_SUCCESS;
            return result;
        }

        if (depth >= EXPANSION_CHAPTER_OBJECTIVE_PER_CHAPTER_CAPACITY)
        {
            result.state = EXPANSION_CHAPTER_OBJECTIVE_FAILURE;
            return result;
        }

        for (index = 0; index < bundle->objectiveCount; index++)
            if (bundle->objectives[index].id == objective->completionObjectiveId)
                completion = &bundle->objectives[index];

        if (completion == NULL)
        {
            result.state = EXPANSION_CHAPTER_OBJECTIVE_FAILURE;
            return result;
        }

        result = EvaluateObjective(context, bundle, completion, depth + 1);
        if (result.state == EXPANSION_CHAPTER_OBJECTIVE_SUCCESS)
        {
            SetFlag(objective->completionFlag);
            return result;
        }

        if (result.state == EXPANSION_CHAPTER_OBJECTIVE_INACTIVE)
            result.state = EXPANSION_CHAPTER_OBJECTIVE_PENDING;

        if (result.state != EXPANSION_CHAPTER_OBJECTIVE_PENDING)
            return result;

        protectedState = GetUnitObjectiveState(context, objective->protectedCharacter);
        if (protectedState != UNIT_OBJECTIVE_ALIVE)
        {
            SetFlag(objective->eventFlag);
            result.state = EXPANSION_CHAPTER_OBJECTIVE_FAILURE;
            result.progress = 0;
            return result;
        }

        result.progress = 1;
        return result;
    }

    case EXPANSION_CHAPTER_OBJECTIVE_REACH_AREA:
        return GetGroupAreaResult(context, objective->group, objective);

    case EXPANSION_CHAPTER_OBJECTIVE_DEFEAT_GROUP:
    {
        int index;
        enum UnitObjectiveState unitState;

        if (objective->group == NULL)
        {
            result.state = EXPANSION_CHAPTER_OBJECTIVE_FAILURE;
            return result;
        }

        result.progress = objective->group->memberCount;
        for (index = 0; index < objective->group->memberCount; index++)
        {
            unitState = GetUnitObjectiveState(context, objective->group->members[index]);
            if (unitState == UNIT_OBJECTIVE_ALIVE || unitState == UNIT_OBJECTIVE_RESCUED)
                result.progress--;
        }

        if (result.progress == objective->group->memberCount)
            result.state = EXPANSION_CHAPTER_OBJECTIVE_SUCCESS;
        return result;
    }

    case EXPANSION_CHAPTER_OBJECTIVE_EVENT_FLAG:
        result.progress = CheckFlag(objective->eventFlag) ? 1 : 0;
        if (result.progress != 0)
            result.state = EXPANSION_CHAPTER_OBJECTIVE_SUCCESS;
        return result;

    case EXPANSION_CHAPTER_OBJECTIVE_HOLD_UNTIL_TURN:
        if (CheckFlag(objective->eventFlag))
        {
            result.state = EXPANSION_CHAPTER_OBJECTIVE_FAILURE;
            return result;
        }

        if (gPlaySt.chapterTurnNumber >= objective->untilTurn)
        {
            result.state = EXPANSION_CHAPTER_OBJECTIVE_SUCCESS;
            result.progress = objective->group != NULL ? objective->group->memberCount : 0;
            return result;
        }

        result = GetGroupAreaResult(context, objective->group, objective);
        if (result.state != EXPANSION_CHAPTER_OBJECTIVE_SUCCESS)
        {
            SetFlag(objective->eventFlag);
            result.state = EXPANSION_CHAPTER_OBJECTIVE_FAILURE;
            return result;
        }

        result.state = EXPANSION_CHAPTER_OBJECTIVE_PENDING;
        return result;

    default:
        result.state = EXPANSION_CHAPTER_OBJECTIVE_FAILURE;
        return result;
    }
}

static int ObjectivePriority(enum ExpansionChapterObjectiveState state)
{
    switch (state)
    {
    case EXPANSION_CHAPTER_OBJECTIVE_FAILURE:
        return 3;

    case EXPANSION_CHAPTER_OBJECTIVE_PENDING:
        return 2;

    case EXPANSION_CHAPTER_OBJECTIVE_SUCCESS:
        return 1;

    default:
        return 0;
    }
}

#if FE8_CHAPTER_OBJECTIVES_RUNTIME_TEST

#define EXPANSION_CHAPTER_OBJECTIVE_RUNTIME_PROBE_MAGIC 0x4F424A54

struct ExpansionChapterObjectiveRuntimeProbe EWRAM_DATA
    gExpansionChapterObjectiveRuntimeProbe = { 0 };

static bool8 EWRAM_DATA sExpansionChapterObjectiveRuntimeProbeComplete = FALSE;

static const struct ExpansionChapterObjective* FindObjectiveByKind(
    const struct ExpansionChapterObjectiveBundle* bundle,
    enum ExpansionChapterObjectiveKind kind)
{
    int index;

    for (index = 0; index < bundle->objectiveCount; index++)
        if (bundle->objectives[index].kind == kind)
            return &bundle->objectives[index];

    return NULL;
}

static void RunChapterObjectiveRuntimeProbe(const struct ExpansionChapterObjectiveBundle* bundle)
{
    const struct ExpansionChapterObjective* eventObjective;
    const struct ExpansionChapterObjective* reachObjective;
    const struct ExpansionChapterObjective* defeatObjective;
    const struct ExpansionChapterObjective* holdObjective;
    const struct ExpansionChapterObjective* protectObjective;
    struct Unit* protectedUnit;
    enum ExpansionChapterObjectiveState state;
    u32 progress;
    u32 originalUnitState;
    int originalX;
    int originalY;
    int originalTurn;
    bool8 eventFlagWasSet;
    bool8 holdFailureFlagWasSet;
    bool8 protectCompletionFlagWasSet;

    if (sExpansionChapterObjectiveRuntimeProbeComplete)
        return;

    eventObjective = FindObjectiveByKind(bundle, EXPANSION_CHAPTER_OBJECTIVE_EVENT_FLAG);
    reachObjective = FindObjectiveByKind(bundle, EXPANSION_CHAPTER_OBJECTIVE_REACH_AREA);
    defeatObjective = FindObjectiveByKind(bundle, EXPANSION_CHAPTER_OBJECTIVE_DEFEAT_GROUP);
    holdObjective = FindObjectiveByKind(bundle, EXPANSION_CHAPTER_OBJECTIVE_HOLD_UNTIL_TURN);
    protectObjective = FindObjectiveByKind(bundle, EXPANSION_CHAPTER_OBJECTIVE_PROTECT);
    if (eventObjective == NULL || reachObjective == NULL || defeatObjective == NULL || holdObjective == NULL
        || protectObjective == NULL)
    {
        return;
    }

    protectedUnit = GetUnitFromCharId(protectObjective->protectedCharacter);
    if (protectedUnit == NULL || protectedUnit->pCharacterData == NULL)
        return;

    if (CheckFlag(protectObjective->eventFlag))
    {
        sExpansionChapterObjectiveRuntimeProbeComplete = TRUE;
        state = ExpansionChapterObjectives_GetStatus(protectObjective->id, &progress);
        gExpansionChapterObjectiveRuntimeProbe.protectLatchReconstructionState = state;
        gExpansionChapterObjectiveRuntimeProbe.replayMutationCount = 0;
        gExpansionChapterObjectiveRuntimeProbe.magic = EXPANSION_CHAPTER_OBJECTIVE_RUNTIME_PROBE_MAGIC;
        return;
    }

    sExpansionChapterObjectiveRuntimeProbeComplete = TRUE;
    eventFlagWasSet = CheckFlag(eventObjective->eventFlag);
    originalUnitState = protectedUnit->state;
    originalX = protectedUnit->xPos;
    originalY = protectedUnit->yPos;
    originalTurn = gPlaySt.chapterTurnNumber;
    holdFailureFlagWasSet = CheckFlag(holdObjective->eventFlag);
    protectCompletionFlagWasSet = CheckFlag(protectObjective->completionFlag);

    ClearFlag(eventObjective->eventFlag);
    state = ExpansionChapterObjectives_GetStatus(eventObjective->id, &progress);
    gExpansionChapterObjectiveRuntimeProbe.pendingId = eventObjective->id;
    gExpansionChapterObjectiveRuntimeProbe.pendingState = state;
    gExpansionChapterObjectiveRuntimeProbe.pendingProgress = progress;

    SetFlag(eventObjective->eventFlag);
    state = ExpansionChapterObjectives_GetStatus(eventObjective->id, &progress);
    gExpansionChapterObjectiveRuntimeProbe.eventState = state;
    gExpansionChapterObjectiveRuntimeProbe.eventProgress = progress;

    protectedUnit->xPos = 63;
    protectedUnit->yPos = 63;
    ExpansionChapterObjectives_GetStatus(reachObjective->id, &progress);
    gExpansionChapterObjectiveRuntimeProbe.reachPendingProgress = progress;

    protectedUnit->xPos = reachObjective->xMin;
    protectedUnit->yPos = reachObjective->yMin;
    ExpansionChapterObjectives_GetStatus(reachObjective->id, &progress);
    gExpansionChapterObjectiveRuntimeProbe.reachSuccessProgress = progress;

    ClearFlag(eventObjective->eventFlag);
    ClearFlag(protectObjective->eventFlag);
    ClearFlag(protectObjective->completionFlag);
    protectedUnit->state |= US_DEAD;
    state = ExpansionChapterObjectives_GetStatus(protectObjective->id, &progress);
    gExpansionChapterObjectiveRuntimeProbe.protectFailureState = state;

    ClearFlag(protectObjective->eventFlag);
    SetFlag(eventObjective->eventFlag);
    state = ExpansionChapterObjectives_GetStatus(defeatObjective->id, &progress);
    gExpansionChapterObjectiveRuntimeProbe.defeatSuccessState = state;
    state = ExpansionChapterObjectives_GetStatus(protectObjective->id, &progress);
    gExpansionChapterObjectiveRuntimeProbe.protectCompletionThenDeathState = state;

    ClearFlag(eventObjective->eventFlag);
    ClearFlag(protectObjective->eventFlag);
    ClearFlag(protectObjective->completionFlag);
    protectedUnit->state |= US_DEAD;
    ExpansionChapterObjectives_GetStatus(protectObjective->id, &progress);
    protectedUnit->state = originalUnitState;
    state = ExpansionChapterObjectives_GetStatus(protectObjective->id, &progress);
    gExpansionChapterObjectiveRuntimeProbe.protectLatchReconstructionState = state;

    ClearFlag(holdObjective->eventFlag);
    protectedUnit->xPos = 63;
    protectedUnit->yPos = 63;
    state = ExpansionChapterObjectives_GetStatus(holdObjective->id, &progress);
    gExpansionChapterObjectiveRuntimeProbe.holdViolationState = state;

    protectedUnit->xPos = holdObjective->xMin;
    protectedUnit->yPos = holdObjective->yMin;
    gPlaySt.chapterTurnNumber = holdObjective->untilTurn;
    state = ExpansionChapterObjectives_GetStatus(holdObjective->id, &progress);
    gExpansionChapterObjectiveRuntimeProbe.holdReentryState = state;

    protectedUnit->state = originalUnitState;
    protectedUnit->xPos = originalX;
    protectedUnit->yPos = originalY;
    gPlaySt.chapterTurnNumber = originalTurn;
    if (eventFlagWasSet)
        SetFlag(eventObjective->eventFlag);
    else
        ClearFlag(eventObjective->eventFlag);
    if (holdFailureFlagWasSet)
        SetFlag(holdObjective->eventFlag);
    else
        ClearFlag(holdObjective->eventFlag);
    if (protectCompletionFlagWasSet)
        SetFlag(protectObjective->completionFlag);
    else
        ClearFlag(protectObjective->completionFlag);
    SetFlag(protectObjective->eventFlag);
    gExpansionChapterObjectiveRuntimeProbe.replayMutationCount = 1;

    gExpansionChapterObjectiveRuntimeProbe.magic = EXPANSION_CHAPTER_OBJECTIVE_RUNTIME_PROBE_MAGIC;
}

#endif

void ExpansionChapterObjectives_ResetTelemetry(void)
{
    sExpansionChapterObjectivesReady = FALSE;
    gExpansionChapterObjectiveTelemetry.objectiveId = 0;
    gExpansionChapterObjectiveTelemetry.state = EXPANSION_CHAPTER_OBJECTIVE_INACTIVE;
    gExpansionChapterObjectiveTelemetry.progress = 0;
    gExpansionChapterObjectiveTelemetry.activeCount = 0;
}

void ExpansionChapterObjectives_OnBeginningEventsComplete(void)
{
    sExpansionChapterObjectivesReady = TRUE;
    gExpansionChapterObjectiveTelemetry.objectiveId = 0;
    gExpansionChapterObjectiveTelemetry.state = EXPANSION_CHAPTER_OBJECTIVE_INACTIVE;
    gExpansionChapterObjectiveTelemetry.progress = 0;
    gExpansionChapterObjectiveTelemetry.activeCount = 0;
}

void ExpansionChapterObjectives_RefreshTelemetry(void)
{
    const struct ExpansionChapterObjectiveBundle* bundle;
    struct ObjectiveEvaluationContext context = { { NULL } };
    struct ObjectiveResult selected = { EXPANSION_CHAPTER_OBJECTIVE_INACTIVE, 0 };
    u32 selectedId = 0;
    int selectedPriority = 0;
    int index;

    /*
     * The default generated table is only its sentinel. Do not touch
     * telemetry or walk chapter state from the per-frame map task unless a
     * chapter actually authored an objective bundle.
     */
    if (gExpansionChapterObjectiveBundles[0].chapterId
        == EXPANSION_CHAPTER_OBJECTIVE_CHAPTER_NONE)
    {
        return;
    }

    if (!sExpansionChapterObjectivesReady)
    {
        gExpansionChapterObjectiveTelemetry.objectiveId = 0;
        gExpansionChapterObjectiveTelemetry.state = EXPANSION_CHAPTER_OBJECTIVE_INACTIVE;
        gExpansionChapterObjectiveTelemetry.progress = 0;
        gExpansionChapterObjectiveTelemetry.activeCount = 0;
        return;
    }

    bundle = GetCurrentBundle();
    gExpansionChapterObjectiveTelemetry.objectiveId = 0;
    gExpansionChapterObjectiveTelemetry.state = EXPANSION_CHAPTER_OBJECTIVE_INACTIVE;
    gExpansionChapterObjectiveTelemetry.progress = 0;
    gExpansionChapterObjectiveTelemetry.activeCount = 0;
    if (bundle == NULL)
        return;

    BuildObjectiveEvaluationContext(&context);

#if FE8_CHAPTER_OBJECTIVES_RUNTIME_TEST
    RunChapterObjectiveRuntimeProbe(bundle);
#endif

    for (index = 0; index < bundle->objectiveCount; index++)
    {
        const struct ExpansionChapterObjective* objective = &bundle->objectives[index];
        struct ObjectiveResult result = EvaluateObjective(&context, bundle, objective, 0);
        int priority = ObjectivePriority(result.state);

        if (result.state != EXPANSION_CHAPTER_OBJECTIVE_INACTIVE)
            gExpansionChapterObjectiveTelemetry.activeCount++;

        if (priority > selectedPriority)
        {
            selected = result;
            selectedId = objective->id;
            selectedPriority = priority;
        }
    }

    gExpansionChapterObjectiveTelemetry.objectiveId = selectedId;
    gExpansionChapterObjectiveTelemetry.state = selected.state;
    gExpansionChapterObjectiveTelemetry.progress = selected.progress;
}

enum ExpansionChapterObjectiveState ExpansionChapterObjectives_GetStatus(u32 objectiveId, u32* progressOut)
{
    const struct ExpansionChapterObjectiveBundle* bundle = GetCurrentBundle();
    int index;

    if (progressOut != NULL)
        *progressOut = 0;

    if (!sExpansionChapterObjectivesReady)
        return EXPANSION_CHAPTER_OBJECTIVE_INACTIVE;

    if (bundle == NULL)
        return EXPANSION_CHAPTER_OBJECTIVE_INACTIVE;

    for (index = 0; index < bundle->objectiveCount; index++)
    {
        if (bundle->objectives[index].id == objectiveId)
        {
            struct ObjectiveResult result = EvaluateObjective(NULL, bundle, &bundle->objectives[index], 0);

            if (progressOut != NULL)
                *progressOut = result.progress;
            return result.state;
        }
    }

    return EXPANSION_CHAPTER_OBJECTIVE_INACTIVE;
}
