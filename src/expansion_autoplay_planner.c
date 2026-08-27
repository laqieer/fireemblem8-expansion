#include "global.h"

#include "bm.h"
#include "bmmap.h"
#include "bmunit.h"
#include "cp_common.h"
#include "eventinfo.h"
#include "rng.h"

#include "expansion_config.h"
#include "expansion_autoplay.h"
#include "expansion_autoplay_internal.h"
#include "expansion_autoplay_planner.h"

typedef char ExpansionAutoplayPlannerObservationSizeCheck[
    sizeof(struct ExpansionAutoplayPlannerObservationV2)
        <= EXPANSION_AUTOPLAY_PLANNER_PAGE_MAX_BYTES ? 1 : -1];
typedef char ExpansionAutoplayPlannerCommandSizeCheck[
    sizeof(struct ExpansionAutoplayPlannerCommandV2) == 64 ? 1 : -1];
typedef char ExpansionAutoplayPlannerPointerFreeActionCheck[
    sizeof(struct ExpansionAutoplayPlannerActionV2) == 32 ? 1 : -1];

#if FE8_EXPANSION_AUTOPLAY_PLANNER && FE8_EXPANSION_DEBUG

struct ExpansionAutoplayPlannerObservationV2 EWRAM_DATA
    gExpansionAutoplayPlannerObservation = { 0 };
volatile struct ExpansionAutoplayPlannerCommandV2 EWRAM_DATA
    gExpansionAutoplayPlannerCommand = { 0 };
struct ExpansionAutoplayPlannerCampaignCheckpointV2 EWRAM_DATA
    gExpansionAutoplayPlannerCampaignCheckpoint = { 0 };
EWRAM_DATA static struct AiDecision sPlannerSelectedDecision;

static bool sPlannerActive;
static u32 sPlannerRunId;
static u32 sPlannerNextObservationId;
static u32 sPlannerTraceDigest;
static u32 sPlannerCandidateCount;
static bool sPlannerHasSelectedDecision;
static u32 sPlannerWaitFrames;
static u32 sPlannerLastTokenLo;
static u32 sPlannerLastTokenHi;

static void ClearCommand(void)
{
    gExpansionAutoplayPlannerCommand.kind = EXPANSION_AUTOPLAY_PLANNER_COMMAND_NONE;
}

static u32 MixDigest(u32 digest, u32 value)
{
    return (digest ^ value) * 16777619u;
}

static u32 HashText(const char* text)
{
    u32 digest = 2166136261u;

    while (*text != '\0')
        digest = MixDigest(digest, (u8)*text++);
    return digest;
}

static u32 ActualRomIdentity(void)
{
    u32 digest = HashText(FE8_EXPANSION_BUILD_COMMIT);
#if FE8_AUTOPLAY_PLANNER_RUNTIME_TEST
    return MixDigest(digest, 0x54455354u);
#else
    const volatile u8* header = (const volatile u8*)0x080000A0;
    int index;

    for (index = 0; index < 16; index++)
        digest = MixDigest(digest, header[index]);
    return digest;
#endif
}

static u32 ActualConfigIdentity(void)
{
    return HashText(FE8_EXPANSION_CONFIG_FINGERPRINT);
}

static u32 ActualSeedIdentity(void)
{
    u16 seeds[3];
    u32 digest = 2166136261u;

    StoreRNState(seeds);
    digest = MixDigest(digest, seeds[0]);
    digest = MixDigest(digest, seeds[1]);
    digest = MixDigest(digest, seeds[2]);
    return MixDigest(digest, GetLCGRNValue());
}

static u32 MakeTokenLo(
    const struct AiDecision* decision,
    u32 observationId,
    u32 ordinal)
{
    u32 digest = 2166136261u;

    digest = MixDigest(digest, sPlannerRunId);
    digest = MixDigest(digest, observationId);
    digest = MixDigest(digest, ordinal);
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
            == sizeof(struct ExpansionAutoplayPlannerCommandV2);
}

static void Reject(enum ExpansionAutoplayPlannerRejection rejection)
{
    gExpansionAutoplayPlannerObservation.rejection = rejection;
    gExpansionAutoplayPlannerCommand.result = 0;
    gExpansionAutoplayPlannerCommand.rejection = rejection;
    ClearCommand();
}

static void PublishReadyState(void)
{
    gExpansionAutoplayPlannerObservation.magic = EXPANSION_AUTOPLAY_PLANNER_MAGIC;
    gExpansionAutoplayPlannerObservation.version = EXPANSION_AUTOPLAY_PLANNER_PROTOCOL_VERSION;
    gExpansionAutoplayPlannerObservation.byteSize =
        sizeof(struct ExpansionAutoplayPlannerObservationV2);
    gExpansionAutoplayPlannerObservation.runId = sPlannerRunId;
    gExpansionAutoplayPlannerObservation.state = EXPANSION_AUTOPLAY_PLANNER_STATE_READY;
    gExpansionAutoplayPlannerObservation.actualRomIdentity = ActualRomIdentity();
    gExpansionAutoplayPlannerObservation.actualConfigIdentity = ActualConfigIdentity();
    gExpansionAutoplayPlannerObservation.actualScenarioIdentity =
        EXPANSION_AUTOPLAY_PLANNER_SCENARIO_ID;
    gExpansionAutoplayPlannerObservation.actualSeedIdentity = ActualSeedIdentity();
}

static void ClearPublishedActions(void)
{
    u8* bytes = (u8*)gExpansionAutoplayPlannerObservation.actions;
    int index;

    for (index = 0; index < (int)sizeof(gExpansionAutoplayPlannerObservation.actions); index++)
        bytes[index] = 0;
}

static bool DecisionsEqual(
    const struct AiDecision* left,
    const struct AiDecision* right)
{
    return left->unitId == right->unitId
        && left->xMove == right->xMove
        && left->yMove == right->yMove
        && left->actionId == right->actionId
        && left->targetId == right->targetId
        && left->itemSlot == right->itemSlot
        && left->xTarget == right->xTarget
        && left->yTarget == right->yTarget;
}

static bool IsLegalWaitDestination(int x, int y)
{
    return gBmMapMovement != NULL
        && gBmMapUnit != NULL
        && gBmMapMovement[y][x] <= MAP_MOVEMENT_MAX
        && (gBmMapUnit[y][x] == 0 || gBmMapUnit[y][x] == gActiveUnitId)
        && (gActiveUnit == NULL
            || x != gActiveUnit->xPos
            || y != gActiveUnit->yPos);
}

static void MakeWaitCandidate(int x, int y, struct AiDecision* candidate)
{
    *candidate = sPlannerSelectedDecision;
    candidate->actionPerformed = true;
    candidate->unitId = gActiveUnitId;
    candidate->xMove = x;
    candidate->yMove = y;
    candidate->actionId = AI_ACTION_NONE;
    candidate->targetId = 0;
    candidate->itemSlot = 0;
    candidate->xTarget = 0;
    candidate->yTarget = 0;
}

static bool GetCandidate(u32 ordinal, struct AiDecision* candidate)
{
    u32 current = sPlannerHasSelectedDecision ? 1 : 0;
    int x;
    int y;

    if (sPlannerHasSelectedDecision && ordinal == 0)
    {
        *candidate = sPlannerSelectedDecision;
        return true;
    }

    for (y = 0; y < gBmMapSize.y; y++)
    {
        for (x = 0; x < gBmMapSize.x; x++)
        {
            struct AiDecision wait;

            if (!IsLegalWaitDestination(x, y))
                continue;
            MakeWaitCandidate(x, y, &wait);
            if (DecisionsEqual(&wait, &sPlannerSelectedDecision))
                continue;
            if (current++ == ordinal)
            {
                *candidate = wait;
                return true;
            }
        }
    }
    return false;
}

static bool BuildCandidates(const struct AiDecision* decision)
{
    int x;
    int y;

    sPlannerSelectedDecision = *decision;
    sPlannerHasSelectedDecision = !(gActiveUnit != NULL
        && decision->actionId == AI_ACTION_NONE
        && decision->xMove == gActiveUnit->xPos
        && decision->yMove == gActiveUnit->yPos);
    sPlannerCandidateCount = sPlannerHasSelectedDecision ? 1 : 0;

    if (gBmMapMovement == NULL || gBmMapUnit == NULL)
        return true;

    for (y = 0; y < gBmMapSize.y; y++)
    {
        for (x = 0; x < gBmMapSize.x; x++)
        {
            struct AiDecision candidate;

            if (!IsLegalWaitDestination(x, y))
                continue;
            MakeWaitCandidate(x, y, &candidate);
            if (DecisionsEqual(&candidate, &sPlannerSelectedDecision))
                continue;
            if (sPlannerCandidateCount
                >= EXPANSION_AUTOPLAY_PLANNER_TOTAL_ACTION_CAPACITY)
                return false;
            sPlannerCandidateCount++;
        }
    }
    return true;
}

static void PublishPage(u32 pageIndex)
{
    u32 index;
    u32 start = pageIndex * EXPANSION_AUTOPLAY_PLANNER_ACTION_CAPACITY;
    u32 remaining = sPlannerCandidateCount - start;
    u32 count = remaining < EXPANSION_AUTOPLAY_PLANNER_ACTION_CAPACITY
        ? remaining : EXPANSION_AUTOPLAY_PLANNER_ACTION_CAPACITY;
    u16 seeds[3];

    StoreRNState(seeds);
    ClearPublishedActions();
    gExpansionAutoplayPlannerObservation.magic = EXPANSION_AUTOPLAY_PLANNER_MAGIC;
    gExpansionAutoplayPlannerObservation.version = EXPANSION_AUTOPLAY_PLANNER_PROTOCOL_VERSION;
    gExpansionAutoplayPlannerObservation.byteSize =
        sizeof(struct ExpansionAutoplayPlannerObservationV2);
    gExpansionAutoplayPlannerObservation.runId = sPlannerRunId;
    gExpansionAutoplayPlannerObservation.state = EXPANSION_AUTOPLAY_PLANNER_STATE_WAITING;
    gExpansionAutoplayPlannerObservation.pageIndex = pageIndex;
    gExpansionAutoplayPlannerObservation.pageCount =
        (sPlannerCandidateCount + EXPANSION_AUTOPLAY_PLANNER_ACTION_CAPACITY - 1)
        / EXPANSION_AUTOPLAY_PLANNER_ACTION_CAPACITY;
    gExpansionAutoplayPlannerObservation.actionStartOrdinal = start;
    gExpansionAutoplayPlannerObservation.actionCount = count;
    gExpansionAutoplayPlannerObservation.totalActionCount = sPlannerCandidateCount;
    gExpansionAutoplayPlannerObservation.rejection = EXPANSION_AUTOPLAY_PLANNER_REJECTION_NONE;
    gExpansionAutoplayPlannerObservation.chapterIndex = (u8)gPlaySt.chapterIndex;
    gExpansionAutoplayPlannerObservation.chapterTurn = gPlaySt.chapterTurnNumber;
    gExpansionAutoplayPlannerObservation.rngState0 = seeds[0];
    gExpansionAutoplayPlannerObservation.rngState1 = seeds[1];
    gExpansionAutoplayPlannerObservation.rngState2 = seeds[2];
    gExpansionAutoplayPlannerObservation.rngLcg = GetLCGRNValue();
    gExpansionAutoplayPlannerObservation.rngConsumption = GetRNConsumptionCount();
    gExpansionAutoplayPlannerObservation.actualRomIdentity = ActualRomIdentity();
    gExpansionAutoplayPlannerObservation.actualConfigIdentity = ActualConfigIdentity();
    gExpansionAutoplayPlannerObservation.actualScenarioIdentity =
        EXPANSION_AUTOPLAY_PLANNER_SCENARIO_ID;
    gExpansionAutoplayPlannerObservation.actualSeedIdentity = ActualSeedIdentity();

    for (index = 0; index < count; index++)
    {
        struct AiDecision decision;
        struct ExpansionAutoplayPlannerActionV2* action =
            &gExpansionAutoplayPlannerObservation.actions[index];
        bool found = GetCandidate(start + index, &decision);
        u32 tokenLo;

        if (!found)
            break;
        tokenLo = MakeTokenLo(
            &decision,
            gExpansionAutoplayPlannerObservation.observationId,
            start + index);
        action->kind = ActionKindFromAiAction(decision.actionId);
        action->actor = decision.unitId;
        action->destination = (u16)decision.xMove | ((u32)(u16)decision.yMove << 16);
        action->target = decision.targetId | ((u32)decision.xTarget << 8)
            | ((u32)decision.yTarget << 16);
        action->itemSlot = decision.itemSlot;
        action->tokenLo = tokenLo;
        action->tokenHi = MakeTokenHi(tokenLo);
        action->actionId = decision.actionId;
    }
}

static u32 SemanticStateDigest(void)
{
    u32 digest = 2166136261u;
    u8* flags;
    int size;
    int index;

    digest = MixDigest(digest, (u8)gPlaySt.chapterIndex);
    digest = MixDigest(digest, gPlaySt.chapterTurnNumber);
    digest = MixDigest(digest, gPlaySt.partyGoldAmount);
    for (index = 1; index < 0x40; index++)
    {
        struct Unit* unit = GetUnit(index);
        int item;

        if (unit == NULL || unit->pCharacterData == NULL)
        {
            digest = MixDigest(digest, 0);
            continue;
        }
        digest = MixDigest(digest, unit->pCharacterData->number);
        digest = MixDigest(
            digest,
            unit->pClassData == NULL ? 0 : unit->pClassData->number);
        digest = MixDigest(digest, unit->level);
        digest = MixDigest(digest, unit->exp);
        digest = MixDigest(digest, unit->curHP);
        digest = MixDigest(digest, unit->state);
        for (item = 0; item < UNIT_ITEM_COUNT; item++)
            digest = MixDigest(digest, unit->items[item]);
    }
    flags = GetPermanentFlagBits();
    size = GetPermanentFlagBitsSize();
    for (index = 0; index < size; index++)
        digest = MixDigest(digest, flags[index]);
    flags = GetChapterFlagBits();
    size = GetChapterFlagBitsSize();
    for (index = 0; index < size; index++)
        digest = MixDigest(digest, flags[index]);
    digest = MixDigest(digest, sPlannerLastTokenLo);
    return MixDigest(digest, sPlannerLastTokenHi);
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
    sPlannerRunId = 0;
    sPlannerNextObservationId = 1;
    sPlannerCandidateCount = 0;
    sPlannerHasSelectedDecision = false;
    sPlannerWaitFrames = 0;
    sPlannerLastTokenLo = 0;
    sPlannerLastTokenHi = 0;
    sPlannerTraceDigest = 2166136261u;
    PublishReadyState();
}

void ExpansionAutoplayPlanner_OnMapReset(void)
{
    u8* bytes;
    int index;

    if (!sPlannerActive)
    {
        ExpansionAutoplayPlanner_Reset();
        return;
    }

    bytes = (u8*)&gExpansionAutoplayPlannerObservation;
    for (index = 0; index < (int)sizeof(gExpansionAutoplayPlannerObservation); index++)
        bytes[index] = 0;
    bytes = (u8*)&gExpansionAutoplayPlannerCommand;
    for (index = 0; index < (int)sizeof(gExpansionAutoplayPlannerCommand); index++)
        bytes[index] = 0;
    sPlannerCandidateCount = 0;
    sPlannerHasSelectedDecision = false;
    sPlannerWaitFrames = 0;
    PublishReadyState();
    ExpansionAutoplay_SetBlueControl(EXPANSION_BLUE_CONTROL_COMPUTER);
}

bool ExpansionAutoplayPlanner_PollStart(void)
{
    if (gExpansionAutoplayPlannerCommand.kind == EXPANSION_AUTOPLAY_PLANNER_COMMAND_NONE)
        return false;

    if (sPlannerActive
        || gExpansionAutoplayPlannerCommand.kind
            != EXPANSION_AUTOPLAY_PLANNER_COMMAND_START)
    {
        Reject(EXPANSION_AUTOPLAY_PLANNER_REJECTION_PROTOCOL_ERROR);
        return false;
    }

    if (!IsCommandHeaderValid())
    {
        Reject(EXPANSION_AUTOPLAY_PLANNER_REJECTION_PROTOCOL_ERROR);
        return false;
    }

    if (gExpansionAutoplayPlannerCommand.expectedRomIdentity != ActualRomIdentity()
        || gExpansionAutoplayPlannerCommand.expectedConfigIdentity
            != ActualConfigIdentity()
        || gExpansionAutoplayPlannerCommand.expectedScenarioIdentity
            != EXPANSION_AUTOPLAY_PLANNER_SCENARIO_ID
        || gExpansionAutoplayPlannerCommand.expectedSeedIdentity
            != ActualSeedIdentity())
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
    sPlannerLastTokenLo = 0;
    sPlannerLastTokenHi = 0;
    gExpansionAutoplayPlannerCommand.result = 1;
    gExpansionAutoplayPlannerCommand.rejection = EXPANSION_AUTOPLAY_PLANNER_REJECTION_NONE;
    ClearCommand();
    PublishReadyState();
    return true;
}

bool ExpansionAutoplayPlanner_IsActive(void)
{
    return sPlannerActive;
}

enum ExpansionAutoplayPlannerDecisionResult ExpansionAutoplayPlanner_OfferDecision(
    const struct AiDecision* decision)
{
    if (!sPlannerActive)
        return EXPANSION_AUTOPLAY_PLANNER_DECISION_FALLBACK;

    if (gExpansionAutoplayPlannerObservation.state
        == EXPANSION_AUTOPLAY_PLANNER_STATE_WAITING)
        return EXPANSION_AUTOPLAY_PLANNER_DECISION_WAIT;

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

    if (!BuildCandidates(decision))
    {
        gExpansionAutoplayPlannerObservation.state = EXPANSION_AUTOPLAY_PLANNER_STATE_EXHAUSTED;
        Reject(EXPANSION_AUTOPLAY_PLANNER_REJECTION_RESOURCE_LIMIT);
        return EXPANSION_AUTOPLAY_PLANNER_DECISION_EXHAUSTED;
    }

    gExpansionAutoplayPlannerObservation.observationId = sPlannerNextObservationId++;
    sPlannerWaitFrames = 0;
    PublishPage(0);
    return EXPANSION_AUTOPLAY_PLANNER_DECISION_WAIT;
}

enum ExpansionAutoplayPlannerDecisionResult ExpansionAutoplayPlanner_PollDecision(
    struct AiDecision* decision)
{
    struct AiDecision candidate;
    u32 tokenLo;

    if (!sPlannerActive
        || gExpansionAutoplayPlannerObservation.state
            != EXPANSION_AUTOPLAY_PLANNER_STATE_WAITING)
        return EXPANSION_AUTOPLAY_PLANNER_DECISION_FALLBACK;

    if (gExpansionAutoplayPlannerCommand.kind == EXPANSION_AUTOPLAY_PLANNER_COMMAND_NONE)
    {
        sPlannerWaitFrames++;
        if (sPlannerWaitFrames < EXPANSION_AUTOPLAY_PLANNER_DECISION_TIMEOUT_FRAMES)
            return EXPANSION_AUTOPLAY_PLANNER_DECISION_WAIT;
        gExpansionAutoplayPlannerObservation.state =
            EXPANSION_AUTOPLAY_PLANNER_STATE_CANCELLED;
        Reject(EXPANSION_AUTOPLAY_PLANNER_REJECTION_TIMEOUT);
        sPlannerActive = false;
        ExpansionAutoplay_RequestPlayerControlRestore();
        return EXPANSION_AUTOPLAY_PLANNER_DECISION_CANCELLED;
    }

    if (gExpansionAutoplayPlannerCommand.kind == EXPANSION_AUTOPLAY_PLANNER_COMMAND_START)
    {
        Reject(EXPANSION_AUTOPLAY_PLANNER_REJECTION_PROTOCOL_ERROR);
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
        ExpansionAutoplay_RequestPlayerControlRestore();
        return EXPANSION_AUTOPLAY_PLANNER_DECISION_CANCELLED;
    }

    if (gExpansionAutoplayPlannerCommand.kind == EXPANSION_AUTOPLAY_PLANNER_COMMAND_PAGE)
    {
        if (!IsCommandHeaderValid()
            || gExpansionAutoplayPlannerCommand.runId != sPlannerRunId
            || gExpansionAutoplayPlannerCommand.observationId
                != gExpansionAutoplayPlannerObservation.observationId)
        {
            Reject(EXPANSION_AUTOPLAY_PLANNER_REJECTION_STALE_OBSERVATION);
            return EXPANSION_AUTOPLAY_PLANNER_DECISION_WAIT;
        }
        if (gExpansionAutoplayPlannerCommand.pageIndex
            >= gExpansionAutoplayPlannerObservation.pageCount)
        {
            Reject(EXPANSION_AUTOPLAY_PLANNER_REJECTION_UNKNOWN_ACTION);
            return EXPANSION_AUTOPLAY_PLANNER_DECISION_WAIT;
        }
        PublishPage(gExpansionAutoplayPlannerCommand.pageIndex);
        gExpansionAutoplayPlannerCommand.result = 1;
        ClearCommand();
        return EXPANSION_AUTOPLAY_PLANNER_DECISION_WAIT;
    }

    if (gExpansionAutoplayPlannerCommand.kind != EXPANSION_AUTOPLAY_PLANNER_COMMAND_COMMIT)
    {
        Reject(EXPANSION_AUTOPLAY_PLANNER_REJECTION_PROTOCOL_ERROR);
        return EXPANSION_AUTOPLAY_PLANNER_DECISION_WAIT;
    }

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

    if (gExpansionAutoplayPlannerCommand.actionOrdinal >= sPlannerCandidateCount)
    {
        Reject(EXPANSION_AUTOPLAY_PLANNER_REJECTION_UNKNOWN_ACTION);
        return EXPANSION_AUTOPLAY_PLANNER_DECISION_WAIT;
    }

    if (!GetCandidate(gExpansionAutoplayPlannerCommand.actionOrdinal, &candidate))
    {
        Reject(EXPANSION_AUTOPLAY_PLANNER_REJECTION_ACTION_BECAME_ILLEGAL);
        return EXPANSION_AUTOPLAY_PLANNER_DECISION_WAIT;
    }
    tokenLo = MakeTokenLo(
        &candidate,
        gExpansionAutoplayPlannerObservation.observationId,
        gExpansionAutoplayPlannerCommand.actionOrdinal);
    if (gExpansionAutoplayPlannerCommand.tokenLo != tokenLo
        || gExpansionAutoplayPlannerCommand.tokenHi != MakeTokenHi(tokenLo))
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
    sPlannerLastTokenLo = tokenLo;
    sPlannerLastTokenHi = MakeTokenHi(tokenLo);
    *decision = candidate;
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
        sizeof(struct ExpansionAutoplayPlannerCampaignCheckpointV2);
    gExpansionAutoplayPlannerCampaignCheckpoint.runId = sPlannerRunId;
    gExpansionAutoplayPlannerCampaignCheckpoint.chapterIndex = (u8)gPlaySt.chapterIndex;
    gExpansionAutoplayPlannerCampaignCheckpoint.chapterTurn = gPlaySt.chapterTurnNumber;
    gExpansionAutoplayPlannerCampaignCheckpoint.rngState0 = seeds[0];
    gExpansionAutoplayPlannerCampaignCheckpoint.rngState1 = seeds[1];
    gExpansionAutoplayPlannerCampaignCheckpoint.rngState2 = seeds[2];
    gExpansionAutoplayPlannerCampaignCheckpoint.rngLcg = GetLCGRNValue();
    gExpansionAutoplayPlannerCampaignCheckpoint.rngConsumption = GetRNConsumptionCount();
    gExpansionAutoplayPlannerCampaignCheckpoint.traceDigest = sPlannerTraceDigest;
    gExpansionAutoplayPlannerCampaignCheckpoint.semanticStateDigest =
        SemanticStateDigest();
    gExpansionAutoplayPlannerCampaignCheckpoint.acceptedTokenLo = sPlannerLastTokenLo;
    gExpansionAutoplayPlannerCampaignCheckpoint.acceptedTokenHi = sPlannerLastTokenHi;
}

#endif
