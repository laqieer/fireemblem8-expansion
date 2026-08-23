#include "global.h"

#include "bm.h"
#include "bmunit.h"
#include "cp_common.h"
#include "rng.h"

#include "expansion_autoplay.h"
#include "expansion_autoplay_planner.h"

typedef char ExpansionAutoplayPlannerObservationSizeCheck[
    sizeof(struct ExpansionAutoplayPlannerObservationV1)
        <= EXPANSION_AUTOPLAY_PLANNER_PAGE_MAX_BYTES ? 1 : -1];
typedef char ExpansionAutoplayPlannerCommandSizeCheck[
    sizeof(struct ExpansionAutoplayPlannerCommandV1) == 44 ? 1 : -1];
typedef char ExpansionAutoplayPlannerPointerFreeActionCheck[
    sizeof(struct ExpansionAutoplayPlannerActionV1) == 32 ? 1 : -1];

#if FE8_EXPANSION_AUTOPLAY_PLANNER && FE8_EXPANSION_DEBUG

struct ExpansionAutoplayPlannerObservationV1 EWRAM_DATA
    gExpansionAutoplayPlannerObservation = { 0 };
volatile struct ExpansionAutoplayPlannerCommandV1 EWRAM_DATA
    gExpansionAutoplayPlannerCommand = { 0 };
struct ExpansionAutoplayPlannerCampaignCheckpointV1 EWRAM_DATA
    gExpansionAutoplayPlannerCampaignCheckpoint = { 0 };

static bool sPlannerActive;
static u32 sPlannerRunId;
static u32 sPlannerNextObservationId;
static u32 sPlannerTraceDigest;

static void ClearCommand(void)
{
    gExpansionAutoplayPlannerCommand.kind = EXPANSION_AUTOPLAY_PLANNER_COMMAND_NONE;
}

static u32 MixDigest(u32 digest, u32 value)
{
    return (digest ^ value) * 16777619u;
}

static u32 MakeTokenLo(const struct AiDecision* decision, u32 observationId)
{
    u32 digest = 2166136261u;

    digest = MixDigest(digest, sPlannerRunId);
    digest = MixDigest(digest, observationId);
    digest = MixDigest(digest, decision->unitId);
    digest = MixDigest(digest, (u32)(u16)decision->xMove | ((u32)(u16)decision->yMove << 16));
    digest = MixDigest(digest, decision->actionId);
    digest = MixDigest(digest, decision->targetId | ((u32)decision->itemSlot << 8));
    digest = MixDigest(digest, decision->xTarget | ((u32)decision->yTarget << 8));
    return digest;
}

static u32 MakeTokenHi(u32 tokenLo)
{
    return MixDigest(tokenLo, 0x92A11A9Fu);
}

static enum ExpansionAutoplayPlannerActionKind ActionKindFromAiAction(u8 actionId)
{
    switch (actionId)
    {
    case AI_ACTION_NONE:
        return EXPANSION_AUTOPLAY_PLANNER_ACTION_MOVE_WAIT;

    case AI_ACTION_COMBAT:
        return EXPANSION_AUTOPLAY_PLANNER_ACTION_COMBAT;

    case AI_ACTION_STAFF:
        return EXPANSION_AUTOPLAY_PLANNER_ACTION_STAFF;

    case AI_ACTION_USEITEM:
        return EXPANSION_AUTOPLAY_PLANNER_ACTION_USE_ITEM;

    case AI_ACTION_PICK:
        return EXPANSION_AUTOPLAY_PLANNER_ACTION_PICK;

    case AI_ACTION_DKSUMMON:
        return EXPANSION_AUTOPLAY_PLANNER_ACTION_SUMMON;

    default:
        return 0;
    }
}

static bool IsCommandHeaderValid(void)
{
    return gExpansionAutoplayPlannerCommand.magic == EXPANSION_AUTOPLAY_PLANNER_MAGIC
        && gExpansionAutoplayPlannerCommand.version
            == EXPANSION_AUTOPLAY_PLANNER_PROTOCOL_VERSION
        && gExpansionAutoplayPlannerCommand.byteSize
            == sizeof(struct ExpansionAutoplayPlannerCommandV1);
}

static void Reject(enum ExpansionAutoplayPlannerRejection rejection)
{
    gExpansionAutoplayPlannerObservation.rejection = rejection;
    gExpansionAutoplayPlannerCommand.result = 0;
    gExpansionAutoplayPlannerCommand.rejection = rejection;
    ClearCommand();
}

static void PublishObservation(const struct AiDecision* decision)
{
    struct ExpansionAutoplayPlannerActionV1* action =
        &gExpansionAutoplayPlannerObservation.actions[0];
    u16 seeds[3];
    u32 tokenLo;

    StoreRNState(seeds);
    tokenLo = MakeTokenLo(decision, sPlannerNextObservationId);

    gExpansionAutoplayPlannerObservation.magic = EXPANSION_AUTOPLAY_PLANNER_MAGIC;
    gExpansionAutoplayPlannerObservation.version = EXPANSION_AUTOPLAY_PLANNER_PROTOCOL_VERSION;
    gExpansionAutoplayPlannerObservation.byteSize =
        sizeof(struct ExpansionAutoplayPlannerObservationV1);
    gExpansionAutoplayPlannerObservation.runId = sPlannerRunId;
    gExpansionAutoplayPlannerObservation.observationId = sPlannerNextObservationId++;
    gExpansionAutoplayPlannerObservation.state = EXPANSION_AUTOPLAY_PLANNER_STATE_WAITING;
    gExpansionAutoplayPlannerObservation.pageIndex = 0;
    gExpansionAutoplayPlannerObservation.pageCount = 1;
    gExpansionAutoplayPlannerObservation.actionCount = 1;
    gExpansionAutoplayPlannerObservation.rejection = EXPANSION_AUTOPLAY_PLANNER_REJECTION_NONE;
    gExpansionAutoplayPlannerObservation.chapterIndex = (u8)gPlaySt.chapterIndex;
    gExpansionAutoplayPlannerObservation.chapterTurn = gPlaySt.chapterTurnNumber;
    gExpansionAutoplayPlannerObservation.rngState0 = seeds[0];
    gExpansionAutoplayPlannerObservation.rngState1 = seeds[1];
    gExpansionAutoplayPlannerObservation.rngState2 = seeds[2];
    gExpansionAutoplayPlannerObservation.rngLcg = GetLCGRNValue();
    gExpansionAutoplayPlannerObservation.rngConsumption = GetRNConsumptionCount();

    action->kind = ActionKindFromAiAction(decision->actionId);
    action->actor = decision->unitId;
    action->destination = (u16)decision->xMove | ((u32)(u16)decision->yMove << 16);
    action->target = decision->targetId | ((u32)decision->xTarget << 8)
        | ((u32)decision->yTarget << 16);
    action->itemSlot = decision->itemSlot;
    action->tokenLo = tokenLo;
    action->tokenHi = MakeTokenHi(tokenLo);
    action->actionId = decision->actionId;
}

void ExpansionAutoplayPlanner_Reset(void)
{
    u8* bytes;
    int index;

    bytes = (u8*)&gExpansionAutoplayPlannerObservation;
    for (index = 0; index < (int)sizeof(gExpansionAutoplayPlannerObservation); index++)
        bytes[index] = 0;
    bytes = (u8*)&gExpansionAutoplayPlannerCampaignCheckpoint;
    for (index = 0; index < (int)sizeof(gExpansionAutoplayPlannerCampaignCheckpoint); index++)
        bytes[index] = 0;
    bytes = (u8*)&gExpansionAutoplayPlannerCommand;
    for (index = 0; index < (int)sizeof(gExpansionAutoplayPlannerCommand); index++)
        bytes[index] = 0;

    sPlannerActive = false;
    sPlannerTraceDigest = 2166136261u;
}

bool ExpansionAutoplayPlanner_PollStart(void)
{
    if (sPlannerActive || gExpansionAutoplayPlannerCommand.kind
        != EXPANSION_AUTOPLAY_PLANNER_COMMAND_START)
        return false;

    if (!IsCommandHeaderValid())
    {
        Reject(EXPANSION_AUTOPLAY_PLANNER_REJECTION_PROTOCOL_ERROR);
        return false;
    }

    if (ExpansionAutoplay_SetBlueControl(EXPANSION_BLUE_CONTROL_COMPUTER)
        != EXPANSION_AUTOPLAY_OK)
    {
        Reject(EXPANSION_AUTOPLAY_PLANNER_REJECTION_NOT_READY);
        return false;
    }

    sPlannerActive = true;
    sPlannerRunId++;
    sPlannerNextObservationId = 1;
    sPlannerTraceDigest = MixDigest(2166136261u, sPlannerRunId);
    gExpansionAutoplayPlannerCommand.result = 1;
    gExpansionAutoplayPlannerCommand.rejection = EXPANSION_AUTOPLAY_PLANNER_REJECTION_NONE;
    ClearCommand();
    return true;
}

bool ExpansionAutoplayPlanner_IsActive(void)
{
    return sPlannerActive;
}

enum ExpansionAutoplayPlannerDecisionResult ExpansionAutoplayPlanner_OfferDecision(
    const struct AiDecision* decision)
{
    const struct ExpansionAutoplayPlannerActionV1* action;
    u32 tokenLo;

    if (!sPlannerActive)
        return EXPANSION_AUTOPLAY_PLANNER_DECISION_FALLBACK;

    if (decision == NULL || !decision->actionPerformed)
    {
        gExpansionAutoplayPlannerObservation.state = EXPANSION_AUTOPLAY_PLANNER_STATE_EXHAUSTED;
        gExpansionAutoplayPlannerObservation.rejection =
            EXPANSION_AUTOPLAY_PLANNER_REJECTION_CAPABILITY_UNAVAILABLE;
        return EXPANSION_AUTOPLAY_PLANNER_DECISION_EXHAUSTED;
    }

    if (ActionKindFromAiAction(decision->actionId) == 0)
    {
        gExpansionAutoplayPlannerObservation.state = EXPANSION_AUTOPLAY_PLANNER_STATE_EXHAUSTED;
        gExpansionAutoplayPlannerObservation.rejection =
            EXPANSION_AUTOPLAY_PLANNER_REJECTION_CAPABILITY_UNAVAILABLE;
        return EXPANSION_AUTOPLAY_PLANNER_DECISION_EXHAUSTED;
    }

    if (gExpansionAutoplayPlannerObservation.state
        != EXPANSION_AUTOPLAY_PLANNER_STATE_WAITING)
    {
        PublishObservation(decision);
        return EXPANSION_AUTOPLAY_PLANNER_DECISION_WAIT;
    }

    if (gExpansionAutoplayPlannerCommand.kind == EXPANSION_AUTOPLAY_PLANNER_COMMAND_CANCEL)
    {
        if (!IsCommandHeaderValid()
            || gExpansionAutoplayPlannerCommand.runId != sPlannerRunId
            || gExpansionAutoplayPlannerCommand.observationId
                != gExpansionAutoplayPlannerObservation.observationId)
        {
            Reject(EXPANSION_AUTOPLAY_PLANNER_REJECTION_STALE_OBSERVATION);
            return EXPANSION_AUTOPLAY_PLANNER_DECISION_WAIT;
        }

        gExpansionAutoplayPlannerObservation.state = EXPANSION_AUTOPLAY_PLANNER_STATE_CANCELLED;
        Reject(EXPANSION_AUTOPLAY_PLANNER_REJECTION_CANCELLED);
        sPlannerActive = false;
        return EXPANSION_AUTOPLAY_PLANNER_DECISION_CANCELLED;
    }

    if (gExpansionAutoplayPlannerCommand.kind != EXPANSION_AUTOPLAY_PLANNER_COMMAND_COMMIT)
        return EXPANSION_AUTOPLAY_PLANNER_DECISION_WAIT;

    if (!IsCommandHeaderValid())
    {
        Reject(EXPANSION_AUTOPLAY_PLANNER_REJECTION_PROTOCOL_ERROR);
        return EXPANSION_AUTOPLAY_PLANNER_DECISION_WAIT;
    }

    if (gExpansionAutoplayPlannerCommand.runId != sPlannerRunId
        || gExpansionAutoplayPlannerCommand.observationId
            != gExpansionAutoplayPlannerObservation.observationId)
    {
        Reject(EXPANSION_AUTOPLAY_PLANNER_REJECTION_STALE_OBSERVATION);
        return EXPANSION_AUTOPLAY_PLANNER_DECISION_WAIT;
    }

    if (gExpansionAutoplayPlannerCommand.actionOrdinal != 0)
    {
        Reject(EXPANSION_AUTOPLAY_PLANNER_REJECTION_UNKNOWN_ACTION);
        return EXPANSION_AUTOPLAY_PLANNER_DECISION_WAIT;
    }

    action = &gExpansionAutoplayPlannerObservation.actions[0];
    tokenLo = MakeTokenLo(decision, gExpansionAutoplayPlannerObservation.observationId);
    if (gExpansionAutoplayPlannerCommand.tokenLo != tokenLo
        || gExpansionAutoplayPlannerCommand.tokenHi != MakeTokenHi(tokenLo)
        || action->tokenLo != tokenLo)
    {
        Reject(EXPANSION_AUTOPLAY_PLANNER_REJECTION_TOKEN_MISMATCH);
        return EXPANSION_AUTOPLAY_PLANNER_DECISION_WAIT;
    }

    gExpansionAutoplayPlannerObservation.state = EXPANSION_AUTOPLAY_PLANNER_STATE_COMMITTED;
    gExpansionAutoplayPlannerObservation.rejection = EXPANSION_AUTOPLAY_PLANNER_REJECTION_NONE;
    gExpansionAutoplayPlannerCommand.result = 1;
    gExpansionAutoplayPlannerCommand.rejection = EXPANSION_AUTOPLAY_PLANNER_REJECTION_NONE;
    ClearCommand();
    sPlannerTraceDigest = MixDigest(sPlannerTraceDigest, tokenLo);
    return EXPANSION_AUTOPLAY_PLANNER_DECISION_ACCEPTED;
}

void ExpansionAutoplayPlanner_RecordCampaignCheckpoint(void)
{
    u16 seeds[3];

    if (!sPlannerActive)
        return;

    StoreRNState(seeds);
    gExpansionAutoplayPlannerCampaignCheckpoint.magic = EXPANSION_AUTOPLAY_PLANNER_MAGIC;
    gExpansionAutoplayPlannerCampaignCheckpoint.version =
        EXPANSION_AUTOPLAY_PLANNER_PROTOCOL_VERSION;
    gExpansionAutoplayPlannerCampaignCheckpoint.byteSize =
        sizeof(struct ExpansionAutoplayPlannerCampaignCheckpointV1);
    gExpansionAutoplayPlannerCampaignCheckpoint.runId = sPlannerRunId;
    gExpansionAutoplayPlannerCampaignCheckpoint.chapterIndex = (u8)gPlaySt.chapterIndex;
    gExpansionAutoplayPlannerCampaignCheckpoint.chapterTurn = gPlaySt.chapterTurnNumber;
    gExpansionAutoplayPlannerCampaignCheckpoint.rngState0 = seeds[0];
    gExpansionAutoplayPlannerCampaignCheckpoint.rngState1 = seeds[1];
    gExpansionAutoplayPlannerCampaignCheckpoint.rngState2 = seeds[2];
    gExpansionAutoplayPlannerCampaignCheckpoint.rngLcg = GetLCGRNValue();
    gExpansionAutoplayPlannerCampaignCheckpoint.rngConsumption = GetRNConsumptionCount();
    gExpansionAutoplayPlannerCampaignCheckpoint.traceDigest = sPlannerTraceDigest;
}

#endif
