/*
 * Issue #125: host-executes the authoritative engine helpers used by the
 * cursor-selected unit editor. The Python harness links the real bmunit.c and
 * eventscr3.c objects with section garbage collection, retaining these exact
 * functions rather than copies in a test fixture.
 */
#include <stdio.h>
#include <string.h>

#include "global.h"
#include "bmunit.h"
#include "bmitem.h"
#include "cp_common.h"

#define CHECK(condition, message) \
    do \
    { \
        if (!(condition)) \
        { \
            fprintf(stderr, "DEBUGTOOLS_UNIT_REAL_HELPERS: FAIL: %s\n", message); \
            return 1; \
        } \
    } while (0)

u16 GetUnitEquippedWeapon(struct Unit* unit)
{
    (void)unit;
    return 0;
}

int GetItemHpBonus(int item)
{
    (void)item;
    return 0;
}

int main(void)
{
    struct CharacterData character;
    struct ClassData classData;
    struct Unit unit;

    memset(&character, 0, sizeof(character));
    memset(&classData, 0, sizeof(classData));
    memset(&unit, 0, sizeof(unit));

    character.number = 1;
    character.baseCon = 2;
    classData.number = 1;
    classData.baseCon = 5;
    classData.baseMov = 6;
    classData.maxHP = 60;
    classData.maxPow = 25;
    classData.maxSkl = 24;
    classData.maxSpd = 23;
    classData.maxDef = 22;
    classData.maxRes = 21;
    classData.maxCon = 20;

    unit.pCharacterData = &character;
    unit.pClassData = &classData;
    unit.index = 1;
    unit.maxHP = 20;
    unit.curHP = 5;

    SetUnitHp(&unit, 99);
    CHECK(unit.curHP == 20, "SetUnitHp must clamp to GetUnitMaxHp");
    SetUnitHp(&unit, 1);
    CHECK(unit.curHP == 1, "SetUnitHp must preserve an in-range current HP");

    unit.maxHP = 99;
    unit.pow = 99;
    unit.skl = 99;
    unit.spd = 99;
    unit.def = 99;
    unit.res = 99;
    unit.lck = 99;
    unit.conBonus = 99;
    unit.movBonus = 99;
    UnitCheckStatCaps(&unit);
    CHECK(unit.maxHP == 60, "UnitCheckStatCaps must apply the blue-unit HP cap");
    CHECK(unit.pow == 25 && unit.skl == 24 && unit.spd == 23,
          "UnitCheckStatCaps must apply class power/skill/speed caps");
    CHECK(unit.def == 22 && unit.res == 21 && unit.lck == 30,
          "UnitCheckStatCaps must apply defense/resistance/luck caps");
    CHECK(unit.conBonus == 13 && unit.movBonus == 9,
          "UnitCheckStatCaps must apply constitution/movement caps");

    SetUnitStatusExt(&unit, UNIT_STATUS_SLEEP, 3);
    CHECK(unit.statusIndex == UNIT_STATUS_SLEEP && unit.statusDuration == 3,
          "SetUnitStatusExt must set the typed temporary status fixture");
    SetUnitStatus(&unit, UNIT_STATUS_NONE);
    CHECK(unit.statusIndex == UNIT_STATUS_NONE && unit.statusDuration == 0,
          "SetUnitStatus must clear both status and duration");

    unit.ai1 = AI_A_00;
    unit.ai2 = AI_B_00;
    unit.ai_a_pc = 7;
    unit.ai_b_pc = 9;
    ChangeUnitAi(&unit, AI_A_14, AI_B_0C, 0);
    CHECK(unit.ai1 == AI_A_14 && unit.ai_a_pc == 0,
          "ChangeUnitAi must set AI A and reset its script cursor");
    CHECK(unit.ai2 == AI_B_0C && unit.ai_b_pc == 0,
          "ChangeUnitAi must set AI B and reset its script cursor");
    CHECK(unit.aiFlags & AI_UNIT_FLAG_3,
          "ChangeUnitAi must preserve the AI_B_0C escape side effect");

    unit.ai_a_pc = 3;
    unit.ai_b_pc = 4;
    ChangeUnitAi(&unit, AI_A_INVALID, AI_B_INVALID, 0);
    CHECK(unit.ai1 == AI_A_14 && unit.ai_a_pc == 3,
          "AI_A_INVALID must leave AI A and its cursor unchanged");
    CHECK(unit.ai2 == AI_B_0C && unit.ai_b_pc == 4,
          "AI_B_INVALID must leave AI B and its cursor unchanged");

    unit.state = US_DEAD;
    ChangeUnitAi(&unit, AI_A_00, AI_B_00, 0);
    CHECK(unit.ai1 == AI_A_14 && unit.ai2 == AI_B_0C,
          "ChangeUnitAi must reject dead units");

    puts("DEBUGTOOLS_UNIT_REAL_HELPERS: PASS");
    return 0;
}
