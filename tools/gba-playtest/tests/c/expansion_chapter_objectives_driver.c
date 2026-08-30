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
    ExpansionChapterObjectives_OnBeginningEventsComplete();
    ExpansionChapterObjectives_OnBeginningEventsComplete();
}

int main(void)
{
    u32 progress;

    ResetFixture();
    ExpansionChapterObjectives_ResetTelemetry();
    sEirika.pCharacterData = NULL;
    CHECK(
        ExpansionChapterObjectives_GetStatus(ObjectiveId(4), &progress)
            == EXPANSION_CHAPTER_OBJECTIVE_INACTIVE
            && !CheckFlag(EVFLAG_DEFEAT_BOSS),
        "setup evaluation must not latch protect failure before beginning events"
    );
    sEirika.pCharacterData = &sEirikaData;
    ExpansionChapterObjectives_OnBeginningEventsComplete();
    CHECK(
        ExpansionChapterObjectives_GetStatus(ObjectiveId(4), &progress)
            == EXPANSION_CHAPTER_OBJECTIVE_PENDING,
        "a beginning-event unit must enter protect pending without a failure latch"
    );

    ResetFixture();
    ExpansionChapterObjectives_ResetTelemetry();
    sEirika.pCharacterData = NULL;
    CHECK(
        ExpansionChapterObjectives_GetStatus(ObjectiveId(4), &progress)
            == EXPANSION_CHAPTER_OBJECTIVE_INACTIVE
            && !CheckFlag(EVFLAG_DEFEAT_BOSS),
        "setup evaluation must not latch protect failure before beginning events"
    );
    sEirika.pCharacterData = &sEirikaData;
    ExpansionChapterObjectives_OnBeginningEventsComplete();
    CHECK(
        ExpansionChapterObjectives_GetStatus(ObjectiveId(4), &progress)
            == EXPANSION_CHAPTER_OBJECTIVE_PENDING,
        "a unit loaded by beginning events must start protect pending without a failure latch"
    );

    ResetFixture();
    CHECK(
        ExpansionChapterObjectives_GetStatus(ObjectiveId(4), &progress)
            == EXPANSION_CHAPTER_OBJECTIVE_PENDING,
        "initial beginning-event completion must make protect evaluation ready"
    );
    ExpansionChapterObjectives_OnMapChangeStarted();
    sEirika.pCharacterData = NULL;
    ExpansionChapterObjectives_RefreshTelemetry();
    CHECK(
        gExpansionChapterObjectiveTelemetry.state == EXPANSION_CHAPTER_OBJECTIVE_INACTIVE
            && !CheckFlag(EVFLAG_DEFEAT_BOSS),
        "map-change setup must remain inactive and cannot latch absent protected units"
    );
    CHECK(
        ExpansionChapterObjectives_GetStatus(ObjectiveId(4), &progress)
            == EXPANSION_CHAPTER_OBJECTIVE_INACTIVE
            && !CheckFlag(EVFLAG_DEFEAT_BOSS),
        "map-change setup status must not evaluate or latch protect failure"
    );
    sEirika.pCharacterData = &sEirikaData;
    ExpansionChapterObjectives_OnMapChangeEventsComplete();
    CHECK(
        ExpansionChapterObjectives_GetStatus(ObjectiveId(4), &progress)
            == EXPANSION_CHAPTER_OBJECTIVE_PENDING
            && !CheckFlag(EVFLAG_DEFEAT_BOSS),
        "post-map-change event completion must reactivate loaded protect units"
    );

    ResetFixture();
    ExpansionChapterObjectives_ResetTelemetry();
    sEirika.pCharacterData = NULL;
    ExpansionChapterObjectives_OnMapChangeEventsComplete();
    CHECK(
        ExpansionChapterObjectives_GetStatus(ObjectiveId(4), &progress)
            == EXPANSION_CHAPTER_OBJECTIVE_INACTIVE
            && !CheckFlag(EVFLAG_DEFEAT_BOSS),
        "an unmatched map-change completion must remain inactive without an early latch"
    );

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
            && progress == 1,
        "protect objective must report its live member while completion is pending"
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
    sEirika.xPos = 63;
    sEirika.yPos = 63;
    ExpansionChapterObjectives_ResetTelemetry();
    ExpansionChapterObjectives_OnBeginningEventsComplete();
    CHECK(
        ExpansionChapterObjectives_GetStatus(ObjectiveId(3), &progress)
            == EXPANSION_CHAPTER_OBJECTIVE_SUCCESS,
        "deadline hold success must survive departure and reconstruction"
    );
    sEirika.xPos = 63;
    sEirika.yPos = 63;
    ExpansionChapterObjectives_ResetTelemetry();
    ExpansionChapterObjectives_OnBeginningEventsComplete();
    CHECK(
        ExpansionChapterObjectives_GetStatus(ObjectiveId(3), &progress)
            == EXPANSION_CHAPTER_OBJECTIVE_SUCCESS,
        "deadline hold success must remain terminal after departure and reconstruction"
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
    sEirika.state = US_DEAD;
    CHECK(
        ExpansionChapterObjectives_GetSnapshot(ObjectiveId(4), &progress)
            == EXPANSION_CHAPTER_OBJECTIVE_FAILURE
            && !CheckFlag(EVFLAG_DEFEAT_BOSS),
        "planner snapshot must report failure without latching event state"
    );
    CHECK(
        ExpansionChapterObjectives_GetStatus(ObjectiveId(4), &progress)
            == EXPANSION_CHAPTER_OBJECTIVE_FAILURE,
        "protect objective must latch a death before its completion"
    );
    CHECK(
        ExpansionChapterObjectives_GetStatus(ObjectiveId(4), &progress)
            == EXPANSION_CHAPTER_OBJECTIVE_FAILURE,
        "protect failure must remain stable after its first evaluation"
    );
    sEirika.state = US_NONE;
    ExpansionChapterObjectives_ResetTelemetry();
    ExpansionChapterObjectives_OnBeginningEventsComplete();
    CHECK(
        ExpansionChapterObjectives_GetStatus(ObjectiveId(4), &progress)
            == EXPANSION_CHAPTER_OBJECTIVE_FAILURE,
        "protect failure latch must reconstruct after telemetry reset"
    );

    ResetFixture();
    sEirika.state = US_RESCUED;
    CHECK(
        ExpansionChapterObjectives_GetStatus(ObjectiveId(4), &progress)
            == EXPANSION_CHAPTER_OBJECTIVE_FAILURE,
        "a rescued protected unit must latch failure before completion"
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
            == EXPANSION_CHAPTER_OBJECTIVE_SUCCESS,
        "completion success must remain terminal when the protected unit dies later"
    );
    sFlags[EVFLAG_BATTLE_QUOTES] = false;
    CHECK(
        ExpansionChapterObjectives_GetStatus(ObjectiveId(4), &progress)
            == EXPANSION_CHAPTER_OBJECTIVE_SUCCESS,
        "protect completion flag must survive later completion regression"
    );
    sFlags[EVFLAG_BATTLE_QUOTES] = false;
    CHECK(
        ExpansionChapterObjectives_GetStatus(ObjectiveId(4), &progress)
            == EXPANSION_CHAPTER_OBJECTIVE_SUCCESS,
        "protect completion flag must survive a later reach or event regression"
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
    ExpansionChapterObjectives_OnBeginningEventsComplete();
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
