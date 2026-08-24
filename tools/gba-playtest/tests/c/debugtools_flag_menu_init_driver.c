#include <stdio.h>
#include <string.h>

#include "global.h"
#include "uimenu.h"
#include "expansion_debugtools.h"
#include "expansion_locale.h"

#define CHECK(cond, msg) \
    do { \
        if (!(cond)) { \
            fprintf(stderr, "DEBUGTOOLS_FLAG_MENU_INIT_HOST_TEST: FAIL: %s\n", msg); \
            return 1; \
        } \
    } while (0)

extern struct MenuDef CONST_DATA gDebugToolsFlagMenuDef;
extern char gDebugToolsToolsHostStubStatusLines[3][64];
extern int gDebugToolsToolsHostStubStatusLineCount;
extern int gDebugToolsToolsHostStubPutDrawTextCallCount;
extern void DebugToolsHostStub_ResetStatusLines(void);
extern void DebugToolsHostStub_SetLocale(ExpansionLocaleId locale);

static int CheckRenderedStatus(int expectedCjkDrawCalls)
{
    CHECK(gDebugToolsToolsHostStubStatusLineCount == 3,
          "configured Flag menu initializer must render three status lines");
    CHECK(strcmp(gDebugToolsToolsHostStubStatusLines[0], "TURN 17 C:2 F:1") == 0,
          "configured Flag menu initializer must render turn/chapter/flag status");
    CHECK(strcmp(gDebugToolsToolsHostStubStatusLines[1], "R:CPU") == 0,
          "configured Flag menu initializer must render red mode status");
    CHECK(strcmp(gDebugToolsToolsHostStubStatusLines[2], "G:CPU") == 0,
          "configured Flag menu initializer must render green mode status");
    CHECK(gDebugToolsToolsHostStubPutDrawTextCallCount == expectedCjkDrawCalls,
          "configured Flag menu initializer must use the expected locale renderer");
    return 0;
}

int main(void)
{
    struct MenuProc menu;

    memset(&gDebugToolsProbe, 0, sizeof(gDebugToolsProbe));
    memset(&menu, 0, sizeof(menu));
    menu.def = &gDebugToolsFlagMenuDef;
    gPlaySt.chapterTurnNumber = 17;
    gDebugToolsProbe.chapterIndexSample = 2;
    gDebugToolsProbe.debugFlagLastValue = 1;

    CHECK(gDebugToolsFlagMenuDef.onInit != NULL,
          "Flag/Chapter MenuDef must configure an initializer");

    DebugToolsHostStub_SetLocale(EXPANSION_LOCALE_EN);
    DebugToolsHostStub_ResetStatusLines();
    gDebugToolsFlagMenuDef.onInit(&menu);
    if (CheckRenderedStatus(0) != 0)
        return 1;

    DebugToolsHostStub_SetLocale(EXPANSION_LOCALE_JA);
    DebugToolsHostStub_ResetStatusLines();
    gDebugToolsFlagMenuDef.onInit(&menu);
    if (CheckRenderedStatus(3) != 0)
        return 1;

    puts("DEBUGTOOLS_FLAG_MENU_INIT_HOST_TEST: PASS");
    return 0;
}
