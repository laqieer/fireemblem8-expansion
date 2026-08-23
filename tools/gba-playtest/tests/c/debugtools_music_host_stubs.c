#include <string.h>

#include "global.h"
#include "hardware.h"
#include "fontgrp.h"
#include "uimenu.h"
#include "soundroom.h"
#include "soundwrapper.h"
#include "expansion_bgm.h"
#include "expansion_debugtools.h"
#include "debugtools_internal.h"

#include "constants/msg.h"
#include "constants/songs.h"

struct KeyStatusBuffer gDebugToolsMusicTestKeyStatus = {0};
struct KeyStatusBuffer * CONST_DATA gKeyStatusPtr = &gDebugToolsMusicTestKeyStatus;
struct LCDControlBuffer gLCDControlBuffer = {0};
struct PlaySt gPlaySt = {0};
struct SoundSt gSoundSt = {0};
struct DebugToolsProbe gDebugToolsProbe = {0};

static struct Font sMusicTestFont = {0};
struct Font * gActiveFont = &sMusicTestFont;
static u16 sMusicTestBgMap[32 * 32];

struct SoundRoomEnt gSoundRoomTable[] =
{
    {1, 60, NULL, 100},
    {SONG_NONE, 0, NULL, 0},
    {255, 60, NULL, MSG_COUNT - 1},
    {256, 60, NULL, 101},
};

const u32 gSoundRoomTableCount = ARRAY_COUNT(gSoundRoomTable);
const struct ExpansionBgmVariant gExpansionBgmVariants[] = {{0}};
const u32 gExpansionBgmVariantCount = 0;
const struct ExpansionBgmActionSelector gExpansionBgmActionSelectors[] = {{0}};
const u32 gExpansionBgmActionSelectorCount = 0;

int gDebugToolsMusicStubRegisteredCount;
struct DebugToolsAction gDebugToolsMusicStubRegisteredAction;
const struct MenuDef * gDebugToolsMusicStubQueuedMenu;
int gDebugToolsMusicStubReturnToHubCount;
int gDebugToolsMusicStubDrawnTextId;
int gDebugToolsMusicStubTransientStartCount;
int gDebugToolsMusicStubRestoreCount;
int gDebugToolsMusicStubLogCount;
struct DebugToolsLogEntry gDebugToolsMusicStubLastLog;

static struct SoundBgmContext sCapturedContext;

void DebugToolsMusicStub_Reset(void)
{
    memset(&gDebugToolsProbe, 0, sizeof(gDebugToolsProbe));
    memset(&gDebugToolsMusicProbe, 0, sizeof(gDebugToolsMusicProbe));
    memset(&gSoundSt, 0, sizeof(gSoundSt));
    memset(&sCapturedContext, 0, sizeof(sCapturedContext));
    memset(&gDebugToolsMusicStubRegisteredAction, 0, sizeof(gDebugToolsMusicStubRegisteredAction));
    memset(&gDebugToolsMusicStubLastLog, 0, sizeof(gDebugToolsMusicStubLastLog));
    memset(&gDebugToolsMusicTestKeyStatus, 0, sizeof(gDebugToolsMusicTestKeyStatus));
    gDebugToolsMusicStubRegisteredCount = 0;
    gDebugToolsMusicStubQueuedMenu = NULL;
    gDebugToolsMusicStubReturnToHubCount = 0;
    gDebugToolsMusicStubDrawnTextId = -1;
    gDebugToolsMusicStubTransientStartCount = 0;
    gDebugToolsMusicStubRestoreCount = 0;
    gDebugToolsMusicStubLogCount = 0;

    gSoundRoomTable[0].bgmId = 1;
    gSoundRoomTable[0].nameTextId = 100;
    gSoundRoomTable[1].bgmId = SONG_NONE;
    gSoundRoomTable[1].nameTextId = 0;
    gSoundRoomTable[2].bgmId = 255;
    gSoundRoomTable[2].nameTextId = MSG_COUNT - 1;
    gSoundRoomTable[3].bgmId = 256;
    gSoundRoomTable[3].nameTextId = 101;
}

void DebugToolsMusicStub_SetSound(
    u16 underlyingSong,
    u16 songId,
    s8 playing,
    s8 temporaryFade,
    s8 maxChannels)
{
    gSoundSt.unk2 = underlyingSong;
    gSoundSt.songId = songId;
    gSoundSt.is_song_playing = playing;
    gSoundSt.unk7 = temporaryFade;
    gSoundSt.maxChannels = maxChannels;
}

u16 * BG_GetMapBuffer(int bg)
{
    (void)bg;
    return sMusicTestBgMap;
}

void BG_EnableSyncByMask(int mask)
{
    (void)mask;
}

void ClearText(struct Text * text)
{
    (void)text;
}

void Text_DrawString(struct Text * text, const char * string)
{
    (void)text;
    (void)string;
}

void PutText(struct Text * text, u16 * dest)
{
    (void)text;
    (void)dest;
}

char * GetStringFromIndex(int index)
{
    static char label[] = "Localized Song";

    gDebugToolsMusicStubDrawnTextId = index;
    return label;
}

u8 MenuAlwaysEnabled(const struct MenuItemDef * def, int number)
{
    (void)def;
    (void)number;
    return MENU_ENABLED;
}

u8 MenuCancelSelect(struct MenuProc * menu, struct MenuItemProc * item)
{
    (void)menu;
    (void)item;
    return MENU_ACT_SKIPCURSOR | MENU_ACT_CLEAR | MENU_ACT_END | MENU_ACT_SND6B;
}

int DebugTools_RegisterBuiltinAction(const struct DebugToolsAction * action)
{
    gDebugToolsMusicStubRegisteredAction = *action;
    gDebugToolsMusicStubRegisteredCount++;
    return DEBUGTOOLS_OK;
}

void DebugTools_QueueSubmenuTransition(
    struct MenuProc * menu,
    const struct MenuDef * menuDef)
{
    (void)menu;
    gDebugToolsMusicStubQueuedMenu = menuDef;
}

void DebugTools_ReturnToHubAfterMenuEnd(struct MenuProc * menu)
{
    (void)menu;
    gDebugToolsMusicStubReturnToHubCount++;
}

void DebugTools_LogEvent(u32 code, u32 a, u32 b)
{
    gDebugToolsMusicStubLogCount++;
    gDebugToolsMusicStubLastLog.code = code;
    gDebugToolsMusicStubLastLog.a = a;
    gDebugToolsMusicStubLastLog.b = b;
}

bool IsSoundRoomSongIdValid(int songId)
{
    return songId >= 0 && songId < SOUND_ROOM_CATALOG_CAPACITY;
}

bool CheckFlag(int flagId)
{
    (void)flagId;
    return FALSE;
}

bool IsSoundRoomCatalogEntryValid(int index)
{
    const struct SoundRoomEnt * entry;

    if (index < 0 || (u32)index >= gSoundRoomTableCount)
        return FALSE;

    entry = &gSoundRoomTable[index];
    return entry->bgmId != SONG_NONE
        && IsSoundRoomSongIdValid(entry->bgmId)
        && entry->nameTextId > 0
        && entry->nameTextId < MSG_COUNT;
}

void Sound_CaptureBgmContext(struct SoundBgmContext * context)
{
    context->state = gSoundSt;
    sCapturedContext = *context;
}

bool Sound_StartTransientBgm(int songId, struct MusicPlayerInfo * player)
{
    (void)player;

    if (songId == SONG_NONE || !IsSoundRoomSongIdValid(songId))
        return FALSE;

    gDebugToolsMusicStubTransientStartCount++;
    gSoundSt.unk2 = SONG_NONE;
    gSoundSt.songId = songId;
    gSoundSt.is_song_playing = TRUE;
    gSoundSt.unk7 = 0;
    return TRUE;
}

bool Sound_RestoreBgmContext(
    const struct SoundBgmContext * context,
    struct MusicPlayerInfo * player)
{
    (void)player;

    gDebugToolsMusicStubRestoreCount++;
    gSoundSt = context->state;
    return TRUE;
}

int GetCurrentBgmSong(void)
{
    return gSoundSt.songId;
}

s8 IsBgmPlaying(void)
{
    return gSoundSt.is_song_playing;
}

void StartOrChangeBgm(
    int songId,
    int speed,
    struct MusicPlayerInfo * player)
{
    (void)speed;
    (void)player;

    gSoundSt.songId = songId;
    gSoundSt.is_song_playing = songId != SONG_NONE;
}
