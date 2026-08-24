#include "global.h"

#include <stdio.h>

#include "bm.h"
#include "bmunit.h"
#include "constants/chapters.h"
#include "constants/characters.h"
#include "constants/event-flags.h"
#include "expansion_chapter_objectives.h"
#include "eventinfo.h"

#define CHECK(condition, message) \
    do \
    { \
        if (!(condition)) \
        { \
            fprintf(stderr, "CHAPTER_OBJECTIVES_HOST_TEST: FAIL: %s\n", message); \
            return 1; \
        } \
    } while (0)

struct PlaySt gPlaySt;

static struct CharacterData sEirikaData;
static struct Unit sEirika;
static bool sFlags[0x100];
static int sGetUnitCallCount;
static int sGetUnitFromCharIdCallCount;

bool CheckFlag(int flag)
{
    return flag >= 0 && flag < (int)ARRAY_COUNT(sFlags) && sFlags[flag];
}

void SetFlag(int flag)
{
    if (flag >= 0 && flag < (int)ARRAY_COUNT(sFlags))
        sFlags[flag] = true;
}

struct Unit* GetUnitFromCharId(int character)
{
    sGetUnitFromCharIdCallCount++;
    if (character == CHARACTER_EIRIKA)
        return &sEirika;
    return NULL;
}

struct Unit* GetUnit(int unitId)
{
    sGetUnitCallCount++;
    return unitId == 1 ? &sEirika : NULL;
}

static u32 ObjectiveId(int index)
{
    return gExpansionChapterObjectiveBundles[0].objectives[index].id;
}

static void ResetFixture(void)
{
    int index;

    for (index = 0; index < (int)ARRAY_COUNT(sFlags); index++)
        sFlags[index] = false;

    sEirikaData.number = CHARACTER_EIRIKA;
    sEirika.pCharacterData = &sEirikaData;
    sEirika.state = US_NONE;
    sEirika.xPos = 1;
    sEirika.yPos = 1;
    gPlaySt.chapterIndex = CHAPTER_L_2;
    gPlaySt.chapterTurnNumber = 1;
    sGetUnitCallCount = 0;
    sGetUnitFromCharIdCallCount = 0;
    ExpansionChapterObjectives_ResetTelemetry();
}

int main(void)
{
    u32 progress;

    ResetFixture();
    CHECK(
        ExpansionChapterObjectives_GetStatus(ObjectiveId(0), &progress)
            == EXPANSION_CHAPTER_OBJECTIVE_PENDING
            && progress == 0,
        "event flag objective must begin pending"
    );
    CHECK(
        ExpansionChapterObjectives_GetStatus(ObjectiveId(1), &progress)
            == EXPANSION_CHAPTER_OBJECTIVE_SUCCESS
            && progress == 1,
        "reach-area objective must report live in-area progress"
    );
    CHECK(
        ExpansionChapterObjectives_GetStatus(ObjectiveId(2), &progress)
            == EXPANSION_CHAPTER_OBJECTIVE_INACTIVE,
        "activation flag must keep defeat objective inactive"
    );
    CHECK(
        ExpansionChapterObjectives_GetStatus(ObjectiveId(3), &progress)
            == EXPANSION_CHAPTER_OBJECTIVE_PENDING
            && progress == 1,
        "hold objective must remain pending before its bounded turn"
    );
    CHECK(
        ExpansionChapterObjectives_GetStatus(ObjectiveId(4), &progress)
            == EXPANSION_CHAPTER_OBJECTIVE_PENDING
            && progress == 0,
        "protect objective must wait for its referenced completion"
    );

    sEirika.state = US_RESCUED;
    CHECK(
        ExpansionChapterObjectives_GetStatus(ObjectiveId(1), &progress)
            == EXPANSION_CHAPTER_OBJECTIVE_PENDING
            && progress == 0,
        "a carried unit must not count as area progress"
    );

    sFlags[EVFLAG_BATTLE_QUOTES] = true;
    CHECK(
        ExpansionChapterObjectives_GetStatus(ObjectiveId(0), &progress)
            == EXPANSION_CHAPTER_OBJECTIVE_SUCCESS
            && progress == 1,
        "event flag objective must observe existing event state"
    );
    CHECK(
        ExpansionChapterObjectives_GetStatus(ObjectiveId(4), &progress)
            == EXPANSION_CHAPTER_OBJECTIVE_SUCCESS,
        "protect objective must complete through its referenced objective"
    );
    CHECK(
        ExpansionChapterObjectives_GetStatus(ObjectiveId(2), &progress)
            == EXPANSION_CHAPTER_OBJECTIVE_PENDING
            && progress == 0,
        "defeat objective must count a rescued member as pending"
    );

    sEirika.state = US_NONE;
    gPlaySt.chapterTurnNumber = 2;
    CHECK(
        ExpansionChapterObjectives_GetStatus(ObjectiveId(3), &progress)
            == EXPANSION_CHAPTER_OBJECTIVE_SUCCESS
            && progress == 1,
        "hold objective must complete at its bounded turn"
    );

    ResetFixture();
    sEirika.xPos = 63;
    sEirika.yPos = 63;
    CHECK(
        ExpansionChapterObjectives_GetStatus(ObjectiveId(3), &progress)
            == EXPANSION_CHAPTER_OBJECTIVE_FAILURE,
        "hold objective must latch the first pre-deadline area violation"
    );
    sEirika.xPos = 1;
    sEirika.yPos = 1;
    gPlaySt.chapterTurnNumber = 2;
    CHECK(
        ExpansionChapterObjectives_GetStatus(ObjectiveId(3), &progress)
            == EXPANSION_CHAPTER_OBJECTIVE_FAILURE,
        "hold objective must reject leave-and-reenter at its bounded turn"
    );

    ResetFixture();
    sFlags[EVFLAG_BATTLE_QUOTES] = true;
    sEirika.state = US_DEAD;
    CHECK(
        ExpansionChapterObjectives_GetStatus(ObjectiveId(2), &progress)
            == EXPANSION_CHAPTER_OBJECTIVE_SUCCESS
            && progress == 1,
        "defeat objective must complete after every live member is defeated"
    );
    CHECK(
        ExpansionChapterObjectives_GetStatus(ObjectiveId(4), &progress)
            == EXPANSION_CHAPTER_OBJECTIVE_FAILURE,
        "protected-unit death must be an explicit failure"
    );

    ResetFixture();
    sFlags[EVFLAG_BATTLE_QUOTES] = true;
    sGetUnitCallCount = 0;
    sGetUnitFromCharIdCallCount = 0;
    ExpansionChapterObjectives_RefreshTelemetry();
    CHECK(
        gExpansionChapterObjectiveTelemetry.objectiveId == ObjectiveId(2)
            && gExpansionChapterObjectiveTelemetry.state == EXPANSION_CHAPTER_OBJECTIVE_PENDING
            && gExpansionChapterObjectiveTelemetry.progress == 0
            && gExpansionChapterObjectiveTelemetry.activeCount == 5,
        "telemetry must deterministically select active pending objective state"
    );
    CHECK(
        sGetUnitCallCount == 255 && sGetUnitFromCharIdCallCount == 0,
        "one telemetry refresh must index 255 slots once without member rescans"
    );

    ExpansionChapterObjectives_ResetTelemetry();
    CHECK(
        gExpansionChapterObjectiveTelemetry.objectiveId == 0
            && gExpansionChapterObjectiveTelemetry.state == EXPANSION_CHAPTER_OBJECTIVE_INACTIVE
            && gExpansionChapterObjectiveTelemetry.progress == 0
            && gExpansionChapterObjectiveTelemetry.activeCount == 0,
        "suspend/reset negative must retain no hidden objective state"
    );
    ExpansionChapterObjectives_RefreshTelemetry();
    CHECK(
        gExpansionChapterObjectiveTelemetry.objectiveId == ObjectiveId(2)
            && gExpansionChapterObjectiveTelemetry.state == EXPANSION_CHAPTER_OBJECTIVE_PENDING,
        "refresh after reset must reconstruct objective state from chapter data"
    );

    puts("CHAPTER_OBJECTIVES_HOST_TEST: PASS");
    return 0;
}
