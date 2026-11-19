#include "global.h"

#include "bmsave.h"
#include "../include/grado_campaign_state.h"

#define ReadGameSave(slot) \
    do { \
        if (!GradoState_TryLoadNativeSave(GRADO_LOAD_NORMAL, (slot))) \
            return; \
    } while (0)

#define ReadSuspendSave(slot) \
    do { \
        if ((slot) == SAVE_ID_SUSPEND) \
        { \
            if (!GradoState_TryLoadNativeSave(GRADO_LOAD_SUSPEND, SAVE_ID_SUSPEND)) \
                return; \
        } \
        else \
        { \
            ReadSuspendSave(slot); \
        } \
    } while (0)

#include "../../../src/savemenu.c"
