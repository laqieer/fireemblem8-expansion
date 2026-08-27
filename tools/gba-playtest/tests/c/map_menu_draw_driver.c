#include "global.h"

#include <stdio.h>
#include <string.h>

#include "bmmenu.h"
#include "expansion_locale.h"
#include "hardware.h"
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

static u16 sBgMaps[4][0x400];
static struct Text *sCursorTexts[2];
static int sCursorValues[2];
static struct Text *sDrawTexts[2];
static const char *sDrawStrings[2];
static struct Text *sPutTexts[2];
static u16 *sPutDestinations[2];
static ExpansionMsgId sResolvedIds[2];
static int sCursorCount;
static int sDrawCount;
static int sPutCount;
static int sResolveCount;
static int sBgIndex = -1;

u16 *BG_GetMapBuffer(int bg)
{
    sBgIndex = bg;
    return sBgMaps[bg];
}

void Text_SetCursor(struct Text *text, int cursor)
{
    sCursorTexts[sCursorCount] = text;
    sCursorValues[sCursorCount] = cursor;
    sCursorCount++;
}

void Text_SetColor(struct Text *text, int color)
{
    (void)text;
    (void)color;
}

void Text_DrawString(struct Text *text, const char *string)
{
    sDrawTexts[sDrawCount] = text;
    sDrawStrings[sDrawCount] = string;
    sDrawCount++;
}

void PutText(struct Text *text, u16 *destination)
{
    sPutTexts[sPutCount] = text;
    sPutDestinations[sPutCount] = destination;
    sPutCount++;
}

const char *ExpansionLocale_ResolveCurrent(ExpansionMsgId message)
{
    static const char sDanger[] = "Danger";
    static const char sCharge[] = "Charge";

    sResolvedIds[sResolveCount++] = message;
    return message == 144 ? sDanger : sCharge;
}

int main(void)
{
    struct MenuProc menu;
    struct MenuItemProc danger;
    struct MenuItemProc charge;
    struct MenuItemDef dangerDef;
    struct MenuItemDef chargeDef;

    memset(&menu, 0, sizeof(menu));
    memset(&danger, 0, sizeof(danger));
    memset(&charge, 0, sizeof(charge));
    memset(&dangerDef, 0, sizeof(dangerDef));
    memset(&chargeDef, 0, sizeof(chargeDef));

    menu.frontBg = 2;
    danger.def = &dangerDef;
    danger.availability = MENU_ENABLED;
    danger.xTile = 3;
    danger.yTile = 4;
    dangerDef.nameMsgId = 144;

    charge.def = &chargeDef;
    charge.availability = MENU_ENABLED;
    charge.xTile = 3;
    charge.yTile = 6;
    chargeDef.nameMsgId = 80;

    CHECK(ExpansionMapMenuItem_Draw(&menu, &danger) == 0);
    CHECK(ExpansionMapMenuItem_Draw(&menu, &charge) == 0);
    CHECK(sResolveCount == 2);
    CHECK(sResolvedIds[0] == 144);
    CHECK(sResolvedIds[1] == 80);
    CHECK(sCursorCount == 2);
    CHECK(sCursorTexts[0] == &danger.text);
    CHECK(sCursorTexts[1] == &charge.text);
    CHECK(sCursorValues[0] == 8);
    CHECK(sCursorValues[1] == 8);
    CHECK(sDrawCount == 2);
    CHECK(sDrawTexts[0] == &danger.text);
    CHECK(sDrawTexts[1] == &charge.text);
    CHECK(strcmp(sDrawStrings[0], "Danger") == 0);
    CHECK(strcmp(sDrawStrings[1], "Charge") == 0);
    CHECK(sBgIndex == 2);
    CHECK(sPutCount == 2);
    CHECK(sPutTexts[0] == &danger.text);
    CHECK(sPutTexts[1] == &charge.text);
    CHECK(sPutDestinations[0] == sBgMaps[2] + TILEMAP_INDEX(3, 4));
    CHECK(sPutDestinations[1] == sBgMaps[2] + TILEMAP_INDEX(3, 6));
    CHECK(sPutDestinations[1] - sPutDestinations[0] == 2 * 32);

    puts("MAP_MENU_DRAW_CALLBACK: PASS");
    return 0;
}
