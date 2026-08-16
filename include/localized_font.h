#ifndef GUARD_LOCALIZED_FONT_H
#define GUARD_LOCALIZED_FONT_H

/*
 * Compact localized font lookup for modern non-English game profiles.
 * Include global.h before this header.
 */
#if defined(MODERN) && ((FE8_EXPANSION_ENABLED_LOCALE_MASK & 0x7Eu) != 0)

#define FE8_LOCALIZED_FONT_ENABLED 1

#include "expansion_locale.h"

#define LOCALIZED_FONT_BITMAP_STRIDE 64u

enum LocalizedFontStyle
{
    LOCALIZED_FONT_STYLE_SYSTEM = 0,
    LOCALIZED_FONT_STYLE_TALK = 1
};

struct LocalizedFontGlyph
{
    const u8 *bitmap;
    u32 scalar;
    u8 width;
};

#if (FE8_EXPANSION_ENABLED_LOCALE_MASK & 0x02u) != 0
extern const u8 gLocalizedFontJaSystemCodepoints[];
extern const u8 gLocalizedFontJaSystemWidths[];
extern const u8 gLocalizedFontJaSystemBitmaps[];
extern const u8 gLocalizedFontJaTalkCodepoints[];
extern const u8 gLocalizedFontJaTalkWidths[];
extern const u8 gLocalizedFontJaTalkBitmaps[];
extern const u32 gLocalizedFontJaSystemGlyphCount;
extern const u32 gLocalizedFontJaTalkGlyphCount;
#endif

#if (FE8_EXPANSION_ENABLED_LOCALE_MASK & 0x04u) != 0
extern const u8 gLocalizedFontZhHansSystemCodepoints[];
extern const u8 gLocalizedFontZhHansSystemWidths[];
extern const u8 gLocalizedFontZhHansSystemBitmaps[];
extern const u8 gLocalizedFontZhHansTalkCodepoints[];
extern const u8 gLocalizedFontZhHansTalkWidths[];
extern const u8 gLocalizedFontZhHansTalkBitmaps[];
extern const u32 gLocalizedFontZhHansSystemGlyphCount;
extern const u32 gLocalizedFontZhHansTalkGlyphCount;
#endif

#if (FE8_EXPANSION_ENABLED_LOCALE_MASK & 0x78u) != 0
extern const u8 gLocalizedFontEuSystemCodepoints[];
extern const u8 gLocalizedFontEuSystemWidths[];
extern const u8 gLocalizedFontEuSystemBitmaps[];
extern const u8 gLocalizedFontEuTalkCodepoints[];
extern const u8 gLocalizedFontEuTalkWidths[];
extern const u8 gLocalizedFontEuTalkBitmaps[];
extern const u32 gLocalizedFontEuSystemGlyphCount;
extern const u32 gLocalizedFontEuTalkGlyphCount;
#endif

bool8 LocalizedFont_IsLocale(ExpansionLocaleId locale);

/*
 * Returns TRUE for an embedded glyph or the explicit U+3000 spacing glyph.
 * Spacing glyphs have a NULL bitmap and a nonzero width. ASCII intentionally
 * returns FALSE because it remains owned by the existing engine font.
 */
bool8 LocalizedFont_Lookup(
    ExpansionLocaleId locale,
    enum LocalizedFontStyle style,
    u32 scalar,
    struct LocalizedFontGlyph *out);

/*
 * The renderer calls this when it visibly substitutes '?'. The counter
 * saturates rather than wrapping so a malformed string cannot erase evidence.
 */
void LocalizedFont_RecordMissing(u32 scalar);
void LocalizedFont_ResetDiagnostics(void);
u16 LocalizedFont_GetMissingGlyphCount(void);
u32 LocalizedFont_GetLastMissingScalar(void);

/* Shared overflow predicate used after the renderer obtains the real glyph
 * advance. A zero cursor never wraps, preventing an unbreakable first glyph
 * from becoming an infinite virtual-newline loop. */
bool8 LocalizedFont_ShouldWrap(u32 cursor, u32 advance, u32 allocationPixels);

#endif /* modern build with a localized game locale */

#endif /* GUARD_LOCALIZED_FONT_H */
