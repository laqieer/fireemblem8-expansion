#ifndef GUARD_EXPANSION_CASUAL_MODE_H
#define GUARD_EXPANSION_CASUAL_MODE_H

#include "bmunit.h"

/*
 * The policy is intentionally narrow: only callers that have already
 * classified a player defeat as ordinary combat or arena defeat should use
 * this seam. Scripted deaths, hazards, and explicit removals continue to call
 * UnitKill directly and are never marked for restoration.
 */
enum ExpansionCasualDefeatKind {
    EXPANSION_CASUAL_DEFEAT_COMBAT = 0,
    EXPANSION_CASUAL_DEFEAT_ARENA = 1,
};

void ExpansionCasualMode_MarkDefeat(struct Unit *unit, enum ExpansionCasualDefeatKind kind);
void ExpansionCasualMode_RestoreAtChapterBoundary(struct Unit *unit);

#endif
