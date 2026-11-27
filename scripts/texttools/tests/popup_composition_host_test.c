#include "global.h"

#include <stdio.h>
#include <string.h>

#include "fontgrp.h"
#include "popup.h"

void GeneratePopupText(const struct PopupInstruction *inst, struct Text th);

extern struct PopupInstruction PopupScr_GotItem[];
extern struct PopupInstruction PopupScr_StoleItem[];

enum { TEST_POPUP_ITEM = 0x123 };

static int sFailures;
static int sLocale;
static char sComposition[128];
static int sMessageIds[2];
static unsigned int sMessageCount;
static int sItemId;
static int sItemCapital;
static int sIconItem;
static ProcPtr sPopupParent;

u16 gBG0TilemapBuffer[32 * 32];

#define CHECK(condition) do { \
    if (!(condition)) { \
        printf("FAIL: %s:%d: %s\n", __FILE__, __LINE__, #condition); \
        sFailures++; \
    } \
} while (0)

static const char *LocalizedMessage(int id)
{
    if (sLocale == 0)
    {
        switch (id)
        {
            case 0x008:
                return "入手：";
            case 0x00A:
                return "盗品：";
            case 0x00F:
                return "廃棄：";
            case 0x010:
                return "輸送隊へ送付：";
            case 0x011:
            case 0x022:
                return "。";
        }
    }
    else
    {
        switch (id)
        {
            case 0x008:
                return "获得：";
            case 0x00A:
                return "盗取：";
            case 0x00F:
                return "丢弃：";
            case 0x010:
                return "送往运输队：";
            case 0x011:
            case 0x022:
                return "。";
        }
    }

    return "";
}

static const char *LocalizedItem(void)
{
    return sLocale == 0 ? "鉄の剣" : "铁剑";
}

static void ResetComposition(void)
{
    sComposition[0] = '\0';
    sMessageCount = 0;
    sItemId = -1;
    sItemCapital = -1;
    sIconItem = -1;
    sPopupParent = NULL;
}

static void AppendComposition(const char *text)
{
    strncat(
        sComposition,
        text,
        sizeof(sComposition) - strlen(sComposition) - 1);
}

char *GetStringFromIndex(int id)
{
    if (sMessageCount < ARRAY_COUNT(sMessageIds))
        sMessageIds[sMessageCount] = id;
    sMessageCount++;
    return (char *)LocalizedMessage(id);
}

char *GetItemNameWithArticle(int item, int capital)
{
    sItemId = item;
    sItemCapital = capital;
    return (char *)LocalizedItem();
}

char *GetItemName(int item)
{
    sItemId = item;
    sItemCapital = -1;
    return (char *)LocalizedItem();
}

int GetItemIconId(int item)
{
    sIconItem = item;
    return 0;
}

int GetStringTextLen(const char *text)
{
    return strlen(text);
}

int NumberToStringAscii(int number, char *buffer)
{
    return sprintf(buffer, "%d", number);
}

void ResetTextFont(void)
{
}

void InitText(struct Text *text, int tileWidth)
{
    memset(text, 0, sizeof(*text));
    text->tile_width = tileWidth;
}

void Text_SetColor(struct Text *text, int color)
{
    text->colorId = color;
}

void Text_SetCursor(struct Text *text, int cursor)
{
    text->x = cursor;
}

int Text_GetCursor(struct Text *text)
{
    return text->x;
}

void Text_Skip(struct Text *text, int amount)
{
    text->x += amount;
}

void Text_DrawString(struct Text *text, const char *string)
{
    AppendComposition(string);
    text->x += strlen(string);
}

void DrawUiFrame2(int x, int y, int width, int height, int style)
{
    (void)x;
    (void)y;
    (void)width;
    (void)height;
    (void)style;
}

void DrawIcon(u16 *tilemap, int icon, int oam2)
{
    (void)tilemap;
    (void)icon;
    (void)oam2;
}

void PutText(struct Text *text, u16 *tilemap)
{
    (void)text;
    (void)tilemap;
}

void BG_EnableSyncByMask(int mask)
{
    (void)mask;
}

ProcPtr Proc_StartBlocking(const struct ProcCmd *script, ProcPtr parent)
{
    (void)script;
    sPopupParent = parent;
    return parent;
}

static void CheckComposition(int prefix, int suffix, const char *expected)
{
    CHECK(strcmp(sComposition, expected) == 0);
    CHECK(sMessageCount == 2);
    if (sMessageCount == 2)
    {
        CHECK(sMessageIds[0] == prefix);
        CHECK(sMessageIds[1] == suffix);
    }
    CHECK(sItemId == TEST_POPUP_ITEM);
    CHECK(sItemCapital == 0);
}

static void CheckPopupInstructionComposition(
    const struct PopupInstruction *script,
    int prefix,
    int suffix,
    const char *expected)
{
    struct Text text;

    ResetComposition();
    InitText(&text, 20);
    GeneratePopupText(script, text);
    CheckComposition(prefix, suffix, expected);
}

static void CheckPopup2Composition(
    void (*show)(ProcPtr, int),
    int prefix,
    int suffix,
    const char *expected)
{
    int parent = 0;

    ResetComposition();
    show(&parent, TEST_POPUP_ITEM);
    CheckComposition(prefix, suffix, expected);
    CHECK(sIconItem == TEST_POPUP_ITEM);
    CHECK(sPopupParent == &parent);
}

static void TestLocale(
    int locale,
    const char *got,
    const char *stole,
    const char *dropped,
    const char *sent)
{
    sLocale = locale;
    SetPopupItem(TEST_POPUP_ITEM);
    CheckPopupInstructionComposition(PopupScr_GotItem, 0x008, 0x022, got);
    CheckPopupInstructionComposition(PopupScr_StoleItem, 0x00A, 0x022, stole);
    CheckPopup2Composition(NewPopup2_DropItem, 0x00F, 0x022, dropped);
    CheckPopup2Composition(NewPopup2_SendItem, 0x010, 0x011, sent);
}

int main(void)
{
    TestLocale(
        0,
        "入手：鉄の剣。",
        "盗品：鉄の剣。",
        "廃棄：鉄の剣。",
        "輸送隊へ送付：鉄の剣。");
    TestLocale(
        1,
        "获得：铁剑。",
        "盗取：铁剑。",
        "丢弃：铁剑。",
        "送往运输队：铁剑。");

    if (sFailures == 0)
    {
        puts("popup_composition_host_test: ok");
        return 0;
    }

    printf("%d failure(s)\n", sFailures);
    return 1;
}
