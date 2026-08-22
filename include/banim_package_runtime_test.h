#ifndef GUARD_BANIM_PACKAGE_RUNTIME_TEST_H
#define GUARD_BANIM_PACKAGE_RUNTIME_TEST_H

#include "global.h"

#ifndef FE8_BANIM_PACKAGE_RUNTIME_TEST
#define FE8_BANIM_PACKAGE_RUNTIME_TEST 0
#endif

#if FE8_BANIM_PACKAGE_RUNTIME_TEST

#define BANIM_PACKAGE_RUNTIME_TEST_MAGIC 0x42505431

struct BanimPackageRuntimeTestProbe {
    /* 00 */ u32 magic;
    /* 04 */ u32 selectionCount;
    /* 08 */ u32 originalIndex;
    /* 0C */ u32 defaultClassId;
    /* 10 */ u32 aliasIndex;
    /* 14 */ u32 modeCount;
    /* 18 */ u32 normalDuration;
    /* 1C */ u32 totalDuration;
    /* 20 */ u32 resourcesReady;
    /* 24 */ u32 battleEntryCount;
    /* 28 */ u32 battleCompleteCount;
    /* 2C */ u32 selectedBattleIndex;
    /* 30 */ u32 runtimeDataConsumeCount;
};

extern EWRAM_DATA struct BanimPackageRuntimeTestProbe gBanimPackageRuntimeTestProbe;

bool BanimPackageRuntimeTest_ForceFirstScriptedBattle(void);
void BanimPackageRuntimeTest_BeginScriptedBattle(void);
void BanimPackageRuntimeTest_MarkBattleEntry(void);
void BanimPackageRuntimeTest_MarkRuntimeDataConsumed(
    const u32 * script,
    const u32 * oam,
    const u16 * palette
);
void BanimPackageRuntimeTest_MarkBattleComplete(void);

#endif /* FE8_BANIM_PACKAGE_RUNTIME_TEST */

#endif /* GUARD_BANIM_PACKAGE_RUNTIME_TEST_H */
