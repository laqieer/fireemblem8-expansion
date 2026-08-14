#include <stdio.h>
#include <string.h>

#include "global.h"
#include "fontgrp.h"
#include "hardware.h"
#include "sio.h"

struct Text gUnk_Sio_7[1];
struct Font Font_0;
u16 gBG0TilemapBuffer[32 * 32];

static const char * sCurrentLabel;
static char sSharedMessage[64];
static char sDrawnLabel[64];
static char sDrawnSuffix[16];
static int sDrawnLabelLength;

static const char * NextUtf8(const char * text)
{
    unsigned char first = (unsigned char)*text;

    if ((first & 0x80) == 0)
        return text + 1;
    if ((first & 0xE0) == 0xC0)
        return text + 2;
    if ((first & 0xF0) == 0xE0)
        return text + 3;
    return text + 4;
}

static void CopyCString(char * dest, int capacity, const char * src)
{
    int i = 0;

    while (i + 1 < capacity && src[i] != '\0')
    {
        dest[i] = src[i];
        i++;
    }
    dest[i] = '\0';
}

char * GetStringFromIndex(int index)
{
    const char * source = "";

    if (index == 0x77E)
        source = sCurrentLabel;
    else if (index == 0x5AE)
        source = "%";

    CopyCString(sSharedMessage, (int)sizeof(sSharedMessage), source);
    return sSharedMessage;
}

void ClearText(struct Text * text)
{
    (void)text;
    sDrawnLabel[0] = '\0';
    sDrawnSuffix[0] = '\0';
    sDrawnLabelLength = 0;
}

int GetStringTextLen(const char * text)
{
    int width = 0;

    while (*text != '\0')
    {
        text = NextUtf8(text);
        width += 8;
    }
    return width;
}

const char * GetCharTextLen(const char * text, u32 * width)
{
    *width = 8;
    return NextUtf8(text);
}

const char * Text_DrawCharacter(struct Text * text, const char * source)
{
    const char * next = NextUtf8(source);
    int byteCount = next - source;

    (void)text;
    if (sDrawnLabelLength + byteCount >= (int)sizeof(sDrawnLabel))
        return source;
    while (source < next)
        sDrawnLabel[sDrawnLabelLength++] = *source++;
    sDrawnLabel[sDrawnLabelLength] = '\0';
    return next;
}

void Text_InsertDrawString(
    struct Text * text,
    int x,
    int color,
    const char * source)
{
    (void)text;
    (void)x;
    (void)color;
    CopyCString(sDrawnSuffix, (int)sizeof(sDrawnSuffix), source);
}

void Text_SetCursor(struct Text * text, int x)
{
    text->x = x;
}

void Text_SetColor(struct Text * text, int color)
{
    text->colorId = color;
}

void Text_DrawNumber(struct Text * text, int number)
{
    (void)text;
    (void)number;
}

void UnpackUiBarPalette(int palette)
{
    (void)palette;
}

void DrawUiFrame2(int x, int y, int width, int height, int style)
{
    (void)x;
    (void)y;
    (void)width;
    (void)height;
    (void)style;
}

void SetTextFont(struct Font * font)
{
    (void)font;
}

void InitSystemTextFont(void)
{
}

void PutText(struct Text * text, u16 * destination)
{
    (void)text;
    (void)destination;
}

void DrawStatBarGfx(
    int tile,
    int bufferWidth,
    u16 * buffer,
    int tileBase,
    int barWidth,
    int progressLength,
    int cappedLength)
{
    (void)tile;
    (void)bufferWidth;
    (void)buffer;
    (void)tileBase;
    (void)barWidth;
    (void)progressLength;
    (void)cappedLength;
}

void BG_EnableSyncByMask(int mask)
{
    (void)mask;
}

int main(void)
{
    static const struct
    {
        const char * locale;
        const char * label;
    } cases[] =
    {
        {"en", "Connecting"},
        {"ja", "通信中"},
        {"zh-Hans", "连接中"},
        {"qps-ploc", "Connecting"},
    };
    int i;

    for (i = 0; i < (int)(sizeof(cases) / sizeof(cases[0])); i++)
    {
        sCurrentLabel = cases[i].label;
        gUnk_Sio_7[0].tile_width = 16;
        XMapTransfer_4();

        if (strcmp(sDrawnLabel, cases[i].label) != 0)
        {
            fprintf(
                stderr,
                "%s label mismatch: expected %s, got %s\n",
                cases[i].locale,
                cases[i].label,
                sDrawnLabel);
            return 1;
        }
        if (strcmp(sDrawnSuffix, "%") != 0)
        {
            fprintf(
                stderr,
                "%s suffix mismatch: expected %%, got %s\n",
                cases[i].locale,
                sDrawnSuffix);
            return 1;
        }
    }

    puts("SIO_XMAP_TRANSFER_SHARED_BUFFER_TEST: PASS");
    return 0;
}
