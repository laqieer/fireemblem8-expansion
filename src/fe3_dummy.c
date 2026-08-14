#include "global.h"
#include "fontgrp.h"
#ifdef MODERN
#include "expansion_locale.h"
#include "expansion_msg_ids.h"
#endif

void PrintDebugBuildDateAndTime(u16 *bg)
{
#ifdef MODERN
    PrintDebugStringToBG(
        bg,
        ExpansionLocale_ResolveCurrent(EXP_MSG_RAW_SURFACE_DIAGNOSTIC_BUILD_TIMESTAMP));
#else
    PrintDebugStringToBG(bg, gBuildDateTime);
#endif
    PrintDebugStringToBG(bg - 0x20, gYearProjectCreated); // subtract to print to the line above.
}
