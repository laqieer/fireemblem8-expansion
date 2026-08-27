#include "global.h"

#include <stdio.h>
#include <string.h>

#include "bmbattle.h"
#include "bmmind.h"
#include "bmunit.h"
#include "constants/characters.h"
#include "constants/classes.h"

#define CHECK(condition, message) \
    do \
    { \
        if (!(condition)) \
        { \
            fprintf(stderr, "SUMMON_EFFECT_HOST_TEST: FAIL: %s\n", message); \
            return 1; \
        } \
    } while (0)

void GenerateSummonUnitDef(void);

struct PlaySt gPlaySt;
struct ActionData gActionData;
struct Unit* gActiveUnit;
struct BattleUnit gBattleActor;
struct UnitDefinition gUnitDef1;
u8 gSummonConfig[4][2] = {
    { CHARACTER_EWAN, CHARACTER_SUMMON_EWAN },
    { 0, 0 },
    { 0, 0 },
    { 0, 0 },
};

static struct CharacterData sSummonerCharacter;
static struct CharacterData sSummonCharacter;
static struct ClassData sSummonerClass;
static struct ClassData sSummonClass;
static struct Unit sSummoner;
static struct Unit sSummon;
static bool sSummonLoaded;

unsigned AdvanceGetLCGRNValue(void)
{
    return 0;
}

int DivRem(int value, int divisor)
{
    return value % divisor;
}

struct Unit* GetUnit(int id)
{
    if (id == 1)
        return &sSummoner;
    if (id == 2 && sSummonLoaded)
        return &sSummon;
    return NULL;
}

struct Unit* GetUnitFromCharId(int character)
{
    if (sSummonLoaded
        && sSummon.pCharacterData->number == character)
        return &sSummon;
    return NULL;
}

void ClearUnit(struct Unit* unit)
{
    memset(unit, 0, sizeof(*unit));
    sSummonLoaded = false;
}

int LoadUnits(const struct UnitDefinition* definition)
{
    memset(&sSummon, 0, sizeof(sSummon));
    sSummonCharacter.number = definition->charIndex;
    sSummonClass.number = definition->classIndex;
    sSummon.pCharacterData = &sSummonCharacter;
    sSummon.pClassData = &sSummonClass;
    sSummon.index = 2;
    sSummon.xPos = definition->xPosition;
    sSummon.yPos = definition->yPosition;
    sSummonLoaded = true;
    return 1;
}

int main(void)
{
    sSummonerCharacter.number = CHARACTER_EWAN;
    sSummonerClass.number = 1;
    sSummoner.pCharacterData = &sSummonerCharacter;
    sSummoner.pClassData = &sSummonerClass;
    sSummoner.index = 1;
    sSummoner.level = 10;
    gActiveUnit = &sSummoner;

    gActionData.xOther = 2;
    gActionData.yOther = 1;
    GenerateSummonUnitDef();
    CHECK(sSummonLoaded
              && sSummon.xPos == 2
              && sSummon.yPos == 1
              && gUnitDef1.xPosition == 2
              && gUnitDef1.yPosition == 1,
          "normal Summon effect must create the unit at the first tile");

    gActionData.xOther = 5;
    gActionData.yOther = 4;
    GenerateSummonUnitDef();
    CHECK(sSummonLoaded
              && sSummon.xPos == 5
              && sSummon.yPos == 4
              && gUnitDef1.xPosition == 5
              && gUnitDef1.yPosition == 4,
          "normal Summon effect must replace the summon at the second tile");

    puts("SUMMON_EFFECT_HOST_TEST: PASS");
    return 0;
}
