#include "global.h"

#include "bmunit.h"
#if FE8_AUTOPLAY_EVENT_TRACE_TEST
#include "event.h"
#include "eventinfo.h"
#endif
#include "cp_common.h"

#include "constants/event-flags.h"

#include "expansion_autoplay_internal.h"
#include "expansion_autoplay_strategies.h"

typedef char ExpansionAutoplayTelemetrySizeCheck[
    sizeof(struct ExpansionAutoplayTelemetry) == EXPANSION_AUTOPLAY_TELEMETRY_SIZE ? 1 : -1];

#if FE8_EXPANSION_HQ_MIXER
#define EXPANSION_AUTOPLAY_IWRAM_DATA \
    __attribute__((section("iwram_data.expansion_autoplay_hq")))
#else
#define EXPANSION_AUTOPLAY_IWRAM_DATA IWRAM_DATA
#endif

static u32 EXPANSION_AUTOPLAY_IWRAM_DATA sExpansionBlueControl =
    EXPANSION_BLUE_CONTROL_PLAYER;
struct ExpansionAutoplayTelemetry EXPANSION_AUTOPLAY_IWRAM_DATA
    gExpansionAutoplayTelemetry = { 0 };
#if FE8_AUTOPLAY_EVENT_TRACE_TEST
EWRAM_DATA struct ExpansionAutoplayEventTrace gExpansionAutoplayEventTrace = { 0 };

struct ExpansionAutoplayEventTracePrevious
{
    bool initialized;
    u32 slotC;
    u32 eventCounter;
    u32 objectiveFlags;
    u32 gameOverFlag;
};

EWRAM_DATA static struct ExpansionAutoplayEventTracePrevious
    sExpansionAutoplayEventTracePrevious = { 0 };
#endif

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
#if FE8_AUTOPLAY_EVENT_TRACE_TEST
    u8* eventTraceByte = (u8*)&gExpansionAutoplayEventTrace;
    u8* eventTracePreviousByte = (u8*)&sExpansionAutoplayEventTracePrevious;
#endif
    int i;

    for (i = 0; i < (int)sizeof(gExpansionAutoplayTelemetry); i++)
        byte[i] = 0;
#if FE8_AUTOPLAY_EVENT_TRACE_TEST
    for (i = 0; i < (int)sizeof(gExpansionAutoplayEventTrace); i++)
        eventTraceByte[i] = 0;
    for (i = 0; i < (int)sizeof(sExpansionAutoplayEventTracePrevious); i++)
        eventTracePreviousByte[i] = 0;
#endif

    sExpansionBlueControl = EXPANSION_BLUE_CONTROL_PLAYER;
    gExpansionAutoplayTelemetry.controller = EXPANSION_BLUE_CONTROL_PLAYER;
    ExpansionAutoplayStrategies_ResetPendingActivation();
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
    return gPlaySt.faction == FACTION_BLUE
        && sExpansionBlueControl == EXPANSION_BLUE_CONTROL_COMPUTER
        && (gExpansionAutoplayTelemetry.state == EXPANSION_AUTOPLAY_STATE_COMPUTER_PHASE
            || gExpansionAutoplayTelemetry.state == EXPANSION_AUTOPLAY_STATE_FAILURE);
}

#if FE8_EXPANSION_BLUE_PHASE_DELEGATE
bool ExpansionAutoplay_TryRestorePlayerControlAfterPhase(void)
{
    if (sExpansionBlueControl == EXPANSION_BLUE_CONTROL_PLAYER)
        return true;

    if (gPlaySt.faction == FACTION_BLUE && Proc_Find(gProcScr_CpPhase))
        return false;

    sExpansionBlueControl = EXPANSION_BLUE_CONTROL_PLAYER;
    gExpansionAutoplayTelemetry.controller = EXPANSION_BLUE_CONTROL_PLAYER;
    return true;
}
#endif

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
    {
        ExpansionAutoplayStrategies_ResetPendingActivation();
        return;
    }

    if (!ExpansionAutoplay_IsBlueComputerPhase())
    {
        ExpansionAutoplayStrategies_ResetPendingActivation();
        if (gExpansionAutoplayTelemetry.state != EXPANSION_AUTOPLAY_STATE_FAILURE)
            SetFailure(EXPANSION_AUTOPLAY_FAILURE_INVALID_PHASE);
        return;
    }

    IncrementBounded(&gExpansionAutoplayTelemetry.bluePhaseCompleteCount);
    gExpansionAutoplayTelemetry.state = EXPANSION_AUTOPLAY_STATE_COMPUTER_PHASE_COMPLETE;
    ExpansionAutoplayStrategies_ApplyPendingActivation();
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

void ExpansionAutoplay_RecordStrategyFailure(int result)
{
    if (!ExpansionAutoplay_IsBlueComputerPhase())
        return;

    IncrementBounded(&gExpansionAutoplayTelemetry.invalidRecordCount);
    if (result == EXPANSION_AUTOPLAY_STRATEGY_ERR_UNSUPPORTED_OBJECTIVE)
        SetFailure(EXPANSION_AUTOPLAY_FAILURE_STRATEGY_OBJECTIVE);
    else if (result == EXPANSION_AUTOPLAY_STRATEGY_ERR_PROFILE_DISABLED)
        SetFailure(EXPANSION_AUTOPLAY_FAILURE_STRATEGY_PROFILE_DISABLED);
    else
        SetFailure(EXPANSION_AUTOPLAY_FAILURE_STRATEGY_REGISTRY);
}

#if FE8_AUTOPLAY_EVENT_TRACE_TEST
void ExpansionAutoplay_RecordEventCommand(u8 command)
{
    struct ExpansionAutoplayEventTraceEntry* entry;
    u32 slotC;
    u32 eventCounter;
    u32 objectiveFlags;
    u32 gameOverFlag;

    if (gExpansionAutoplayTelemetry.controller != EXPANSION_BLUE_CONTROL_COMPUTER)
        return;

    slotC = gEventSlots[EVT_SLOT_C];
    eventCounter = gEventSlotCounter;
    objectiveFlags = (CheckFlag(EVFLAG_WIN) != 0)
        | ((CheckFlag(EVFLAG_DEFEAT_ALL) != 0) << 1);
    gameOverFlag = CheckFlag(EVFLAG_GAMEOVER) != 0;

    if (sExpansionAutoplayEventTracePrevious.initialized
        && sExpansionAutoplayEventTracePrevious.slotC == slotC
        && sExpansionAutoplayEventTracePrevious.eventCounter == eventCounter
        && sExpansionAutoplayEventTracePrevious.objectiveFlags == objectiveFlags
        && sExpansionAutoplayEventTracePrevious.gameOverFlag == gameOverFlag)
        return;

    sExpansionAutoplayEventTracePrevious.initialized = TRUE;
    sExpansionAutoplayEventTracePrevious.slotC = slotC;
    sExpansionAutoplayEventTracePrevious.eventCounter = eventCounter;
    sExpansionAutoplayEventTracePrevious.objectiveFlags = objectiveFlags;
    sExpansionAutoplayEventTracePrevious.gameOverFlag = gameOverFlag;

    if (gExpansionAutoplayEventTrace.count
        >= EXPANSION_AUTOPLAY_EVENT_TRACE_CAPACITY)
    {
        gExpansionAutoplayEventTrace.overflow = TRUE;
        return;
    }

    entry = &gExpansionAutoplayEventTrace.entries[gExpansionAutoplayEventTrace.count];
    entry->command = command;
    entry->slotC = slotC;
    entry->eventCounter = eventCounter;
    entry->objectiveFlags = objectiveFlags;
    entry->gameOverFlag = gameOverFlag;
    gExpansionAutoplayEventTrace.count++;
}
#endif
