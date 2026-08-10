#include <stdio.h>
#include <string.h>

#include "global.h"
#include "fontgrp.h"
#include "uimenu.h"
#include "expansion_debugtools.h"
#include "debugtools_internal.h"

#define CHECK(cond, msg) \
    do { \
        if (!(cond)) { \
            fprintf(stderr, "DEBUGTOOLS_LIFECYCLE_HOST_TEST: FAIL: %s\n", msg); \
            return 1; \
        } \
    } while (0)

extern int gDebugToolsLifecycleStartMenuCount;
extern int gDebugToolsLifecycleTransitionProcCount;
extern int gDebugToolsLifecycleLastMenuItemCount;
extern const struct MenuDef* gDebugToolsLifecycleLastMenuDef;
extern struct MenuProc* gDebugToolsLifecycleLastMenuProc;
extern void DebugToolsLifecycle_SetTextCounter(u16 value);
extern u16 DebugToolsLifecycle_GetTextCounter(void);
extern void DebugToolsLifecycle_RunPendingTransition(void);

extern u8 DebugToolsLifecycle_Builtin1Selected(struct MenuProc*, struct MenuItemProc*);
extern u8 DebugToolsLifecycle_Builtin2Selected(struct MenuProc*, struct MenuItemProc*);
extern u8 DebugToolsLifecycle_Builtin3Selected(struct MenuProc*, struct MenuItemProc*);
extern u8 DebugToolsLifecycle_Builtin4Selected(struct MenuProc*, struct MenuItemProc*);
extern u8 DebugToolsLifecycle_Builtin5Selected(struct MenuProc*, struct MenuItemProc*);
extern u8 DebugToolsLifecycle_Builtin6Selected(struct MenuProc*, struct MenuItemProc*);
extern u8 DebugToolsLifecycle_Builtin7Selected(struct MenuProc*, struct MenuItemProc*);
extern u8 DebugToolsLifecycle_Builtin8Selected(struct MenuProc*, struct MenuItemProc*);
extern u8 DebugToolsLifecycle_Builtin9Selected(struct MenuProc*, struct MenuItemProc*);

extern struct MenuDef CONST_DATA gDebugToolsHubMenuDef;

static u8 ContributorCollisionSelected(struct MenuProc* menu, struct MenuItemProc* item)
{
    (void)menu;
    (void)item;
    return 0;
}

static void FakeSubmenuOnEnd(struct MenuProc* menu)
{
    DebugTools_ReturnToHubAfterMenuEnd(menu);
}

static struct MenuItemDef sFakeSubmenuItems[] =
{
    {"Back", 0, 0, 0, 0, MenuAlwaysEnabled, NULL, MenuCancelSelect, NULL, NULL, NULL},
    {0}
};

static CONST_DATA struct MenuDef sFakeSubmenuDef =
{
    {1, 1, DEBUGTOOLS_MENU_WIDTH_TILES, 0},
    0,
    sFakeSubmenuItems,
    0,
    FakeSubmenuOnEnd,
    0,
    MenuCancelSelect,
    0,
    0
};

int main(void)
{
    static const char* const expectedLabels[9] =
    {
        "Fast Boot: Chapter 2",
        "Weather",
        "Fog",
        "Fast Boot: Ch4 Prep",
        "Unit Inspect",
        "Convoy Inspect",
        "Flag/Chapter",
        "RNG Inspect",
        "Save State"
    };
    static u8 (*const expectedCallbacks[9])(struct MenuProc*, struct MenuItemProc*) =
    {
        DebugToolsLifecycle_Builtin1Selected,
        DebugToolsLifecycle_Builtin2Selected,
        DebugToolsLifecycle_Builtin3Selected,
        DebugToolsLifecycle_Builtin4Selected,
        DebugToolsLifecycle_Builtin5Selected,
        DebugToolsLifecycle_Builtin6Selected,
        DebugToolsLifecycle_Builtin7Selected,
        DebugToolsLifecycle_Builtin8Selected,
        DebugToolsLifecycle_Builtin9Selected
    };
    struct DebugToolsAction contributor;
    struct Text statusText;
    const struct DebugToolsAction* action;
    const u16 textBase = 17;
    int i;
    int cycle;

    CHECK(DEBUGTOOLS_BUILTIN_ID_MIN == 1 && DEBUGTOOLS_BUILTIN_ID_MAX == 9,
          "built-in IDs must remain the closed range 1-9");
    CHECK(DEBUGTOOLS_CONTRIBUTOR_ID_MIN == 10
          && DEBUGTOOLS_CONTRIBUTOR_ID_MAX == 0xFFFF,
          "contributor IDs must remain the explicit range 10-65535");
    CHECK(DEBUGTOOLS_TEXT_ALLOC_CAPACITY == 448,
          "the default BG text allocator capacity must remain 448 columns");
    CHECK(DEBUGTOOLS_HUB_TEXT_ALLOC_BUDGET == 204,
          "the maximum hub plus localized status line must use 204 columns");

    contributor.id = 1;
    contributor.label = "Contributor Collision";
    contributor.onSelected = ContributorCollisionSelected;
    CHECK(DebugTools_RegisterAction(&contributor) == DEBUGTOOLS_ERR_ID_RESERVED,
          "a contributor must not claim built-in id 1 before lazy initialization");
    CHECK(DebugTools_GetRegisteredCount() == 0,
          "a rejected reserved-ID collision must not initialize or mutate the registry");

    contributor.id = DEBUGTOOLS_CONTRIBUTOR_ID_MIN;
    contributor.label = "Contributor Before Init";
    CHECK(DebugTools_RegisterAction(&contributor) == DEBUGTOOLS_ERR_CAPACITY_FULL,
          "a valid contributor-before-init attempt must initialize all built-ins before capacity is checked");
    CHECK(DebugTools_GetRegisteredCount() == DEBUGTOOLS_ACTION_MAX,
          "lazy initialization must deterministically install all nine built-ins");

    for (i = 0; i < DEBUGTOOLS_ACTION_MAX; ++i)
    {
        action = DebugTools_GetRegisteredAction(i);
        CHECK(action != NULL, "every built-in registry row must exist");
        CHECK(action->id == (u16)(i + 1), "built-in IDs must remain in deterministic 1-9 order");
        CHECK(strcmp(action->label, expectedLabels[i]) == 0,
              "each built-in ID must retain its matching label");
        CHECK(action->onSelected == expectedCallbacks[i],
              "each built-in ID/label must retain its matching callback identity");
        CHECK(action->onSelected != ContributorCollisionSelected,
              "the rejected contributor callback must never occupy a localized built-in row");
    }

    DebugToolsLifecycle_SetTextCounter(textBase);
    CHECK(DebugTools_OpenHub() == DEBUGTOOLS_OK, "opening a maximum-row hub must succeed");
    CHECK(gDebugToolsLifecycleLastMenuDef == &gDebugToolsHubMenuDef,
          "the opened menu must be the real debug hub");
    CHECK(gDebugToolsLifecycleLastMenuItemCount == DEBUGTOOLS_ACTION_MAX + 1,
          "the maximum hub must contain nine actions plus Back");
    CHECK(DebugToolsLifecycle_GetTextCounter()
          == textBase + (DEBUGTOOLS_ACTION_MAX + 1) * (DEBUGTOOLS_MENU_WIDTH_TILES - 1),
          "the maximum hub rows must allocate exactly 180 text columns");

    InitText(&statusText, DEBUGTOOLS_STATUS_TEXT_WIDTH_TILES);
    CHECK(DebugToolsLifecycle_GetTextCounter()
          == textBase + DEBUGTOOLS_HUB_TEXT_ALLOC_BUDGET,
          "the maximum CJK hub/status allocation must equal the 204-column budget");
    CHECK(DebugToolsLifecycle_GetTextCounter() <= DEBUGTOOLS_TEXT_ALLOC_CAPACITY,
          "the maximum hub/status allocation must stay within text tile capacity");

    for (cycle = 0; cycle < 64; ++cycle)
    {
        u16 hubCounter = DebugToolsLifecycle_GetTextCounter();
        int startsBefore = gDebugToolsLifecycleStartMenuCount;

        DebugTools_QueueSubmenuTransition(
            gDebugToolsLifecycleLastMenuProc,
            &sFakeSubmenuDef);
        gDebugToolsHubMenuDef.onEnd(gDebugToolsLifecycleLastMenuProc);
        CHECK(DebugToolsLifecycle_GetTextCounter() == hubCounter,
              "hub onEnd must not rewind while its Text rows are still live");
        CHECK(gDebugToolsLifecycleStartMenuCount == startsBefore,
              "hub onEnd must not start the submenu before the old menu dies");
        CHECK(DebugTools_IsHubActive() != 0,
              "the session guard must remain active across the transition frame");

        DebugToolsLifecycle_RunPendingTransition();
        CHECK(gDebugToolsLifecycleLastMenuDef == &sFakeSubmenuDef,
              "the deferred transition must open the queued submenu");
        CHECK(DebugToolsLifecycle_GetTextCounter()
              == textBase + (DEBUGTOOLS_MENU_WIDTH_TILES - 1),
              "submenu allocation must restart from the saved text base");

        sFakeSubmenuDef.onEnd(gDebugToolsLifecycleLastMenuProc);
        CHECK(DebugToolsLifecycle_GetTextCounter()
              == textBase + (DEBUGTOOLS_MENU_WIDTH_TILES - 1),
              "submenu onEnd must not rewind while its Text row is still live");

        DebugToolsLifecycle_RunPendingTransition();
        CHECK(gDebugToolsLifecycleLastMenuDef == &gDebugToolsHubMenuDef,
              "the deferred submenu transition must reopen the hub");
        InitText(&statusText, DEBUGTOOLS_STATUS_TEXT_WIDTH_TILES);
        CHECK(DebugToolsLifecycle_GetTextCounter()
              == textBase + DEBUGTOOLS_HUB_TEXT_ALLOC_BUDGET,
              "every reopened maximum CJK hub must return to the same bounded peak");
    }

    CHECK(gDebugToolsLifecycleTransitionProcCount == 64 * 2,
          "each hub/submenu cycle must schedule exactly two deferred transitions");

    gDebugToolsHubMenuDef.onEnd(gDebugToolsLifecycleLastMenuProc);
    CHECK(DebugToolsLifecycle_GetTextCounter()
          == textBase + DEBUGTOOLS_HUB_TEXT_ALLOC_BUDGET,
          "final hub cleanup must also defer rewind until live Text rows end");
    DebugToolsLifecycle_RunPendingTransition();
    CHECK(DebugToolsLifecycle_GetTextCounter() == textBase,
          "final cleanup must restore the allocator to its pre-debug base");
    CHECK(DebugTools_IsHubActive() == 0,
          "the session guard must clear only after deferred final cleanup");

    {
        int startsBefore = gDebugToolsLifecycleStartMenuCount;

        DebugToolsLifecycle_SetTextCounter(
            DEBUGTOOLS_TEXT_ALLOC_CAPACITY - DEBUGTOOLS_HUB_TEXT_ALLOC_BUDGET + 1);
        CHECK(DebugTools_OpenHub() == DEBUGTOOLS_ERR_TEXT_CAPACITY,
              "opening must fail explicitly when one maximum hub would exceed capacity");
        CHECK(gDebugToolsLifecycleStartMenuCount == startsBefore,
              "a text-capacity failure must not start a menu");
        CHECK(DebugTools_IsHubActive() == 0,
              "a text-capacity failure must not leave the session guard active");
    }

    printf("DEBUGTOOLS_LIFECYCLE_HOST_TEST: PASS\n");
    return 0;
}
