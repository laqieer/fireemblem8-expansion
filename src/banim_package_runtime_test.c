#include "global.h"

#include "constants/classes.h"

#include "banim_package_runtime_test.h"

#if FE8_BANIM_PACKAGE_RUNTIME_TEST

#include "banim_data.h"
#include "ekrbattle.h"

#include "build/generated/assets/banim/banim_runtime_test_defs.h"

EWRAM_DATA struct BanimPackageRuntimeTestProbe gBanimPackageRuntimeTestProbe = {0};

bool BanimPackageRuntimeTest_ForceFirstScriptedBattle(void)
{
    return gBanimPackageRuntimeTestProbe.selectionCount == 0;
}

void BanimPackageRuntimeTest_BeginScriptedBattle(void)
{
    const struct BattleAnim * animation;
    int originalIndex;

    if (CheckBattleScripted() == false)
        return;

    animation = &banim_data[BANIM_PACKAGE_LORM_SP1_PROOF_INDEX];
    originalIndex = AnimConf_0[0].index - 1;

    if (gBanimPackageRuntimeTestProbe.selectionCount == 0)
    {
        gBanimPackageRuntimeTestProbe.selectionCount++;
        gBanimPackageRuntimeTestProbe.originalIndex = (u32)originalIndex;
        gBanimPackageRuntimeTestProbe.defaultClassId = CLASS_EPHRAIM_LORD;
        gBanimPackageRuntimeTestProbe.aliasIndex = BANIM_PACKAGE_LORM_SP1_PROOF_INDEX;
        gBanimPackageRuntimeTestProbe.modeCount = BANIM_PACKAGE_LORM_SP1_PROOF_MODE_COUNT;
        gBanimPackageRuntimeTestProbe.normalDuration = BANIM_PACKAGE_LORM_SP1_PROOF_NORMAL_DURATION;
        gBanimPackageRuntimeTestProbe.totalDuration = BANIM_PACKAGE_LORM_SP1_PROOF_TOTAL_DURATION;
        gBanimPackageRuntimeTestProbe.resourcesReady =
            (animation->modes != NULL ? 1 : 0) |
            (animation->script != NULL ? 2 : 0) |
            (animation->oam_r != NULL ? 4 : 0) |
            (animation->oam_l != NULL ? 8 : 0) |
            (animation->pal != NULL ? 16 : 0);
        gBanimIdx[EKR_POS_L] = BANIM_PACKAGE_LORM_SP1_PROOF_INDEX;
        gBanimIdx_bak[EKR_POS_L] = BANIM_PACKAGE_LORM_SP1_PROOF_INDEX;
        gBanimPackageRuntimeTestProbe.selectedBattleIndex = gBanimIdx[EKR_POS_L];
    }
}

void BanimPackageRuntimeTest_MarkBattleEntry(void)
{
    if (
        gBanimPackageRuntimeTestProbe.selectionCount == 1
        && gBanimPackageRuntimeTestProbe.battleEntryCount == 0
        && gBanimIdx[EKR_POS_L] == BANIM_PACKAGE_LORM_SP1_PROOF_INDEX
        && gBanimPackageRuntimeTestProbe.selectedBattleIndex
            == BANIM_PACKAGE_LORM_SP1_PROOF_INDEX
    )
    {
        gBanimPackageRuntimeTestProbe.battleEntryCount++;
    }
}

void BanimPackageRuntimeTest_MarkRuntimeDataConsumed(
    const u32 * script,
    const u32 * oam,
    const u16 * palette
)
{
    int index;

    if (
        gBanimPackageRuntimeTestProbe.selectionCount != 1
        || gBanimPackageRuntimeTestProbe.runtimeDataConsumeCount != 0
        || gBanimPackageRuntimeTestProbe.selectedBattleIndex
            != BANIM_PACKAGE_LORM_SP1_PROOF_INDEX
    )
    {
        return;
    }

    for (index = 0; index < BANIM_PACKAGE_LORM_SP1_PROOF_SCRIPT_WORD_COUNT; index++)
    {
        if (script[index] == BANIM_PACKAGE_LORM_SP1_PROOF_SOUND_OPCODE)
        {
            if (
                ((const u16 *)oam)[1] == 0x8000
                && palette[1] == BANIM_PACKAGE_LORM_SP1_PROOF_PALETTE_COLOR_1
            )
            {
                gBanimPackageRuntimeTestProbe.runtimeDataConsumeCount++;
            }
            return;
        }
    }
}

void BanimPackageRuntimeTest_MarkBattleComplete(void)
{
    if (
        gBanimPackageRuntimeTestProbe.selectionCount == 1
        && gBanimPackageRuntimeTestProbe.battleEntryCount == 1
        && gBanimPackageRuntimeTestProbe.battleCompleteCount == 0
        && gBanimPackageRuntimeTestProbe.selectedBattleIndex
            == BANIM_PACKAGE_LORM_SP1_PROOF_INDEX
    )
    {
        gBanimPackageRuntimeTestProbe.battleCompleteCount++;
        gBanimPackageRuntimeTestProbe.magic = BANIM_PACKAGE_RUNTIME_TEST_MAGIC;
    }
}

#endif /* FE8_BANIM_PACKAGE_RUNTIME_TEST */
