#include "global.h"

#ifndef FE8_ARCHIVAL_BUILD
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

#ifdef MODERN
#include "expansion_locale.h"
#include "expansion_msg_ids.h"
#endif

SECTION("debugtools_contributor_data") struct DebugToolsMusicProbe
    gDebugToolsMusicProbe = {0};

#if FE8_EXPANSION_DEBUGTOOLS_ENABLED

enum
{
    DEBUGTOOLS_MUSIC_OVERRIDE_ID = 0xE2
};

struct DebugToolsMusicState
{
    int selectedIndex;
    u8 sessionActive;
};

SECTION("debugtools_contributor_data") static struct DebugToolsMusicState
    sMusicState = {-1, FALSE};

static void DebugToolsMusic_RecordRejected(int index)
{
    u32 songId = (u32)SONG_NONE;

    if (index >= 0 && (u32)index < gSoundRoomTableCount
        && gSoundRoomTableCount <= SOUND_ROOM_CATALOG_CAPACITY)
        songId = (u32)gSoundRoomTable[index].bgmId;

    gDebugToolsMusicProbe.rejectedEntryCount++;
    DebugTools_LogEvent(
        DEBUGTOOLS_LOG_MUSIC_REJECTED,
        (u32)index,
        songId);
}

static int DebugToolsMusic_FindNext(int current, int direction)
{
    int candidate = current;
    u32 step;

    if (gSoundRoomTableCount == 0
        || gSoundRoomTableCount > SOUND_ROOM_CATALOG_CAPACITY)
        return -1;

    for (step = 0; step < gSoundRoomTableCount; ++step)
    {
        candidate += direction;

        if (candidate < 0)
            candidate = (int)gSoundRoomTableCount - 1;
        else if ((u32)candidate >= gSoundRoomTableCount)
            candidate = 0;

        if (IsSoundRoomCatalogEntryValid(candidate))
            return candidate;

        DebugToolsMusic_RecordRejected(candidate);
    }

    return -1;
}

static int DebugToolsMusic_DrawSong(struct MenuProc * menu, struct MenuItemProc * item)
{
    const char * label;

    ClearText(&item->text);

    if (!IsSoundRoomCatalogEntryValid(sMusicState.selectedIndex))
        return 0;

    label = GetStringFromIndex(
        gSoundRoomTable[sMusicState.selectedIndex].nameTextId);
    Text_DrawString(&item->text, label);
    PutText(
        &item->text,
        TILEMAP_LOCATED(
            BG_GetMapBuffer(menu->frontBg),
            item->xTile,
            item->yTile));
    BG_EnableSyncByMask(BG_SYNC_BIT(menu->frontBg));
    return 0;
}

#ifdef MODERN
static int DebugToolsMusic_DrawBack(struct MenuProc * menu, struct MenuItemProc * item)
{
    ClearText(&item->text);
    Text_DrawString(
        &item->text,
        ExpansionLocale_ResolveCurrent(EXP_MSG_FRAMEWORK_BACK));
    PutText(
        &item->text,
        TILEMAP_LOCATED(
            BG_GetMapBuffer(menu->frontBg),
            item->xTile,
            item->yTile));
    BG_EnableSyncByMask(BG_SYNC_BIT(menu->frontBg));
    return 0;
}
#endif

static u8 DebugToolsMusic_SongIdle(struct MenuProc * menu, struct MenuItemProc * item)
{
    int next = -1;

    if (gKeyStatusPtr->repeatedKeys & DPAD_LEFT)
        next = DebugToolsMusic_FindNext(sMusicState.selectedIndex, -1);
    else if (gKeyStatusPtr->repeatedKeys & DPAD_RIGHT)
        next = DebugToolsMusic_FindNext(sMusicState.selectedIndex, 1);

    if (next >= 0 && next != sMusicState.selectedIndex)
    {
        sMusicState.selectedIndex = next;
        DebugToolsMusic_DrawSong(menu, item);
    }

    return 0;
}

static u8 DebugToolsMusic_SongSelected(
    struct MenuProc * menu,
    struct MenuItemProc * item)
{
    const struct SoundRoomEnt * entry;

    (void)menu;
    (void)item;

    if (!sMusicState.sessionActive
        || !IsSoundRoomCatalogEntryValid(sMusicState.selectedIndex))
    {
        DebugToolsMusic_RecordRejected(sMusicState.selectedIndex);
        return MENU_ACT_SND6B;
    }

    entry = &gSoundRoomTable[sMusicState.selectedIndex];

    if (!ExpansionBgm_PreviewSong(
            EXPANSION_BGM_PREVIEW_OWNER_DEBUGTOOLS_MUSIC,
            entry->bgmId))
    {
        DebugToolsMusic_RecordRejected(sMusicState.selectedIndex);
        return MENU_ACT_SND6B;
    }

    gDebugToolsMusicProbe.selectedSongId = (u32)entry->bgmId;
    gDebugToolsMusicProbe.previewCount++;
    gDebugToolsMusicProbe.ownerActive = 1;
    DebugTools_LogEvent(
        DEBUGTOOLS_LOG_MUSIC_PREVIEW,
        (u32)entry->bgmId,
        gDebugToolsMusicProbe.previewCount);

    return MENU_ACT_SND6A;
}

static u8 DebugToolsMusic_BackSelected(
    struct MenuProc * menu,
    struct MenuItemProc * item)
{
    return MenuCancelSelect(menu, item);
}

static const struct MenuItemDef sMusicMenuItemDefs[] =
{
    {
        .name = "Music",
        .overrideId = DEBUGTOOLS_MUSIC_OVERRIDE_ID,
        .isAvailable = MenuAlwaysEnabled,
        .onDraw = DebugToolsMusic_DrawSong,
        .onSelected = DebugToolsMusic_SongSelected,
        .onIdle = DebugToolsMusic_SongIdle,
    },
    {
        .name = "Back",
        .isAvailable = MenuAlwaysEnabled,
#ifdef MODERN
        .onDraw = DebugToolsMusic_DrawBack,
#endif
        .onSelected = DebugToolsMusic_BackSelected,
    },
    {0},
};

void DebugTools_CleanupMusicPreview(void)
{
    int releaseResult;

    if (!sMusicState.sessionActive
        && ExpansionBgm_GetPreviewOwner()
            != EXPANSION_BGM_PREVIEW_OWNER_DEBUGTOOLS_MUSIC)
        return;

    releaseResult = ExpansionBgm_ReleasePreview(
        EXPANSION_BGM_PREVIEW_OWNER_DEBUGTOOLS_MUSIC);

    if (releaseResult == EXPANSION_BGM_PREVIEW_RESTORED)
    {
        gDebugToolsMusicProbe.restoreCount++;
        DebugTools_LogEvent(
            DEBUGTOOLS_LOG_MUSIC_RESTORE,
            gDebugToolsMusicProbe.priorSongId,
            gDebugToolsMusicProbe.priorWasPlaying);
    }
    else if (releaseResult == EXPANSION_BGM_PREVIEW_RELEASE_ERROR)
    {
        DebugToolsMusic_RecordRejected(sMusicState.selectedIndex);
    }

    gDebugToolsMusicProbe.ownerActive = 0;
    sMusicState.selectedIndex = -1;
    sMusicState.sessionActive = FALSE;
}

static void DebugToolsMusic_OnEnd(struct MenuProc * menu)
{
    int shouldReturn = sMusicState.sessionActive;

    DebugTools_CleanupMusicPreview();

    if (shouldReturn)
        DebugTools_ReturnToHubAfterMenuEnd(menu);
}

CONST_DATA struct MenuDef gDebugToolsMusicPreviewMenuDef = {
    {1, 1, DEBUGTOOLS_MENU_WIDTH_TILES, 0},
    0,
    sMusicMenuItemDefs,
    0,
    DebugToolsMusic_OnEnd,
    0,
    MenuCancelSelect,
    0,
    0
};

static u8 DebugToolsMusic_ActionSelected(
    struct MenuProc * menu,
    struct MenuItemProc * item)
{
    int first;

    (void)item;

    first = DebugToolsMusic_FindNext(-1, 1);
    if (first < 0)
    {
        DebugToolsMusic_RecordRejected(-1);
        return MENU_ACT_SND6B;
    }

    if (!ExpansionBgm_AcquirePreview(
            EXPANSION_BGM_PREVIEW_OWNER_DEBUGTOOLS_MUSIC))
    {
        DebugToolsMusic_RecordRejected(first);
        return MENU_ACT_SND6B;
    }

    sMusicState.selectedIndex = first;
    sMusicState.sessionActive = TRUE;
    gDebugToolsMusicProbe.ownerActive = 1;
    gDebugToolsMusicProbe.priorWasPlaying = IsBgmPlaying() != 0;
    gDebugToolsMusicProbe.priorSongId =
        gDebugToolsMusicProbe.priorWasPlaying
            ? (u32)GetCurrentBgmSong()
            : (u32)SONG_NONE;
    gDebugToolsMusicProbe.priorContext =
        (u32)ExpansionBgm_GetCurrentContext();

    DebugTools_QueueSubmenuTransition(menu, &gDebugToolsMusicPreviewMenuDef);

    return MENU_ACT_SKIPCURSOR | MENU_ACT_END | MENU_ACT_SND6A | MENU_ACT_CLEAR;
}

CONST_DATA static struct DebugToolsAction sMusicPreviewAction = {
    10,
    "Music Preview",
    DebugToolsMusic_ActionSelected
};

void DebugTools_RegisterMusicPreviewAction(void)
{
    DebugTools_RegisterBuiltinAction(&sMusicPreviewAction);
}

#else

void DebugTools_RegisterMusicPreviewAction(void)
{
}

void DebugTools_CleanupMusicPreview(void)
{
}

#endif

#endif
