#include "global.h"

#include <stdio.h>

#include "bmmind.h"
#include "bmunit.h"
#include "cp_common.h"
#include "cp_perform.h"
#include "expansion_autoplay.h"

#define CHECK(condition, message) \
    do \
    { \
        if (!(condition)) \
        { \
            fprintf(stderr, "PLANNER_STATIONARY_WAIT_TEST: FAIL: %s\n", message); \
            return 1; \
        } \
    } while (0)

struct PlaySt gPlaySt;
struct ActionData gActionData;
struct AiState gAiState;
struct ExpansionAutoplayTelemetry gExpansionAutoplayTelemetry;
struct Unit* gActiveUnit;
u8 gActiveUnitId;

static struct CharacterData sCharacter;
static struct ClassData sClass;
static struct Unit sUnit;
static const struct ProcCmd* sStartedScript;
static int sGotoCount;
static int sTrapCleanupCount;
static int sWaitEventCount;
static int sStatusCleanupCount;
static int sMapCleanupCount;
static int sTelemetryCount;
static int sRngCount;
static u8 sUnitIds[2] = { 1, 0 };

struct ProcCmd gProcScr_CpPerform[] = {
    PROC_END,
};

struct CpPerformProc;

void CpDecide_CompleteDecisionForTest(ProcPtr proc);
void CpPerform_Cleanup(struct CpPerformProc* proc);
s8 AiDummyAction(struct CpPerformProc* proc);

bool ExpansionAutoplayPlanner_IsActive(void)
{
    return true;
}

bool ExpansionAutoplay_IsBlueComputerPhase(void)
{
    return true;
}

bool ExpansionAutoplay_IsActionSupported(u8 actionId)
{
    return actionId == AI_ACTION_NONE;
}

void ExpansionAutoplay_RecordCommittedAction(
    int faction,
    int actorSlot,
    int actionId,
    int targetSlot,
    enum ExpansionAutoplayTargetRelation relation)
{
    if (faction == FACTION_BLUE
        && actorSlot == 1
        && actionId == AI_ACTION_NONE
        && targetSlot == 0
        && relation == EXPANSION_AUTOPLAY_TARGET_NONE)
        sTelemetryCount++;
}

ProcPtr Proc_StartBlocking(const struct ProcCmd* script, ProcPtr parent)
{
    (void)parent;
    sStartedScript = script;
    return (ProcPtr)1;
}

void Proc_Goto(ProcPtr proc, int label)
{
    (void)proc;
    (void)label;
    sGotoCount++;
}

bool HandlePostActionTraps(ProcPtr proc)
{
    (void)proc;
    sTrapCleanupCount++;
    return false;
}

bool RunPotentialWaitEvents(void)
{
    sWaitEventCount++;
    return false;
}

void UpdateAllPhaseHealingAIStatus(void)
{
    sStatusCleanupCount++;
}

struct Unit* GetUnit(int id)
{
    return id == 1 ? &sUnit : NULL;
}

void SetCursorMapPosition(int x, int y)
{
    (void)x;
    (void)y;
}

void RenderBmMapOnBg2(void)
{
}

void MoveActiveUnit(int x, int y)
{
    sUnit.xPos = x;
    sUnit.yPos = y;
}

void RefreshEntityBmMaps(void)
{
}

void RenderBmMap(void)
{
    sMapCleanupCount++;
}

void NewBMXFADE(s8 lock)
{
    (void)lock;
}

void EndAllMus(void)
{
}

void ShowUnitSprite(struct Unit* unit)
{
    (void)unit;
}

void RefreshUnitSprites(void)
{
}

s8 IsAllegianceAllied(int left, int right)
{
    return (left & 0x80) == (right & 0x80);
}

int NextRN_N(int max)
{
    (void)max;
    sRngCount++;
    return 0;
}

int main(void)
{
    sCharacter.number = 1;
    sClass.number = 1;
    sUnit.pCharacterData = &sCharacter;
    sUnit.pClassData = &sClass;
    sUnit.index = 1;
    sUnit.xPos = 4;
    sUnit.yPos = 3;
    gActiveUnit = &sUnit;
    gActiveUnitId = 1;
    gPlaySt.faction = FACTION_BLUE;
    gExpansionAutoplayTelemetry.state =
        EXPANSION_AUTOPLAY_STATE_COMPUTER_PHASE;
    gAiDecision.actionPerformed = true;
    gAiDecision.actionId = AI_ACTION_NONE;
    gAiDecision.unitId = 1;
    gAiDecision.xMove = 4;
    gAiDecision.yMove = 3;
    gAiDecision.itemSlot = 0xFF;
    gActionData.subjectIndex = 1;
    gAiState.unitIt = sUnitIds;

    CpDecide_CompleteDecisionForTest(NULL);
    CHECK(sStartedScript == gProcScr_CpPerform
              && sGotoCount == 0
              && gAiState.unitIt == &sUnitIds[1],
          "stationary planner Wait must enter normal CpPerform");
    CHECK(AiDummyAction(NULL) == 1,
          "stationary Wait must use the normal dummy action");
    HandlePostActionTraps(NULL);
    RunPotentialWaitEvents();
    CpPerform_Cleanup(NULL);
    CHECK(sTrapCleanupCount == 1
              && sWaitEventCount == 1
              && sStatusCleanupCount == 1
              && sMapCleanupCount == 1
              && sTelemetryCount == 1,
          "stationary Wait must run post-action and telemetry cleanup");
    CHECK(sUnit.xPos == 4 && sUnit.yPos == 3 && sRngCount == 0,
          "stationary Wait must preserve position and consume no RNG");

    puts("PLANNER_STATIONARY_WAIT_TEST: PASS");
    return 0;
}
