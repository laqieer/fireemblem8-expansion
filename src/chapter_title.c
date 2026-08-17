#include "global.h"

#include "hardware.h"
#include "chap_title.h"
#include "chapterdata.h"
#include "bmlib.h"
#include "helpbox.h"
#include "localized_ui_graphics.h"
#include "worldmap.h"

EWRAM_DATA struct ChapterTitleFxSt gChapterTitleFxSt = { 0 };

void ApplyChapterTitlePal(int config, int palId)
{
    u16 * pal;
    pal = (config & 1)
        ? Pal_ChapterTitleAlt
        : Pal_ChapterTitleMain;

    if ((config & 0x80) == 0)
    {
        if ((config & 8) != 0)
        {
            pal = pal + 0xA0;
        }
        else
        {
            if ((config & 0x10) == 0)
            {
                if ((config & 0x20) != 0)
                    pal = pal + 0x20;
                if ((config & 0x40) != 0)
                    pal = pal + 0x40;
                if ((config & 4) != 0)
                    pal = pal + 0x40;
            }
        }
    }

    if ((config & 2) != 0) {
        pal = pal + 0x10;
    }

    ApplyPalette(pal, palId);
}

void PutChapterTitleGfx(int chr, u32 titleId)
{
#if LOCALIZED_UI_GRAPHICS_CJK_ENABLED
    const struct LocalizedUiGraphicsChapterTitle *localizedTitle;
#endif

    if (titleId > 0x108)
        titleId = 0x54;

    gChapterTitleFxSt.chr_str = chr & 0x3FF;

#if LOCALIZED_UI_GRAPHICS_CJK_ENABLED
    localizedTitle = LocalizedUiGraphics_GetChapterTitle(titleId);
    if (localizedTitle != 0 && localizedTitle->save != 0) {
        Decompress(localizedTitle->save, (void*)((chr * TILE_SIZE_4BPP) + VRAM));
        return;
    }
#endif

    Decompress(chap_title_data[titleId].save, (void*)((chr * TILE_SIZE_4BPP) + VRAM));
}

void _PutChapterTitleGfx(int chr, int titleId)
{
#if LOCALIZED_UI_GRAPHICS_CJK_ENABLED
    const struct LocalizedUiGraphicsChapterTitle *localizedTitle;
    const u8 *frame;

    localizedTitle = LocalizedUiGraphics_GetChapterTitle(titleId);
    frame = LocalizedUiGraphics_GetChapterTitleFrame();
    if (localizedTitle != 0 && localizedTitle->introLeft != 0
        && localizedTitle->introRight != 0 && frame != 0) {
        Decompress(frame, (void *)(VRAM + chr * TILE_SIZE_4BPP));
        Decompress(localizedTitle->introLeft, (void *)(VRAM + chr * TILE_SIZE_4BPP + 0x20));
        Decompress(localizedTitle->introRight, (void *)(VRAM + chr * TILE_SIZE_4BPP + 0x2A0));
        return;
    }
#endif

    PutChapterTitleGfx(chr, titleId);
}

void PutChapterTitleBG(int chr)
{
    gChapterTitleFxSt.chr_bg = chr & 0x3FF;
    Decompress(Img_ChapterTitleBg, (void*)((chr * TILE_SIZE_4BPP) + VRAM));
}

extern u8 Img_ChapterTitleBgAlt[];

void PutChapterTitleBGAlt(int chr)
{
    gChapterTitleFxSt.chr_bg = chr & 0x3FF;
    Decompress(Img_ChapterTitleBgAlt, (void*)((chr * TILE_SIZE_4BPP) + VRAM));
}

void DrawChapterTitleStr(u16 * tm, int pal)
{
    int i;
    int tile = TILEREF(gChapterTitleFxSt.chr_str, pal);
    for (i = 0; i < 0x40; i++)
        *tm++ = tile++;
}

void DrawChapterTitleStrEx(u16 * tm, int pal, int c)
{
#if LOCALIZED_UI_GRAPHICS_CJK_ENABLED
    const struct LocalizedUiGraphicsChapterTitle *localizedTitle;
    const u8 *tsa;

    localizedTitle = LocalizedUiGraphics_GetChapterTitle(c);
    tsa = LocalizedUiGraphics_GetChapterTitleTsa();
    if (localizedTitle != 0 && localizedTitle->introLeft != 0 && tsa != 0) {
        Decompress(tsa, gGenericBuffer);
        CallARM_FillTileRect(gBG0TilemapBuffer, gGenericBuffer, (u16)TILEREF(0x280, pal));
        BG_SetPosition(0, 0, 2);
        return;
    }
#endif

    int i;
    int tile = TILEREF(gChapterTitleFxSt.chr_str, pal);
    for (i = 0; i < 0x40; i++)
        *tm++ = tile++;
}

void DrawChapterTitleBG(u16 * tm, int pal)
{
    int i;
    int tile = TILEREF(gChapterTitleFxSt.chr_bg, pal);
    for (i = 0; i < 0x80; i++)
        *tm++ = tile++;
}

void DrawChapterTitleBGTsa(u16 * tm, int pal)
{
    CallARM_FillTileRect(tm, Tsa_ChapterTitleBg, (u16)TILEREF(gChapterTitleFxSt.chr_bg, pal));
}

int GetChapterTitleExtra(struct PlaySt * chapterData)
{

    if (chapterData == 0)
        return 0x54; // No Data

    if (chapterData->chapterStateBits & PLAY_FLAG_POSTGAME)
        return 0x57; // Creature Campaign

    if (chapterData->chapterStateBits & PLAY_FLAG_COMPLETE)
        return 0x55; // Epilogue

    return GetROMChapterStruct(chapterData->chapterIndex)->chapTitleId;
}

int GetChapterTitleWM(struct PlaySt * chapterData)
{
    int unk;
    int i;

    if (chapterData == 0) {
        return 0x54; // No Data
    }

    unk = GetPlayChapterId(chapterData->chapterIndex);

    if ((chapterData->chapterStateBits & PLAY_FLAG_POSTGAME) || GetNextUnclearedNode(&gGMData) != unk)
    {
        for (i = 0; i < gWMMonsterSpawnsSize; i++)
        {
            if (unk == gWMMonsterSpawnLocations[i])
                return 0x46 + i;
        }
    }

    return GetROMChapterStruct(chapterData->chapterIndex)->chapTitleId;
}
