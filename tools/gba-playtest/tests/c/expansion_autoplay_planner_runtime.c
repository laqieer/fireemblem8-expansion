#include "global.h"

#include "bm.h"
#include "bmmap.h"
#include "bmunit.h"
#include "cp_common.h"
#include "expansion_autoplay.h"
#include "expansion_autoplay_planner.h"
#include "rng.h"

struct PlaySt gPlaySt;
struct Unit* gActiveUnit;
u8 gActiveUnitId;
struct Vec2 gBmMapSize;
u8** gBmMapMovement;
u8** gBmMapUnit;

struct PlannerRuntimeProbe
{
    u32 terminal;
    u32 malformedRejection;
    u32 provenanceRejection;
    u32 duplicateStartRejection;
    u32 tokenRejection;
    u32 acceptedOrdinal;
    u32 pageCount;
    u32 candidateCount;
    u32 cancelled;
    u32 checkpointChapter;
    u32 checkpointRunId;
};

struct PlannerRuntimeProbe EWRAM_DATA gPlannerRuntimeProbe;

static u16 sSeeds[3];
static u32 sConsumption;
static u8 sMovementData[8][8];
static u8* sMovementRows[8];
static u8 sUnitData[8][8];
static u8* sUnitRows[8];

void* memcpy(void* destination, const void* source, size_t size)
{
    u8* output = destination;
    const u8* input = source;

    while (size-- != 0)
        *output++ = *input++;
    return destination;
}

void StoreRNState(u16* seeds)
{
    seeds[0] = sSeeds[0];
    seeds[1] = sSeeds[1];
    seeds[2] = sSeeds[2];
}

unsigned GetLCGRNValue(void)
{
    return 0x12345678;
}

u32 GetRNConsumptionCount(void)
{
    return sConsumption;
}

enum ExpansionAutoplayResult ExpansionAutoplay_SetBlueControl(enum ExpansionBlueControl control)
{
    return control == EXPANSION_BLUE_CONTROL_COMPUTER
        ? EXPANSION_AUTOPLAY_OK
        : EXPANSION_AUTOPLAY_ERR_INVALID_CONTROL;
}

static void WriteCommand(
    u32 kind,
    u32 runId,
    u32 observationId,
    u32 pageIndex,
    u32 ordinal,
    u32 tokenLo,
    u32 tokenHi)
{
    gExpansionAutoplayPlannerCommand.magic = EXPANSION_AUTOPLAY_PLANNER_MAGIC;
    gExpansionAutoplayPlannerCommand.version = EXPANSION_AUTOPLAY_PLANNER_PROTOCOL_VERSION;
    gExpansionAutoplayPlannerCommand.byteSize =
        sizeof(struct ExpansionAutoplayPlannerCommandV2);
    gExpansionAutoplayPlannerCommand.kind = kind;
    gExpansionAutoplayPlannerCommand.runId = runId;
    gExpansionAutoplayPlannerCommand.observationId = observationId;
    gExpansionAutoplayPlannerCommand.pageIndex = pageIndex;
    gExpansionAutoplayPlannerCommand.actionOrdinal = ordinal;
    gExpansionAutoplayPlannerCommand.tokenLo = tokenLo;
    gExpansionAutoplayPlannerCommand.tokenHi = tokenHi;
    gExpansionAutoplayPlannerCommand.expectedRomIdentity =
        gExpansionAutoplayPlannerObservation.actualRomIdentity;
    gExpansionAutoplayPlannerCommand.expectedConfigIdentity =
        gExpansionAutoplayPlannerObservation.actualConfigIdentity;
    gExpansionAutoplayPlannerCommand.expectedScenarioIdentity =
        gExpansionAutoplayPlannerObservation.actualScenarioIdentity;
    gExpansionAutoplayPlannerCommand.expectedSeedIdentity =
        gExpansionAutoplayPlannerObservation.actualSeedIdentity;
}

void PlannerRuntime_Main(void)
{
    struct AiDecision decision;
    const struct ExpansionAutoplayPlannerActionV2* action;
    int x;
    int y;

    sSeeds[0] = 1;
    sSeeds[1] = 2;
    sSeeds[2] = 3;
    gActiveUnitId = 1;
    gBmMapSize.x = 8;
    gBmMapSize.y = 8;
    for (y = 0; y < 8; y++)
    {
        sMovementRows[y] = sMovementData[y];
        sUnitRows[y] = sUnitData[y];
        for (x = 0; x < 8; x++)
        {
            sMovementData[y][x] = 1;
            sUnitData[y][x] = 0;
        }
    }
    gBmMapMovement = sMovementRows;
    gBmMapUnit = sUnitRows;
    gPlaySt.chapterIndex = 1;
    gPlaySt.chapterTurnNumber = 1;

    ExpansionAutoplayPlanner_Reset();
    WriteCommand(99, 0, 0, 0, 0, 0, 0);
    ExpansionAutoplayPlanner_PollStart();
    gPlannerRuntimeProbe.malformedRejection =
        gExpansionAutoplayPlannerObservation.rejection;

    WriteCommand(EXPANSION_AUTOPLAY_PLANNER_COMMAND_START, 0, 0, 0, 0, 0, 0);
    gExpansionAutoplayPlannerCommand.expectedConfigIdentity ^= 1;
    ExpansionAutoplayPlanner_PollStart();
    gPlannerRuntimeProbe.provenanceRejection =
        gExpansionAutoplayPlannerObservation.rejection;

    WriteCommand(EXPANSION_AUTOPLAY_PLANNER_COMMAND_START, 0, 0, 0, 0, 0, 0);
    ExpansionAutoplayPlanner_PollStart();
    WriteCommand(EXPANSION_AUTOPLAY_PLANNER_COMMAND_START, 0, 0, 0, 0, 0, 0);
    ExpansionAutoplayPlanner_PollStart();
    gPlannerRuntimeProbe.duplicateStartRejection =
        gExpansionAutoplayPlannerObservation.rejection;

    decision.actionPerformed = true;
    decision.unitId = 1;
    decision.xMove = 2;
    decision.yMove = 3;
    decision.actionId = AI_ACTION_COMBAT;
    decision.targetId = 0x81;
    decision.itemSlot = 0;
    decision.xTarget = 2;
    decision.yTarget = 3;
    ExpansionAutoplayPlanner_OfferDecision(&decision);
    gPlannerRuntimeProbe.pageCount = gExpansionAutoplayPlannerObservation.pageCount;
    gPlannerRuntimeProbe.candidateCount =
        gExpansionAutoplayPlannerObservation.totalActionCount;

    action = &gExpansionAutoplayPlannerObservation.actions[0];
    WriteCommand(
        EXPANSION_AUTOPLAY_PLANNER_COMMAND_COMMIT,
        gExpansionAutoplayPlannerObservation.runId,
        gExpansionAutoplayPlannerObservation.observationId,
        0,
        0,
        action->tokenLo,
        action->tokenHi ^ 1);
    ExpansionAutoplayPlanner_PollDecision(&decision);
    gPlannerRuntimeProbe.tokenRejection =
        gExpansionAutoplayPlannerObservation.rejection;

    WriteCommand(
        EXPANSION_AUTOPLAY_PLANNER_COMMAND_PAGE,
        gExpansionAutoplayPlannerObservation.runId,
        gExpansionAutoplayPlannerObservation.observationId,
        1,
        0,
        0,
        0);
    ExpansionAutoplayPlanner_PollDecision(&decision);
    action = &gExpansionAutoplayPlannerObservation.actions[0];
    WriteCommand(
        EXPANSION_AUTOPLAY_PLANNER_COMMAND_COMMIT,
        gExpansionAutoplayPlannerObservation.runId,
        gExpansionAutoplayPlannerObservation.observationId,
        0,
        gExpansionAutoplayPlannerObservation.actionStartOrdinal,
        action->tokenLo,
        action->tokenHi);
    ExpansionAutoplayPlanner_PollDecision(&decision);
    gPlannerRuntimeProbe.acceptedOrdinal =
        gExpansionAutoplayPlannerObservation.actionStartOrdinal;

    ExpansionAutoplayPlanner_RecordCampaignCheckpoint();
    ExpansionAutoplayPlanner_OnMapReset();
    gPlaySt.chapterIndex = 2;
    ExpansionAutoplayPlanner_RecordCampaignCheckpoint();
    gPlannerRuntimeProbe.checkpointChapter =
        gExpansionAutoplayPlannerCampaignCheckpoint.chapterIndex;
    gPlannerRuntimeProbe.checkpointRunId =
        gExpansionAutoplayPlannerCampaignCheckpoint.runId;

    ExpansionAutoplayPlanner_OfferDecision(&decision);
    WriteCommand(
        EXPANSION_AUTOPLAY_PLANNER_COMMAND_CANCEL,
        gExpansionAutoplayPlannerObservation.runId,
        gExpansionAutoplayPlannerObservation.observationId,
        0,
        0,
        0,
        0);
    ExpansionAutoplayPlanner_PollDecision(&decision);
    gPlannerRuntimeProbe.cancelled =
        gExpansionAutoplayPlannerObservation.state
        == EXPANSION_AUTOPLAY_PLANNER_STATE_CANCELLED;
    gPlannerRuntimeProbe.terminal = 1;

    for (;;)
        ;
}
