#include "global.h"

#include <stdio.h>

#include "bm.h"
#include "bmmap.h"
#include "cp_common.h"
#include "expansion_autoplay.h"
#include "expansion_autoplay_planner.h"
#include "rng.h"

#define CHECK(condition, message) \
    do \
    { \
        if (!(condition)) \
        { \
            fprintf(stderr, "AUTOPLAY_PLANNER_HOST_TEST: FAIL: %s\n", message); \
            return 1; \
        } \
    } while (0)

struct PlaySt gPlaySt;
struct Unit* gActiveUnit;
u8 gActiveUnitId;
struct Vec2 gBmMapSize;
u8** gBmMapMovement;
u8** gBmMapUnit;

static u16 sSeeds[3] = { 1, 2, 3 };
static u32 sConsumption;
static int sControlRequests;
static u8 sMovementData[8][8];
static u8* sMovementRows[8];
static u8 sUnitData[8][8];
static u8* sUnitRows[8];

void StoreRNState(u16* seeds)
{
    seeds[0] = sSeeds[0];
    seeds[1] = sSeeds[1];
    seeds[2] = sSeeds[2];
}

unsigned GetLCGRNValue(void)
{
    return 0;
}

u32 GetRNConsumptionCount(void)
{
    return sConsumption;
}

enum ExpansionAutoplayResult ExpansionAutoplay_SetBlueControl(enum ExpansionBlueControl control)
{
    sControlRequests++;
    return control == EXPANSION_BLUE_CONTROL_COMPUTER
        ? EXPANSION_AUTOPLAY_OK
        : EXPANSION_AUTOPLAY_ERR_INVALID_CONTROL;
}

static void WriteCommand(
    enum ExpansionAutoplayPlannerCommandKind kind,
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

int main(void)
{
    struct AiDecision decision = { 0 };
    struct AiDecision original;
    const struct ExpansionAutoplayPlannerActionV2* action;
    u32 selectedOrdinal;
    u32 selectedTokenLo;
    u32 selectedTokenHi;
    int index;

    gPlaySt.chapterIndex = 1;
    gPlaySt.chapterTurnNumber = 1;
    gActiveUnitId = 1;
    gBmMapSize.x = 8;
    gBmMapSize.y = 8;
    for (index = 0; index < 8; index++)
    {
        int x;
        sMovementRows[index] = sMovementData[index];
        sUnitRows[index] = sUnitData[index];
        for (x = 0; x < 8; x++)
        {
            sMovementData[index][x] = 1;
            sUnitData[index][x] = 0;
        }
    }
    gBmMapMovement = sMovementRows;
    gBmMapUnit = sUnitRows;
    ExpansionAutoplayPlanner_Reset();

    WriteCommand((enum ExpansionAutoplayPlannerCommandKind)99, 0, 0, 0, 0, 0, 0);
    CHECK(!ExpansionAutoplayPlanner_PollStart(), "unknown idle command must reject");
    CHECK(
        gExpansionAutoplayPlannerObservation.rejection
            == EXPANSION_AUTOPLAY_PLANNER_REJECTION_PROTOCOL_ERROR,
        "unknown idle command must report protocol error"
    );

    WriteCommand(EXPANSION_AUTOPLAY_PLANNER_COMMAND_START, 0, 0, 0, 0, 0, 0);
    gExpansionAutoplayPlannerCommand.expectedScenarioIdentity ^= 1;
    CHECK(!ExpansionAutoplayPlanner_PollStart(), "mismatched provenance must reject");

    WriteCommand(EXPANSION_AUTOPLAY_PLANNER_COMMAND_START, 0, 0, 0, 0, 0, 0);
    CHECK(ExpansionAutoplayPlanner_PollStart(), "typed START mailbox command must activate");
    CHECK(sControlRequests == 1, "valid provenance activates computer control once");

    WriteCommand(EXPANSION_AUTOPLAY_PLANNER_COMMAND_START, 0, 0, 0, 0, 0, 0);
    CHECK(!ExpansionAutoplayPlanner_PollStart(), "duplicate START must reject");
    CHECK(
        gExpansionAutoplayPlannerObservation.rejection
            == EXPANSION_AUTOPLAY_PLANNER_REJECTION_PROTOCOL_ERROR,
        "duplicate START must report protocol error"
    );

    decision.actionPerformed = true;
    decision.unitId = 1;
    decision.xMove = 2;
    decision.yMove = 3;
    decision.actionId = AI_ACTION_COMBAT;
    decision.targetId = 0x81;
    decision.itemSlot = 0;
    decision.xTarget = 2;
    decision.yTarget = 3;
    original = decision;
    CHECK(
        ExpansionAutoplayPlanner_OfferDecision(&decision)
            == EXPANSION_AUTOPLAY_PLANNER_DECISION_WAIT,
        "first production decision must publish one legal token"
    );
    CHECK(
        gExpansionAutoplayPlannerObservation.totalActionCount == 65,
        "selected action plus 64 reachable waits expected"
    );
    CHECK(
        gExpansionAutoplayPlannerObservation.pageCount == 3
            && gExpansionAutoplayPlannerObservation.actionCount == 29,
        "candidate set must be paged at the 1024-byte boundary"
    );
    CHECK(
        gExpansionAutoplayPlannerObservation.actions[0].kind
            == EXPANSION_AUTOPLAY_PLANNER_ACTION_COMBAT,
        "combat decision must retain semantic action kind"
    );

    for (index = 0; index < 4; index++)
    {
        CHECK(
            ExpansionAutoplayPlanner_PollDecision(&decision)
                == EXPANSION_AUTOPLAY_PLANNER_DECISION_WAIT,
            "idle poll must keep waiting"
        );
        CHECK(
            decision.actionId == original.actionId
                && decision.xMove == original.xMove
                && decision.yMove == original.yMove
                && sConsumption == 0,
            "poll latency must not regenerate or replace the decision/RNG"
        );
    }

    WriteCommand(
        EXPANSION_AUTOPLAY_PLANNER_COMMAND_PAGE,
        gExpansionAutoplayPlannerObservation.runId,
        gExpansionAutoplayPlannerObservation.observationId,
        1,
        0,
        0,
        0);
    CHECK(
        ExpansionAutoplayPlanner_PollDecision(&decision)
            == EXPANSION_AUTOPLAY_PLANNER_DECISION_WAIT,
        "valid PAGE command must publish another page"
    );
    CHECK(
        gExpansionAutoplayPlannerObservation.pageIndex == 1
            && gExpansionAutoplayPlannerObservation.actionStartOrdinal == 29
            && gExpansionAutoplayPlannerObservation.actionCount == 29,
        "second page must retain stable global ordinals"
    );

    WriteCommand(
        (enum ExpansionAutoplayPlannerCommandKind)99,
        gExpansionAutoplayPlannerObservation.runId,
        gExpansionAutoplayPlannerObservation.observationId,
        0,
        0,
        0,
        0);
    CHECK(
        ExpansionAutoplayPlanner_PollDecision(&decision)
            == EXPANSION_AUTOPLAY_PLANNER_DECISION_WAIT
            && gExpansionAutoplayPlannerObservation.rejection
                == EXPANSION_AUTOPLAY_PLANNER_REJECTION_PROTOCOL_ERROR,
        "unexpected waiting command must reject instead of waiting forever"
    );
    WriteCommand(
        EXPANSION_AUTOPLAY_PLANNER_COMMAND_PAGE,
        gExpansionAutoplayPlannerObservation.runId,
        gExpansionAutoplayPlannerObservation.observationId,
        1,
        0,
        0,
        0);
    CHECK(
        ExpansionAutoplayPlanner_PollDecision(&decision)
            == EXPANSION_AUTOPLAY_PLANNER_DECISION_WAIT,
        "page must republish after malformed command"
    );

    action = &gExpansionAutoplayPlannerObservation.actions[0];
    selectedOrdinal = gExpansionAutoplayPlannerObservation.actionStartOrdinal;
    selectedTokenLo = action->tokenLo;
    selectedTokenHi = action->tokenHi;
    WriteCommand(
        EXPANSION_AUTOPLAY_PLANNER_COMMAND_COMMIT,
        gExpansionAutoplayPlannerObservation.runId,
        gExpansionAutoplayPlannerObservation.observationId,
        0,
        selectedOrdinal,
        action->tokenLo,
        action->tokenHi ^ 1);
    CHECK(
        ExpansionAutoplayPlanner_PollDecision(&decision)
            == EXPANSION_AUTOPLAY_PLANNER_DECISION_WAIT,
        "forged token must not commit"
    );
    CHECK(
        gExpansionAutoplayPlannerObservation.rejection
            == EXPANSION_AUTOPLAY_PLANNER_REJECTION_TOKEN_MISMATCH,
        "forged token must have explicit rejection"
    );

    WriteCommand(
        EXPANSION_AUTOPLAY_PLANNER_COMMAND_COMMIT,
        gExpansionAutoplayPlannerObservation.runId,
        gExpansionAutoplayPlannerObservation.observationId,
        0,
        selectedOrdinal,
        selectedTokenLo,
        selectedTokenHi);
    CHECK(
        ExpansionAutoplayPlanner_PollDecision(&decision)
            == EXPANSION_AUTOPLAY_PLANNER_DECISION_ACCEPTED,
        "matching token must commit the existing production decision"
    );

    sConsumption = 4;
    gPlaySt.chapterIndex = 1;
    ExpansionAutoplayPlanner_RecordCampaignCheckpoint();
    ExpansionAutoplayPlanner_OnMapReset();
    CHECK(
        ExpansionAutoplayPlanner_IsActive()
            && gExpansionAutoplayPlannerCampaignCheckpoint.chapterIndex == 1,
        "chapter transition must preserve active campaign checkpoint"
    );
    gPlaySt.chapterIndex = 2;
    ExpansionAutoplayPlanner_RecordCampaignCheckpoint();
    CHECK(
        gExpansionAutoplayPlannerCampaignCheckpoint.chapterIndex == 2
            && gExpansionAutoplayPlannerCampaignCheckpoint.rngConsumption == 4,
        "campaign checkpoint must be semantic and RNG-owned"
    );

    puts("AUTOPLAY_PLANNER_HOST_TEST: PASS");
    return 0;
}
