#include <stdio.h>
#include <string.h>

#include "global.h"
#include "debugtools_internal.h"
#include "gamecontrol.h"
#include "hardware.h"
#include "uimenu.h"

#include "constants/worldmap.h"

extern struct KeyStatusBuffer gDebugToolsSelectorTestKeyStatus;
extern const struct DebugToolsAction* gDebugToolsSelectorCapturedAction;
extern const struct MenuDef* gDebugToolsSelectorCapturedMenuDef;
extern int gDebugToolsSelectorReturnHubCount;
extern int gDebugToolsSelectorEndSessionCount;
extern int gDebugToolsSelectorProcStartCount;
extern int gDebugToolsSelectorProcBreakCount;
extern int gDebugToolsSelectorEndBMapCount;
extern int gDebugToolsSelectorSetNextAction;
extern int gDebugToolsSelectorProcGotoLabel;
extern ProcPtr gDebugToolsSelectorLastProc;
extern int gDebugToolsSelectorEnumerationCount;

extern void DebugToolsSelectorHostStub_Init(void);
extern void DebugToolsSelectorHostStub_SetHubActive(int active);
extern void DebugToolsSelectorHostStub_SetMapActive(int active);

#define CHECK(condition, message) \
    do { \
        if (!(condition)) { \
            fprintf(stderr, "DEBUGTOOLS_SELECTOR_HOST_TEST: FAIL: %s\n", message); \
            return 1; \
        } \
    } while (0)

static struct MenuProc sMenu;
static struct MenuItemProc sItem;

static int OpenSelector(void)
{
    u8 result;

    result = gDebugToolsSelectorCapturedAction->onSelected(&sMenu, &sItem);
    CHECK(
        result & MENU_ACT_END,
        "selector action must end the hub through the deferred submenu handoff");
    CHECK(
        gDebugToolsSelectorCapturedMenuDef != NULL,
        "selector action must queue its submenu");
    if (gDebugToolsSelectorCapturedMenuDef->onInit != NULL)
        gDebugToolsSelectorCapturedMenuDef->onInit(&sMenu);
    return 0;
}

int main(void)
{
    struct DebugToolsLaunchTarget target;
    struct DebugToolsLaunchRequest request;
    const struct MenuItemDef* itemDef;
    int returnHubBefore;

    DebugToolsSelectorHostStub_Init();
    CHECK(!DebugTools_IsTargetLaunchPending(),
        "unselected selector must not queue a default launch");
    CHECK(!DebugTools_ConsumePendingTargetLaunch(&request),
        "unselected selector must not be consumable");
    DebugTools_RegisterChapterSelectorAction();

    CHECK(gDebugToolsSelectorCapturedAction != NULL, "selector action must register");
    CHECK(gDebugToolsSelectorCapturedAction->id == 4, "selector must retain stable built-in ID 4");
    CHECK(
        strcmp(gDebugToolsSelectorCapturedAction->label, "Chapter/Skirmish") == 0,
        "selector action label mismatch");

    CHECK(
        DebugTools_GetLaunchTargetCount() == 7,
        "expected seven metadata-derived fixture targets");
    CHECK(DebugTools_GetLaunchTarget(-1, &target) == 0, "negative target index must fail closed");
    CHECK(
        DebugTools_GetLaunchTarget(7, &target) == 0,
        "out-of-range target index must fail closed");
    CHECK(DebugTools_GetLaunchTarget(0, NULL) == 0, "NULL target output must fail closed");

    CHECK(DebugTools_GetLaunchTarget(0, &target), "missing Chapter 2 target");
    CHECK(target.id == 0x1102, "Chapter 2 stable target ID mismatch");
    CHECK(target.kind == DEBUGTOOLS_LAUNCH_TARGET_CHAPTER, "Chapter 2 target kind mismatch");
    CHECK(target.chapterId == 2, "Chapter 2 target metadata mismatch");

    CHECK(DebugTools_GetLaunchTarget(1, &target), "missing Chapter 4 target");
    CHECK(target.id == 0x1104, "Chapter 4 stable target ID mismatch");
    CHECK(DebugTools_GetLaunchTarget(2, &target), "missing Chapter 4 skirmish target");
    CHECK(target.id == 0x2104, "Chapter 4 skirmish stable target ID mismatch");
    CHECK(target.kind == DEBUGTOOLS_LAUNCH_TARGET_SKIRMISH, "skirmish target kind mismatch");

    gDebugToolsSelectorEnumerationCount = 0;
    CHECK(
        DebugTools_RequestTargetLaunch(0xFFFF) == DEBUGTOOLS_LAUNCH_REQUEST_INVALID,
        "malformed target identity must be rejected as invalid");
    CHECK(
        DebugTools_RequestTargetLaunch(0x1103) == DEBUGTOOLS_LAUNCH_REQUEST_UNAVAILABLE,
        "well-formed absent target must be rejected as unavailable");

    CHECK(OpenSelector() == 0, "opening selector failed");
    CHECK(DebugTools_GetSelectedTargetId() == 0x1104, "default must be Chapter 4");

    itemDef = &gDebugToolsSelectorCapturedMenuDef->menuItems[0];
    CHECK(
        itemDef->onSelected(&sMenu, &sItem) & MENU_ACT_END,
        "valid selection must end the selector");
    CHECK(DebugTools_IsTargetLaunchPending(), "valid selection must queue one typed request");
    CHECK(
        DebugTools_RequestTargetLaunch(0x1104) == DEBUGTOOLS_LAUNCH_REQUEST_BUSY,
        "duplicate request must be rejected as busy");
    gDebugToolsSelectorCapturedMenuDef->onEnd(&sMenu);
    CHECK(
        gDebugToolsSelectorEndSessionCount == 1,
        "successful selection must end the debug session");
    CHECK(gDebugToolsSelectorReturnHubCount == 0, "successful selection must not reopen the hub");

    CHECK(!DebugTools_ConsumePendingTargetLaunch(NULL), "NULL consume output must fail closed");
    CHECK(DebugTools_IsTargetLaunchPending(), "NULL consume must preserve the pending request");
    CHECK(DebugTools_ConsumePendingTargetLaunch(&request), "GameControl consume must succeed once");
    CHECK(request.targetId == 0x1104, "consumed request target mismatch");
    CHECK(
        request.origin == DEBUGTOOLS_LAUNCH_REQUEST_ORIGIN_DIRECT,
        "ordinary selector confirmation must retain direct provenance");
    CHECK(
        request.chapterId == 4 && request.nodeId == NODE_ZAHA_WOODS,
        "consumed chapter route mismatch");
    CHECK(
        gDebugToolsSelectorEnumerationCount == 0,
        "direct request, draw, and consume must not enumerate targets");
    CHECK(!DebugTools_ConsumePendingTargetLaunch(&request), "duplicate consume must be a no-op");

    returnHubBefore = gDebugToolsSelectorReturnHubCount;
    CHECK(OpenSelector() == 0, "reopening selector for cancel failed");
    gDebugToolsSelectorCapturedMenuDef->onEnd(&sMenu);
    CHECK(
        gDebugToolsSelectorReturnHubCount == returnHubBefore + 1,
        "cancel/forced teardown without a request must return to the hub");
    CHECK(!DebugTools_IsTargetLaunchPending(), "cancel must not queue a request");

    CHECK(OpenSelector() == 0, "reopening selector for skirmish failed");
    itemDef = &gDebugToolsSelectorCapturedMenuDef->menuItems[0];
    gDebugToolsSelectorEnumerationCount = 0;
    gDebugToolsSelectorTestKeyStatus.newKeys = DPAD_RIGHT;
    itemDef->onIdle(&sMenu, &sItem);
    gDebugToolsSelectorTestKeyStatus.newKeys = 0;
    CHECK(
        gDebugToolsSelectorEnumerationCount == 2,
        "left/right navigation may scan once and resolve the destination once");
    CHECK(DebugTools_GetSelectedTargetId() == 0x2104, "RIGHT must select adjacent Ch4 skirmish");
    CHECK(
        itemDef->onSelected(&sMenu, &sItem) & MENU_ACT_END,
        "skirmish selection must close submenu");
    gDebugToolsSelectorCapturedMenuDef->onEnd(&sMenu);

    DebugToolsSelectorHostStub_SetHubActive(1);
    CHECK(DebugTools_QueueMapLaunchHandoff(), "active session must retain pending map request");
    CHECK(gDebugToolsSelectorProcStartCount == 0, "handoff must not start before session cleanup");
    DebugToolsSelectorHostStub_SetHubActive(0);
    DebugToolsSelectorHostStub_SetMapActive(1);
    CHECK(DebugTools_QueueMapLaunchHandoff(), "cleaned-up map request must schedule handoff");
    CHECK(gDebugToolsSelectorProcStartCount == 1, "exactly one handoff proc must start");
    CHECK(DebugTools_QueueMapLaunchHandoff(), "duplicate handoff scheduling must be a no-op");
    CHECK(
        gDebugToolsSelectorProcStartCount == 1,
        "duplicate schedule must not start a second proc");

    DebugToolsSelector_RunMapHandoff(gDebugToolsSelectorLastProc);
    CHECK(gDebugToolsSelectorEndBMapCount == 1, "handoff must end BMap exactly once");
    CHECK(
        gDebugToolsSelectorSetNextAction == GAME_ACTION_EVENT_RETURN,
        "handoff next action mismatch");
    CHECK(
        gDebugToolsSelectorProcGotoLabel == LGAMECTRL_POST_TITLE_IDLE,
        "handoff must route the existing GameControl through PostIntro");
    CHECK(gDebugToolsSelectorProcBreakCount == 1, "handoff proc must break after success");
    DebugToolsSelector_MapHandoffOnEnd(gDebugToolsSelectorLastProc);

    CHECK(DebugTools_ConsumePendingTargetLaunch(&request), "skirmish request must consume once");
    CHECK(request.kind == DEBUGTOOLS_LAUNCH_TARGET_SKIRMISH, "consumed skirmish kind mismatch");
    CHECK(request.chapterId == 4, "consumed skirmish chapter mismatch");
    CHECK(!DebugTools_IsTargetLaunchPending(), "request must clear after consume");

    CHECK(OpenSelector() == 0, "reopening selector for compatibility route failed");
    itemDef = &gDebugToolsSelectorCapturedMenuDef->menuItems[0];
    gDebugToolsSelectorTestKeyStatus.newKeys = L_BUTTON;
    CHECK(
        itemDef->onSelected(&sMenu, &sItem) & MENU_ACT_END,
        "compatibility selection must close submenu");
    gDebugToolsSelectorTestKeyStatus.newKeys = 0;
    gDebugToolsSelectorCapturedMenuDef->onEnd(&sMenu);
    CHECK(
        DebugTools_ConsumePendingTargetLaunch(&request),
        "compatibility request must consume once");
    CHECK(
        request.origin == DEBUGTOOLS_LAUNCH_REQUEST_ORIGIN_CH4_PREP_COMPAT,
        "L+A selector confirmation must retain compatibility provenance");

    CHECK(
        DebugTools_RequestTargetLaunch(0x1104) == DEBUGTOOLS_LAUNCH_REQUEST_OK,
        "timeout probe request must arm");
    DebugToolsSelectorHostStub_SetMapActive(1);
    CHECK(DebugTools_QueueMapLaunchHandoff(), "timeout probe handoff must schedule");
    DebugToolsSelectorHostStub_SetMapActive(0);
    {
        int i;

        for (i = 0; i < 60; ++i)
            DebugToolsSelector_RunMapHandoff(gDebugToolsSelectorLastProc);
    }
    CHECK(!DebugTools_IsTargetLaunchPending(), "lost-owner timeout must cancel the request");
    DebugToolsSelector_MapHandoffOnEnd(gDebugToolsSelectorLastProc);

    puts("DEBUGTOOLS_SELECTOR_HOST_TEST: PASS");
    return 0;
}
