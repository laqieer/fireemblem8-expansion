#include "global.h"

#include <stdio.h>
#include <string.h>
#include <limits.h>

#include "action_semantics.h"
#include "bm.h"
#include "bmcontainer.h"
#include "bmidoten.h"
#include "bmitem.h"
#include "bmmap.h"
#include "bmmind.h"
#include "bmphase.h"
#include "bmtarget.h"
#include "bmtrick.h"
#include "bmunit.h"
#include "cp_common.h"
#include "uiselecttarget.h"
#include "constants/characters.h"
#include "constants/classes.h"
#include "constants/items.h"
#include "constants/terrains.h"
#include "expansion_autoplay.h"
#include "expansion_autoplay_planner.h"
#include "expansion_autoplay_strategies.h"
#include "expansion_chapter_objectives.h"
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
struct BattleUnit gBattleTarget;
struct ExpansionAutoplayTelemetry gExpansionAutoplayTelemetry;
struct Unit* gActiveUnit;
u8 gActiveUnitId;
struct AiDecision gAiDecision;
struct Vec2 gBmMapSize;
u8** gBmMapMovement;
u8** gBmMapUnit;
u8** gBmMapTerrain;
u8** gBmMapFog;
u8** gBmMapRange;
u8** gWorkingBmMap;
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
static int sRedUnitCount;
static int sTrapApplyCount;
static int sConsumedSlot;
static bool sTrapContractFailed;
static u16 sConvoy[CONVOY_ITEM_COUNT];
static u8 sMovementData[17][32];
static u8* sMovementRows[17];
static u8 sUnitData[17][32];
static u8* sUnitRows[17];
static u8 sTerrainData[17][32];
static u8* sTerrainRows[17];
static u8 sFogData[17][32];
static u8* sFogRows[17];
static u8 sRangeData[17][32];
static u8* sRangeRows[17];
static struct SelectTarget sTargets[16];
static int sTargetCount;
static const u8 sObjectiveMembers[] = { 1, 2 };
static const struct ExpansionChapterAiGroup sObjectiveGroups[] = {
    { 0x1001, sObjectiveMembers, 2 },
};
static const struct ExpansionChapterObjective sObjectives[] = {
    {
        0x2001, 0, &sObjectiveGroups[0], 0xFFFF, 0xFFFF, 1, 2, 5,
        EXPANSION_CHAPTER_OBJECTIVE_REACH_AREA, 0, 1, 1, 4, 4,
    },
};
const struct ExpansionChapterObjectiveBundle gExpansionChapterObjectiveBundles[] = {
    { 1, 1, 1, sObjectives, sObjectiveGroups },
    { EXPANSION_CHAPTER_OBJECTIVE_CHAPTER_NONE, 0, 0, NULL, NULL },
};
struct ExpansionChapterObjectiveTelemetry gExpansionChapterObjectiveTelemetry;
static const struct ExpansionAutoplayStrategyGroupAssignment sStrategyGroups[] = {
    { 0x1001, EXPANSION_AUTOPLAY_STRATEGY_AGGRESSIVE_ID, 0xFFFF },
};
static const struct ExpansionAutoplayStrategyUnitAssignment sStrategyUnits[] = {
    { 1, EXPANSION_AUTOPLAY_STRATEGY_OBJECTIVE_FIRST_ID, 0xFFFF },
};
const struct ExpansionAutoplayStrategy gExpansionAutoplayStrategies[] = {
    {
        EXPANSION_AUTOPLAY_STRATEGY_AGGRESSIVE_ID,
        EXPANSION_AUTOPLAY_STRATEGY_OBJECTIVE_ALL,
        EXPANSION_AUTOPLAY_STRATEGY_ACTION_COMBAT,
        NULL, 1,
    },
    {
        EXPANSION_AUTOPLAY_STRATEGY_OBJECTIVE_FIRST_ID,
        EXPANSION_AUTOPLAY_STRATEGY_OBJECTIVE_REACH_AREA,
        EXPANSION_AUTOPLAY_STRATEGY_ACTION_ALL,
        NULL, 0,
    },
    { 0 },
};
const struct ExpansionAutoplayStrategyBundle gExpansionAutoplayStrategyBundles[] = {
    {
        1, 1, 1, EXPANSION_AUTOPLAY_STRATEGY_AGGRESSIVE_ID, 0xFFFF,
        sStrategyGroups, sStrategyUnits,
    },
    { EXPANSION_AUTOPLAY_STRATEGY_CHAPTER_NONE, 0, 0, 0, 0, NULL, NULL },
};

void StoreRNState(u16* seeds)
{
    seeds[0] = sSeeds[0];
    seeds[1] = sSeeds[1];
    seeds[2] = sSeeds[2];
}

unsigned GetLCGRNValue(void) { return 0; }
u32 GetRNConsumptionCount(void) { return sConsumption; }
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
    if (id > FACTION_RED
        && id < FACTION_PURPLE
        && id - FACTION_RED <= sRedUnitCount)
        return &sEnemy;
    return NULL;
}

u8* GetPermanentFlagBits(void) { return sFlagPointersAvailable ? sPermanentFlags : NULL; }
int GetPermanentFlagBitsSize(void) { return sPermanentFlagSize; }
u8* GetChapterFlagBits(void) { return sFlagPointersAvailable ? sChapterFlags : NULL; }
int GetChapterFlagBitsSize(void) { return sChapterFlagSize; }
bool CheckFlag(int flag) { return (sPermanentFlags[flag >> 3] >> (flag & 7)) & 1; }
u16* GetConvoyItemArray(void) { return sConvoyAvailable ? sConvoy : NULL; }
s8 AreUnitsAllied(int left, int right) { return (left & 0x80) == (right & 0x80); }
s8 IsSameAllegiance(int left, int right) { return (left & 0xC0) == (right & 0xC0); }
int GetCurrentPhase(void) { return FACTION_BLUE; }
int GetUnitCurrentHp(struct Unit* unit) { return unit->curHP; }
int GetUnitMaxHp(struct Unit* unit) { return unit->maxHP; }
int GetUnitPower(struct Unit* unit) { return unit->pow; }
int GetUnitSkill(struct Unit* unit) { return unit->skl; }
int GetUnitSpeed(struct Unit* unit) { return unit->spd; }
int GetUnitDefense(struct Unit* unit) { return unit->def; }
int GetUnitResistance(struct Unit* unit) { return unit->res; }
int GetUnitLuck(struct Unit* unit) { return unit->lck; }
int GetUnitEquippedWeaponSlot(struct Unit* unit) { return unit->items[0] == 0 ? -1 : 0; }
const struct ExpansionChapterObjectiveBundle* ExpansionChapterObjectives_GetCurrentBundle(void)
{
    return gPlaySt.chapterIndex == 1 ? &gExpansionChapterObjectiveBundles[0] : NULL;
}
enum ExpansionChapterObjectiveState ExpansionChapterObjectives_GetSnapshot(
    u32 objectiveId, u32* progressOut)
{
    if (progressOut != NULL)
        *progressOut = objectiveId == sObjectives[0].id ? 1 : 0;
    return objectiveId == sObjectives[0].id
        ? EXPANSION_CHAPTER_OBJECTIVE_PENDING
        : EXPANSION_CHAPTER_OBJECTIVE_INACTIVE;
}
const struct ExpansionAutoplayStrategyBundle* ExpansionAutoplayStrategies_GetCurrentBundle(void)
{
    return gPlaySt.chapterIndex == 1 ? &gExpansionAutoplayStrategyBundles[0] : NULL;
}
const struct ExpansionAutoplayStrategy* ExpansionAutoplayStrategies_Find(u32 id)
{
    int index;
    for (index = 0; gExpansionAutoplayStrategies[index].id != 0; index++)
        if (gExpansionAutoplayStrategies[index].id == id)
            return &gExpansionAutoplayStrategies[index];
    return NULL;
}
enum ExpansionAutoplayStrategyResult ExpansionAutoplayStrategies_ResolveCurrent(
    struct ExpansionAutoplayStrategyResolution* resolution)
{
    resolution->strategyId = EXPANSION_AUTOPLAY_STRATEGY_OBJECTIVE_FIRST_ID;
    resolution->subjectId = 1;
    resolution->source = EXPANSION_AUTOPLAY_STRATEGY_ASSIGNMENT_UNIT;
    return EXPANSION_AUTOPLAY_STRATEGY_OK;
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

int GetItemAttributes(int item)
{
    if (GetItemIndex(item) == ITEM_SWORD_IRON)
        return IA_WEAPON;
    if (CanUnitUseStaff(gActiveUnit, item))
        return IA_STAFF;
    return 0;
}

int GetItemIndex(int item) { return item & 0xFF; }
int GetItemMinRange(int item) { (void)item; return 1; }
int GetItemMaxRange(int item)
{
    if (GetItemIndex(item) == ITEM_STAFF_WARP
        || GetItemIndex(item) == ITEM_STAFF_TORCH)
        return 0;
    if (GetItemIndex(item) == ITEM_STAFF_UNLOCK)
        return 2;
    return 1;
}

int GetUnitMagBy2Range(struct Unit* unit) { (void)unit; return sMagRange; }
bool IsPositionMagicSealed(int x, int y) { (void)x; (void)y; return false; }
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

s8 IsItemHammernable(int item) { return item != 0 && (item & 0xFF00) != 0xFF00; }

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

struct Trap* AddTrap(int x, int y, int trapType, int meta)
{
    struct Trap* trap = &sTraps[sTrapApplyCount];
    if (trapType != TRAP_TORCHLIGHT || meta != 8
        || sTrapApplyCount >= TRAP_MAX_COUNT)
    {
        sTrapContractFailed = true;
        return NULL;
    }
    trap->xPos = x;
    trap->yPos = y;
    trap->type = trapType;
    sTrapApplyCount++;
    return trap;
}

int MakeNewItem(int item) { return (item & 0xFF) | 0xFF00; }

void UnitUpdateUsedItem(struct Unit* unit, int itemSlot)
{
    sConsumedSlot = itemSlot;
    unit->items[itemSlot] -= 0x100;
}

void BmMapFill(u8** map, int value)
{
    int y;
    int x;
    for (y = 0; y < gBmMapSize.y; y++)
        for (x = 0; x < gBmMapSize.x; x++)
            map[y][x] = value;
}

void MapAddInRange(int x, int y, int range, int value)
{
    int iy;
    int ix;
    for (iy = 0; iy < gBmMapSize.y; iy++)
        for (ix = 0; ix < gBmMapSize.x; ix++)
            if (ABS(ix - x) + ABS(iy - y) <= range)
                gWorkingBmMap[iy][ix] += value;
}

void MapAddInBoundedRange(short x, short y, short minRange, short maxRange)
{
    int iy;
    int ix;
    for (iy = 0; iy < gBmMapSize.y; iy++)
        for (ix = 0; ix < gBmMapSize.x; ix++)
        {
            int distance = ABS(ix - x) + ABS(iy - y);
            if (distance >= minRange && distance <= maxRange)
                gWorkingBmMap[iy][ix] = 1;
        }
}

void InitTargets(int xRoot, int yRoot)
{
    (void)xRoot;
    (void)yRoot;
    sTargetCount = 0;
    gWorkingBmMap = gBmMapRange;
}

void AddTarget(int x, int y, int unitId, int targetId)
{
    struct SelectTarget* target = &sTargets[sTargetCount++];
    target->x = x;
    target->y = y;
    target->uid = unitId;
    target->extra = targetId;
}

s8 CanUnitUseHealItem(struct Unit* unit) { return unit->curHP < unit->maxHP; }
s8 CanUnitUsePureWaterItem(struct Unit* unit) { return unit->barrierDuration < 7; }
s8 CanUnitUseTorchItem(struct Unit* unit)
{
    return gPlaySt.chapterVisionRange != 0 && unit->torchDuration != 4;
}
s8 CanUnitUseAntitoxinItem(struct Unit* unit)
{
    return unit->statusIndex == UNIT_STATUS_POISON;
}

s8 CanUnitUsePromotionItem(struct Unit* unit, int item) { (void)unit; (void)item; return false; }
s8 CanUnitUseStatGainItem(struct Unit* unit, int item) { (void)unit; (void)item; return false; }
s8 CanUnitUseFruitItem(struct Unit* unit) { (void)unit; return false; }
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

static void WriteCommand(enum ExpansionAutoplayPlannerCommandKind kind, u32 runId,
                         u32 observationId, u32 pageIndex, u32 ordinal, const u32* token)
{
    memset((void*)&gExpansionAutoplayPlannerCommand, 0, sizeof(gExpansionAutoplayPlannerCommand));
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

static void PreparePlannerStart(void)
{
    ExpansionAutoplayPlanner_Reset();
    ExpansionAutoplayPlanner_OnMapReady();
    ExpansionAutoplayPlanner_PollStart();
}

static bool StartPreparedPlanner(void)
{
    WriteCommand(EXPANSION_AUTOPLAY_PLANNER_COMMAND_START, 0, 0, 0, 0, NULL);
    return ExpansionAutoplayPlanner_PollStart();
}

static bool ResetAndStartPlanner(void)
{
    PreparePlannerStart();
    return StartPreparedPlanner();
}

static enum ExpansionAutoplayPlannerDecisionResult RequestPage(struct AiDecision* decision,
                                                                u32 pageIndex)
{
    WriteCommand(EXPANSION_AUTOPLAY_PLANNER_COMMAND_PAGE,
                 gExpansionAutoplayPlannerObservation.runId,
                 gExpansionAutoplayPlannerObservation.observationId, pageIndex, 0, NULL);
    return ExpansionAutoplayPlanner_PollDecision(decision);
}

static bool PageMatches(struct AiDecision* decision, u32 pageIndex, u32 pageKind, u32 recordStart,
                        u32 recordCount)
{
    return RequestPage(decision, pageIndex)
            == EXPANSION_AUTOPLAY_PLANNER_DECISION_WAIT
        && gExpansionAutoplayPlannerObservation.pageIndex == pageIndex
        && gExpansionAutoplayPlannerObservation.pageKind == pageKind
        && gExpansionAutoplayPlannerObservation.start.recordStart == recordStart
        && gExpansionAutoplayPlannerObservation.count.recordCount == recordCount;
}

static enum ExpansionAutoplayPlannerDecisionResult CommitCurrent(
    struct AiDecision* decision, u32 ordinal, const u32* token)
{
    WriteCommand(EXPANSION_AUTOPLAY_PLANNER_COMMAND_COMMIT,
                 gExpansionAutoplayPlannerObservation.runId,
                 gExpansionAutoplayPlannerObservation.observationId, 0, ordinal, token);
    return ExpansionAutoplayPlanner_PollDecision(decision);
}

static bool StartActionPage(struct AiDecision* decision, u32 count)
{
    return ResetAndStartPlanner()
        && ExpansionAutoplayPlanner_OfferDecision(decision)
            == EXPANSION_AUTOPLAY_PLANNER_DECISION_WAIT
        && PageMatches(decision, gExpansionAutoplayPlannerObservation.pageCount - 1,
                       EXPANSION_AUTOPLAY_PLANNER_PAGE_ACTIONS, 0, count);
}

static bool CommitBecameIllegal(
    struct AiDecision* decision, u32 ordinal, const u32* token)
{
    return CommitCurrent(decision, ordinal, token)
            == EXPANSION_AUTOPLAY_PLANNER_DECISION_WAIT
        && gExpansionAutoplayPlannerObservation.rejection
            == EXPANSION_AUTOPLAY_PLANNER_REJECTION_ACTION_BECAME_ILLEGAL;
}

static struct AiDecision sEnumeratedActions[EXPANSION_AUTOPLAY_PLANNER_TOTAL_ACTION_CAPACITY];

static bool CollectAction(u32 ordinal, const struct AiDecision* decision, void* context)
{
    u32* count = context;
    CHECK(ordinal == *count, "enumerator ordinals must be contiguous");
    sEnumeratedActions[*count] = *decision;
    (*count)++;
    return true;
}

static enum ExpansionAutoplayPlannerEnumerationResult CollectActions(u32* count)
{
    *count = 0;
    return ExpansionAutoplayPlanner_EnumerateLegalActions(CollectAction, count, NULL);
}

static bool SelectAction(struct AiDecision* decision, int actionId, int occurrence,
                         u32* ordinal, struct ExpansionAutoplayPlannerActionV2* action)
{
    u32 count, index, seen = 0;
    u32 actionPageCount, actionPage, actionStart, actionCount;

    if (CollectActions(&count) != EXPANSION_AUTOPLAY_PLANNER_ENUMERATION_OK)
        return false;
    for (index = 0; index < count; index++)
    {
        if (sEnumeratedActions[index].actionId == actionId
            && seen++ == (u32)occurrence)
            break;
    }
    if (index == count
        || !ResetAndStartPlanner()
        || ExpansionAutoplayPlanner_OfferDecision(decision)
            != EXPANSION_AUTOPLAY_PLANNER_DECISION_WAIT)
        return false;
    actionPageCount = (count + EXPANSION_AUTOPLAY_PLANNER_ACTION_CAPACITY - 1)
        / EXPANSION_AUTOPLAY_PLANNER_ACTION_CAPACITY;
    actionStart = index / EXPANSION_AUTOPLAY_PLANNER_ACTION_CAPACITY
        * EXPANSION_AUTOPLAY_PLANNER_ACTION_CAPACITY;
    actionCount = count - actionStart;
    if (actionCount > EXPANSION_AUTOPLAY_PLANNER_ACTION_CAPACITY)
        actionCount = EXPANSION_AUTOPLAY_PLANNER_ACTION_CAPACITY;
    actionPage = gExpansionAutoplayPlannerObservation.pageCount
        - actionPageCount
        + index / EXPANSION_AUTOPLAY_PLANNER_ACTION_CAPACITY;
    if (!PageMatches(decision, actionPage, EXPANSION_AUTOPLAY_PLANNER_PAGE_ACTIONS,
                     actionStart, actionCount))
        return false;
    *ordinal = index;
    *action = gExpansionAutoplayPlannerObservation.payload.actions[
        index % EXPANSION_AUTOPLAY_PLANNER_ACTION_CAPACITY];
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

static void SetupTestUnit(struct Unit* unit, struct CharacterData* character,
                          struct ClassData* unitClass, int index, int x, int y)
{
    unit->pCharacterData = character;
    unit->pClassData = unitClass;
    unit->index = index;
    unit->xPos = x;
    unit->yPos = y;
    unit->maxHP = 20;
    unit->curHP = 20;
}

static void BindFixtureMaps(void)
{
    int y;
    for (y = 0; y < 17; y++)
    {
        sMovementRows[y] = sMovementData[y];
        sUnitRows[y] = sUnitData[y];
        sTerrainRows[y] = sTerrainData[y];
        sFogRows[y] = sFogData[y];
        sRangeRows[y] = sRangeData[y];
    }
    gBmMapMovement = sMovementRows;
    gBmMapUnit = sUnitRows;
    gBmMapTerrain = sTerrainRows;
    gBmMapFog = sFogRows;
    gBmMapRange = sRangeRows;
    gWorkingBmMap = gBmMapRange;
}

static void FillFixtureMaps(int movement)
{
    int y;
    int x;
    for (y = 0; y < 17; y++)
    {
        for (x = 0; x < 32; x++)
        {
            sMovementData[y][x] = movement;
            sUnitData[y][x] = 0;
            sTerrainData[y][x] = 1;
            sFogData[y][x] = 1;
            sRangeData[y][x] = 0;
        }
    }
}

static void ResetActionFixture(int width, int height)
{
    FillFixtureMaps(MAP_MOVEMENT_MAX + 1);
    memset(&sUnit, 0, sizeof(sUnit));
    memset(&sAlly, 0, sizeof(sAlly));
    memset(&sEnemy, 0, sizeof(sEnemy));
    memset(&sSummon, 0, sizeof(sSummon));
    memset(sTraps, 0, sizeof(sTraps));
    memset(&gActionData, 0, sizeof(gActionData));
    sCharacter.number = 1;
    sClass.number = 1;
    sClass.attributes = 0;
    SetupTestUnit(&sUnit, &sCharacter, &sClass, 1, 2, 2);
    gActiveUnit = &sUnit;
    gActiveUnitId = 1;
    gBmMapSize.x = width;
    gBmMapSize.y = height;
    BindFixtureMaps();
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
    sRedUnitCount = 0;
    sTrapApplyCount = 0;
    sConsumedSlot = -1;
    sTrapContractFailed = false;
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

static struct AiDecision* GetActionId(u32 count, int actionId, int actionIndex)
{
    int index;
    for (index = 0; index < (int)count; index++)
    {
        if (sEnumeratedActions[index].actionId != actionId)
            continue;
        if (actionIndex-- == 0)
            return &sEnumeratedActions[index];
    }
    return NULL;
}

static int TestActionSemanticEffects(void)
{
    ResetActionFixture(8, 8);
    sCharacter.number = CHARACTER_EWAN;
    sClass.attributes = CA_SUMMON;
    SetupTestUnit(&sAlly, &sAllyCharacter, &sAllyClass, 2, 4, 4);
    CHECK(ActionSemantics_ApplyTorchTarget(1, 6)
              && ActionSemantics_ApplyTorchTarget(6, 1) && !sTrapContractFailed
              && sTrapApplyCount == 2 && sTraps[1].xPos == 6 && sTraps[1].yPos == 1,
          "Torch effect must use both selected coordinates");
    CHECK(!ActionSemantics_ApplyTorchTarget(8, 1) && sTrapApplyCount == 2,
          "out-of-bounds Torch coordinate must not apply");
    CHECK(ActionSemantics_ApplyWarpTarget(&sAlly, 1, 5)
              && sAlly.xPos == 1 && sAlly.yPos == 5
              && ActionSemantics_ApplyWarpTarget(&sAlly, 6, 2)
              && sAlly.xPos == 6 && sAlly.yPos == 2,
          "Warp must apply both selected destinations");
    CHECK(!ActionSemantics_ApplyWarpTarget(&sAlly, -1, 2) && sAlly.xPos == 6
              && sAlly.yPos == 2,
          "invalid Warp coordinates must preserve the target");
    CHECK(ActionSemantics_ApplyUnlockTarget(3, 4)
              && gBattleTarget.unit.xPos == 3 && gBattleTarget.unit.yPos == 4
              && ActionSemantics_ApplyUnlockTarget(5, 1)
              && gBattleTarget.unit.xPos == 5 && gBattleTarget.unit.yPos == 1,
          "Unlock must lower both selected coordinates");
    CHECK(!ActionSemantics_ApplyUnlockTarget(3, 9) && gBattleTarget.unit.xPos == 5
              && gBattleTarget.unit.yPos == 1,
          "invalid Unlock coordinates must preserve the target");
    sAlly.items[0] = 0x0101;
    sAlly.items[1] = 0x0202;
    CHECK(ActionSemantics_ApplyHammerneTarget(&sAlly, 1)
              && sAlly.items[0] == 0x0101 && sAlly.items[1] == 0xFF02
              && ActionSemantics_ApplyHammerneTarget(&sAlly, 0)
              && sAlly.items[0] == 0xFF01,
          "Hammerne must repair only each selected target slot");
    CHECK(!ActionSemantics_ApplyHammerneTarget(&sAlly, 4) && sAlly.items[4] == 0,
          "stale Hammerne slot must fail without mutation");
    sUnit.items[2] = ITEM_CHESTKEY | (3 << 8);
    CHECK(ActionSemantics_ConsumePickKey(&sUnit, 2)
              && sUnit.items[2] == (ITEM_CHESTKEY | (2 << 8))
              && sConsumedSlot == 2,
          "Pick key path must consume the selected slot");
    sConsumedSlot = -1;
    CHECK(ActionSemantics_ConsumePickKey(&sUnit, 0xFF)
              && sConsumedSlot == -1
              && !ActionSemantics_ConsumePickKey(&sUnit, UNIT_ITEM_COUNT),
          "Rogue and invalid Pick slots must not consume an item");
    sUnitData[2][2] = 1;
    CHECK(ActionSemantics_IsNormalSummonTarget(&sUnit, 2, 3, 2, 2),
          "normal Summon must allow the vacated origin tile");
    sUnitData[2][2] = 0;
    sFogData[1][2] = 0;
    CHECK(!ActionSemantics_IsNormalSummonTarget(&sUnit, 2, 2, 2, 1),
          "normal Summon must reject hidden tiles");
    sFogData[1][2] = 1;
    sAllyCharacter.number = CHARACTER_SUMMON_EWAN;
    sAlly.state = US_NOT_DEPLOYED;
    CHECK(ActionSemantics_IsNormalSummonAvailable(&sUnit, false)
              && sAlly.state == US_NOT_DEPLOYED
              && ActionSemantics_IsNormalSummonAvailable(&sUnit, true)
              && !(sAlly.state & US_UNAVAILABLE),
          "summon availability must preserve planner and player restoration");
    sAlly.pCharacterData = NULL;
    sUnit.index = FACTION_RED + 1;
    CHECK(!ActionSemantics_IsNormalSummonAvailable(&sUnit, false),
          "non-player summoner must not receive the player command");
    sUnit.index = 1;
    sClass.number = CLASS_DEMON_KING;
    sEnemy.pCharacterData = &sEnemyCharacter;
    sEnemy.pClassData = &sEnemyClass;
    sRedUnitCount = 40;
    CHECK(ActionSemantics_IsDarkSummonAvailable(&sUnit),
          "dark summon must allow the forty-unit boundary");
    sRedUnitCount = 41;
    CHECK(!ActionSemantics_IsDarkSummonAvailable(&sUnit),
          "dark summon must reject a forty-first red unit");
    sRedUnitCount = 0;
    sClass.number = 1;
    CHECK(!ActionSemantics_IsDarkSummonAvailable(&sUnit),
          "non-Demon-King unit must reject dark summon");
    return 0;
}

static int TestCoordinateActionFamilies(void)
{
    u32 count;
    struct AiDecision first;
    struct AiDecision second;
    int index;
    ResetActionFixture(6, 6);
    sUnit.items[0] = ITEM_STAFF_TORCH;
    CHECK(CollectActions(&count) == EXPANSION_AUTOPLAY_PLANNER_ENUMERATION_OK,
          "Torch enumeration must succeed");
    first.actionPerformed = false;
    second.actionPerformed = false;
    for (index = 0; index < (int)count; index++)
    {
        struct AiDecision* candidate = &sEnumeratedActions[index];
        if (candidate->actionId != AI_ACTION_STAFF
            || candidate->itemSlot != 0)
            continue;
        CHECK(ActionSemantics_IsStandingReachPosition(gActiveUnit, candidate->xMove,
                                                      candidate->yMove, REACH_MAGBY2,
                                                      candidate->xTarget, candidate->yTarget),
              "every Torch candidate must target a legal bounded tile");
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
    SetupTestUnit(&sAlly, &sAllyCharacter, &sAllyClass, 2, 3, 2);
    sUnitData[2][3] = 2;
    CollectActions(&count);
    first = *GetActionId(count, AI_ACTION_STAFF, 0);
    second = *GetActionId(count, AI_ACTION_STAFF, 1);
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
    CollectActions(&count);
    first = *GetActionId(count, AI_ACTION_STAFF, 0);
    second = *GetActionId(count, AI_ACTION_STAFF, 1);
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
    SetupTestUnit(&sAlly, &sAllyCharacter, &sAllyClass, 2, 3, 2);
    sAlly.items[0] = 0x0101;
    sAlly.items[1] = 0x0202;
    sUnitData[2][3] = 2;
    CollectActions(&count);
    first = *GetActionId(count, AI_ACTION_STAFF, 0);
    second = *GetActionId(count, AI_ACTION_STAFF, 1);
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
    return 0;
}

static bool PickSlotsMatch(u32 count, int x, int y, const u8* slots, int slotCount)
{
    int index;
    if (CountActionId(count, AI_ACTION_PICK) != slotCount)
        return false;
    for (index = 0; index < slotCount; index++)
    {
        struct AiDecision* action = GetActionId(count, AI_ACTION_PICK, index);
        if (action == NULL || action->xTarget != x || action->yTarget != y
            || action->itemSlot != slots[index])
            return false;
    }
    return true;
}

static int TestPickActionFamily(void)
{
    static const u8 sDoorSlots[] = { 0, 1, 3 };
    static const u8 sChestSlots[] = { 0, 1, 2, 4 };
    static const u8 sAllSlots[] = { 0, 1, 2, 3, 4 };
    static const u8 sBridgeSlot[] = { 1 };
    struct AiDecision selected;
    u32 count, stateBefore;
    ResetActionFixture(6, 6);
    sTerrainData[2][2] = TERRAIN_CHEST_FULL;
    sTerrainData[2][3] = TERRAIN_DOOR;
    sClass.number = CLASS_ROGUE;
    CollectActions(&count);
    CHECK(CountActionId(count, AI_ACTION_PICK) == 2
              && GetActionId(count, AI_ACTION_PICK, 0)->itemSlot == 0xFF
              && GetActionId(count, AI_ACTION_PICK, 1)->itemSlot == 0xFF,
          "Rogue Pick must remain one item-free action per target");
    ResetActionFixture(6, 6);
    sTerrainData[2][3] = TERRAIN_DOOR;
    sClass.attributes = CA_THIEF;
    sUnit.items[0] = ITEM_LOCKPICK | (3 << 8);
    sUnit.items[1] = ITEM_DOORKEY | (2 << 8);
    sUnit.items[2] = ITEM_CHESTKEY | (2 << 8);
    sUnit.items[3] = ITEM_DOORKEY | (1 << 8);
    sUnit.items[4] = ITEM_VULNERARY | (2 << 8);
    stateBefore = RuntimeStateDigest();
    CollectActions(&count);
    CHECK(stateBefore == RuntimeStateDigest()
              && PickSlotsMatch(count, 3, 2, sDoorSlots, ARRAY_COUNT(sDoorSlots)),
          "Door Pick must enumerate Lockpick and every Door Key slot in slot order");
    selected = *GetActionId(count, AI_ACTION_PICK, 1);
    CollectActions(&count);
    CHECK(memcmp(&selected, GetActionId(count, AI_ACTION_PICK, 1), sizeof(selected)) == 0,
          "Pick target and inventory-slot ordering must be deterministic");
    CHECK(ExpansionAutoplayPlanner_PrepareActionData(&selected)
              && gActionData.itemSlotIndex == 1,
          "nonpreferred Door Key must lower its exact inventory slot");
    sUnit.items[1] = ITEM_DOORKEY;
    CHECK(!ExpansionAutoplayPlanner_PrepareActionData(&selected),
          "depleted selected key must reject while other keys remain");
    sUnit.items[1] = sUnit.items[2];
    CHECK(!ExpansionAutoplayPlanner_PrepareActionData(&selected),
          "wrong-purpose key swapped into selected slot must reject");
    sUnit.items[1] = ITEM_DOORKEY | (2 << 8);
    sUnit.items[2] = ITEM_DOORKEY | (4 << 8);
    sUnit.items[4] = ITEM_LOCKPICK | (5 << 8);
    CollectActions(&count);
    CHECK(PickSlotsMatch(count, 3, 2, sAllSlots, ARRAY_COUNT(sAllSlots)),
          "five applicable inventory stacks must remain bounded and distinct");
    ResetActionFixture(6, 6);
    sTerrainData[2][2] = TERRAIN_CHEST_FULL;
    sClass.attributes = CA_THIEF;
    sUnit.items[0] = ITEM_LOCKPICK | (3 << 8);
    sUnit.items[1] = ITEM_CHESTKEY | (2 << 8);
    sUnit.items[2] = ITEM_CHESTKEY_BUNDLE | (5 << 8);
    sUnit.items[3] = ITEM_DOORKEY | (2 << 8);
    sUnit.items[4] = ITEM_CHESTKEY | (1 << 8);
    CollectActions(&count);
    CHECK(PickSlotsMatch(count, 2, 2, sChestSlots, ARRAY_COUNT(sChestSlots)),
          "Chest Pick must include Lockpick, both chest-key types, and duplicate stacks");
    sTerrainData[2][2] = 1;
    sTerrainData[2][3] = TERRAIN_BRIDGE_14;
    sUnit.items[0] = ITEM_DOORKEY | (2 << 8);
    sUnit.items[1] = ITEM_LOCKPICK | (3 << 8);
    CollectActions(&count);
    CHECK(PickSlotsMatch(count, 3, 2, sBridgeSlot, ARRAY_COUNT(sBridgeSlot)),
          "bridge Pick must exclude Door and Chest Keys and retain only Lockpick");
    return 0;
}

static int TestSnagActionFamily(void)
{
    u32 count = 0;
    struct AiDecision* combat;
    struct AiDecision* firstSnag;
    struct AiDecision* secondSnag;
    ResetActionFixture(6, 6);
    sUnit.items[0] = ITEM_SWORD_IRON | (30 << 8);
    SetupTestUnit(&sEnemy, &sEnemyCharacter, &sEnemyClass, 0x81, 1, 2);
    sUnitData[2][1] = 0x81;
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
    MakeTargetListForWeapon(&sUnit, sUnit.items[0]);
    CHECK(sTargetCount == 3
              && (u8)sTargets[0].uid == 0x81
              && sTargets[1].uid == 0
              && sTargets[2].uid == 0,
          "production weapon builder must retain unit and snag targets");
    CHECK(CollectActions(&count)
            == EXPANSION_AUTOPLAY_PLANNER_ENUMERATION_OK
            && CountActionId(count, AI_ACTION_COMBAT) == 3,
        "combat enumeration must include one unit and two snag targets");
    combat = GetActionId(count, AI_ACTION_COMBAT, 0);
    firstSnag = GetActionId(count, AI_ACTION_COMBAT, 1);
    secondSnag = GetActionId(count, AI_ACTION_COMBAT, 2);
    CHECK(combat->targetId == 0x81
            && firstSnag->targetId == 0
            && firstSnag->xTarget == 3
            && firstSnag->yTarget == 2
            && secondSnag->targetId == 0
            && secondSnag->xTarget == 2
            && secondSnag->yTarget == 3,
        "snag candidates must follow unit targets in stable trap order");
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
    struct AiDecision* first;
    struct AiDecision* second;
    ResetActionFixture(8, 8);
    sUnit.items[0] = ITEM_STAFF_REPAIR;
    SetupTestUnit(&sAlly, &sAllyCharacter, &sAllyClass, 2, 3, 2);
    sAlly.items[0] = 0x0101;
    sAlly.items[1] = 0x0202;
    sUnitData[2][3] = 2;
    SetupTestUnit(&sEnemy, &sEnemyCharacter, &sEnemyClass, 0x41, 2, 3);
    sEnemy.items[0] = 0x0101;
    sUnitData[3][2] = 0x41;
    SetupTestUnit(&sSummon, &sSummonCharacter, &sSummonClass, 0x81, 1, 2);
    sSummon.items[0] = 0x0101;
    sUnitData[2][1] = 0x81;
    MakeTargetListForHammerne(&sUnit);
    CHECK(sTargetCount == 1
              && sTargets[0].uid == 2
              && !IsUnitInHammerneTargetList(&sUnit, &sEnemy)
              && !IsUnitInHammerneTargetList(&sUnit, &sSummon),
          "production Hammerne builder must retain only same-faction targets");
    CHECK(CollectActions(&count)
            == EXPANSION_AUTOPLAY_PLANNER_ENUMERATION_OK
            && CountActionId(count, AI_ACTION_STAFF) == 2,
        "Hammerne must retain only same-faction repairable slots");
    first = GetActionId(count, AI_ACTION_STAFF, 0);
    second = GetActionId(count, AI_ACTION_STAFF, 1);
    CHECK(first->targetId == 2
              && first->unk04 == 0
              && second->targetId == 2
              && second->unk04 == 1,
          "Hammerne slot ordering must remain deterministic");
    CHECK(ExpansionAutoplayPlanner_PrepareActionData(second)
              && gActionData.trapType == 1,
          "Hammerne must revalidate and lower the same-faction slot");
    ResetActionFixture(8, 8);
    sUnit.items[0] = ITEM_STAFF_FORTIFY;
    sUnit.curHP = 10;
    sMagRange = 3;
    CollectActions(&count);
    CHECK(CountActionId(count, AI_ACTION_STAFF) == 0,
          "Fortify must not target an injured caster");
    sUnit.curHP = sUnit.maxHP;
    SetupTestUnit(&sAlly, &sAllyCharacter, &sAllyClass, 2, 6, 2);
    sAlly.curHP = 10;
    sUnitData[2][6] = 2;
    CollectActions(&count);
    MakeTargetListForRangedHeal(&sUnit);
    CHECK(CountActionId(count, AI_ACTION_STAFF) == 0
              && sTargetCount == 0,
          "Fortify must reject an ally outside MAG/2 range");
    sAlly.xPos = 5;
    sUnitData[2][6] = 0;
    sUnitData[2][5] = 2;
    CollectActions(&count);
    MakeTargetListForRangedHeal(&sUnit);
    CHECK(CountActionId(count, AI_ACTION_STAFF) == 1
              && sTargetCount == 1,
          "Fortify must retain an injured ally inside MAG/2 range");
    ResetActionFixture(8, 8);
    sUnit.items[0] = ITEM_STAFF_LATONA;
    sUnit.curHP = 10;
    CollectActions(&count);
    MakeTargetListForLatona(&sUnit);
    CHECK(CountActionId(count, AI_ACTION_STAFF) == 0
              && sTargetCount == 0,
          "Latona must exclude an injured caster");
    SetupTestUnit(&sAlly, &sAllyCharacter, &sAllyClass, 2, 7, 7);
    sAlly.curHP = 10;
    CollectActions(&count);
    MakeTargetListForLatona(&sUnit);
    CHECK(CountActionId(count, AI_ACTION_STAFF) == 1
              && sTargetCount == 1,
          "Latona must retain an injured non-caster in its phase domain");
    return 0;
}

static int TestSummonActionFamily(void)
{
    struct AiDecision first, second, adversary;
    u32 count, stateBefore;
    int summonCount, index;
    ResetActionFixture(6, 6);
    sCharacter.number = CHARACTER_EWAN;
    sClass.attributes = CA_SUMMON;
    stateBefore = RuntimeStateDigest();
    CHECK(CollectActions(&count)
            == EXPANSION_AUTOPLAY_PLANNER_ENUMERATION_OK,
        "normal Summon enumeration must succeed");
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
    CollectActions(&count);
    CHECK(CountActionId(count, AI_ACTION_SUMMON) == 0,
          "missing gSummonConfig entry must publish no normal Summon");
    gSummonConfig[0][0] = CHARACTER_EWAN;
    gSummonConfig[0][1] = CHARACTER_SUMMON_EWAN;
    sSummonCharacter.number = CHARACTER_SUMMON_EWAN;
    sSummonClass.number = CLASS_PHANTOM;
    SetupTestUnit(&sSummon, &sSummonCharacter, &sSummonClass, 3, 0, 0);
    sSummon.state = 0;
    CollectActions(&count);
    CHECK(CountActionId(count, AI_ACTION_SUMMON) == 0,
          "available existing summon must block another normal Summon");
    sSummon.state = US_NOT_DEPLOYED;
    CollectActions(&count);
    CHECK(CountActionId(count, AI_ACTION_SUMMON) == 4
              && sSummon.state == US_NOT_DEPLOYED,
          "unavailable existing summon must be reusable without enumeration mutation");
    sUnit.state = US_HAS_MOVED;
    CollectActions(&count);
    CHECK(CountActionId(count, AI_ACTION_SUMMON) == 0,
          "moved summoner must publish no normal Summon");
    sUnit.state = 0;
    sClass.attributes = 0;
    CollectActions(&count);
    CHECK(CountActionId(count, AI_ACTION_SUMMON) == 0,
          "unit without CA_SUMMON must publish no normal Summon");
    ResetActionFixture(6, 6);
    sClass.number = CLASS_DEMON_KING;
    CollectActions(&count);
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
    CHECK(CollectActions(&count)
            == EXPANSION_AUTOPLAY_PLANNER_ENUMERATION_UNAVAILABLE
            && count == 0,
        "not-deployed active unit must be unavailable before enumeration");
    ResetActionFixture(6, 6);
    sUnit.items[0] = ITEM_SWORD_IRON;
    sEnemyCharacter.number = 3;
    sEnemyClass.number = 3;
    SetupTestUnit(&sEnemy, &sEnemyCharacter, &sEnemyClass, 0x81, 3, 2);
    sEnemy.state = US_NOT_DEPLOYED;
    CollectActions(&count);
    CHECK(CountActionId(count, AI_ACTION_COMBAT) == 0,
          "not-deployed stale-coordinate target must not be legal");
    sEnemy.state = 0;
    CollectActions(&count);
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
    CHECK(ResetAndStartPlanner(),
          "maximum semantic-page run must start");
    CHECK(ExpansionAutoplayPlanner_OfferDecision(&decision)
                == EXPANSION_AUTOPLAY_PLANNER_DECISION_WAIT
            && gExpansionAutoplayPlannerObservation.pageCount == 53,
        "maximum units and flags must use bounded canonical pages");
    CHECK(PageMatches(
            &decision, 7, EXPANSION_AUTOPLAY_PLANNER_PAGE_UNITS, 115, 17),
        "maximum unit page boundary must be exact");
    CHECK(PageMatches(
            &decision, 13, EXPANSION_AUTOPLAY_PLANNER_PAGE_INVENTORY, 575, 85)
            && ((gExpansionAutoplayPlannerObservation
                    .payload.inventory[0].identity >> 8) & 0xFF) == 0xA2
            && ((gExpansionAutoplayPlannerObservation
                    .payload.inventory[0].identity >> 16) & 0xFF) == 0
            && ((gExpansionAutoplayPlannerObservation
                    .payload.inventory[84].identity >> 8) & 0xFF) == 0xB2
            && ((gExpansionAutoplayPlannerObservation
                    .payload.inventory[84].identity >> 16) & 0xFF) == 4,
        "maximum inventory page boundary must be exact");
    CHECK(PageMatches(
            &decision, 15, EXPANSION_AUTOPLAY_PLANNER_PAGE_RESOURCES, 115, 2),
        "maximum resource page boundary must be exact");
    CHECK(PageMatches(
            &decision, 51, EXPANSION_AUTOPLAY_PLANNER_PAGE_FLAGS, 4025, 71),
        "maximum flag page boundary must be exact");
    CHECK(PageMatches(
            &decision, 52, EXPANSION_AUTOPLAY_PLANNER_PAGE_ACTIONS, 0, 1),
        "action page must follow every maximum semantic page");
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
    u32 availableZeroCheckpointDigest;
    int sizes[] = { 0, 1, 256, 257, INT_MAX };
    int index;
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
    CHECK(ResetAndStartPlanner(),
          "zero-digest availability run must start");
    CHECK(ExpansionAutoplayPlanner_OfferDecision(&decision)
                == EXPANSION_AUTOPLAY_PLANNER_DECISION_WAIT
            && gExpansionAutoplayPlannerObservation
                    .payload.summary.fields[6].availability
                == EXPANSION_AUTOPLAY_PLANNER_AVAILABLE
            && gExpansionAutoplayPlannerObservation
                    .payload.summary.fields[6].value == 0
            && gExpansionAutoplayPlannerObservation
                    .payload.summary.fields[7].availability
                == EXPANSION_AUTOPLAY_PLANNER_AVAILABLE
            && gExpansionAutoplayPlannerObservation
                    .payload.summary.fields[7].value == 0,
        "valid zero flag and convoy/resource digests must remain available");
    ExpansionAutoplayPlanner_RecordCampaignCheckpoint();
    availableZeroCheckpointDigest =
        gExpansionAutoplayPlannerCampaignCheckpoint.semanticStateDigest;
    ExpansionAutoplayPlanner_Reset();
    sFlagPointersAvailable = false;
    sConvoyAvailable = false;
    ExpansionAutoplayPlanner_OnMapReady();
    ExpansionAutoplayPlanner_PollStart();
    CHECK(StartPreparedPlanner(),
          "null semantic-domain run must start");
    CHECK(ExpansionAutoplayPlanner_OfferDecision(&decision)
                == EXPANSION_AUTOPLAY_PLANNER_DECISION_WAIT
            && gExpansionAutoplayPlannerObservation
                    .payload.summary.fields[6].availability
                == EXPANSION_AUTOPLAY_PLANNER_UNINITIALIZED
            && gExpansionAutoplayPlannerObservation
                    .payload.summary.fields[7].availability
                == EXPANSION_AUTOPLAY_PLANNER_UNINITIALIZED,
        "null flag and convoy domains must be unavailable");
    ExpansionAutoplayPlanner_Reset();
    sFlagPointersAvailable = true;
    sConvoyAvailable = true;
    sPermanentFlagSize = 257;
    ExpansionAutoplayPlanner_OnMapReady();
    ExpansionAutoplayPlanner_PollStart();
    CHECK(StartPreparedPlanner(),
          "out-of-bounds flag-domain run must start");
    CHECK(ExpansionAutoplayPlanner_OfferDecision(&decision)
                == EXPANSION_AUTOPLAY_PLANNER_DECISION_WAIT
            && gExpansionAutoplayPlannerObservation
                    .payload.summary.fields[6].availability
                == EXPANSION_AUTOPLAY_PLANNER_UNINITIALIZED,
        "out-of-bounds flag storage must be unavailable");
    ExpansionAutoplayPlanner_RecordCampaignCheckpoint();
    CHECK(gExpansionAutoplayPlannerCampaignCheckpoint.semanticStateDigest
            != availableZeroCheckpointDigest,
        "unavailable flag storage must not alias an available zero digest");
    sChapterFlagSize = 0;
    for (index = 0; index < (int)ARRAY_COUNT(sizes); index++)
    {
        sPermanentFlagSize = sizes[index];
        ExpansionAutoplayPlanner_RecordCampaignCheckpoint();
        CHECK(
            gExpansionAutoplayPlannerCampaignCheckpoint.magic
                == EXPANSION_AUTOPLAY_PLANNER_MAGIC,
            "checkpoint flag bounds must fail safely without invalid reads"
        );
    }
    sFlagPointersAvailable = false;
    sPermanentFlagSize = 256;
    ExpansionAutoplayPlanner_RecordCampaignCheckpoint();
    CHECK(gExpansionAutoplayPlannerCampaignCheckpoint.magic
                == EXPANSION_AUTOPLAY_PLANNER_MAGIC
            && gExpansionAutoplayPlannerCampaignCheckpoint.semanticStateDigest
                != availableZeroCheckpointDigest,
        "null checkpoint flag domains must fail safely and remain distinct");
    sFlagPointersAvailable = true;
    sPermanentFlagSize = 8;
    sChapterFlagSize = 8;
    gPlaySt.partyGoldAmount = 1234;
    memset(sPermanentFlags, 0, sizeof(sPermanentFlags));
    memset(sChapterFlags, 0, sizeof(sChapterFlags));
    memset(sConvoy, 0, sizeof(sConvoy));
    sControlRequests = 0;
    return 0;
}

static int TestInventorySlotWireIdentity(void)
{
    struct AiDecision decision = { 0 };
    struct ExpansionAutoplayPlannerActionV2 first, second;
    u32 checkpointDigest, ordinal;
    u16 item;
    ResetActionFixture(6, 6);
    sUnit.items[0] = ITEM_STAFF_REPAIR;
    sAllyCharacter.number = 2;
    sAllyClass.number = 2;
    SetupTestUnit(&sAlly, &sAllyCharacter, &sAllyClass, 2, 3, 2);
    sAlly.items[0] = 0x0101;
    sAlly.items[1] = 0x0202;
    sUnitData[2][3] = 2;
    CHECK(StartActionPage(&decision, 3),
          "Hammerne candidates must traverse the fixed action page");
    first = gExpansionAutoplayPlannerObservation.payload.actions[1];
    second = gExpansionAutoplayPlannerObservation.payload.actions[2];
    CHECK(first.itemSlot == 0x0000
            && second.itemSlot == 0x0100
            && memcmp(&first.token0, &second.token0, sizeof(first.token0) * 4) != 0,
        "Hammerne target slot must be packed and token-bound");
    CHECK(CommitCurrent(
            &decision,
            gExpansionAutoplayPlannerObservation.start.actionStartOrdinal + 2,
            &first.token0)
                == EXPANSION_AUTOPLAY_PLANNER_DECISION_WAIT
            && gExpansionAutoplayPlannerObservation.rejection
                == EXPANSION_AUTOPLAY_PLANNER_REJECTION_TOKEN_MISMATCH,
        "Hammerne token for another inventory slot must reject");
    CHECK(CommitCurrent(
            &decision,
            gExpansionAutoplayPlannerObservation.start.actionStartOrdinal + 2,
            &second.token0)
                == EXPANSION_AUTOPLAY_PLANNER_DECISION_ACCEPTED
            && decision.unk04 == 1
            && ExpansionAutoplayPlanner_PrepareActionData(&decision)
            && gActionData.trapType == 1,
        "matching Hammerne slot token must lower the selected slot");
    ResetActionFixture(6, 6);
    sTerrainData[2][3] = TERRAIN_DOOR;
    sClass.attributes = CA_THIEF;
    sUnit.items[0] = ITEM_LOCKPICK | (3 << 8);
    sUnit.items[1] = ITEM_DOORKEY | (2 << 8);
    CHECK(StartActionPage(&decision, 3),
        "Wait plus two Pick inventory choices must share the action page");
    first = gExpansionAutoplayPlannerObservation.payload.actions[1];
    second = gExpansionAutoplayPlannerObservation.payload.actions[2];
    CHECK(first.itemSlot == 0xFF00
            && second.itemSlot == 0xFF01
            && memcmp(&first.token0, &second.token0, sizeof(first.token0) * 4) != 0,
        "Pick inventory slots must be distinct and token-bound");
    ordinal = gExpansionAutoplayPlannerObservation.start.actionStartOrdinal + 2;
    sUnit.items[1] = ITEM_DOORKEY | (1 << 8);
    CHECK(CommitBecameIllegal(&decision, ordinal, &second.token0),
        "changed uses in still-applicable Pick slot must stale identity");
    sUnit.items[1] = ITEM_DOORKEY;
    CHECK(CommitBecameIllegal(&decision, ordinal, &second.token0),
        "depleted selected Pick slot must reject while Lockpick remains");
    sUnit.items[1] = ITEM_DOORKEY | (2 << 8);
    item = sUnit.items[0];
    sUnit.items[0] = sUnit.items[1];
    sUnit.items[1] = item;
    CHECK(CommitBecameIllegal(&decision, ordinal, &second.token0),
        "swapped applicable Pick stacks must invalidate candidate identity");
    item = sUnit.items[0];
    sUnit.items[0] = sUnit.items[1];
    sUnit.items[1] = item;
    sUnit.items[1] = ITEM_CHESTKEY | (2 << 8);
    CHECK(CommitBecameIllegal(&decision, ordinal, &second.token0),
        "wrong-purpose selected key must reject while Lockpick remains");
    sUnit.items[1] = ITEM_DOORKEY | (2 << 8);
    CHECK(CommitCurrent(
            &decision,
            ordinal,
            &second.token0)
                == EXPANSION_AUTOPLAY_PLANNER_DECISION_ACCEPTED
            && decision.itemSlot == 1,
        "matching nonpreferred Pick token must commit the selected stack");
    item = sUnit.items[1];
    sUnit.items[1] = sUnit.items[0];
    CHECK(!ExpansionAutoplayPlanner_PrepareActionData(&decision),
          "post-commit applicable stack swap must reject");
    sUnit.items[1] = item;
    CHECK(ExpansionAutoplayPlanner_PrepareActionData(&decision)
              && gActionData.itemSlotIndex == 1,
          "unchanged committed Pick stack must lower its exact slot");
    ExpansionAutoplayPlanner_RecordCampaignCheckpoint();
    checkpointDigest =
        gExpansionAutoplayPlannerCampaignCheckpoint.semanticStateDigest;
    item = sUnit.items[0];
    CHECK(ActionSemantics_ConsumePickKey(gActiveUnit, gActionData.itemSlotIndex)
              && sConsumedSlot == 1
              && sUnit.items[0] == item
              && sUnit.items[1] == (ITEM_DOORKEY | (1 << 8)),
          "Pick execution must consume only the selected nonpreferred stack");
    ExpansionAutoplayPlanner_RecordCampaignCheckpoint();
    CHECK(checkpointDigest
              != gExpansionAutoplayPlannerCampaignCheckpoint.semanticStateDigest,
          "selected Pick stack consumption must alter the campaign digest");
    return 0;
}

static int TestCandidateInventoryBinding(void)
{
    struct AiDecision decision = { 0 };
    struct ExpansionAutoplayPlannerActionV2 original, refreshed;
    u32 ordinal;
    u16 item;

    ResetActionFixture(5, 5);
    sUnit.items[0] = ITEM_SWORD_IRON | (2 << 8);
    sUnit.items[1] = ITEM_SWORD_IRON | (4 << 8);
    sEnemyCharacter.number = 3;
    sEnemyClass.number = 3;
    SetupTestUnit(&sEnemy, &sEnemyCharacter, &sEnemyClass, 0x81, 3, 2);
    sUnitData[2][3] = 0x81;
    CHECK(SelectAction(&decision, AI_ACTION_COMBAT, 0, &ordinal, &original),
          "publish Combat");
    sUnit.items[0] = ITEM_SWORD_IRON | (1 << 8);
    CHECK(CommitBecameIllegal(&decision, ordinal, &original.token0), "bind Combat uses");
    CHECK(SelectAction(&decision, AI_ACTION_COMBAT, 0, &ordinal, &refreshed)
              && memcmp(&original.token0, &refreshed.token0, sizeof(original.token0) * 4) != 0,
          "bind selected Combat raw item into token");
    sUnit.items[0] = ITEM_SWORD_IRON | (2 << 8);
    CHECK(SelectAction(&decision, AI_ACTION_COMBAT, 0, &ordinal, &original),
          "publish Combat swap");
    item = sUnit.items[0];
    sUnit.items[0] = sUnit.items[1];
    sUnit.items[1] = item;
    CHECK(CommitBecameIllegal(&decision, ordinal, &original.token0), "bind Combat slots");
    item = sUnit.items[0];
    sUnit.items[0] = sUnit.items[1];
    sUnit.items[1] = item;
    sUnit.items[2] = ITEM_CHESTKEY | (2 << 8);
    CHECK(SelectAction(&decision, AI_ACTION_COMBAT, 0, &ordinal, &original),
          "publish Combat control");
    sUnit.items[2] = ITEM_CHESTKEY | (1 << 8);
    CHECK(CommitCurrent(&decision, ordinal, &original.token0)
              == EXPANSION_AUTOPLAY_PLANNER_DECISION_ACCEPTED,
          "ignore unselected unusable inventory");

    ResetActionFixture(5, 5);
    sUnit.items[0] = ITEM_STAFF_TORCH | (2 << 8);
    CHECK(SelectAction(&decision, AI_ACTION_STAFF, 0, &ordinal, &original), "publish Staff");
    sUnit.items[0] = ITEM_STAFF_TORCH | (1 << 8);
    CHECK(CommitBecameIllegal(&decision, ordinal, &original.token0), "bind Staff uses");
    sUnit.items[0] = 0;
    CHECK(CommitBecameIllegal(&decision, ordinal, &original.token0), "reject empty Staff");

    ResetActionFixture(5, 5);
    sUnit.curHP = 10;
    sUnit.items[0] = ITEM_VULNERARY | (2 << 8);
    CHECK(SelectAction(&decision, AI_ACTION_USEITEM, 0, &ordinal, &original),
          "publish use-item");
    sUnit.items[0] = ITEM_ELIXIR | (2 << 8);
    CHECK(CommitBecameIllegal(&decision, ordinal, &original.token0), "bind item replacement");

    ResetActionFixture(5, 5);
    sUnit.items[0] = ITEM_STAFF_REPAIR | (2 << 8);
    sAllyCharacter.number = 2;
    sAllyClass.number = 2;
    SetupTestUnit(&sAlly, &sAllyCharacter, &sAllyClass, 2, 3, 2);
    sAlly.items[0] = 0x0101;
    sAlly.items[1] = 0x0202;
    sUnitData[2][3] = 2;
    CHECK(SelectAction(&decision, AI_ACTION_STAFF, 1, &ordinal, &original),
          "publish Hammerne slot");
    sAlly.items[1] = 0x0102;
    CHECK(CommitBecameIllegal(&decision, ordinal, &original.token0), "bind Hammerne uses");
    sAlly.items[1] = 0x0202;
    CHECK(SelectAction(&decision, AI_ACTION_STAFF, 1, &ordinal, &original),
          "publish Hammerne swap");
    item = sAlly.items[0];
    sAlly.items[0] = sAlly.items[1];
    sAlly.items[1] = item;
    CHECK(CommitBecameIllegal(&decision, ordinal, &original.token0), "bind Hammerne swap");
    return 0;
}

static int TestCompleteEnumerator(void)
{
    u32 firstCount = 0, secondCount = 0, stateBefore;
    u32 sequenceDigest = 2166136261u, secondSequenceDigest = 2166136261u;
    u32 kinds = 0;
    int index, other;
    ResetActionFixture(3, 3);
    sCharacter.number = CHARACTER_EWAN;
    sClass.attributes = CA_STEAL | CA_SUMMON;
    sClass.number = CLASS_ROGUE;
    sUnit.xPos = 1;
    sUnit.yPos = 1;
    sMovementData[1][1] = 0;
    sUnit.maxHP = 20;
    sUnit.curHP = 10;
    sUnit.items[0] = ITEM_SWORD_IRON;
    sUnit.items[1] = ITEM_STAFF_HEAL;
    sUnit.items[2] = ITEM_VULNERARY;
    sUnitData[1][1] = 1;
    sAllyCharacter.number = 2;
    sAllyClass.number = 2;
    SetupTestUnit(&sAlly, &sAllyCharacter, &sAllyClass, 2, 1, 0);
    sAlly.curHP = 10;
    sUnitData[0][1] = 2;
    sEnemyCharacter.number = 3;
    sEnemyClass.number = 3;
    SetupTestUnit(&sEnemy, &sEnemyCharacter, &sEnemyClass, 0x81, 2, 1);
    sUnitData[1][2] = 0x81;
    sTerrainData[2][1] = TERRAIN_CHEST_FULL;
    sTerrainData[1][0] = TERRAIN_DOOR;
    stateBefore = RuntimeStateDigest();
    CHECK(CollectActions(&firstCount)
            == EXPANSION_AUTOPLAY_PLANNER_ENUMERATION_OK,
        "complete legal-action enumeration must succeed");
    CHECK(stateBefore == RuntimeStateDigest(),
          "legal-action enumeration must not mutate unit, map, or RNG state");
    CHECK(firstCount > 0, "complete legal-action enumeration must produce actions");
    for (index = 0; index < (int)firstCount; index++)
    {
        kinds |= 1u << sEnumeratedActions[index].actionId;
        sequenceDigest = DigestBytes(
            sequenceDigest, &sEnumeratedActions[index], sizeof(sEnumeratedActions[index]));
        for (other = 0; other < index; other++)
        {
            CHECK(memcmp(&sEnumeratedActions[index], &sEnumeratedActions[other],
                         sizeof(sEnumeratedActions[index])) != 0,
                  "legal-action enumeration must not publish duplicates");
        }
    }
    CHECK((kinds & (1u << AI_ACTION_NONE))
            && (kinds & (1u << AI_ACTION_COMBAT))
            && (kinds & (1u << AI_ACTION_STAFF))
            && (kinds & (1u << AI_ACTION_USEITEM))
            && (kinds & (1u << AI_ACTION_PICK))
            && (kinds & (1u << AI_ACTION_SUMMON)),
        "complete enumeration must cover every declared action family");
    CHECK(CollectActions(&secondCount)
            == EXPANSION_AUTOPLAY_PLANNER_ENUMERATION_OK,
        "repeated legal-action enumeration must succeed");
    for (index = 0; index < (int)secondCount; index++)
        secondSequenceDigest = DigestBytes(
            secondSequenceDigest, &sEnumeratedActions[index], sizeof(sEnumeratedActions[index]));
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
    u32 selectedOrdinal, selectedToken[4], forgedToken[4];
    u32 configuredConfigIdentity, configuredScenarioIdentity;
    u32 previousScenarioIdentity, previousSeedIdentity;
    int index, other, restoreBefore, selectedX, selectedY;
    gPlaySt.chapterIndex = 1;
    gPlaySt.chapterTurnNumber = 1;
    CHECK(TestActionSemanticEffects() == 0,
          "action semantics effect test");
    CHECK(TestCompleteEnumerator() == 0, "complete action enumerator test");
    CHECK(TestCoordinateActionFamilies() == 0,
          "coordinate-sensitive action family test");
    CHECK(TestPickActionFamily() == 0, "Pick inventory-slot action family test");
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
    ResetActionFixture(32, 17);
    sUnit.xPos = 0;
    sUnit.yPos = 0;
    sUnit.level = 12;
    sUnit.exp = 34;
    sUnit.pow = 5;
    sUnit.skl = 6;
    sUnit.spd = 7;
    sUnit.lck = 8;
    sUnit.def = 9;
    sUnit.res = 10;
    sUnit.conBonus = 2;
    sUnit.movBonus = 1;
    sUnit.statusIndex = UNIT_STATUS_POISON;
    sUnit.statusDuration = 3;
    sUnit.items[0] = ITEM_SWORD_IRON | (30 << 8);
    sUnit.ranks[0] = 0x1F;
    sUnit.ranks[4] = 0x20;
    sCharacter.baseCon = 1;
    sClass.baseCon = 6;
    sClass.baseMov = 5;
    sAllyCharacter.number = 2;
    sAllyClass.number = 2;
    SetupTestUnit(&sAlly, &sAllyCharacter, &sAllyClass, 2, 5, 5);
    sAlly.state = US_NOT_DEPLOYED;
    sAlly.items[0] = ITEM_VULNERARY | (2 << 8);
    sEnemy.pCharacterData = NULL;
    sSummon.pCharacterData = NULL;
    gPlaySt.partyGoldAmount = 1234;
    sConvoy[0] = ITEM_CHESTKEY | (3 << 8);
    sPermanentFlags[0] = 1;
    sChapterFlags[0] = 2;
    FillFixtureMaps(1);
    for (index = 0; index < 32; index++)
        sMovementData[16][index] = MAP_MOVEMENT_MAX + 1;
    sUnitData[0][0] = 1;
    ExpansionAutoplayPlanner_Reset();
    CHECK(gExpansionAutoplayPlannerObservation.state
            == EXPANSION_AUTOPLAY_PLANNER_STATE_DISABLED,
        "early reset must not publish stale READY identities");
    ExpansionAutoplayPlanner_OnMapReady();
    CHECK(!ExpansionAutoplayPlanner_PollStart(),
          "idle poll without a command must publish READY");
    configuredScenarioIdentity =
        gExpansionAutoplayPlannerObservation.actualScenarioIdentity;
    configuredConfigIdentity =
        gExpansionAutoplayPlannerObservation.actualConfigIdentity;
    WriteCommand((enum ExpansionAutoplayPlannerCommandKind)99, 0, 0, 0, 0, NULL);
    CHECK(!ExpansionAutoplayPlanner_PollStart(), "unknown idle command must reject");
    CHECK(gExpansionAutoplayPlannerObservation.rejection
            == EXPANSION_AUTOPLAY_PLANNER_REJECTION_PROTOCOL_ERROR,
        "unknown idle command must report protocol error");
    previousSeedIdentity =
        gExpansionAutoplayPlannerObservation.actualSeedIdentity;
    sSeeds[0] = 9;
    CHECK(!ExpansionAutoplayPlanner_PollStart()
            && gExpansionAutoplayPlannerObservation.actualSeedIdentity
                != previousSeedIdentity,
        "idle READY refresh must follow map/RNG initialization");
    WriteCommand(EXPANSION_AUTOPLAY_PLANNER_COMMAND_START, 0, 0, 0, 0, NULL);
    gExpansionAutoplayPlannerCommand.payload.start.expectedSeedIdentity =
        previousSeedIdentity;
    CHECK(!ExpansionAutoplayPlanner_PollStart()
            && gExpansionAutoplayPlannerObservation.rejection
                == EXPANSION_AUTOPLAY_PLANNER_REJECTION_PROTOCOL_ERROR,
        "a START prepared from an older READY seed must reject provenance");
    sSeeds[0] = 1;
    PreparePlannerStart();
    WriteCommand(EXPANSION_AUTOPLAY_PLANNER_COMMAND_START, 0, 0, 0, 0, NULL);
    gExpansionAutoplayPlannerCommand.payload.start.expectedRomIdentity ^= 1;
    CHECK(!ExpansionAutoplayPlanner_PollStart()
            && gExpansionAutoplayPlannerObservation.rejection
                == EXPANSION_AUTOPLAY_PLANNER_REJECTION_PROTOCOL_ERROR,
        "same header with different build identity must reject provenance");
    PreparePlannerStart();
    WriteCommand(EXPANSION_AUTOPLAY_PLANNER_COMMAND_START, 0, 0, 0, 0, NULL);
    gExpansionAutoplayPlannerCommand.payload.start.expectedScenarioIdentity ^= 1;
    CHECK(!ExpansionAutoplayPlanner_PollStart(), "mismatched provenance must reject");
    WriteCommand(EXPANSION_AUTOPLAY_PLANNER_COMMAND_START, 0, 0, 0, 0, NULL);
    CHECK(ExpansionAutoplayPlanner_PollStart(), "typed START mailbox command must activate");
    CHECK(sControlRequests == 1, "valid provenance activates computer control once");
    WriteCommand(EXPANSION_AUTOPLAY_PLANNER_COMMAND_START, 0, 0, 0, 0, NULL);
    CHECK(!ExpansionAutoplayPlanner_PollStart(), "duplicate START must reject");
    CHECK(gExpansionAutoplayPlannerObservation.rejection
            == EXPANSION_AUTOPLAY_PLANNER_REJECTION_PROTOCOL_ERROR,
        "duplicate START must report protocol error");
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
    CHECK(ExpansionAutoplayPlanner_OfferDecision(&decision)
                == EXPANSION_AUTOPLAY_PLANNER_DECISION_WAIT
            && ExpansionAutoplayPlanner_IsActive(),
        "first production decision must publish one legal token");
    CHECK(gExpansionAutoplayPlannerObservation.totalActionCount == 512,
        "complete enumerator must retain the 512-candidate boundary");
    CHECK(sizeof(gExpansionAutoplayPlannerObservation)
                <= EXPANSION_AUTOPLAY_PLANNER_PAGE_MAX_BYTES
            && gExpansionAutoplayPlannerObservation.pageCount == 33
            && gExpansionAutoplayPlannerObservation.pageKind
                == EXPANSION_AUTOPLAY_PLANNER_PAGE_SUMMARY
            && gExpansionAutoplayPlannerObservation.count.recordCount
                == EXPANSION_AUTOPLAY_PLANNER_SEMANTIC_FIELD_CAPACITY,
        "summary/map/unit/action pages must share the fixed-width boundary");
    CHECK(gExpansionAutoplayPlannerObservation.payload.summary.fields[0].availability
                == EXPANSION_AUTOPLAY_PLANNER_AVAILABLE
            && gExpansionAutoplayPlannerObservation.payload.summary.fields[1].value != 0
            && gExpansionAutoplayPlannerObservation.payload.summary.fields[2].value
                == 0x010101,
        "summary page must expose actual map and active-unit semantics");
    CHECK(gExpansionAutoplayPlannerObservation.payload.summary.campaign.chapter
                == (1u << 8)
            && (gExpansionAutoplayPlannerObservation
                    .payload.summary.campaign.counts & 0xFFFFFF) == 0x020101
            && gExpansionAutoplayPlannerObservation
                    .payload.summary.campaign.currentStrategyId
                == EXPANSION_AUTOPLAY_STRATEGY_OBJECTIVE_FIRST_ID
            && ((gExpansionAutoplayPlannerObservation
                    .payload.summary.campaign.currentAssignment >> 8) & 0xFF)
                == EXPANSION_AUTOPLAY_PLANNER_ASSIGNMENT_UNIT,
        "summary must expose chapter objective and current strategy semantics");
    CHECK(gExpansionAutoplayPlannerObservation
                .payload.summary.campaign.objectives[0].id == 0x2001
            && (gExpansionAutoplayPlannerObservation
                    .payload.summary.campaign.objectives[0].status & 0xFF)
                == EXPANSION_CHAPTER_OBJECTIVE_PENDING
            && gExpansionAutoplayPlannerObservation
                    .payload.summary.campaign.groups[0].members[0] == 0x0201
            && gExpansionAutoplayPlannerObservation
                    .payload.summary.campaign.strategies[1].actionCapabilities
                == EXPANSION_AUTOPLAY_STRATEGY_ACTION_ALL
            && ((gExpansionAutoplayPlannerObservation
                    .payload.summary.campaign.assignments[2].identity >> 21) & 1) == 1,
        "summary must expose typed objective, group, strategy, and assignment records");
    CHECK(PageMatches(
            &decision, 4, EXPANSION_AUTOPLAY_PLANNER_PAGE_UNITS, 0, 2)
            && (gExpansionAutoplayPlannerObservation
                    .payload.units[1].identity >> 24)
                == EXPANSION_AUTOPLAY_PLANNER_UNAVAILABLE
            && gExpansionAutoplayPlannerObservation.payload.units[1].state
                == US_NOT_DEPLOYED,
        "unit page must mark benched stale-coordinate units unavailable");
    CHECK((gExpansionAutoplayPlannerObservation.payload.units[0].status & 0xFFFF)
                == (UNIT_STATUS_POISON | (3 << 4) | (12 << 8))
            && (gExpansionAutoplayPlannerObservation.payload.units[0].status >> 16)
                == (34 | (EXPANSION_AUTOPLAY_PLANNER_UNIT_DEPLOYED << 8))
            && gExpansionAutoplayPlannerObservation
                    .payload.units[0].rescueAndEquipped
                == ((ITEM_SWORD_IRON | (30 << 8)) << 16)
            && gExpansionAutoplayPlannerObservation.payload.units[0].stats0
                == 0x08070605
            && gExpansionAutoplayPlannerObservation.payload.units[0].stats1
                == 0x06090A09
            && gExpansionAutoplayPlannerObservation.payload.units[0].ranks0 == 0x1F
            && gExpansionAutoplayPlannerObservation.payload.units[0].ranks1 == 0x20,
        "unit page must expose status, level, stats, equipment, and weapon ranks");
    CHECK(PageMatches(
            &decision, 5, EXPANSION_AUTOPLAY_PLANNER_PAGE_INVENTORY, 0, 10)
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
        "inventory page must expose present, empty, and unavailable unit slots");
    CHECK(PageMatches(
            &decision, 6, EXPANSION_AUTOPLAY_PLANNER_PAGE_RESOURCES, 0, 115)
            && gExpansionAutoplayPlannerObservation
                    .payload.resources[0].value == 1234
            && gExpansionAutoplayPlannerObservation
                    .payload.resources[1].value
                == (ITEM_CHESTKEY | (3 << 8))
            && (gExpansionAutoplayPlannerObservation
                    .payload.resources[2].identity >> 24)
                == EXPANSION_AUTOPLAY_PLANNER_EMPTY,
        "resource page must expose gold plus present and empty convoy slots");
    CHECK(PageMatches(
            &decision, 7,
            EXPANSION_AUTOPLAY_PLANNER_PAGE_RESOURCES, 115, 2),
        "resource paging must include the complete autoplay telemetry");
    CHECK(PageMatches(
            &decision, 8, EXPANSION_AUTOPLAY_PLANNER_PAGE_FLAGS, 0, 115)
            && gExpansionAutoplayPlannerObservation
                    .payload.flags[0].value == 1
            && gExpansionAutoplayPlannerObservation
                    .payload.flags[1].value == 0,
        "flag page must expose explicit set and clear states");
    CHECK(PageMatches(
            &decision, 9, EXPANSION_AUTOPLAY_PLANNER_PAGE_FLAGS, 115, 13),
        "flag paging must retain canonical record boundaries");
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
    CHECK(PageMatches(
            &decision, 32,
            EXPANSION_AUTOPLAY_PLANNER_PAGE_ACTIONS, 506, 6),
        "valid PAGE command must publish another page");
    WriteCommand(
        (enum ExpansionAutoplayPlannerCommandKind)99,
        gExpansionAutoplayPlannerObservation.runId,
        gExpansionAutoplayPlannerObservation.observationId,
        0,
        0,
        NULL);
    CHECK(ExpansionAutoplayPlanner_PollDecision(&decision)
            == EXPANSION_AUTOPLAY_PLANNER_DECISION_WAIT
            && gExpansionAutoplayPlannerObservation.rejection
                == EXPANSION_AUTOPLAY_PLANNER_REJECTION_PROTOCOL_ERROR,
        "unexpected waiting command must reject instead of waiting forever");
    CHECK(RequestPage(&decision, 32)
            == EXPANSION_AUTOPLAY_PLANNER_DECISION_WAIT,
        "page must republish after malformed command");
    action = &gExpansionAutoplayPlannerObservation.payload.actions[5];
    selectedOrdinal =
        gExpansionAutoplayPlannerObservation.start.actionStartOrdinal + 5;
    selectedToken[0] = action->token0;
    selectedToken[1] = action->token1;
    selectedToken[2] = action->token2;
    selectedToken[3] = action->token3;
    selectedX = action->destination & 0xFFFF;
    selectedY = action->destination >> 16;
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
        CHECK(
            CommitCurrent(&decision, selectedOrdinal, forgedToken)
                == EXPANSION_AUTOPLAY_PLANNER_DECISION_WAIT,
            "every forged token word must reject"
        );
        CHECK(
            gExpansionAutoplayPlannerObservation.rejection
                == EXPANSION_AUTOPLAY_PLANNER_REJECTION_TOKEN_MISMATCH,
            "forged token word must have explicit rejection"
        );
    }
    sMovementData[selectedY][selectedX] = MAP_MOVEMENT_MAX + 1;
    CHECK(CommitCurrent(&decision, selectedOrdinal, selectedToken)
                == EXPANSION_AUTOPLAY_PLANNER_DECISION_WAIT
            && gExpansionAutoplayPlannerObservation.rejection
                == EXPANSION_AUTOPLAY_PLANNER_REJECTION_ACTION_BECAME_ILLEGAL,
        "candidate-set mutation must reject before ordinal reconstruction");
    sMovementData[selectedY][selectedX] = 1;
    CHECK(CommitCurrent(&decision, selectedOrdinal, selectedToken)
            == EXPANSION_AUTOPLAY_PLANNER_DECISION_ACCEPTED,
        "matching token must commit the existing production decision");
    CHECK(selectedOrdinal == 511
            && decision.xMove == selectedX
            && decision.yMove == selectedY,
        "last-page selection must map to its stable row-major candidate");
    sConsumption = 4;
    gPlaySt.chapterIndex = 1;
    ExpansionAutoplayPlanner_RecordCampaignCheckpoint();
    previousSeedIdentity =
        gExpansionAutoplayPlannerObservation.actualSeedIdentity;
    previousScenarioIdentity =
        gExpansionAutoplayPlannerObservation.actualScenarioIdentity;
    ExpansionAutoplayPlanner_OnMapReset();
    CHECK(ExpansionAutoplayPlanner_IsActive()
            && gExpansionAutoplayPlannerCampaignCheckpoint.chapterIndex == 1
            && gExpansionAutoplayPlannerObservation.state
                == EXPANSION_AUTOPLAY_PLANNER_STATE_DISABLED,
        "chapter transition must preserve active campaign checkpoint");
    gPlaySt.chapterIndex = 2;
    sSeeds[2]++;
    ExpansionAutoplayPlanner_OnMapReady();
    CHECK(gExpansionAutoplayPlannerObservation.actualSeedIdentity
            != previousSeedIdentity,
        "READY must publish identities after chapter map/RNG initialization");
    CHECK(gExpansionAutoplayPlannerObservation.actualScenarioIdentity
            != previousScenarioIdentity,
        "scenario identity must bind the current chapter/map contract");
    ExpansionAutoplayPlanner_RecordCampaignCheckpoint();
    CHECK(gExpansionAutoplayPlannerCampaignCheckpoint.chapterIndex == 2
            && gExpansionAutoplayPlannerCampaignCheckpoint.rngConsumption == 4
            && gExpansionAutoplayPlannerCampaignCheckpoint.semanticStateDigest != 0,
        "campaign checkpoint must be semantic and RNG-owned");
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
    CHECK(RequestPage(&decision, 0)
            == EXPANSION_AUTOPLAY_PLANNER_DECISION_CANCELLED
            && gExpansionAutoplayPlannerObservation.rejection
                == EXPANSION_AUTOPLAY_PLANNER_REJECTION_TIMEOUT
            && gExpansionAutoplayPlannerCampaignCheckpoint.magic == 0
            && gExpansionAutoplayPlannerCampaignCheckpoint.version == 0
            && gExpansionAutoplayPlannerCampaignCheckpoint.byteSize == 0
            && gExpansionAutoplayPlannerCampaignCheckpoint.semanticStateDigest
                == 0
            && sRestoreRequests == 1,
        "deadline must clear checkpoint before restoring player control");
    ExpansionAutoplayPlanner_Reset();
    CHECK(!ExpansionAutoplayPlanner_IsActive()
            && gExpansionAutoplayPlannerCampaignCheckpoint.magic == 0,
        "destructive full-run reset must clear active/checkpoint state");
    ExpansionAutoplayPlanner_OnMapReady();
    ExpansionAutoplayPlanner_PollStart();
    CHECK(StartPreparedPlanner(), "second run must start after reset");
    CHECK(gExpansionAutoplayPlannerCampaignCheckpoint.magic == 0
            && gExpansionAutoplayPlannerCampaignCheckpoint.chapterIndex == 0
            && gExpansionAutoplayPlannerCampaignCheckpoint.semanticStateDigest
                == 0,
        "new START must not expose a checkpoint from the timed-out run");
    CHECK(ExpansionAutoplayPlanner_OfferDecision(&decision)
            == EXPANSION_AUTOPLAY_PLANNER_DECISION_WAIT,
        "second run must publish before cancellation");
    ExpansionAutoplayPlanner_RecordCampaignCheckpoint();
    CHECK(gExpansionAutoplayPlannerCampaignCheckpoint.magic
            == EXPANSION_AUTOPLAY_PLANNER_MAGIC,
        "explicit-cancel negative must begin with a valid checkpoint");
    WriteCommand(
        EXPANSION_AUTOPLAY_PLANNER_COMMAND_CANCEL,
        gExpansionAutoplayPlannerObservation.runId,
        gExpansionAutoplayPlannerObservation.observationId,
        0,
        0,
        NULL);
    CHECK(ExpansionAutoplayPlanner_PollDecision(&decision)
            == EXPANSION_AUTOPLAY_PLANNER_DECISION_CANCELLED
            && gExpansionAutoplayPlannerCampaignCheckpoint.magic == 0
            && gExpansionAutoplayPlannerCampaignCheckpoint.version == 0
            && gExpansionAutoplayPlannerCampaignCheckpoint.byteSize == 0
            && gExpansionAutoplayPlannerCampaignCheckpoint.semanticStateDigest
                == 0
            && sRestoreRequests == 2,
        "explicit cancellation must clear checkpoint before restoration");
    CHECK(ResetAndStartPlanner(), "wait-candidate run must start");
    CHECK(gExpansionAutoplayPlannerCampaignCheckpoint.magic == 0
            && gExpansionAutoplayPlannerCampaignCheckpoint.runId == 0
            && gExpansionAutoplayPlannerCampaignCheckpoint.chapterIndex == 0,
        "START after explicit cancel must retain no prior checkpoint");
    for (index = 0; index < 17; index++)
    {
        int x;
        for (x = 0; x < 32; x++)
            sMovementData[index][x] = MAP_MOVEMENT_MAX + 1;
    }
    sMovementData[sUnit.yPos][sUnit.xPos] = 0;
    decision.actionPerformed = true;
    decision.unitId = 1;
    decision.xMove = sUnit.xPos;
    decision.yMove = sUnit.yPos;
    decision.actionId = AI_ACTION_NONE;
    decision.targetId = 0;
    CHECK(ExpansionAutoplayPlanner_OfferDecision(&decision)
                == EXPANSION_AUTOPLAY_PLANNER_DECISION_WAIT
            && gExpansionAutoplayPlannerObservation.totalActionCount == 1,
        "immobile active unit must publish exactly one stationary Wait");
    CHECK(RequestPage(
            &decision,
            gExpansionAutoplayPlannerObservation.pageCount - 1)
            == EXPANSION_AUTOPLAY_PLANNER_DECISION_WAIT
            && gExpansionAutoplayPlannerObservation.pageKind
                == EXPANSION_AUTOPLAY_PLANNER_PAGE_ACTIONS,
        "wait candidate must be read through a typed action page");
    action = &gExpansionAutoplayPlannerObservation.payload.actions[0];
    CHECK(action->itemSlot == 0xFFFF
            && (action->destination & 0xFFFF) == sUnit.xPos
            && (action->destination >> 16) == sUnit.yPos,
        "stationary Wait must bind the active unit current tile");
    sMovementData[sUnit.yPos][sUnit.xPos] = MAP_MOVEMENT_MAX + 1;
    CHECK(CommitCurrent(
            &decision,
            gExpansionAutoplayPlannerObservation.start.actionStartOrdinal,
            &action->token0)
                == EXPANSION_AUTOPLAY_PLANNER_DECISION_WAIT
            && gExpansionAutoplayPlannerObservation.rejection
                == EXPANSION_AUTOPLAY_PLANNER_REJECTION_ACTION_BECAME_ILLEGAL,
        "stale stationary Wait must reject before execution");
    sMovementData[sUnit.yPos][sUnit.xPos] = 0;
    CHECK(CommitCurrent(
            &decision,
            gExpansionAutoplayPlannerObservation.start.actionStartOrdinal,
            &action->token0)
                == EXPANSION_AUTOPLAY_PLANNER_DECISION_ACCEPTED
            && decision.xMove == sUnit.xPos
            && decision.yMove == sUnit.yPos
            && decision.actionId == AI_ACTION_NONE,
        "stationary Wait must commit through its bound current tile");
    ExpansionAutoplayPlanner_RecordCampaignCheckpoint();
    CHECK(gExpansionAutoplayPlannerCampaignCheckpoint.semanticStateDigest != 0,
        "accepted wait token must enter the semantic action trace digest");
    CHECK(TestInventorySlotWireIdentity() == 0,
          "inventory-slot fixed-width wire identity test");
    CHECK(TestCandidateInventoryBinding() == 0,
          "candidate inventory-state identity test");
    ResetActionFixture(32, 17);
    CHECK(ResetAndStartPlanner(), "unavailable-actor run must start");
    sUnit.state = US_NOT_DEPLOYED;
    restoreBefore = sRestoreRequests;
    original = decision;
    CHECK(ExpansionAutoplayPlanner_OfferDecision(&decision)
                == EXPANSION_AUTOPLAY_PLANNER_DECISION_EXHAUSTED
            && gExpansionAutoplayPlannerObservation.state
                == EXPANSION_AUTOPLAY_PLANNER_STATE_EXHAUSTED
            && gExpansionAutoplayPlannerObservation.rejection
                == EXPANSION_AUTOPLAY_PLANNER_REJECTION_CAPABILITY_UNAVAILABLE
            && !ExpansionAutoplayPlanner_IsActive()
            && gExpansionAutoplayPlannerCampaignCheckpoint.magic == 0
            && sRestoreRequests == restoreBefore + 1
            && memcmp(&decision, &original, sizeof(decision)) == 0,
        "unavailable actor must fail before publishing WAITING page zero");
    CHECK(ExpansionAutoplayPlanner_OfferDecision(&decision)
            == EXPANSION_AUTOPLAY_PLANNER_DECISION_FALLBACK,
        "exhausted planner must not re-enter its stale terminal state");
    for (index = 0; index < 17; index++)
    {
        int x;
        for (x = 0; x < 32; x++)
            sMovementData[index][x] = 1;
    }
    sUnitData[sUnit.yPos][sUnit.xPos] = 1;
    sUnit.state = 0;
    CHECK(ResetAndStartPlanner(),
          "capacity terminal run must start");
    restoreBefore = sRestoreRequests;
    CHECK(ExpansionAutoplayPlanner_OfferDecision(&decision)
                == EXPANSION_AUTOPLAY_PLANNER_DECISION_EXHAUSTED
            && gExpansionAutoplayPlannerObservation.state
                == EXPANSION_AUTOPLAY_PLANNER_STATE_EXHAUSTED
            && gExpansionAutoplayPlannerObservation.rejection
                == EXPANSION_AUTOPLAY_PLANNER_REJECTION_RESOURCE_LIMIT
            && !ExpansionAutoplayPlanner_IsActive()
            && gExpansionAutoplayPlannerCampaignCheckpoint.magic == 0
            && sRestoreRequests == restoreBefore + 1,
        "capacity overflow must terminate and queue safe restoration");
    printf("SCENARIO_IDENTITY=%08x\n", configuredScenarioIdentity);
    printf("CONFIG_IDENTITY=%08x\n", configuredConfigIdentity);
    puts("AUTOPLAY_PLANNER_HOST_TEST: PASS");
    return 0;
}
