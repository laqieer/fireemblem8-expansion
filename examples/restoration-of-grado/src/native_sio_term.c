#include "global.h"

#include "bmsave.h"
#include "../include/grado_campaign_state.h"

#define ReadGameSave(slot) \
    do { \
        if (!GradoState_TryLoadNativeSave(GRADO_LOAD_NORMAL, (slot))) \
        { \
            Proc_Goto(proc, 4); \
            return; \
        } \
    } while (0)

#include "../../../src/sio_term.c"
