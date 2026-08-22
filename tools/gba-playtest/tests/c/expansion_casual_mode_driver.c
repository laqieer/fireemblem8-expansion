#include <stdio.h>
#include <string.h>

#include "global.h"
#include "expansion_casual_mode.h"

#define CHECK(condition, message) \
    do { \
        if (!(condition)) { \
            fprintf(stderr, "CASUAL_MODE_HOST_TEST: FAIL: %s\n", message); \
            return 1; \
        } \
    } while (0)

static struct CharacterData sCharacter;

static void InitBlueUnit(struct Unit *unit)
{
    memset(unit, 0, sizeof(*unit));
    unit->pCharacterData = &sCharacter;
    unit->index = 1;
}

int main(void)
{
    struct Unit unit;

    InitBlueUnit(&unit);

#if FE8_EXPANSION_CASUAL_MODE
    unit.state = US_DEAD | US_HIDDEN;
    ExpansionCasualMode_MarkDefeat(&unit, EXPANSION_CASUAL_DEFEAT_COMBAT);
    CHECK((unit.state & US_BIT24) != 0, "combat defeat must set the restore marker");
    ExpansionCasualMode_RestoreAtChapterBoundary(&unit);
    CHECK((unit.state & (US_BIT24 | US_DEAD)) == 0,
          "chapter boundary must clear only the marker and ordinary death");
    CHECK((unit.state & US_HIDDEN) != 0, "restore must preserve unrelated state");

    unit.state = US_DEAD;
    ExpansionCasualMode_MarkDefeat(&unit, EXPANSION_CASUAL_DEFEAT_ARENA);
    CHECK((unit.state & US_BIT24) != 0, "arena defeat must set the restore marker");
    ExpansionCasualMode_RestoreAtChapterBoundary(&unit);
    CHECK((unit.state & (US_BIT24 | US_DEAD)) == 0,
          "arena restore must clear the marker and ordinary death");

    unit.state = US_DEAD;
    ExpansionCasualMode_MarkDefeat(&unit, (enum ExpansionCasualDefeatKind)2);
    CHECK((unit.state & US_BIT24) == 0, "unknown defeat kinds must not be marked");

    unit.index = FACTION_RED | 1;
    ExpansionCasualMode_MarkDefeat(&unit, EXPANSION_CASUAL_DEFEAT_COMBAT);
    CHECK((unit.state & US_BIT24) == 0, "non-player units must not be marked");

    InitBlueUnit(&unit);
    unit.state = US_DEAD | US_BIT16;
    ExpansionCasualMode_MarkDefeat(&unit, EXPANSION_CASUAL_DEFEAT_COMBAT);
    CHECK((unit.state & US_BIT24) == 0, "permanently unavailable units must not be marked");
#else
    unit.state = US_DEAD | US_BIT24;
    ExpansionCasualMode_MarkDefeat(&unit, EXPANSION_CASUAL_DEFEAT_COMBAT);
    ExpansionCasualMode_RestoreAtChapterBoundary(&unit);
    CHECK(unit.state == (US_DEAD | US_BIT24),
          "disabled policy must preserve ordinary permadeath and existing markers");
#endif

    ExpansionCasualMode_MarkDefeat(NULL, EXPANSION_CASUAL_DEFEAT_COMBAT);
    ExpansionCasualMode_RestoreAtChapterBoundary(NULL);

    printf("CASUAL_MODE_HOST_TEST: PASS\n");
    return 0;
}
