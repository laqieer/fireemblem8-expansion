/*
 * Issue #11 slice 1 -- host-executed registration behavior test driver.
 *
 * Links directly against the real, unmodified src/debugtools_registry.c
 * (compiled for the host, see test_debugtools_registry.py) plus
 * debugtools_host_stubs.c's small set of hardware/menu stand-ins, and
 * drives DebugTools_RegisterAction/GetRegisteredCount/GetRegisteredAction/
 * GetLastRegistrationResult through the exact same public API contributor
 * code uses (include/expansion_debugtools.h) -- not a reimplementation of
 * the registry's logic.
 *
 * Prints "DEBUGTOOLS_HOST_TEST: PASS" and exits 0 on success; on any
 * failure it prints the specific failing assertion to stderr and exits 1
 * without running further checks (fail fast, actionable diagnostic).
 */
#include <stdio.h>
#include <string.h>

#include "global.h"
#include "expansion_debugtools.h"

#define CHECK(cond, msg) \
    do { \
        if (!(cond)) { \
            fprintf(stderr, "DEBUGTOOLS_HOST_TEST: FAIL: %s\n", msg); \
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
    const struct DebugToolsAction* got;
    int i;
    int rc;

    printf("DEBUGTOOLS_ACTION_MAX=%d\n", (int)DEBUGTOOLS_ACTION_MAX);
    printf("DEBUGTOOLS_HUB_MENU_SLOTS=%d\n", (int)DEBUGTOOLS_HUB_MENU_SLOTS);

    CHECK(DEBUGTOOLS_ACTION_MAX == 9, "DEBUGTOOLS_ACTION_MAX must be 9");
    CHECK(DEBUGTOOLS_HUB_MENU_SLOTS == DEBUGTOOLS_ACTION_MAX + 2,
          "DEBUGTOOLS_HUB_MENU_SLOTS must reserve exactly one Back slot "
          "and one terminator slot beyond DEBUGTOOLS_ACTION_MAX");

    CHECK(DebugTools_GetRegisteredCount() == 0,
          "registry must start empty");
    CHECK(DebugTools_GetRegisteredAction(0) == NULL,
          "GetRegisteredAction must return NULL on an empty registry");

    /* --- Fill to capacity (DEBUGTOOLS_ACTION_MAX = 9), asserting
     * deterministic append-order and DEBUGTOOLS_OK every time. --- */
    for (i = 0; i < DEBUGTOOLS_ACTION_MAX; ++i)
    {
        static const char* labels[9] = {
            "Action0", "Action1", "Action2", "Action3", "Action4",
            "Action5", "Action6", "Action7", "Action8"
        };

        action.id = (u16)(100 + i);
        action.label = labels[i];
        action.onSelected = DummyOnSelected;

        rc = DebugTools_RegisterAction(&action);
        CHECK(rc == DEBUGTOOLS_OK, "registering action within capacity must return DEBUGTOOLS_OK");
        CHECK(DebugTools_GetLastRegistrationResult() == DEBUGTOOLS_OK,
              "GetLastRegistrationResult must mirror the last DEBUGTOOLS_OK result");
        CHECK(DebugTools_GetRegisteredCount() == i + 1,
              "GetRegisteredCount must increment by exactly one per successful registration");
    }

    /* --- Deterministic ordering: registration order must be preserved
     * exactly, not reordered/sorted/compacted. --- */
    for (i = 0; i < DEBUGTOOLS_ACTION_MAX; ++i)
    {
        static const char* labels[9] = {
            "Action0", "Action1", "Action2", "Action3", "Action4",
            "Action5", "Action6", "Action7", "Action8"
        };

        got = DebugTools_GetRegisteredAction(i);
        CHECK(got != NULL, "GetRegisteredAction must return a live entry within the registered range");
        CHECK(got->id == (u16)(100 + i), "registered action id must match registration order exactly");
        CHECK(strcmp(got->label, labels[i]) == 0, "registered action label must match registration order exactly");
    }

    /* Out-of-range reads must return NULL (no sentinel/garbage reads). */
    CHECK(DebugTools_GetRegisteredAction(-1) == NULL, "negative index must return NULL");
    CHECK(DebugTools_GetRegisteredAction(DEBUGTOOLS_ACTION_MAX) == NULL,
          "index at exactly the capacity (first unused slot) must return NULL");

    /* --- Capacity-full: the 10th registration must fail explicitly, and
     * must not silently drop into an 11th MenuItemDef live slot (that
     * would overflow MENU_ITEM_MAX=11 with no room for the reserved Back
     * entry -- see docs/debugtools.md). --- */
    action.id = 999;
    action.label = "Overflow";
    action.onSelected = DummyOnSelected;
    rc = DebugTools_RegisterAction(&action);
    CHECK(rc == DEBUGTOOLS_ERR_CAPACITY_FULL,
          "the 10th registration attempt must return DEBUGTOOLS_ERR_CAPACITY_FULL");
    CHECK(DebugTools_GetLastRegistrationResult() == DEBUGTOOLS_ERR_CAPACITY_FULL,
          "GetLastRegistrationResult must mirror DEBUGTOOLS_ERR_CAPACITY_FULL");
    CHECK(DebugTools_GetRegisteredCount() == DEBUGTOOLS_ACTION_MAX,
          "a rejected registration must not change the registered count");

    /* --- Duplicate id (different label) must be rejected. --- */
    action.id = 100; /* same id as Action0 */
    action.label = "NotAction0";
    action.onSelected = DummyOnSelected;
    rc = DebugTools_RegisterAction(&action);
    CHECK(rc == DEBUGTOOLS_ERR_DUPLICATE, "duplicate id must return DEBUGTOOLS_ERR_DUPLICATE");
    CHECK(DebugTools_GetRegisteredCount() == DEBUGTOOLS_ACTION_MAX,
          "a rejected duplicate registration must not change the registered count");

    /* --- Duplicate label (different id) must also be rejected. --- */
    action.id = 555;
    action.label = "Action0"; /* same label as the first registered action */
    action.onSelected = DummyOnSelected;
    rc = DebugTools_RegisterAction(&action);
    CHECK(rc == DEBUGTOOLS_ERR_DUPLICATE, "duplicate label must return DEBUGTOOLS_ERR_DUPLICATE");

    /* --- Invalid actions (NULL action/label/onSelected) must be
     * rejected explicitly, never silently dropped. --- */
    rc = DebugTools_RegisterAction(NULL);
    CHECK(rc == DEBUGTOOLS_ERR_INVALID_ACTION, "a NULL action pointer must return DEBUGTOOLS_ERR_INVALID_ACTION");

    action.id = 1;
    action.label = NULL;
    action.onSelected = DummyOnSelected;
    rc = DebugTools_RegisterAction(&action);
    CHECK(rc == DEBUGTOOLS_ERR_INVALID_ACTION, "a NULL label must return DEBUGTOOLS_ERR_INVALID_ACTION");

    action.id = 2;
    action.label = "MissingCallback";
    action.onSelected = NULL;
    rc = DebugTools_RegisterAction(&action);
    CHECK(rc == DEBUGTOOLS_ERR_INVALID_ACTION, "a NULL onSelected callback must return DEBUGTOOLS_ERR_INVALID_ACTION");

    /* Final sanity: none of the rejected attempts leaked into the
     * registry, and the original 9 entries are still exactly as
     * registered. */
    CHECK(DebugTools_GetRegisteredCount() == DEBUGTOOLS_ACTION_MAX,
          "final registered count must still be exactly DEBUGTOOLS_ACTION_MAX");
    got = DebugTools_GetRegisteredAction(0);
    CHECK(got != NULL && got->id == 100 && strcmp(got->label, "Action0") == 0,
          "the first registered action must be unchanged by every later rejected attempt");

    /* --- Review-fix regression: DebugTools_OpenHub()'s single
     * authoritative reentrancy guard. A release-and-repress of the title
     * hotkey (or any other future caller) while the hub is already open
     * must be rejected explicitly and must never start a second
     * concurrent MenuProc / increment hubOpenCount again. --- */
    CHECK(DebugTools_IsHubActive() == 0, "hub must start inactive");
    CHECK(gDebugToolsProbe.hubOpenCount == 0, "hubOpenCount must start at 0");

    rc = DebugTools_OpenHub();
    CHECK(rc == DEBUGTOOLS_OK, "the first DebugTools_OpenHub() call must return DEBUGTOOLS_OK");
    CHECK(DebugTools_IsHubActive() != 0, "the hub must be active after a successful OpenHub");
    CHECK(gDebugToolsProbe.hubOpenCount == 1, "hubOpenCount must be exactly 1 after the first successful open");

    rc = DebugTools_OpenHub();
    CHECK(rc == DEBUGTOOLS_ERR_ALREADY_ACTIVE,
          "a second OpenHub call while the hub remains active must return DEBUGTOOLS_ERR_ALREADY_ACTIVE");
    CHECK(gDebugToolsProbe.hubOpenCount == 1,
          "hubOpenCount must stay 1 after a rejected reentrant OpenHub call (no second concurrent MenuProc)");
    CHECK(DebugTools_IsHubActive() != 0, "the hub must remain active (unchanged) after a rejected reentrant call");

    /* A third (and further) repeated call -- simulating a release-and-
     * repress of the hotkey happening more than once -- must keep
     * rejecting, not merely reject once and then silently succeed. */
    rc = DebugTools_OpenHub();
    CHECK(rc == DEBUGTOOLS_ERR_ALREADY_ACTIVE,
          "a third OpenHub call while the hub remains active must also return DEBUGTOOLS_ERR_ALREADY_ACTIVE");
    CHECK(gDebugToolsProbe.hubOpenCount == 1,
          "hubOpenCount must still be exactly 1 after repeated reentrant OpenHub calls");

    printf("DEBUGTOOLS_HOST_TEST: PASS\n");
    return 0;
}
