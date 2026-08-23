#include "global.h"

#include "bmunit.h"
#include "cp_common.h"

#include "expansion_autoplay_internal.h"

typedef char ExpansionAutoplayTelemetrySizeCheck[
    sizeof(struct ExpansionAutoplayTelemetry) == EXPANSION_AUTOPLAY_TELEMETRY_SIZE ? 1 : -1];

static u32 IWRAM_DATA sExpansionBlueControl = EXPANSION_BLUE_CONTROL_PLAYER;
struct ExpansionAutoplayTelemetry IWRAM_DATA gExpansionAutoplayTelemetry = { 0 };

static void IncrementBounded(u32* value)
{
    if (*value != 0xFFFFFFFFu)
        (*value)++;
}

static void SetFailure(enum ExpansionAutoplayFailure failure)
{
    gExpansionAutoplayTelemetry.state = EXPANSION_AUTOPLAY_STATE_FAILURE;
    gExpansionAutoplayTelemetry.failure = failure;
}

void ExpansionAutoplay_Reset(void)
{
    u8* byte = (u8*)&gExpansionAutoplayTelemetry;
    int i;

    for (i = 0; i < (int)sizeof(gExpansionAutoplayTelemetry); i++)
        byte[i] = 0;

    sExpansionBlueControl = EXPANSION_BLUE_CONTROL_PLAYER;
    gExpansionAutoplayTelemetry.controller = EXPANSION_BLUE_CONTROL_PLAYER;
}

enum ExpansionAutoplayResult ExpansionAutoplay_SetBlueControl(enum ExpansionBlueControl control)
{
    switch (control)
    {
    case EXPANSION_BLUE_CONTROL_PLAYER:
    case EXPANSION_BLUE_CONTROL_COMPUTER:
        break;

    default:
        IncrementBounded(&gExpansionAutoplayTelemetry.invalidRecordCount);
        SetFailure(EXPANSION_AUTOPLAY_FAILURE_INVALID_CONTROL);
        return EXPANSION_AUTOPLAY_ERR_INVALID_CONTROL;
    }

    if (ExpansionAutoplay_IsBlueComputerPhase())
    {
        if (control == sExpansionBlueControl)
            return EXPANSION_AUTOPLAY_OK;

        IncrementBounded(&gExpansionAutoplayTelemetry.invalidRecordCount);
        SetFailure(EXPANSION_AUTOPLAY_FAILURE_INVALID_PHASE);
        return EXPANSION_AUTOPLAY_ERR_PHASE_ACTIVE;
    }

    sExpansionBlueControl = control;
    gExpansionAutoplayTelemetry.controller = control;
    gExpansionAutoplayTelemetry.failure = EXPANSION_AUTOPLAY_FAILURE_NONE;
    if (gExpansionAutoplayTelemetry.state == EXPANSION_AUTOPLAY_STATE_FAILURE)
        gExpansionAutoplayTelemetry.state = EXPANSION_AUTOPLAY_STATE_RESET;
    return EXPANSION_AUTOPLAY_OK;
}

enum ExpansionBlueControl ExpansionAutoplay_GetBlueControl(void)
{
    return sExpansionBlueControl;
}

const struct ExpansionAutoplayTelemetry* ExpansionAutoplay_GetTelemetry(void)
{
    return &gExpansionAutoplayTelemetry;
}

bool ExpansionAutoplay_TryActivateScenario(u16 newKeys, u16 heldKeys)
{
#if FE8_EXPANSION_DEBUG
    if (heldKeys != EXPANSION_AUTOPLAY_SCENARIO_HOTKEY_MASK
        || !(newKeys & EXPANSION_AUTOPLAY_SCENARIO_HOTKEY_MASK))
        return false;

    if (ExpansionAutoplay_SetBlueControl(EXPANSION_BLUE_CONTROL_COMPUTER)
        != EXPANSION_AUTOPLAY_OK)
        return false;

    IncrementBounded(&gExpansionAutoplayTelemetry.debugActivationCount);
    return true;
#else
    (void)newKeys;
    (void)heldKeys;
    return false;
#endif
}

bool ExpansionAutoplay_IsBlueComputerPhase(void)
{
    return sExpansionBlueControl == EXPANSION_BLUE_CONTROL_COMPUTER
        && (gExpansionAutoplayTelemetry.state == EXPANSION_AUTOPLAY_STATE_COMPUTER_PHASE
            || gExpansionAutoplayTelemetry.state == EXPANSION_AUTOPLAY_STATE_FAILURE);
}

bool ExpansionAutoplay_IsActionSupported(u8 actionId)
{
    switch (actionId)
    {
    case AI_ACTION_NONE:
    case AI_ACTION_COMBAT:
    case AI_ACTION_STEAL:
    case AI_ACTION_PILLAGE:
    case AI_ACTION_STAFF:
    case AI_ACTION_USEITEM:
    case AI_ACTION_REFRESH:
    case AI_ACTION_TALK:
    case AI_ACTION_RIDEBALLISTA:
    case AI_ACTION_EXITBALLISTA:
    case AI_ACTION_DKNIGHTMARE:
    case AI_ACTION_DKSUMMON:
    case AI_ACTION_PICK:
        return true;

    case AI_ACTION_ESCAPE:
        if (ExpansionAutoplay_IsBlueComputerPhase())
            SetFailure(EXPANSION_AUTOPLAY_FAILURE_UNSUPPORTED_ESCAPE);
        return false;

    default:
        if (ExpansionAutoplay_IsBlueComputerPhase())
            SetFailure(EXPANSION_AUTOPLAY_FAILURE_UNSUPPORTED_ACTION);
        return false;
    }
}

void ExpansionAutoplay_OnPlayerPhaseStart(void)
{
    if (gExpansionAutoplayTelemetry.state != EXPANSION_AUTOPLAY_STATE_FAILURE)
        gExpansionAutoplayTelemetry.state = EXPANSION_AUTOPLAY_STATE_PLAYER_PHASE;
}

void ExpansionAutoplay_OnBlueComputerPhaseStart(void)
{
    if (sExpansionBlueControl != EXPANSION_BLUE_CONTROL_COMPUTER)
    {
        SetFailure(EXPANSION_AUTOPLAY_FAILURE_INVALID_PHASE);
        return;
    }

    IncrementBounded(&gExpansionAutoplayTelemetry.bluePhaseStartCount);
    gExpansionAutoplayTelemetry.eligibleActorCount = 0;
    if (gExpansionAutoplayTelemetry.state != EXPANSION_AUTOPLAY_STATE_FAILURE)
        gExpansionAutoplayTelemetry.state = EXPANSION_AUTOPLAY_STATE_COMPUTER_PHASE;
}

void ExpansionAutoplay_OnBlueComputerPhaseComplete(void)
{
    if (gExpansionAutoplayTelemetry.state == EXPANSION_AUTOPLAY_STATE_FAILURE)
        return;

    if (!ExpansionAutoplay_IsBlueComputerPhase())
    {
        if (gExpansionAutoplayTelemetry.state != EXPANSION_AUTOPLAY_STATE_FAILURE)
            SetFailure(EXPANSION_AUTOPLAY_FAILURE_INVALID_PHASE);
        return;
    }

    IncrementBounded(&gExpansionAutoplayTelemetry.bluePhaseCompleteCount);
    gExpansionAutoplayTelemetry.state = EXPANSION_AUTOPLAY_STATE_COMPUTER_PHASE_COMPLETE;
}

void ExpansionAutoplay_RecordEligibleActors(int side, int count)
{
    if (gExpansionAutoplayTelemetry.state != EXPANSION_AUTOPLAY_STATE_COMPUTER_PHASE
        || side != FACTION_BLUE)
        return;

    if (count < 0 || count > EXPANSION_AUTOPLAY_BLUE_ACTOR_CAPACITY)
    {
        SetFailure(EXPANSION_AUTOPLAY_FAILURE_ROSTER_CAPACITY);
        return;
    }

    gExpansionAutoplayTelemetry.eligibleActorCount = count;
}

void ExpansionAutoplay_RecordCommittedAction(
    int side,
    u8 actorSlot,
    u8 actionId,
    u8 targetSlot,
    enum ExpansionAutoplayTargetRelation relation)
{
    if (gExpansionAutoplayTelemetry.state != EXPANSION_AUTOPLAY_STATE_COMPUTER_PHASE
        || side != FACTION_BLUE)
        return;

    if ((actorSlot & 0xC0) != FACTION_BLUE || actorSlot == 0)
    {
        SetFailure(EXPANSION_AUTOPLAY_FAILURE_INVALID_ACTOR);
        return;
    }

    if (!ExpansionAutoplay_IsActionSupported(actionId))
        return;

    IncrementBounded(&gExpansionAutoplayTelemetry.committedActionCount);
    gExpansionAutoplayTelemetry.lastActorSlot = actorSlot;
    gExpansionAutoplayTelemetry.lastActionId = actionId;
    gExpansionAutoplayTelemetry.lastTargetSlot = targetSlot;
    gExpansionAutoplayTelemetry.lastTargetRelation = relation;
}

void ExpansionAutoplay_RecordRelationCheck(int leftSlot, int rightSlot, bool allied)
{
    int rightFaction;

    if (gExpansionAutoplayTelemetry.state != EXPANSION_AUTOPLAY_STATE_COMPUTER_PHASE)
        return;

    if ((leftSlot & 0xC0) != FACTION_BLUE)
        return;

    rightFaction = rightSlot & 0xC0;

    if (rightFaction == FACTION_RED)
    {
        if (allied)
        {
            IncrementBounded(&gExpansionAutoplayTelemetry.invalidRecordCount);
            SetFailure(EXPANSION_AUTOPLAY_FAILURE_ALLIANCE_SEMANTICS);
        }
        else
        {
            IncrementBounded(&gExpansionAutoplayTelemetry.hostileTargetCheckCount);
        }
    }
    else if (rightFaction == FACTION_GREEN)
    {
        if (allied)
        {
            IncrementBounded(&gExpansionAutoplayTelemetry.alliedTargetCheckCount);
        }
        else
        {
            IncrementBounded(&gExpansionAutoplayTelemetry.invalidRecordCount);
            SetFailure(EXPANSION_AUTOPLAY_FAILURE_ALLIANCE_SEMANTICS);
        }
    }
}

void ExpansionAutoplay_RecordUnsupportedEscape(void)
{
    if (ExpansionAutoplay_IsBlueComputerPhase())
        SetFailure(EXPANSION_AUTOPLAY_FAILURE_UNSUPPORTED_ESCAPE);
}

void ExpansionAutoplay_RecordSuspendSuppressed(void)
{
    if (ExpansionAutoplay_IsBlueComputerPhase())
        IncrementBounded(&gExpansionAutoplayTelemetry.suspendWriteSuppressedCount);
}
