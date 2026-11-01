#include "global.h"

#include "expansion_locale.h"
#include "cgtext.h"
#include "fontgrp.h"
#include "helpbox.h"
#include "localized_font.h"

extern struct Font gDefaultFont;
extern struct Font * gActiveFont;
void DialogBoxGetGlyphLen(const char * str, u8 * length);
void SetBoxDialogueSize(struct HelpBoxProc * proc, int width, int height);
void ApplyBoxDialoguePosition(struct HelpBoxProc * proc, int x, int y);

static EWRAM_DATA struct Glyph sSystemGlyph;
static EWRAM_DATA struct Glyph sTalkGlyph;
EWRAM_DATA struct Glyph * TextGlyphs_System[128];
EWRAM_DATA struct Glyph * TextGlyphs_Talk[128];

const u8 gLocalizedFontJaSystemCodepoints[] = {0, 0x4E, 0, 0};
const u8 gLocalizedFontJaSystemWidths[] = {13};
const u8 gLocalizedFontJaSystemBitmaps[LOCALIZED_FONT_BITMAP_STRIDE] = {0};
const u32 gLocalizedFontJaSystemGlyphCount = 1;
const u8 gLocalizedFontJaTalkCodepoints[] = {0, 0x4E, 0, 0};
const u8 gLocalizedFontJaTalkWidths[] = {7};
const u8 gLocalizedFontJaTalkBitmaps[LOCALIZED_FONT_BITMAP_STRIDE] = {0};
const u32 gLocalizedFontJaTalkGlyphCount = 1;

struct NativeGeometryProbe
{
    u32 phase;
    u32 systemAdvance;
    u32 talkAdvance;
    u32 systemBoxWidth;
    u32 systemBoxHeight;
    u32 talkBoxWidth;
    u32 talkBoxHeight;
    u32 staticTalkWidth;
    u32 dialogueGlyphLength;
    u32 boxWaitWidth;
    u32 boxCapacity;
    u32 dialogueUsesTalk;
    u32 cgWidth;
    u32 cgHeight;
    u32 cgBoxWidth;
    u32 cgBoxHeight;
    u32 cgWaitWidth;
    u32 cgUsesTalk;
};

volatile struct NativeGeometryProbe gNativeGeometryProbe;

ExpansionLocaleId ExpansionLocale_GetCurrent(void)
{
    return EXPANSION_LOCALE_JA;
}

void AgbMain(void)
{
    struct HelpBoxProc box = {0};
    int width;
    int height;
    u32 advance;
    u8 length;
    u8 cgWidth;
    u8 cgHeight;
    unsigned int i;
    const char * stream = "AA\x02" "AA";

    sSystemGlyph.width = 3;
    sTalkGlyph.width = 7;
    for (i = 0; i < 128; ++i)
    {
        TextGlyphs_System[i] = &sSystemGlyph;
        TextGlyphs_Talk[i] = &sTalkGlyph;
    }

    gDefaultFont.lang = 1;
    SetTextFont(&gDefaultFont);
    SetTextFontGlyphs(TEXT_GLYPHS_SYSTEM);
    GetCharTextLen("A", &advance);
    gNativeGeometryProbe.systemAdvance = advance;
    GetBoxDialogueSize(stream, &width, &height);
    gNativeGeometryProbe.systemBoxWidth = width;
    gNativeGeometryProbe.systemBoxHeight = height;

    DialogBoxGetGlyphLen(stream, &length);
    gNativeGeometryProbe.dialogueGlyphLength = length;
    gNativeGeometryProbe.dialogueUsesTalk = gActiveFont->glyphs == TextGlyphs_Talk;
    GetCharTextLen("A", &advance);
    gNativeGeometryProbe.talkAdvance = advance;
    GetBoxDialogueSize(stream, &width, &height);
    gNativeGeometryProbe.talkBoxWidth = width;
    gNativeGeometryProbe.talkBoxHeight = height;
    gNativeGeometryProbe.staticTalkWidth = GetStringTextLen(stream);
    GetBoxDialogueSize("AA\x03", &width, &height);
    gNativeGeometryProbe.boxWaitWidth = width;

    SetDialogueBoxConfig(0);
    SetBoxDialogueSize(&box, 248, 16);
    ApplyBoxDialoguePosition(&box, 0, 0);
    gNativeGeometryProbe.boxCapacity = box.wBoxFinal;

    SetTextFontGlyphs(TEXT_GLYPHS_SYSTEM);
    cgWidth = 0;
    cgHeight = 0;
    GetCgTextDimensions(stream, &cgWidth, &cgHeight);
    gNativeGeometryProbe.cgWidth = cgWidth;
    gNativeGeometryProbe.cgHeight = cgHeight;
    gNativeGeometryProbe.cgUsesTalk = gActiveFont->glyphs == TextGlyphs_Talk;
    GetCgTextBoxDimensions(stream, &width, &height);
    gNativeGeometryProbe.cgBoxWidth = width;
    gNativeGeometryProbe.cgBoxHeight = height;
    GetCgTextBoxDimensions("AA\x03", &width, &height);
    gNativeGeometryProbe.cgWaitWidth = width;
    gNativeGeometryProbe.phase = 1;
    for (;;)
        ;
}
