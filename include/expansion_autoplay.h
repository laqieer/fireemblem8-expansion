#ifndef GUARD_EXPANSION_AUTOPLAY_H
#define GUARD_EXPANSION_AUTOPLAY_H

#include "global.h"

enum ExpansionBlueControl
{
    EXPANSION_BLUE_CONTROL_PLAYER = 0,
    EXPANSION_BLUE_CONTROL_COMPUTER = 1,
};

enum ExpansionAutoplayResult
{
    EXPANSION_AUTOPLAY_OK = 0,
    EXPANSION_AUTOPLAY_ERR_INVALID_CONTROL = 1,
    EXPANSION_AUTOPLAY_ERR_PHASE_ACTIVE = 2,
};

enum ExpansionAutoplayState
{
    EXPANSION_AUTOPLAY_STATE_RESET = 0,
    EXPANSION_AUTOPLAY_STATE_PLAYER_PHASE = 1,
    EXPANSION_AUTOPLAY_STATE_COMPUTER_PHASE = 2,
    EXPANSION_AUTOPLAY_STATE_COMPUTER_PHASE_COMPLETE = 3,
    EXPANSION_AUTOPLAY_STATE_FAILURE = 4,
};

enum ExpansionAutoplayFailure
{
    EXPANSION_AUTOPLAY_FAILURE_NONE = 0,
    EXPANSION_AUTOPLAY_FAILURE_INVALID_CONTROL = 1,
    EXPANSION_AUTOPLAY_FAILURE_INVALID_PHASE = 2,
    EXPANSION_AUTOPLAY_FAILURE_ROSTER_CAPACITY = 3,
    EXPANSION_AUTOPLAY_FAILURE_UNSUPPORTED_ESCAPE = 4,
    EXPANSION_AUTOPLAY_FAILURE_UNSUPPORTED_ACTION = 5,
    EXPANSION_AUTOPLAY_FAILURE_INVALID_ACTOR = 6,
    EXPANSION_AUTOPLAY_FAILURE_ALLIANCE_SEMANTICS = 7,
    EXPANSION_AUTOPLAY_FAILURE_STRATEGY_REGISTRY = 8,
    EXPANSION_AUTOPLAY_FAILURE_STRATEGY_OBJECTIVE = 9,
    EXPANSION_AUTOPLAY_FAILURE_STRATEGY_PROFILE_DISABLED = 10,
};

enum ExpansionAutoplayTargetRelation
{
    EXPANSION_AUTOPLAY_TARGET_NONE = 0,
    EXPANSION_AUTOPLAY_TARGET_ALLIED = 1,
    EXPANSION_AUTOPLAY_TARGET_HOSTILE = 2,
};

enum
{
    EXPANSION_AUTOPLAY_BLUE_ACTOR_CAPACITY = 62,
    EXPANSION_AUTOPLAY_TELEMETRY_SIZE = 64,
};

#if FE8_AUTOPLAY_EVENT_TRACE_TEST
enum
{
    EXPANSION_AUTOPLAY_EVENT_TRACE_CAPACITY = 64,
    EXPANSION_AUTOPLAY_EVENT_TRACE_ENTRY_WORDS = 5,
};
#endif

struct ExpansionAutoplayTelemetry
{
    u32 controller;
    u32 state;
    u32 failure;
    u32 bluePhaseStartCount;
    u32 bluePhaseCompleteCount;
    u32 eligibleActorCount;
    u32 committedActionCount;
    u32 lastActorSlot;
    u32 lastActionId;
    u32 lastTargetSlot;
    u32 lastTargetRelation;
    u32 hostileTargetCheckCount;
    u32 alliedTargetCheckCount;
    u32 invalidRecordCount;
    u32 debugActivationCount;
    u32 suspendWriteSuppressedCount;
};

extern struct ExpansionAutoplayTelemetry gExpansionAutoplayTelemetry;

#if FE8_AUTOPLAY_EVENT_TRACE_TEST
struct ExpansionAutoplayEventTraceEntry
{
    u32 command;
    u32 slotC;
    u32 eventCounter;
    u32 objectiveFlags;
    u32 gameOverFlag;
};

struct ExpansionAutoplayEventTrace
{
    u32 count;
    u32 overflow;
    struct ExpansionAutoplayEventTraceEntry entries[EXPANSION_AUTOPLAY_EVENT_TRACE_CAPACITY];
};

extern struct ExpansionAutoplayEventTrace gExpansionAutoplayEventTrace;
#endif

enum ExpansionAutoplayResult ExpansionAutoplay_SetBlueControl(enum ExpansionBlueControl control);
enum ExpansionBlueControl ExpansionAutoplay_GetBlueControl(void);
const struct ExpansionAutoplayTelemetry* ExpansionAutoplay_GetTelemetry(void);
void ExpansionAutoplay_Reset(void);

bool ExpansionAutoplay_IsActionSupported(u8 actionId);

#endif /* GUARD_EXPANSION_AUTOPLAY_H */
