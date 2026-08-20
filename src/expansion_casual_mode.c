#include "global.h"

#include "expansion_casual_mode.h"

void ExpansionCasualMode_MarkDefeat(struct Unit *unit, enum ExpansionCasualDefeatKind kind)
{
    (void)kind;

#if FE8_EXPANSION_CASUAL_MODE
    if (unit == NULL || unit->pCharacterData == NULL)
        return;

    if (kind != EXPANSION_CASUAL_DEFEAT_COMBAT
        && kind != EXPANSION_CASUAL_DEFEAT_ARENA)
        return;

    if (UNIT_FACTION(unit) != FACTION_BLUE)
        return;

    if (unit->state & US_BIT16)
        return;

    unit->state |= US_BIT24;
#else
    (void)unit;
#endif
}

void ExpansionCasualMode_RestoreAtChapterBoundary(struct Unit *unit)
{
#if FE8_EXPANSION_CASUAL_MODE
    if (unit == NULL || unit->pCharacterData == NULL)
        return;

    if (!(unit->state & US_BIT24))
        return;

    unit->state &= ~(US_BIT24 | US_DEAD);
#else
    (void)unit;
#endif
}
