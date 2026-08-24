#include "global.h"

#include <stdio.h>
#include <string.h>

#include "bm.h"
#include "bmio.h"
#include "cp_common.h"
#include "event.h"
#include "expansion_autoplay.h"
#include "expansion_debugtools.h"
#include "fontgrp.h"
#include "gamecontrol.h"
#include "hardware.h"
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

struct ProcCmd CONST_DATA gProc_BMapMain[] = { { 0 } };
struct ProcCmd gProcScr_PlayerPhase[] = { { 0 } };
struct ProcCmd gProcScr_Playerphase_0[] = { { 0 } };
struct ProcCmd CONST_DATA gProcScr_CpPhase[] = { { 0 } };
struct ProcCmd CONST_DATA gProcScr_BerserkCpPhase[] = { { 0 } };
struct ProcCmd CONST_DATA ProcScr_CamMove[] = { { 0 } };

struct LCDControlBuffer gLCDControlBuffer;

static struct BMapMainProc sMapMainProc;
static struct GameCtrlProc sGameCtrl;
static struct Proc sPlayerPhaseProc;
static struct Proc sBlockingProc;
static struct Font sFont;
static u16 sBgMap[32 * 32];
struct Font* gActiveFont = &sFont;
static int sMapMainLive;
static int sPlayerPhaseLive;
static int sPlayerActionLive;
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
static int sProcBreaks;
static int sProcGotoLabel;
static int sPhaseSwitchEventCount;
static int sPhaseSwitchEventFaction;
static int sPhaseSwitchEventTurn;

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

ProcPtr Proc_StartBlocking(const struct ProcCmd* script, ProcPtr parent)
{
    (void)parent;

    if (script == gProcScr_CpPhase)
        sComputerPhaseStarts++;
    else if (script == gProcScr_BerserkCpPhase && sBerserkEligible)
        sBerserkActionStarts++;

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
    sProcBreaks = 0;
    sProcGotoLabel = -1;
    sPhaseSwitchEventCount = 0;
    sPhaseSwitchEventFaction = -1;
    sPhaseSwitchEventTurn = -1;
    sMapMainProc.gameCtrl = &sGameCtrl;
    sGameCtrl.proc_lockCnt = 1;

    gPlaySt.faction = FACTION_BLUE;
    gPlaySt.chapterTurnNumber = 5;
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
    if (sProcGotoLabel != 3)
        Proc_StartBlocking(gProcScr_BerserkCpPhase, &sMapMainProc);
    CHECK(sComputerPhaseStarts == 0 && sBerserkActionStarts == 0
              && sProcGotoLabel == 3,
          "BLOCKED must bypass normal and eligible berserk computer actions");
    CHECK(gDebugToolsProbe.phaseControlAppliedCount == 1
              && gDebugToolsProbe.phaseControlRestoredCount == 1,
          "a blocked phase must restore ordinary ownership immediately");

    BmMain_StartPhase(&sMapMainProc);
    CHECK(sComputerPhaseStarts == 1,
          "the next red phase must restore the ordinary computer route");

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
          "active events must reject a request without mutation");
    sEventLive = 0;

    sFadeLive = 1;
    CHECK(DebugToolsPhaseControl_RequestTurn(6)
              == DEBUGTOOLS_PHASE_CONTROL_ERR_UNSAFE_BOUNDARY,
          "map fade ownership must reject a request without mutation");
    sFadeLive = 0;

    sBattleEventLive = 1;
    CHECK(DebugToolsPhaseControl_RequestTurn(6)
              == DEBUGTOOLS_PHASE_CONTROL_ERR_UNSAFE_BOUNDARY,
          "battle-event ownership must reject a request without mutation");
    sBattleEventLive = 0;

    sBattleLive = 1;
    CHECK(DebugToolsPhaseControl_RequestTurn(6)
              == DEBUGTOOLS_PHASE_CONTROL_ERR_UNSAFE_BOUNDARY,
          "active battle ownership must reject a request without mutation");
    sBattleLive = 0;

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
    CHECK(gDebugToolsProbe.phaseControlExpiredCount == 1
              && gDebugToolsProbe.phaseControlRestoredCount == 1
              && gDebugToolsProbe.phaseControlLastResult
                  == DEBUGTOOLS_PHASE_CONTROL_EXPIRED,
          "lifecycle reset must expire and restore a pending request");
    gPlaySt.faction = FACTION_GREEN;
    BmMain_StartPhase(&sMapMainProc);
    CHECK(sComputerPhaseStarts == 1,
          "an expired request must not affect the next green phase");

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
    CHECK(TestFactionModesAndRestoration() == 0, "faction ownership contract");
    CHECK(TestRejectedAndExpiredRequests() == 0, "rejection and cleanup contract");
    CHECK(TestForcedLifecycleCleanup() == 0, "forced lifecycle cleanup contract");
    puts("DEBUGTOOLS_PHASE_CONTROL_HOST_TEST: PASS");
    return 0;
}
