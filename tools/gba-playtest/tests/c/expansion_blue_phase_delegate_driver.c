#include "global.h"

#include <stdio.h>
#include <string.h>

#include "bm.h"
#include "bmunit.h"
#include "cp_common.h"
#include "expansion_autoplay.h"
#include "expansion_autoplay_internal.h"
#include "expansion_blue_phase_delegate.h"
#include "expansion_blue_phase_delegate_internal.h"
#include "playerphase.h"

#define CHECK(condition, message) \
    do \
    { \
        if (!(condition)) \
        { \
            fprintf(stderr, "BLUE_PHASE_DELEGATE_HOST_TEST: FAIL: %s\n", message); \
            return 1; \
        } \
    } while (0)

struct PlaySt gPlaySt;

void ExpansionAutoplayStrategies_ResetPendingActivation(void)
{
}

void ExpansionAutoplayStrategies_ApplyPendingActivation(void)
{
}
u8 gGenericBuffer[0x2000];

struct ProcCmd CONST_DATA gProc_BMapMain[] = { { 0 } };
struct ProcCmd gProcScr_PlayerPhase[] = { { 0 } };
struct ProcCmd gProcScr_Playerphase_0[] = { { 0 } };
struct ProcCmd CONST_DATA gProcScr_CpPhase[] = { { 0 } };
struct ProcCmd CONST_DATA gProcScr_BerserkCpPhase[] = { { 0 } };
struct ProcCmd CONST_DATA ProcScr_CamMove[] = { { 0 } };

static struct Unit sUnits[EXPANSION_AUTOPLAY_BLUE_ACTOR_CAPACITY + 1];
static struct CharacterData sCharacters[EXPANSION_AUTOPLAY_BLUE_ACTOR_CAPACITY + 1];
static struct Proc sMapMainProc;
static struct Proc sPlayerPhaseProc;
static struct Proc sBlockingProc;
static struct Proc sMarkerProc;
static const struct ProcCmd* sMarkerScript;
static int sMapMainLive;
static int sPlayerPhaseLive;
static int sPlayerBlockingLive;
static int sComputerPhaseLive;
static int sBerserkPhaseLive;
static int sCameraLive;
static int sMarkerLive;
static int sEventLive;
static int sFadeLive;
static int sGameLock;
static int sMapMainLabel;
static int sPlayerPhaseLabel;

struct Unit* GetUnit(int id)
{
    if (id < 0 || id > EXPANSION_AUTOPLAY_BLUE_ACTOR_CAPACITY)
        return NULL;

    return &sUnits[id];
}

ProcPtr Proc_Find(const struct ProcCmd* script)
{
    if (script == gProc_BMapMain)
        return sMapMainLive ? &sMapMainProc : NULL;
    if (script == gProcScr_PlayerPhase)
        return sPlayerPhaseLive ? &sPlayerPhaseProc : NULL;
    if (script == gProcScr_Playerphase_0)
        return sPlayerBlockingLive ? &sBlockingProc : NULL;
    if (script == gProcScr_CpPhase)
        return sComputerPhaseLive ? &sBlockingProc : NULL;
    if (script == gProcScr_BerserkCpPhase)
        return sBerserkPhaseLive ? &sBlockingProc : NULL;
    if (script == ProcScr_CamMove)
        return sCameraLive ? &sBlockingProc : NULL;
    if (script == sMarkerScript)
        return sMarkerLive ? &sMarkerProc : NULL;

    return NULL;
}

ProcPtr Proc_Start(const struct ProcCmd* script, ProcPtr parent)
{
    (void)parent;

    sMarkerScript = script;
    sMarkerLive = 1;
    return &sMarkerProc;
}

void Proc_End(ProcPtr proc)
{
    if (proc == &sMarkerProc)
        sMarkerLive = 0;
}

void Proc_Goto(ProcPtr proc, int label)
{
    if (proc == &sMapMainProc)
        sMapMainLabel = label;
    else if (proc == &sPlayerPhaseProc)
        sPlayerPhaseLabel = label;
}

u8 GetGameLock(void)
{
    return sGameLock;
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
    memset(sUnits, 0, sizeof(sUnits));
    memset(sCharacters, 0, sizeof(sCharacters));
    memset(&gPlaySt, 0, sizeof(gPlaySt));
    memset(&sMapMainProc, 0, sizeof(sMapMainProc));
    memset(&sPlayerPhaseProc, 0, sizeof(sPlayerPhaseProc));
    memset(&sBlockingProc, 0, sizeof(sBlockingProc));
    memset(&sMarkerProc, 0, sizeof(sMarkerProc));
    sMarkerScript = NULL;
    sMapMainLive = 1;
    sPlayerPhaseLive = 1;
    sPlayerBlockingLive = 0;
    sComputerPhaseLive = 0;
    sBerserkPhaseLive = 0;
    sCameraLive = 0;
    sMarkerLive = 0;
    sEventLive = 0;
    sFadeLive = 0;
    sGameLock = 1;
    sMapMainLabel = -1;
    sPlayerPhaseLabel = -1;
    gPlaySt.faction = FACTION_BLUE;
    ExpansionAutoplay_Reset();
    ExpansionAutoplay_OnPlayerPhaseStart();
}

static void AddUnit(int slot)
{
    sUnits[slot].pCharacterData = &sCharacters[slot];
}

static int TestEligibilityAndInvalidStates(void)
{
    ResetHarness();
    AddUnit(1);
    AddUnit(2);
    AddUnit(3);
    AddUnit(4);
    AddUnit(5);
    AddUnit(6);
    AddUnit(7);
    sUnits[2].state = US_UNSELECTABLE;
    sUnits[3].state = US_RESCUED;
    sUnits[4].state = US_DEAD;
    sUnits[5].statusIndex = UNIT_STATUS_SLEEP;
    sUnits[6].statusIndex = UNIT_STATUS_BERSERK;

    CHECK(ExpansionBluePhaseDelegate_CountEligibleBlueUnits() == 2,
          "only the two remaining ordinary blue units may be delegated");
    CHECK(ExpansionBluePhaseDelegate_GetAvailability()
              == EXPANSION_BLUE_PHASE_DELEGATE_OK,
          "valid interactive blue map menu must expose Charge");

    gPlaySt.faction = FACTION_RED;
    CHECK(ExpansionBluePhaseDelegate_GetAvailability()
              == EXPANSION_BLUE_PHASE_DELEGATE_ERR_WRONG_PHASE,
          "non-blue phase must be rejected");
    gPlaySt.faction = FACTION_BLUE;

    sGameLock = 0;
    CHECK(ExpansionBluePhaseDelegate_GetAvailability()
              == EXPANSION_BLUE_PHASE_DELEGATE_ERR_LOCKED,
          "missing map-menu lock must be rejected");
    sGameLock = 2;
    CHECK(ExpansionBluePhaseDelegate_GetAvailability()
              == EXPANSION_BLUE_PHASE_DELEGATE_ERR_LOCKED,
          "another game lock must be rejected");
    sGameLock = 1;

    sEventLive = 1;
    CHECK(ExpansionBluePhaseDelegate_GetAvailability()
              == EXPANSION_BLUE_PHASE_DELEGATE_ERR_LOCKED,
          "event engine must hide Charge");
    sEventLive = 0;
    sFadeLive = 1;
    CHECK(ExpansionBluePhaseDelegate_GetAvailability()
              == EXPANSION_BLUE_PHASE_DELEGATE_ERR_LOCKED,
          "map fade must hide Charge");
    sFadeLive = 0;
    sCameraLive = 1;
    CHECK(ExpansionBluePhaseDelegate_GetAvailability()
              == EXPANSION_BLUE_PHASE_DELEGATE_ERR_LOCKED,
          "camera action must hide Charge");
    sCameraLive = 0;

    sPlayerBlockingLive = 1;
    CHECK(ExpansionBluePhaseDelegate_GetAvailability()
              == EXPANSION_BLUE_PHASE_DELEGATE_ERR_BUSY,
          "blocking player action must hide Charge");
    sPlayerBlockingLive = 0;
    sComputerPhaseLive = 1;
    CHECK(ExpansionBluePhaseDelegate_GetAvailability()
              == EXPANSION_BLUE_PHASE_DELEGATE_ERR_BUSY,
          "computer phase must hide Charge");
    sComputerPhaseLive = 0;

    memset(sUnits, 0, sizeof(sUnits));
    CHECK(ExpansionBluePhaseDelegate_GetAvailability()
              == EXPANSION_BLUE_PHASE_DELEGATE_ERR_NO_ELIGIBLE_UNIT,
          "empty eligible roster must hide Charge");

    return 0;
}

static int TestCurrentPhaseDelegationAndRestore(void)
{
    const struct ExpansionAutoplayTelemetry* telemetry;

    ResetHarness();
    AddUnit(1);
    AddUnit(2);
    AddUnit(3);
    sUnits[1].state = US_UNSELECTABLE;

    CHECK(ExpansionBluePhaseDelegate_Start()
              == EXPANSION_BLUE_PHASE_DELEGATE_OK,
          "valid Charge selection must start");
    CHECK(ExpansionAutoplay_GetBlueControl()
              == EXPANSION_BLUE_CONTROL_COMPUTER,
          "Charge must use the validated #85 controller");
    CHECK(ExpansionBluePhaseDelegate_IsPending(),
          "one transient marker must own restoration");
    CHECK(sMapMainLabel == 5 && sPlayerPhaseLabel == 3,
          "Charge must re-enter the existing current-phase router");

    ExpansionAutoplay_OnBlueComputerPhaseStart();
    ExpansionAutoplay_RecordEligibleActors(FACTION_BLUE, 2);
    ExpansionAutoplay_RecordCommittedAction(
        FACTION_BLUE,
        2,
        AI_ACTION_NONE,
        0,
        EXPANSION_AUTOPLAY_TARGET_NONE);
    sComputerPhaseLive = 1;
    ExpansionAutoplay_OnBlueComputerPhaseComplete();
    ExpansionBluePhaseDelegate_Monitor(&sMarkerProc);
    CHECK(ExpansionAutoplay_GetBlueControl()
              == EXPANSION_BLUE_CONTROL_COMPUTER,
          "control must not change while the computer proc is live");
    CHECK(ExpansionBluePhaseDelegate_IsPending(),
          "marker must wait for computer-proc cleanup");

    sComputerPhaseLive = 0;
    ExpansionBluePhaseDelegate_Monitor(&sMarkerProc);
    telemetry = ExpansionAutoplay_GetTelemetry();
    CHECK(ExpansionAutoplay_GetBlueControl()
              == EXPANSION_BLUE_CONTROL_PLAYER,
          "control must restore immediately after the delegated phase");
    CHECK(!ExpansionBluePhaseDelegate_IsPending(),
          "one-shot marker must end after restoration");
    CHECK(telemetry->controller == EXPANSION_BLUE_CONTROL_PLAYER,
          "telemetry controller must report restored PLAYER");
    CHECK(telemetry->bluePhaseStartCount == 1
              && telemetry->bluePhaseCompleteCount == 1
              && telemetry->eligibleActorCount == 2
              && telemetry->committedActionCount == 1,
          "restoration must preserve semantic phase/action telemetry");

    ExpansionAutoplay_OnPlayerPhaseStart();
    CHECK(telemetry->state == EXPANSION_AUTOPLAY_STATE_PLAYER_PHASE,
          "the next blue phase must be an ordinary player phase");

    return 0;
}

static int TestFailureStillRestoresWithoutErasingTelemetry(void)
{
    ResetHarness();
    AddUnit(1);
    CHECK(ExpansionBluePhaseDelegate_Start()
              == EXPANSION_BLUE_PHASE_DELEGATE_OK,
          "failure fixture must start delegation");
    ExpansionAutoplay_OnBlueComputerPhaseStart();
    CHECK(!ExpansionAutoplay_IsActionSupported(AI_ACTION_ESCAPE),
          "blue escape fixture must enter explicit failure");
    sComputerPhaseLive = 0;
    ExpansionBluePhaseDelegate_Monitor(&sMarkerProc);
    CHECK(ExpansionAutoplay_GetBlueControl()
              == EXPANSION_BLUE_CONTROL_PLAYER,
          "failure cleanup must still restore PLAYER");
    CHECK(gExpansionAutoplayTelemetry.state
              == EXPANSION_AUTOPLAY_STATE_FAILURE
              && gExpansionAutoplayTelemetry.failure
                  == EXPANSION_AUTOPLAY_FAILURE_UNSUPPORTED_ESCAPE,
          "restoration must preserve the parent failure evidence");

    return 0;
}

int main(void)
{
    CHECK(TestEligibilityAndInvalidStates() == 0, "eligibility/invalid states");
    CHECK(TestCurrentPhaseDelegationAndRestore() == 0,
          "current-phase delegation lifecycle");
    CHECK(TestFailureStillRestoresWithoutErasingTelemetry() == 0,
          "failure restoration");
    puts("BLUE_PHASE_DELEGATE_HOST_TEST: PASS");
    return 0;
}
