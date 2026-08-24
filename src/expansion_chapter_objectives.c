#include "global.h"

#include "bm.h"
#include "bmunit.h"
#include "eventinfo.h"

#include "expansion_chapter_objectives.h"

struct ExpansionChapterObjectiveTelemetry EWRAM_DATA
    gExpansionChapterObjectiveTelemetry = { 0 };

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

static enum UnitObjectiveState GetUnitObjectiveState(u8 character)
{
    return GetUnitObjectiveStateFromUnit(GetUnitFromCharId(character));
}

static struct ObjectiveResult GetGroupAreaResult(
    const struct ExpansionChapterAiGroup* group,
    const struct ExpansionChapterObjective* objective)
{
    struct ObjectiveResult result = { EXPANSION_CHAPTER_OBJECTIVE_PENDING, 0 };
    int index;

    if (group == NULL)
        return result;

    for (index = 0; index < group->memberCount; index++)
    {
        struct Unit* unit = GetUnitFromCharId(group->members[index]);
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
        enum UnitObjectiveState protectedState =
            GetUnitObjectiveState(objective->protectedCharacter);
        int index;

        if (protectedState == UNIT_OBJECTIVE_DEAD)
        {
            result.state = EXPANSION_CHAPTER_OBJECTIVE_FAILURE;
            return result;
        }

        if (protectedState == UNIT_OBJECTIVE_MISSING)
            return result;

        result.progress = 1;
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

        result = EvaluateObjective(bundle, completion, depth + 1);
        if (result.state == EXPANSION_CHAPTER_OBJECTIVE_INACTIVE)
            result.state = EXPANSION_CHAPTER_OBJECTIVE_PENDING;
        return result;
    }

    case EXPANSION_CHAPTER_OBJECTIVE_REACH_AREA:
        return GetGroupAreaResult(objective->group, objective);

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
            unitState = GetUnitObjectiveState(objective->group->members[index]);
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
        result = GetGroupAreaResult(objective->group, objective);
        if (result.state == EXPANSION_CHAPTER_OBJECTIVE_FAILURE)
            return result;

        if (gPlaySt.chapterTurnNumber >= objective->untilTurn)
        {
            if (result.state == EXPANSION_CHAPTER_OBJECTIVE_SUCCESS)
                return result;

            result.state = EXPANSION_CHAPTER_OBJECTIVE_FAILURE;
            return result;
        }

        if (result.state == EXPANSION_CHAPTER_OBJECTIVE_SUCCESS)
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

static const struct ExpansionChapterObjective* GetSelectedObjective(
    const struct ExpansionChapterObjectiveBundle* bundle,
    struct ObjectiveResult* resultOut)
{
    const struct ExpansionChapterObjective* selected = NULL;
    struct ObjectiveResult selectedResult = { EXPANSION_CHAPTER_OBJECTIVE_INACTIVE, 0 };
    int selectedPriority = 0;
    int index;

    if (bundle == NULL)
    {
        if (resultOut != NULL)
            *resultOut = selectedResult;
        return NULL;
    }

    for (index = 0; index < bundle->objectiveCount; index++)
    {
        const struct ExpansionChapterObjective* objective = &bundle->objectives[index];
        struct ObjectiveResult result = EvaluateObjective(bundle, objective, 0);
        int priority = ObjectivePriority(result.state);

        if (priority > selectedPriority)
        {
            selected = objective;
            selectedResult = result;
            selectedPriority = priority;
        }
    }

    if (resultOut != NULL)
        *resultOut = selectedResult;
    return selected;
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
    const struct ExpansionChapterObjective* protectObjective;
    struct Unit* protectedUnit;
    enum ExpansionChapterObjectiveState state;
    u32 progress;
    u32 originalUnitState;
    int originalX;
    int originalY;
    bool8 eventFlagWasSet;

    if (sExpansionChapterObjectiveRuntimeProbeComplete)
        return;

    eventObjective = FindObjectiveByKind(bundle, EXPANSION_CHAPTER_OBJECTIVE_EVENT_FLAG);
    reachObjective = FindObjectiveByKind(bundle, EXPANSION_CHAPTER_OBJECTIVE_REACH_AREA);
    defeatObjective = FindObjectiveByKind(bundle, EXPANSION_CHAPTER_OBJECTIVE_DEFEAT_GROUP);
    protectObjective = FindObjectiveByKind(bundle, EXPANSION_CHAPTER_OBJECTIVE_PROTECT);
    if (eventObjective == NULL || reachObjective == NULL || defeatObjective == NULL
        || protectObjective == NULL)
    {
        return;
    }

    protectedUnit = GetUnitFromCharId(protectObjective->protectedCharacter);
    if (protectedUnit == NULL || protectedUnit->pCharacterData == NULL)
        return;

    sExpansionChapterObjectiveRuntimeProbeComplete = TRUE;
    eventFlagWasSet = CheckFlag(eventObjective->eventFlag);
    originalUnitState = protectedUnit->state;
    originalX = protectedUnit->xPos;
    originalY = protectedUnit->yPos;

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

    protectedUnit->state |= US_DEAD;
    state = ExpansionChapterObjectives_GetStatus(protectObjective->id, &progress);
    gExpansionChapterObjectiveRuntimeProbe.protectFailureState = state;
    state = ExpansionChapterObjectives_GetStatus(defeatObjective->id, &progress);
    gExpansionChapterObjectiveRuntimeProbe.defeatSuccessState = state;

    protectedUnit->state = originalUnitState;
    protectedUnit->xPos = originalX;
    protectedUnit->yPos = originalY;
    if (eventFlagWasSet)
        SetFlag(eventObjective->eventFlag);
    else
        ClearFlag(eventObjective->eventFlag);

    gExpansionChapterObjectiveRuntimeProbe.magic = EXPANSION_CHAPTER_OBJECTIVE_RUNTIME_PROBE_MAGIC;
}

#endif

void ExpansionChapterObjectives_ResetTelemetry(void)
{
    gExpansionChapterObjectiveTelemetry.objectiveId = 0;
    gExpansionChapterObjectiveTelemetry.state = EXPANSION_CHAPTER_OBJECTIVE_INACTIVE;
    gExpansionChapterObjectiveTelemetry.progress = 0;
    gExpansionChapterObjectiveTelemetry.activeCount = 0;
}

void ExpansionChapterObjectives_RefreshTelemetry(void)
{
    const struct ExpansionChapterObjectiveBundle* bundle;
    const struct ExpansionChapterObjective* selected;
    struct ObjectiveResult result;
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

    bundle = GetCurrentBundle();
    ExpansionChapterObjectives_ResetTelemetry();
    if (bundle == NULL)
        return;

#if FE8_CHAPTER_OBJECTIVES_RUNTIME_TEST
    RunChapterObjectiveRuntimeProbe(bundle);
#endif

    for (index = 0; index < bundle->objectiveCount; index++)
    {
        if (EvaluateObjective(bundle, &bundle->objectives[index], 0).state
            != EXPANSION_CHAPTER_OBJECTIVE_INACTIVE)
            gExpansionChapterObjectiveTelemetry.activeCount++;
    }

    selected = GetSelectedObjective(bundle, &result);
    if (selected == NULL)
        return;

    gExpansionChapterObjectiveTelemetry.objectiveId = selected->id;
    gExpansionChapterObjectiveTelemetry.state = result.state;
    gExpansionChapterObjectiveTelemetry.progress = result.progress;
}

enum ExpansionChapterObjectiveState ExpansionChapterObjectives_GetStatus(u32 objectiveId, u32* progressOut)
{
    const struct ExpansionChapterObjectiveBundle* bundle = GetCurrentBundle();
    int index;

    if (progressOut != NULL)
        *progressOut = 0;

    if (bundle == NULL)
        return EXPANSION_CHAPTER_OBJECTIVE_INACTIVE;

    for (index = 0; index < bundle->objectiveCount; index++)
    {
        if (bundle->objectives[index].id == objectiveId)
        {
            struct ObjectiveResult result = EvaluateObjective(bundle, &bundle->objectives[index], 0);

            if (progressOut != NULL)
                *progressOut = result.progress;
            return result.state;
        }
    }

    return EXPANSION_CHAPTER_OBJECTIVE_INACTIVE;
}

const struct ExpansionChapterObjective* ExpansionChapterObjectives_GetActiveObjective(void)
{
    return GetSelectedObjective(GetCurrentBundle(), NULL);
}

const struct ExpansionChapterAiGroup* ExpansionChapterObjectives_FindGroup(u32 groupId)
{
    const struct ExpansionChapterObjectiveBundle* bundle = GetCurrentBundle();
    int index;

    if (bundle == NULL)
        return NULL;

    for (index = 0; index < bundle->groupCount; index++)
        if (bundle->groups[index].id == groupId)
            return &bundle->groups[index];

    return NULL;
}

bool ExpansionChapterObjectives_GroupContains(u32 groupId, u8 character)
{
    const struct ExpansionChapterAiGroup* group = ExpansionChapterObjectives_FindGroup(groupId);
    int index;

    if (group == NULL)
        return false;

    for (index = 0; index < group->memberCount; index++)
        if (group->members[index] == character)
            return true;

    return false;
}
