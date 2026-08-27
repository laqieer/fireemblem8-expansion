#include "global.h"

#include <stdio.h>
#include <string.h>

#include "action_semantics.h"
#include "bm.h"
#include "bmcontainer.h"
#include "bmitem.h"
#include "bmmap.h"
#include "bmmind.h"
#include "bmtrick.h"
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
struct ExpansionAutoplayTelemetry gExpansionAutoplayTelemetry;
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
static struct Trap sTraps[TRAP_MAX_COUNT];
static struct Unit sMaxUnits[132];
static u8 sPermanentFlags[256];
static u8 sChapterFlags[256];
static int sPermanentFlagSize = 8;
static int sChapterFlagSize = 8;
static bool sFlagPointersAvailable = true;
static bool sConvoyAvailable = true;
static bool sUseMaxUnits;
static int sMagRange = 1;
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
    int ordinal;

    if (sUseMaxUnits)
    {
        if (id >= 1 && id <= 0x3E)
            ordinal = id - 1;
        else if (id >= 0x41 && id <= 0x54)
            ordinal = 62 + id - 0x41;
        else if (id >= 0x81 && id <= 0xB2)
            ordinal = 82 + id - 0x81;
        else
            return NULL;
        return &sMaxUnits[ordinal];
    }
    if (id == 1)
        return &sUnit;
    if (id == (u8)sAlly.index && sAlly.pCharacterData != NULL)
        return &sAlly;
    if (id == (u8)sSummon.index && sSummon.pCharacterData != NULL)
        return &sSummon;
    if (id == (u8)sEnemy.index && sEnemy.pCharacterData != NULL)
        return &sEnemy;
    return NULL;
}

u8* GetPermanentFlagBits(void)
{
    return sFlagPointersAvailable ? sPermanentFlags : NULL;
}

int GetPermanentFlagBitsSize(void)
{
    return sPermanentFlagSize;
}

u8* GetChapterFlagBits(void)
{
    return sFlagPointersAvailable ? sChapterFlags : NULL;
}

int GetChapterFlagBitsSize(void)
{
    return sChapterFlagSize;
}

u16* GetConvoyItemArray(void)
{
    return sConvoyAvailable ? sConvoy : NULL;
}

s8 AreUnitsAllied(int left, int right)
{
    return (left & 0x80) == (right & 0x80);
}

s8 IsSameAllegiance(int left, int right)
{
    return (left & 0xC0) == (right & 0xC0);
}

int GetCurrentPhase(void)
{
    return FACTION_BLUE;
}

int GetUnitCurrentHp(struct Unit* unit)
{
    return unit->curHP;
}

int GetUnitMaxHp(struct Unit* unit)
{
    return unit->maxHP;
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
    case ITEM_STAFF_FORTIFY:
    case ITEM_STAFF_LATONA:
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
    return sMagRange;
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

struct Trap* GetTrap(int id)
{
    return &sTraps[id];
}

struct Trap* GetTrapAt(int x, int y)
{
    int index;

    for (index = 0; index < TRAP_MAX_COUNT; index++)
    {
        if (sTraps[index].type == TRAP_NONE)
            break;
        if (sTraps[index].xPos == x && sTraps[index].yPos == y)
            return &sTraps[index];
    }
    return NULL;
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
    const u32* token)
{
    memset(
        (void*)&gExpansionAutoplayPlannerCommand,
        0,
        sizeof(gExpansionAutoplayPlannerCommand));
    gExpansionAutoplayPlannerCommand.magic = EXPANSION_AUTOPLAY_PLANNER_MAGIC;
    gExpansionAutoplayPlannerCommand.version = EXPANSION_AUTOPLAY_PLANNER_PROTOCOL_VERSION;
    gExpansionAutoplayPlannerCommand.byteSize =
        sizeof(struct ExpansionAutoplayPlannerCommandV2);
    gExpansionAutoplayPlannerCommand.runId = runId;
    gExpansionAutoplayPlannerCommand.observationId = observationId;
    gExpansionAutoplayPlannerCommand.pageIndex = pageIndex;
    gExpansionAutoplayPlannerCommand.actionOrdinal = ordinal;
    if (token != NULL)
    {
        gExpansionAutoplayPlannerCommand.payload.commit.token0 = token[0];
        gExpansionAutoplayPlannerCommand.payload.commit.token1 = token[1];
        gExpansionAutoplayPlannerCommand.payload.commit.token2 = token[2];
        gExpansionAutoplayPlannerCommand.payload.commit.token3 = token[3];
    }
    else if (kind == EXPANSION_AUTOPLAY_PLANNER_COMMAND_START)
    {
        gExpansionAutoplayPlannerCommand.payload.start.expectedRomIdentity =
            gExpansionAutoplayPlannerObservation.actualRomIdentity;
        gExpansionAutoplayPlannerCommand.payload.start.expectedConfigIdentity =
            gExpansionAutoplayPlannerObservation.actualConfigIdentity;
        gExpansionAutoplayPlannerCommand.payload.start.expectedScenarioIdentity =
            gExpansionAutoplayPlannerObservation.actualScenarioIdentity;
        gExpansionAutoplayPlannerCommand.payload.start.expectedSeedIdentity =
            gExpansionAutoplayPlannerObservation.actualSeedIdentity;
    }
    gExpansionAutoplayPlannerCommand.kind = kind;
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
    memset(sTraps, 0, sizeof(sTraps));
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
    sMagRange = 1;
    sFlagPointersAvailable = true;
    sConvoyAvailable = true;
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
            &count)
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

static int TestSnagActionFamily(void)
{
    u32 count = 0;
    struct AiDecision* firstSnag;

    ResetActionFixture(6, 6);
    sUnit.items[0] = ITEM_SWORD_IRON | (30 << 8);
    sEnemy.pCharacterData = &sEnemyCharacter;
    sEnemy.pClassData = &sEnemyClass;
    sEnemy.index = 0x81;
    sEnemy.xPos = 1;
    sEnemy.yPos = 2;
    sEnemy.maxHP = 20;
    sEnemy.curHP = 20;
    sTraps[0].type = TRAP_OBSTACLE;
    sTraps[0].xPos = 3;
    sTraps[0].yPos = 2;
    sTraps[0].extra = 20;
    sTraps[1].type = TRAP_OBSTACLE;
    sTraps[1].xPos = 2;
    sTraps[1].yPos = 3;
    sTraps[1].extra = 20;
    sTerrainData[2][3] = TERRAIN_SNAG;
    sTerrainData[3][2] = TERRAIN_SNAG;
    CHECK(
        ExpansionAutoplayPlanner_EnumerateLegalActions(
            CollectAction,
            &count,
            &count)
            == EXPANSION_AUTOPLAY_PLANNER_ENUMERATION_OK
            && count == 3,
        "combat enumeration must include one unit and two snag targets"
    );
    CHECK(
        sEnumeratedActions[0].targetId == 0x81
            && sEnumeratedActions[1].targetId == 0
            && sEnumeratedActions[1].xTarget == 3
            && sEnumeratedActions[1].yTarget == 2
            && sEnumeratedActions[2].targetId == 0
            && sEnumeratedActions[2].xTarget == 2
            && sEnumeratedActions[2].yTarget == 3,
        "snag candidates must follow unit targets in stable trap order"
    );
    firstSnag = &sEnumeratedActions[1];
    CHECK(ExpansionAutoplayPlanner_PrepareActionData(firstSnag),
          "live snag target must revalidate");
    sTraps[0].type = TRAP_NONE;
    CHECK(!ExpansionAutoplayPlanner_PrepareActionData(firstSnag),
          "destroyed snag must reject before executor lowering");
    sTraps[0].type = TRAP_OBSTACLE;
    sTerrainData[2][3] = 1;
    CHECK(!ExpansionAutoplayPlanner_PrepareActionData(firstSnag),
          "stale non-snag terrain must reject");
    sTerrainData[2][3] = TERRAIN_SNAG;
    firstSnag->xTarget = 5;
    firstSnag->yTarget = 5;
    CHECK(!ExpansionAutoplayPlanner_PrepareActionData(firstSnag),
          "out-of-range snag coordinates must reject");
    return 0;
}

static int TestStaffTargetParity(void)
{
    u32 count = 0;

    ResetActionFixture(8, 8);
    sUnit.items[0] = ITEM_STAFF_REPAIR;
    sAlly.pCharacterData = &sAllyCharacter;
    sAlly.pClassData = &sAllyClass;
    sAlly.index = 2;
    sAlly.xPos = 3;
    sAlly.yPos = 2;
    sAlly.items[0] = 0x0101;
    sAlly.items[1] = 0x0202;
    sEnemy.pCharacterData = &sEnemyCharacter;
    sEnemy.pClassData = &sEnemyClass;
    sEnemy.index = 0x41;
    sEnemy.xPos = 2;
    sEnemy.yPos = 3;
    sEnemy.items[0] = 0x0101;
    sSummon.pCharacterData = &sSummonCharacter;
    sSummon.pClassData = &sSummonClass;
    sSummon.index = 0x81;
    sSummon.xPos = 1;
    sSummon.yPos = 2;
    sSummon.items[0] = 0x0101;
    CHECK(
        ExpansionAutoplayPlanner_EnumerateLegalActions(
            CollectAction,
            &count,
            &count)
            == EXPANSION_AUTOPLAY_PLANNER_ENUMERATION_OK
            && count == 2
            && sEnumeratedActions[0].targetId == 2
            && sEnumeratedActions[0].unk04 == 0
            && sEnumeratedActions[1].targetId == 2
            && sEnumeratedActions[1].unk04 == 1,
        "Hammerne must retain only same-faction repairable slots"
    );
    CHECK(ExpansionAutoplayPlanner_PrepareActionData(
              &sEnumeratedActions[1])
              && gActionData.trapType == 1,
          "Hammerne must revalidate and lower the same-faction slot");

    ResetActionFixture(8, 8);
    sUnit.items[0] = ITEM_STAFF_FORTIFY;
    sUnit.curHP = 10;
    sMagRange = 3;
    count = 0;
    ExpansionAutoplayPlanner_EnumerateLegalActions(
        CollectAction,
        &count,
        &count);
    CHECK(CountActionId(count, AI_ACTION_STAFF) == 0,
          "Fortify must not target an injured caster");
    sAlly.pCharacterData = &sAllyCharacter;
    sAlly.pClassData = &sAllyClass;
    sAlly.index = 2;
    sAlly.xPos = 6;
    sAlly.yPos = 2;
    sAlly.maxHP = 20;
    sAlly.curHP = 10;
    count = 0;
    ExpansionAutoplayPlanner_EnumerateLegalActions(
        CollectAction,
        &count,
        &count);
    CHECK(CountActionId(count, AI_ACTION_STAFF) == 0,
          "Fortify must reject an ally outside MAG/2 range");
    sAlly.xPos = 5;
    count = 0;
    ExpansionAutoplayPlanner_EnumerateLegalActions(
        CollectAction,
        &count,
        &count);
    CHECK(CountActionId(count, AI_ACTION_STAFF) == 1,
          "Fortify must retain an injured ally inside MAG/2 range");

    ResetActionFixture(8, 8);
    sUnit.items[0] = ITEM_STAFF_LATONA;
    sUnit.curHP = 10;
    count = 0;
    ExpansionAutoplayPlanner_EnumerateLegalActions(
        CollectAction,
        &count,
        &count);
    CHECK(CountActionId(count, AI_ACTION_STAFF) == 0,
          "Latona must exclude an injured caster");
    sAlly.pCharacterData = &sAllyCharacter;
    sAlly.pClassData = &sAllyClass;
    sAlly.index = 2;
    sAlly.xPos = 7;
    sAlly.yPos = 7;
    sAlly.maxHP = 20;
    sAlly.curHP = 10;
    count = 0;
    ExpansionAutoplayPlanner_EnumerateLegalActions(
        CollectAction,
        &count,
        &count);
    CHECK(CountActionId(count, AI_ACTION_STAFF) == 1,
          "Latona must retain an injured non-caster in its phase domain");
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
        CHECK(candidate->itemSlot == 0xFF,
              "normal Summon must use the no-item sentinel");
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
              && first.itemSlot == 0xFF
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

static int TestUnavailableUnitSemantics(void)
{
    u32 count = 99;

    ResetActionFixture(6, 6);
    sUnit.state = US_NOT_DEPLOYED;
    CHECK(
        ExpansionAutoplayPlanner_EnumerateLegalActions(
            CollectAction,
            &count,
            &count)
            == EXPANSION_AUTOPLAY_PLANNER_ENUMERATION_UNAVAILABLE
            && count == 0,
        "not-deployed active unit must be unavailable before enumeration"
    );

    ResetActionFixture(6, 6);
    sUnit.items[0] = ITEM_SWORD_IRON;
    sEnemyCharacter.number = 3;
    sEnemyClass.number = 3;
    sEnemy.pCharacterData = &sEnemyCharacter;
    sEnemy.pClassData = &sEnemyClass;
    sEnemy.index = 0x81;
    sEnemy.xPos = 3;
    sEnemy.yPos = 2;
    sEnemy.maxHP = 20;
    sEnemy.curHP = 20;
    sEnemy.state = US_NOT_DEPLOYED;
    count = 0;
    ExpansionAutoplayPlanner_EnumerateLegalActions(
        CollectAction,
        &count,
        NULL);
    CHECK(CountActionId(count, AI_ACTION_COMBAT) == 0,
          "not-deployed stale-coordinate target must not be legal");
    sEnemy.state = 0;
    count = 0;
    ExpansionAutoplayPlanner_EnumerateLegalActions(
        CollectAction,
        &count,
        NULL);
    CHECK(CountActionId(count, AI_ACTION_COMBAT) == 1,
          "available in-range target must remain legal");
    return 0;
}

static int TestMaximumSemanticPaging(void)
{
    struct AiDecision decision = { 0 };
    int index;

    ResetActionFixture(6, 6);
    sUseMaxUnits = true;
    for (index = 0; index < 132; index++)
    {
        int unitId;

        if (index < 62)
            unitId = index + 1;
        else if (index < 82)
            unitId = 0x41 + index - 62;
        else
            unitId = 0x81 + index - 82;
        memset(&sMaxUnits[index], 0, sizeof(sMaxUnits[index]));
        sMaxUnits[index].pCharacterData = &sCharacter;
        sMaxUnits[index].pClassData = &sClass;
        sMaxUnits[index].index = unitId;
        sMaxUnits[index].xPos = 1;
        sMaxUnits[index].yPos = 1;
        sMaxUnits[index].maxHP = 20;
        sMaxUnits[index].curHP = 20;
        sMaxUnits[index].state =
            index == 0 ? 0 : US_NOT_DEPLOYED;
    }
    gActiveUnit = &sMaxUnits[0];
    gActiveUnitId = 1;
    gBmMapUnit[2][2] = 0;
    gBmMapUnit[1][1] = 1;
    gBmMapMovement[2][2] = MAP_MOVEMENT_MAX + 1;
    gBmMapMovement[1][2] = 1;
    sPermanentFlagSize = sizeof(sPermanentFlags);
    sChapterFlagSize = sizeof(sChapterFlags);
    memset(sPermanentFlags, 0, sizeof(sPermanentFlags));
    memset(sChapterFlags, 0, sizeof(sChapterFlags));
    sPermanentFlags[255] = 0x80;
    sChapterFlags[255] = 0x80;

    ExpansionAutoplayPlanner_Reset();
    ExpansionAutoplayPlanner_OnMapReady();
    ExpansionAutoplayPlanner_PollStart();
    WriteCommand(
        EXPANSION_AUTOPLAY_PLANNER_COMMAND_START,
        0,
        0,
        0,
        0,
        NULL);
    CHECK(ExpansionAutoplayPlanner_PollStart(),
          "maximum semantic-page run must start");
    CHECK(
        ExpansionAutoplayPlanner_OfferDecision(&decision)
                == EXPANSION_AUTOPLAY_PLANNER_DECISION_WAIT
            && gExpansionAutoplayPlannerObservation.pageCount == 51,
        "maximum units and flags must use bounded canonical pages"
    );

    WriteCommand(
        EXPANSION_AUTOPLAY_PLANNER_COMMAND_PAGE,
        gExpansionAutoplayPlannerObservation.runId,
        gExpansionAutoplayPlannerObservation.observationId,
        4,
        0,
        NULL);
    CHECK(
        ExpansionAutoplayPlanner_PollDecision(&decision)
                == EXPANSION_AUTOPLAY_PLANNER_DECISION_WAIT
            && gExpansionAutoplayPlannerObservation.pageKind
                == EXPANSION_AUTOPLAY_PLANNER_PAGE_UNITS
            && gExpansionAutoplayPlannerObservation.start.recordStart == 112
            && gExpansionAutoplayPlannerObservation.count.recordCount == 20,
        "maximum unit page boundary must be exact"
    );
    WriteCommand(
        EXPANSION_AUTOPLAY_PLANNER_COMMAND_PAGE,
        gExpansionAutoplayPlannerObservation.runId,
        gExpansionAutoplayPlannerObservation.observationId,
        10,
        0,
        NULL);
    CHECK(
        ExpansionAutoplayPlanner_PollDecision(&decision)
                == EXPANSION_AUTOPLAY_PLANNER_DECISION_WAIT
            && gExpansionAutoplayPlannerObservation.pageKind
                == EXPANSION_AUTOPLAY_PLANNER_PAGE_INVENTORY
            && gExpansionAutoplayPlannerObservation.start.recordStart == 560
            && gExpansionAutoplayPlannerObservation.count.recordCount == 100
            && ((gExpansionAutoplayPlannerObservation
                    .payload.inventory[0].identity >> 8) & 0xFF) == 0x9F
            && ((gExpansionAutoplayPlannerObservation
                    .payload.inventory[0].identity >> 16) & 0xFF) == 0
            && ((gExpansionAutoplayPlannerObservation
                    .payload.inventory[99].identity >> 8) & 0xFF) == 0xB2
            && ((gExpansionAutoplayPlannerObservation
                    .payload.inventory[99].identity >> 16) & 0xFF) == 4,
        "maximum inventory page boundary must be exact"
    );
    WriteCommand(
        EXPANSION_AUTOPLAY_PLANNER_COMMAND_PAGE,
        gExpansionAutoplayPlannerObservation.runId,
        gExpansionAutoplayPlannerObservation.observationId,
        12,
        0,
        NULL);
    CHECK(
        ExpansionAutoplayPlanner_PollDecision(&decision)
                == EXPANSION_AUTOPLAY_PLANNER_DECISION_WAIT
            && gExpansionAutoplayPlannerObservation.pageKind
                == EXPANSION_AUTOPLAY_PLANNER_PAGE_RESOURCES
            && gExpansionAutoplayPlannerObservation.start.recordStart == 112
            && gExpansionAutoplayPlannerObservation.count.recordCount == 5,
        "maximum resource page boundary must be exact"
    );
    WriteCommand(
        EXPANSION_AUTOPLAY_PLANNER_COMMAND_PAGE,
        gExpansionAutoplayPlannerObservation.runId,
        gExpansionAutoplayPlannerObservation.observationId,
        49,
        0,
        NULL);
    CHECK(
        ExpansionAutoplayPlanner_PollDecision(&decision)
                == EXPANSION_AUTOPLAY_PLANNER_DECISION_WAIT
            && gExpansionAutoplayPlannerObservation.pageKind
                == EXPANSION_AUTOPLAY_PLANNER_PAGE_FLAGS
            && gExpansionAutoplayPlannerObservation.start.recordStart == 4032
            && gExpansionAutoplayPlannerObservation.count.recordCount == 64,
        "maximum flag page boundary must be exact"
    );
    WriteCommand(
        EXPANSION_AUTOPLAY_PLANNER_COMMAND_PAGE,
        gExpansionAutoplayPlannerObservation.runId,
        gExpansionAutoplayPlannerObservation.observationId,
        50,
        0,
        NULL);
    CHECK(
        ExpansionAutoplayPlanner_PollDecision(&decision)
                == EXPANSION_AUTOPLAY_PLANNER_DECISION_WAIT
            && gExpansionAutoplayPlannerObservation.pageKind
                == EXPANSION_AUTOPLAY_PLANNER_PAGE_ACTIONS
            && gExpansionAutoplayPlannerObservation.count.actionCount == 1,
        "action page must follow every maximum semantic page"
    );

    ExpansionAutoplayPlanner_Reset();
    sUseMaxUnits = false;
    sPermanentFlagSize = 8;
    sChapterFlagSize = 8;
    gActiveUnit = &sUnit;
    gActiveUnitId = 1;
    sControlRequests = 0;
    return 0;
}

static int TestZeroDigestAvailability(void)
{
    struct AiDecision decision = { 0 };

    ResetActionFixture(6, 6);
    sMovementData[2][3] = 1;
    memset(sPermanentFlags, 0, sizeof(sPermanentFlags));
    memset(sChapterFlags, 0, sizeof(sChapterFlags));
    sPermanentFlags[0] = 0xCC;
    sPermanentFlags[1] = 0x24;
    sPermanentFlags[2] = 0x31;
    sPermanentFlags[3] = 0xC4;
    sPermanentFlagSize = 4;
    sChapterFlagSize = 0;
    memset(sConvoy, 0, sizeof(sConvoy));
    sConvoy[97] = 0xEDD0;
    sConvoy[98] = 0xC25D;
    gPlaySt.partyGoldAmount = 2166136261u;
    ExpansionAutoplayPlanner_Reset();
    ExpansionAutoplayPlanner_OnMapReady();
    ExpansionAutoplayPlanner_PollStart();
    WriteCommand(
        EXPANSION_AUTOPLAY_PLANNER_COMMAND_START,
        0,
        0,
        0,
        0,
        NULL);
    CHECK(ExpansionAutoplayPlanner_PollStart(),
          "zero-digest availability run must start");
    CHECK(
        ExpansionAutoplayPlanner_OfferDecision(&decision)
                == EXPANSION_AUTOPLAY_PLANNER_DECISION_WAIT
            && gExpansionAutoplayPlannerObservation
                    .payload.fields[6].availability
                == EXPANSION_AUTOPLAY_PLANNER_AVAILABLE
            && gExpansionAutoplayPlannerObservation
                    .payload.fields[6].value == 0
            && gExpansionAutoplayPlannerObservation
                    .payload.fields[7].availability
                == EXPANSION_AUTOPLAY_PLANNER_AVAILABLE
            && gExpansionAutoplayPlannerObservation
                    .payload.fields[7].value == 0,
        "valid zero flag and convoy/resource digests must remain available"
    );

    ExpansionAutoplayPlanner_Reset();
    sFlagPointersAvailable = false;
    sConvoyAvailable = false;
    ExpansionAutoplayPlanner_OnMapReady();
    ExpansionAutoplayPlanner_PollStart();
    WriteCommand(
        EXPANSION_AUTOPLAY_PLANNER_COMMAND_START,
        0,
        0,
        0,
        0,
        NULL);
    CHECK(ExpansionAutoplayPlanner_PollStart(),
          "null semantic-domain run must start");
    CHECK(
        ExpansionAutoplayPlanner_OfferDecision(&decision)
                == EXPANSION_AUTOPLAY_PLANNER_DECISION_WAIT
            && gExpansionAutoplayPlannerObservation
                    .payload.fields[6].availability
                == EXPANSION_AUTOPLAY_PLANNER_UNINITIALIZED
            && gExpansionAutoplayPlannerObservation
                    .payload.fields[7].availability
                == EXPANSION_AUTOPLAY_PLANNER_UNINITIALIZED,
        "null flag and convoy domains must be unavailable"
    );

    ExpansionAutoplayPlanner_Reset();
    sFlagPointersAvailable = true;
    sConvoyAvailable = true;
    sPermanentFlagSize = 257;
    ExpansionAutoplayPlanner_OnMapReady();
    ExpansionAutoplayPlanner_PollStart();
    WriteCommand(
        EXPANSION_AUTOPLAY_PLANNER_COMMAND_START,
        0,
        0,
        0,
        0,
        NULL);
    CHECK(ExpansionAutoplayPlanner_PollStart(),
          "out-of-bounds flag-domain run must start");
    CHECK(
        ExpansionAutoplayPlanner_OfferDecision(&decision)
                == EXPANSION_AUTOPLAY_PLANNER_DECISION_WAIT
            && gExpansionAutoplayPlannerObservation
                    .payload.fields[6].availability
                == EXPANSION_AUTOPLAY_PLANNER_UNINITIALIZED,
        "out-of-bounds flag storage must be unavailable"
    );
    sPermanentFlagSize = 8;
    sChapterFlagSize = 8;
    gPlaySt.partyGoldAmount = 1234;
    memset(sPermanentFlags, 0, sizeof(sPermanentFlags));
    memset(sChapterFlags, 0, sizeof(sChapterFlags));
    memset(sConvoy, 0, sizeof(sConvoy));
    sControlRequests = 0;
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
        NULL);
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
        NULL);
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
            && (first.token0 != second.token0
                || first.token1 != second.token1
                || first.token2 != second.token2
                || first.token3 != second.token3),
        "Hammerne target slot must be packed and token-bound"
    );
    WriteCommand(
        EXPANSION_AUTOPLAY_PLANNER_COMMAND_COMMIT,
        gExpansionAutoplayPlannerObservation.runId,
        gExpansionAutoplayPlannerObservation.observationId,
        0,
        gExpansionAutoplayPlannerObservation.start.actionStartOrdinal + 1,
        &first.token0);
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
        &second.token0);
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
    u32 selectedToken[4];
    u32 forgedToken[4];
    u32 previousScenarioIdentity;
    u32 previousSeedIdentity;
    int index;
    int other;
    int restoreBefore;

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
    CHECK(TestSnagActionFamily() == 0,
          "snag combat action family test");
    CHECK(TestStaffTargetParity() == 0,
          "staff target predicate parity test");
    CHECK(TestSummonActionFamily() == 0,
          "normal and dark summon action family test");
    CHECK(TestUnavailableUnitSemantics() == 0,
          "unavailable actor and target semantics test");
    CHECK(TestMaximumSemanticPaging() == 0,
          "maximum semantic paging test");
    CHECK(TestZeroDigestAvailability() == 0,
          "zero semantic digest availability test");

    gBmMapSize.x = 32;
    gBmMapSize.y = 17;
    sUnit.xPos = 0;
    sUnit.yPos = 0;
    sUnit.maxHP = 20;
    sUnit.curHP = 20;
    sCharacter.number = 1;
    sClass.number = 1;
    sClass.attributes = 0;
    sUnit.items[0] = ITEM_SWORD_IRON | (30 << 8);
    sAllyCharacter.number = 2;
    sAllyClass.number = 2;
    sAlly.pCharacterData = &sAllyCharacter;
    sAlly.pClassData = &sAllyClass;
    sAlly.index = 2;
    sAlly.xPos = 5;
    sAlly.yPos = 5;
    sAlly.maxHP = 20;
    sAlly.curHP = 20;
    sAlly.state = US_NOT_DEPLOYED;
    sAlly.items[0] = ITEM_VULNERARY | (2 << 8);
    sEnemy.pCharacterData = NULL;
    sSummon.pCharacterData = NULL;
    gPlaySt.partyGoldAmount = 1234;
    sConvoy[0] = ITEM_CHESTKEY | (3 << 8);
    sPermanentFlags[0] = 1;
    sChapterFlags[0] = 2;
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

    WriteCommand((enum ExpansionAutoplayPlannerCommandKind)99, 0, 0, 0, 0, NULL);
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
    WriteCommand(EXPANSION_AUTOPLAY_PLANNER_COMMAND_START, 0, 0, 0, 0, NULL);
    gExpansionAutoplayPlannerCommand.payload.start.expectedSeedIdentity =
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

    WriteCommand(EXPANSION_AUTOPLAY_PLANNER_COMMAND_START, 0, 0, 0, 0, NULL);
    gExpansionAutoplayPlannerCommand.payload.start.expectedRomIdentity ^= 1;
    CHECK(
        !ExpansionAutoplayPlanner_PollStart()
            && gExpansionAutoplayPlannerObservation.rejection
                == EXPANSION_AUTOPLAY_PLANNER_REJECTION_PROTOCOL_ERROR,
        "same header with different build identity must reject provenance"
    );
    ExpansionAutoplayPlanner_Reset();
    ExpansionAutoplayPlanner_OnMapReady();
    ExpansionAutoplayPlanner_PollStart();

    WriteCommand(EXPANSION_AUTOPLAY_PLANNER_COMMAND_START, 0, 0, 0, 0, NULL);
    gExpansionAutoplayPlannerCommand.payload.start.expectedScenarioIdentity ^= 1;
    CHECK(!ExpansionAutoplayPlanner_PollStart(), "mismatched provenance must reject");

    WriteCommand(EXPANSION_AUTOPLAY_PLANNER_COMMAND_START, 0, 0, 0, 0, NULL);
    CHECK(ExpansionAutoplayPlanner_PollStart(), "typed START mailbox command must activate");
    CHECK(sControlRequests == 1, "valid provenance activates computer control once");

    WriteCommand(EXPANSION_AUTOPLAY_PLANNER_COMMAND_START, 0, 0, 0, 0, NULL);
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
                == EXPANSION_AUTOPLAY_PLANNER_DECISION_WAIT
            && ExpansionAutoplayPlanner_IsActive(),
        "first production decision must publish one legal token"
    );
    CHECK(
        gExpansionAutoplayPlannerObservation.totalActionCount == 512,
        "complete enumerator must retain the 512-candidate boundary"
    );
    CHECK(
        sizeof(gExpansionAutoplayPlannerObservation)
                <= EXPANSION_AUTOPLAY_PLANNER_PAGE_MAX_BYTES
            && gExpansionAutoplayPlannerObservation.pageCount == 34
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

    WriteCommand(
        EXPANSION_AUTOPLAY_PLANNER_COMMAND_PAGE,
        gExpansionAutoplayPlannerObservation.runId,
        gExpansionAutoplayPlannerObservation.observationId,
        4,
        0,
        NULL);
    CHECK(
        ExpansionAutoplayPlanner_PollDecision(&decision)
                == EXPANSION_AUTOPLAY_PLANNER_DECISION_WAIT
            && gExpansionAutoplayPlannerObservation.pageKind
                == EXPANSION_AUTOPLAY_PLANNER_PAGE_UNITS
            && gExpansionAutoplayPlannerObservation.count.recordCount == 2
            && (gExpansionAutoplayPlannerObservation
                    .payload.units[1].identity >> 24)
                == EXPANSION_AUTOPLAY_PLANNER_UNAVAILABLE
            && gExpansionAutoplayPlannerObservation.payload.units[1].state
                == US_NOT_DEPLOYED,
        "unit page must mark benched stale-coordinate units unavailable"
    );
    WriteCommand(
        EXPANSION_AUTOPLAY_PLANNER_COMMAND_PAGE,
        gExpansionAutoplayPlannerObservation.runId,
        gExpansionAutoplayPlannerObservation.observationId,
        5,
        0,
        NULL);
    CHECK(
        ExpansionAutoplayPlanner_PollDecision(&decision)
                == EXPANSION_AUTOPLAY_PLANNER_DECISION_WAIT
            && gExpansionAutoplayPlannerObservation.pageKind
                == EXPANSION_AUTOPLAY_PLANNER_PAGE_INVENTORY
            && gExpansionAutoplayPlannerObservation.count.recordCount == 10
            && (gExpansionAutoplayPlannerObservation
                    .payload.inventory[0].identity & 0xFF)
                == EXPANSION_AUTOPLAY_PLANNER_VALUE_UNIT_ITEM
            && (gExpansionAutoplayPlannerObservation
                    .payload.inventory[0].identity >> 24)
                == EXPANSION_AUTOPLAY_PLANNER_AVAILABLE
            && gExpansionAutoplayPlannerObservation
                    .payload.inventory[0].value
                == (ITEM_SWORD_IRON | (30 << 8))
            && (gExpansionAutoplayPlannerObservation
                    .payload.inventory[1].identity >> 24)
                == EXPANSION_AUTOPLAY_PLANNER_EMPTY
            && (gExpansionAutoplayPlannerObservation
                    .payload.inventory[5].identity >> 24)
                == EXPANSION_AUTOPLAY_PLANNER_UNAVAILABLE
            && gExpansionAutoplayPlannerObservation
                    .payload.inventory[5].value
                == (ITEM_VULNERARY | (2 << 8)),
        "inventory page must expose present, empty, and unavailable unit slots"
    );
    WriteCommand(
        EXPANSION_AUTOPLAY_PLANNER_COMMAND_PAGE,
        gExpansionAutoplayPlannerObservation.runId,
        gExpansionAutoplayPlannerObservation.observationId,
        6,
        0,
        NULL);
    CHECK(
        ExpansionAutoplayPlanner_PollDecision(&decision)
                == EXPANSION_AUTOPLAY_PLANNER_DECISION_WAIT
            && gExpansionAutoplayPlannerObservation.pageKind
                == EXPANSION_AUTOPLAY_PLANNER_PAGE_RESOURCES
            && gExpansionAutoplayPlannerObservation.count.recordCount == 112
            && gExpansionAutoplayPlannerObservation
                    .payload.resources[0].value == 1234
            && gExpansionAutoplayPlannerObservation
                    .payload.resources[1].value
                == (ITEM_CHESTKEY | (3 << 8))
            && (gExpansionAutoplayPlannerObservation
                    .payload.resources[2].identity >> 24)
                == EXPANSION_AUTOPLAY_PLANNER_EMPTY,
        "resource page must expose gold plus present and empty convoy slots"
    );
    WriteCommand(
        EXPANSION_AUTOPLAY_PLANNER_COMMAND_PAGE,
        gExpansionAutoplayPlannerObservation.runId,
        gExpansionAutoplayPlannerObservation.observationId,
        7,
        0,
        NULL);
    CHECK(
        ExpansionAutoplayPlanner_PollDecision(&decision)
                == EXPANSION_AUTOPLAY_PLANNER_DECISION_WAIT
            && gExpansionAutoplayPlannerObservation.pageKind
                == EXPANSION_AUTOPLAY_PLANNER_PAGE_RESOURCES
            && gExpansionAutoplayPlannerObservation.start.recordStart == 112
            && gExpansionAutoplayPlannerObservation.count.recordCount == 5,
        "resource paging must include the complete autoplay telemetry"
    );
    WriteCommand(
        EXPANSION_AUTOPLAY_PLANNER_COMMAND_PAGE,
        gExpansionAutoplayPlannerObservation.runId,
        gExpansionAutoplayPlannerObservation.observationId,
        8,
        0,
        NULL);
    CHECK(
        ExpansionAutoplayPlanner_PollDecision(&decision)
                == EXPANSION_AUTOPLAY_PLANNER_DECISION_WAIT
            && gExpansionAutoplayPlannerObservation.pageKind
                == EXPANSION_AUTOPLAY_PLANNER_PAGE_FLAGS
            && gExpansionAutoplayPlannerObservation.count.recordCount == 112
            && gExpansionAutoplayPlannerObservation
                    .payload.flags[0].value == 1
            && gExpansionAutoplayPlannerObservation
                    .payload.flags[1].value == 0,
        "flag page must expose explicit set and clear states"
    );
    WriteCommand(
        EXPANSION_AUTOPLAY_PLANNER_COMMAND_PAGE,
        gExpansionAutoplayPlannerObservation.runId,
        gExpansionAutoplayPlannerObservation.observationId,
        9,
        0,
        NULL);
    CHECK(
        ExpansionAutoplayPlanner_PollDecision(&decision)
                == EXPANSION_AUTOPLAY_PLANNER_DECISION_WAIT
            && gExpansionAutoplayPlannerObservation.pageKind
                == EXPANSION_AUTOPLAY_PLANNER_PAGE_FLAGS
            && gExpansionAutoplayPlannerObservation.start.recordStart == 112
            && gExpansionAutoplayPlannerObservation.count.recordCount == 16,
        "flag paging must retain canonical record boundaries"
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
        33,
        0,
        NULL);
    CHECK(
        ExpansionAutoplayPlanner_PollDecision(&decision)
            == EXPANSION_AUTOPLAY_PLANNER_DECISION_WAIT,
        "valid PAGE command must publish another page"
    );
    CHECK(
        gExpansionAutoplayPlannerObservation.pageIndex == 33
            && gExpansionAutoplayPlannerObservation.pageKind
                == EXPANSION_AUTOPLAY_PLANNER_PAGE_ACTIONS
            && gExpansionAutoplayPlannerObservation.start.actionStartOrdinal
                == 506
            && gExpansionAutoplayPlannerObservation.count.actionCount == 6,
        "last page must retain stable global ordinals"
    );

    WriteCommand(
        (enum ExpansionAutoplayPlannerCommandKind)99,
        gExpansionAutoplayPlannerObservation.runId,
        gExpansionAutoplayPlannerObservation.observationId,
        0,
        0,
        NULL);
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
        33,
        0,
        NULL);
    CHECK(
        ExpansionAutoplayPlanner_PollDecision(&decision)
            == EXPANSION_AUTOPLAY_PLANNER_DECISION_WAIT,
        "page must republish after malformed command"
    );

    action = &gExpansionAutoplayPlannerObservation.payload.actions[5];
    selectedOrdinal =
        gExpansionAutoplayPlannerObservation.start.actionStartOrdinal + 5;
    selectedToken[0] = action->token0;
    selectedToken[1] = action->token1;
    selectedToken[2] = action->token2;
    selectedToken[3] = action->token3;
    for (index = 0; index < 4; index++)
    {
        for (other = 0; other < 4; other++)
        {
            CHECK(
                index == other
                    || selectedToken[index] != selectedToken[other],
                "opaque token words must be independently mixed"
            );
            forgedToken[other] = selectedToken[other];
        }
        forgedToken[index] ^= 1;
        WriteCommand(
            EXPANSION_AUTOPLAY_PLANNER_COMMAND_COMMIT,
            gExpansionAutoplayPlannerObservation.runId,
            gExpansionAutoplayPlannerObservation.observationId,
            0,
            selectedOrdinal,
            forgedToken);
        CHECK(
            ExpansionAutoplayPlanner_PollDecision(&decision)
                == EXPANSION_AUTOPLAY_PLANNER_DECISION_WAIT,
            "every forged token word must reject"
        );
        CHECK(
            gExpansionAutoplayPlannerObservation.rejection
                == EXPANSION_AUTOPLAY_PLANNER_REJECTION_TOKEN_MISMATCH,
            "forged token word must have explicit rejection"
        );
    }

    sMovementData[16][0] = MAP_MOVEMENT_MAX + 1;
    WriteCommand(
        EXPANSION_AUTOPLAY_PLANNER_COMMAND_COMMIT,
        gExpansionAutoplayPlannerObservation.runId,
        gExpansionAutoplayPlannerObservation.observationId,
        0,
        selectedOrdinal,
        selectedToken);
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
        selectedToken);
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
            NULL);
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
        NULL);
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
    WriteCommand(EXPANSION_AUTOPLAY_PLANNER_COMMAND_START, 0, 0, 0, 0, NULL);
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
        NULL);
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
    WriteCommand(EXPANSION_AUTOPLAY_PLANNER_COMMAND_START, 0, 0, 0, 0, NULL);
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
        10,
        0,
        NULL);
    CHECK(
        ExpansionAutoplayPlanner_PollDecision(&decision)
            == EXPANSION_AUTOPLAY_PLANNER_DECISION_WAIT
            && gExpansionAutoplayPlannerObservation.pageKind
                == EXPANSION_AUTOPLAY_PLANNER_PAGE_ACTIONS,
        "wait candidate must be read through a typed action page"
    );
    action = &gExpansionAutoplayPlannerObservation.payload.actions[0];
    CHECK(
        action->itemSlot == 0xFFFF
            && ((action->destination & 0xFFFF) != sUnit.xPos
                || (action->destination >> 16) != sUnit.yPos),
        "active unit current tile must not be a committed wait candidate"
    );
    WriteCommand(
        EXPANSION_AUTOPLAY_PLANNER_COMMAND_COMMIT,
        gExpansionAutoplayPlannerObservation.runId,
        gExpansionAutoplayPlannerObservation.observationId,
        0,
        gExpansionAutoplayPlannerObservation.start.actionStartOrdinal,
        &action->token0);
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
    WriteCommand(EXPANSION_AUTOPLAY_PLANNER_COMMAND_START, 0, 0, 0, 0, NULL);
    CHECK(ExpansionAutoplayPlanner_PollStart(), "zero-candidate run must start");
    for (index = 0; index < 17; index++)
    {
        int x;

        for (x = 0; x < 32; x++)
            sMovementData[index][x] = MAP_MOVEMENT_MAX + 1;
    }
    sMovementData[sUnit.yPos][sUnit.xPos] = 0;
    restoreBefore = sRestoreRequests;
    original = decision;
    CHECK(
        ExpansionAutoplayPlanner_OfferDecision(&decision)
                == EXPANSION_AUTOPLAY_PLANNER_DECISION_EXHAUSTED
            && gExpansionAutoplayPlannerObservation.state
                == EXPANSION_AUTOPLAY_PLANNER_STATE_EXHAUSTED
            && gExpansionAutoplayPlannerObservation.rejection
                == EXPANSION_AUTOPLAY_PLANNER_REJECTION_CAPABILITY_UNAVAILABLE
            && !ExpansionAutoplayPlanner_IsActive()
            && gExpansionAutoplayPlannerCampaignCheckpoint.magic == 0
            && sRestoreRequests == restoreBefore + 1
            && memcmp(&decision, &original, sizeof(decision)) == 0,
        "zero candidates must fail before publishing WAITING page zero"
    );
    CHECK(
        ExpansionAutoplayPlanner_OfferDecision(&decision)
            == EXPANSION_AUTOPLAY_PLANNER_DECISION_FALLBACK,
        "exhausted planner must not re-enter its stale terminal state"
    );

    for (index = 0; index < 17; index++)
    {
        int x;

        for (x = 0; x < 32; x++)
            sMovementData[index][x] = 1;
    }
    sUnitData[sUnit.yPos][sUnit.xPos] = 1;
    ExpansionAutoplayPlanner_Reset();
    ExpansionAutoplayPlanner_OnMapReady();
    ExpansionAutoplayPlanner_PollStart();
    WriteCommand(
        EXPANSION_AUTOPLAY_PLANNER_COMMAND_START,
        0,
        0,
        0,
        0,
        NULL);
    CHECK(ExpansionAutoplayPlanner_PollStart(),
          "capacity terminal run must start");
    restoreBefore = sRestoreRequests;
    CHECK(
        ExpansionAutoplayPlanner_OfferDecision(&decision)
                == EXPANSION_AUTOPLAY_PLANNER_DECISION_EXHAUSTED
            && gExpansionAutoplayPlannerObservation.state
                == EXPANSION_AUTOPLAY_PLANNER_STATE_EXHAUSTED
            && gExpansionAutoplayPlannerObservation.rejection
                == EXPANSION_AUTOPLAY_PLANNER_REJECTION_RESOURCE_LIMIT
            && !ExpansionAutoplayPlanner_IsActive()
            && gExpansionAutoplayPlannerCampaignCheckpoint.magic == 0
            && sRestoreRequests == restoreBefore + 1,
        "capacity overflow must terminate and queue safe restoration"
    );

    puts("AUTOPLAY_PLANNER_HOST_TEST: PASS");
    return 0;
}
