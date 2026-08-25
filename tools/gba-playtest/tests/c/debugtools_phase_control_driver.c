#include "global.h"

#include <stdio.h>
#include <string.h>

#include "bm.h"
#include "bmio.h"
#include "bmmind.h"
#include "bmsave.h"
#include "bmunit.h"
#include "cp_common.h"
#include "event.h"
#include "expansion_autoplay.h"
#include "expansion_debugtools.h"
#include "fontgrp.h"
#include "gamecontrol.h"
#include "hardware.h"
#include "mu.h"
#include "playerphase.h"
#include "uimenu.h"

#define CHECK(condition, message) \
    do \
    { \
        if (!(condition)) \
        { \
            fprintf(stderr, "DEBUGTOOLS_PHASE_CONTROL_HOST_TEST: FAIL: %s\n", message); \
            return 1; \
        } \
    } while (0)

struct DebugToolsProbe gDebugToolsProbe;
struct ActionData gActionData;
struct Unit *gActiveUnit;

struct ProcCmd CONST_DATA gProc_BMapMain[] = { { 0 } };
struct ProcCmd gProcScr_PlayerPhase[] = { { 0 } };
struct ProcCmd gProcScr_Playerphase_0[] = { { 0 } };
struct ProcCmd CONST_DATA gProcScr_CpPhase[] = { { 0 } };
struct ProcCmd CONST_DATA gProcScr_BerserkCpPhase[] = { { 0 } };
struct ProcCmd CONST_DATA gProcScr_UpdateTraps[] = { { 0 } };
struct ProcCmd CONST_DATA ProcScr_CamMove[] = { { 0 } };

struct LCDControlBuffer gLCDControlBuffer;

static struct BMapMainProc sMapMainProc;
static struct GameCtrlProc sGameCtrl;
static struct Proc sPlayerPhaseProc;
static struct Proc sBlockingProc;
static struct Unit sActiveUnit;
static struct MuProc sActiveMu;
static struct Font sFont;
static u16 sBgMap[32 * 32];
struct Font* gActiveFont = &sFont;
static int sMapMainLive;
static int sPlayerPhaseLive;
static int sPlayerActionLive;
static int sActiveMuLive;
static int sComputerPhaseLive;
static int sBerserkPhaseLive;
static int sCameraLive;
static int sEventLive;
static int sBattleEventLive;
static int sBattleLive;
static int sFadeLive;
static int sComputerPhaseStarts;
static int sBerserkActionStarts;
static int sBerserkEligible;
static int sTrapUpdateStarts;
static int sTrapDecayCount;
static int sDebugSessionActive;
static int sProcBreaks;
static int sProcGotoLabel;
static int sPhaseSwitchEventCount;
static int sPhaseSwitchEventFaction;
static int sPhaseSwitchEventTurn;
static int sSuspendWriteCount;
static int sSuspendWriteFailed;
static int sSuspendSerializedTurn;
static int sChapterStatsEnabled;
static int sChapterStatsRankingsCount;
static int sChapterStatsSaveRankingsCount;
static int sChapterStatsStartMapCount;
static int sChapterStatsCleanupCount;
static int sChapterStatsRnCount;
static u16 sChapterStatsStoredRn[3];

extern struct ChapterStats gChapterStats[WIN_ARRAY_NUM];
u16 gGmMonsterRnState[3];

void PlayerPhase_MainIdle(ProcPtr proc)
{
    (void)proc;
}

void PlayerPhase_WaitForUnitMovement(ProcPtr proc)
{
    (void)proc;
}

s8 PlayerPhase_PrepareAction(ProcPtr proc)
{
    (void)proc;
    return 0;
}

void PlayerPhase_ApplyUnitMovement(ProcPtr proc)
{
    (void)proc;
}

void PlayerPhase_FinishAction(ProcPtr proc)
{
    (void)proc;
}

ProcPtr Proc_Find(const struct ProcCmd* script)
{
    if (script == gProc_BMapMain)
        return sMapMainLive ? (ProcPtr)&sMapMainProc : NULL;
    if (script == gProcScr_PlayerPhase)
        return sPlayerPhaseLive ? &sPlayerPhaseProc : NULL;
    if (script == gProcScr_Playerphase_0)
        return sPlayerActionLive ? &sBlockingProc : NULL;
    if (script == gProcScr_CpPhase)
        return sComputerPhaseLive ? &sBlockingProc : NULL;
    if (script == gProcScr_BerserkCpPhase)
        return sBerserkPhaseLive ? &sBlockingProc : NULL;
    if (script == ProcScr_CamMove)
        return sCameraLive ? &sBlockingProc : NULL;

    return NULL;
}

struct MuProc *GetUnitMu(struct Unit *unit)
{
    if (sActiveMuLive && unit == gActiveUnit)
        return &sActiveMu;
    return NULL;
}

ProcPtr Proc_StartBlocking(const struct ProcCmd* script, ProcPtr parent)
{
    (void)parent;

    if (script == gProcScr_CpPhase)
        sComputerPhaseStarts++;
    else if (script == gProcScr_BerserkCpPhase && sBerserkEligible)
        sBerserkActionStarts++;
    else if (script == gProcScr_UpdateTraps)
        sTrapUpdateStarts++;

    return &sBlockingProc;
}

void Proc_Break(ProcPtr proc)
{
    (void)proc;
    sProcBreaks++;
}

void Proc_Goto(ProcPtr proc, int label)
{
    (void)proc;
    sProcGotoLabel = label;
}

void Proc_End(ProcPtr proc)
{
    if (proc == (ProcPtr)&sMapMainProc)
        sMapMainLive = 0;
}

void Proc_EndEachMarked(int mark)
{
    (void)mark;
}

void DebugTools_CleanupMusicPreview(void)
{
}

void DebugTools_ForceSessionCleanup(void)
{
    DebugToolsPhaseControl_Reset();
}

int DebugTools_IsHubActive(void)
{
    return sDebugSessionActive;
}

int DebugTools_IsBootstrapSuppressionActive(void)
{
    return 0;
}

void WriteSuspendSave(int slot)
{
    (void)slot;

    DebugToolsPhaseControl_BeginSuspendSerialization();
    sSuspendWriteCount++;
    sSuspendSerializedTurn = gPlaySt.chapterTurnNumber;
    if (sSuspendWriteFailed)
    {
        DebugToolsPhaseControl_EndSuspendSerialization();
        return;
    }
    DebugToolsPhaseControl_EndSuspendSerialization();
}

u8 MenuAlwaysEnabled(const struct MenuItemDef* def, int number)
{
    (void)def;
    (void)number;
    return 1;
}

u8 MenuCancelSelect(struct MenuProc* menu, struct MenuItemProc* item)
{
    (void)menu;
    (void)item;
    return 0;
}

void SetupDebugFontForBG(int bg, int tileDataOffset)
{
    (void)bg;
    (void)tileDataOffset;
}

u16* BG_GetMapBuffer(int bg)
{
    (void)bg;
    return sBgMap;
}

void PrintDebugStringToBG(u16* bg, const char* asciiStr)
{
    (void)bg;
    (void)asciiStr;
}

struct MenuProc* StartOrphanMenu(const struct MenuDef* def)
{
    (void)def;
    return NULL;
}

struct Proc* EndMenu(struct MenuProc* proc)
{
    return (struct Proc*)proc;
}

ProcPtr Proc_Start(const struct ProcCmd* script, ProcPtr parent)
{
    (void)script;
    (void)parent;
    return &sBlockingProc;
}

s8 EventEngineExists(void)
{
    return sEventLive;
}

int BattleEventEngineExists(void)
{
    return sBattleEventLive;
}

int IsBattleDeamonActive(void)
{
    return sBattleLive;
}

bool8 DoesBMXFADEExist(void)
{
    return sFadeLive;
}

void ClearActiveFactionGrayedStates(void)
{
}

void RefreshUnitSprites(void)
{
}

s8 RunPhaseSwitchEvents(void)
{
    sPhaseSwitchEventCount++;
    sPhaseSwitchEventFaction = gPlaySt.faction;
    sPhaseSwitchEventTurn = gPlaySt.chapterTurnNumber;
    return false;
}

void ProcessTurnSupportExp(void)
{
}

int CheckFlag(int flag)
{
    return flag == 3 && sChapterStatsEnabled;
}

void ComputeChapterRankings(void)
{
    sChapterStatsRankingsCount++;
}

void SaveEndgameRankings(void)
{
    sChapterStatsSaveRankingsCount++;
}

void StartBattleMap(struct GameCtrlProc* gameCtrl)
{
    (void)gameCtrl;
    sChapterStatsStartMapCount++;
}

void ChapterChangeUnitCleanup(void)
{
    sChapterStatsCleanupCount++;
}

u32 GetGameClock(void)
{
    return 0;
}

int NextRN(void)
{
    sChapterStatsRnCount++;
    return 0;
}

void StoreRNState(u16* seeds)
{
    memcpy(sChapterStatsStoredRn, seeds, sizeof(sChapterStatsStoredRn));
}

void DecayTraps(void)
{
    sTrapDecayCount++;
}

static void ResetHarness(void)
{
    memset(&gPlaySt, 0, sizeof(gPlaySt));
    memset(&gDebugToolsProbe, 0, sizeof(gDebugToolsProbe));
    memset(&sMapMainProc, 0, sizeof(sMapMainProc));
    memset(&sGameCtrl, 0, sizeof(sGameCtrl));
    memset(&sPlayerPhaseProc, 0, sizeof(sPlayerPhaseProc));
    memset(&sBlockingProc, 0, sizeof(sBlockingProc));
    sMapMainLive = 1;
    sPlayerPhaseLive = 1;
    sPlayerActionLive = 0;
    sActiveMuLive = 0;
    sComputerPhaseLive = 0;
    sBerserkPhaseLive = 0;
    sCameraLive = 0;
    sEventLive = 0;
    sBattleEventLive = 0;
    sBattleLive = 0;
    sFadeLive = 0;
    sComputerPhaseStarts = 0;
    sBerserkActionStarts = 0;
    sBerserkEligible = 0;
    sTrapUpdateStarts = 0;
    sTrapDecayCount = 0;
    sDebugSessionActive = 0;
    sProcBreaks = 0;
    sProcGotoLabel = -1;
    sPhaseSwitchEventCount = 0;
    sPhaseSwitchEventFaction = -1;
    sPhaseSwitchEventTurn = -1;
    sSuspendWriteCount = 0;
    sSuspendWriteFailed = 0;
    sSuspendSerializedTurn = -1;
    sChapterStatsEnabled = 1;
    sChapterStatsRankingsCount = 0;
    sChapterStatsSaveRankingsCount = 0;
    sChapterStatsStartMapCount = 0;
    sChapterStatsCleanupCount = 0;
    sChapterStatsRnCount = 0;
    memset(sChapterStatsStoredRn, 0, sizeof(sChapterStatsStoredRn));
    memset(gGmMonsterRnState, 0, sizeof(gGmMonsterRnState));
    memset(gChapterStats, 0, sizeof(gChapterStats));
    gActiveUnit = NULL;
    memset(&sActiveUnit, 0, sizeof(sActiveUnit));
    memset(&sActiveMu, 0, sizeof(sActiveMu));
    sMapMainProc.gameCtrl = &sGameCtrl;
    sGameCtrl.proc_lockCnt = 1;
    sPlayerPhaseProc.proc_idleCb = PlayerPhase_MainIdle;

    gPlaySt.faction = FACTION_BLUE;
    gPlaySt.chapterTurnNumber = 5;
    gBmSt.lock = 1;
    ExpansionAutoplay_Reset();
    ExpansionAutoplay_OnPlayerPhaseStart();
    DebugToolsPhaseControl_Reset();
}

static int TestTurnRequestAtBoundary(void)
{
    struct PlaySt_OptionBits configBefore;

    ResetHarness();
    configBefore = gPlaySt.config;

    CHECK(DebugToolsPhaseControl_RequestTurn(9) == DEBUGTOOLS_PHASE_CONTROL_OK,
          "a bounded turn request must queue from a stable blue PLAYER phase");
    CHECK(gPlaySt.chapterTurnNumber == 5,
          "a queued turn request must not mutate the live phase");
    CHECK(gDebugToolsProbe.phaseControlRequestedCount == 1
              && gDebugToolsProbe.phaseControlLastRequestKind
                  == DEBUGTOOLS_PHASE_CONTROL_REQUEST_TURN,
          "turn request telemetry must identify the queued operation");

    CHECK(BmMain_ChangePhase() == true,
          "the phase change must complete before starting the destination route");
    CHECK(sPhaseSwitchEventCount == 1
              && sPhaseSwitchEventFaction == FACTION_RED
              && sPhaseSwitchEventTurn == 9,
          "destination phase events must observe the requested turn");
    BmMain_StartPhase(&sMapMainProc);

    CHECK(gPlaySt.chapterTurnNumber == 9,
          "the existing phase router must apply the queued turn at its boundary");
    CHECK(sComputerPhaseStarts == 1 && sProcBreaks == 1,
          "a turn request must retain the ordinary red computer phase");
    CHECK(gDebugToolsProbe.phaseControlAppliedCount == 1
              && gDebugToolsProbe.phaseControlRestoredCount == 1,
          "a completed turn request must record application and restoration");
    CHECK(memcmp(&gPlaySt.config, &configBefore, sizeof(configBefore)) == 0,
          "turn control must never write persistent debug-control option bits");

    return 0;
}

static int TestTransientTurnSuspendSerialization(void)
{
    ResetHarness();
    gPlaySt.chapterTurnNumber = 1;
    CHECK(DebugToolsPhaseControl_RequestTurn(2) == DEBUGTOOLS_PHASE_CONTROL_OK,
          "turn request must queue before suspend serialization");
    CHECK(BmMain_ChangePhase() == true,
          "turn request must apply before the destination phase event");
    CHECK(gPlaySt.chapterTurnNumber == 2 && sPhaseSwitchEventTurn == 2,
          "live phase events must observe the requested turn");

    BmMain_SuspendBeforePhase();
    CHECK(sSuspendWriteCount == 1 && sSuspendSerializedTurn == 1
              && gPlaySt.chapterTurnNumber == 2,
          "first suspend must serialize the original turn and restore the live turn");

    gPlaySt.faction = FACTION_GREEN;
    CHECK(BmMain_ChangePhase() == true,
          "green-to-blue must complete the native turn increment");
    CHECK(gPlaySt.faction == FACTION_BLUE && gPlaySt.chapterTurnNumber == 3,
          "live turn must continue naturally after the override");
    BmMain_SuspendBeforePhase();
    CHECK(sSuspendWriteCount == 2 && sSuspendSerializedTurn == 2
              && gPlaySt.chapterTurnNumber == 3,
          "later suspend must serialize the naturally advanced persistent turn");

    sSuspendWriteFailed = 1;
    BmMain_SuspendBeforePhase();
    CHECK(sSuspendWriteCount == 3 && sSuspendSerializedTurn == 2
              && gPlaySt.chapterTurnNumber == 3,
          "failed suspend writer path must still restore the live turn");
    sSuspendWriteFailed = 0;

    DebugToolsPhaseControl_Reset();
    BmMain_SuspendBeforePhase();
    CHECK(sSuspendWriteCount == 4 && sSuspendSerializedTurn == 3
              && gPlaySt.chapterTurnNumber == 3,
          "reset after an applied turn must not leak the serialization swap");

    ResetHarness();
    CHECK(DebugToolsPhaseControl_RequestTurn(9) == DEBUGTOOLS_PHASE_CONTROL_OK,
          "turn request must queue before expiry");
    DebugToolsPhaseControl_Reset();
    BmMain_SuspendBeforePhase();
    CHECK(sSuspendWriteCount == 1 && sSuspendSerializedTurn == 5
              && gPlaySt.chapterTurnNumber == 5,
          "expired request must not leave a serialization swap active");

    return 0;
}

static int ApplyOverrideAndAdvanceNaturally(void)
{
    u16 persistentTurn;

    gPlaySt.chapterTurnNumber = 5;
    CHECK(DebugToolsPhaseControl_RequestTurn(9) == DEBUGTOOLS_PHASE_CONTROL_OK,
          "chapter-stat override must queue from the stable player boundary");
    CHECK(BmMain_ChangePhase() == true,
          "chapter-stat override must apply at the real phase boundary");
    CHECK(gPlaySt.chapterTurnNumber == 9,
          "live chapter turn must retain the requested override");

    gPlaySt.faction = FACTION_GREEN;
    SwitchPhases();
    CHECK(gPlaySt.faction == FACTION_BLUE && gPlaySt.chapterTurnNumber == 10,
          "the live turn must advance naturally after the override");
    CHECK(DebugToolsPhaseControl_GetSerializedSuspendTurn(&persistentTurn)
              && persistentTurn == 6,
          "the retained persistent turn must advance naturally before completion");
    return 0;
}

static int CheckChapterStatsSaveBytes(int expectedTurn, int expectedChapter)
{
    struct ChapterStats* stats = GetChapterStats(0);
    u16 savedWord;
    u16 expectedWord;
    u16 retainedTurn;

    memcpy(&savedWord, stats, sizeof(savedWord));
    expectedWord = (u16)(
        ((u16)expectedChapter & 0x7F)
        | (((u16)expectedTurn & 0x1FF) << 7));
    CHECK(stats->chapter_index == expectedChapter
              && stats->chapter_turn == expectedTurn,
          "chapter statistics must retain the persistent turn, not the live override");
    CHECK(savedWord == expectedWord,
          "the packed chapter-stat save bytes must encode the persistent turn");
    CHECK(!DebugToolsPhaseControl_GetSerializedSuspendTurn(&retainedTurn),
          "the retained turn must clear only after chapter-stat registration");
    return 0;
}

static int RegisterChapterStatsThroughPath(int path)
{
    struct GameCtrlProc gameCtrl;

    switch (path)
    {
    case 0:
        BmMain_BeginNextChapter();
        break;

    case 1:
        GameCtrl_DeclareCompletedChapter();
        break;

    default:
        memset(&gameCtrl, 0, sizeof(gameCtrl));
        gameCtrl.nextChapter = (u8)(gPlaySt.chapterIndex + 1);
        GameControl_ChapterSwitch(&gameCtrl);
        break;
    }

    return 0;
}

static int TestChapterStatsPersistentTurn(void)
{
    int path;
    int chapter;

    for (path = 0; path < 3; path++)
    {
        ResetHarness();
        chapter = 0x12 + path;
        gPlaySt.chapterIndex = (u8)chapter;
        CHECK(ApplyOverrideAndAdvanceNaturally() == 0,
              "override setup for chapter-stat registration");
        CHECK(RegisterChapterStatsThroughPath(path) == 0,
              "chapter-stat override registration path");
        CHECK(CheckChapterStatsSaveBytes(6, chapter) == 0,
              "persistent chapter-stat save bytes");

        ResetHarness();
        gPlaySt.chapterIndex = (u8)chapter;
        gPlaySt.chapterTurnNumber = 7;
        CHECK(RegisterChapterStatsThroughPath(path) == 0,
              "natural chapter-stat registration path");
        CHECK(CheckChapterStatsSaveBytes(7, chapter) == 0,
              "natural chapter-stat save-byte negative control");
    }

    return 0;
}

static int TestPlayerActionOwnership(void)
{
    u32 rejectedBefore;

    ResetHarness();
    gActiveUnit = &sActiveUnit;
    sActiveMuLive = 1;
    sPlayerPhaseProc.proc_idleCb = PlayerPhase_WaitForUnitMovement;
    rejectedBefore = gDebugToolsProbe.phaseControlRejectedCount;
    CHECK(DebugToolsPhaseControl_RequestTurn(6)
              == DEBUGTOOLS_PHASE_CONTROL_ERR_UNSAFE_BOUNDARY,
          "active unit movement MU must reject a request");

    sActiveMuLive = 0;
    gActionData.unitActionType = UNIT_ACTION_COMBAT;
    sPlayerPhaseProc.proc_idleCb = (ProcFunc)PlayerPhase_PrepareAction;
    CHECK(DebugToolsPhaseControl_RequestTurn(6)
              == DEBUGTOOLS_PHASE_CONTROL_ERR_UNSAFE_BOUNDARY,
          "combat action stage must reject a request");

    gActionData.unitActionType = UNIT_ACTION_USE_ITEM;
    sPlayerPhaseProc.proc_idleCb = (ProcFunc)PlayerPhase_ApplyUnitMovement;
    CHECK(DebugToolsPhaseControl_RequestTurn(6)
              == DEBUGTOOLS_PHASE_CONTROL_ERR_UNSAFE_BOUNDARY,
          "item action stage must reject a request");

    gActionData.unitActionType = UNIT_ACTION_RESCUE;
    sPlayerPhaseProc.proc_idleCb = (ProcFunc)PlayerPhase_FinishAction;
    CHECK(DebugToolsPhaseControl_RequestTurn(6)
              == DEBUGTOOLS_PHASE_CONTROL_ERR_UNSAFE_BOUNDARY,
          "rescue action stage must reject a request");

    CHECK(gDebugToolsProbe.phaseControlRejectedCount == rejectedBefore + 4
              && gDebugToolsProbe.phaseControlRequestedCount == 0
              && gDebugToolsProbe.phaseControlLastResult
                  == DEBUGTOOLS_PHASE_CONTROL_ERR_UNSAFE_BOUNDARY,
          "player action ownership must reject without queueing a request");

    gActiveUnit = NULL;
    gActionData.unitActionType = 0;
    sPlayerPhaseProc.proc_idleCb = PlayerPhase_MainIdle;
    CHECK(DebugToolsPhaseControl_RequestTurn(6) == DEBUGTOOLS_PHASE_CONTROL_OK,
          "idle player map must accept a request after action ownership clears");
    DebugToolsPhaseControl_Reset();

    return 0;
}

static int TestFactionModesAndRestoration(void)
{
    ResetHarness();
    CHECK(DebugToolsPhaseControl_RequestFactionMode(
              FACTION_RED, DEBUGTOOLS_PHASE_CONTROL_BLOCKED)
              == DEBUGTOOLS_PHASE_CONTROL_OK,
          "red BLOCKED must queue at a safe boundary");
    sBerserkEligible = 1;
    gPlaySt.faction = FACTION_RED;
    BmMain_StartPhase(&sMapMainProc);
    if (sProcGotoLabel != 12)
        Proc_StartBlocking(gProcScr_BerserkCpPhase, &sMapMainProc);
    if (sProcGotoLabel == 12)
        BmMain_UpdateTraps(&sMapMainProc);
    CHECK(sComputerPhaseStarts == 0 && sBerserkActionStarts == 0
              && sTrapUpdateStarts == 0 && sTrapDecayCount == 0
              && sProcGotoLabel == 12,
          "BLOCKED must bypass normal and eligible berserk computer actions");
    CHECK(gDebugToolsProbe.phaseControlAppliedCount == 1
              && gDebugToolsProbe.phaseControlRestoredCount == 1,
          "a blocked phase must restore ordinary ownership immediately");

    BmMain_StartPhase(&sMapMainProc);
    CHECK(sComputerPhaseStarts == 1,
          "the next red phase must restore the ordinary computer route");

    ResetHarness();
    CHECK(DebugToolsPhaseControl_RequestFactionMode(
              FACTION_GREEN, DEBUGTOOLS_PHASE_CONTROL_BLOCKED)
              == DEBUGTOOLS_PHASE_CONTROL_OK,
          "green BLOCKED must queue at a safe boundary");
    sBerserkEligible = 1;
    gPlaySt.faction = FACTION_GREEN;
    BmMain_StartPhase(&sMapMainProc);
    if (sProcGotoLabel != 12)
        Proc_StartBlocking(gProcScr_BerserkCpPhase, &sMapMainProc);
    if (sProcGotoLabel == 12)
        BmMain_UpdateTraps(&sMapMainProc);
    CHECK(sComputerPhaseStarts == 0 && sBerserkActionStarts == 0
              && sTrapUpdateStarts == 1 && sTrapDecayCount == 1
              && sProcGotoLabel == 12,
          "green BLOCKED must retain traps while bypassing all AI actions");

    ResetHarness();
    CHECK(DebugToolsPhaseControl_RequestFactionMode(
              FACTION_GREEN, DEBUGTOOLS_PHASE_CONTROL_COMPUTER)
              == DEBUGTOOLS_PHASE_CONTROL_OK,
          "green COMPUTER must queue as an explicit typed request");
    gPlaySt.faction = FACTION_RED;
    BmMain_StartPhase(&sMapMainProc);
    CHECK(sComputerPhaseStarts == 1
              && gDebugToolsProbe.phaseControlAppliedCount == 0,
          "a green request must not overlap the preceding red controller");
    gPlaySt.faction = FACTION_GREEN;
    BmMain_StartPhase(&sMapMainProc);
    CHECK(sComputerPhaseStarts == 2
              && gDebugToolsProbe.phaseControlAppliedCount == 1
              && gDebugToolsProbe.phaseControlRestoredCount == 1,
          "green COMPUTER must consume through the existing green route");

    return 0;
}

static int TestRejectedAndExpiredRequests(void)
{
    u32 rejectedBefore;
    u32 requestsBeforeUnsafeOwnership;
    u32 expiredBeforeCleanup;

    ResetHarness();
    rejectedBefore = gDebugToolsProbe.phaseControlRejectedCount;
    CHECK(DebugToolsPhaseControl_RequestTurn(0)
              == DEBUGTOOLS_PHASE_CONTROL_ERR_INVALID_TURN,
          "turn zero must fail closed");
    CHECK(DebugToolsPhaseControl_RequestTurn(1000)
              == DEBUGTOOLS_PHASE_CONTROL_ERR_INVALID_TURN,
          "turn 1000 must fail closed");
    CHECK(DebugToolsPhaseControl_RequestFactionMode(
              FACTION_BLUE, DEBUGTOOLS_PHASE_CONTROL_COMPUTER)
              == DEBUGTOOLS_PHASE_CONTROL_ERR_INVALID_FACTION,
          "blue must remain owned by the #85/#87 controller seam");
    CHECK(DebugToolsPhaseControl_RequestFactionMode(
              FACTION_RED, DEBUGTOOLS_PHASE_CONTROL_PLAYER)
              == DEBUGTOOLS_PHASE_CONTROL_ERR_UNSUPPORTED_MODE,
          "red PLAYER must reject because the existing player commit path is blue-only");
    CHECK(gDebugToolsProbe.phaseControlRejectedCount == rejectedBefore + 4,
          "every invalid typed request must record one rejection");

    sEventLive = 1;
    CHECK(DebugToolsPhaseControl_RequestTurn(6)
              == DEBUGTOOLS_PHASE_CONTROL_ERR_UNSAFE_BOUNDARY,
          "active events must reject without queueing a request");
    sEventLive = 0;

    requestsBeforeUnsafeOwnership = gDebugToolsProbe.phaseControlRequestedCount;
    sPlayerActionLive = 1;
    CHECK(DebugToolsPhaseControl_RequestFactionMode(
              FACTION_RED, DEBUGTOOLS_PHASE_CONTROL_BLOCKED)
              == DEBUGTOOLS_PHASE_CONTROL_ERR_UNSAFE_BOUNDARY,
          "map-action ownership must reject without queueing a request");
    sPlayerActionLive = 0;

    sCameraLive = 1;
    CHECK(DebugToolsPhaseControl_RequestFactionMode(
              FACTION_RED, DEBUGTOOLS_PHASE_CONTROL_BLOCKED)
              == DEBUGTOOLS_PHASE_CONTROL_ERR_UNSAFE_BOUNDARY,
          "camera Proc ownership must reject without queueing a request");
    sCameraLive = 0;

    sFadeLive = 1;
    CHECK(DebugToolsPhaseControl_RequestTurn(6)
              == DEBUGTOOLS_PHASE_CONTROL_ERR_UNSAFE_BOUNDARY,
          "map fade ownership must reject without queueing a request");
    sFadeLive = 0;

    sBattleEventLive = 1;
    CHECK(DebugToolsPhaseControl_RequestTurn(6)
              == DEBUGTOOLS_PHASE_CONTROL_ERR_UNSAFE_BOUNDARY,
          "battle-event ownership must reject without queueing a request");
    sBattleEventLive = 0;

    sBattleLive = 1;
    CHECK(DebugToolsPhaseControl_RequestTurn(6)
              == DEBUGTOOLS_PHASE_CONTROL_ERR_UNSAFE_BOUNDARY,
          "active battle ownership must reject without queueing a request");
    sBattleLive = 0;
    CHECK(gDebugToolsProbe.phaseControlRequestedCount == requestsBeforeUnsafeOwnership
              && gDebugToolsProbe.phaseControlAppliedCount == 0
              && gDebugToolsProbe.phaseControlRestoredCount == 0,
          "unsafe ownership must not queue, apply, or restore a request");

    CHECK(DebugToolsPhaseControl_RequestFactionMode(
              FACTION_GREEN, DEBUGTOOLS_PHASE_CONTROL_BLOCKED)
              == DEBUGTOOLS_PHASE_CONTROL_OK,
          "cleared map-action and battle ownership must restore acceptance");
    CHECK(gDebugToolsProbe.phaseControlRequestedCount
              == requestsBeforeUnsafeOwnership + 1,
          "normal acceptance must record exactly one request after adversaries");
    CHECK(DebugToolsPhaseControl_RequestTurn(6)
              == DEBUGTOOLS_PHASE_CONTROL_ERR_PENDING,
          "a second request must fail while the original request is pending");
    CHECK(gDebugToolsProbe.phaseControlRequestedCount
              == requestsBeforeUnsafeOwnership + 1
              && gDebugToolsProbe.phaseControlLastRequestKind
                  == DEBUGTOOLS_PHASE_CONTROL_REQUEST_FACTION
              && gDebugToolsProbe.phaseControlLastFaction == FACTION_GREEN
              && gDebugToolsProbe.phaseControlLastMode
                  == DEBUGTOOLS_PHASE_CONTROL_BLOCKED,
          "pending rejection must preserve the original request fields");
    gPlaySt.faction = FACTION_GREEN;
    BmMain_StartPhase(&sMapMainProc);
    CHECK(gDebugToolsProbe.phaseControlAppliedCount == 1
              && gDebugToolsProbe.phaseControlRestoredCount == 1,
          "the original pending request must consume normally");

    gPlaySt.faction = FACTION_BLUE;
    CHECK(DebugToolsPhaseControl_RequestFactionMode(
              FACTION_RED, DEBUGTOOLS_PHASE_CONTROL_BLOCKED)
              == DEBUGTOOLS_PHASE_CONTROL_OK,
          "a second normal request must queue after the original consumes");
    CHECK(DebugToolsPhaseControl_RequestTurn(6)
              == DEBUGTOOLS_PHASE_CONTROL_ERR_PENDING,
          "pending request must remain unique before reset");
    CHECK(gDebugToolsProbe.phaseControlLastRequestKind
              == DEBUGTOOLS_PHASE_CONTROL_REQUEST_FACTION
              && gDebugToolsProbe.phaseControlLastFaction == FACTION_RED
              && gDebugToolsProbe.phaseControlLastMode
                  == DEBUGTOOLS_PHASE_CONTROL_BLOCKED,
          "reset must retain diagnostics for the original pending request");
    expiredBeforeCleanup = gDebugToolsProbe.phaseControlExpiredCount;
    DebugToolsPhaseControl_Reset();
    CHECK(gDebugToolsProbe.phaseControlExpiredCount == expiredBeforeCleanup + 1,
          "test cleanup must expire the accepted post-adversary request");

    CHECK(ExpansionAutoplay_SetBlueControl(EXPANSION_BLUE_CONTROL_COMPUTER)
              == EXPANSION_AUTOPLAY_OK,
          "the #85 controller must accept its own transient blue computer mode");
    ExpansionAutoplay_OnBlueComputerPhaseStart();
    CHECK(DebugToolsPhaseControl_RequestFactionMode(
              FACTION_RED, DEBUGTOOLS_PHASE_CONTROL_BLOCKED)
              == DEBUGTOOLS_PHASE_CONTROL_ERR_UNSAFE_BOUNDARY,
          "a live #85/#87-style blue computer phase must reject duplicate ownership");

    ExpansionAutoplay_Reset();
    ExpansionAutoplay_OnPlayerPhaseStart();
    CHECK(DebugToolsPhaseControl_RequestFactionMode(
              FACTION_GREEN, DEBUGTOOLS_PHASE_CONTROL_BLOCKED)
              == DEBUGTOOLS_PHASE_CONTROL_OK,
          "a valid request must queue before expiry testing");
    DebugToolsPhaseControl_Reset();
    CHECK(gDebugToolsProbe.phaseControlExpiredCount == expiredBeforeCleanup + 2
              && gDebugToolsProbe.phaseControlRestoredCount == 3
              && gDebugToolsProbe.phaseControlLastResult
                  == DEBUGTOOLS_PHASE_CONTROL_EXPIRED,
          "lifecycle reset must expire and restore a pending request");
    gPlaySt.faction = FACTION_GREEN;
    BmMain_StartPhase(&sMapMainProc);
    CHECK(sComputerPhaseStarts == 1,
          "an expired request must not affect the next green phase");

    return 0;
}

static int TestLockOwnership(void)
{
    u32 requestsBefore;
    u32 rejectedBefore;
    u32 appliedBefore;
    u32 restoredBefore;

    ResetHarness();
    CHECK(DebugToolsPhaseControl_RequestFactionMode(
              FACTION_RED, DEBUGTOOLS_PHASE_CONTROL_BLOCKED)
              == DEBUGTOOLS_PHASE_CONTROL_OK,
          "normal stable-map lock must accept a request");
    DebugToolsPhaseControl_Reset();

    requestsBefore = gDebugToolsProbe.phaseControlRequestedCount;
    rejectedBefore = gDebugToolsProbe.phaseControlRejectedCount;
    appliedBefore = gDebugToolsProbe.phaseControlAppliedCount;
    restoredBefore = gDebugToolsProbe.phaseControlRestoredCount;
    gBmSt.lock = 2;
    CHECK(DebugToolsPhaseControl_RequestFactionMode(
              FACTION_RED, DEBUGTOOLS_PHASE_CONTROL_BLOCKED)
              == DEBUGTOOLS_PHASE_CONTROL_ERR_UNSAFE_BOUNDARY,
          "orphan extra lock must reject a request");
    CHECK(gDebugToolsProbe.phaseControlRequestedCount == requestsBefore
              && gDebugToolsProbe.phaseControlAppliedCount == appliedBefore
              && gDebugToolsProbe.phaseControlRestoredCount == restoredBefore,
          "orphan extra lock must not queue, apply, or restore a request");
    CHECK(gDebugToolsProbe.phaseControlRejectedCount == rejectedBefore + 1
              && gDebugToolsProbe.phaseControlLastResult
                  == DEBUGTOOLS_PHASE_CONTROL_ERR_UNSAFE_BOUNDARY
              && gDebugToolsProbe.phaseControlTurnSample == gPlaySt.chapterTurnNumber
              && gDebugToolsProbe.phaseControlRedModeSample
                  == DEBUGTOOLS_PHASE_CONTROL_COMPUTER
              && gDebugToolsProbe.phaseControlGreenModeSample
                  == DEBUGTOOLS_PHASE_CONTROL_COMPUTER,
          "orphan extra lock must preserve diagnostic rejection telemetry");

    sDebugSessionActive = 1;
    CHECK(DebugToolsPhaseControl_RequestFactionMode(
              FACTION_GREEN, DEBUGTOOLS_PHASE_CONTROL_BLOCKED)
              == DEBUGTOOLS_PHASE_CONTROL_OK,
          "the one diagnostics-session lock must accept a request");
    DebugToolsPhaseControl_Reset();

    requestsBefore = gDebugToolsProbe.phaseControlRequestedCount;
    rejectedBefore = gDebugToolsProbe.phaseControlRejectedCount;
    appliedBefore = gDebugToolsProbe.phaseControlAppliedCount;
    restoredBefore = gDebugToolsProbe.phaseControlRestoredCount;
    gBmSt.lock = 3;
    CHECK(DebugToolsPhaseControl_RequestFactionMode(
              FACTION_GREEN, DEBUGTOOLS_PHASE_CONTROL_BLOCKED)
              == DEBUGTOOLS_PHASE_CONTROL_ERR_UNSAFE_BOUNDARY,
          "extra lock state must reject a request");
    CHECK(gDebugToolsProbe.phaseControlRequestedCount == requestsBefore
              && gDebugToolsProbe.phaseControlAppliedCount == appliedBefore
              && gDebugToolsProbe.phaseControlRestoredCount == restoredBefore,
          "extra lock state must not queue, apply, or restore a request");
    CHECK(gDebugToolsProbe.phaseControlRejectedCount == rejectedBefore + 1
              && gDebugToolsProbe.phaseControlLastResult
                  == DEBUGTOOLS_PHASE_CONTROL_ERR_UNSAFE_BOUNDARY
              && gDebugToolsProbe.phaseControlTurnSample == gPlaySt.chapterTurnNumber
              && gDebugToolsProbe.phaseControlRedModeSample
                  == DEBUGTOOLS_PHASE_CONTROL_COMPUTER
              && gDebugToolsProbe.phaseControlGreenModeSample
                  == DEBUGTOOLS_PHASE_CONTROL_COMPUTER,
          "extra lock state must preserve diagnostic rejection telemetry");

    sDebugSessionActive = 0;
    gBmSt.lock = 1;
    CHECK(DebugToolsPhaseControl_RequestFactionMode(
              FACTION_GREEN, DEBUGTOOLS_PHASE_CONTROL_BLOCKED)
              == DEBUGTOOLS_PHASE_CONTROL_OK,
          "normal acceptance must resume after lock release");
    DebugToolsPhaseControl_Reset();

    return 0;
}

static int TestForcedLifecycleCleanup(void)
{
    ResetHarness();
    CHECK(DebugToolsPhaseControl_RequestFactionMode(
              FACTION_RED, DEBUGTOOLS_PHASE_CONTROL_BLOCKED)
              == DEBUGTOOLS_PHASE_CONTROL_OK,
          "a valid request must queue before forced session cleanup");
    DebugTools_ForceSessionCleanup();
    CHECK(gDebugToolsProbe.phaseControlExpiredCount == 1
              && gDebugToolsProbe.phaseControlRestoredCount == 1
              && gDebugToolsProbe.phaseControlLastResult
                  == DEBUGTOOLS_PHASE_CONTROL_EXPIRED,
          "forced debugtools cleanup must expire and restore a pending request");
    gPlaySt.faction = FACTION_RED;
    BmMain_StartPhase((ProcPtr)&sMapMainProc);
    CHECK(sComputerPhaseStarts == 1,
          "forced session cleanup must leave the next red phase on its ordinary route");

    ResetHarness();
    CHECK(DebugToolsPhaseControl_RequestFactionMode(
              FACTION_GREEN, DEBUGTOOLS_PHASE_CONTROL_BLOCKED)
              == DEBUGTOOLS_PHASE_CONTROL_OK,
          "a valid request must queue before forced map teardown");
    EndBMapMain();
    CHECK(gDebugToolsProbe.phaseControlExpiredCount == 1
              && gDebugToolsProbe.phaseControlRestoredCount == 1
              && gDebugToolsProbe.phaseControlLastResult
                  == DEBUGTOOLS_PHASE_CONTROL_EXPIRED,
          "EndBMapMain must expire and restore a pending request");
    CHECK(sGameCtrl.proc_lockCnt == 0 && !sMapMainLive,
          "forced map teardown must complete the real map-main lifecycle");
    gPlaySt.faction = FACTION_GREEN;
    BmMain_StartPhase((ProcPtr)&sMapMainProc);
    CHECK(sComputerPhaseStarts == 1,
          "forced map teardown must leave the next green phase on its ordinary route");

    return 0;
}

int main(void)
{
    CHECK(TestTurnRequestAtBoundary() == 0, "turn boundary contract");
    CHECK(TestTransientTurnSuspendSerialization() == 0,
          "transient turn suspend serialization contract");
    CHECK(TestChapterStatsPersistentTurn() == 0,
          "chapter-stat persistence contract");
    CHECK(TestPlayerActionOwnership() == 0, "player action ownership contract");
    CHECK(TestFactionModesAndRestoration() == 0, "faction ownership contract");
    CHECK(TestRejectedAndExpiredRequests() == 0, "rejection and cleanup contract");
    CHECK(TestLockOwnership() == 0, "game lock ownership contract");
    CHECK(TestForcedLifecycleCleanup() == 0, "forced lifecycle cleanup contract");
    puts("DEBUGTOOLS_PHASE_CONTROL_HOST_TEST: PASS");
    return 0;
}
