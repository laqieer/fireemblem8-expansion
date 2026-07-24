/*
 * Issue #11 slice 2 -- host-executed Weather/Fog adapter behavior test
 * driver.
 *
 * Links directly against the real, unmodified src/debugtools_actions.c
 * and src/debugtools_registry.c (compiled for the host, see
 * test_debugtools_registry.py) plus debugtools_actions_host_stubs.c's
 * small set of hardware/menu/proc/dormant-bmdebug stand-ins, and drives
 * DebugTools_RegisterWeatherFogActions/DebugTools_RegisterAction/
 * DebugTools_GetRegisteredAction through the exact same public API
 * contributor code uses (include/expansion_debugtools.h) -- not a
 * reimplementation of the adapter's logic.
 *
 * Prints "DEBUGTOOLS_ACTIONS_HOST_TEST: PASS" and exits 0 on success; on
 * any failure it prints the specific failing assertion to stderr and
 * exits 1 without running further checks (fail fast, actionable
 * diagnostic).
 */
#include <stdio.h>
#include <string.h>

#include "global.h"
#include "uimenu.h"
#include "proc.h"
#include "expansion_debugtools.h"

#define CHECK(cond, msg) \
    do { \
        if (!(cond)) { \
            fprintf(stderr, "DEBUGTOOLS_ACTIONS_HOST_TEST: FAIL: %s\n", msg); \
            return 1; \
        } \
    } while (0)

/* Declared, not defined, by include/expansion_debugtools.h -- these are
 * the exact extra host-stub instrumentation hooks from
 * debugtools_actions_host_stubs.c this driver needs. */
extern int gDebugToolsActionsHostStub_StartOrphanMenuCallCount;
extern const struct MenuDef* gDebugToolsActionsHostStub_LastMenuDef;
extern int gDebugToolsActionsHostStub_ProcFindCallCount;
extern int gDebugToolsActionsHostStub_ProcStartCallCount;
extern int gDebugToolsActionsHostStub_ProcEndEachCallCount;
extern const struct ProcCmd* gDebugToolsActionsHostStub_LastStartedScript;
extern const struct ProcCmd* gDebugToolsActionsHostStub_LastEndEachScript;
extern int gDebugToolsActionsHostStub_WeatherDrawCallCount;
extern u8 gDebugToolsActionsHostStub_WeatherEffectCallCount;
extern u8 gDebugToolsActionsHostStub_WeatherIdleCallCount;
extern int gDebugToolsActionsHostStub_FogDrawCallCount;
extern u8 gDebugToolsActionsHostStub_FogEffectCallCount;
extern u8 gDebugToolsActionsHostStub_FogIdleCallCount;
extern struct ProcCmd CONST_DATA ProcScr_DebugMonitor[];
extern void DebugToolsHostStub_SetMonitorAlive(int alive);

extern int DebugMenu_WeatherDraw(struct MenuProc*, struct MenuItemProc*);
extern u8 DebugMenu_WeatherEffect(struct MenuProc*, struct MenuItemProc*);
extern u8 DebugMenu_WeatherIdle(struct MenuProc*, struct MenuItemProc*);
extern int DebugMenu_FogDraw(struct MenuProc*, struct MenuItemProc*);
extern u8 DebugMenu_FogEffect(struct MenuProc*, struct MenuItemProc*);
extern u8 DebugMenu_FogIdle(struct MenuProc*, struct MenuItemProc*);

/* gDebugToolsWeatherMenuDef/gDebugToolsFogMenuDef are the real, non-static
 * CONST_DATA sentinels defined by src/debugtools_actions.c. */
extern struct MenuDef CONST_DATA gDebugToolsWeatherMenuDef;
extern struct MenuDef CONST_DATA gDebugToolsFogMenuDef;
extern struct MenuDef CONST_DATA gDebugToolsHubMenuDef;

static u8 DummyOnSelected(struct MenuProc* menu, struct MenuItemProc* item)
{
    (void)menu;
    (void)item;
    return 0;
}

static int AllZero(const void* p, size_t n)
{
    size_t i;
    const unsigned char* b = (const unsigned char*)p;
    for (i = 0; i < n; ++i)
    {
        if (b[i] != 0)
            return 0;
    }
    return 1;
}

int main(void)
{
    struct DebugToolsAction filler;
    const struct DebugToolsAction* got;
    int i;
    u8 rc;

    /* --- Capacity boundary, "including Chapter 2 launcher" ------------
     * Simulate the launcher's own exactly-one-slot contribution plus six
     * more fillers (7 total), then register Weather/Fog on top: the
     * combined total must land at exactly DEBUGTOOLS_ACTION_MAX (9), not
     * silently overflow past it. */
    static const char* const fillerLabels[7] = {
        "FastBootChapter2Sim", "D1", "D2", "D3", "D4", "D5", "D6"
    };

    for (i = 0; i < 7; ++i)
    {
        filler.id = (u16)(10 + i);
        filler.label = fillerLabels[i];
        filler.onSelected = DummyOnSelected;

        CHECK(DebugTools_RegisterAction(&filler) == DEBUGTOOLS_OK, "filler action registration must succeed");
    }
    CHECK(DebugTools_GetRegisteredCount() == 7, "expected 7 filler actions registered");

    DebugTools_RegisterWeatherFogActions();
    CHECK(DebugTools_GetRegisteredCount() == 9, "Weather+Fog must bring the total to exactly DEBUGTOOLS_ACTION_MAX (9)");

    got = DebugTools_GetRegisteredAction(7);
    CHECK(got != NULL, "expected a registered action at index 7");
    CHECK(got->id == 2, "Weather must register with id 2");
    CHECK(strcmp(got->label, "Weather") == 0, "Weather must register with label \"Weather\"");

    got = DebugTools_GetRegisteredAction(8);
    CHECK(got != NULL, "expected a registered action at index 8");
    CHECK(got->id == 3, "Fog must register with id 3");
    CHECK(strcmp(got->label, "Fog") == 0, "Fog must register with label \"Fog\"");

    /* --- Idempotency: a repeat call must not grow the registry. ------- */
    DebugTools_RegisterWeatherFogActions();
    CHECK(DebugTools_GetRegisteredCount() == 9, "a repeat DebugTools_RegisterWeatherFogActions call must not grow the registry");

    /* --- Capacity-full boundary: one more registration past 9 must be
     * explicitly rejected, never silently dropped or overflowing. ----- */
    filler.id = 99;
    filler.label = "Overflow";
    filler.onSelected = DummyOnSelected;
    CHECK(DebugTools_RegisterAction(&filler) == DEBUGTOOLS_ERR_CAPACITY_FULL,
          "a 10th registration attempt must fail with DEBUGTOOLS_ERR_CAPACITY_FULL");
    CHECK(DebugTools_GetRegisteredCount() == 9, "registered count must stay at 9 after a rejected overflow attempt");

    /* --- Weather: MenuDef sentinel/callback wiring, DebugMonitor
     * lifecycle ownership. ---------------------------------------------- */
    got = DebugTools_GetRegisteredAction(7);
    DebugToolsHostStub_SetMonitorAlive(0);
    rc = got->onSelected(NULL, NULL);
    CHECK(rc == (MENU_ACT_SKIPCURSOR | MENU_ACT_END | MENU_ACT_SND6A | MENU_ACT_CLEAR),
          "Weather onSelected must return SKIPCURSOR|END|SND6A|CLEAR to close the hub");
    CHECK(gDebugToolsActionsHostStub_ProcFindCallCount == 1, "Weather onSelected must probe ProcScr_DebugMonitor exactly once");
    CHECK(gDebugToolsActionsHostStub_ProcStartCallCount == 1, "Weather onSelected must start ProcScr_DebugMonitor when none is alive");
    CHECK(gDebugToolsActionsHostStub_LastStartedScript == ProcScr_DebugMonitor, "Weather onSelected must start exactly ProcScr_DebugMonitor");
    CHECK(gDebugToolsActionsHostStub_StartOrphanMenuCallCount == 1, "Weather onSelected must open exactly one orphan submenu");
    CHECK(gDebugToolsActionsHostStub_LastMenuDef == &gDebugToolsWeatherMenuDef,
          "Weather onSelected must open gDebugToolsWeatherMenuDef, never a hand-rolled copy");

    CHECK(gDebugToolsWeatherMenuDef.menuItems != NULL, "gDebugToolsWeatherMenuDef.menuItems must be wired");
    CHECK(strcmp(gDebugToolsWeatherMenuDef.menuItems[0].name, "Weather") == 0, "weather item name must be \"Weather\"");
    CHECK(gDebugToolsWeatherMenuDef.menuItems[0].onDraw == DebugMenu_WeatherDraw, "weather item onDraw must be the real dormant DebugMenu_WeatherDraw");
    CHECK(gDebugToolsWeatherMenuDef.menuItems[0].onSelected == DebugMenu_WeatherEffect, "weather item onSelected must be the real dormant DebugMenu_WeatherEffect");
    CHECK(gDebugToolsWeatherMenuDef.menuItems[0].onIdle == DebugMenu_WeatherIdle, "weather item onIdle must be the real dormant DebugMenu_WeatherIdle");
    CHECK(gDebugToolsWeatherMenuDef.menuItems[0].isAvailable == MenuAlwaysEnabled, "weather item must always be available");
    CHECK(gDebugToolsWeatherMenuDef.menuItems[0].overrideId == 0xE0, "weather item overrideId must be the reserved 0xE0");
    CHECK(AllZero(&gDebugToolsWeatherMenuDef.menuItems[1], sizeof(gDebugToolsWeatherMenuDef.menuItems[1])),
          "weather menu's second slot must stay the all-zero MenuItemsEnd terminator");
    CHECK(gDebugToolsWeatherMenuDef.onBPress == MenuCancelSelect, "weather submenu must close on B via MenuCancelSelect, same idiom as the hub");

    CHECK(gDebugToolsWeatherMenuDef.onEnd != NULL, "weather MenuDef must define onEnd");
    CHECK(DebugTools_IsHubActive() == 0, "hub must not be active before weather's onEnd reopens it");
    gDebugToolsWeatherMenuDef.onEnd(NULL);
    CHECK(gDebugToolsActionsHostStub_ProcEndEachCallCount == 1, "weather onEnd must Proc_EndEach the monitor it started");
    CHECK(gDebugToolsActionsHostStub_LastEndEachScript == ProcScr_DebugMonitor, "weather onEnd must Proc_EndEach exactly ProcScr_DebugMonitor");
    CHECK(DebugTools_IsHubActive() != 0, "weather onEnd must reopen the hub");
    /* DebugTools_OpenHub() (the real function, src/debugtools_registry.c)
     * itself calls StartOrphanMenu(&gDebugToolsHubMenuDef) -- so the
     * reopen bumps the same instrumented call count/last-def capture
     * this driver already uses for the submenus themselves. */
    CHECK(gDebugToolsActionsHostStub_StartOrphanMenuCallCount == 2, "weather's onEnd reopening the hub must call StartOrphanMenu exactly once more");
    CHECK(gDebugToolsActionsHostStub_LastMenuDef == &gDebugToolsHubMenuDef, "weather's onEnd must reopen the real hub MenuDef, not some other menu");

    /* A second call must be a no-op for the monitor teardown (ownership
     * flag already cleared) -- only ever ends an instance this module
     * itself started, exactly once per instance it started. The hub is
     * now already active, so this second reopen attempt is rejected
     * (DEBUGTOOLS_ERR_ALREADY_ACTIVE, ignored) and must not call
     * StartOrphanMenu again either. */
    gDebugToolsWeatherMenuDef.onEnd(NULL);
    CHECK(gDebugToolsActionsHostStub_ProcEndEachCallCount == 1,
          "a second weather onEnd call must not re-issue Proc_EndEach (ownership already released)");
    CHECK(gDebugToolsActionsHostStub_StartOrphanMenuCallCount == 2,
          "a second weather onEnd call must not reopen an already-active hub");

    /* --- Fog: MenuDef sentinel/callback wiring, no DebugMonitor
     * dependency at all. --------------------------------------------- */
    got = DebugTools_GetRegisteredAction(8);
    rc = got->onSelected(NULL, NULL);
    CHECK(rc == (MENU_ACT_SKIPCURSOR | MENU_ACT_END | MENU_ACT_SND6A | MENU_ACT_CLEAR),
          "Fog onSelected must return SKIPCURSOR|END|SND6A|CLEAR to close the hub");
    CHECK(gDebugToolsActionsHostStub_StartOrphanMenuCallCount == 3, "Fog onSelected must open exactly one more orphan submenu");
    CHECK(gDebugToolsActionsHostStub_LastMenuDef == &gDebugToolsFogMenuDef,
          "Fog onSelected must open gDebugToolsFogMenuDef, never a hand-rolled copy");

    CHECK(strcmp(gDebugToolsFogMenuDef.menuItems[0].name, "Fog") == 0, "fog item name must be \"Fog\"");
    CHECK(gDebugToolsFogMenuDef.menuItems[0].onDraw == DebugMenu_FogDraw, "fog item onDraw must be the real dormant DebugMenu_FogDraw");
    CHECK(gDebugToolsFogMenuDef.menuItems[0].onSelected == DebugMenu_FogEffect, "fog item onSelected must be the real dormant DebugMenu_FogEffect");
    CHECK(gDebugToolsFogMenuDef.menuItems[0].onIdle == DebugMenu_FogIdle, "fog item onIdle must be the real dormant DebugMenu_FogIdle");
    CHECK(gDebugToolsFogMenuDef.menuItems[0].overrideId == 0xE1, "fog item overrideId must be the reserved 0xE1");
    CHECK(AllZero(&gDebugToolsFogMenuDef.menuItems[1], sizeof(gDebugToolsFogMenuDef.menuItems[1])),
          "fog menu's second slot must stay the all-zero MenuItemsEnd terminator");

    CHECK(gDebugToolsFogMenuDef.onEnd != NULL, "fog MenuDef must define onEnd");
    gDebugToolsFogMenuDef.onEnd(NULL);
    CHECK(gDebugToolsActionsHostStub_ProcEndEachCallCount == 1,
          "fog onEnd must never touch ProcScr_DebugMonitor/Proc_EndEach (no such dependency)");

    /* Weather and Fog's MenuDefs must never be aliases of each other or
     * of the hub's own item storage -- two genuinely separate bounded
     * one-item submenus. */
    CHECK(&gDebugToolsWeatherMenuDef != &gDebugToolsFogMenuDef, "weather and fog MenuDefs must be distinct objects");
    CHECK(gDebugToolsWeatherMenuDef.menuItems != gDebugToolsFogMenuDef.menuItems, "weather and fog item storage must be distinct arrays");

    printf("DEBUGTOOLS_ACTIONS_HOST_TEST: PASS\n");
    return 0;
}
