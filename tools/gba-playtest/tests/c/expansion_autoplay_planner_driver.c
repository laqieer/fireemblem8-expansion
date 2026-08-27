#include "global.h"

#include <stdio.h>
#include <string.h>

#include "bm.h"
#include "bmcontainer.h"
#include "bmitem.h"
#include "bmmap.h"
#include "bmunit.h"
#include "cp_common.h"
#include "constants/items.h"
#include "constants/terrains.h"
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
struct AiDecision gAiDecision;
struct Vec2 gBmMapSize;
u8** gBmMapMovement;
u8** gBmMapUnit;
u8** gBmMapTerrain;
u8** gBmMapFog;

static u16 sSeeds[3] = { 1, 2, 3 };
static u32 sConsumption;
static int sControlRequests;
static int sRestoreRequests;
static struct CharacterData sCharacter;
static struct CharacterData sAllyCharacter;
static struct CharacterData sEnemyCharacter;
static struct ClassData sClass;
static struct ClassData sAllyClass;
static struct ClassData sEnemyClass;
static struct Unit sUnit;
static struct Unit sAlly;
static struct Unit sEnemy;
static u8 sPermanentFlags[8];
static u8 sChapterFlags[8];
static u16 sConvoy[CONVOY_ITEM_COUNT];
static u8 sMovementData[17][32];
static u8* sMovementRows[17];
static u8 sUnitData[17][32];
static u8* sUnitRows[17];
static u8 sTerrainData[17][32];
static u8* sTerrainRows[17];
static u8 sFogData[17][32];
static u8* sFogRows[17];

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
    if (id == 1)
        return &sUnit;
    if (id == 2 && sAlly.pCharacterData != NULL)
        return &sAlly;
    if (id == 0x81 && sEnemy.pCharacterData != NULL)
        return &sEnemy;
    return NULL;
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

u16* GetConvoyItemArray(void)
{
    return sConvoy;
}

s8 AreUnitsAllied(int left, int right)
{
    return (left & 0xC0) == (right & 0xC0);
}

s8 CanUnitUseWeapon(struct Unit* unit, int item)
{
    (void)unit;
    return GetItemIndex(item) == ITEM_SWORD_IRON;
}

s8 CanUnitUseStaff(struct Unit* unit, int item)
{
    (void)unit;
    return GetItemIndex(item) == ITEM_STAFF_HEAL;
}

int GetItemAttributes(int item)
{
    if (GetItemIndex(item) == ITEM_SWORD_IRON)
        return IA_WEAPON;
    if (GetItemIndex(item) == ITEM_STAFF_HEAL)
        return IA_STAFF;
    return 0;
}

int GetItemIndex(int item)
{
    return item & 0xFF;
}

int GetItemMinRange(int item)
{
    (void)item;
    return 1;
}

int GetItemMaxRange(int item)
{
    (void)item;
    return 1;
}

int GetUnitMagBy2Range(struct Unit* unit)
{
    (void)unit;
    return 1;
}

bool IsPositionMagicSealed(int x, int y)
{
    (void)x;
    (void)y;
    return false;
}

s8 CanUnitCrossTerrain(struct Unit* unit, int terrain)
{
    (void)unit;
    return terrain != 0xFF;
}

bool IsThereClosedChestAt(s8 x, s8 y)
{
    return gBmMapTerrain[y][x] == TERRAIN_CHEST_FULL;
}

bool IsThereClosedDoorAt(s8 x, s8 y)
{
    return gBmMapTerrain[y][x] == TERRAIN_DOOR;
}

s8 IsItemHammernable(int item)
{
    (void)item;
    return false;
}

s8 CanUnitUseHealItem(struct Unit* unit)
{
    return unit->curHP < unit->maxHP;
}

s8 CanUnitUsePureWaterItem(struct Unit* unit)
{
    return unit->barrierDuration < 7;
}

s8 CanUnitUseTorchItem(struct Unit* unit)
{
    return gPlaySt.chapterVisionRange != 0 && unit->torchDuration != 4;
}

s8 CanUnitUseAntitoxinItem(struct Unit* unit)
{
    return unit->statusIndex == UNIT_STATUS_POISON;
}

s8 CanUnitUsePromotionItem(struct Unit* unit, int item)
{
    (void)unit;
    (void)item;
    return false;
}

s8 CanUnitUseStatGainItem(struct Unit* unit, int item)
{
    (void)unit;
    (void)item;
    return false;
}

s8 CanUnitUseFruitItem(struct Unit* unit)
{
    (void)unit;
    return false;
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

static struct AiDecision sEnumeratedActions[EXPANSION_AUTOPLAY_PLANNER_TOTAL_ACTION_CAPACITY];

static bool CollectAction(
    u32 ordinal,
    const struct AiDecision* decision,
    void* context)
{
    u32* count = context;

    CHECK(ordinal == *count, "enumerator ordinals must be contiguous");
    sEnumeratedActions[*count] = *decision;
    (*count)++;
    return true;
}

static u32 DigestBytes(u32 digest, const void* data, int size)
{
    const u8* bytes = data;
    int index;

    for (index = 0; index < size; index++)
        digest = (digest ^ bytes[index]) * 16777619u;
    return digest;
}

static u32 RuntimeStateDigest(void)
{
    u32 digest = 2166136261u;

    digest = DigestBytes(digest, &sUnit, sizeof(sUnit));
    digest = DigestBytes(digest, &gAiDecision, sizeof(gAiDecision));
    digest = DigestBytes(digest, &sAlly, sizeof(sAlly));
    digest = DigestBytes(digest, &sEnemy, sizeof(sEnemy));
    digest = DigestBytes(digest, sSeeds, sizeof(sSeeds));
    digest = DigestBytes(digest, &sConsumption, sizeof(sConsumption));
    digest = DigestBytes(digest, sMovementData, sizeof(sMovementData));
    digest = DigestBytes(digest, sUnitData, sizeof(sUnitData));
    digest = DigestBytes(digest, sTerrainData, sizeof(sTerrainData));
    digest = DigestBytes(digest, sFogData, sizeof(sFogData));
    return digest;
}

static int TestCompleteEnumerator(void)
{
    u32 firstCount = 0;
    u32 secondCount = 0;
    u32 stateBefore;
    u32 sequenceDigest = 2166136261u;
    u32 secondSequenceDigest = 2166136261u;
    u32 kinds = 0;
    int index;
    int other;

    gBmMapSize.x = 3;
    gBmMapSize.y = 3;
    sClass.attributes = CA_STEAL | CA_SUMMON;
    sUnit.xPos = 1;
    sUnit.yPos = 1;
    sUnit.maxHP = 20;
    sUnit.curHP = 10;
    sUnit.items[0] = ITEM_SWORD_IRON;
    sUnit.items[1] = ITEM_STAFF_HEAL;
    sUnit.items[2] = ITEM_VULNERARY;
    sUnitData[1][1] = 1;
    sAllyCharacter.number = 2;
    sAllyClass.number = 2;
    sAlly.pCharacterData = &sAllyCharacter;
    sAlly.pClassData = &sAllyClass;
    sAlly.index = 2;
    sAlly.xPos = 1;
    sAlly.yPos = 0;
    sAlly.maxHP = 20;
    sAlly.curHP = 10;
    sUnitData[0][1] = 2;
    sEnemyCharacter.number = 3;
    sEnemyClass.number = 3;
    sEnemy.pCharacterData = &sEnemyCharacter;
    sEnemy.pClassData = &sEnemyClass;
    sEnemy.index = 0x81;
    sEnemy.xPos = 2;
    sEnemy.yPos = 1;
    sEnemy.maxHP = 20;
    sEnemy.curHP = 20;
    sUnitData[1][2] = 0x81;
    sTerrainData[2][1] = TERRAIN_CHEST_FULL;
    sTerrainData[1][0] = TERRAIN_DOOR;
    stateBefore = RuntimeStateDigest();
    CHECK(
        ExpansionAutoplayPlanner_EnumerateLegalActions(
            CollectAction,
            &firstCount,
            NULL)
            == EXPANSION_AUTOPLAY_PLANNER_ENUMERATION_OK,
        "complete legal-action enumeration must succeed"
    );
    CHECK(stateBefore == RuntimeStateDigest(),
          "legal-action enumeration must not mutate unit, map, or RNG state");
    CHECK(firstCount > 0, "complete legal-action enumeration must produce actions");
    for (index = 0; index < (int)firstCount; index++)
    {
        kinds |= 1u << sEnumeratedActions[index].actionId;
        sequenceDigest = DigestBytes(
            sequenceDigest,
            &sEnumeratedActions[index],
            sizeof(sEnumeratedActions[index]));
        for (other = 0; other < index; other++)
        {
            CHECK(
                memcmp(
                    &sEnumeratedActions[index],
                    &sEnumeratedActions[other],
                    sizeof(sEnumeratedActions[index]))
                    != 0,
                "legal-action enumeration must not publish duplicates"
            );
        }
    }
    CHECK(
        (kinds & (1u << AI_ACTION_NONE))
            && (kinds & (1u << AI_ACTION_COMBAT))
            && (kinds & (1u << AI_ACTION_STAFF))
            && (kinds & (1u << AI_ACTION_USEITEM))
            && (kinds & (1u << AI_ACTION_PICK))
            && (kinds & (1u << AI_ACTION_DKSUMMON)),
        "complete enumeration must cover every declared action family"
    );
    CHECK(
        ExpansionAutoplayPlanner_EnumerateLegalActions(
            CollectAction,
            &secondCount,
            NULL)
            == EXPANSION_AUTOPLAY_PLANNER_ENUMERATION_OK,
        "repeated legal-action enumeration must succeed"
    );
    for (index = 0; index < (int)secondCount; index++)
        secondSequenceDigest = DigestBytes(
            secondSequenceDigest,
            &sEnumeratedActions[index],
            sizeof(sEnumeratedActions[index]));
    CHECK(firstCount == secondCount && sequenceDigest == secondSequenceDigest,
          "legal-action ordering must be deterministic");

    sClass.attributes = 0;
    sUnit.items[0] = 0;
    sUnit.items[1] = 0;
    sUnit.items[2] = 0;
    sAlly.pCharacterData = NULL;
    sEnemy.pCharacterData = NULL;
    return 0;
}

int main(void)
{
    struct AiDecision decision = { 0 };
    struct AiDecision original;
    const struct ExpansionAutoplayPlannerActionV2* action;
    u32 selectedOrdinal;
    u32 selectedTokenLo;
    u32 selectedTokenHi;
    u32 previousScenarioIdentity;
    u32 previousSeedIdentity;
    int index;

    gPlaySt.chapterIndex = 1;
    gPlaySt.chapterTurnNumber = 1;
    gActiveUnitId = 1;
    sCharacter.number = 1;
    sClass.number = 1;
    sUnit.pCharacterData = &sCharacter;
    sUnit.pClassData = &sClass;
    sUnit.index = 1;
    sUnit.level = 1;
    sUnit.maxHP = 20;
    sUnit.curHP = 20;
    gActiveUnit = &sUnit;
    gBmMapSize.x = 32;
    gBmMapSize.y = 17;
    for (index = 0; index < 17; index++)
    {
        int x;
        sMovementRows[index] = sMovementData[index];
        sUnitRows[index] = sUnitData[index];
        sTerrainRows[index] = sTerrainData[index];
        sFogRows[index] = sFogData[index];
        for (x = 0; x < 32; x++)
        {
            sMovementData[index][x] = 1;
            sUnitData[index][x] = 0;
            sTerrainData[index][x] = 1;
            sFogData[index][x] = 1;
        }
    }
    gBmMapMovement = sMovementRows;
    gBmMapUnit = sUnitRows;
    gBmMapTerrain = sTerrainRows;
    gBmMapFog = sFogRows;
    CHECK(TestCompleteEnumerator() == 0, "complete action enumerator test");

    gBmMapSize.x = 32;
    gBmMapSize.y = 17;
    sUnit.xPos = 0;
    sUnit.yPos = 0;
    sUnit.maxHP = 20;
    sUnit.curHP = 20;
    sClass.attributes = 0;
    for (index = 0; index < 17; index++)
    {
        int x;

        for (x = 0; x < 32; x++)
        {
            sMovementData[index][x] = 1;
            sUnitData[index][x] = 0;
            sTerrainData[index][x] = 1;
            sFogData[index][x] = 1;
        }
    }
    for (index = 1; index < 32; index++)
        sMovementData[16][index] = MAP_MOVEMENT_MAX + 1;
    sUnitData[0][0] = 1;
    ExpansionAutoplayPlanner_Reset();
    CHECK(
        gExpansionAutoplayPlannerObservation.state
            == EXPANSION_AUTOPLAY_PLANNER_STATE_DISABLED,
        "early reset must not publish stale READY identities"
    );
    ExpansionAutoplayPlanner_OnMapReady();
    CHECK(!ExpansionAutoplayPlanner_PollStart(),
          "idle poll without a command must publish READY");

    WriteCommand((enum ExpansionAutoplayPlannerCommandKind)99, 0, 0, 0, 0, 0, 0);
    CHECK(!ExpansionAutoplayPlanner_PollStart(), "unknown idle command must reject");
    CHECK(
        gExpansionAutoplayPlannerObservation.rejection
            == EXPANSION_AUTOPLAY_PLANNER_REJECTION_PROTOCOL_ERROR,
        "unknown idle command must report protocol error"
    );

    previousSeedIdentity =
        gExpansionAutoplayPlannerObservation.actualSeedIdentity;
    sSeeds[0] = 9;
    CHECK(
        !ExpansionAutoplayPlanner_PollStart()
            && gExpansionAutoplayPlannerObservation.actualSeedIdentity
                != previousSeedIdentity,
        "idle READY refresh must follow map/RNG initialization"
    );
    WriteCommand(EXPANSION_AUTOPLAY_PLANNER_COMMAND_START, 0, 0, 0, 0, 0, 0);
    gExpansionAutoplayPlannerCommand.expectedSeedIdentity =
        previousSeedIdentity;
    CHECK(
        !ExpansionAutoplayPlanner_PollStart()
            && gExpansionAutoplayPlannerObservation.rejection
                == EXPANSION_AUTOPLAY_PLANNER_REJECTION_PROTOCOL_ERROR,
        "a START prepared from an older READY seed must reject provenance"
    );
    sSeeds[0] = 1;
    ExpansionAutoplayPlanner_Reset();
    ExpansionAutoplayPlanner_OnMapReady();
    ExpansionAutoplayPlanner_PollStart();

    WriteCommand(EXPANSION_AUTOPLAY_PLANNER_COMMAND_START, 0, 0, 0, 0, 0, 0);
    gExpansionAutoplayPlannerCommand.expectedRomIdentity ^= 1;
    CHECK(
        !ExpansionAutoplayPlanner_PollStart()
            && gExpansionAutoplayPlannerObservation.rejection
                == EXPANSION_AUTOPLAY_PLANNER_REJECTION_PROTOCOL_ERROR,
        "same header with different build identity must reject provenance"
    );
    ExpansionAutoplayPlanner_Reset();
    ExpansionAutoplayPlanner_OnMapReady();
    ExpansionAutoplayPlanner_PollStart();

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
        "complete enumerator must retain the 512-candidate boundary"
    );
    CHECK(
        sizeof(gExpansionAutoplayPlannerObservation)
                <= EXPANSION_AUTOPLAY_PLANNER_PAGE_MAX_BYTES
            && gExpansionAutoplayPlannerObservation.pageCount == 24
            && gExpansionAutoplayPlannerObservation.pageKind
                == EXPANSION_AUTOPLAY_PLANNER_PAGE_SUMMARY
            && gExpansionAutoplayPlannerObservation.recordCount
                == EXPANSION_AUTOPLAY_PLANNER_SEMANTIC_FIELD_CAPACITY,
        "summary/map/unit/action pages must share the fixed-width boundary"
    );
    CHECK(
        gExpansionAutoplayPlannerObservation.fields[0].availability
                == EXPANSION_AUTOPLAY_PLANNER_AVAILABLE
            && gExpansionAutoplayPlannerObservation.fields[1].value != 0
            && gExpansionAutoplayPlannerObservation.fields[2].value == 0x010101,
        "summary page must expose actual map and active-unit semantics"
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
        23,
        0,
        0,
        0);
    CHECK(
        ExpansionAutoplayPlanner_PollDecision(&decision)
            == EXPANSION_AUTOPLAY_PLANNER_DECISION_WAIT,
        "valid PAGE command must publish another page"
    );
    CHECK(
        gExpansionAutoplayPlannerObservation.pageIndex == 23
            && gExpansionAutoplayPlannerObservation.pageKind
                == EXPANSION_AUTOPLAY_PLANNER_PAGE_ACTIONS
            && gExpansionAutoplayPlannerObservation.actionStartOrdinal == 504
            && gExpansionAutoplayPlannerObservation.actionCount == 8,
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
        23,
        0,
        0,
        0);
    CHECK(
        ExpansionAutoplayPlanner_PollDecision(&decision)
            == EXPANSION_AUTOPLAY_PLANNER_DECISION_WAIT,
        "page must republish after malformed command"
    );

    action = &gExpansionAutoplayPlannerObservation.actions[7];
    selectedOrdinal = gExpansionAutoplayPlannerObservation.actionStartOrdinal + 7;
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

    sMovementData[16][0] = MAP_MOVEMENT_MAX + 1;
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
                == EXPANSION_AUTOPLAY_PLANNER_DECISION_WAIT
            && gExpansionAutoplayPlannerObservation.rejection
                == EXPANSION_AUTOPLAY_PLANNER_REJECTION_ACTION_BECAME_ILLEGAL,
        "candidate-set mutation must reject before ordinal reconstruction"
    );
    sMovementData[16][0] = 1;
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
            && decision.xMove == 0
            && decision.yMove == 16,
        "last-page selection must map to its stable row-major candidate"
    );

    sConsumption = 4;
    gPlaySt.chapterIndex = 1;
    ExpansionAutoplayPlanner_RecordCampaignCheckpoint();
    previousSeedIdentity =
        gExpansionAutoplayPlannerObservation.actualSeedIdentity;
    previousScenarioIdentity =
        gExpansionAutoplayPlannerObservation.actualScenarioIdentity;
    ExpansionAutoplayPlanner_OnMapReset();
    CHECK(
        ExpansionAutoplayPlanner_IsActive()
            && gExpansionAutoplayPlannerCampaignCheckpoint.chapterIndex == 1
            && gExpansionAutoplayPlannerObservation.state
                == EXPANSION_AUTOPLAY_PLANNER_STATE_DISABLED,
        "chapter transition must preserve active campaign checkpoint"
    );
    gPlaySt.chapterIndex = 2;
    sSeeds[2]++;
    ExpansionAutoplayPlanner_OnMapReady();
    CHECK(
        gExpansionAutoplayPlannerObservation.actualSeedIdentity
            != previousSeedIdentity,
        "READY must publish identities after chapter map/RNG initialization"
    );
    CHECK(
        gExpansionAutoplayPlannerObservation.actualScenarioIdentity
            != previousScenarioIdentity,
        "scenario identity must bind the current chapter/map contract"
    );
    ExpansionAutoplayPlanner_RecordCampaignCheckpoint();
    CHECK(
        gExpansionAutoplayPlannerCampaignCheckpoint.chapterIndex == 2
            && gExpansionAutoplayPlannerCampaignCheckpoint.rngConsumption == 4
            && gExpansionAutoplayPlannerCampaignCheckpoint.semanticStateDigest != 0,
        "campaign checkpoint must be semantic and RNG-owned"
    );

    {
        u32 digest = gExpansionAutoplayPlannerCampaignCheckpoint.semanticStateDigest;
        sConvoy[99] = 2;
        ExpansionAutoplayPlanner_RecordCampaignCheckpoint();
        CHECK(
            digest != gExpansionAutoplayPlannerCampaignCheckpoint.semanticStateDigest,
            "convoy-only changes must alter the semantic checkpoint digest"
        );
    }

    ExpansionAutoplayPlanner_OfferDecision(&decision);
    for (index = 1;
         index < EXPANSION_AUTOPLAY_PLANNER_DECISION_TIMEOUT_FRAMES;
         index++)
    {
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
                == EXPANSION_AUTOPLAY_PLANNER_DECISION_WAIT,
            "non-idle malformed traffic must not evade the deadline"
        );
    }
    WriteCommand(
        EXPANSION_AUTOPLAY_PLANNER_COMMAND_PAGE,
        gExpansionAutoplayPlannerObservation.runId,
        gExpansionAutoplayPlannerObservation.observationId,
        0,
        0,
        0,
        0);
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
    ExpansionAutoplayPlanner_OnMapReady();
    ExpansionAutoplayPlanner_PollStart();
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
    ExpansionAutoplayPlanner_OnMapReady();
    ExpansionAutoplayPlanner_PollStart();
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
    WriteCommand(
        EXPANSION_AUTOPLAY_PLANNER_COMMAND_PAGE,
        gExpansionAutoplayPlannerObservation.runId,
        gExpansionAutoplayPlannerObservation.observationId,
        5,
        0,
        0,
        0);
    CHECK(
        ExpansionAutoplayPlanner_PollDecision(&decision)
            == EXPANSION_AUTOPLAY_PLANNER_DECISION_WAIT
            && gExpansionAutoplayPlannerObservation.pageKind
                == EXPANSION_AUTOPLAY_PLANNER_PAGE_ACTIONS,
        "wait candidate must be read through a typed action page"
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
        gExpansionAutoplayPlannerCampaignCheckpoint.semanticStateDigest != 0,
        "accepted wait token must enter the semantic action trace digest"
    );

    ExpansionAutoplayPlanner_Reset();
    ExpansionAutoplayPlanner_OnMapReady();
    ExpansionAutoplayPlanner_PollStart();
    WriteCommand(EXPANSION_AUTOPLAY_PLANNER_COMMAND_START, 0, 0, 0, 0, 0, 0);
    CHECK(ExpansionAutoplayPlanner_PollStart(), "zero-candidate run must start");
    for (index = 0; index < 17; index++)
    {
        int x;

        for (x = 0; x < 32; x++)
            sMovementData[index][x] = MAP_MOVEMENT_MAX + 1;
    }
    sMovementData[sUnit.yPos][sUnit.xPos] = 0;
    CHECK(
        ExpansionAutoplayPlanner_OfferDecision(&decision)
                == EXPANSION_AUTOPLAY_PLANNER_DECISION_EXHAUSTED
            && gExpansionAutoplayPlannerObservation.state
                == EXPANSION_AUTOPLAY_PLANNER_STATE_EXHAUSTED
            && gExpansionAutoplayPlannerObservation.rejection
                == EXPANSION_AUTOPLAY_PLANNER_REJECTION_CAPABILITY_UNAVAILABLE,
        "zero candidates must fail before publishing WAITING page zero"
    );

    puts("AUTOPLAY_PLANNER_HOST_TEST: PASS");
    return 0;
}
