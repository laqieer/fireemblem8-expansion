#ifndef GUARD_EXPANSION_AUTOPLAY_PLANNER_H
#define GUARD_EXPANSION_AUTOPLAY_PLANNER_H

#include "global.h"

#include "cp_common.h"

/*
 * Version 2 is a bounded, pointer-free paged protocol. The host may read only
 * these three exported records and may write only PlannerCommandV2.
 */
#define EXPANSION_AUTOPLAY_PLANNER_MAGIC 0x41504C4Eu
#define EXPANSION_AUTOPLAY_PLANNER_PROTOCOL_VERSION 2
#define EXPANSION_AUTOPLAY_PLANNER_SCENARIO_ID 0x00009201u

enum ExpansionAutoplayPlannerCommandKind
{
    EXPANSION_AUTOPLAY_PLANNER_COMMAND_NONE = 0,
    EXPANSION_AUTOPLAY_PLANNER_COMMAND_START = 1,
    EXPANSION_AUTOPLAY_PLANNER_COMMAND_COMMIT = 2,
    EXPANSION_AUTOPLAY_PLANNER_COMMAND_CANCEL = 3,
    EXPANSION_AUTOPLAY_PLANNER_COMMAND_PAGE = 4,
};

enum ExpansionAutoplayPlannerState
{
    EXPANSION_AUTOPLAY_PLANNER_STATE_DISABLED = 0,
    EXPANSION_AUTOPLAY_PLANNER_STATE_READY = 1,
    EXPANSION_AUTOPLAY_PLANNER_STATE_WAITING = 2,
    EXPANSION_AUTOPLAY_PLANNER_STATE_COMMITTED = 3,
    EXPANSION_AUTOPLAY_PLANNER_STATE_CANCELLED = 4,
    EXPANSION_AUTOPLAY_PLANNER_STATE_EXHAUSTED = 5,
};

enum ExpansionAutoplayPlannerRejection
{
    EXPANSION_AUTOPLAY_PLANNER_REJECTION_NONE = 0,
    EXPANSION_AUTOPLAY_PLANNER_REJECTION_NOT_READY = 1,
    EXPANSION_AUTOPLAY_PLANNER_REJECTION_STALE_OBSERVATION = 2,
    EXPANSION_AUTOPLAY_PLANNER_REJECTION_UNKNOWN_ACTION = 3,
    EXPANSION_AUTOPLAY_PLANNER_REJECTION_TOKEN_MISMATCH = 4,
    EXPANSION_AUTOPLAY_PLANNER_REJECTION_CAPABILITY_UNAVAILABLE = 5,
    EXPANSION_AUTOPLAY_PLANNER_REJECTION_ACTION_BECAME_ILLEGAL = 6,
    EXPANSION_AUTOPLAY_PLANNER_REJECTION_RESOURCE_LIMIT = 7,
    EXPANSION_AUTOPLAY_PLANNER_REJECTION_CANCELLED = 8,
    EXPANSION_AUTOPLAY_PLANNER_REJECTION_PROTOCOL_ERROR = 9,
    EXPANSION_AUTOPLAY_PLANNER_REJECTION_TIMEOUT = 10,
};

enum ExpansionAutoplayPlannerActionKind
{
    EXPANSION_AUTOPLAY_PLANNER_ACTION_MOVE_WAIT = 1,
    EXPANSION_AUTOPLAY_PLANNER_ACTION_COMBAT = 2,
    EXPANSION_AUTOPLAY_PLANNER_ACTION_STAFF = 3,
    EXPANSION_AUTOPLAY_PLANNER_ACTION_USE_ITEM = 4,
    EXPANSION_AUTOPLAY_PLANNER_ACTION_PICK = 5,
    EXPANSION_AUTOPLAY_PLANNER_ACTION_SUMMON = 6,
};

enum ExpansionAutoplayPlannerDecisionResult
{
    EXPANSION_AUTOPLAY_PLANNER_DECISION_FALLBACK = 0,
    EXPANSION_AUTOPLAY_PLANNER_DECISION_WAIT = 1,
    EXPANSION_AUTOPLAY_PLANNER_DECISION_ACCEPTED = 2,
    EXPANSION_AUTOPLAY_PLANNER_DECISION_CANCELLED = 3,
    EXPANSION_AUTOPLAY_PLANNER_DECISION_EXHAUSTED = 4,
};

enum
{
    EXPANSION_AUTOPLAY_PLANNER_PAGE_MAX_BYTES = 1024,
    EXPANSION_AUTOPLAY_PLANNER_ACTION_CAPACITY = 29,
    EXPANSION_AUTOPLAY_PLANNER_TOTAL_ACTION_CAPACITY = 512,
    EXPANSION_AUTOPLAY_PLANNER_DECISION_TIMEOUT_FRAMES = 300,
};

struct ExpansionAutoplayPlannerActionV2
{
    u32 kind;
    u32 actor;
    u32 destination;
    u32 target;
    u32 itemSlot;
    u32 tokenLo;
    u32 tokenHi;
    u32 actionId;
};

struct ExpansionAutoplayPlannerObservationV2
{
    u32 magic;
    u32 version;
    u32 byteSize;
    u32 runId;
    u32 observationId;
    u32 state;
    u32 pageIndex;
    u32 pageCount;
    u32 actionStartOrdinal;
    u32 actionCount;
    u32 totalActionCount;
    u32 rejection;
    u32 chapterIndex;
    u32 chapterTurn;
    u32 rngState0;
    u32 rngState1;
    u32 rngState2;
    u32 rngLcg;
    u32 rngConsumption;
    u32 actualRomIdentity;
    u32 actualConfigIdentity;
    u32 actualScenarioIdentity;
    u32 actualSeedIdentity;
    struct ExpansionAutoplayPlannerActionV2 actions[EXPANSION_AUTOPLAY_PLANNER_ACTION_CAPACITY];
};

struct ExpansionAutoplayPlannerCommandV2
{
    u32 magic;
    u32 version;
    u32 byteSize;
    u32 kind;
    u32 runId;
    u32 observationId;
    u32 pageIndex;
    u32 actionOrdinal;
    u32 tokenLo;
    u32 tokenHi;
    u32 expectedRomIdentity;
    u32 expectedConfigIdentity;
    u32 expectedScenarioIdentity;
    u32 expectedSeedIdentity;
    u32 result;
    u32 rejection;
};

struct ExpansionAutoplayPlannerCampaignCheckpointV2
{
    u32 magic;
    u32 version;
    u32 byteSize;
    u32 runId;
    u32 chapterIndex;
    u32 chapterTurn;
    u32 rngState0;
    u32 rngState1;
    u32 rngState2;
    u32 rngLcg;
    u32 rngConsumption;
    u32 semanticStateDigest;
};

extern struct ExpansionAutoplayPlannerObservationV2 gExpansionAutoplayPlannerObservation;
extern volatile struct ExpansionAutoplayPlannerCommandV2 gExpansionAutoplayPlannerCommand;
extern struct ExpansionAutoplayPlannerCampaignCheckpointV2
    gExpansionAutoplayPlannerCampaignCheckpoint;

void ExpansionAutoplayPlanner_Reset(void);
void ExpansionAutoplayPlanner_OnMapReset(void);
bool ExpansionAutoplayPlanner_PollStart(void);
bool ExpansionAutoplayPlanner_IsActive(void);
enum ExpansionAutoplayPlannerDecisionResult ExpansionAutoplayPlanner_OfferDecision(
    const struct AiDecision* decision);
enum ExpansionAutoplayPlannerDecisionResult ExpansionAutoplayPlanner_PollDecision(
    struct AiDecision* decision);
void ExpansionAutoplayPlanner_RecordCampaignCheckpoint(void);

#endif /* GUARD_EXPANSION_AUTOPLAY_PLANNER_H */
