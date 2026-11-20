#include "global.h"

#include "bmsave.h"
#include "savemenu.h"
#include "../include/grado_campaign_state.h"

void GradoState_SaveMenu_NewGame(ProcPtr procPtr);

#define SAVEMENU_NEW_GAME_HANDLER GradoState_SaveMenu_NewGame
#define SaveMenu_NewGame GradoState_SaveMenu_NewGameCore

#define ReadGameSave(slot) \
    do { \
        if (!GradoState_TryLoadNativeSaveFromSaveMenu(GRADO_LOAD_NORMAL, (slot), proc)) \
            return; \
    } while (0)

#define ReadSuspendSave(slot) \
    do { \
        if ((slot) == SAVE_ID_SUSPEND) \
        { \
            if (!GradoState_TryLoadNativeSaveFromSaveMenu(GRADO_LOAD_SUSPEND, SAVE_ID_SUSPEND, proc)) \
                return; \
        } \
        else \
        { \
            ReadSuspendSave(slot); \
        } \
    } while (0)

#include "../../../src/savemenu.c"

#undef SAVEMENU_NEW_GAME_HANDLER
#undef SaveMenu_NewGame

void GradoState_SaveMenu_NewGame(ProcPtr procPtr)
{
    struct SaveMenuProc * proc = procPtr;
    struct GradoStateLoadDiagnostic diagnostic;
    enum GradoStateLoadRoute route;
    enum GradoStateLoadResult result;
    int slot;

    if (proc->main_sel_bitfile & MAIN_MENU_OPTION_RESUME)
    {
        route = GRADO_LOAD_SUSPEND;
        slot = SAVE_ID_SUSPEND;
    }
    else if (proc->main_sel_bitfile & (MAIN_MENU_OPTION_RESTART | MAIN_MENU_OPTION_7))
    {
        route = GRADO_LOAD_NORMAL;
        slot = proc->sus_slot;
    }
    else
    {
        GradoState_SaveMenu_NewGameCore(procPtr);
        return;
    }

    result = GradoState_PreflightNativeSave(route, slot, &diagnostic);
    if (result != GRADO_STATE_LOAD_VALID
        && result != GRADO_STATE_LOAD_RECOVERY
        && result != GRADO_STATE_LOAD_REOPEN)
    {
        GradoState_ReportNativeLoadRefusal(result, procPtr);
        return;
    }

    GradoState_SaveMenu_NewGameCore(procPtr);
}

void SaveMenu_NewGame(ProcPtr procPtr)
{
    GradoState_SaveMenu_NewGame(procPtr);
}
