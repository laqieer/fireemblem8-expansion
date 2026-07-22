/*
 * Fast Boot: Chapter 2 -- host-executed disabled-path (release build)
 * behavior driver. Links against src/debugtools_launcher.c compiled with
 * FE8_EXPANSION_DEBUGTOOLS_ENABLED=0 and proves every public
 * pending-request/bootstrap-suppression entry point is a harmless no-op:
 * nothing is ever pending, consuming always reports failure, and
 * suppression is never active -- matching debugtools-hub-modern-
 * release.json's own gDebugToolsProbe-stays-all-zero playtest evidence,
 * but checkable on the host without the arm-none-eabi toolchain.
 *
 * Prints "DEBUGTOOLS_LAUNCHER_DISABLED_HOST_TEST: PASS" and exits 0 on
 * success.
 */
#include <stdio.h>

#include "global.h"
#include "expansion_debugtools.h"

#define CHECK(cond, msg) \
    do { \
        if (!(cond)) { \
            fprintf(stderr, "DEBUGTOOLS_LAUNCHER_DISABLED_HOST_TEST: FAIL: %s\n", msg); \
            return 1; \
        } \
    } while (0)

int main(void)
{
    CHECK(!DebugTools_IsChapter2LaunchPending(), "must never report pending in a disabled build");
    CHECK(!DebugTools_IsBootstrapSuppressionActive(), "must never report suppression active in a disabled build");

    DebugTools_RequestChapter2Launch();
    CHECK(!DebugTools_IsChapter2LaunchPending(),
          "requesting a launch must remain a no-op in a disabled build");

    CHECK(DebugTools_ConsumePendingChapter2Launch() == 0,
          "consuming must always report failure (0) in a disabled build");

    DebugTools_ArmBootstrapSuppression();
    CHECK(!DebugTools_IsBootstrapSuppressionActive(),
          "arming suppression must remain a no-op in a disabled build");

    DebugTools_CleanupBootstrapObserver();
    CHECK(!DebugTools_IsBootstrapSuppressionActive(),
          "explicit cleanup must remain a harmless no-op in a disabled build");

    DebugTools_NotifyTitleScreenStarting();
    CHECK(!DebugTools_IsBootstrapSuppressionActive(),
          "notifying title-screen-starting must remain a harmless no-op in a disabled build");

    DebugTools_RegisterBuiltinActions();

    printf("DEBUGTOOLS_LAUNCHER_DISABLED_HOST_TEST: PASS\n");
    return 0;
}
