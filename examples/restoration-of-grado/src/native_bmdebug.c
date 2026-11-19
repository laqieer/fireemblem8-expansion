#include "global.h"

#include "bmsave.h"
#include "../include/grado_campaign_state.h"

#define ReadSuspendSave(slot) \
    do { \
        if ((slot) == SAVE_ID_SUSPEND) \
        { \
            if (!GradoState_TryLoadNativeSave(GRADO_LOAD_SUSPEND, SAVE_ID_SUSPEND)) \
                return MENU_ACT_SND6B; \
        } \
        else \
        { \
            ReadSuspendSave(slot); \
        } \
    } while (0)

#include "../../../src/bmdebug.c"
