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

#ifndef FE8_EXPANSION_AUTOPLAY_PLANNER_SCENARIO_ID
#define FE8_EXPANSION_AUTOPLAY_PLANNER_SCENARIO_ID 0x00009201u
#endif

#if !defined(FE8_ARCHIVAL_BUILD) && FE8_EXPANSION_MODERN_BUILD \
    && FE8_EXPANSION_AUTOPLAY_PLANNER
enum { ExpansionAutoplayPlannerScenarioIdMustBeIntegerConstant =
    FE8_EXPANSION_AUTOPLAY_PLANNER_SCENARIO_ID };
typedef char ExpansionAutoplayPlannerScenarioIdMustFitU32[
    FE8_EXPANSION_AUTOPLAY_PLANNER_SCENARIO_ID >= 0
    && (unsigned long long)FE8_EXPANSION_AUTOPLAY_PLANNER_SCENARIO_ID
        <= 0xFFFFFFFFULL ? 1 : -1];
#endif

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

enum ExpansionAutoplayPlannerAvailability
{
    EXPANSION_AUTOPLAY_PLANNER_AVAILABLE = 0,
    EXPANSION_AUTOPLAY_PLANNER_NOT_APPLICABLE = 1,
    EXPANSION_AUTOPLAY_PLANNER_NOT_VISIBLE = 2,
    EXPANSION_AUTOPLAY_PLANNER_UNSUPPORTED_RULE = 3,
    EXPANSION_AUTOPLAY_PLANNER_OUT_OF_RANGE = 4,
    EXPANSION_AUTOPLAY_PLANNER_UNINITIALIZED = 5,
    EXPANSION_AUTOPLAY_PLANNER_UNAVAILABLE = 6,
    EXPANSION_AUTOPLAY_PLANNER_EMPTY = 7,
};

enum ExpansionAutoplayPlannerSemanticFieldId
{
    EXPANSION_AUTOPLAY_PLANNER_FIELD_MAP_DIMENSIONS = 1,
    EXPANSION_AUTOPLAY_PLANNER_FIELD_MAP_STATE_DIGEST = 2,
    EXPANSION_AUTOPLAY_PLANNER_FIELD_ACTIVE_UNIT = 3,
    EXPANSION_AUTOPLAY_PLANNER_FIELD_ACTIVE_UNIT_STATE = 4,
    EXPANSION_AUTOPLAY_PLANNER_FIELD_OBJECTIVE_ID = 5,
    EXPANSION_AUTOPLAY_PLANNER_FIELD_OBJECTIVE_STATE = 6,
    EXPANSION_AUTOPLAY_PLANNER_FIELD_FLAGS_DIGEST = 7,
    EXPANSION_AUTOPLAY_PLANNER_FIELD_RESOURCE_DIGEST = 8,
};

enum ExpansionAutoplayPlannerPageKind
{
    EXPANSION_AUTOPLAY_PLANNER_PAGE_SUMMARY = 1,
    EXPANSION_AUTOPLAY_PLANNER_PAGE_MAP = 2,
    EXPANSION_AUTOPLAY_PLANNER_PAGE_UNITS = 3,
    EXPANSION_AUTOPLAY_PLANNER_PAGE_ACTIONS = 4,
    EXPANSION_AUTOPLAY_PLANNER_PAGE_INVENTORY = 5,
    EXPANSION_AUTOPLAY_PLANNER_PAGE_RESOURCES = 6,
    EXPANSION_AUTOPLAY_PLANNER_PAGE_FLAGS = 7,
};

enum ExpansionAutoplayPlannerValueKind
{
    EXPANSION_AUTOPLAY_PLANNER_VALUE_UNIT_ITEM = 1,
    EXPANSION_AUTOPLAY_PLANNER_VALUE_GOLD = 2,
    EXPANSION_AUTOPLAY_PLANNER_VALUE_CONVOY_ITEM = 3,
    EXPANSION_AUTOPLAY_PLANNER_VALUE_PERMANENT_FLAG = 4,
    EXPANSION_AUTOPLAY_PLANNER_VALUE_CHAPTER_FLAG = 5,
    EXPANSION_AUTOPLAY_PLANNER_VALUE_AUTOPLAY_TELEMETRY = 6,
};

enum ExpansionAutoplayPlannerAssignmentSource
{
    EXPANSION_AUTOPLAY_PLANNER_ASSIGNMENT_NONE = 0,
    EXPANSION_AUTOPLAY_PLANNER_ASSIGNMENT_CHAPTER = 1,
    EXPANSION_AUTOPLAY_PLANNER_ASSIGNMENT_GROUP = 2,
    EXPANSION_AUTOPLAY_PLANNER_ASSIGNMENT_UNIT = 3,
};

enum ExpansionAutoplayPlannerUnitSemanticFlag
{
    EXPANSION_AUTOPLAY_PLANNER_UNIT_DEPLOYED = (1 << 0),
    EXPANSION_AUTOPLAY_PLANNER_UNIT_DEAD = (1 << 1),
    EXPANSION_AUTOPLAY_PLANNER_UNIT_MOVED = (1 << 2),
    EXPANSION_AUTOPLAY_PLANNER_UNIT_ACTED = (1 << 3),
    EXPANSION_AUTOPLAY_PLANNER_UNIT_RESCUED = (1 << 4),
    EXPANSION_AUTOPLAY_PLANNER_UNIT_RESCUING = (1 << 5),
};

enum ExpansionAutoplayPlannerDecisionResult
{
    EXPANSION_AUTOPLAY_PLANNER_DECISION_FALLBACK = 0,
    EXPANSION_AUTOPLAY_PLANNER_DECISION_WAIT = 1,
    EXPANSION_AUTOPLAY_PLANNER_DECISION_ACCEPTED = 2,
    EXPANSION_AUTOPLAY_PLANNER_DECISION_CANCELLED = 3,
    EXPANSION_AUTOPLAY_PLANNER_DECISION_EXHAUSTED = 4,
};

enum ExpansionAutoplayPlannerEnumerationResult
{
    EXPANSION_AUTOPLAY_PLANNER_ENUMERATION_OK = 0,
    EXPANSION_AUTOPLAY_PLANNER_ENUMERATION_UNAVAILABLE = 1,
    EXPANSION_AUTOPLAY_PLANNER_ENUMERATION_CAPACITY = 2,
};

enum
{
    EXPANSION_AUTOPLAY_PLANNER_PAGE_MAX_BYTES = 1024,
    EXPANSION_AUTOPLAY_PLANNER_MAP_CELL_CAPACITY = 64 * 64,
    EXPANSION_AUTOPLAY_PLANNER_SEMANTIC_FIELD_CAPACITY = 8,
    EXPANSION_AUTOPLAY_PLANNER_MAP_RECORD_CAPACITY = 231,
    EXPANSION_AUTOPLAY_PLANNER_UNIT_RECORD_CAPACITY = 23,
    EXPANSION_AUTOPLAY_PLANNER_VALUE_RECORD_CAPACITY = 115,
    EXPANSION_AUTOPLAY_PLANNER_ACTION_CAPACITY = 23,
    EXPANSION_AUTOPLAY_PLANNER_TOTAL_ACTION_CAPACITY = 512,
    EXPANSION_AUTOPLAY_PLANNER_TRACE_ACTION_CAPACITY = 4096,
    EXPANSION_AUTOPLAY_PLANNER_DECISION_TIMEOUT_FRAMES = 300,
    EXPANSION_AUTOPLAY_PLANNER_OBJECTIVE_CAPACITY = 8,
    EXPANSION_AUTOPLAY_PLANNER_GROUP_CAPACITY = 8,
    EXPANSION_AUTOPLAY_PLANNER_GROUP_MEMBER_CAPACITY = 16,
    EXPANSION_AUTOPLAY_PLANNER_STRATEGY_CAPACITY = 8,
    EXPANSION_AUTOPLAY_PLANNER_ASSIGNMENT_CAPACITY = 17,
};

struct ExpansionAutoplayPlannerSemanticFieldV2
{
    u16 id;
    u8 availability;
    u8 valueSize;
    u32 value;
};

struct ExpansionAutoplayPlannerActionV2
{
    u32 kind;
    u32 actor;
    u32 destination;
    u32 target;
    u32 itemSlot;
    u32 token0;
    u32 token1;
    u32 token2;
    u32 token3;
    u32 actionId;
};

struct ExpansionAutoplayPlannerUnitV2
{
    u32 identity;
    u32 position;
    u32 state;
    u32 inventoryDigest;
    u32 status;
    u32 rescueAndEquipped;
    u32 stats0;
    u32 stats1;
    u32 ranks0;
    u32 ranks1;
};

struct ExpansionAutoplayPlannerValueRecordV2
{
    u32 identity;
    u32 value;
};

struct ExpansionAutoplayPlannerObjectiveV2
{
    u32 id;
    u32 completionObjectiveId;
    u32 groupId;
    u32 activationFlags;
    u32 completionFlags;
    u32 kind;
    u32 area;
    u32 status;
};

struct ExpansionAutoplayPlannerGroupV2
{
    u32 id;
    u32 identity;
    u32 members[4];
};

struct ExpansionAutoplayPlannerStrategyV2
{
    u32 id;
    u32 objectiveCapabilities;
    u32 actionCapabilities;
    u32 identity;
};

struct ExpansionAutoplayPlannerAssignmentV2
{
    u32 identity;
    u32 subjectId;
    u32 strategyId;
};

struct ExpansionAutoplayPlannerCampaignV2
{
    u32 availability;
    u32 chapter;
    u32 counts;
    u32 currentStrategyId;
    u32 currentObjectiveCapabilities;
    u32 currentActionCapabilities;
    u32 currentAssignment;
    u32 currentAssignmentSubject;
    struct ExpansionAutoplayPlannerObjectiveV2
        objectives[EXPANSION_AUTOPLAY_PLANNER_OBJECTIVE_CAPACITY];
    struct ExpansionAutoplayPlannerGroupV2
        groups[EXPANSION_AUTOPLAY_PLANNER_GROUP_CAPACITY];
    struct ExpansionAutoplayPlannerStrategyV2
        strategies[EXPANSION_AUTOPLAY_PLANNER_STRATEGY_CAPACITY];
    struct ExpansionAutoplayPlannerAssignmentV2
        assignments[EXPANSION_AUTOPLAY_PLANNER_ASSIGNMENT_CAPACITY];
};

struct ExpansionAutoplayPlannerSummaryV2
{
    struct ExpansionAutoplayPlannerSemanticFieldV2
        fields[EXPANSION_AUTOPLAY_PLANNER_SEMANTIC_FIELD_CAPACITY];
    struct ExpansionAutoplayPlannerCampaignV2 campaign;
};

union ExpansionAutoplayPlannerRecordStartV2
{
    u32 recordStart;
    u32 actionStartOrdinal;
};

union ExpansionAutoplayPlannerRecordCountV2
{
    u32 recordCount;
    u32 actionCount;
};

union ExpansionAutoplayPlannerPayloadV2
{
    struct ExpansionAutoplayPlannerSummaryV2 summary;
    u32 mapCells[EXPANSION_AUTOPLAY_PLANNER_MAP_RECORD_CAPACITY];
    struct ExpansionAutoplayPlannerUnitV2
        units[EXPANSION_AUTOPLAY_PLANNER_UNIT_RECORD_CAPACITY];
    struct ExpansionAutoplayPlannerValueRecordV2
        inventory[EXPANSION_AUTOPLAY_PLANNER_VALUE_RECORD_CAPACITY];
    struct ExpansionAutoplayPlannerValueRecordV2
        resources[EXPANSION_AUTOPLAY_PLANNER_VALUE_RECORD_CAPACITY];
    struct ExpansionAutoplayPlannerValueRecordV2
        flags[EXPANSION_AUTOPLAY_PLANNER_VALUE_RECORD_CAPACITY];
    struct ExpansionAutoplayPlannerActionV2
        actions[EXPANSION_AUTOPLAY_PLANNER_ACTION_CAPACITY];
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
    u32 pageKind;
    union ExpansionAutoplayPlannerRecordStartV2 start;
    union ExpansionAutoplayPlannerRecordCountV2 count;
    u32 totalRecordCount;
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
    union ExpansionAutoplayPlannerPayloadV2 payload;
};

struct ExpansionAutoplayPlannerStartCommandV2
{
    u32 expectedRomIdentity;
    u32 expectedConfigIdentity;
    u32 expectedScenarioIdentity;
    u32 expectedSeedIdentity;
    u32 reserved0;
    u32 reserved1;
};

struct ExpansionAutoplayPlannerCommitCommandV2
{
    u32 token0;
    u32 token1;
    u32 token2;
    u32 token3;
    u32 reserved0;
    u32 reserved1;
};

union ExpansionAutoplayPlannerCommandPayloadV2
{
    struct ExpansionAutoplayPlannerStartCommandV2 start;
    struct ExpansionAutoplayPlannerCommitCommandV2 commit;
    u32 words[6];
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
    union ExpansionAutoplayPlannerCommandPayloadV2 payload;
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
    u32 chapterMode;
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

typedef bool (*ExpansionAutoplayPlannerActionVisitor)(
    u32 ordinal,
    const struct AiDecision* decision,
    void* context);

void ExpansionAutoplayPlanner_Reset(void);
void ExpansionAutoplayPlanner_OnMapReset(void);
void ExpansionAutoplayPlanner_OnMapReady(void);
bool ExpansionAutoplayPlanner_PollStart(void);
bool ExpansionAutoplayPlanner_IsActive(void);
enum ExpansionAutoplayPlannerEnumerationResult
ExpansionAutoplayPlanner_EnumerateLegalActions(
    ExpansionAutoplayPlannerActionVisitor visitor,
    void* context,
    u32* countOut);
bool ExpansionAutoplayPlanner_PrepareActionData(
    const struct AiDecision* decision);
/* The decision parameter remains for source compatibility; enumeration is authoritative. */
enum ExpansionAutoplayPlannerDecisionResult ExpansionAutoplayPlanner_OfferDecision(
    const struct AiDecision* decision);
enum ExpansionAutoplayPlannerDecisionResult ExpansionAutoplayPlanner_PollDecision(
    struct AiDecision* decision);
void ExpansionAutoplayPlanner_RecordCampaignCheckpoint(void);

#endif /* GUARD_EXPANSION_AUTOPLAY_PLANNER_H */
