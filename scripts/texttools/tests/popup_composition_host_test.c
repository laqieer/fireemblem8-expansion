#include "global.h"

#include <stdio.h>
#include <string.h>

#include "fontgrp.h"
#include "popup.h"

void GeneratePopupText(const struct PopupInstruction *inst, struct Text th);
void NewPopup2_PlanD(ProcPtr parent, int item, int msg0, int msg1);

extern struct PopupInstruction PopupScr_GotItem[];
extern struct PopupInstruction PopupScr_StoleItem[];

static int sFailures;
static int sLocale;
static char sComposition[128];

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
    return (char *)LocalizedMessage(id);
}

char *GetItemNameWithArticle(int item, int capital)
{
    (void)item;
    (void)capital;
    return (char *)LocalizedItem();
}

char *GetItemName(int item)
{
    (void)item;
    return (char *)LocalizedItem();
}

int GetItemIconId(int item)
{
    (void)item;
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
    return parent;
}

static void CheckPopupInstructionComposition(
    const struct PopupInstruction *script,
    const char *expected)
{
    struct Text text;

    ResetComposition();
    InitText(&text, 20);
    GeneratePopupText(script, text);
    CHECK(strcmp(sComposition, expected) == 0);
}

static void CheckPopup2Composition(int prefix, int suffix, const char *expected)
{
    ResetComposition();
    NewPopup2_PlanD(NULL, 1, prefix, suffix);
    CHECK(strcmp(sComposition, expected) == 0);
}

static void TestLocale(
    int locale,
    const char *got,
    const char *stole,
    const char *dropped,
    const char *sent)
{
    sLocale = locale;
    SetPopupItem(1);
    CheckPopupInstructionComposition(PopupScr_GotItem, got);
    CheckPopupInstructionComposition(PopupScr_StoleItem, stole);
    CheckPopup2Composition(0x00F, 0x022, dropped);
    CheckPopup2Composition(0x010, 0x011, sent);
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
