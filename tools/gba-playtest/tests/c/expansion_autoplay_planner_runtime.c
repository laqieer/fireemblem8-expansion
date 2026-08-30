#include "global.h"

#include "bm.h"
#include "bmcontainer.h"
#include "bmitem.h"
#include "bmio.h"
#include "bmmap.h"
#include "bmmind.h"
#include "bmphase.h"
#include "bmtrick.h"
#include "bmunit.h"
#include "cp_common.h"
#include "event.h"
#include "eventscript.h"
#include "expansion_autoplay.h"
#include "expansion_autoplay_internal.h"
#include "expansion_autoplay_planner.h"
#include "expansion_autoplay_strategies.h"
#include "gamecontrol.h"
#include "proc.h"
#include "rng.h"
#include "constants/items.h"

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

#ifndef FE8_AUTOPLAY_PLANNER_RUNTIME_CANDIDATE_MODE
#define FE8_AUTOPLAY_PLANNER_RUNTIME_CANDIDATE_MODE 0
#endif

#ifndef FE8_AUTOPLAY_PLANNER_RUNTIME_FLAG_DOMAIN_MODE
#define FE8_AUTOPLAY_PLANNER_RUNTIME_FLAG_DOMAIN_MODE 0
#endif

#ifndef FE8_AUTOPLAY_PLANNER_RUNTIME_ACK_OVERRIDE
#define FE8_AUTOPLAY_PLANNER_RUNTIME_ACK_OVERRIDE 0
#endif

#ifndef FE8_AUTOPLAY_PLANNER_RUNTIME_ACK_RESULT
#define FE8_AUTOPLAY_PLANNER_RUNTIME_ACK_RESULT 0
#endif

#ifndef FE8_AUTOPLAY_PLANNER_RUNTIME_ACK_REJECTION
#define FE8_AUTOPLAY_PLANNER_RUNTIME_ACK_REJECTION 0
#endif

#ifndef FE8_AUTOPLAY_PLANNER_RUNTIME_ZERO_DIGEST
#define FE8_AUTOPLAY_PLANNER_RUNTIME_ZERO_DIGEST 0
#endif

#ifndef FE8_AUTOPLAY_PLANNER_RUNTIME_STARTUP_DELAY
#define FE8_AUTOPLAY_PLANNER_RUNTIME_STARTUP_DELAY 0
#endif

#ifndef FE8_AUTOPLAY_PLANNER_RUNTIME_STARTUP_STATE
#define FE8_AUTOPLAY_PLANNER_RUNTIME_STARTUP_STATE 0
#endif

#ifndef FE8_AUTOPLAY_PLANNER_RUNTIME_MUTATE_SELECTED_ITEM
#define FE8_AUTOPLAY_PLANNER_RUNTIME_MUTATE_SELECTED_ITEM 0
#endif

struct ActionData gActionData;
struct ExpansionAutoplayTelemetry gExpansionAutoplayTelemetry;
struct Unit* gActiveUnit;
u8 gActiveUnitId;
struct Vec2 gBmMapSize;
u8** gBmMapMovement;
u8** gBmMapUnit;
u8** gBmMapTerrain;
u8** gBmMapFog;
s8 TerrainTable_MovCost_FlyNormal[0x100];
u8 gSummonConfig[4][2];
u32 gEventSlots[EVENT_SLOT_COUNT];
struct EnqueuedEventCall gEventCallQueue[16];
const struct ExpansionAutoplayStrategy gExpansionAutoplayStrategies[] = { { 0 } };
const struct ExpansionAutoplayStrategyBundle gExpansionAutoplayStrategyBundles[] = {
    { EXPANSION_AUTOPLAY_STRATEGY_CHAPTER_NONE, 0, 0, 0, 0, NULL, NULL },
};

static u16 sSeeds[3];
static u32 sConsumption;
static u32 sRestoreRequests;
static struct CharacterData sCharacter;
static struct CharacterData sTargetCharacter;
static struct ClassData sClass;
static struct ClassData sTargetClass;
static struct Unit sUnit;
static struct Unit sTarget;
static u8 sPermanentFlags[256];
static u8 sChapterFlags[256];
#if FE8_AUTOPLAY_PLANNER_RUNTIME_ZERO_DIGEST
static int sPermanentFlagSize = 4;
static int sChapterFlagSize = 0;
#elif FE8_AUTOPLAY_PLANNER_RUNTIME_FLAG_DOMAIN_MODE == 1
static int sPermanentFlagSize = 0;
static int sChapterFlagSize = 0;
#elif FE8_AUTOPLAY_PLANNER_RUNTIME_FLAG_DOMAIN_MODE == 2
static int sPermanentFlagSize = 1;
static int sChapterFlagSize = 0;
#elif FE8_AUTOPLAY_PLANNER_RUNTIME_FLAG_DOMAIN_MODE == 3
static int sPermanentFlagSize = 256;
static int sChapterFlagSize = 0;
#elif FE8_AUTOPLAY_PLANNER_RUNTIME_FLAG_DOMAIN_MODE == 4
static int sPermanentFlagSize = 257;
static int sChapterFlagSize = 0;
#elif FE8_AUTOPLAY_PLANNER_RUNTIME_FLAG_DOMAIN_MODE == 5
static int sPermanentFlagSize = 0x7FFFFFFF;
static int sChapterFlagSize = 0;
#elif FE8_AUTOPLAY_PLANNER_RUNTIME_FLAG_DOMAIN_MODE == 6
static int sPermanentFlagSize = 256;
static int sChapterFlagSize = 0;
#else
static int sPermanentFlagSize = 8;
static int sChapterFlagSize = 8;
#endif
static u16 sConvoy[CONVOY_ITEM_COUNT];
static struct Trap sTraps[TRAP_MAX_COUNT];
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
    PLANNER_RUNTIME_DELAY_CHAPTER_THREE,
    PLANNER_RUNTIME_WAIT_FINAL,
    PLANNER_RUNTIME_DONE,
};

static enum PlannerRuntimeStage sStage;
static u32 sCommitDelayFrames;
static struct GameCtrlProc sGameControl;
static struct BMapMainProc sMapMain;
struct ProcCmd gProc_BMapMain[] = { PROC_END };
struct ProcCmd gProcScr_PlayerPhase[] = { PROC_END };
struct ProcCmd gProcScr_CpPhase[] = { PROC_END };
struct ProcCmd gProcScr_BerserkCpPhase[] = { PROC_END };

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
void SetTextFont(struct Font* font) { (void)font; }
void InitSystemTextFont(void) {}
void LoadUiFrameGraphics(void) {}
void __wrap_ReadGameSaveCoreGfx(void) {}
void UnpackChapterMapPalette(void) {}
void ChangeUnitSpritePalette(u16 palette) { (void)palette; }
void EndAllMus(void) {}
void __wrap_UnlockGame(void) {}
void ResumeMenu(void) {}
void ResetBkselPalette(void) {}
void ClearCutsceneUnits(void) {}
void EndTalk(void) {}
void EndCgText(void) {}
void EndAllBoxDialogue(void) {}
void __wrap_EndEventFaces(struct EventEngineProc* proc) { (void)proc; }
void SetNextGameActionId(int id) { (void)id; }
void SetNextChapterId(int id) { (void)id; }
void DeleteAll6CWaitMusicRelated(void) {}
void Sound_FadeOutBGM(int speed) { (void)speed; }
void Proc_EndEachMarked(int mark) { (void)mark; }
ProcPtr Proc_Find(const struct ProcCmd* script) { (void)script; return (ProcPtr)&sMapMain; }
void Proc_Goto(ProcPtr proc, int label)
{
    (void)proc;
    if (label == 2)
        ExpansionAutoplay_ResetForChapterTransition();
}
void Proc_EndEach(const struct ProcCmd* script) { (void)script; }
void Proc_End(ProcPtr proc) { (void)proc; }
void ExpansionAutoplay_ResetForChapterTransition(void) { ExpansionAutoplayPlanner_OnMapReset(); }
void ExpansionAutoplay_Reset(void) { ExpansionAutoplayPlanner_Reset(); }
void DebugToolsPhaseControl_Reset(void) {}
void DebugToolsPhaseControl_RestorePersistentTurnForChapterTransition(void) {}
void StoreRNState(u16* seeds)
{
    seeds[0] = sSeeds[0];
    seeds[1] = sSeeds[1];
    seeds[2] = sSeeds[2];
}
unsigned GetLCGRNValue(void) { return 0x12345678; }
u32 GetRNConsumptionCount(void) { return sConsumption; }
struct Unit* GetUnit(int id)
{
    if (id == 1)
        return &sUnit;
    return id == (u8)sTarget.index && sTarget.pCharacterData != NULL
        ? &sTarget : NULL;
}

u8* GetPermanentFlagBits(void)
{
#if FE8_AUTOPLAY_PLANNER_RUNTIME_FLAG_DOMAIN_MODE == 6
    return NULL;
#else
    return sPermanentFlags;
#endif
}
int GetPermanentFlagBitsSize(void) { return sPermanentFlagSize; }
u8* GetChapterFlagBits(void) { return sChapterFlags; }
int GetChapterFlagBitsSize(void) { return sChapterFlagSize; }
bool CheckFlag(int flag) { return (sPermanentFlags[flag >> 3] >> (flag & 7)) & 1; }
u16* GetConvoyItemArray(void) { return sConvoy; }
s8 AreUnitsAllied(int left, int right) { return (left & 0xC0) == (right & 0xC0); }
s8 IsSameAllegiance(int left, int right) { return (left & 0xC0) == (right & 0xC0); }
int GetCurrentPhase(void) { return FACTION_BLUE; }
int GetUnitCurrentHp(struct Unit* unit) { return unit->curHP; }
int GetUnitMaxHp(struct Unit* unit) { return unit->maxHP; }
struct Trap* GetTrap(int id) { return &sTraps[id]; }
struct Trap* GetTrapAt(int x, int y)
{
    int index;
    for (index = 0; index < TRAP_MAX_COUNT; index++)
    {
        if (sTraps[index].type == TRAP_NONE)
            break;
        if (sTraps[index].xPos == x && sTraps[index].yPos == y)
            return &sTraps[index];
    }
    return NULL;
}
int GetObstacleHpAt(int x, int y)
{
    struct Trap* trap = GetTrapAt(x, y);

    if (trap == NULL)
        trap = GetObstacleTrapForTarget(x, y);
    return trap == NULL ? 0 : trap->extra;
}
int GetBallistaItemAt(int x, int y)
{
    struct Trap* trap = GetTrapAt(x, y);
    if (trap == NULL || trap->type != TRAP_BALLISTA
        || trap->data[TRAP_EXTDATA_BLST_ITEMUSES] == 0)
        return 0;
    return trap->extra
        | (trap->data[TRAP_EXTDATA_BLST_ITEMUSES] << 8);
}
struct Trap* GetRiddenBallistaAt(int x, int y)
{
    return GetBallistaItemAt(x, y) == 0 ? NULL : GetTrapAt(x, y);
}
s8 CanUnitUseWeapon(struct Unit* unit, int item) { (void)unit; (void)item; return false; }
s8 CanUnitUseStaff(struct Unit* unit, int item)
{
    (void)unit;
    return FE8_AUTOPLAY_PLANNER_RUNTIME_CANDIDATE_MODE == 2
        && GetItemIndex(item) == ITEM_STAFF_TORCH;
}
int GetUnitItemUseReachBits(struct Unit* unit, int itemSlot)
{
    (void)unit;
    (void)itemSlot;
    return FE8_AUTOPLAY_PLANNER_RUNTIME_CANDIDATE_MODE == 2
        ? REACH_MAGBY2 : REACH_RANGE1;
}
int GetItemAttributes(int item)
{
    return FE8_AUTOPLAY_PLANNER_RUNTIME_CANDIDATE_MODE == 2
        && GetItemIndex(item) == ITEM_STAFF_TORCH
        ? IA_STAFF : 0;
}
int GetItemIndex(int item) { return item & 0xFF; }
int GetUnitEquippedWeaponSlot(struct Unit* unit) { return unit->items[0] == 0 ? -1 : 0; }
int GetUnitPower(struct Unit* unit) { return unit->pow; }
int GetUnitSkill(struct Unit* unit) { return unit->skl; }
int GetUnitSpeed(struct Unit* unit) { return unit->spd; }
int GetUnitDefense(struct Unit* unit) { return unit->def; }
int GetUnitResistance(struct Unit* unit) { return unit->res; }
int GetUnitLuck(struct Unit* unit) { return unit->lck; }
const struct ExpansionAutoplayStrategyBundle* ExpansionAutoplayStrategies_GetCurrentBundle(void)
{
    return NULL;
}
const struct ExpansionAutoplayStrategy* ExpansionAutoplayStrategies_Find(u32 id)
{
    (void)id;
    return NULL;
}
enum ExpansionAutoplayStrategyResult ExpansionAutoplayStrategies_ResolveCurrent(
    struct ExpansionAutoplayStrategyResolution* resolution)
{
    resolution->strategyId = 0;
    resolution->subjectId = 0;
    resolution->source = EXPANSION_AUTOPLAY_STRATEGY_ASSIGNMENT_NONE;
    return EXPANSION_AUTOPLAY_STRATEGY_FALLBACK;
}
int GetItemMinRange(int item)
{
    return GetItemIndex(item) >= ITEM_BALLISTA_REGULAR
        && GetItemIndex(item) <= ITEM_BALLISTA_KILLER ? 2 : 1;
}
int GetItemMaxRange(int item)
{
    return GetItemIndex(item) >= ITEM_BALLISTA_REGULAR
        && GetItemIndex(item) <= ITEM_BALLISTA_KILLER ? 4 : 1;
}
int GetUnitMagBy2Range(struct Unit* unit)
{
    (void)unit;
    return FE8_AUTOPLAY_PLANNER_RUNTIME_CANDIDATE_MODE == 2
        ? 4 : 1;
}
bool IsPositionMagicSealed(int x, int y) { (void)x; (void)y; return false; }
s8 CanUnitCrossTerrain(struct Unit* unit, int terrain) { (void)unit; (void)terrain; return true; }
bool IsThereClosedChestAt(s8 x, s8 y) { (void)x; (void)y; return false; }
bool IsThereClosedDoorAt(s8 x, s8 y) { (void)x; (void)y; return false; }
s8 IsItemHammernable(int item) { (void)item; return false; }
s8 CanUnitUseHealItem(struct Unit* unit)
{
    return FE8_AUTOPLAY_PLANNER_RUNTIME_CANDIDATE_MODE == 3
        && unit->curHP < unit->maxHP;
}
s8 CanUnitUsePureWaterItem(struct Unit* unit) { (void)unit; return false; }
s8 CanUnitUseTorchItem(struct Unit* unit) { (void)unit; return false; }
s8 CanUnitUseAntitoxinItem(struct Unit* unit) { (void)unit; return false; }
s8 CanUnitUsePromotionItem(struct Unit* unit, int item) { (void)unit; (void)item; return false; }
s8 CanUnitUseStatGainItem(struct Unit* unit, int item) { (void)unit; (void)item; return false; }
s8 CanUnitUseFruitItem(struct Unit* unit) { (void)unit; return false; }
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
    int height = 8;
    int width = 8;
    int y;
    int x;
    sSeeds[0] = 1;
    sSeeds[1] = 2;
    sSeeds[2] = 3;
    TerrainTable_MovCost_FlyNormal[1] = 1;
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
#if FE8_AUTOPLAY_PLANNER_RUNTIME_CANDIDATE_MODE == 2
    sUnit.items[0] = ITEM_STAFF_TORCH;
#elif FE8_AUTOPLAY_PLANNER_RUNTIME_CANDIDATE_MODE == 3
    sUnit.items[0] = ITEM_VULNERARY | (2 << 8);
    sUnit.curHP = 10;
#elif FE8_AUTOPLAY_PLANNER_RUNTIME_CANDIDATE_MODE == 4
    sUnit.items[0] = ITEM_MINE | (2 << 8);
#elif FE8_AUTOPLAY_PLANNER_RUNTIME_CANDIDATE_MODE == 5
    sClass.attributes = CA_BALLISTAE;
#elif FE8_AUTOPLAY_PLANNER_RUNTIME_CANDIDATE_MODE >= 6
    sUnit.items[0] = (ITEM_FILLAS_MIGHT
        + FE8_AUTOPLAY_PLANNER_RUNTIME_CANDIDATE_MODE - 6) | (2 << 8);
#endif
#if FE8_AUTOPLAY_PLANNER_RUNTIME_CANDIDATE_MODE >= 5
    sTargetCharacter.number = 2;
    sTargetClass.number = 2;
    sTarget.pCharacterData = &sTargetCharacter;
    sTarget.pClassData = &sTargetClass;
    sTarget.index =
        FE8_AUTOPLAY_PLANNER_RUNTIME_CANDIDATE_MODE == 5 ? 0x81 : 2;
    sTarget.xPos =
        FE8_AUTOPLAY_PLANNER_RUNTIME_CANDIDATE_MODE == 5 ? 3 : 1;
    sTarget.yPos = 0;
    sTarget.maxHP = 20;
    sTarget.curHP = 20;
#endif
#if FE8_AUTOPLAY_PLANNER_RUNTIME_CANDIDATE_MODE == 5
    sTraps[0].type = TRAP_BALLISTA;
    sTraps[0].xPos = 0;
    sTraps[0].yPos = 0;
    sTraps[0].extra = ITEM_BALLISTA_REGULAR;
    sTraps[0].data[TRAP_EXTDATA_BLST_ITEMUSES] = 3;
    sTraps[1].type = TRAP_OBSTACLE;
    sTraps[1].xPos = 2;
    sTraps[1].extra = 20;
#elif FE8_AUTOPLAY_PLANNER_RUNTIME_CANDIDATE_MODE == 1
    sUnit.state = US_NOT_DEPLOYED;
#endif
    gActiveUnit = &sUnit;
    gActiveUnitId = 1;
    gBmMapSize.x = width;
    gBmMapSize.y = height;
    for (y = 0; y < height; y++)
    {
        sMovementRows[y] = sMovementData[y];
        sUnitRows[y] = sUnitData[y];
        sTerrainRows[y] = sTerrainData[y];
        sFogRows[y] = sFogData[y];
        for (x = 0; x < width; x++)
        {
            sMovementData[y][x] = 1;
            sUnitData[y][x] = 0;
            sTerrainData[y][x] = 1;
            sFogData[y][x] = 1;
        }
    }
    sFogData[0][1] = 0;
    sUnitData[0][0] = 1;
#if FE8_AUTOPLAY_PLANNER_RUNTIME_CANDIDATE_MODE == 5
    sUnitData[0][3] = 0x81;
    sTerrainData[0][2] = TERRAIN_SNAG;
#elif FE8_AUTOPLAY_PLANNER_RUNTIME_CANDIDATE_MODE >= 6
    sUnitData[0][1] = 2;
#endif
#if FE8_AUTOPLAY_PLANNER_RUNTIME_CANDIDATE_MODE == 1
    for (y = 0; y < height; y++)
        for (x = 0; x < width; x++)
            sMovementData[y][x] = MAP_MOVEMENT_MAX + 1;
    sMovementData[0][0] = 0;
#endif
    gBmMapMovement = sMovementRows;
    gBmMapUnit = sUnitRows;
    gBmMapTerrain = sTerrainRows;
    gBmMapFog = sFogRows;
    gPlaySt.chapterIndex = 1;
    gPlaySt.chapterTurnNumber = 1;
    gPlaySt.chapterVisionRange = 3;
    gPlaySt.partyGoldAmount = 1000;
    sConvoy[0] = 1;
#if FE8_AUTOPLAY_PLANNER_RUNTIME_ZERO_DIGEST
    sPermanentFlags[0] = 0xCC;
    sPermanentFlags[1] = 0x24;
    sPermanentFlags[2] = 0x31;
    sPermanentFlags[3] = 0xC4;
    sConvoy[0] = 0;
    sConvoy[97] = 0xEDD0;
    sConvoy[98] = 0xC25D;
    gPlaySt.partyGoldAmount = 2166136261u;
#endif
    sMapMain.gameCtrl = &sGameControl;
    ExpansionAutoplayPlanner_Reset();
    ExpansionAutoplayPlanner_OnMapReady();
#if FE8_AUTOPLAY_PLANNER_RUNTIME_STARTUP_STATE
    ExpansionAutoplayPlanner_PollStart();
    gExpansionAutoplayPlannerObservation.state =
        FE8_AUTOPLAY_PLANNER_RUNTIME_STARTUP_STATE;
#endif
    sStage = PLANNER_RUNTIME_WAIT_START;
}
static void PublishNextChapter(struct AiDecision* decision, int chapter,
                               enum PlannerRuntimeStage stage)
{
    u32 command = _EvtArg0(
        EV_CMD_CHANGECHAPTER,
        chapter,
        FE8_AUTOPLAY_PLANNER_RUNTIME_TRANSITION_SUBCODE,
        chapter);
    struct EventEngineProc event;

    memset(&event, 0, sizeof(event));
    event.pEventCurrent = (const u16*)&command;
    event.execType = EV_EXEC_UNK5;
    event.evStateBits = EV_STATE_ABORT;
    Event2A_MoveToChapter(&event);
    EventEngine_OnEnd(&event);
    gPlaySt.chapterIndex = chapter;
    gPlaySt.chapterTurnNumber = 1;
    sConvoy[chapter - 1] = chapter;
    ExpansionAutoplayPlanner_OnMapReady();
    PrepareDecision(decision);
    ExpansionAutoplayPlanner_OfferDecision(decision);
    sStage = stage;
}
static void PollCommittedDecision(
    struct AiDecision* decision,
    enum PlannerRuntimeStage delayStage)
{
    enum ExpansionAutoplayPlannerDecisionResult result;
    u16 selectedItem = sUnit.items[0];
#if FE8_AUTOPLAY_PLANNER_RUNTIME_MUTATE_SELECTED_ITEM
    bool mutated =
        gExpansionAutoplayPlannerCommand.kind
        == EXPANSION_AUTOPLAY_PLANNER_COMMAND_COMMIT;
#endif
#if FE8_AUTOPLAY_PLANNER_RUNTIME_MUTATE_SELECTED_ITEM
    if (mutated)
#if FE8_AUTOPLAY_PLANNER_RUNTIME_CANDIDATE_MODE == 5
        sTraps[0].data[TRAP_EXTDATA_BLST_ITEMUSES]--;
#elif FE8_AUTOPLAY_PLANNER_RUNTIME_CANDIDATE_MODE >= 6
        sTarget.state |= US_DEAD;
#else
        sUnit.items[0] ^= 0x100;
#endif
#endif
    result = ExpansionAutoplayPlanner_PollDecision(decision);
#if FE8_AUTOPLAY_PLANNER_RUNTIME_MUTATE_SELECTED_ITEM
    if (mutated)
#if FE8_AUTOPLAY_PLANNER_RUNTIME_CANDIDATE_MODE == 5
        sTraps[0].data[TRAP_EXTDATA_BLST_ITEMUSES]++;
#elif FE8_AUTOPLAY_PLANNER_RUNTIME_CANDIDATE_MODE >= 6
        sTarget.state &= ~US_DEAD;
#else
        sUnit.items[0] = selectedItem;
#endif
#else
    (void)selectedItem;
#endif
    if (result == EXPANSION_AUTOPLAY_PLANNER_DECISION_ACCEPTED)
    {
        sUnit.xPos = decision->xMove;
        sUnit.yPos = decision->yMove;
#if FE8_AUTOPLAY_PLANNER_RUNTIME_STALL_AFTER_COMMIT
        sStage = PLANNER_RUNTIME_DONE;
#else
        sCommitDelayFrames =
            FE8_AUTOPLAY_PLANNER_RUNTIME_COMMIT_DELAY_FRAMES;
        if (sCommitDelayFrames == 0)
            sCommitDelayFrames = 1;
        sStage = delayStage;
#endif
    }
    else if (result == EXPANSION_AUTOPLAY_PLANNER_DECISION_CANCELLED)
    {
        sStage = PLANNER_RUNTIME_DONE;
    }
}

static void TickRuntime(void)
{
    static struct AiDecision decision;

#if FE8_AUTOPLAY_PLANNER_RUNTIME_IGNORE_COMMANDS
    if (gExpansionAutoplayPlannerObservation.state
        != EXPANSION_AUTOPLAY_PLANNER_STATE_READY)
        ExpansionAutoplayPlanner_PollStart();
    return;
#endif
#if FE8_AUTOPLAY_PLANNER_RUNTIME_STARTUP_STATE
    return;
#endif
    switch (sStage)
    {
    case PLANNER_RUNTIME_WAIT_START:
        if (!ExpansionAutoplayPlanner_PollStart())
            return;
#if FE8_AUTOPLAY_PLANNER_RUNTIME_ACK_OVERRIDE
        gExpansionAutoplayPlannerCommand.result =
            FE8_AUTOPLAY_PLANNER_RUNTIME_ACK_RESULT;
        gExpansionAutoplayPlannerCommand.rejection =
            FE8_AUTOPLAY_PLANNER_RUNTIME_ACK_REJECTION;
        return;
#endif
        PrepareDecision(&decision);
        if (ExpansionAutoplayPlanner_OfferDecision(&decision)
            == EXPANSION_AUTOPLAY_PLANNER_DECISION_WAIT)
            sStage = PLANNER_RUNTIME_WAIT_CHAPTER_ONE;
        return;
    case PLANNER_RUNTIME_WAIT_CHAPTER_ONE:
        PollCommittedDecision(
            &decision,
            PLANNER_RUNTIME_DELAY_CHAPTER_ONE);
        return;
    case PLANNER_RUNTIME_DELAY_CHAPTER_ONE:
        if (--sCommitDelayFrames == 0)
            PublishNextChapter(&decision, 2, PLANNER_RUNTIME_WAIT_CHAPTER_TWO);
        return;
    case PLANNER_RUNTIME_WAIT_CHAPTER_TWO:
        PollCommittedDecision(
            &decision,
            PLANNER_RUNTIME_DELAY_CHAPTER_TWO);
        return;
    case PLANNER_RUNTIME_DELAY_CHAPTER_TWO:
        if (--sCommitDelayFrames == 0)
            PublishNextChapter(&decision, 3, PLANNER_RUNTIME_WAIT_FINAL);
        return;
    case PLANNER_RUNTIME_DELAY_CHAPTER_THREE:
        if (--sCommitDelayFrames == 0)
        {
            PrepareDecision(&decision);
            ExpansionAutoplayPlanner_OfferDecision(&decision);
            sStage = PLANNER_RUNTIME_WAIT_FINAL;
        }
        return;
    case PLANNER_RUNTIME_WAIT_FINAL:
        PollCommittedDecision(&decision, PLANNER_RUNTIME_DELAY_CHAPTER_THREE);
        return;
    default:
        return;
    }
}

void PlannerRuntime_Main(void)
{
    volatile u16* vcount = (volatile u16*)0x04000006;
    int startupDelay = FE8_AUTOPLAY_PLANNER_RUNTIME_STARTUP_DELAY;

    InitializeRuntime();
    while (startupDelay-- > 0)
    {
        while (*vcount >= 160)
            ;
        while (*vcount < 160)
            ;
    }
    for (;;)
    {
        while (*vcount >= 160)
            ;
        while (*vcount < 160)
            ;
        TickRuntime();
    }
}
