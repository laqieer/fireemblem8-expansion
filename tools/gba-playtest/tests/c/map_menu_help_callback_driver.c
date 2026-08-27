#include "global.h"

#include <stdio.h>
#include <string.h>

#include "bmmenu.h"
#include "expansion_locale.h"
#include "hardware.h"
#include "statscreen.h"
#include "uimenu.h"

#define CHECK(condition) \
    do \
    { \
        if (!(condition)) \
        { \
            printf("FAIL:%d:%s\n", __LINE__, #condition); \
            return 1; \
        } \
    } while (0)

static const char sPersistentHelp[] = "Localized help";
static u16 sBgMap[0x400];
static ExpansionMsgId sPersistentId;
static const char *sStartedString;
static int sStartedX;
static int sStartedY;
static int sStringHelpCount;
static int sVanillaHelpCount;
static struct MenuProc *sVanillaMenu;
static struct MenuItemProc *sVanillaItem;

const char *ExpansionLocale_ResolveCurrentPersistent(ExpansionMsgId message)
{
    sPersistentId = message;
    return sPersistentHelp;
}

const char *ExpansionLocale_ResolveCurrent(ExpansionMsgId message)
{
    (void)message;
    return "";
}

void StartHelpBoxString(int x, int y, const char *string)
{
    sStartedX = x;
    sStartedY = y;
    sStartedString = string;
    sStringHelpCount++;
}

u8 MenuStdHelpBox(struct MenuProc *menu, struct MenuItemProc *item)
{
    sVanillaMenu = menu;
    sVanillaItem = item;
    sVanillaHelpCount++;
    return 0x5A;
}

u16 *BG_GetMapBuffer(int bg)
{
    (void)bg;
    return sBgMap;
}

void Text_SetCursor(struct Text *text, int cursor)
{
    (void)text;
    (void)cursor;
}

void Text_SetColor(struct Text *text, int color)
{
    (void)text;
    (void)color;
}

void Text_DrawString(struct Text *text, const char *string)
{
    (void)text;
    (void)string;
}

void PutText(struct Text *text, u16 *destination)
{
    (void)text;
    (void)destination;
}

int main(void)
{
    struct MenuProc menu;
    struct MenuItemProc expansion;
    struct MenuItemProc vanilla;
    struct MenuItemDef expansionDef;
    struct MenuItemDef vanillaDef;

    memset(&menu, 0, sizeof(menu));
    memset(&expansion, 0, sizeof(expansion));
    memset(&vanilla, 0, sizeof(vanilla));
    memset(&expansionDef, 0, sizeof(expansionDef));
    memset(&vanillaDef, 0, sizeof(vanillaDef));

    expansion.def = &expansionDef;
    expansion.xTile = 5;
    expansion.yTile = 7;
    expansionDef.helpMsgId = 81;
    expansionDef.onDraw = ExpansionMapMenuItem_Draw;

    CHECK(ExpansionMapMenuItem_HelpBox(&menu, &expansion) == 0);
    CHECK(sPersistentId == 81);
    CHECK(sStringHelpCount == 1);
    CHECK(sStartedX == 40);
    CHECK(sStartedY == 56);
    CHECK(sStartedString == sPersistentHelp);
    CHECK(sVanillaHelpCount == 0);

    vanilla.def = &vanillaDef;
    vanilla.xTile = 2;
    vanilla.yTile = 3;
    vanillaDef.helpMsgId = 0x6E0;
    vanillaDef.onDraw = NULL;

    CHECK(ExpansionMapMenuItem_HelpBox(&menu, &vanilla) == 0x5A);
    CHECK(sPersistentId == 81);
    CHECK(sStringHelpCount == 1);
    CHECK(sVanillaHelpCount == 1);
    CHECK(sVanillaMenu == &menu);
    CHECK(sVanillaItem == &vanilla);

    puts("MAP_MENU_HELP_CALLBACK: PASS");
    return 0;
}
