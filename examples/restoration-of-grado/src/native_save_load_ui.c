#include "global.h"

#include "helpbox.h"
#include "savemenu.h"
#include "../include/grado_campaign_state.h"

#include "constants/msg.h"

static int TryLoadNativeSave(
    enum GradoStateLoadRoute route, int slot, ProcPtr parentProc)
{
    struct GradoStateLoadDiagnostic diagnostic;
    enum GradoStateLoadResult result;

    result = GradoState_LoadNativeSave(route, slot, &diagnostic);
    if (result == GRADO_STATE_LOAD_VALID || result == GRADO_STATE_LOAD_REOPEN)
        return 1;

    GradoState_ReportNativeLoadRefusal(result, parentProc);
    return 0;
}

int GradoState_TryLoadNativeSaveFromProc(
    enum GradoStateLoadRoute route, int slot, ProcPtr parentProc)
{
    return TryLoadNativeSave(route, slot, parentProc);
}

void GradoState_ReportNativeLoadRefusal(
    enum GradoStateLoadResult result, ProcPtr parentProc)
{
    int message = MSG_SAVE_COMPAT_UNKNOWN;

    if (result == GRADO_STATE_LOAD_INCOMPATIBLE)
        message = MSG_SAVE_COMPAT_CONFIG_INCOMPATIBLE;

    StartSaveMenuPopupHelpBox(0x30, 0x30, message, parentProc);
}
