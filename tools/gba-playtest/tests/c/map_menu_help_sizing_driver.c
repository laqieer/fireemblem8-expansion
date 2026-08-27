#include "global.h"

#include <stdio.h>
#include <string.h>

#include "fontgrp.h"

extern struct Font gDefaultFont;
extern struct Font *gActiveFont;

static struct Glyph sGlyph;
static struct Glyph *sGlyphs[256];
static char sStaleMessage[] = "WWWW\001WW";

#define CHECK(condition) \
    do \
    { \
        if (!(condition)) \
        { \
            printf("FAIL:%d:%s\n", __LINE__, #condition); \
            return 1; \
        } \
    } while (0)

char *StringInsertSpecialPrefixByCtrl(void)
{
    return sStaleMessage;
}

static void InitAsciiFont(void)
{
    int index;

    memset(&gDefaultFont, 0, sizeof(gDefaultFont));
    memset(&sGlyph, 0, sizeof(sGlyph));
    sGlyph.width = 4;
    for (index = 0; index < 256; index++)
        sGlyphs[index] = &sGlyph;
    gDefaultFont.glyphs = sGlyphs;
    gDefaultFont.lang = 1;
    gActiveFont = &gDefaultFont;
}

static int CheckBox(const char *text, int expectedWidth, int expectedHeight)
{
    int width = -1;
    int height = -1;

#ifdef MODERN
    GetStringTextBoxFromString(text, &width, &height);
#else
    (void)text;
    GetStringTextBox("ignored", &width, &height);
#endif

    if (width != expectedWidth || height != expectedHeight)
    {
        printf(
            "FAIL: box width=%d height=%d expected=%d,%d\n",
            width,
            height,
            expectedWidth,
            expectedHeight);
        return 1;
    }
    return 0;
}

int main(void)
{
    InitAsciiFont();

#ifdef MODERN
    CHECK(CheckBox("", 0, 0) == 0);
    CHECK(CheckBox("A", 4, 16) == 0);
    CHECK(CheckBox("AAAA\001BB", 16, 32) == 0);
    CHECK(CheckBox("\001AA", 8, 32) == 0);
    CHECK(CheckBox("AA\001\001B", 8, 48) == 0);
    CHECK(CheckBox("AA\001", 8, 32) == 0);
    CHECK(CheckBox("\001", 0, 32) == 0);
    CHECK(CheckBox("A\001B\001C", 4, 48) == 0);

    strcpy(sStaleMessage, "WWWW\001WW");
    CHECK(CheckBox("A", 4, 16) == 0);
    puts("MAP_MENU_HELP_SIZING_MODERN: PASS");
#else
    CHECK(CheckBox("ignored", 16, 32) == 0);
    puts("MAP_MENU_HELP_SIZING_ARCHIVAL: PASS");
#endif
    return 0;
}
