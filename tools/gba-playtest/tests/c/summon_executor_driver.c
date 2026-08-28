#include "global.h"

#include <stdio.h>
#include <string.h>

#include "bmbattle.h"
#include "bmmind.h"
#include "bmunit.h"
#include "cp_common.h"
#include "expansion_autoplay_planner.h"
#include "constants/characters.h"
#include "constants/classes.h"

#define CHECK(condition, message) \
    do \
    { \
        if (!(condition)) \
        { \
            fprintf(stderr, "SUMMON_EXECUTOR_HOST_TEST: FAIL: %s\n", message); \
            return 1; \
        } \
    } while (0)

struct CpPerformProc;

s8 AiSummonAction(struct CpPerformProc* proc);
void AiDKSummonAction(struct CpPerformProc* proc);
void GenerateSummonUnitDef(void);

struct PlaySt gPlaySt;
struct ActionData gActionData;
struct Unit* gActiveUnit;
u8 gActiveUnitId;
struct AiDecision gAiDecision;
struct BattleUnit gBattleActor;
struct UnitDefinition gUnitDef1;
u8 gSummonConfig[4][2] = {
    { CHARACTER_EWAN, CHARACTER_SUMMON_EWAN },
};

static bool sPlannerActive;
static bool sPrepareResult;
static int sApplyCount;
static u8 sAppliedAction;
static s8 sAppliedX;
static s8 sAppliedY;
static struct CharacterData sSummonCharacter;
static struct ClassData sSummonClass;
static struct Unit sSummon;
static bool sSummonLoaded;

bool ExpansionAutoplayPlanner_IsActive(void) { return sPlannerActive; }
unsigned AdvanceGetLCGRNValue(void) { return 0; }
int DivRem(int value, int divisor) { return value % divisor; }

struct Unit* GetUnit(int id)
{
    return id == 2 && sSummonLoaded ? &sSummon : NULL;
}

struct Unit* GetUnitFromCharId(int character)
{
    return sSummonLoaded && sSummon.pCharacterData->number == character
        ? &sSummon : NULL;
}

void ClearUnit(struct Unit* unit)
{
    memset(unit, 0, sizeof(*unit));
    sSummonLoaded = false;
}

int LoadUnits(const struct UnitDefinition* definition)
{
    memset(&sSummon, 0, sizeof(sSummon));
    sSummonCharacter.number = definition->charIndex;
    sSummonClass.number = definition->classIndex;
    sSummon.pCharacterData = &sSummonCharacter;
    sSummon.pClassData = &sSummonClass;
    sSummon.index = 2;
    sSummon.xPos = definition->xPosition;
    sSummon.yPos = definition->yPosition;
    sSummonLoaded = true;
    return 1;
}

bool ExpansionAutoplayPlanner_PrepareActionData(
    const struct AiDecision* decision)
{
    (void)decision;
    return sPrepareResult;
}

u32 ApplyUnitAction(ProcPtr proc)
{
    (void)proc;
    sApplyCount++;
    sAppliedAction = gActionData.unitActionType;
    sAppliedX = gActionData.xOther;
    sAppliedY = gActionData.yOther;
    return 0;
}

static void PrepareDecision(
    struct Unit* unit,
    int xMove,
    int yMove,
    int xTarget,
    int yTarget)
{
    memset(&gActionData, 0, sizeof(gActionData));
    memset(&gAiDecision, 0, sizeof(gAiDecision));
    gActiveUnit = unit;
    gActiveUnitId = unit->index;
    gAiDecision.unitId = unit->index;
    gAiDecision.xMove = xMove;
    gAiDecision.yMove = yMove;
    gAiDecision.xTarget = xTarget;
    gAiDecision.yTarget = yTarget;
    gAiDecision.actionPerformed = true;
    sApplyCount = 0;
    sPrepareResult = true;
}

int main(void)
{
    struct CharacterData character = { 0 };
    struct ClassData unitClass = { 0 };
    struct Unit unit = { 0 };

    unit.pCharacterData = &character;
    unit.pClassData = &unitClass;
    unit.index = 1;

    PrepareDecision(&unit, 2, 2, 2, 1);
    gAiDecision.actionId = AI_ACTION_SUMMON;
    sPlannerActive = true;
    CHECK(AiSummonAction(NULL) == 1
              && sApplyCount == 1
              && sAppliedAction == UNIT_ACTION_SUMMON
              && sAppliedX == 2
              && sAppliedY == 1
              && unit.xPos == 2
              && unit.yPos == 2,
          "normal Summon must execute the first selected tile");

    PrepareDecision(&unit, 4, 3, 5, 3);
    gAiDecision.actionId = AI_ACTION_SUMMON;
    CHECK(AiSummonAction(NULL) == 1
              && sApplyCount == 1
              && sAppliedAction == UNIT_ACTION_SUMMON
              && sAppliedX == 5
              && sAppliedY == 3
              && unit.xPos == 4
              && unit.yPos == 3,
          "normal Summon must execute another selected tile");

    PrepareDecision(&unit, 1, 1, 1, 2);
    gAiDecision.actionId = AI_ACTION_SUMMON;
    sPrepareResult = false;
    CHECK(AiSummonAction(NULL) == 1
              && sApplyCount == 0
              && !gAiDecision.actionPerformed,
          "failed normal Summon revalidation must not execute");

    PrepareDecision(&unit, 3, 4, 6, 6);
    gAiDecision.actionId = AI_ACTION_DKSUMMON;
    gActionData.xOther = 9;
    gActionData.yOther = 8;
    AiDKSummonAction(NULL);
    CHECK(sApplyCount == 1
              && sAppliedAction == UNIT_ACTION_SUMMON_DK
              && sAppliedX == 9
              && sAppliedY == 8
              && unit.xPos == 3
              && unit.yPos == 4,
          "dark summon must remain distinct from coordinate-targeted Summon");

    character.number = CHARACTER_EWAN;
    unitClass.number = 1;
    unit.level = 10;
    gActiveUnit = &unit;
    gActionData.xOther = 2;
    gActionData.yOther = 1;
    GenerateSummonUnitDef();
    CHECK(sSummonLoaded
              && sSummon.xPos == 2
              && sSummon.yPos == 1
              && gUnitDef1.xPosition == 2
              && gUnitDef1.yPosition == 1,
          "normal Summon effect must create at the first selected tile");
    gActionData.xOther = 5;
    gActionData.yOther = 4;
    GenerateSummonUnitDef();
    CHECK(sSummon.xPos == 5
              && sSummon.yPos == 4
              && gUnitDef1.xPosition == 5
              && gUnitDef1.yPosition == 4,
          "normal Summon effect must replace at the second selected tile");

    puts("SUMMON_EXECUTOR_HOST_TEST: PASS");
    return 0;
}
