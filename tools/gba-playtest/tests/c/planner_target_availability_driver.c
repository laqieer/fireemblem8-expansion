#include "global.h"

#include <stdio.h>

#include "bmphase.h"
#include "bmtarget.h"
#include "bmunit.h"

#define CHECK(condition, message) \
    do \
    { \
        if (!(condition)) \
        { \
            fprintf(stderr, "PLANNER_TARGET_AVAILABILITY_TEST: FAIL: %s\n", message); \
            return 1; \
        } \
    } while (0)

static struct CharacterData sActiveCharacter;
static struct CharacterData sTargetCharacter;
static struct ClassData sActiveClass;
static struct ClassData sTargetClass;
static struct Unit sActive;
static struct Unit sTarget;
static int sTargetCount;

int GetCurrentPhase(void)
{
    return FACTION_BLUE;
}

struct Unit* GetUnit(int id)
{
    if (id == 1)
        return &sActive;
    if (id == 2)
        return &sTarget;
    return NULL;
}

int GetUnitCurrentHp(struct Unit* unit)
{
    return unit->curHP;
}

int GetUnitMaxHp(struct Unit* unit)
{
    return unit->maxHP;
}

void InitTargets(int xRoot, int yRoot)
{
    (void)xRoot;
    (void)yRoot;
    sTargetCount = 0;
}

void AddTarget(int x, int y, int unitId, int targetId)
{
    (void)x;
    (void)y;
    (void)unitId;
    (void)targetId;
    sTargetCount++;
}

int main(void)
{
    sActive.pCharacterData = &sActiveCharacter;
    sActive.pClassData = &sActiveClass;
    sActive.index = 1;
    sActive.xPos = 2;
    sActive.yPos = 2;
    sTarget.pCharacterData = &sTargetCharacter;
    sTarget.pClassData = &sTargetClass;
    sTarget.index = 2;
    sTarget.xPos = 3;
    sTarget.yPos = 2;
    sTarget.maxHP = 20;
    sTarget.curHP = 10;

    sTarget.state = US_NOT_DEPLOYED;
    MakeTargetListForLatona(&sActive);
    CHECK(sTargetCount == 0,
          "real target builder must exclude a not-deployed unit");

    sTarget.state = 0;
    MakeTargetListForLatona(&sActive);
    CHECK(sTargetCount == 1,
          "real target builder must retain the available target");

    puts("PLANNER_TARGET_AVAILABILITY_TEST: PASS");
    return 0;
}
