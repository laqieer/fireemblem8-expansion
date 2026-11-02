#include "global.h"

#include "agb_sram.h"
#include "bmmap.h"
#include "bmsave.h"
#include "chapterdata.h"
#include "hardware.h"
#include "m4a.h"

#ifdef GRADO_NATIVE_SOURCE_OWNER
#define GradoNativeMapCH00 PrologueMap
#define GradoNativeMapCH02 Ch2Map
#define GradoNativeTileConfig TileConfiguration5
#endif

extern const u8 GradoNativeMapCH00[];
extern const u8 GradoNativeMapCH02[];
extern const u8 GradoNativeTileConfig[];
#ifdef GRADO_NATIVE_SOURCE_OWNER
extern const u8 ObjectType5[];
extern const u8 MapPalette7[];
#endif

#ifdef GRADO_RENDER_CHAPTER
static u8 sVisibleFog[12][16];
static u8 * sVisibleFogRows[12];
#endif

struct GradoMapRecord
{
    u32 chapter;
    u32 binding;
    u32 width;
    u32 height;
    u32 graphics;
    u32 palette;
    u32 configuration;
    u32 layer;
    u32 values[5];
    u32 terrain[5];
};

struct GradoMapProbe
{
    u32 phase;
    u32 unexpectedCalls;
    struct GradoMapRecord records[2];
};

EWRAM_DATA volatile struct GradoMapProbe gGradoMapProbe;
EWRAM_DATA u8 gGenericBuffer[0x2000];
CONST_DATA struct ExtraMapInfo * gExtraMapInfo;
/* Decompress's legacy data section retains unused audio callbacks; those trap below. */
const struct Song gSongTable[1] = {{0}};
EWRAM_DATA struct MusicPlayerInfo gMPlayInfo_BGM1;
EWRAM_DATA struct MusicPlayerInfo gMPlayInfo_BGM2;
EWRAM_DATA struct MusicPlayerInfo gMPlayInfo_SE1_SYS1;
EWRAM_DATA struct MusicPlayerInfo gMPlayInfo_SE2_SYS2;
EWRAM_DATA struct MusicPlayerInfo gMPlayInfo_SE3_BMP1;
EWRAM_DATA struct MusicPlayerInfo gMPlayInfo_SE4_BMP2;
EWRAM_DATA struct MusicPlayerInfo gMPlayInfo_SE5_BMP3;
EWRAM_DATA struct MusicPlayerInfo gMPlayInfo_SE6_BMP4;
EWRAM_DATA struct MusicPlayerInfo gMPlayInfo_SE7_EVT;

struct ROMChapterData CONST_DATA gChapterDataTable[] = {
    {.map = {.obj1Id = 60, .paletteId = 61, .tileConfigId = 62, .mainLayerId = 4}},
    {0},
    {.map = {.obj1Id = 60, .paletteId = 61, .tileConfigId = 62, .mainLayerId = 11}},
};

const void * gChapterDataAssetTable[63] = {
    [4] = GradoNativeMapCH00,
    [11] = GradoNativeMapCH02,
#ifdef GRADO_NATIVE_SOURCE_OWNER
    [60] = ObjectType5,
    [61] = MapPalette7,
#endif
    [62] = GradoNativeTileConfig,
};

static void UnexpectedRoute(void) __attribute__((noreturn));

static void UnexpectedRoute(void)
{
    ++gGradoMapProbe.unexpectedCalls;
    gGradoMapProbe.phase = 0xEE;
    for (;;)
        ;
}

static void UnexpectedRead(const void * source, void * destination, u32 size)
{
    (void)source;
    (void)destination;
    (void)size;
    UnexpectedRoute();
}

void m4aSongNumStart(u16 song)
{
    (void)song;
    UnexpectedRoute();
}

void m4aMPlayImmInit(struct MusicPlayerInfo * player)
{
    (void)player;
    UnexpectedRoute();
}

void m4aMPlayPanpotControl(struct MusicPlayerInfo * player, u16 tracks, s8 pan)
{
    (void)player;
    (void)tracks;
    (void)pan;
    UnexpectedRoute();
}

void (*ReadSramFast)(const void *, void *, u32) = UnexpectedRead;

const void * GetExtraMapMapReadAddr(void)
{
    UnexpectedRoute();
}

u32 GetExtraMapMapSize(void)
{
    UnexpectedRoute();
}

void AgbMain(void)
{
    const u8 coordinates[2][5][2] = {
        {{0, 0}, {2, 4}, {4, 3}, {14, 5}, {12, 1}},
        {{0, 0}, {7, 10}, {4, 9}, {14, 1}, {7, 4}},
    };
    const void * expectedMaps[2] = {GradoNativeMapCH00, GradoNativeMapCH02};
    const struct ROMChapterData * chapter;
    volatile struct GradoMapRecord * record;
    unsigned int map;
    unsigned int sample;
    unsigned int x;
    unsigned int y;

    gGradoMapProbe.phase = 1;
    for (map = 0; map < 2; ++map)
    {
        chapter = GetROMChapterStruct(map * 2);
        record = gGradoMapProbe.records + map;
        record->chapter = map * 2;
        record->binding = GetChapterMapPointer(map * 2) == expectedMaps[map];
        record->graphics = chapter->map.obj1Id;
        record->palette = chapter->map.paletteId;
        record->configuration = chapter->map.tileConfigId;
        record->layer = chapter->map.mainLayerId;
        UnpackChapterMap(gBmMapBuffer, map * 2);
        InitBaseTilesBmMap();
        record->width = gBmMapSize.x;
        record->height = gBmMapSize.y;
        for (sample = 0; sample < 5; ++sample)
        {
            x = coordinates[map][sample][0];
            y = coordinates[map][sample][1];
            record->values[sample] = gBmMapBaseTiles[y][x];
            record->terrain[sample] = GetTrueTerrainAt(x, y);
        }
    }
#ifdef GRADO_RENDER_CHAPTER
    gGradoMapProbe.phase = 3;
    gPlaySt.chapterIndex = GRADO_RENDER_CHAPTER;
    UnpackChapterMap(gBmMapBuffer, GRADO_RENDER_CHAPTER);
    InitBaseTilesBmMap();
    for (y = 0; y < 12; ++y)
    {
        sVisibleFogRows[y] = sVisibleFog[y];
        for (x = 0; x < 16; ++x)
            sVisibleFog[y][x] = 1;
    }
    gBmMapFog = sVisibleFogRows;
    UnpackChapterMapGraphics(GRADO_RENDER_CHAPTER);
    SetBackgroundMapDataOffset(2, 0xF000);
    SetBackgroundScreenSize(2, 0);
    BG_SetColorBpp(2, 4);
    SetDispEnable(0, 0, 1, 0, 0);
    RenderBmMapOnBg2();
    FlushBackgrounds();
    FlushLCDControl();
#endif
    gGradoMapProbe.phase = 2;
    for (;;)
        ;
}
