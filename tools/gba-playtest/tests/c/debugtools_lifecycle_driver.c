#include <stdio.h>
#include <string.h>

#include "global.h"
#include "hardware.h"
#include "fontgrp.h"
#include "uimenu.h"
#include "expansion_locale.h"
#include "expansion_debugtools.h"

#define CHECK(cond, msg) \
    do { \
        if (!(cond)) { \
            fprintf(stderr, "DEBUGTOOLS_LIFECYCLE_HOST_TEST: FAIL: %s\n", msg); \
            return 1; \
        } \
    } while (0)

extern int gDebugToolsLifecycleStartMenuCount;
extern int gDebugToolsLifecycleTransitionProcCount;
extern int gDebugToolsLifecycleEndMenuCount;
extern int gDebugToolsLifecycleCursorDisplayCount;
extern int gDebugToolsLifecycleLastMenuItemCount;
extern const struct MenuDef* gDebugToolsLifecycleLastMenuDef;
extern struct MenuProc* gDebugToolsLifecycleLastMenuProc;
extern void DebugToolsLifecycle_SetTextCounter(u16 value);
extern u16 DebugToolsLifecycle_GetTextCounter(void);
extern void DebugToolsLifecycle_RunPendingTransition(void);
extern void DebugToolsLifecycle_SetDiagnosticsRestoring(int restoring);

extern u8 DebugToolsLifecycle_Builtin1Selected(struct MenuProc*, struct MenuItemProc*);
extern u8 DebugToolsLifecycle_Builtin2Selected(struct MenuProc*, struct MenuItemProc*);
extern u8 DebugToolsLifecycle_Builtin3Selected(struct MenuProc*, struct MenuItemProc*);
extern u8 DebugToolsLifecycle_Builtin4Selected(struct MenuProc*, struct MenuItemProc*);
extern u8 DebugToolsLifecycle_Builtin5Selected(struct MenuProc*, struct MenuItemProc*);
extern u8 DebugToolsLifecycle_Builtin6Selected(struct MenuProc*, struct MenuItemProc*);
extern u8 DebugToolsLifecycle_Builtin7Selected(struct MenuProc*, struct MenuItemProc*);
extern u8 DebugToolsLifecycle_Builtin8Selected(struct MenuProc*, struct MenuItemProc*);
extern u8 DebugToolsLifecycle_Builtin9Selected(struct MenuProc*, struct MenuItemProc*);
extern u8 DebugToolsLifecycle_Builtin10Selected(struct MenuProc*, struct MenuItemProc*);

extern struct MenuDef CONST_DATA gDebugToolsHubMenuDef;
extern struct Font* gActiveFont;

enum
{
    SUBMENU_FONT_TEXT_BASE = 71,
    SUBMENU_FONT_TEXT_WIDTH = 7
};

static struct Font sFakeSubmenuFont;
static struct Text sFakeSubmenuText;
static int sFakeSubmenuInitCount;

static void DispatchMenuKey(u16 key)
{
    gKeyStatusPtr->newKeys = key;
    gKeyStatusPtr->repeatedKeys = 0;
    Menu_OnIdle(gDebugToolsLifecycleLastMenuProc);
    gKeyStatusPtr->newKeys = 0;
}

static void FakeSubmenuOnInit(struct MenuProc* menu)
{
    (void)menu;

    sFakeSubmenuInitCount++;
    SetTextFont(&sFakeSubmenuFont);
    InitText(&sFakeSubmenuText, SUBMENU_FONT_TEXT_WIDTH);
}

static u8 ContributorCollisionSelected(struct MenuProc* menu, struct MenuItemProc* item)
{
    (void)menu;
    (void)item;
    return 0;
}

static u8 Contributor0Selected(struct MenuProc* menu, struct MenuItemProc* item);

static u8 Contributor1Selected(struct MenuProc* menu, struct MenuItemProc* item)
{
    (void)menu;
    (void)item;
    return 11;
}

static u8 Contributor2Selected(struct MenuProc* menu, struct MenuItemProc* item)
{
    (void)menu;
    (void)item;
    return 12;
}

static u8 Contributor3Selected(struct MenuProc* menu, struct MenuItemProc* item)
{
    (void)menu;
    (void)item;
    return 13;
}

static u8 Contributor4Selected(struct MenuProc* menu, struct MenuItemProc* item)
{
    (void)menu;
    (void)item;
    return 14;
}

static u8 Contributor5Selected(struct MenuProc* menu, struct MenuItemProc* item)
{
    (void)menu;
    (void)item;
    return 15;
}

static u8 Contributor6Selected(struct MenuProc* menu, struct MenuItemProc* item)
{
    (void)menu;
    (void)item;
    return 16;
}

static u8 Contributor7Selected(struct MenuProc* menu, struct MenuItemProc* item)
{
    (void)menu;
    (void)item;
    return 17;
}

static u8 Contributor8Selected(struct MenuProc* menu, struct MenuItemProc* item)
{
    (void)menu;
    (void)item;
    return 18;
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
    FakeSubmenuOnInit,
    FakeSubmenuOnEnd,
    0,
    MenuCancelSelect,
    0,
    0
};

static u8 Contributor0Selected(struct MenuProc* menu, struct MenuItemProc* item)
{
    (void)item;

    DebugTools_QueueSubmenuTransition(menu, &sFakeSubmenuDef);

    return MENU_ACT_SKIPCURSOR | MENU_ACT_END | MENU_ACT_SND6A | MENU_ACT_CLEAR;
}

int main(void)
{
    static const char* const expectedLabels[10] =
    {
        "Fast Boot: Chapter 2",
        "Weather",
        "Fog",
        "Chapter/Skirmish",
        "Unit Inspect",
        "Convoy Inspect",
        "Flag/Chapter",
        "RNG Inspect",
        "Save State",
        "Music Preview"
    };
    static u8 (*const expectedCallbacks[10])(struct MenuProc*, struct MenuItemProc*) =
    {
        DebugToolsLifecycle_Builtin1Selected,
        DebugToolsLifecycle_Builtin2Selected,
        DebugToolsLifecycle_Builtin3Selected,
        DebugToolsLifecycle_Builtin4Selected,
        DebugToolsLifecycle_Builtin5Selected,
        DebugToolsLifecycle_Builtin6Selected,
        DebugToolsLifecycle_Builtin7Selected,
        DebugToolsLifecycle_Builtin8Selected,
        DebugToolsLifecycle_Builtin9Selected,
        DebugToolsLifecycle_Builtin10Selected
    };
    static const char* const contributorLabels[9] =
    {
        "Contributor 0",
        "Contributor 1",
        "Contributor 2",
        "Contributor 3",
        "Contributor 4",
        "Contributor 5",
        "Contributor 6",
        "Contributor 7",
        "Contributor 8"
    };
    static u8 (*const contributorCallbacks[9])(struct MenuProc*, struct MenuItemProc*) =
    {
        Contributor0Selected,
        Contributor1Selected,
        Contributor2Selected,
        Contributor3Selected,
        Contributor4Selected,
        Contributor5Selected,
        Contributor6Selected,
        Contributor7Selected,
        Contributor8Selected
    };
    struct DebugToolsAction contributors[9];
    struct DebugToolsAction contributor;
    struct Text statusText;
    const struct DebugToolsAction* action;
    struct Font* sessionFont;
    const u16 textBase = 17;
    int i;
    int cycle;

    CHECK(DEBUGTOOLS_BUILTIN_ID_MIN == 1 && DEBUGTOOLS_BUILTIN_ID_MAX == 10,
          "built-in IDs must remain the closed range 1-10");
    CHECK(DEBUGTOOLS_CONTRIBUTOR_ID_MIN == 11
          && DEBUGTOOLS_CONTRIBUTOR_ID_MAX == 0xFFFF,
          "contributor IDs must remain the explicit range 11-65535");
    CHECK(DEBUGTOOLS_BUILTIN_ACTION_MAX == 10,
          "built-in storage must contain exactly ten actions");
    CHECK(DEBUGTOOLS_CONTRIBUTOR_ACTION_MAX == 9,
          "public contributor storage must preserve nine actions");
    CHECK(DEBUGTOOLS_ACTION_MAX == 19,
          "combined introspection capacity must be 19");
    CHECK(DEBUGTOOLS_HUB_PAGE_ACTION_MAX == 9
          && DEBUGTOOLS_HUB_PAGE_MAX == 3,
          "the full registry must render as at most three bounded pages");
    CHECK(DEBUGTOOLS_TEXT_ALLOC_CAPACITY == 448,
          "the default BG text allocator capacity must remain 448 columns");
    CHECK(DEBUGTOOLS_HUB_TEXT_ALLOC_BUDGET == 187,
          "the maximum hub plus localized status line must use 187 columns");
    CHECK(ExpansionLocale_GetCurrent() == EXPANSION_LOCALE_QPS_PLOC,
          "the public lifecycle fixture must execute under QPS");

    for (i = 0; i < DEBUGTOOLS_CONTRIBUTOR_ACTION_MAX; ++i)
    {
        contributors[i].id = (u16)(DEBUGTOOLS_CONTRIBUTOR_ID_MIN + i);
        contributors[i].label = contributorLabels[i];
        contributors[i].onSelected = contributorCallbacks[i];
    }

    contributor.id = 1;
    contributor.label = "Contributor Collision";
    contributor.onSelected = ContributorCollisionSelected;
    CHECK(DebugTools_RegisterAction(&contributor) == DEBUGTOOLS_ERR_ID_RESERVED,
          "a contributor must not claim built-in id 1 before lazy initialization");
    CHECK(DebugTools_GetRegisteredCount() == 0,
          "a rejected reserved-ID collision must not initialize or mutate the registry");

    contributor.id = 10;
    contributor.label = "Former Contributor Boundary";
    contributor.onSelected = ContributorCollisionSelected;
    CHECK(DebugTools_RegisterAction(&contributor) == DEBUGTOOLS_ERR_ID_RESERVED,
          "built-in music id 10 must be reserved before lazy initialization");
    CHECK(DebugTools_GetRegisteredCount() == 0,
          "rejecting id 10 must not initialize or mutate the registry");

    CHECK(DebugTools_RegisterAction(&contributors[0]) == DEBUGTOOLS_OK,
          "the first valid contributor-before-init registration must succeed");
    CHECK(DebugTools_GetRegisteredCount() == DEBUGTOOLS_BUILTIN_ACTION_MAX + 1,
          "lazy initialization must install ten built-ins without consuming contributor storage");

    contributor.id = contributors[0].id;
    contributor.label = "Duplicate Id";
    contributor.onSelected = ContributorCollisionSelected;
    CHECK(DebugTools_RegisterAction(&contributor) == DEBUGTOOLS_ERR_DUPLICATE,
          "a duplicate contributor id must be rejected");
    CHECK(DebugTools_GetRegisteredCount() == DEBUGTOOLS_BUILTIN_ACTION_MAX + 1,
          "a duplicate id must not change the combined count");

    contributor.id = DEBUGTOOLS_CONTRIBUTOR_ID_MAX;
    contributor.label = contributors[0].label;
    CHECK(DebugTools_RegisterAction(&contributor) == DEBUGTOOLS_ERR_DUPLICATE,
          "a duplicate contributor label must be rejected");

    for (i = 1; i < DEBUGTOOLS_CONTRIBUTOR_ACTION_MAX; ++i)
    {
        CHECK(DebugTools_RegisterAction(&contributors[i]) == DEBUGTOOLS_OK,
              "all nine documented contributor registrations must succeed");
    }

    contributor.id = DEBUGTOOLS_CONTRIBUTOR_ID_MIN
        + DEBUGTOOLS_CONTRIBUTOR_ACTION_MAX;
    contributor.label = "Contributor Overflow";
    CHECK(DebugTools_RegisterAction(&contributor) == DEBUGTOOLS_ERR_CAPACITY_FULL,
          "contributor capacity plus one must fail explicitly");
    CHECK(DebugTools_GetRegisteredCount() == DEBUGTOOLS_ACTION_MAX,
          "capacity failure must leave all ten built-ins and contributors intact");

    for (i = 0; i < DEBUGTOOLS_BUILTIN_ACTION_MAX; ++i)
    {
        action = DebugTools_GetRegisteredAction(i);
        CHECK(action != NULL, "every built-in registry row must exist");
        CHECK(action->id == (u16)(i + 1), "built-in IDs must remain in deterministic 1-10 order");
        CHECK(strcmp(action->label, expectedLabels[i]) == 0,
              "each built-in ID must retain its matching label");
        CHECK(action->onSelected == expectedCallbacks[i],
              "each built-in ID/label must retain its matching callback identity");
        CHECK(action->onSelected != ContributorCollisionSelected,
              "the rejected contributor callback must never occupy a localized built-in row");
    }

    for (i = 0; i < DEBUGTOOLS_CONTRIBUTOR_ACTION_MAX; ++i)
    {
        action = DebugTools_GetRegisteredAction(DEBUGTOOLS_BUILTIN_ACTION_MAX + i);
        CHECK(action != NULL, "every contributor registry row must exist");
        CHECK(action->id == contributors[i].id,
              "contributor IDs must retain registration order");
        CHECK(action->label == contributors[i].label,
              "contributor label pointer identity must be preserved");
        CHECK(action->onSelected == contributors[i].onSelected,
              "contributor callback identity must be preserved");
    }

    CHECK(DebugTools_GetRegisteredAction(-1) == NULL,
          "negative combined registry indices must return NULL");
    CHECK(DebugTools_GetRegisteredAction(DEBUGTOOLS_ACTION_MAX) == NULL,
          "the first index past combined capacity must return NULL");

    DebugToolsLifecycle_SetTextCounter(textBase);
    sessionFont = gActiveFont;
    memset(&sFakeSubmenuFont, 0, sizeof(sFakeSubmenuFont));
    sFakeSubmenuFont.tileref = 0x180;
    sFakeSubmenuFont.chr_counter = SUBMENU_FONT_TEXT_BASE;
    CHECK(DebugTools_OpenHub() == DEBUGTOOLS_OK,
          "opening a maximum built-in-plus-contributor registry must succeed");
    CHECK(gDebugToolsLifecycleLastMenuDef == &gDebugToolsHubMenuDef,
          "the opened menu must be the real debug hub");
    CHECK(gDebugToolsLifecycleLastMenuItemCount == DEBUGTOOLS_HUB_PAGE_ACTION_MAX + 1,
          "the first page must contain nine built-ins plus Back");
    CHECK(DebugToolsLifecycle_GetTextCounter()
          == textBase + (DEBUGTOOLS_HUB_PAGE_ACTION_MAX + 1)
             * (DEBUGTOOLS_MENU_WIDTH_TILES - 1),
          "each maximum hub page must allocate exactly 180 text columns");

    for (i = 0; i < DEBUGTOOLS_HUB_PAGE_ACTION_MAX; ++i)
    {
        CHECK(strcmp(gDebugToolsHubMenuDef.menuItems[i].name, expectedLabels[i]) == 0,
              "page one must preserve built-in label order");
        CHECK(gDebugToolsHubMenuDef.menuItems[i].onSelected == expectedCallbacks[i],
              "page one must preserve built-in callback identity");
        CHECK(gDebugToolsHubMenuDef.menuItems[i].onDraw != NULL,
              "QPS must keep built-in rows on the localized draw adapter");
    }

    InitText(&statusText, DEBUGTOOLS_STATUS_TEXT_WIDTH_TILES);
    CHECK(DebugToolsLifecycle_GetTextCounter()
          == textBase + DEBUGTOOLS_HUB_TEXT_ALLOC_BUDGET,
          "the maximum CJK hub/status allocation must equal the 187-column budget");
    CHECK(DebugToolsLifecycle_GetTextCounter() <= DEBUGTOOLS_TEXT_ALLOC_CAPACITY,
          "the maximum hub/status allocation must stay within text tile capacity");

    {
        u16 hubCounter = DebugToolsLifecycle_GetTextCounter();
        int cursorBefore = gDebugToolsLifecycleCursorDisplayCount;
        int endsBefore = gDebugToolsLifecycleEndMenuCount;

        DispatchMenuKey(R_BUTTON);
        CHECK(DebugToolsLifecycle_GetTextCounter() == hubCounter,
              "page switch must not rewind while page-one Text rows are live");
        CHECK(gDebugToolsLifecycleEndMenuCount == endsBefore,
              "R dispatch must not end the menu synchronously from onRPress");
        CHECK(gDebugToolsLifecycleCursorDisplayCount == cursorBefore + 1,
              "Menu_OnIdle must finish the current R input dispatch with the menu alive");
        CHECK(gDebugToolsLifecycleLastMenuProc->state & MENU_STATE_FROZEN,
              "a queued R page transition must freeze input until deferred teardown");
        DispatchMenuKey(B_BUTTON);
        CHECK(gDebugToolsLifecycleEndMenuCount == endsBefore,
              "frozen input must prevent a second-frame B press from ending the queued page");
        DebugToolsLifecycle_RunPendingTransition();
        CHECK(gDebugToolsLifecycleEndMenuCount == endsBefore + 1,
              "the deferred transition proc must end the old page after dispatch");
        CHECK(gDebugToolsLifecycleLastMenuDef == &gDebugToolsHubMenuDef,
              "R must reopen the hub on contributor page two");
        CHECK(gDebugToolsLifecycleLastMenuItemCount
              == DEBUGTOOLS_HUB_PAGE_ACTION_MAX + 1,
              "a full contributor page must contain nine actions plus Back");

        CHECK(strcmp(gDebugToolsHubMenuDef.menuItems[0].name, "Music Preview") == 0,
              "page two must begin with the localized music-preview built-in");
        CHECK(gDebugToolsHubMenuDef.menuItems[0].onDraw != NULL,
              "the music-preview built-in must retain localized drawing");

        for (i = 0; i < DEBUGTOOLS_CONTRIBUTOR_ACTION_MAX - 1; ++i)
        {
            CHECK(gDebugToolsHubMenuDef.menuItems[i + 1].name == contributors[i].label,
                  "page two must preserve contributor label pointer identity");
            CHECK(gDebugToolsHubMenuDef.menuItems[i + 1].onSelected
                  == contributors[i].onSelected,
                  "page two must preserve contributor callback identity");
            CHECK(gDebugToolsHubMenuDef.menuItems[i + 1].onDraw == NULL,
                  "QPS must preserve contributor rows on the raw label renderer");
        }

        InitText(&statusText, DEBUGTOOLS_STATUS_TEXT_WIDTH_TILES);
        CHECK(DebugToolsLifecycle_GetTextCounter()
              == textBase + DEBUGTOOLS_HUB_TEXT_ALLOC_BUDGET,
              "the contributor page must use the same bounded allocator peak");
    }

    for (cycle = 0; cycle < 64; ++cycle)
    {
        u16 hubCounter = DebugToolsLifecycle_GetTextCounter();
        int startsBefore = gDebugToolsLifecycleStartMenuCount;
        int submenuFontCounter =
            SUBMENU_FONT_TEXT_BASE + cycle * SUBMENU_FONT_TEXT_WIDTH;

        action = DebugTools_GetRegisteredAction(DEBUGTOOLS_BUILTIN_ACTION_MAX);
        CHECK(action != NULL && action->onSelected == Contributor0Selected,
              "the first contributor row must expose the real submenu callback");
        CHECK(gDebugToolsLifecycleLastMenuDef->menuItems[1].onSelected
              == Contributor0Selected,
              "the rendered contributor row must retain the registered callback");
        gDebugToolsLifecycleLastMenuProc->itemCurrent = 1;
        DispatchMenuKey(A_BUTTON);
        CHECK(DebugToolsLifecycle_GetTextCounter() == hubCounter,
              "ordinary A dispatch must not rewind live hub Text before deferred handoff");
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

        Menu_OnInit(gDebugToolsLifecycleLastMenuProc);
        CHECK(sFakeSubmenuInitCount == cycle + 1,
              "the real Menu_OnInit sequence must run for every contributor submenu");
        CHECK(gActiveFont == &sFakeSubmenuFont,
              "the contributor submenu must be allowed to switch the active font");
        CHECK(sFakeSubmenuFont.chr_counter
              == submenuFontCounter + SUBMENU_FONT_TEXT_WIDTH,
              "the contributor font must retain its own onInit allocation");

        DispatchMenuKey(B_BUTTON);
        CHECK(DebugToolsLifecycle_GetTextCounter()
              == textBase + (DEBUGTOOLS_MENU_WIDTH_TILES - 1),
              "the public submenu onEnd callback must not rewind its live Text row");
        CHECK(DebugTools_IsHubActive() != 0,
              "ending a contributor submenu must retain session ownership until return");

        DebugToolsLifecycle_RunPendingTransition();
        CHECK(gDebugToolsLifecycleLastMenuDef == &gDebugToolsHubMenuDef,
              "the deferred submenu transition must reopen the hub");
        CHECK(gActiveFont == sessionFont,
              "returning from a font-switching submenu must restore the session font");
        CHECK(sFakeSubmenuFont.chr_counter
              == submenuFontCounter + SUBMENU_FONT_TEXT_WIDTH,
              "rewinding menu rows must not overwrite the contributor font counter");
        InitText(&statusText, DEBUGTOOLS_STATUS_TEXT_WIDTH_TILES);
        CHECK(DebugToolsLifecycle_GetTextCounter()
              == textBase + DEBUGTOOLS_HUB_TEXT_ALLOC_BUDGET,
              "every reopened maximum CJK hub must return to the same bounded peak");

        hubCounter = DebugToolsLifecycle_GetTextCounter();
        DispatchMenuKey(R_BUTTON);
        CHECK(DebugToolsLifecycle_GetTextCounter() == hubCounter,
              "page-two onEnd must not rewind its live Text rows");
        DebugToolsLifecycle_RunPendingTransition();
        InitText(&statusText, DEBUGTOOLS_STATUS_TEXT_WIDTH_TILES);
        CHECK(gDebugToolsLifecycleLastMenuItemCount
              == 2,
              "page three must contain the final contributor plus Back");
        CHECK(DebugToolsLifecycle_GetTextCounter()
              == textBase + 2 * (DEBUGTOOLS_MENU_WIDTH_TILES - 1)
                 + DEBUGTOOLS_STATUS_TEXT_WIDTH_TILES,
              "page three must use only its bounded live-row allocation");

        hubCounter = DebugToolsLifecycle_GetTextCounter();
        DispatchMenuKey(R_BUTTON);
        CHECK(DebugToolsLifecycle_GetTextCounter() == hubCounter,
              "page-three onEnd must not rewind its live Text rows");
        DebugToolsLifecycle_RunPendingTransition();
        CHECK(gDebugToolsLifecycleLastMenuItemCount
              == DEBUGTOOLS_HUB_PAGE_ACTION_MAX + 1,
              "state diagnostics page must stay bounded");
        CHECK(DebugToolsLifecycle_GetTextCounter()
              == textBase + (DEBUGTOOLS_HUB_PAGE_ACTION_MAX + 1)
                 * (DEBUGTOOLS_MENU_WIDTH_TILES - 1),
              "state diagnostics page must use only its ten row allocations");

        hubCounter = DebugToolsLifecycle_GetTextCounter();
        DispatchMenuKey(R_BUTTON);
        CHECK(DebugToolsLifecycle_GetTextCounter() == hubCounter,
              "state diagnostics onEnd must not rewind live Text rows");
        DebugToolsLifecycle_RunPendingTransition();
        CHECK(gDebugToolsLifecycleLastMenuItemCount
              == DEBUGTOOLS_HUB_PAGE_ACTION_MAX + 1,
              "engine diagnostics page must stay bounded");

        hubCounter = DebugToolsLifecycle_GetTextCounter();
        DispatchMenuKey(R_BUTTON);
        CHECK(DebugToolsLifecycle_GetTextCounter() == hubCounter,
              "engine diagnostics onEnd must not rewind live Text rows");
        DebugToolsLifecycle_RunPendingTransition();
        CHECK(gDebugToolsLifecycleLastMenuItemCount
              == DEBUGTOOLS_HUB_PAGE_ACTION_MAX + 1,
              "page one must stay bounded after repeated cycles");
        CHECK(DebugToolsLifecycle_GetTextCounter()
              == textBase + (DEBUGTOOLS_HUB_PAGE_ACTION_MAX + 1)
                 * (DEBUGTOOLS_MENU_WIDTH_TILES - 1),
              "page one must use only its bounded live-row allocation");

        hubCounter = DebugToolsLifecycle_GetTextCounter();
        DispatchMenuKey(R_BUTTON);
        CHECK(DebugToolsLifecycle_GetTextCounter() == hubCounter,
              "page-one onEnd must not rewind its live Text rows");
        DebugToolsLifecycle_RunPendingTransition();
        InitText(&statusText, DEBUGTOOLS_STATUS_TEXT_WIDTH_TILES);
        CHECK(gDebugToolsLifecycleLastMenuItemCount
              == DEBUGTOOLS_HUB_PAGE_ACTION_MAX + 1,
              "page two must stay bounded after repeated cycles");
        CHECK(DebugToolsLifecycle_GetTextCounter()
              == textBase + DEBUGTOOLS_HUB_TEXT_ALLOC_BUDGET,
              "page two must reuse the same allocation scope");
    }

    CHECK(gDebugToolsLifecycleTransitionProcCount == 1 + 64 * 7,
          "action, music, diagnostics, and submenu views must schedule exact deferred transitions");
    CHECK(sFakeSubmenuFont.chr_counter
          == SUBMENU_FONT_TEXT_BASE + 64 * SUBMENU_FONT_TEXT_WIDTH,
          "repeated public submenu cycles must preserve every unrelated font allocation");

    DispatchMenuKey(B_BUTTON);
    CHECK(DebugToolsLifecycle_GetTextCounter()
          == textBase + DEBUGTOOLS_HUB_TEXT_ALLOC_BUDGET,
          "final hub cleanup must also defer rewind until live Text rows end");
    DebugToolsLifecycle_RunPendingTransition();
    CHECK(DebugToolsLifecycle_GetTextCounter() == textBase,
          "final cleanup must restore the allocator to its pre-debug base");
    CHECK(DebugTools_IsHubActive() == 0,
          "the session guard must clear only after deferred final cleanup");

    {
        int transitionsBefore;
        int endsBefore;

        DebugToolsLifecycle_SetTextCounter(textBase);
        CHECK(DebugTools_OpenHub() == DEBUGTOOLS_OK,
              "restoration fixture must open a fresh session");
        DebugTools_QueueSubmenuTransition(
            gDebugToolsLifecycleLastMenuProc,
            &sFakeSubmenuDef);
        EndMenu(gDebugToolsLifecycleLastMenuProc);
        DebugToolsLifecycle_RunPendingTransition();
        CHECK(gDebugToolsLifecycleLastMenuDef == &sFakeSubmenuDef,
              "restoration fixture must enter the real contributor submenu");

        transitionsBefore = gDebugToolsLifecycleTransitionProcCount;
        endsBefore = gDebugToolsLifecycleEndMenuCount;
        DebugToolsLifecycle_SetDiagnosticsRestoring(TRUE);
        DebugTools_EndSessionAfterMenuEnd(gDebugToolsLifecycleLastMenuProc);
        CHECK(gDebugToolsLifecycleTransitionProcCount == transitionsBefore,
              "restoring diagnostics must not schedule cleanup");
        CHECK(gDebugToolsLifecycleEndMenuCount == endsBefore,
              "restoring diagnostics must preserve the live submenu owner");
        CHECK(DebugTools_IsHubActive() != 0,
              "restoring diagnostics must retain session ownership");

        DebugToolsLifecycle_SetDiagnosticsRestoring(FALSE);
        DebugTools_EndSessionAfterMenuEnd(gDebugToolsLifecycleLastMenuProc);
        CHECK(gDebugToolsLifecycleTransitionProcCount == transitionsBefore + 1,
              "post-restoration end-session must schedule one cleanup");
        DebugToolsLifecycle_RunPendingTransition();
        CHECK(DebugTools_IsHubActive() == 0,
              "post-restoration cleanup must release the session");
    }

    {
        int startsBefore = gDebugToolsLifecycleStartMenuCount;
        int transitionsBefore = gDebugToolsLifecycleTransitionProcCount;

        DebugTools_QueueSubmenuTransition(gDebugToolsLifecycleLastMenuProc, &sFakeSubmenuDef);
        DebugTools_ReturnToHubAfterMenuEnd(gDebugToolsLifecycleLastMenuProc);
        CHECK(gDebugToolsLifecycleTransitionProcCount == transitionsBefore,
              "public submenu lifecycle calls outside a debug session must be no-ops");

        DebugToolsLifecycle_SetTextCounter(
            DEBUGTOOLS_TEXT_ALLOC_CAPACITY - DEBUGTOOLS_HUB_TEXT_ALLOC_BUDGET + 1);
        CHECK(DebugTools_OpenHub() == DEBUGTOOLS_ERR_TEXT_CAPACITY,
              "opening must fail explicitly when one maximum hub would exceed capacity");
        CHECK(gDebugToolsLifecycleStartMenuCount == startsBefore,
              "a text-capacity failure must not start a menu");
        CHECK(DebugTools_IsHubActive() == 0,
              "a text-capacity failure must not leave the session guard active");
    }

    {
        int transitionsBefore;

        DebugToolsLifecycle_SetTextCounter(textBase);
        CHECK(DebugTools_OpenHub() == DEBUGTOOLS_OK,
              "forced-cleanup fixture must open one final session");
        transitionsBefore = gDebugToolsLifecycleTransitionProcCount;
        DebugTools_ForceSessionCleanup();
        CHECK(DebugTools_IsHubActive() == 0,
              "forced cleanup must synchronously release the session guard");
        gDebugToolsHubMenuDef.onEnd(gDebugToolsLifecycleLastMenuProc);
        CHECK(gDebugToolsLifecycleTransitionProcCount == transitionsBefore,
              "a forced hub teardown must not queue a cleanup/reopen transition");
        DebugTools_ForceSessionCleanup();
        CHECK(DebugTools_IsHubActive() == 0,
              "forced cleanup must remain idempotent");
    }

    printf("DEBUGTOOLS_LIFECYCLE_HOST_TEST: PASS\n");
    return 0;
}
