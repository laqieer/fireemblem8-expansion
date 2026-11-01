#include "global.h"

#include "eventinfo.h"
#include "../include/grado_state.h"

int GradoGuide_CheckFlag(int flag)
{
    if (GradoState_IsProgressiveGuideFlag(flag))
        return 1;

    return CheckFlag(flag);
}

void GradoGuide_SetFlag(int flag)
{
    if (!GradoState_IsProgressiveGuideFlag(flag))
        SetFlag(flag);
}

void GradoGuide_ClearFlag(int flag)
{
    if (!GradoState_IsProgressiveGuideFlag(flag))
        ClearFlag(flag);
}

#define CheckFlag GradoGuide_CheckFlag
#define SetFlag GradoGuide_SetFlag
#define ClearFlag GradoGuide_ClearFlag
#include "../../../src/bmguide.c"
