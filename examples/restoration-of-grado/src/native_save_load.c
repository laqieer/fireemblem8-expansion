#include "global.h"

#include "bmsave.h"
#include "../include/grado_campaign_state.h"

enum GradoStateLoadResult GradoState_LoadNativeSave(
    enum GradoStateLoadRoute route,
    int slot,
    struct GradoStateLoadDiagnostic * diagnostic)
{
    enum GradoStateLoadResult result;

    result = GradoState_PreflightNativeSave(route, slot, diagnostic);
    if (result != GRADO_STATE_LOAD_VALID && result != GRADO_STATE_LOAD_RECOVERY)
        return result;

    if (route == GRADO_LOAD_SUSPEND)
    {
        gSuspendSaveIdOffset = diagnostic->sourceSlot - SAVE_ID_SUSPEND;
        ReadSuspendSave(SAVE_ID_SUSPEND);
    }
    else
    {
        ReadGameSave(slot);
    }

    if (result == GRADO_STATE_LOAD_RECOVERY)
        return GradoState_ApplyLoadRecovery(diagnostic);

    return result;
}
