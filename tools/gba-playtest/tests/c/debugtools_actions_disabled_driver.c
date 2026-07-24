/*
 * Issue #11 slice 2 -- disabled-path host test driver for
 * src/debugtools_actions.c. Compiled and linked against the real
 * translation unit with FE8_EXPANSION_DEBUGTOOLS_ENABLED=0 (the same
 * gate a supported modern release build uses, see
 * include/expansion_debugtools.h): the whole Weather/Fog adapter must
 * physically collapse to the one harmless no-op public entry point, with
 * no menu/proc/hardware stub of any kind required to link successfully.
 *
 * Prints "DEBUGTOOLS_ACTIONS_DISABLED_HOST_TEST: PASS" and exits 0 on
 * success.
 */
#include <stdio.h>

#include "global.h"
#include "expansion_debugtools.h"

int main(void)
{
    /* No menu/proc/hardware stub of any kind is linked alongside this
     * driver and src/debugtools_actions.c (disabled) -- if the disabled
     * body ever grew a real dependency on StartOrphanMenu, Proc_Find,
     * Proc_Start, Proc_EndEach, or any DebugMenu_Weather/DebugMenu_Fog
     * function, this driver would fail to *link*, not just fail an
     * assertion. */
    DebugTools_RegisterWeatherFogActions();

    printf("DEBUGTOOLS_ACTIONS_DISABLED_HOST_TEST: PASS\n");
    return 0;
}
