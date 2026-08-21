#include "global.h"

#include "banim_package_runtime_test.h"

#if FE8_BANIM_PACKAGE_RUNTIME_TEST

#include "banim_data.h"
#include "ekrbattle.h"

#include "../build/generated/assets/banim/banim_runtime_test_defs.h"

EWRAM_DATA struct BanimPackageRuntimeTestProbe gBanimPackageRuntimeTestProbe = {0};

void BanimPackageRuntimeTest_BeginScriptedBattle(void)
{
    const struct BattleAnim * animation;
    int originalIndex;

    animation = &banim_data[BANIM_PACKAGE_LORM_SP1_PROOF_INDEX];
    originalIndex = AnimConf_0[0].index - 1;

    if (gBanimPackageRuntimeTestProbe.selectionCount == 0)
    {
        gBanimPackageRuntimeTestProbe.selectionCount = 1;
        gBanimPackageRuntimeTestProbe.originalIndex = (u32)originalIndex;
        gBanimPackageRuntimeTestProbe.defaultClassIndex = (u32)originalIndex;
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
    }

    gBanimIdx[EKR_POS_L] = BANIM_PACKAGE_LORM_SP1_PROOF_INDEX;
    gBanimIdx_bak[EKR_POS_L] = BANIM_PACKAGE_LORM_SP1_PROOF_INDEX;
    gBanimPackageRuntimeTestProbe.selectedBattleIndex = gBanimIdx[EKR_POS_L];
    gBanimPackageRuntimeTestProbe.battleEntryCount = 1;
}

void BanimPackageRuntimeTest_MarkBattleComplete(void)
{
    if (gBanimPackageRuntimeTestProbe.selectionCount != 0)
    {
        gBanimPackageRuntimeTestProbe.battleCompleteCount = 1;
        gBanimPackageRuntimeTestProbe.magic = BANIM_PACKAGE_RUNTIME_TEST_MAGIC;
    }
}

#endif /* FE8_BANIM_PACKAGE_RUNTIME_TEST */
