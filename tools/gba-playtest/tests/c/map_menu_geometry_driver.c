#include "global.h"

#include <stdio.h>
#include <string.h>

#include "bmmenu.h"
#include "bmguide.h"
#include "hardware.h"
#include "uimenu.h"
#include "worldmap.h"

#define CHECK(condition) \
    do \
    { \
        if (!(condition)) \
        { \
            printf("FAIL:%d:%s\n", __LINE__, #condition); \
            return 1; \
        } \
    } while (0)

static struct MenuProc sMenu;
static struct MenuItemProc sItems[MENU_ITEM_MAX];
static int sMapKind;
static bool sGuideLocked;
struct PlaySt gPlaySt;

u32 GetBattleMapKind(void)
{
    return sMapKind;
}

bool IsGuideLocked(void)
{
    return sGuideLocked;
}

bool CheckFlag(int flag)
{
    (void)flag;
    return FALSE;
}

static int IsVisible(u8 availability)
{
    return availability != MENU_NOTSHOWN;
}

static int GetVisibleCount(int optionalCount)
{
    int count = optionalCount + 4;

    count += IsVisible(MapMenu_IsStatusCommandAvailable());
    count += IsVisible(MapMenu_IsGuideCommandAvailable(NULL, 0));
    count += IsVisible(MapMenu_IsRecordsCommandAvailable(NULL, 0));
    count += IsVisible(MapMenu_IsRetreatCommandAvailable(NULL, 0));
    return count;
}

static struct MenuProc *ConfigureMenu(int rowCount)
{
    int i;
    int yTileInner;

    memset(&sMenu, 0, sizeof(sMenu));
    memset(sItems, 0, sizeof(sItems));
    sMenu.rect.x = 1;
    sMenu.rect.y = 2;
    sMenu.rect.w = 7;
    sMenu.itemCount = rowCount;
    yTileInner = sMenu.rect.y + 1;

    for (i = 0; i < rowCount; ++i)
    {
        sMenu.menuItems[i] = &sItems[i];
        sItems[i].xTile = sMenu.rect.x + 1;
        sItems[i].yTile = yTileInner;
        yTileInner += 2;
    }

    sMenu.rect.h = yTileInner + 1 - sMenu.rect.y;
    return &sMenu;
}

static int CheckVisibleFramebuffer(const struct MenuProc *menu)
{
    int i;

    CHECK(menu->rect.y >= 0);
    CHECK(menu->rect.y + menu->rect.h <= DISPLAY_HEIGHT / 8);

    for (i = 0; i < menu->itemCount; ++i)
    {
        CHECK(menu->menuItems[i]->yTile >= 0);
        CHECK(menu->menuItems[i]->yTile + 1 < DISPLAY_HEIGHT / 8);
    }

    return 0;
}

int main(void)
{
    struct MenuProc *menu;

    sMapKind = BATTLEMAP_KIND_SKIRMISH;
    sGuideLocked = FALSE;
    CHECK(MapMenu_IsStatusCommandAvailable() == MENU_ENABLED);
    CHECK(MapMenu_IsGuideCommandAvailable(NULL, 0) == MENU_ENABLED);
    CHECK(MapMenu_IsRecordsCommandAvailable(NULL, 0) == MENU_NOTSHOWN);
    CHECK(MapMenu_IsRetreatCommandAvailable(NULL, 0) == MENU_ENABLED);
    CHECK(GetVisibleCount(2) == 9);
    menu = ConfigureMenu(9);
    ExpansionMapMenu_EnsureVerticalBounds(menu);
    CHECK(menu->rect.y == 0);
    CHECK(menu->rect.h == 20);
    CHECK(menu->menuItems[8]->yTile == 17);
    CHECK(CheckVisibleFramebuffer(menu) == 0);

    sMapKind = BATTLEMAP_KIND_DUNGEON;
    gPlaySt.chapterIndex = 0x2E;
    CHECK(MapMenu_IsStatusCommandAvailable() == MENU_NOTSHOWN);
    CHECK(MapMenu_IsGuideCommandAvailable(NULL, 0) == MENU_ENABLED);
    CHECK(MapMenu_IsRecordsCommandAvailable(NULL, 0) == MENU_ENABLED);
    CHECK(MapMenu_IsRetreatCommandAvailable(NULL, 0) == MENU_ENABLED);
    CHECK(GetVisibleCount(2) == 9);
    menu = ConfigureMenu(9);
    ExpansionMapMenu_EnsureVerticalBounds(menu);
    CHECK(menu->rect.y == 0);
    CHECK(menu->rect.h == 20);
    CHECK(menu->menuItems[8]->yTile == 17);
    CHECK(CheckVisibleFramebuffer(menu) == 0);

    sMapKind = BATTLEMAP_KIND_SKIRMISH;
    sGuideLocked = TRUE;
    CHECK(GetVisibleCount(2) == 8);
    menu = ConfigureMenu(8);
    ExpansionMapMenu_EnsureVerticalBounds(menu);
    CHECK(menu->rect.y == 2);
    CHECK(menu->rect.h == 18);
    CHECK(menu->menuItems[7]->yTile == 17);
    CHECK(CheckVisibleFramebuffer(menu) == 0);

    sMapKind = BATTLEMAP_KIND_STORY;
    sGuideLocked = FALSE;
    CHECK(GetVisibleCount(2) == 8);
    menu = ConfigureMenu(8);
    ExpansionMapMenu_EnsureVerticalBounds(menu);
    CHECK(menu->rect.y == 2);
    CHECK(menu->menuItems[7]->yTile == 17);
    CHECK(CheckVisibleFramebuffer(menu) == 0);

    sGuideLocked = TRUE;
    CHECK(GetVisibleCount(0) == 5);
    menu = ConfigureMenu(5);
    ExpansionMapMenu_EnsureVerticalBounds(menu);
    CHECK(menu->rect.y == 2);
    CHECK(menu->menuItems[4]->yTile == 11);
    CHECK(CheckVisibleFramebuffer(menu) == 0);

    puts("MAP_MENU_GEOMETRY: PASS");
    return 0;
}
