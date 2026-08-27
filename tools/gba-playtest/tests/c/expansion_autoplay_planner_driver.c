#include "global.h"

#include <stdio.h>

#include "bm.h"
#include "bmmap.h"
#include "bmunit.h"
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
static int sRestoreRequests;
static struct CharacterData sCharacter;
static struct ClassData sClass;
static struct Unit sUnit;
static u8 sPermanentFlags[8];
static u8 sChapterFlags[8];
static u8 sMovementData[16][32];
static u8* sMovementRows[16];
static u8 sUnitData[16][32];
static u8* sUnitRows[16];

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

struct Unit* GetUnit(int id)
{
    return id == 1 ? &sUnit : NULL;
}

u8* GetPermanentFlagBits(void)
{
    return sPermanentFlags;
}

int GetPermanentFlagBitsSize(void)
{
    return sizeof(sPermanentFlags);
}

u8* GetChapterFlagBits(void)
{
    return sChapterFlags;
}

int GetChapterFlagBitsSize(void)
{
    return sizeof(sChapterFlags);
}

void ExpansionAutoplay_RequestPlayerControlRestore(void)
{
    sRestoreRequests++;
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
    sCharacter.number = 1;
    sClass.number = 1;
    sUnit.pCharacterData = &sCharacter;
    sUnit.pClassData = &sClass;
    sUnit.level = 1;
    sUnit.curHP = 20;
    sUnit.items[0] = 1;
    gActiveUnit = &sUnit;
    gBmMapSize.x = 32;
    gBmMapSize.y = 16;
    for (index = 0; index < 16; index++)
    {
        int x;
        sMovementRows[index] = sMovementData[index];
        sUnitRows[index] = sUnitData[index];
        for (x = 0; x < 32; x++)
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
    sSeeds[0] = 9;
    CHECK(
        !ExpansionAutoplayPlanner_PollStart()
            && gExpansionAutoplayPlannerObservation.rejection
                == EXPANSION_AUTOPLAY_PLANNER_REJECTION_PROTOCOL_ERROR,
        "different RN words with the same LCG must reject provenance"
    );
    sSeeds[0] = 1;
    ExpansionAutoplayPlanner_Reset();

    WriteCommand(EXPANSION_AUTOPLAY_PLANNER_COMMAND_START, 0, 0, 0, 0, 0, 0);
    gExpansionAutoplayPlannerCommand.expectedRomIdentity ^= 1;
    CHECK(
        !ExpansionAutoplayPlanner_PollStart()
            && gExpansionAutoplayPlannerObservation.rejection
                == EXPANSION_AUTOPLAY_PLANNER_REJECTION_PROTOCOL_ERROR,
        "same header with different build identity must reject provenance"
    );
    ExpansionAutoplayPlanner_Reset();

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
        gExpansionAutoplayPlannerObservation.totalActionCount == 512,
        "selected action plus 511 reachable waits expected"
    );
    CHECK(
        gExpansionAutoplayPlannerObservation.pageCount == 18
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
        17,
        0,
        0,
        0);
    CHECK(
        ExpansionAutoplayPlanner_PollDecision(&decision)
            == EXPANSION_AUTOPLAY_PLANNER_DECISION_WAIT,
        "valid PAGE command must publish another page"
    );
    CHECK(
        gExpansionAutoplayPlannerObservation.pageIndex == 17
            && gExpansionAutoplayPlannerObservation.actionStartOrdinal == 493
            && gExpansionAutoplayPlannerObservation.actionCount == 19,
        "last page must retain stable global ordinals"
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
        17,
        0,
        0,
        0);
    CHECK(
        ExpansionAutoplayPlanner_PollDecision(&decision)
            == EXPANSION_AUTOPLAY_PLANNER_DECISION_WAIT,
        "page must republish after malformed command"
    );

    action = &gExpansionAutoplayPlannerObservation.actions[18];
    selectedOrdinal = gExpansionAutoplayPlannerObservation.actionStartOrdinal + 18;
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
    CHECK(
        selectedOrdinal == 511
            && decision.xMove == 31
            && decision.yMove == 15,
        "last-page selection must map to its stable row-major candidate"
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
            && gExpansionAutoplayPlannerCampaignCheckpoint.rngConsumption == 4
            && gExpansionAutoplayPlannerCampaignCheckpoint.semanticStateDigest != 0,
        "campaign checkpoint must be semantic and RNG-owned"
    );

    {
        u32 digest = gExpansionAutoplayPlannerCampaignCheckpoint.semanticStateDigest;
        sUnit.items[0] = 2;
        ExpansionAutoplayPlanner_RecordCampaignCheckpoint();
        CHECK(
            digest != gExpansionAutoplayPlannerCampaignCheckpoint.semanticStateDigest,
            "inventory changes must alter the semantic checkpoint digest"
        );
    }

    ExpansionAutoplayPlanner_OfferDecision(&decision);
    for (index = 1;
         index < EXPANSION_AUTOPLAY_PLANNER_DECISION_TIMEOUT_FRAMES;
         index++)
    {
        CHECK(
            ExpansionAutoplayPlanner_PollDecision(&decision)
                == EXPANSION_AUTOPLAY_PLANNER_DECISION_WAIT,
            "planner must wait through the frame before the deadline"
        );
    }
    CHECK(
        ExpansionAutoplayPlanner_PollDecision(&decision)
            == EXPANSION_AUTOPLAY_PLANNER_DECISION_CANCELLED
            && gExpansionAutoplayPlannerObservation.rejection
                == EXPANSION_AUTOPLAY_PLANNER_REJECTION_TIMEOUT
            && sRestoreRequests == 1,
        "deadline must cancel and queue player control restoration"
    );

    ExpansionAutoplayPlanner_Reset();
    CHECK(
        !ExpansionAutoplayPlanner_IsActive()
            && gExpansionAutoplayPlannerCampaignCheckpoint.magic == 0,
        "destructive full-run reset must clear active/checkpoint state"
    );
    WriteCommand(EXPANSION_AUTOPLAY_PLANNER_COMMAND_START, 0, 0, 0, 0, 0, 0);
    CHECK(ExpansionAutoplayPlanner_PollStart(), "second run must start after reset");
    CHECK(
        ExpansionAutoplayPlanner_OfferDecision(&decision)
            == EXPANSION_AUTOPLAY_PLANNER_DECISION_WAIT,
        "second run must publish before cancellation"
    );
    WriteCommand(
        EXPANSION_AUTOPLAY_PLANNER_COMMAND_CANCEL,
        gExpansionAutoplayPlannerObservation.runId,
        gExpansionAutoplayPlannerObservation.observationId,
        0,
        0,
        0,
        0);
    CHECK(
        ExpansionAutoplayPlanner_PollDecision(&decision)
            == EXPANSION_AUTOPLAY_PLANNER_DECISION_CANCELLED
            && sRestoreRequests == 2,
        "explicit cancellation must queue player control restoration"
    );

    ExpansionAutoplayPlanner_Reset();
    WriteCommand(EXPANSION_AUTOPLAY_PLANNER_COMMAND_START, 0, 0, 0, 0, 0, 0);
    CHECK(ExpansionAutoplayPlanner_PollStart(), "wait-candidate run must start");
    decision.actionPerformed = true;
    decision.unitId = 1;
    decision.xMove = sUnit.xPos;
    decision.yMove = sUnit.yPos;
    decision.actionId = AI_ACTION_NONE;
    decision.targetId = 0;
    CHECK(
        ExpansionAutoplayPlanner_OfferDecision(&decision)
            == EXPANSION_AUTOPLAY_PLANNER_DECISION_WAIT,
        "same-tile wait input must still publish legal alternatives"
    );
    action = &gExpansionAutoplayPlannerObservation.actions[0];
    CHECK(
        (action->destination & 0xFFFF) != sUnit.xPos
            || (action->destination >> 16) != sUnit.yPos,
        "active unit current tile must not be a committed wait candidate"
    );
    WriteCommand(
        EXPANSION_AUTOPLAY_PLANNER_COMMAND_COMMIT,
        gExpansionAutoplayPlannerObservation.runId,
        gExpansionAutoplayPlannerObservation.observationId,
        0,
        gExpansionAutoplayPlannerObservation.actionStartOrdinal,
        action->tokenLo,
        action->tokenHi);
    CHECK(
        ExpansionAutoplayPlanner_PollDecision(&decision)
            == EXPANSION_AUTOPLAY_PLANNER_DECISION_ACCEPTED
            && (decision.xMove != sUnit.xPos || decision.yMove != sUnit.yPos),
        "accepted wait must traverse the normal nontrivial perform path"
    );
    ExpansionAutoplayPlanner_RecordCampaignCheckpoint();
    CHECK(
        gExpansionAutoplayPlannerCampaignCheckpoint.acceptedTokenLo
            == action->tokenLo,
        "accepted wait token must enter the action trace checkpoint"
    );

    puts("AUTOPLAY_PLANNER_HOST_TEST: PASS");
    return 0;
}
