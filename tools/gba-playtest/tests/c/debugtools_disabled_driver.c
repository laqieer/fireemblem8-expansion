/*
 * Issue #11 slice 1 -- host-executed release/disabled-path behavior test
 * driver. Links against src/debugtools_registry.c compiled with
 * -DFE8_EXPANSION_DEBUGTOOLS_ENABLED=0 (the same gate a supported modern
 * release build compiles with -- see include/expansion_debugtools.h) and
 * proves every public entry point degrades to its documented inert,
 * disabled-result stub: no registration ever succeeds, the registry
 * never reports any registered action, the hub is never active, and the
 * hotkey check/open-hub calls are safe no-ops.
 */
#include <stdio.h>

#include "global.h"
#include "expansion_debugtools.h"

#define CHECK(cond, msg) \
    do { \
        if (!(cond)) { \
            fprintf(stderr, "DEBUGTOOLS_DISABLED_HOST_TEST: FAIL: %s\n", msg); \
            return 1; \
        } \
    } while (0)

static u8 DummyOnSelected(struct MenuProc* menu, struct MenuItemProc* item)
{
    (void)menu;
    (void)item;
    return 0;
}

int main(void)
{
    struct DebugToolsAction action;
    int rc;

    CHECK(DebugTools_GetRegisteredCount() == 0,
          "a disabled build must always report zero registered actions");
    CHECK(DebugTools_IsHubActive() == 0,
          "a disabled build must never report the hub as active");

    action.id = 1;
    action.label = "ShouldNeverRegister";
    action.onSelected = DummyOnSelected;
    rc = DebugTools_RegisterAction(&action);
    CHECK(rc == DEBUGTOOLS_ERR_DISABLED,
          "registration in a disabled build must return DEBUGTOOLS_ERR_DISABLED, "
          "never silently succeed or silently drop");
    CHECK(DebugTools_GetLastRegistrationResult() == DEBUGTOOLS_ERR_DISABLED,
          "GetLastRegistrationResult must mirror DEBUGTOOLS_ERR_DISABLED");
    CHECK(DebugTools_GetRegisteredCount() == 0,
          "a rejected disabled-build registration must not change the registered count");
    CHECK(DebugTools_GetRegisteredAction(0) == NULL,
          "a disabled build must never expose a registered action");

    /* Safe no-ops: must not crash, must not change any observable state. */
    DebugTools_TitleHotkeyCheck();
    rc = DebugTools_OpenHub();
    CHECK(rc == DEBUGTOOLS_ERR_DISABLED,
          "OpenHub must return DEBUGTOOLS_ERR_DISABLED explicitly in a disabled build, "
          "never silently succeed");
    CHECK(DebugTools_IsHubActive() == 0,
          "OpenHub/TitleHotkeyCheck must remain no-ops in a disabled build");
    CHECK(DebugTools_GetRegisteredCount() == 0,
          "OpenHub must not lazily register the built-in launcher in a disabled build");

    /* Repeating OpenHub must keep returning DEBUGTOOLS_ERR_DISABLED --
     * a disabled build has no reentrancy state to guard, but must still
     * never report success. */
    rc = DebugTools_OpenHub();
    CHECK(rc == DEBUGTOOLS_ERR_DISABLED,
          "a second OpenHub call in a disabled build must also return DEBUGTOOLS_ERR_DISABLED");

    /* DebugTools_RecordTitleIdleTimer must also be a safe no-op: it is
     * called unconditionally by Title_IDLE every frame regardless of
     * build config (see src/titlescreen.c). */
    DebugTools_RecordTitleIdleTimer(815);
    CHECK(gDebugToolsProbe.titleIdleTimerSample == 0,
          "DebugTools_RecordTitleIdleTimer must be a no-op in a disabled build");

    /* gDebugToolsProbe (always linked, both builds) must stay all-zero:
     * this is the exact invariant the release playtest scenario
     * (tools/gba-playtest/scenarios/debugtools-hub-modern-release.json)
     * asserts by reading this struct directly by address. */
    CHECK(gDebugToolsProbe.hubOpenCount == 0, "gDebugToolsProbe.hubOpenCount must stay 0 in a disabled build");
    CHECK(gDebugToolsProbe.registeredActionCount == 0, "gDebugToolsProbe.registeredActionCount must stay 0 in a disabled build");
    CHECK(gDebugToolsProbe.launcherArmed == 0, "gDebugToolsProbe.launcherArmed must stay 0 in a disabled build");

    printf("DEBUGTOOLS_DISABLED_HOST_TEST: PASS\n");
    return 0;
}
