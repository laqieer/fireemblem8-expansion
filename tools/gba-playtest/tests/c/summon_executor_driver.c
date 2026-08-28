#include "global.h"

#include <stdio.h>
#include <string.h>

#include "bm.h"
#include "bmbattle.h"
#include "bmmap.h"
#include "bmmind.h"
#include "bmtrick.h"
#include "bmunit.h"
#include "chapterdata.h"
#include "cp_common.h"
#include "expansion_autoplay.h"
#include "expansion_autoplay_planner.h"
#include "constants/characters.h"
#include "constants/classes.h"
#include "constants/items.h"
#include "constants/terrains.h"

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
s8 AiPickAction(struct CpPerformProc* proc);
void AiDKSummonAction(struct CpPerformProc* proc);
void GenerateSummonUnitDef(void);
void AiStartCombatAction(struct CpPerformProc* proc);
void CpDecide_CompleteDecisionForTest(ProcPtr proc);
void CpPerform_Cleanup(struct CpPerformProc* proc);
s8 AiDummyAction(struct CpPerformProc* proc);

struct PlaySt gPlaySt;
struct ActionData gActionData;
struct Unit* gActiveUnit;
u8 gActiveUnitId;
struct UnitDefinition gUnitDef1;
struct AiState gAiState;
struct ExpansionAutoplayTelemetry gExpansionAutoplayTelemetry;
struct Vec2 gBmMapSize;
u8** gBmMapTerrain;
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
static struct CharacterData sCharacter;
static struct CharacterData sSnagCharacter;
static struct ClassData sSummonClass;
static struct ClassData sClass;
static struct ClassData sObstacleClass;
static struct ROMChapterData sChapter;
static struct Unit sUnit;
static struct Unit sSummon;
static struct Trap sTrap;
static u8 sTerrainData[8][8];
static u8* sTerrainRows[8];
static bool sSummonLoaded;
static bool sSnagMode;
static const struct ProcCmd* sStartedScript;
static int sGotoCount;
static int sTrapCleanupCount;
static int sWaitEventCount;
static int sStatusCleanupCount;
static int sMapCleanupCount;
static int sTelemetryCount;
static int sRngCount;
static int sMapChangeCount;
static u8 sUnitIds[2] = { 1, 0 };

struct ProcCmd gProcScr_CpPerform[] = { PROC_END };

bool ExpansionAutoplayPlanner_IsActive(void) { return sPlannerActive; }
unsigned AdvanceGetLCGRNValue(void) { return 0; }
int DivRem(int value, int divisor) { return value % divisor; }

struct Unit* GetUnit(int id)
{
    if (id == 1)
        return &sUnit;
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
    if (sSnagMode)
        return decision->targetId == 0
            && decision->xTarget == sTrap.xPos
            && decision->yTarget == sTrap.yPos
            && sTrap.type == TRAP_OBSTACLE
            && gBmMapTerrain[sTrap.yPos][sTrap.xPos] == TERRAIN_SNAG;
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

bool ExpansionAutoplay_IsBlueComputerPhase(void) { return true; }
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

void UpdateAllPhaseHealingAIStatus(void) { sStatusCleanupCount++; }
void SetCursorMapPosition(int x, int y) { (void)x; (void)y; }
void RenderBmMapOnBg2(void) {}
void MoveActiveUnit(int x, int y) { sUnit.xPos = x; sUnit.yPos = y; }
void RefreshEntityBmMaps(void) {}
void RenderBmMap(void) { sMapCleanupCount++; }
void NewBMXFADE(s8 lock) { (void)lock; }
void EndAllMus(void) {}
void ShowUnitSprite(struct Unit* unit) { (void)unit; }
void RefreshUnitSprites(void) {}
s8 IsAllegianceAllied(int left, int right) { return (left & 0x80) == (right & 0x80); }

int NextRN_N(int max)
{
    (void)max;
    sRngCount++;
    return 0;
}

struct Trap* GetTrapAt(int x, int y)
{
    return sTrap.type != TRAP_NONE
        && sTrap.xPos == x
        && sTrap.yPos == y
        ? &sTrap : NULL;
}

void EquipUnitItemSlot(struct Unit* unit, int slot)
{
    u16 item = unit->items[slot];
    unit->items[slot] = unit->items[0];
    unit->items[0] = item;
}

const struct ClassData* GetClassData(int id)
{
    return id == CLASS_OBSTACLE ? &sObstacleClass : &sClass;
}

const struct CharacterData* GetCharacterData(int id)
{
    return id == CHARACTER_SNAG ? &sSnagCharacter : &sCharacter;
}

const struct ROMChapterData* GetROMChapterStruct(unsigned chapter)
{
    (void)chapter;
    return &sChapter;
}

int GetMapChangeIdAt(int x, int y) { (void)x; (void)y; return 7; }
void PlaySoundEffect(int song) { (void)song; }
void m4aSongNumStart(u16 song) { (void)song; }
void ApplyMapChangesById(int id) { if (id == 7) sMapChangeCount++; }
void EnableMapChange(int id) { (void)id; }
void RefreshTerrainBmMap(void) {}
void UpdateRoofedUnits(void) {}
void UpdateUnitMapAndVision(void) {}

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
    int y;
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
    PrepareDecision(&unit, 3, 2, 4, 2);
    gAiDecision.actionId = AI_ACTION_PICK;
    gAiDecision.itemSlot = 1;
    CHECK(AiPickAction(NULL) == 1
              && sApplyCount == 1
              && sAppliedAction == UNIT_ACTION_PICK
              && sAppliedX == 4
              && sAppliedY == 2
              && gActionData.subjectIndex == 1,
          "Pick executor must lower the selected actor and target");
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
              && gAiState.unitIt == &sUnitIds[1]
              && AiDummyAction(NULL) == 1,
          "stationary planner Wait must enter normal CpPerform");
    HandlePostActionTraps(NULL);
    RunPotentialWaitEvents();
    CpPerform_Cleanup(NULL);
    CHECK(sTrapCleanupCount == 1
              && sWaitEventCount == 1
              && sStatusCleanupCount == 1
              && sMapCleanupCount == 1
              && sTelemetryCount == 1
              && sUnit.xPos == 4
              && sUnit.yPos == 3
              && sRngCount == 0,
          "stationary Wait must run cleanup without movement or RNG");
    gBmMapSize.x = 8;
    gBmMapSize.y = 8;
    for (y = 0; y < 8; y++)
        sTerrainRows[y] = sTerrainData[y];
    gBmMapTerrain = sTerrainRows;
    sSnagCharacter.number = CHARACTER_SNAG;
    sObstacleClass.number = CLASS_OBSTACLE;
    sChapter.mapCrackedWallHeath = 30;
    sUnit.xPos = 2;
    sUnit.yPos = 2;
    sUnit.items[0] = ITEM_SWORD_IRON | (30 << 8);
    gActiveUnitId = 1;
    sTrap.type = TRAP_OBSTACLE;
    sTrap.xPos = 3;
    sTrap.yPos = 2;
    sTrap.extra = 20;
    sTerrainData[2][3] = TERRAIN_SNAG;
    PrepareDecision(&sUnit, 2, 2, 3, 2);
    gAiDecision.actionId = AI_ACTION_COMBAT;
    gAiDecision.targetId = 0;
    gAiDecision.itemSlot = 0;
    sSnagMode = true;
    AiStartCombatAction(NULL);
    CHECK(sApplyCount == 1
              && gActionData.unitActionType == UNIT_ACTION_COMBAT
              && gActionData.targetIndex == 0
              && gActionData.xOther == 3
              && gActionData.yOther == 2
              && gActionData.trapType == 20,
          "combat executor must lower selected snag coordinates");
    InitObstacleBattleUnit();
    CHECK(gBattleTarget.unit.pCharacterData == &sSnagCharacter
              && gBattleTarget.unit.curHP == 20
              && gBattleTarget.unit.xPos == 3
              && gBattleTarget.unit.yPos == 2,
          "battle setup must construct the selected snag target");
    gBattleTarget.unit.curHP = 5;
    UpdateObstacleFromBattle(&gBattleTarget);
    CHECK(sTrap.extra == 5 && sTrap.type == TRAP_OBSTACLE,
          "nonlethal combat damage must update snag HP");
    gBattleTarget.unit.curHP = 0;
    UpdateObstacleFromBattle(&gBattleTarget);
    CHECK(sTrap.extra == 0
              && sTrap.type == TRAP_NONE
              && sMapChangeCount == 1,
          "lethal combat damage must destroy the selected snag");
    gAiDecision.actionPerformed = true;
    AiStartCombatAction(NULL);
    CHECK(sApplyCount == 1 && !gAiDecision.actionPerformed,
          "destroyed snag must not reach the executor");
    puts("PLANNER_EXECUTOR_HOST_TEST: PASS");
    return 0;
}
