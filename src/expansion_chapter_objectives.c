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

static enum UnitObjectiveState GetUnitObjectiveState(u8 character)
{
    struct Unit* unit = GetUnitFromCharId(character);

    if (unit == NULL || unit->pCharacterData == NULL)
        return UNIT_OBJECTIVE_MISSING;

    if (unit->state & US_DEAD)
        return UNIT_OBJECTIVE_DEAD;

    if (unit->state & US_UNAVAILABLE)
        return UNIT_OBJECTIVE_MISSING;

    return UNIT_OBJECTIVE_ALIVE;
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
        enum UnitObjectiveState unitState = GetUnitObjectiveState(group->members[index]);

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

        if (objective->group == NULL)
        {
            result.state = EXPANSION_CHAPTER_OBJECTIVE_FAILURE;
            return result;
        }

        result.progress = objective->group->memberCount;
        for (index = 0; index < objective->group->memberCount; index++)
            if (GetUnitObjectiveState(objective->group->members[index]) == UNIT_OBJECTIVE_ALIVE)
                result.progress--;

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

void ExpansionChapterObjectives_ResetTelemetry(void)
{
    gExpansionChapterObjectiveTelemetry.objectiveId = 0;
    gExpansionChapterObjectiveTelemetry.state = EXPANSION_CHAPTER_OBJECTIVE_INACTIVE;
    gExpansionChapterObjectiveTelemetry.progress = 0;
    gExpansionChapterObjectiveTelemetry.activeCount = 0;
}

void ExpansionChapterObjectives_RefreshTelemetry(void)
{
    const struct ExpansionChapterObjectiveBundle* bundle = GetCurrentBundle();
    struct ObjectiveResult selected = { EXPANSION_CHAPTER_OBJECTIVE_INACTIVE, 0 };
    u32 selectedId = 0;
    int selectedPriority = 0;
    int index;

    ExpansionChapterObjectives_ResetTelemetry();
    if (bundle == NULL)
        return;

    for (index = 0; index < bundle->objectiveCount; index++)
    {
        const struct ExpansionChapterObjective* objective = &bundle->objectives[index];
        struct ObjectiveResult result = EvaluateObjective(bundle, objective, 0);
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
