#include "global.h"

#include "helpbox.h"
#include "savemenu.h"
#include "../include/grado_campaign_state.h"

#include "constants/msg.h"

static int TryLoadNativeSave(
    enum GradoStateLoadRoute route, int slot, ProcPtr saveMenuProc)
{
    struct GradoStateLoadDiagnostic diagnostic;
    enum GradoStateLoadResult result;

    result = GradoState_LoadNativeSave(route, slot, &diagnostic);
    if (result == GRADO_STATE_LOAD_VALID || result == GRADO_STATE_LOAD_REOPEN)
        return 1;

    GradoState_ReportNativeLoadRefusal(result, saveMenuProc);
    return 0;
}

int GradoState_TryLoadNativeSave(enum GradoStateLoadRoute route, int slot)
{
    return TryLoadNativeSave(route, slot, NULL);
}

int GradoState_TryLoadNativeSaveFromSaveMenu(
    enum GradoStateLoadRoute route, int slot, ProcPtr saveMenuProc)
{
    return TryLoadNativeSave(route, slot, saveMenuProc);
}

void GradoState_ReportNativeLoadRefusal(
    enum GradoStateLoadResult result, ProcPtr saveMenuProc)
{
    int message = MSG_SAVE_COMPAT_UNKNOWN;

    if (result == GRADO_STATE_LOAD_INCOMPATIBLE)
        message = MSG_SAVE_COMPAT_CONFIG_INCOMPATIBLE;

    if (saveMenuProc != NULL)
        StartSaveMenuPopupHelpBox(0x30, 0x30, message, saveMenuProc);
    else
        StartHelpBoxExt_Unk(0x30, 0x30, message);
}
