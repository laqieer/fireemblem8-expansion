#include "global.h"

#include "helpbox.h"
#include "savemenu.h"
#include "../include/grado_campaign_state.h"

#include "constants/msg.h"

enum GradoLoadUiContext
{
    GRADO_LOAD_UI_SAVE_MENU,
    GRADO_LOAD_UI_LINK_ARENA,
    GRADO_LOAD_UI_DEBUG,
};

static int GetNativeLoadRefusalMessage(enum GradoStateLoadResult result);

static int TryLoadNativeSave(
    enum GradoStateLoadRoute route,
    int slot,
    ProcPtr parentProc,
    enum GradoLoadUiContext context)
{
    struct GradoStateLoadDiagnostic diagnostic;
    enum GradoStateLoadResult result;

    result = GradoState_LoadNativeSave(route, slot, &diagnostic);
    if (result == GRADO_STATE_LOAD_VALID || result == GRADO_STATE_LOAD_REOPEN)
        return 1;

    if (context == GRADO_LOAD_UI_LINK_ARENA)
        GradoState_ReportLinkArenaLoadRefusal(result, parentProc);
    else if (context == GRADO_LOAD_UI_DEBUG)
        StartPopupHelpBox(
            0x30, 0x30, GetNativeLoadRefusalMessage(result), parentProc,
            OBJ_VRAM0 + 0x180 * TILE_SIZE_4BPP, OBJPAL_SAVEMENU_SLOTSEL_HELPBOX);
    else
        GradoState_ReportNativeLoadRefusal(result, parentProc);

    return 0;
}

int GradoState_TryLoadNativeSaveFromProc(
    enum GradoStateLoadRoute route, int slot, ProcPtr parentProc)
{
    return TryLoadNativeSave(route, slot, parentProc, GRADO_LOAD_UI_SAVE_MENU);
}

int GradoState_TryLoadDebugSaveFromProc(
    enum GradoStateLoadRoute route, int slot, ProcPtr parentProc)
{
    return TryLoadNativeSave(route, slot, parentProc, GRADO_LOAD_UI_DEBUG);
}

int GradoState_TryLoadLinkArenaNativeSaveFromProc(
    enum GradoStateLoadRoute route, int slot, ProcPtr parentProc)
{
    return TryLoadNativeSave(route, slot, parentProc, GRADO_LOAD_UI_LINK_ARENA);
}

static int GetNativeLoadRefusalMessage(enum GradoStateLoadResult result)
{
    int message = MSG_SAVE_COMPAT_UNKNOWN;

    if (result == GRADO_STATE_LOAD_INCOMPATIBLE)
        message = MSG_SAVE_COMPAT_CONFIG_INCOMPATIBLE;

    return message;
}

void GradoState_ReportNativeLoadRefusal(
    enum GradoStateLoadResult result, ProcPtr parentProc)
{
    StartSaveMenuPopupHelpBox(
        0x30, 0x30, GetNativeLoadRefusalMessage(result), parentProc);
}

void GradoState_ReportLinkArenaLoadRefusal(
    enum GradoStateLoadResult result, ProcPtr parentProc)
{
    StartPopupHelpBox(
        0x30, 0x30, GetNativeLoadRefusalMessage(result), parentProc,
        OBJ_VRAM0 + 0x300 * TILE_SIZE_4BPP, 6);
}
