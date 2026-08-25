#include "global.h"

#include <stdio.h>

#include "bm.h"
#include "bmunit.h"
#include "constants/chapters.h"
#include "constants/characters.h"
#include "constants/event-flags.h"
#include "expansion_chapter_objectives.h"

#define CHECK(condition, message) \
    do \
    { \
        if (!(condition)) \
        { \
            fprintf(stderr, "CHAPTER_OBJECTIVES_DEACTIVATION_ALIAS: FAIL: %s\n", message); \
            return 1; \
        } \
    } while (0)

struct PlaySt gPlaySt;

static struct CharacterData sEirikaData;
static struct Unit sEirika;
static bool sFlags[0x100];

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
    return character == CHARACTER_EIRIKA ? &sEirika : NULL;
}

struct Unit* GetUnit(int unitId)
{
    return unitId == 1 ? &sEirika : NULL;
}

int main(void)
{
    u32 progress;

    sEirikaData.number = CHARACTER_EIRIKA;
    sEirika.pCharacterData = &sEirikaData;
    sEirika.state = US_NONE;
    sEirika.xPos = 1;
    sEirika.yPos = 1;
    gPlaySt.chapterIndex = CHAPTER_L_2;
    gPlaySt.chapterTurnNumber = 1;
    ExpansionChapterObjectives_ResetTelemetry();
    ExpansionChapterObjectives_OnBeginningEventsComplete();

    CHECK(
        ExpansionChapterObjectives_GetStatus(gExpansionChapterObjectiveBundles[0].objectives[1].id, &progress)
            == EXPANSION_CHAPTER_OBJECTIVE_PENDING,
        "protect must begin pending before the referenced event"
    );
    SetFlag(EVFLAG_BATTLE_QUOTES);
    CHECK(
        ExpansionChapterObjectives_GetStatus(gExpansionChapterObjectiveBundles[0].objectives[1].id, &progress)
            == EXPANSION_CHAPTER_OBJECTIVE_INACTIVE,
        "event alias must deactivate protect before completion can latch"
    );
    CHECK(!CheckFlag(EVFLAG_WIN), "deactivation must prevent the protect completion latch");

    puts("CHAPTER_OBJECTIVES_DEACTIVATION_ALIAS: PASS");
    return 0;
}
