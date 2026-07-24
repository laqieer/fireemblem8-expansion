#include "global.h"

#include <string.h>

#include "proc.h"
#include "uimenu.h"
#include "fontgrp.h"
#include "bmdebug.h"
#include "expansion_debugtools.h"

#include "constants/msg.h"

/*
 * Issue #11 slice 2 -- Weather and Fog built-in actions.
 *
 * DebugToolsAction is onSelected-only (include/expansion_debugtools.h),
 * but the dormant DebugMenu_WeatherDraw/Idle/Effect and
 * DebugMenu_FogDraw/Idle/Effect functions (src/bmdebug.c, unmodified,
 * untouched by this file) are onDraw/onSelected/onIdle triples designed
 * to live *inside* a MenuItemProc that gets ticked every frame while
 * focused -- a single onSelected callback cannot drive that cycling
 * behavior directly. Each action below therefore closes the hub and
 * opens its own bounded one-item orphan submenu (same StartOrphanMenu
 * idiom as the hub itself, src/debugtools_registry.c) whose sole
 * MenuItemDef reuses the real dormant function pointers, then reopens
 * the hub from its own onEnd once the submenu's B/Back closes it -- see
 * docs/debugtools.md. This file never edits src/bmdebug.c, src/menu_def.c,
 * or src/uidebug.c, and never adds a second copy of any dormant handler.
 */

#if FE8_EXPANSION_DEBUGTOOLS_ENABLED

enum
{
    /* overrideId values distinct from every one used by src/menu_def.c's
     * own static MenuItemDef tables (0x10-0x4F observed there) and from
     * the dynamic AddMenuOverride() cmdids event scripts install at
     * runtime (src/eventscr.c's UnitMenuOverrideConf/ItemMenuOverrideConf)
     * -- an accidental collision would let some unrelated override
     * silently hide/disable/redirect this submenu's one live item. */
    DEBUGTOOLS_WEATHER_OVERRIDE_ID = 0xE0,
    DEBUGTOOLS_FOG_OVERRIDE_ID = 0xE1
};

/* --- Weather submenu ------------------------------------------------- */

EWRAM_DATA static struct MenuItemDef sWeatherMenuItemDefs[2] = {0}; /* item + terminator */
EWRAM_DATA static u8 sWeatherMonitorStartedByUs = 0;

static void DebugToolsWeather_StopMonitorIfOwned(void)
{
    if (!sWeatherMonitorStartedByUs)
        return;

    /* Proc_EndEach mirrors src/mapanim_debug.c / src/sio_menu.c's own
     * teardown of this exact script -- idempotent, a no-op if no
     * instance happens to be alive. Only ever ends an instance this
     * module itself started (see the Proc_Find guard in
     * DebugToolsActions_WeatherSelected below); an instance some other
     * system started and left running is never touched. */
    Proc_EndEach(ProcScr_DebugMonitor);
    sWeatherMonitorStartedByUs = 0;
}

static void DebugToolsWeather_OnEnd(struct MenuProc* menu)
{
    (void)menu;

    DebugToolsWeather_StopMonitorIfOwned();

    /* Reopen the hub so a second action (e.g. Fog) can be picked without
     * re-pressing the map/prep/title hotkey. The hub is guaranteed
     * closed already: this submenu only ever opens after the hub's own
     * MENU_ACT_END has ended it (see DebugToolsActions_WeatherSelected),
     * so DebugTools_OpenHub()'s reentrancy guard cannot reject this
     * call. */
    DebugTools_OpenHub();
}

CONST_DATA struct MenuDef gDebugToolsWeatherMenuDef = {
    {1, 1, 15, 0},
    0,
    sWeatherMenuItemDefs,
    0,
    DebugToolsWeather_OnEnd,
    0,
    MenuCancelSelect,
    0,
    0
};

static void DebugToolsWeather_BuildMenuItems(void)
{
    memset(sWeatherMenuItemDefs, 0, sizeof(sWeatherMenuItemDefs));

    sWeatherMenuItemDefs[0].name = "Weather";
    sWeatherMenuItemDefs[0].nameMsgId = MSG_6AC; /* "　天気" -- same vanilla label src/menu_def.c's dormant entry uses */
    sWeatherMenuItemDefs[0].overrideId = DEBUGTOOLS_WEATHER_OVERRIDE_ID;
    sWeatherMenuItemDefs[0].isAvailable = MenuAlwaysEnabled;
    sWeatherMenuItemDefs[0].onDraw = DebugMenu_WeatherDraw;
    sWeatherMenuItemDefs[0].onSelected = DebugMenu_WeatherEffect;
    sWeatherMenuItemDefs[0].onIdle = DebugMenu_WeatherIdle;

    /* sWeatherMenuItemDefs[1] stays all-zero: the MenuItemsEnd terminator. */
}

static u8 DebugToolsActions_WeatherSelected(struct MenuProc* menu, struct MenuItemProc* item)
{
    (void)menu;
    (void)item;

    /* DebugMenu_WeatherDraw/Idle (src/bmdebug.c, unmodified) read/write
     * through Proc_Find(ProcScr_DebugMonitor)'s struct DebugPrintProc --
     * in the dormant original they were only ever reachable from a
     * context that had already started this proc (src/sio_menu.c).
     * Start it here only if nothing else already has one alive, and
     * remember whether this module itself started it, so
     * DebugToolsWeather_StopMonitorIfOwned above never ends an instance
     * some other system owns. */
    if (Proc_Find(ProcScr_DebugMonitor) == NULL)
    {
        Proc_Start(ProcScr_DebugMonitor, PROC_TREE_3);
        sWeatherMonitorStartedByUs = 1;
    }

    DebugToolsWeather_BuildMenuItems();
    SetupDebugFontForBG(2, 0);

    /* Starts the weather submenu before the hub itself actually ends
     * (see the MENU_ACT_END return below) -- the submenu is therefore
     * already live the instant the hub finishes closing, never leaving
     * a gap where neither menu is reachable. */
    StartOrphanMenu(&gDebugToolsWeatherMenuDef);

    /* Closes the hub (this action's own owning menu) exactly like the
     * Chapter 2 launcher action does -- see src/debugtools_launcher.c. */
    return MENU_ACT_SKIPCURSOR | MENU_ACT_END | MENU_ACT_SND6A | MENU_ACT_CLEAR;
}

CONST_DATA static struct DebugToolsAction sWeatherAction = {
    2, "Weather", DebugToolsActions_WeatherSelected
};

/* --- Fog submenu ------------------------------------------------------- */

EWRAM_DATA static struct MenuItemDef sFogMenuItemDefs[2] = {0}; /* item + terminator */

static void DebugToolsFog_OnEnd(struct MenuProc* menu)
{
    (void)menu;

    /* DebugMenu_FogDraw/Idle/Effect (src/bmdebug.c) only ever touch
     * gPlaySt.chapterVisionRange directly -- no ProcScr_DebugMonitor
     * dependency, so there is nothing else to tear down here. Reopen the
     * hub for the same reason as the weather submenu above. */
    DebugTools_OpenHub();
}

CONST_DATA struct MenuDef gDebugToolsFogMenuDef = {
    {1, 1, 15, 0},
    0,
    sFogMenuItemDefs,
    0,
    DebugToolsFog_OnEnd,
    0,
    MenuCancelSelect,
    0,
    0
};

static void DebugToolsFog_BuildMenuItems(void)
{
    memset(sFogMenuItemDefs, 0, sizeof(sFogMenuItemDefs));

    sFogMenuItemDefs[0].name = "Fog";
    sFogMenuItemDefs[0].nameMsgId = MSG_6AD; /* "　索敵" -- same vanilla label src/menu_def.c's dormant entry uses */
    sFogMenuItemDefs[0].overrideId = DEBUGTOOLS_FOG_OVERRIDE_ID;
    sFogMenuItemDefs[0].isAvailable = MenuAlwaysEnabled;
    sFogMenuItemDefs[0].onDraw = DebugMenu_FogDraw;
    sFogMenuItemDefs[0].onSelected = DebugMenu_FogEffect;
    sFogMenuItemDefs[0].onIdle = DebugMenu_FogIdle;

    /* sFogMenuItemDefs[1] stays all-zero: the MenuItemsEnd terminator. */
}

static u8 DebugToolsActions_FogSelected(struct MenuProc* menu, struct MenuItemProc* item)
{
    (void)menu;
    (void)item;

    DebugToolsFog_BuildMenuItems();
    SetupDebugFontForBG(2, 0);

    StartOrphanMenu(&gDebugToolsFogMenuDef);

    return MENU_ACT_SKIPCURSOR | MENU_ACT_END | MENU_ACT_SND6A | MENU_ACT_CLEAR;
}

CONST_DATA static struct DebugToolsAction sFogAction = {
    3, "Fog", DebugToolsActions_FogSelected
};

void DebugTools_RegisterWeatherFogActions(void)
{
    /* Idempotent: a repeat call reports DEBUGTOOLS_ERR_DUPLICATE (same
     * id/label) for each action -- an expected, non-silent result this
     * one-shot lazy-init call site (DebugTools_OpenHub,
     * src/debugtools_registry.c) deliberately ignores, same as the
     * Chapter 2 launcher's own DebugTools_RegisterBuiltinActions. */
    DebugTools_RegisterAction(&sWeatherAction);
    DebugTools_RegisterAction(&sFogAction);
}

#else /* !FE8_EXPANSION_DEBUGTOOLS_ENABLED */

void DebugTools_RegisterWeatherFogActions(void)
{
    /* No-op: nothing to register in a release build. */
}

#endif /* FE8_EXPANSION_DEBUGTOOLS_ENABLED */
