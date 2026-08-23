#include "global.h"

#include <stdio.h>
#include <string.h>

#include "bm.h"
#include "cp_common.h"
#include "event.h"
#include "expansion_autoplay.h"
#include "expansion_debugtools.h"
#include "playerphase.h"

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

struct ProcCmd CONST_DATA gProc_BMapMain[] = { { 0 } };
struct ProcCmd gProcScr_PlayerPhase[] = { { 0 } };
struct ProcCmd gProcScr_Playerphase_0[] = { { 0 } };
struct ProcCmd CONST_DATA gProcScr_CpPhase[] = { { 0 } };
struct ProcCmd CONST_DATA gProcScr_BerserkCpPhase[] = { { 0 } };
struct ProcCmd CONST_DATA ProcScr_CamMove[] = { { 0 } };

static struct Proc sMapMainProc;
static struct Proc sPlayerPhaseProc;
static struct Proc sBlockingProc;
static int sMapMainLive;
static int sPlayerPhaseLive;
static int sPlayerActionLive;
static int sComputerPhaseLive;
static int sBerserkPhaseLive;
static int sCameraLive;
static int sEventLive;
static int sFadeLive;
static int sComputerPhaseStarts;
static int sProcBreaks;

ProcPtr Proc_Find(const struct ProcCmd* script)
{
    if (script == gProc_BMapMain)
        return sMapMainLive ? &sMapMainProc : NULL;
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

    return &sBlockingProc;
}

void Proc_Break(ProcPtr proc)
{
    (void)proc;
    sProcBreaks++;
}

s8 EventEngineExists(void)
{
    return sEventLive;
}

bool8 DoesBMXFADEExist(void)
{
    return sFadeLive;
}

static void ResetHarness(void)
{
    memset(&gPlaySt, 0, sizeof(gPlaySt));
    memset(&gDebugToolsProbe, 0, sizeof(gDebugToolsProbe));
    memset(&sMapMainProc, 0, sizeof(sMapMainProc));
    memset(&sPlayerPhaseProc, 0, sizeof(sPlayerPhaseProc));
    memset(&sBlockingProc, 0, sizeof(sBlockingProc));
    sMapMainLive = 1;
    sPlayerPhaseLive = 1;
    sPlayerActionLive = 0;
    sComputerPhaseLive = 0;
    sBerserkPhaseLive = 0;
    sCameraLive = 0;
    sEventLive = 0;
    sFadeLive = 0;
    sComputerPhaseStarts = 0;
    sProcBreaks = 0;

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

    gPlaySt.faction = FACTION_RED;
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
    gPlaySt.faction = FACTION_RED;
    BmMain_StartPhase(&sMapMainProc);
    CHECK(sComputerPhaseStarts == 0 && sProcBreaks == 1,
          "BLOCKED must skip exactly the requested red computer phase");
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

int main(void)
{
    CHECK(TestTurnRequestAtBoundary() == 0, "turn boundary contract");
    CHECK(TestFactionModesAndRestoration() == 0, "faction ownership contract");
    CHECK(TestRejectedAndExpiredRequests() == 0, "rejection and cleanup contract");
    puts("DEBUGTOOLS_PHASE_CONTROL_HOST_TEST: PASS");
    return 0;
}
