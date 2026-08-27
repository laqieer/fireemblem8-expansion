#include "global.h"

#include <stdio.h>
#include <string.h>

#include "action_semantics.h"
#include "bm.h"
#include "bmcontainer.h"
#include "bmitem.h"
#include "bmmap.h"
#include "bmmind.h"
#include "bmunit.h"
#include "cp_common.h"
#include "constants/characters.h"
#include "constants/classes.h"
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
struct ActionData gActionData;
struct Unit* gActiveUnit;
u8 gActiveUnitId;
struct AiDecision gAiDecision;
struct Vec2 gBmMapSize;
u8** gBmMapMovement;
u8** gBmMapUnit;
u8** gBmMapTerrain;
u8** gBmMapFog;
u8 gSummonConfig[4][2] = {
    { CHARACTER_EWAN, CHARACTER_SUMMON_EWAN },
    { 0, 0 },
    { 0, 0 },
    { 0, 0 },
};

static u16 sSeeds[3] = { 1, 2, 3 };
static u32 sConsumption;
static int sControlRequests;
static int sRestoreRequests;
static struct CharacterData sCharacter;
static struct CharacterData sAllyCharacter;
static struct CharacterData sEnemyCharacter;
static struct CharacterData sSummonCharacter;
static struct ClassData sClass;
static struct ClassData sAllyClass;
static struct ClassData sEnemyClass;
static struct ClassData sSummonClass;
static struct Unit sUnit;
static struct Unit sAlly;
static struct Unit sEnemy;
static struct Unit sSummon;
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
    if (id == 3 && sSummon.pCharacterData != NULL)
        return &sSummon;
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
    switch (GetItemIndex(item))
    {
    case ITEM_STAFF_HEAL:
    case ITEM_STAFF_WARP:
    case ITEM_STAFF_TORCH:
    case ITEM_STAFF_REPAIR:
    case ITEM_STAFF_UNLOCK:
        return true;

    default:
        return false;
    }
}

int GetUnitItemUseReachBits(struct Unit* unit, int itemSlot)
{
    int item = unit->items[itemSlot];

    return GetItemIndex(item) == ITEM_STAFF_TORCH
        ? REACH_MAGBY2 : REACH_RANGE1;
}

int GetUnitKeyItemSlotForTerrain(struct Unit* unit, int terrain)
{
    int slot;

    for (slot = 0; slot < UNIT_ITEM_COUNT; slot++)
    {
        int item = GetItemIndex(unit->items[slot]);

        if ((UNIT_CATTRIBUTES(unit) & CA_THIEF)
            && item == ITEM_LOCKPICK)
            return slot;
        if (terrain == TERRAIN_CHEST_FULL
            && (item == ITEM_CHESTKEY
                || item == ITEM_CHESTKEY_BUNDLE))
            return slot;
        if (terrain == TERRAIN_DOOR && item == ITEM_DOORKEY)
            return slot;
    }
    return -1;
}

int GetItemAttributes(int item)
{
    if (GetItemIndex(item) == ITEM_SWORD_IRON)
        return IA_WEAPON;
    if (CanUnitUseStaff(gActiveUnit, item))
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
    if (GetItemIndex(item) == ITEM_STAFF_WARP
        || GetItemIndex(item) == ITEM_STAFF_TORCH)
        return 0;
    if (GetItemIndex(item) == ITEM_STAFF_UNLOCK)
        return 2;
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
    return gBmMapTerrain[y][x] == TERRAIN_DOOR
        || gBmMapTerrain[y][x] == TERRAIN_BRIDGE_14;
}

s8 IsItemHammernable(int item)
{
    return item != 0 && (item & 0xFF00) != 0xFF00;
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
    digest = DigestBytes(digest, &sSummon, sizeof(sSummon));
    digest = DigestBytes(digest, sSeeds, sizeof(sSeeds));
    digest = DigestBytes(digest, &sConsumption, sizeof(sConsumption));
    digest = DigestBytes(digest, sMovementData, sizeof(sMovementData));
    digest = DigestBytes(digest, sUnitData, sizeof(sUnitData));
    digest = DigestBytes(digest, sTerrainData, sizeof(sTerrainData));
    digest = DigestBytes(digest, sFogData, sizeof(sFogData));
    return digest;
}

static void ResetActionFixture(int width, int height)
{
    int y;
    int x;

    for (y = 0; y < 17; y++)
    {
        for (x = 0; x < 32; x++)
        {
            sMovementData[y][x] = MAP_MOVEMENT_MAX + 1;
            sUnitData[y][x] = 0;
            sTerrainData[y][x] = 1;
            sFogData[y][x] = 1;
        }
    }
    memset(&sUnit, 0, sizeof(sUnit));
    memset(&sAlly, 0, sizeof(sAlly));
    memset(&sEnemy, 0, sizeof(sEnemy));
    memset(&sSummon, 0, sizeof(sSummon));
    memset(&gActionData, 0, sizeof(gActionData));
    sCharacter.number = 1;
    sClass.number = 1;
    sClass.attributes = 0;
    sUnit.pCharacterData = &sCharacter;
    sUnit.pClassData = &sClass;
    sUnit.index = 1;
    sUnit.xPos = 2;
    sUnit.yPos = 2;
    sUnit.maxHP = 20;
    sUnit.curHP = 20;
    gActiveUnit = &sUnit;
    gActiveUnitId = 1;
    gBmMapSize.x = width;
    gBmMapSize.y = height;
    sMovementData[2][2] = 0;
    sUnitData[2][2] = 1;
    gPlaySt.chapterVisionRange = 3;
    gSummonConfig[0][0] = CHARACTER_EWAN;
    gSummonConfig[0][1] = CHARACTER_SUMMON_EWAN;
    gSummonConfig[1][0] = 0;
    gSummonConfig[1][1] = 0;
}

static int CountActionId(u32 count, int actionId)
{
    int result = 0;
    int index;

    for (index = 0; index < (int)count; index++)
        if (sEnumeratedActions[index].actionId == actionId)
            result++;
    return result;
}

static int TestCoordinateActionFamilies(void)
{
    u32 count;
    struct AiDecision first;
    struct AiDecision second;
    int index;

    ResetActionFixture(6, 6);
    sUnit.items[0] = ITEM_STAFF_TORCH;
    count = 0;
    CHECK(
        ExpansionAutoplayPlanner_EnumerateLegalActions(
            CollectAction,
            &count,
            NULL)
            == EXPANSION_AUTOPLAY_PLANNER_ENUMERATION_OK,
        "Torch enumeration must succeed"
    );
    first.actionPerformed = false;
    second.actionPerformed = false;
    for (index = 0; index < (int)count; index++)
    {
        struct AiDecision* candidate = &sEnumeratedActions[index];

        if (candidate->actionId != AI_ACTION_STAFF
            || candidate->itemSlot != 0)
            continue;
        CHECK(
            ActionSemantics_IsStandingReachPosition(
                gActiveUnit,
                candidate->xMove,
                candidate->yMove,
                REACH_MAGBY2,
                candidate->xTarget,
                candidate->yTarget),
            "every Torch candidate must target a legal bounded tile"
        );
        if (!first.actionPerformed)
            first = *candidate;
        else if (candidate->xTarget != first.xTarget
            || candidate->yTarget != first.yTarget)
        {
            second = *candidate;
            break;
        }
    }
    CHECK(first.actionPerformed && second.actionPerformed,
          "Torch must enumerate multiple distinct target tiles");
    CHECK(ExpansionAutoplayPlanner_PrepareActionData(&first)
              && gActionData.xOther == first.xTarget
              && gActionData.yOther == first.yTarget,
          "Torch lowering must use the first selected coordinate");
    CHECK(ExpansionAutoplayPlanner_PrepareActionData(&second)
              && gActionData.xOther == second.xTarget
              && gActionData.yOther == second.yTarget,
          "Torch lowering must use the second selected coordinate");
    first.xTarget = 5;
    first.yTarget = 5;
    CHECK(!ExpansionAutoplayPlanner_PrepareActionData(&first),
          "stale default Torch coordinate must reject");
    gPlaySt.chapterVisionRange = 0;
    CHECK(!ExpansionAutoplayPlanner_PrepareActionData(&second),
          "Torch must revalidate live fog capability");

    ResetActionFixture(7, 7);
    sUnit.items[0] = ITEM_STAFF_WARP;
    sAllyCharacter.number = 2;
    sAllyClass.number = 2;
    sAlly.pCharacterData = &sAllyCharacter;
    sAlly.pClassData = &sAllyClass;
    sAlly.index = 2;
    sAlly.xPos = 3;
    sAlly.yPos = 2;
    sAlly.maxHP = 20;
    sAlly.curHP = 20;
    sUnitData[2][3] = 2;
    count = 0;
    ExpansionAutoplayPlanner_EnumerateLegalActions(
        CollectAction,
        &count,
        NULL);
    first.actionPerformed = false;
    second.actionPerformed = false;
    for (index = 0; index < (int)count; index++)
    {
        struct AiDecision* candidate = &sEnumeratedActions[index];

        if (candidate->actionId != AI_ACTION_STAFF
            || candidate->targetId != 2)
            continue;
        if (!first.actionPerformed)
            first = *candidate;
        else if (candidate->xTarget != first.xTarget
            || candidate->yTarget != first.yTarget)
        {
            second = *candidate;
            break;
        }
    }
    CHECK(first.actionPerformed && second.actionPerformed,
          "Warp must enumerate multiple legal destinations");
    CHECK(ExpansionAutoplayPlanner_PrepareActionData(&first)
              && gActionData.xOther == first.xTarget
              && gActionData.yOther == first.yTarget,
          "Warp lowering must preserve the first selected destination");
    CHECK(ExpansionAutoplayPlanner_PrepareActionData(&second)
              && gActionData.xOther == second.xTarget
              && gActionData.yOther == second.yTarget,
          "Warp lowering must preserve the second selected destination");
    first.xTarget = 6;
    first.yTarget = 6;
    CHECK(!ExpansionAutoplayPlanner_PrepareActionData(&first),
          "stale Warp coordinate must reject");
    sUnitData[second.yTarget][second.xTarget] = 0x81;
    CHECK(!ExpansionAutoplayPlanner_PrepareActionData(&second),
          "occupied Warp destination must fail revalidation");

    ResetActionFixture(6, 6);
    sUnit.items[0] = ITEM_STAFF_UNLOCK;
    sTerrainData[1][2] = TERRAIN_DOOR;
    sTerrainData[2][4] = TERRAIN_DOOR;
    count = 0;
    ExpansionAutoplayPlanner_EnumerateLegalActions(
        CollectAction,
        &count,
        NULL);
    first.actionPerformed = false;
    second.actionPerformed = false;
    for (index = 0; index < (int)count; index++)
    {
        struct AiDecision* candidate = &sEnumeratedActions[index];

        if (candidate->actionId != AI_ACTION_STAFF)
            continue;
        if (!first.actionPerformed)
            first = *candidate;
        else
        {
            second = *candidate;
            break;
        }
    }
    CHECK(first.actionPerformed && second.actionPerformed,
          "Unlock must enumerate multiple closed doors");
    CHECK(ExpansionAutoplayPlanner_PrepareActionData(&first)
              && gActionData.xOther == first.xTarget
              && gActionData.yOther == first.yTarget,
          "Unlock lowering must preserve the first selected door");
    CHECK(ExpansionAutoplayPlanner_PrepareActionData(&second)
              && gActionData.xOther == second.xTarget
              && gActionData.yOther == second.yTarget,
          "Unlock lowering must preserve the second selected door");
    sTerrainData[first.yTarget][first.xTarget] = 1;
    CHECK(!ExpansionAutoplayPlanner_PrepareActionData(&first),
          "opened or wrong Unlock coordinate must reject");

    ResetActionFixture(6, 6);
    sUnit.items[0] = ITEM_STAFF_REPAIR;
    sAllyCharacter.number = 2;
    sAllyClass.number = 2;
    sAlly.pCharacterData = &sAllyCharacter;
    sAlly.pClassData = &sAllyClass;
    sAlly.index = 2;
    sAlly.xPos = 3;
    sAlly.yPos = 2;
    sAlly.maxHP = 20;
    sAlly.curHP = 20;
    sAlly.items[0] = 0x0101;
    sAlly.items[1] = 0x0202;
    sUnitData[2][3] = 2;
    count = 0;
    ExpansionAutoplayPlanner_EnumerateLegalActions(
        CollectAction,
        &count,
        NULL);
    first.actionPerformed = false;
    second.actionPerformed = false;
    for (index = 0; index < (int)count; index++)
    {
        struct AiDecision* candidate = &sEnumeratedActions[index];

        if (candidate->actionId != AI_ACTION_STAFF
            || candidate->targetId != 2)
            continue;
        if (candidate->unk04 == 0)
            first = *candidate;
        else if (candidate->unk04 == 1)
            second = *candidate;
    }
    CHECK(first.actionPerformed && second.actionPerformed,
          "Hammerne must enumerate each repairable target slot");
    CHECK(ExpansionAutoplayPlanner_PrepareActionData(&first)
              && gActionData.trapType == 0,
          "Hammerne lowering must preserve target slot zero");
    CHECK(ExpansionAutoplayPlanner_PrepareActionData(&second)
              && gActionData.trapType == 1,
          "Hammerne lowering must preserve target slot one");
    sAlly.items[1] = 0xFF02;
    CHECK(!ExpansionAutoplayPlanner_PrepareActionData(&second),
          "stale Hammerne target slot must reject");

    ResetActionFixture(6, 6);
    sTerrainData[2][2] = TERRAIN_CHEST_FULL;
    sTerrainData[2][3] = TERRAIN_DOOR;
    sClass.number = CLASS_ROGUE;
    count = 0;
    ExpansionAutoplayPlanner_EnumerateLegalActions(
        CollectAction,
        &count,
        NULL);
    CHECK(count == 2
              && sEnumeratedActions[0].itemSlot == 0xFF
              && sEnumeratedActions[1].itemSlot == 0xFF,
          "Rogue Pick must enumerate chest and door without a key");

    sClass.number = 1;
    sClass.attributes = CA_THIEF;
    sUnit.items[0] = ITEM_LOCKPICK;
    count = 0;
    ExpansionAutoplayPlanner_EnumerateLegalActions(
        CollectAction,
        &count,
        NULL);
    CHECK(count == 2
              && sEnumeratedActions[0].itemSlot == 0
              && sEnumeratedActions[1].itemSlot == 0,
          "non-Rogue thief Pick must bind the Lockpick slot");
    CHECK(ExpansionAutoplayPlanner_PrepareActionData(&sEnumeratedActions[0])
              && gActionData.itemSlotIndex == 0,
          "Pick lowering must preserve consumable key identity");
    sUnit.items[0] = 0;
    CHECK(!ExpansionAutoplayPlanner_PrepareActionData(&sEnumeratedActions[0]),
          "consumed Pick key must fail revalidation");
    count = 0;
    ExpansionAutoplayPlanner_EnumerateLegalActions(
        CollectAction,
        &count,
        NULL);
    CHECK(count == 0,
          "thief without Lockpick or key must publish no Pick action");

    sUnit.items[0] = ITEM_CHESTKEY;
    sUnit.items[1] = ITEM_DOORKEY;
    count = 0;
    ExpansionAutoplayPlanner_EnumerateLegalActions(
        CollectAction,
        &count,
        NULL);
    CHECK(count == 2
             && sEnumeratedActions[0].xTarget == 2
             && sEnumeratedActions[0].yTarget == 2
             && sEnumeratedActions[0].itemSlot == 0
             && sEnumeratedActions[1].xTarget == 3
             && sEnumeratedActions[1].yTarget == 2
             && sEnumeratedActions[1].itemSlot == 1,
          "chest and door actions must bind their applicable key slots");
    CHECK(ExpansionAutoplayPlanner_PrepareActionData(&sEnumeratedActions[1])
             && gActionData.itemSlotIndex == 1,
          "door-key lowering must preserve the selected consumable slot");

    sTerrainData[2][2] = 1;
    sTerrainData[2][3] = TERRAIN_BRIDGE_14;
    sUnit.items[0] = ITEM_DOORKEY;
    sUnit.items[1] = 0;
    count = 0;
    ExpansionAutoplayPlanner_EnumerateLegalActions(
        CollectAction,
        &count,
        NULL);
    CHECK(count == 0,
          "Door Key must not substitute for a bridge Lockpick");
    sUnit.items[0] = ITEM_LOCKPICK;
    count = 0;
    ExpansionAutoplayPlanner_EnumerateLegalActions(
        CollectAction,
        &count,
        NULL);
    CHECK(count == 1
             && sEnumeratedActions[0].xTarget == 3
             && sEnumeratedActions[0].yTarget == 2
             && sEnumeratedActions[0].itemSlot == 0,
          "thief Lockpick must retain the normal bridge path");
    return 0;
}

static int TestSummonActionFamily(void)
{
    struct AiDecision first;
    struct AiDecision second;
    struct AiDecision adversary;
    u32 count;
    u32 stateBefore;
    int summonCount;
    int index;

    ResetActionFixture(6, 6);
    sCharacter.number = CHARACTER_EWAN;
    sClass.attributes = CA_SUMMON;
    stateBefore = RuntimeStateDigest();
    count = 0;
    CHECK(
        ExpansionAutoplayPlanner_EnumerateLegalActions(
            CollectAction,
            &count,
            NULL)
            == EXPANSION_AUTOPLAY_PLANNER_ENUMERATION_OK,
        "normal Summon enumeration must succeed"
    );
    CHECK(stateBefore == RuntimeStateDigest(),
          "normal Summon enumeration must not mutate runtime state");
    first.actionPerformed = false;
    second.actionPerformed = false;
    summonCount = 0;
    for (index = 0; index < (int)count; index++)
    {
        struct AiDecision* candidate = &sEnumeratedActions[index];

        if (candidate->actionId != AI_ACTION_SUMMON)
            continue;
        summonCount++;
        CHECK(
            ActionSemantics_IsNormalSummonTarget(
                gActiveUnit,
                candidate->xMove,
                candidate->yMove,
                candidate->xTarget,
                candidate->yTarget),
            "every normal Summon candidate must use a canonical target tile"
        );
        if (!first.actionPerformed)
            first = *candidate;
        else if (!second.actionPerformed)
            second = *candidate;
    }
    CHECK(summonCount == 4
              && first.actionPerformed
              && second.actionPerformed,
          "normal Summon must enumerate all four distinct adjacent tiles");
    CHECK(ExpansionAutoplayPlanner_PrepareActionData(&first)
              && gActionData.xOther == first.xTarget
              && gActionData.yOther == first.yTarget,
          "normal Summon must lower its first selected tile");
    CHECK(ExpansionAutoplayPlanner_PrepareActionData(&second)
              && gActionData.xOther == second.xTarget
              && gActionData.yOther == second.yTarget,
          "normal Summon must lower its second selected tile");

    sUnitData[first.yTarget][first.xTarget] = 2;
    CHECK(!ExpansionAutoplayPlanner_PrepareActionData(&first),
          "occupied normal Summon tile must fail live revalidation");
    sUnitData[first.yTarget][first.xTarget] = 0;
    adversary = first;
    adversary.xTarget = 5;
    adversary.yTarget = 5;
    CHECK(!ExpansionAutoplayPlanner_PrepareActionData(&adversary),
          "non-adjacent normal Summon tile must reject");
    adversary = first;
    adversary.actionId = AI_ACTION_DKSUMMON;
    CHECK(!ExpansionAutoplayPlanner_PrepareActionData(&adversary),
          "normal Summon candidate must not lower as dark summon");

    gSummonConfig[0][0] = 0;
    gSummonConfig[0][1] = 0;
    count = 0;
    ExpansionAutoplayPlanner_EnumerateLegalActions(
        CollectAction,
        &count,
        NULL);
    CHECK(CountActionId(count, AI_ACTION_SUMMON) == 0,
          "missing gSummonConfig entry must publish no normal Summon");

    gSummonConfig[0][0] = CHARACTER_EWAN;
    gSummonConfig[0][1] = CHARACTER_SUMMON_EWAN;
    sSummonCharacter.number = CHARACTER_SUMMON_EWAN;
    sSummonClass.number = CLASS_PHANTOM;
    sSummon.pCharacterData = &sSummonCharacter;
    sSummon.pClassData = &sSummonClass;
    sSummon.index = 3;
    sSummon.state = 0;
    count = 0;
    ExpansionAutoplayPlanner_EnumerateLegalActions(
        CollectAction,
        &count,
        NULL);
    CHECK(CountActionId(count, AI_ACTION_SUMMON) == 0,
          "available existing summon must block another normal Summon");

    sSummon.state = US_NOT_DEPLOYED;
    count = 0;
    ExpansionAutoplayPlanner_EnumerateLegalActions(
        CollectAction,
        &count,
        NULL);
    CHECK(CountActionId(count, AI_ACTION_SUMMON) == 4
              && sSummon.state == US_NOT_DEPLOYED,
          "unavailable existing summon must be reusable without enumeration mutation");

    sUnit.state = US_HAS_MOVED;
    count = 0;
    ExpansionAutoplayPlanner_EnumerateLegalActions(
        CollectAction,
        &count,
        NULL);
    CHECK(CountActionId(count, AI_ACTION_SUMMON) == 0,
          "moved summoner must publish no normal Summon");
    sUnit.state = 0;
    sClass.attributes = 0;
    count = 0;
    ExpansionAutoplayPlanner_EnumerateLegalActions(
        CollectAction,
        &count,
        NULL);
    CHECK(CountActionId(count, AI_ACTION_SUMMON) == 0,
          "unit without CA_SUMMON must publish no normal Summon");

    ResetActionFixture(6, 6);
    sClass.number = CLASS_DEMON_KING;
    count = 0;
    ExpansionAutoplayPlanner_EnumerateLegalActions(
        CollectAction,
        &count,
        NULL);
    CHECK(CountActionId(count, AI_ACTION_DKSUMMON) == 1
              && CountActionId(count, AI_ACTION_SUMMON) == 0,
          "Demon King must retain one distinct dark-summon action");
    first.actionPerformed = false;
    for (index = 0; index < (int)count; index++)
    {
        if (sEnumeratedActions[index].actionId != AI_ACTION_DKSUMMON)
            continue;
        first = sEnumeratedActions[index];
        break;
    }
    CHECK(first.actionPerformed
              && first.xTarget == 0
              && first.yTarget == 0
              && ExpansionAutoplayPlanner_PrepareActionData(&first),
          "dark summon must retain its coordinate-free executor contract");
    first.xTarget = 1;
    CHECK(!ExpansionAutoplayPlanner_PrepareActionData(&first),
          "dark summon must reject normal Summon coordinates");
    first.xTarget = 0;
    first.actionId = AI_ACTION_SUMMON;
    CHECK(!ExpansionAutoplayPlanner_PrepareActionData(&first),
          "dark summon candidate must not lower as normal Summon");
    return 0;
}

static int TestHammerneWireIdentity(void)
{
    struct AiDecision decision = { 0 };
    struct ExpansionAutoplayPlannerActionV2 first;
    struct ExpansionAutoplayPlannerActionV2 second;
    u32 actionPage;

    ResetActionFixture(6, 6);
    sUnit.items[0] = ITEM_STAFF_REPAIR;
    sAllyCharacter.number = 2;
    sAllyClass.number = 2;
    sAlly.pCharacterData = &sAllyCharacter;
    sAlly.pClassData = &sAllyClass;
    sAlly.index = 2;
    sAlly.xPos = 3;
    sAlly.yPos = 2;
    sAlly.maxHP = 20;
    sAlly.curHP = 20;
    sAlly.items[0] = 0x0101;
    sAlly.items[1] = 0x0202;
    sUnitData[2][3] = 2;

    ExpansionAutoplayPlanner_Reset();
    ExpansionAutoplayPlanner_OnMapReady();
    ExpansionAutoplayPlanner_PollStart();
    WriteCommand(
        EXPANSION_AUTOPLAY_PLANNER_COMMAND_START,
        0,
        0,
        0,
        0,
        0,
        0);
    CHECK(ExpansionAutoplayPlanner_PollStart(),
          "Hammerne wire run must start");
    CHECK(
        ExpansionAutoplayPlanner_OfferDecision(&decision)
            == EXPANSION_AUTOPLAY_PLANNER_DECISION_WAIT,
        "Hammerne wire run must publish candidates"
    );
    actionPage = gExpansionAutoplayPlannerObservation.pageCount - 1;
    WriteCommand(
        EXPANSION_AUTOPLAY_PLANNER_COMMAND_PAGE,
        gExpansionAutoplayPlannerObservation.runId,
        gExpansionAutoplayPlannerObservation.observationId,
        actionPage,
        0,
        0,
        0);
    CHECK(
        ExpansionAutoplayPlanner_PollDecision(&decision)
                == EXPANSION_AUTOPLAY_PLANNER_DECISION_WAIT
            && gExpansionAutoplayPlannerObservation.pageKind
                == EXPANSION_AUTOPLAY_PLANNER_PAGE_ACTIONS
            && gExpansionAutoplayPlannerObservation.count.actionCount == 2,
        "Hammerne candidates must traverse the fixed action page"
    );
    first = gExpansionAutoplayPlannerObservation.payload.actions[0];
    second = gExpansionAutoplayPlannerObservation.payload.actions[1];
    CHECK(
        first.itemSlot == 0x0000
            && second.itemSlot == 0x0100
            && (first.tokenLo != second.tokenLo
                || first.tokenHi != second.tokenHi),
        "Hammerne target slot must be packed and token-bound"
    );
    WriteCommand(
        EXPANSION_AUTOPLAY_PLANNER_COMMAND_COMMIT,
        gExpansionAutoplayPlannerObservation.runId,
        gExpansionAutoplayPlannerObservation.observationId,
        0,
        gExpansionAutoplayPlannerObservation.start.actionStartOrdinal + 1,
        first.tokenLo,
        first.tokenHi);
    CHECK(
        ExpansionAutoplayPlanner_PollDecision(&decision)
                == EXPANSION_AUTOPLAY_PLANNER_DECISION_WAIT
            && gExpansionAutoplayPlannerObservation.rejection
                == EXPANSION_AUTOPLAY_PLANNER_REJECTION_TOKEN_MISMATCH,
        "Hammerne token for another inventory slot must reject"
    );
    WriteCommand(
        EXPANSION_AUTOPLAY_PLANNER_COMMAND_COMMIT,
        gExpansionAutoplayPlannerObservation.runId,
        gExpansionAutoplayPlannerObservation.observationId,
        0,
        gExpansionAutoplayPlannerObservation.start.actionStartOrdinal + 1,
        second.tokenLo,
        second.tokenHi);
    CHECK(
        ExpansionAutoplayPlanner_PollDecision(&decision)
                == EXPANSION_AUTOPLAY_PLANNER_DECISION_ACCEPTED
            && decision.unk04 == 1
            && ExpansionAutoplayPlanner_PrepareActionData(&decision)
            && gActionData.trapType == 1,
        "matching Hammerne slot token must lower the selected slot"
    );
    return 0;
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
    sCharacter.number = CHARACTER_EWAN;
    sClass.attributes = CA_STEAL | CA_SUMMON;
    sClass.number = CLASS_ROGUE;
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
            && (kinds & (1u << AI_ACTION_SUMMON)),
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
    sClass.number = 1;
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
    CHECK(TestCoordinateActionFamilies() == 0,
          "coordinate-sensitive action family test");
    CHECK(TestSummonActionFamily() == 0,
          "normal and dark summon action family test");

    gBmMapSize.x = 32;
    gBmMapSize.y = 17;
    sUnit.xPos = 0;
    sUnit.yPos = 0;
    sUnit.maxHP = 20;
    sUnit.curHP = 20;
    sCharacter.number = 1;
    sClass.number = 1;
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
            && gExpansionAutoplayPlannerObservation.count.recordCount
                == EXPANSION_AUTOPLAY_PLANNER_SEMANTIC_FIELD_CAPACITY,
        "summary/map/unit/action pages must share the fixed-width boundary"
    );
    CHECK(
        gExpansionAutoplayPlannerObservation.payload.fields[0].availability
                == EXPANSION_AUTOPLAY_PLANNER_AVAILABLE
            && gExpansionAutoplayPlannerObservation.payload.fields[1].value != 0
            && gExpansionAutoplayPlannerObservation.payload.fields[2].value
                == 0x010101,
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
            && gExpansionAutoplayPlannerObservation.start.actionStartOrdinal
                == 504
            && gExpansionAutoplayPlannerObservation.count.actionCount == 8,
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

    action = &gExpansionAutoplayPlannerObservation.payload.actions[7];
    selectedOrdinal =
        gExpansionAutoplayPlannerObservation.start.actionStartOrdinal + 7;
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
        u32 digest =
            gExpansionAutoplayPlannerCampaignCheckpoint.semanticStateDigest;
        gPlaySt.chapterModeIndex++;
        ExpansionAutoplayPlanner_RecordCampaignCheckpoint();
        CHECK(
            gExpansionAutoplayPlannerCampaignCheckpoint.chapterMode
                    == gPlaySt.chapterModeIndex
                && digest
                    != gExpansionAutoplayPlannerCampaignCheckpoint
                        .semanticStateDigest,
            "route-only changes must alter the semantic checkpoint digest"
        );
    }

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
            && gExpansionAutoplayPlannerCampaignCheckpoint.magic == 0
            && gExpansionAutoplayPlannerCampaignCheckpoint.version == 0
            && gExpansionAutoplayPlannerCampaignCheckpoint.byteSize == 0
            && gExpansionAutoplayPlannerCampaignCheckpoint.semanticStateDigest
                == 0
            && sRestoreRequests == 1,
        "deadline must clear checkpoint before restoring player control"
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
        gExpansionAutoplayPlannerCampaignCheckpoint.magic == 0
            && gExpansionAutoplayPlannerCampaignCheckpoint.chapterIndex == 0
            && gExpansionAutoplayPlannerCampaignCheckpoint.semanticStateDigest
                == 0,
        "new START must not expose a checkpoint from the timed-out run"
    );
    CHECK(
        ExpansionAutoplayPlanner_OfferDecision(&decision)
            == EXPANSION_AUTOPLAY_PLANNER_DECISION_WAIT,
        "second run must publish before cancellation"
    );
    ExpansionAutoplayPlanner_RecordCampaignCheckpoint();
    CHECK(
        gExpansionAutoplayPlannerCampaignCheckpoint.magic
            == EXPANSION_AUTOPLAY_PLANNER_MAGIC,
        "explicit-cancel negative must begin with a valid checkpoint"
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
            && gExpansionAutoplayPlannerCampaignCheckpoint.magic == 0
            && gExpansionAutoplayPlannerCampaignCheckpoint.version == 0
            && gExpansionAutoplayPlannerCampaignCheckpoint.byteSize == 0
            && gExpansionAutoplayPlannerCampaignCheckpoint.semanticStateDigest
                == 0
            && sRestoreRequests == 2,
        "explicit cancellation must clear checkpoint before restoration"
    );

    ExpansionAutoplayPlanner_Reset();
    ExpansionAutoplayPlanner_OnMapReady();
    ExpansionAutoplayPlanner_PollStart();
    WriteCommand(EXPANSION_AUTOPLAY_PLANNER_COMMAND_START, 0, 0, 0, 0, 0, 0);
    CHECK(ExpansionAutoplayPlanner_PollStart(), "wait-candidate run must start");
    CHECK(
        gExpansionAutoplayPlannerCampaignCheckpoint.magic == 0
            && gExpansionAutoplayPlannerCampaignCheckpoint.runId == 0
            && gExpansionAutoplayPlannerCampaignCheckpoint.chapterIndex == 0,
        "START after explicit cancel must retain no prior checkpoint"
    );
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
    action = &gExpansionAutoplayPlannerObservation.payload.actions[0];
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
        gExpansionAutoplayPlannerObservation.start.actionStartOrdinal,
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

    CHECK(TestHammerneWireIdentity() == 0,
          "Hammerne fixed-width wire identity test");

    ResetActionFixture(32, 17);
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
