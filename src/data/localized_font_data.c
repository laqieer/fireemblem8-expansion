#include "global.h"

#include "localized_font.h"

#define LOCALIZED_FONT_DATA_STATIC_ASSERT(condition, tag) \
    typedef char localized_font_data_static_assert_##tag[(condition) ? 1 : -1]

#if defined(MODERN) && ((FE8_EXPANSION_ENABLED_LOCALE_MASK & 0x02u) != 0)

const u8 gLocalizedFontJaSystemCodepoints[]
    SECTION(".locale_data.font.ja.system.codepoints") __attribute__((aligned(4))) =
    INCBIN_U8("graphics/fonts/cjk/ja.system.codepoints.u32le");
const u8 gLocalizedFontJaSystemWidths[]
    SECTION(".locale_data.font.ja.system.widths") __attribute__((aligned(4))) =
    INCBIN_U8("graphics/fonts/cjk/ja.system.widths.u8");
const u8 gLocalizedFontJaSystemBitmaps[]
    SECTION(".locale_data.font.ja.system.bitmaps") __attribute__((aligned(4))) =
    INCBIN_U8("graphics/fonts/cjk/ja.system.glyphs.2bpp");

const u8 gLocalizedFontJaTalkCodepoints[]
    SECTION(".locale_data.font.ja.talk.codepoints") __attribute__((aligned(4))) =
    INCBIN_U8("graphics/fonts/cjk/ja.talk.codepoints.u32le");
const u8 gLocalizedFontJaTalkWidths[]
    SECTION(".locale_data.font.ja.talk.widths") __attribute__((aligned(4))) =
    INCBIN_U8("graphics/fonts/cjk/ja.talk.widths.u8");
const u8 gLocalizedFontJaTalkBitmaps[]
    SECTION(".locale_data.font.ja.talk.bitmaps") __attribute__((aligned(4))) =
    INCBIN_U8("graphics/fonts/cjk/ja.talk.glyphs.2bpp");

LOCALIZED_FONT_DATA_STATIC_ASSERT(
    sizeof(gLocalizedFontJaSystemCodepoints)
        == sizeof(gLocalizedFontJaSystemWidths) * sizeof(u32),
    ja_system_codepoint_count);
LOCALIZED_FONT_DATA_STATIC_ASSERT(
    sizeof(gLocalizedFontJaSystemBitmaps)
        == sizeof(gLocalizedFontJaSystemWidths) * LOCALIZED_FONT_BITMAP_STRIDE,
    ja_system_bitmap_count);
LOCALIZED_FONT_DATA_STATIC_ASSERT(
    sizeof(gLocalizedFontJaTalkCodepoints)
        == sizeof(gLocalizedFontJaTalkWidths) * sizeof(u32),
    ja_talk_codepoint_count);
LOCALIZED_FONT_DATA_STATIC_ASSERT(
    sizeof(gLocalizedFontJaTalkBitmaps)
        == sizeof(gLocalizedFontJaTalkWidths) * LOCALIZED_FONT_BITMAP_STRIDE,
    ja_talk_bitmap_count);
const u32 gLocalizedFontJaSystemGlyphCount =
    sizeof(gLocalizedFontJaSystemWidths) / sizeof(gLocalizedFontJaSystemWidths[0]);
const u32 gLocalizedFontJaTalkGlyphCount =
    sizeof(gLocalizedFontJaTalkWidths) / sizeof(gLocalizedFontJaTalkWidths[0]);

#endif

#if defined(MODERN) && ((FE8_EXPANSION_ENABLED_LOCALE_MASK & 0x78u) != 0)

const u8 gLocalizedFontEuSystemCodepoints[]
    SECTION(".locale_data.font.eu.system.codepoints") __attribute__((aligned(4))) =
    INCBIN_U8("graphics/fonts/eu/eu.system.codepoints.u32le");
const u8 gLocalizedFontEuSystemWidths[]
    SECTION(".locale_data.font.eu.system.widths") __attribute__((aligned(4))) =
    INCBIN_U8("graphics/fonts/eu/eu.system.widths.u8");
const u8 gLocalizedFontEuSystemBitmaps[]
    SECTION(".locale_data.font.eu.system.bitmaps") __attribute__((aligned(4))) =
    INCBIN_U8("graphics/fonts/eu/eu.system.glyphs.2bpp");

const u8 gLocalizedFontEuTalkCodepoints[]
    SECTION(".locale_data.font.eu.talk.codepoints") __attribute__((aligned(4))) =
    INCBIN_U8("graphics/fonts/eu/eu.talk.codepoints.u32le");
const u8 gLocalizedFontEuTalkWidths[]
    SECTION(".locale_data.font.eu.talk.widths") __attribute__((aligned(4))) =
    INCBIN_U8("graphics/fonts/eu/eu.talk.widths.u8");
const u8 gLocalizedFontEuTalkBitmaps[]
    SECTION(".locale_data.font.eu.talk.bitmaps") __attribute__((aligned(4))) =
    INCBIN_U8("graphics/fonts/eu/eu.talk.glyphs.2bpp");

LOCALIZED_FONT_DATA_STATIC_ASSERT(
    sizeof(gLocalizedFontEuSystemCodepoints)
        == sizeof(gLocalizedFontEuSystemWidths) * sizeof(u32),
    eu_system_codepoint_count);
LOCALIZED_FONT_DATA_STATIC_ASSERT(
    sizeof(gLocalizedFontEuSystemBitmaps)
        == sizeof(gLocalizedFontEuSystemWidths) * LOCALIZED_FONT_BITMAP_STRIDE,
    eu_system_bitmap_count);
LOCALIZED_FONT_DATA_STATIC_ASSERT(
    sizeof(gLocalizedFontEuTalkCodepoints)
        == sizeof(gLocalizedFontEuTalkWidths) * sizeof(u32),
    eu_talk_codepoint_count);
LOCALIZED_FONT_DATA_STATIC_ASSERT(
    sizeof(gLocalizedFontEuTalkBitmaps)
        == sizeof(gLocalizedFontEuTalkWidths) * LOCALIZED_FONT_BITMAP_STRIDE,
    eu_talk_bitmap_count);
const u32 gLocalizedFontEuSystemGlyphCount =
    sizeof(gLocalizedFontEuSystemWidths)
        / sizeof(gLocalizedFontEuSystemWidths[0]);
const u32 gLocalizedFontEuTalkGlyphCount =
    sizeof(gLocalizedFontEuTalkWidths)
        / sizeof(gLocalizedFontEuTalkWidths[0]);

#endif

#if defined(MODERN) && ((FE8_EXPANSION_ENABLED_LOCALE_MASK & 0x04u) != 0)

const u8 gLocalizedFontZhHansSystemCodepoints[]
    SECTION(".locale_data.font.zh_hans.system.codepoints") __attribute__((aligned(4))) =
    INCBIN_U8("graphics/fonts/cjk/zh-Hans.system.codepoints.u32le");
const u8 gLocalizedFontZhHansSystemWidths[]
    SECTION(".locale_data.font.zh_hans.system.widths") __attribute__((aligned(4))) =
    INCBIN_U8("graphics/fonts/cjk/zh-Hans.system.widths.u8");
const u8 gLocalizedFontZhHansSystemBitmaps[]
    SECTION(".locale_data.font.zh_hans.system.bitmaps") __attribute__((aligned(4))) =
    INCBIN_U8("graphics/fonts/cjk/zh-Hans.system.glyphs.2bpp");

const u8 gLocalizedFontZhHansTalkCodepoints[]
    SECTION(".locale_data.font.zh_hans.talk.codepoints") __attribute__((aligned(4))) =
    INCBIN_U8("graphics/fonts/cjk/zh-Hans.talk.codepoints.u32le");
const u8 gLocalizedFontZhHansTalkWidths[]
    SECTION(".locale_data.font.zh_hans.talk.widths") __attribute__((aligned(4))) =
    INCBIN_U8("graphics/fonts/cjk/zh-Hans.talk.widths.u8");
const u8 gLocalizedFontZhHansTalkBitmaps[]
    SECTION(".locale_data.font.zh_hans.talk.bitmaps") __attribute__((aligned(4))) =
    INCBIN_U8("graphics/fonts/cjk/zh-Hans.talk.glyphs.2bpp");

LOCALIZED_FONT_DATA_STATIC_ASSERT(
    sizeof(gLocalizedFontZhHansSystemCodepoints)
        == sizeof(gLocalizedFontZhHansSystemWidths) * sizeof(u32),
    zh_hans_system_codepoint_count);
LOCALIZED_FONT_DATA_STATIC_ASSERT(
    sizeof(gLocalizedFontZhHansSystemBitmaps)
        == sizeof(gLocalizedFontZhHansSystemWidths) * LOCALIZED_FONT_BITMAP_STRIDE,
    zh_hans_system_bitmap_count);
LOCALIZED_FONT_DATA_STATIC_ASSERT(
    sizeof(gLocalizedFontZhHansTalkCodepoints)
        == sizeof(gLocalizedFontZhHansTalkWidths) * sizeof(u32),
    zh_hans_talk_codepoint_count);
LOCALIZED_FONT_DATA_STATIC_ASSERT(
    sizeof(gLocalizedFontZhHansTalkBitmaps)
        == sizeof(gLocalizedFontZhHansTalkWidths) * LOCALIZED_FONT_BITMAP_STRIDE,
    zh_hans_talk_bitmap_count);
const u32 gLocalizedFontZhHansSystemGlyphCount =
    sizeof(gLocalizedFontZhHansSystemWidths)
        / sizeof(gLocalizedFontZhHansSystemWidths[0]);
const u32 gLocalizedFontZhHansTalkGlyphCount =
    sizeof(gLocalizedFontZhHansTalkWidths)
        / sizeof(gLocalizedFontZhHansTalkWidths[0]);

#endif
