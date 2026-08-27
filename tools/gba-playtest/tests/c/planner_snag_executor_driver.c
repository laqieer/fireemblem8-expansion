#include "global.h"

#include <stdio.h>
#include <string.h>

#include "bm.h"
#include "bmbattle.h"
#include "bmmind.h"
#include "bmmap.h"
#include "bmtrick.h"
#include "bmunit.h"
#include "chapterdata.h"
#include "cp_common.h"
#include "cp_perform.h"
#include "constants/characters.h"
#include "constants/classes.h"
#include "constants/items.h"
#include "constants/terrains.h"

struct CpPerformProc;
void AiStartCombatAction(struct CpPerformProc* proc);

#define CHECK(condition, message) \
    do \
    { \
        if (!(condition)) \
        { \
            fprintf(stderr, "PLANNER_SNAG_EXECUTOR_TEST: FAIL: %s\n", message); \
            return 1; \
        } \
    } while (0)

struct PlaySt gPlaySt;
struct ActionData gActionData;
struct AiDecision gAiDecision;
struct Unit* gActiveUnit;
u8 gActiveUnitId;
struct Vec2 gBmMapSize;
u8** gBmMapTerrain;

static struct CharacterData sCharacter;
static struct CharacterData sSnagCharacter;
static struct ClassData sClass;
static struct ClassData sObstacleClass;
static struct ROMChapterData sChapter;
static struct Unit sUnit;
static struct Trap sTrap;
static u8 sTerrainData[8][8];
static u8* sTerrainRows[8];
static int sApplyCount;
static int sMapChangeCount;

bool ExpansionAutoplayPlanner_IsActive(void)
{
    return true;
}

bool ExpansionAutoplayPlanner_PrepareActionData(
    const struct AiDecision* decision)
{
    return decision->targetId == 0
        && decision->xTarget == sTrap.xPos
        && decision->yTarget == sTrap.yPos
        && sTrap.type == TRAP_OBSTACLE
        && gBmMapTerrain[sTrap.yPos][sTrap.xPos] == TERRAIN_SNAG;
}

struct Trap* GetTrapAt(int x, int y)
{
    if (sTrap.type != TRAP_NONE
        && sTrap.xPos == x
        && sTrap.yPos == y)
        return &sTrap;
    return NULL;
}

void EquipUnitItemSlot(struct Unit* unit, int slot)
{
    u16 item = unit->items[slot];

    unit->items[slot] = unit->items[0];
    unit->items[0] = item;
}

u32 ApplyUnitAction(ProcPtr proc)
{
    (void)proc;
    sApplyCount++;
    return 0;
}

void ClearUnit(struct Unit* unit)
{
    memset(unit, 0, sizeof(*unit));
}

const struct ClassData* GetClassData(int id)
{
    return id == CLASS_OBSTACLE ? &sObstacleClass : &sClass;
}

const struct CharacterData* GetCharacterData(int id)
{
    return id == CHARACTER_SNAG ? &sSnagCharacter : &sCharacter;
}

const struct ROMChapterData* GetROMChapterStruct(unsigned chapter)
{
    (void)chapter;
    return &sChapter;
}

int GetMapChangeIdAt(int x, int y)
{
    (void)x;
    (void)y;
    return 7;
}

void PlaySoundEffect(int song)
{
    (void)song;
}

void m4aSongNumStart(u16 song)
{
    (void)song;
}

void NewBMXFADE(s8 lock)
{
    (void)lock;
}

void RenderBmMapOnBg2(void)
{
}

void ApplyMapChangesById(int id)
{
    if (id == 7)
        sMapChangeCount++;
}

void EnableMapChange(int id)
{
    (void)id;
}

void RefreshTerrainBmMap(void)
{
}

void UpdateRoofedUnits(void)
{
}

void RenderBmMap(void)
{
}

void RefreshEntityBmMaps(void)
{
}

void UpdateUnitMapAndVision(void)
{
}

int main(void)
{
    int y;

    gBmMapSize.x = 8;
    gBmMapSize.y = 8;
    for (y = 0; y < 8; y++)
        sTerrainRows[y] = sTerrainData[y];
    gBmMapTerrain = sTerrainRows;
    sCharacter.number = 1;
    sSnagCharacter.number = CHARACTER_SNAG;
    sClass.number = 1;
    sObstacleClass.number = CLASS_OBSTACLE;
    sChapter.mapCrackedWallHeath = 30;
    sUnit.pCharacterData = &sCharacter;
    sUnit.pClassData = &sClass;
    sUnit.index = 1;
    sUnit.xPos = 2;
    sUnit.yPos = 2;
    sUnit.items[0] = ITEM_SWORD_IRON | (30 << 8);
    gActiveUnit = &sUnit;
    gActiveUnitId = 1;
    sTrap.type = TRAP_OBSTACLE;
    sTrap.xPos = 3;
    sTrap.yPos = 2;
    sTrap.extra = 20;
    sTerrainData[2][3] = TERRAIN_SNAG;
    gAiDecision.actionPerformed = true;
    gAiDecision.unitId = 1;
    gAiDecision.xMove = 2;
    gAiDecision.yMove = 2;
    gAiDecision.actionId = AI_ACTION_COMBAT;
    gAiDecision.targetId = 0;
    gAiDecision.itemSlot = 0;
    gAiDecision.xTarget = 3;
    gAiDecision.yTarget = 2;

    AiStartCombatAction(NULL);
    CHECK(sApplyCount == 1
              && gActionData.unitActionType == UNIT_ACTION_COMBAT
              && gActionData.targetIndex == 0
              && gActionData.xOther == 3
              && gActionData.yOther == 2
              && gActionData.trapType == 20,
          "combat executor must lower the selected snag coordinates");

    InitObstacleBattleUnit();
    CHECK(gBattleTarget.unit.pCharacterData == &sSnagCharacter
              && gBattleTarget.unit.curHP == 20
              && gBattleTarget.unit.maxHP == 20
              && gBattleTarget.unit.xPos == 3
              && gBattleTarget.unit.yPos == 2,
          "battle setup must construct the selected snag target");
    gBattleTarget.unit.curHP = 5;
    UpdateObstacleFromBattle(&gBattleTarget);
    CHECK(sTrap.extra == 5 && sTrap.type == TRAP_OBSTACLE,
          "nonlethal combat damage must update snag HP");
    gBattleTarget.unit.curHP = 0;
    UpdateObstacleFromBattle(&gBattleTarget);
    CHECK(sTrap.extra == 0
              && sTrap.type == TRAP_NONE
              && sMapChangeCount == 1,
          "lethal combat damage must destroy the selected snag");

    sTrap.type = TRAP_NONE;
    gAiDecision.actionPerformed = true;
    AiStartCombatAction(NULL);
    CHECK(sApplyCount == 1 && !gAiDecision.actionPerformed,
          "stale destroyed snag must not reach the action executor");

    puts("PLANNER_SNAG_EXECUTOR_TEST: PASS");
    return 0;
}
