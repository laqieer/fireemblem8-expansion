#include "global.h"

#include <stdio.h>
#include <string.h>

#include "bmmind.h"
#include "bmunit.h"
#include "cp_common.h"
#include "expansion_autoplay_planner.h"

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

struct PlaySt gPlaySt;
struct ActionData gActionData;
struct Unit* gActiveUnit;
u8 gActiveUnitId;
struct AiDecision gAiDecision;

static bool sPlannerActive;
static bool sPrepareResult;
static int sApplyCount;
static u8 sAppliedAction;
static s8 sAppliedX;
static s8 sAppliedY;

bool ExpansionAutoplayPlanner_IsActive(void)
{
    return sPlannerActive;
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

    puts("SUMMON_EXECUTOR_HOST_TEST: PASS");
    return 0;
}
