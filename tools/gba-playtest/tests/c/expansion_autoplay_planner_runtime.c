#include "global.h"

#include "bm.h"
#include "bmcontainer.h"
#include "bmitem.h"
#include "bmio.h"
#include "bmmap.h"
#include "bmmind.h"
#include "bmphase.h"
#include "bmunit.h"
#include "cp_common.h"
#include "event.h"
#include "eventscript.h"
#include "expansion_autoplay.h"
#include "expansion_autoplay_internal.h"
#include "expansion_autoplay_planner.h"
#include "gamecontrol.h"
#include "proc.h"
#include "rng.h"

#ifndef FE8_AUTOPLAY_PLANNER_RUNTIME_COMMIT_DELAY_FRAMES
#define FE8_AUTOPLAY_PLANNER_RUNTIME_COMMIT_DELAY_FRAMES 0
#endif

#ifndef FE8_AUTOPLAY_PLANNER_RUNTIME_STALL_AFTER_COMMIT
#define FE8_AUTOPLAY_PLANNER_RUNTIME_STALL_AFTER_COMMIT 0
#endif

#ifndef FE8_AUTOPLAY_PLANNER_RUNTIME_IGNORE_COMMANDS
#define FE8_AUTOPLAY_PLANNER_RUNTIME_IGNORE_COMMANDS 0
#endif

#ifndef FE8_AUTOPLAY_PLANNER_RUNTIME_TRANSITION_SUBCODE
#define FE8_AUTOPLAY_PLANNER_RUNTIME_TRANSITION_SUBCODE EVSUBCMD_MNC2
#endif

struct PlaySt gPlaySt;
struct ActionData gActionData;
struct ExpansionAutoplayTelemetry gExpansionAutoplayTelemetry;
struct Unit* gActiveUnit;
u8 gActiveUnitId;
struct Vec2 gBmMapSize;
u8** gBmMapMovement;
u8** gBmMapUnit;
u8** gBmMapTerrain;
u8** gBmMapFog;
u8 gSummonConfig[4][2];
u32 gEventSlots[EVENT_SLOT_COUNT];
struct EnqueuedEventCall gEventCallQueue[16];

static u16 sSeeds[3];
static u32 sConsumption;
static u32 sRestoreRequests;
static struct CharacterData sCharacter;
static struct ClassData sClass;
static struct Unit sUnit;
static u8 sPermanentFlags[8];
static u8 sChapterFlags[8];
static u16 sConvoy[CONVOY_ITEM_COUNT];
static u8 sMovementData[17][32];
static u8* sMovementRows[17];
static u8 sUnitData[17][32];
static u8* sUnitRows[17];
static u8 sTerrainData[17][32];
static u8* sTerrainRows[17];
static u8 sFogData[17][32];
static u8* sFogRows[17];

enum PlannerRuntimeStage
{
    PLANNER_RUNTIME_WAIT_START,
    PLANNER_RUNTIME_WAIT_CHAPTER_ONE,
    PLANNER_RUNTIME_DELAY_CHAPTER_ONE,
    PLANNER_RUNTIME_WAIT_CHAPTER_TWO,
    PLANNER_RUNTIME_DELAY_CHAPTER_TWO,
    PLANNER_RUNTIME_WAIT_FINAL,
    PLANNER_RUNTIME_DONE,
};

static enum PlannerRuntimeStage sStage;
static u32 sCommitDelayFrames;
static struct GameCtrlProc sGameControl;
static struct BMapMainProc sMapMain;
struct ProcCmd gProc_BMapMain[] = {
    PROC_END,
};

void* memcpy(void* destination, const void* source, size_t size)
{
    u8* output = destination;
    const u8* input = source;

    while (size-- != 0)
        *output++ = *input++;
    return destination;
}

void* memset(void* destination, int value, size_t size)
{
    u8* output = destination;

    while (size-- != 0)
        *output++ = value;
    return destination;
}

void SetTextFont(struct Font* font)
{
    (void)font;
}

void InitSystemTextFont(void)
{
}

void LoadUiFrameGraphics(void)
{
}

void ReadGameSaveCoreGfx(void)
{
}

void UnpackChapterMapPalette(void)
{
}

void ChangeUnitSpritePalette(u16 palette)
{
    (void)palette;
}

void EndAllMus(void)
{
}

void UnlockGame(void)
{
}

void ResumeMenu(void)
{
}

void ResetBkselPalette(void)
{
}

void ClearCutsceneUnits(void)
{
}

void EndTalk(void)
{
}

void EndCgText(void)
{
}

void EndAllBoxDialogue(void)
{
}

void __wrap_EndEventFaces(struct EventEngineProc* proc)
{
    (void)proc;
}

void SetNextGameActionId(int id)
{
    (void)id;
}

void SetNextChapterId(int id)
{
    (void)id;
}

void GotoChapterWithoutSave(u16 chapter)
{
    (void)chapter;
}

void DeleteAll6CWaitMusicRelated(void)
{
}

void Sound_FadeOutBGM(int speed)
{
    (void)speed;
}

void Proc_EndEachMarked(int mark)
{
    (void)mark;
}

ProcPtr Proc_Find(const struct ProcCmd* script)
{
    (void)script;
    return (ProcPtr)&sMapMain;
}

void Proc_End(ProcPtr proc)
{
    (void)proc;
}

void ExpansionAutoplay_ResetForChapterTransition(void)
{
    ExpansionAutoplayPlanner_OnMapReset();
}

void ExpansionAutoplay_Reset(void)
{
    ExpansionAutoplayPlanner_Reset();
}

void DebugToolsPhaseControl_Reset(void)
{
}

void DebugToolsPhaseControl_RestorePersistentTurnForChapterTransition(void)
{
}

void StoreRNState(u16* seeds)
{
    seeds[0] = sSeeds[0];
    seeds[1] = sSeeds[1];
    seeds[2] = sSeeds[2];
}

unsigned GetLCGRNValue(void)
{
    return 0x12345678;
}

u32 GetRNConsumptionCount(void)
{
    return sConsumption;
}

struct Unit* GetUnit(int id)
{
    return id == 1 ? &sUnit : NULL;
}

u8* GetPermanentFlagBits(void)
{
    return sPermanentFlags;
}

int GetPermanentFlagBitsSize(void)
{
    return sizeof(sPermanentFlags);
}

u8* GetChapterFlagBits(void)
{
    return sChapterFlags;
}

int GetChapterFlagBitsSize(void)
{
    return sizeof(sChapterFlags);
}

u16* GetConvoyItemArray(void)
{
    return sConvoy;
}

s8 AreUnitsAllied(int left, int right)
{
    return (left & 0xC0) == (right & 0xC0);
}

s8 CanUnitUseWeapon(struct Unit* unit, int item)
{
    (void)unit;
    (void)item;
    return false;
}

s8 CanUnitUseStaff(struct Unit* unit, int item)
{
    (void)unit;
    (void)item;
    return false;
}

int GetUnitItemUseReachBits(struct Unit* unit, int itemSlot)
{
    (void)unit;
    (void)itemSlot;
    return REACH_RANGE1;
}

int GetUnitKeyItemSlotForTerrain(struct Unit* unit, int terrain)
{
    (void)unit;
    (void)terrain;
    return -1;
}

int GetItemAttributes(int item)
{
    (void)item;
    return 0;
}

int GetItemIndex(int item)
{
    return item & 0xFF;
}

int GetItemMinRange(int item)
{
    (void)item;
    return 1;
}

int GetItemMaxRange(int item)
{
    (void)item;
    return 1;
}

int GetUnitMagBy2Range(struct Unit* unit)
{
    (void)unit;
    return 1;
}

bool IsPositionMagicSealed(int x, int y)
{
    (void)x;
    (void)y;
    return false;
}

s8 CanUnitCrossTerrain(struct Unit* unit, int terrain)
{
    (void)unit;
    (void)terrain;
    return true;
}

bool IsThereClosedChestAt(s8 x, s8 y)
{
    (void)x;
    (void)y;
    return false;
}

bool IsThereClosedDoorAt(s8 x, s8 y)
{
    (void)x;
    (void)y;
    return false;
}

s8 IsItemHammernable(int item)
{
    (void)item;
    return false;
}

s8 CanUnitUseHealItem(struct Unit* unit)
{
    (void)unit;
    return false;
}

s8 CanUnitUsePureWaterItem(struct Unit* unit)
{
    (void)unit;
    return false;
}

s8 CanUnitUseTorchItem(struct Unit* unit)
{
    (void)unit;
    return false;
}

s8 CanUnitUseAntitoxinItem(struct Unit* unit)
{
    (void)unit;
    return false;
}

s8 CanUnitUsePromotionItem(struct Unit* unit, int item)
{
    (void)unit;
    (void)item;
    return false;
}

s8 CanUnitUseStatGainItem(struct Unit* unit, int item)
{
    (void)unit;
    (void)item;
    return false;
}

s8 CanUnitUseFruitItem(struct Unit* unit)
{
    (void)unit;
    return false;
}

void ExpansionAutoplay_RequestPlayerControlRestore(void)
{
    sRestoreRequests++;
}

enum ExpansionAutoplayResult ExpansionAutoplay_SetBlueControl(
    enum ExpansionBlueControl control)
{
    return control == EXPANSION_BLUE_CONTROL_COMPUTER
        ? EXPANSION_AUTOPLAY_OK
        : EXPANSION_AUTOPLAY_ERR_INVALID_CONTROL;
}

static void PrepareDecision(struct AiDecision* decision)
{
    decision->actionId = AI_ACTION_NONE;
    decision->unitId = 1;
    decision->xMove = 1;
    decision->yMove = 0;
    decision->unk04 = 0;
    decision->unk05 = 0;
    decision->targetId = 0;
    decision->itemSlot = 0;
    decision->xTarget = 0;
    decision->yTarget = 0;
    decision->actionPerformed = true;
}

static void InitializeRuntime(void)
{
    int y;
    int x;

    sSeeds[0] = 1;
    sSeeds[1] = 2;
    sSeeds[2] = 3;
    sCharacter.number = 1;
    sClass.number = 1;
    sUnit.pCharacterData = &sCharacter;
    sUnit.pClassData = &sClass;
    sUnit.index = 1;
    sUnit.level = 1;
    sUnit.maxHP = 20;
    sUnit.curHP = 20;
    sUnit.xPos = 0;
    sUnit.yPos = 0;
    gActiveUnit = &sUnit;
    gActiveUnitId = 1;
    gBmMapSize.x = 8;
    gBmMapSize.y = 8;
    for (y = 0; y < 8; y++)
    {
        sMovementRows[y] = sMovementData[y];
        sUnitRows[y] = sUnitData[y];
        sTerrainRows[y] = sTerrainData[y];
        sFogRows[y] = sFogData[y];
        for (x = 0; x < 8; x++)
        {
            sMovementData[y][x] = 1;
            sUnitData[y][x] = 0;
            sTerrainData[y][x] = 1;
            sFogData[y][x] = 1;
        }
    }
    sFogData[0][1] = 0;
    sUnitData[0][0] = 1;
    gBmMapMovement = sMovementRows;
    gBmMapUnit = sUnitRows;
    gBmMapTerrain = sTerrainRows;
    gBmMapFog = sFogRows;
    gPlaySt.chapterIndex = 1;
    gPlaySt.chapterTurnNumber = 1;
    gPlaySt.chapterVisionRange = 3;
    gPlaySt.partyGoldAmount = 1000;
    sConvoy[0] = 1;
    sMapMain.gameCtrl = &sGameControl;
    ExpansionAutoplayPlanner_Reset();
    ExpansionAutoplayPlanner_OnMapReady();
    sStage = PLANNER_RUNTIME_WAIT_START;
}

static void PublishChapterTwo(struct AiDecision* decision)
{
    u32 command = _EvtArg0(
        EV_CMD_CHANGECHAPTER,
        2,
        FE8_AUTOPLAY_PLANNER_RUNTIME_TRANSITION_SUBCODE,
        2);
    struct EventEngineProc event;

    memset(&event, 0, sizeof(event));
    event.pEventCurrent = (const u16*)&command;
    event.execType = EV_EXEC_UNK5;
    event.evStateBits = EV_STATE_ABORT;
    Event2A_MoveToChapter(&event);
    EventEngine_OnEnd(&event);
    gPlaySt.chapterIndex = 2;
    gPlaySt.chapterTurnNumber = 1;
    sConvoy[1] = 2;
    ExpansionAutoplayPlanner_OnMapReady();
    PrepareDecision(decision);
    ExpansionAutoplayPlanner_OfferDecision(decision);
    sStage = PLANNER_RUNTIME_WAIT_CHAPTER_TWO;
}

static void PublishFinalObservation(struct AiDecision* decision)
{
    PrepareDecision(decision);
    ExpansionAutoplayPlanner_OfferDecision(decision);
    sStage = PLANNER_RUNTIME_WAIT_FINAL;
}

static void TickRuntime(void)
{
    static struct AiDecision decision;
    enum ExpansionAutoplayPlannerDecisionResult result;

#if FE8_AUTOPLAY_PLANNER_RUNTIME_IGNORE_COMMANDS
    return;
#endif
    switch (sStage)
    {
    case PLANNER_RUNTIME_WAIT_START:
        if (!ExpansionAutoplayPlanner_PollStart())
            return;
        PrepareDecision(&decision);
        if (ExpansionAutoplayPlanner_OfferDecision(&decision)
            == EXPANSION_AUTOPLAY_PLANNER_DECISION_WAIT)
            sStage = PLANNER_RUNTIME_WAIT_CHAPTER_ONE;
        return;

    case PLANNER_RUNTIME_WAIT_CHAPTER_ONE:
        result = ExpansionAutoplayPlanner_PollDecision(&decision);
        if (result == EXPANSION_AUTOPLAY_PLANNER_DECISION_ACCEPTED)
        {
            sUnit.xPos = decision.xMove;
            sUnit.yPos = decision.yMove;
#if FE8_AUTOPLAY_PLANNER_RUNTIME_STALL_AFTER_COMMIT
            sStage = PLANNER_RUNTIME_DONE;
#else
            sCommitDelayFrames =
                FE8_AUTOPLAY_PLANNER_RUNTIME_COMMIT_DELAY_FRAMES;
            if (sCommitDelayFrames == 0)
                sCommitDelayFrames = 1;
            sStage = PLANNER_RUNTIME_DELAY_CHAPTER_ONE;
#endif
        }
        else if (result == EXPANSION_AUTOPLAY_PLANNER_DECISION_CANCELLED)
        {
            sStage = PLANNER_RUNTIME_DONE;
        }
        return;

    case PLANNER_RUNTIME_DELAY_CHAPTER_ONE:
        if (--sCommitDelayFrames == 0)
            PublishChapterTwo(&decision);
        return;

    case PLANNER_RUNTIME_WAIT_CHAPTER_TWO:
        result = ExpansionAutoplayPlanner_PollDecision(&decision);
        if (result == EXPANSION_AUTOPLAY_PLANNER_DECISION_ACCEPTED)
        {
            sUnit.xPos = decision.xMove;
            sUnit.yPos = decision.yMove;
#if FE8_AUTOPLAY_PLANNER_RUNTIME_STALL_AFTER_COMMIT
            sStage = PLANNER_RUNTIME_DONE;
#else
            sCommitDelayFrames =
                FE8_AUTOPLAY_PLANNER_RUNTIME_COMMIT_DELAY_FRAMES;
            if (sCommitDelayFrames == 0)
                sCommitDelayFrames = 1;
            sStage = PLANNER_RUNTIME_DELAY_CHAPTER_TWO;
#endif
        }
        else if (result == EXPANSION_AUTOPLAY_PLANNER_DECISION_CANCELLED)
        {
            sStage = PLANNER_RUNTIME_DONE;
        }
        return;

    case PLANNER_RUNTIME_DELAY_CHAPTER_TWO:
        if (--sCommitDelayFrames == 0)
            PublishFinalObservation(&decision);
        return;

    case PLANNER_RUNTIME_WAIT_FINAL:
        result = ExpansionAutoplayPlanner_PollDecision(&decision);
        if (result != EXPANSION_AUTOPLAY_PLANNER_DECISION_WAIT)
            sStage = PLANNER_RUNTIME_DONE;
        return;

    default:
        return;
    }
}

void PlannerRuntime_Main(void)
{
    volatile u16* vcount = (volatile u16*)0x04000006;

    InitializeRuntime();
    for (;;)
    {
        while (*vcount >= 160)
            ;
        while (*vcount < 160)
            ;
        TickRuntime();
    }
}
