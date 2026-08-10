#include <stdio.h>
#include <string.h>

#include "global.h"
#include "fontgrp.h"
#include "uimenu.h"
#include "expansion_debugtools.h"

#ifndef DEBUGTOOLS_INITIALIZER_FIRST
#error "DEBUGTOOLS_INITIALIZER_FIRST must select one public initializer (1-4)"
#endif

#define CHECK(cond, msg) \
    do { \
        if (!(cond)) { \
            fprintf(stderr, "DEBUGTOOLS_INITIALIZER_ORDER_HOST_TEST: FAIL: %s\n", msg); \
            return 1; \
        } \
    } while (0)

extern int gDebugToolsLifecycleLastMenuItemCount;
extern const struct MenuDef* gDebugToolsLifecycleLastMenuDef;
extern void DebugToolsLifecycle_SetTextCounter(u16 value);
extern struct MenuDef CONST_DATA gDebugToolsHubMenuDef;
extern u8 DebugToolsLifecycle_Builtin1Selected(struct MenuProc*, struct MenuItemProc*);
extern u8 DebugToolsLifecycle_Builtin2Selected(struct MenuProc*, struct MenuItemProc*);
extern u8 DebugToolsLifecycle_Builtin3Selected(struct MenuProc*, struct MenuItemProc*);
extern u8 DebugToolsLifecycle_Builtin4Selected(struct MenuProc*, struct MenuItemProc*);
extern u8 DebugToolsLifecycle_Builtin5Selected(struct MenuProc*, struct MenuItemProc*);
extern u8 DebugToolsLifecycle_Builtin6Selected(struct MenuProc*, struct MenuItemProc*);
extern u8 DebugToolsLifecycle_Builtin7Selected(struct MenuProc*, struct MenuItemProc*);
extern u8 DebugToolsLifecycle_Builtin8Selected(struct MenuProc*, struct MenuItemProc*);
extern u8 DebugToolsLifecycle_Builtin9Selected(struct MenuProc*, struct MenuItemProc*);

static u8 ContributorSelected(struct MenuProc* menu, struct MenuItemProc* item)
{
    (void)menu;
    (void)item;
    return 0;
}

static void CallInitializer(int group)
{
    switch (group)
    {
    case 1:
        DebugTools_RegisterBuiltinActions();
        break;

    case 2:
        DebugTools_RegisterWeatherFogActions();
        break;

    case 3:
        DebugTools_RegisterChapter4PrepAction();
        break;

    case 4:
        DebugTools_RegisterExtendedToolActions();
        break;
    }
}

int main(void)
{
    static const u16 expectedIds[9] = {1, 2, 3, 4, 5, 6, 7, 8, 9};
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
    static const u16 firstGroupIds[4][5] =
    {
        {1, 0, 0, 0, 0},
        {2, 3, 0, 0, 0},
        {4, 0, 0, 0, 0},
        {5, 6, 7, 8, 9}
    };
    static const int firstGroupCounts[4] = {1, 2, 1, 5};
    struct DebugToolsAction contributor;
    const struct DebugToolsAction* action;
    int first = DEBUGTOOLS_INITIALIZER_FIRST;
    int group;
    int i;

    CHECK(first >= 1 && first <= 4,
          "the test must select a valid public initializer");
    CHECK(DebugTools_GetRegisteredCount() == 0,
          "the registry must start empty");

    CallInitializer(first);
    CHECK(DebugTools_GetRegisteredCount() == firstGroupCounts[first - 1],
          "the first public initializer must register exactly its own group");

    for (i = 0; i < firstGroupCounts[first - 1]; ++i)
    {
        action = DebugTools_GetRegisteredAction(i);
        CHECK(action != NULL,
              "sparse built-in introspection must return every initialized action");
        CHECK(action->id == firstGroupIds[first - 1][i],
              "sparse built-in introspection must be ascending by stable ID");
        CHECK(action->onSelected == expectedCallbacks[action->id - 1],
              "sparse built-in introspection must retain callback identity");
    }

    for (group = 4; group >= 1; --group)
    {
        if (group != first)
            CallInitializer(group);
    }

    CHECK(DebugTools_GetRegisteredCount() == DEBUGTOOLS_BUILTIN_ACTION_MAX,
          "all public initializer orders must produce nine built-ins");

    for (i = 0; i < DEBUGTOOLS_BUILTIN_ACTION_MAX; ++i)
    {
        action = DebugTools_GetRegisteredAction(i);
        CHECK(action != NULL,
              "every fully initialized built-in row must exist");
        CHECK(action->id == expectedIds[i],
              "full built-in introspection must remain in stable ID order 1-9");
        CHECK(strcmp(action->label, expectedLabels[i]) == 0,
              "each stable built-in ID must retain its matching label");
        CHECK(action->onSelected == expectedCallbacks[i],
              "each stable built-in ID must retain its matching callback");
    }

    CallInitializer(first);
    CHECK(DebugTools_GetRegisteredCount() == DEBUGTOOLS_BUILTIN_ACTION_MAX,
          "repeating the first public initializer must remain idempotent");
    CHECK(DebugTools_GetLastRegistrationResult() == DEBUGTOOLS_OK,
          "repeating a public initializer must be a successful no-op");

    contributor.id = DEBUGTOOLS_CONTRIBUTOR_ID_MIN;
    contributor.label = "Contributor";
    contributor.onSelected = ContributorSelected;
    CHECK(DebugTools_RegisterAction(&contributor) == DEBUGTOOLS_OK,
          "a contributor must remain usable after arbitrary built-in initialization");
    action = DebugTools_GetRegisteredAction(DEBUGTOOLS_BUILTIN_ACTION_MAX);
    CHECK(action != NULL && action->id == contributor.id,
          "contributors must remain separately appended after all built-ins");

    DebugToolsLifecycle_SetTextCounter(17);
    CHECK(DebugTools_OpenHub() == DEBUGTOOLS_OK,
          "the hub must open after arbitrary public initializer order");
    CHECK(gDebugToolsLifecycleLastMenuDef == &gDebugToolsHubMenuDef,
          "the real hub menu must be rendered");
    CHECK(gDebugToolsLifecycleLastMenuItemCount
          == DEBUGTOOLS_HUB_PAGE_ACTION_MAX + 1,
          "the first page must still contain nine built-ins plus Back");
    CHECK(strcmp(gDebugToolsHubMenuDef.menuItems[1].name, "Weather") == 0,
          "Weather must remain at hub row index 1");
    CHECK(strcmp(gDebugToolsHubMenuDef.menuItems[2].name, "Fog") == 0,
          "Fog must remain at hub row index 2");
    CHECK(gDebugToolsHubMenuDef.menuItems[1].onSelected == expectedCallbacks[1],
          "Weather must retain its callback at hub row index 1");
    CHECK(gDebugToolsHubMenuDef.menuItems[2].onSelected == expectedCallbacks[2],
          "Fog must retain its callback at hub row index 2");

    printf(
        "DEBUGTOOLS_INITIALIZER_ORDER_HOST_TEST: PASS first=%d\n",
        first);
    return 0;
}
