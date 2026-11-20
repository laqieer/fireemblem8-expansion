#include "global.h"

#include "bmsave.h"
#include "../include/grado_campaign_state.h"

#define ReadGameSave(slot) \
    do { \
        if (!GradoState_TryLoadLinkArenaNativeSaveFromProc( \
                GRADO_LOAD_NORMAL, (slot), proc)) \
        { \
            Proc_Goto(proc, 4); \
            return; \
        } \
    } while (0)

#define LinkArenaTeamBuild_Loop GradoState_LinkArenaTeamBuild_LoopCore
#include "../../../src/sio_term.c"
#undef LinkArenaTeamBuild_Loop

void LinkArenaTeamBuild_Loop(struct SioTermProc * proc)
{
    struct GradoStateLoadDiagnostic diagnostic;
    enum GradoStateLoadResult result;
    int current;

    current = proc->unk_48;
    MoveMenuCursorSkippingInvalid(
        &proc->unk_48, proc->unk_50, proc->unk_4c, proc->unk_38, 3);
    DisplayUiHand(28, 40 + proc->unk_48 * 32);

    if (current != proc->unk_48)
        SioPlaySoundEffect(3);

    if ((gKeyStatusPtr->newKeys & A_BUTTON) != 0)
    {
        result = GradoState_PreflightNativeSave(
            GRADO_LOAD_NORMAL, proc->unk_48, &diagnostic);
        if (result != GRADO_STATE_LOAD_VALID
            && result != GRADO_STATE_LOAD_RECOVERY
            && result != GRADO_STATE_LOAD_REOPEN)
        {
            SioPlaySoundEffect(2);
            GradoState_ReportLinkArenaLoadRefusal(result, proc);
            return;
        }

        SioPlaySoundEffect(2);
        Proc_Break(proc);
    }

    if ((gKeyStatusPtr->newKeys & B_BUTTON) != 0)
    {
        SioPlaySoundEffect(1);
        Proc_Goto(proc, 4);
    }
}
