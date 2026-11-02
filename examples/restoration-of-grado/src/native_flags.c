#include "global.h"

#include "eventinfo.h"
#include "../include/grado_state_internal.h"

#define ResetPermanentFlags GradoNative_ResetPermanentFlags
#include "../../../src/eventinfo.c"
#undef ResetPermanentFlags

void ResetPermanentFlags(void)
{
    GradoNative_ResetPermanentFlags();
    SetFlag(GRADO_INTERNAL_GUIDE_TRUTH);
}
