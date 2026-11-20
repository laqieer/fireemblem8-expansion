#include "global.h"
#include "expansion_debugtools.h"

#if FE8_EXPANSION_DEBUGTOOLS_ENABLED && !defined(FE8_ARCHIVAL_BUILD)

#include "debug_save_fixture_internal.h"
#include "../include/grado_campaign_state.h"

#define DebugSaveFixture_ConsumePendingContinue \
    GradoState_DebugSaveFixture_ConsumePendingContinueCore
#include "../../../src/debug_save_fixture.c"
#undef DebugSaveFixture_ConsumePendingContinue

enum DebugSaveFixtureContinueResult DebugSaveFixture_ConsumePendingContinue(void)
{
    struct SaveBlocks * image = DebugSaveFixture_GetImage();
    struct DebugSaveFixtureTarget target;
    struct GradoStateFlagView view;
    struct GradoStateLoadDiagnostic diagnostic;
    enum GradoStateLoadRoute route;
    enum GradoStateLoadResult loadResult;
    enum GradoStateLoadResult recoveryResult;
    enum DebugSaveFixtureContinueResult continueResult;
    enum SaveCompatState compatibility;
    unsigned int chapter;

    if (sDebugSaveFixtureState.phase != DEBUG_SAVE_FIXTURE_PENDING_CONTINUE)
        return DEBUG_SAVE_FIXTURE_CONTINUE_NONE;

    target = sSaveStateStableLayout.fixture.preview.target;

    if (!DebugSaveFixture_ValidatePreparedImage(&target))
    {
        DebugSaveFixture_Abort(DEBUG_SAVE_FIXTURE_ABORT_CONSUME_FAILED);
        DebugSaveFixture_SetResult(DEBUG_SAVE_FIXTURE_ERR_IMAGE_INVALID);
        return DEBUG_SAVE_FIXTURE_CONTINUE_FAILED;
    }

    compatibility = DebugSaveFixture_ClassifyImage(image);

    if (target.sourceKind == DEBUG_SAVE_FIXTURE_SOURCE_GAME
        && target.sourceGameSlot <= SAVE_ID_GAME2)
    {
        struct GameSaveBlock * gameSave =
            &image->gameSaveBlocks[target.sourceGameSlot - SAVE_ID_GAME0];

        route = GRADO_LOAD_NORMAL;
        chapter = gameSave->playSt.chapterIndex;
        view.chapterFlags = NULL;
        view.chapterBytes = 0;
        view.permanentFlags = gameSave->permanentFlags;
        view.permanentBytes = sizeof(gameSave->permanentFlags);
    }
    else if (target.sourceKind == DEBUG_SAVE_FIXTURE_SOURCE_SUSPEND
        && target.resolvedSuspendSlot >= SAVE_ID_SUSPEND
        && target.resolvedSuspendSlot <= SAVE_ID_SUSPEND_ALT
        && target.backingGameSlot <= SAVE_ID_GAME2)
    {
        struct SuspendSaveBlock * suspendSave =
            &image->suspendSaveBlocks[
                target.resolvedSuspendSlot - SAVE_ID_SUSPEND];

        if (suspendSave->playSt.gameSaveSlot != target.backingGameSlot)
        {
            DebugSaveFixture_Abort(DEBUG_SAVE_FIXTURE_ABORT_CONSUME_FAILED);
            DebugSaveFixture_SetResult(DEBUG_SAVE_FIXTURE_ERR_IMAGE_INVALID);
            return DEBUG_SAVE_FIXTURE_CONTINUE_FAILED;
        }

        route = GRADO_LOAD_SUSPEND;
        chapter = suspendSave->playSt.chapterIndex;
        view.chapterFlags = suspendSave->chapterFlags;
        view.chapterBytes = sizeof(suspendSave->chapterFlags);
        view.permanentFlags = suspendSave->permanentFlags;
        view.permanentBytes = sizeof(suspendSave->permanentFlags);
    }
    else
    {
        DebugSaveFixture_Abort(DEBUG_SAVE_FIXTURE_ABORT_CONSUME_FAILED);
        DebugSaveFixture_SetResult(DEBUG_SAVE_FIXTURE_ERR_IMAGE_INVALID);
        return DEBUG_SAVE_FIXTURE_CONTINUE_FAILED;
    }

    loadResult = GradoState_PreflightSave(
        compatibility,
        &image->expansionSaveMeta,
        route,
        chapter,
        &view,
        &diagnostic);
    if (loadResult != GRADO_STATE_LOAD_VALID
        && loadResult != GRADO_STATE_LOAD_RECOVERY
        && loadResult != GRADO_STATE_LOAD_REOPEN)
    {
        DebugSaveFixture_Abort(DEBUG_SAVE_FIXTURE_ABORT_CONSUME_FAILED);
        DebugSaveFixture_SetResult(DEBUG_SAVE_FIXTURE_ERR_IMAGE_INVALID);
        return DEBUG_SAVE_FIXTURE_CONTINUE_FAILED;
    }

    continueResult = GradoState_DebugSaveFixture_ConsumePendingContinueCore();
    if ((continueResult == DEBUG_SAVE_FIXTURE_CONTINUE_GAME
            || continueResult == DEBUG_SAVE_FIXTURE_CONTINUE_SUSPEND)
        && loadResult == GRADO_STATE_LOAD_RECOVERY)
    {
        recoveryResult = GradoState_ApplyLoadRecovery(&diagnostic);
        if (recoveryResult != GRADO_STATE_LOAD_VALID)
        {
            DebugSaveFixture_Abort(DEBUG_SAVE_FIXTURE_ABORT_CONSUME_FAILED);
            DebugSaveFixture_SetResult(DEBUG_SAVE_FIXTURE_ERR_IMAGE_INVALID);
            return DEBUG_SAVE_FIXTURE_CONTINUE_FAILED;
        }
    }

    return continueResult;
}

#else

#include "../../../src/debug_save_fixture.c"

#endif
