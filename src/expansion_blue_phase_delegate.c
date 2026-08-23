#include "global.h"

#include "bm.h"
#include "bmunit.h"
#include "cp_common.h"
#include "event.h"
#include "playerphase.h"

#include "expansion_autoplay.h"
#include "expansion_autoplay_internal.h"
#include "expansion_blue_phase_delegate_internal.h"

#if FE8_EXPANSION_BLUE_PHASE_DELEGATE

enum
{
    BLUE_PHASE_DELEGATE_MAP_MAIN_START_PHASE_LABEL = 5,
    BLUE_PHASE_DELEGATE_PLAYER_PHASE_END_LABEL = 3,
};

static struct ProcCmd CONST_DATA sProcScr_ExpansionBluePhaseDelegate[] =
{
    PROC_NAME("E_BLUE_DELEGATE"),
    PROC_REPEAT(ExpansionBluePhaseDelegate_Monitor),
    PROC_END,
};

int ExpansionBluePhaseDelegate_CountEligibleBlueUnits(void)
{
    int count = 0;
    int slot;

    for (slot = 1; slot <= EXPANSION_AUTOPLAY_BLUE_ACTOR_CAPACITY; slot++)
    {
        if (IsUnitEligibleForAiPhase(GetUnit(slot)))
            count++;
    }

    return count;
}

bool ExpansionBluePhaseDelegate_IsPending(void)
{
    return Proc_Find(sProcScr_ExpansionBluePhaseDelegate) != NULL;
}

enum ExpansionBluePhaseDelegateResult ExpansionBluePhaseDelegate_GetAvailability(void)
{
    const struct ExpansionAutoplayTelemetry* telemetry;

    if (gPlaySt.faction != FACTION_BLUE)
        return EXPANSION_BLUE_PHASE_DELEGATE_ERR_WRONG_PHASE;

    telemetry = ExpansionAutoplay_GetTelemetry();
    if (ExpansionAutoplay_GetBlueControl() != EXPANSION_BLUE_CONTROL_PLAYER
        || telemetry->state != EXPANSION_AUTOPLAY_STATE_PLAYER_PHASE
        || telemetry->failure != EXPANSION_AUTOPLAY_FAILURE_NONE)
        return EXPANSION_BLUE_PHASE_DELEGATE_ERR_BUSY;

    if (GetGameLock() != 1 || EventEngineExists() || DoesBMXFADEExist()
        || Proc_Find(ProcScr_CamMove))
        return EXPANSION_BLUE_PHASE_DELEGATE_ERR_LOCKED;

    if (!Proc_Find(gProc_BMapMain) || !Proc_Find(gProcScr_PlayerPhase)
        || Proc_Find(gProcScr_Playerphase_0) || Proc_Find(gProcScr_CpPhase)
        || Proc_Find(gProcScr_BerserkCpPhase) || ExpansionBluePhaseDelegate_IsPending())
        return EXPANSION_BLUE_PHASE_DELEGATE_ERR_BUSY;

    if (ExpansionBluePhaseDelegate_CountEligibleBlueUnits() == 0)
        return EXPANSION_BLUE_PHASE_DELEGATE_ERR_NO_ELIGIBLE_UNIT;

    return EXPANSION_BLUE_PHASE_DELEGATE_OK;
}

enum ExpansionBluePhaseDelegateResult ExpansionBluePhaseDelegate_Start(void)
{
    struct Proc* mapMain;
    struct Proc* playerPhase;
    struct Proc* marker;
    enum ExpansionAutoplayResult controlResult;
    enum ExpansionBluePhaseDelegateResult availability;

    availability = ExpansionBluePhaseDelegate_GetAvailability();
    if (availability != EXPANSION_BLUE_PHASE_DELEGATE_OK)
        return availability;

    mapMain = Proc_Find(gProc_BMapMain);
    playerPhase = Proc_Find(gProcScr_PlayerPhase);
    marker = Proc_Start(sProcScr_ExpansionBluePhaseDelegate, PROC_TREE_3);
    if (!marker)
        return EXPANSION_BLUE_PHASE_DELEGATE_ERR_BUSY;

    controlResult =
        ExpansionAutoplay_SetBlueControl(EXPANSION_BLUE_CONTROL_COMPUTER);
    if (controlResult != EXPANSION_AUTOPLAY_OK)
    {
        Proc_End(marker);
        return EXPANSION_BLUE_PHASE_DELEGATE_ERR_CONTROL;
    }

    Proc_Goto(mapMain, BLUE_PHASE_DELEGATE_MAP_MAIN_START_PHASE_LABEL);
    Proc_Goto(playerPhase, BLUE_PHASE_DELEGATE_PLAYER_PHASE_END_LABEL);
    return EXPANSION_BLUE_PHASE_DELEGATE_OK;
}

void ExpansionBluePhaseDelegate_Monitor(ProcPtr proc)
{
    const struct ExpansionAutoplayTelemetry* telemetry =
        ExpansionAutoplay_GetTelemetry();

    if (telemetry->state != EXPANSION_AUTOPLAY_STATE_COMPUTER_PHASE_COMPLETE
        && telemetry->state != EXPANSION_AUTOPLAY_STATE_FAILURE)
        return;

    if (!ExpansionAutoplay_TryRestorePlayerControlAfterPhase())
        return;

    Proc_End(proc);
}

#endif /* FE8_EXPANSION_BLUE_PHASE_DELEGATE */
