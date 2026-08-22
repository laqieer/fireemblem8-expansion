#ifndef GUARD_LOCALIZED_UI_GRAPHICS_H
#define GUARD_LOCALIZED_UI_GRAPHICS_H

#include "expansion_locale.h"

#define LOCALIZED_UI_GRAPHICS_CHAPTER_TITLE_COUNT 88

#if defined(MODERN) && ((FE8_EXPANSION_ENABLED_LOCALE_MASK & 0x7Eu) != 0)
#define LOCALIZED_UI_GRAPHICS_CJK_ENABLED 1
#else
#define LOCALIZED_UI_GRAPHICS_CJK_ENABLED 0
#endif

#if defined(MODERN) && ((FE8_EXPANSION_ENABLED_LOCALE_MASK & 0x78u) != 0)
#define LOCALIZED_UI_GRAPHICS_EU_ENABLED 1
#else
#define LOCALIZED_UI_GRAPHICS_EU_ENABLED 0
#endif

struct LocalizedUiGraphicsTitle
{
    const u8 *logo;
    const u8 *labels;
};

struct LocalizedUiGraphicsTitleSprites
{
    const u16 *logo;
    const u16 *extra;
    const u16 *subtitle;
    const u16 *banner;
    const u16 *copyright;
    const u16 *pressStart;
};

struct LocalizedUiGraphicsSubtitleSlide
{
    const u8 *gfx;
    const u8 *tsa;
    int timer;
};

struct LocalizedUiGraphicsChapterTitle
{
    const u8 *save;
    const u8 *introLeft;
    const u8 *introRight;
};

#if LOCALIZED_UI_GRAPHICS_CJK_ENABLED
const struct LocalizedUiGraphicsTitle *LocalizedUiGraphics_GetTitle(void);
const u8 *LocalizedUiGraphics_GetSaveMenuOptions(void);
const u8 *LocalizedUiGraphics_GetSaveMenuMainSprites(void);
const u8 *LocalizedUiGraphics_GetDifficultyMenuObjects(void);
const struct LocalizedUiGraphicsSubtitleSlide *LocalizedUiGraphics_GetSubtitleSlides(void);
const struct LocalizedUiGraphicsChapterTitle *LocalizedUiGraphics_GetChapterTitle(u32 titleId);
const u8 *LocalizedUiGraphics_GetChapterTitleFrame(void);
const u8 *LocalizedUiGraphics_GetChapterTitleTsa(void);
const struct LocalizedUiGraphicsTitleSprites *LocalizedUiGraphics_GetTitleSprites(void);
#endif

#if LOCALIZED_UI_GRAPHICS_EU_ENABLED
const void *LocalizedEuUiGraphics_RemapCompressed(const void *source);
const void *LocalizedEuUiGraphics_RemapRaw(const void *source);
const void *LocalizedEuUiGraphics_RemapAp(const void *source);
const struct LocalizedUiGraphicsSubtitleSlide *
    LocalizedEuUiGraphics_GetSubtitleSlides(void);
const struct LocalizedUiGraphicsChapterTitle *
    LocalizedEuUiGraphics_GetChapterTitle(u32 titleId);
#endif

#endif /* GUARD_LOCALIZED_UI_GRAPHICS_H */
