#include "global.h"

#include "helpbox.h"
#include "../include/grado_campaign_state.h"

#include "constants/msg.h"

int GradoState_TryLoadNativeSave(enum GradoStateLoadRoute route, int slot)
{
    struct GradoStateLoadDiagnostic diagnostic;
    enum GradoStateLoadResult result;

    result = GradoState_LoadNativeSave(route, slot, &diagnostic);
    if (result == GRADO_STATE_LOAD_VALID)
        return 1;

    GradoState_ReportNativeLoadRefusal(result);
    return 0;
}

void GradoState_ReportNativeLoadRefusal(enum GradoStateLoadResult result)
{
    int message = MSG_SAVE_COMPAT_UNKNOWN;

    if (result == GRADO_STATE_LOAD_INCOMPATIBLE)
        message = MSG_SAVE_COMPAT_CONFIG_INCOMPATIBLE;

    StartHelpBoxExt_Unk(0x30, 0x30, message);
}
