#ifndef GUARD_EXPANSION_AOE_REFERENCE_H
#define GUARD_EXPANSION_AOE_REFERENCE_H

#include "expansion_aoe.h"

#define EXPANSION_AOE_REFERENCE_HEAL_AMOUNT 3
#define EXPANSION_AOE_REFERENCE_RADIUS 2
#define EXPANSION_AOE_REFERENCE_PROBE_MAGIC 0x414F4531 /* ASCII "AOE1" */

/*
 * The reference is a synchronous, test-entry-only radius heal. It targets
 * the source and damaged allies in a radius-2 diamond, applies in the public
 * target order, continues after a per-target failure, awards no EXP, starts
 * no animation, invokes no event, is never selected by AI, and persists no
 * in-progress state.
 */
struct ExpansionAoEReferenceProbe
{
    /* 00 */ u32 magic;
    /* 04 */ u32 enabled;
    /* 08 */ u32 runCount;
    /* 0C */ u32 buildResult;
    /* 10 */ u32 executionOutcome;
    /* 14 */ u32 sourceUnitId;
    /* 18 */ u32 targetCount;
    /* 1C */ u32 totalTargetCount;
    /* 20 */ u32 firstTargetUnitId;
    /* 24 */ u32 secondTargetUnitId;
    /* 28 */ u32 firstHpBefore;
    /* 2C */ u32 firstHpAfter;
    /* 30 */ u32 secondHpBefore;
    /* 34 */ u32 secondHpAfter;
    /* 38 */ u32 appliedCount;
    /* 3C */ u32 skippedCount;
    /* 40 */ u32 failedCount;
    /* 44 */ u32 expAwarded;
    /* 48 */ u32 rangeTileCount;
    /* 4C */ u32 legacyTargetCount;
    /* 50 */ u32 aiPolicy;
    /* 54 */ u32 animationPolicy;
    /* 58 */ u32 eventPolicy;
    /* 5C */ u32 savePolicy;
    /* 60 */ u32 restoredOriginalHp;
};

#if FE8_EXPANSION_MODERN_BUILD && FE8_EXPANSION_AOE_REFERENCE
extern struct ExpansionAoEReferenceProbe gExpansionAoEReferenceProbe;
#endif

int ExpansionAoEReference_IsEnabled(void);
enum ExpansionAoEResult ExpansionAoEReference_Apply(
    ExpansionAoEUnitId sourceUnitId,
    struct ExpansionAoETargetSet* targets,
    struct ExpansionAoEExecutionResult* result);
void ExpansionAoEReference_RunProbe(void);

#endif /* GUARD_EXPANSION_AOE_REFERENCE_H */
