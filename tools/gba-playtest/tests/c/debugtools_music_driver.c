#include <stdio.h>
#include <string.h>

#include "global.h"
#include "hardware.h"
#include "uimenu.h"
#include "soundroom.h"
#include "soundwrapper.h"
#include "expansion_bgm.h"
#include "expansion_debugtools.h"

#include "constants/msg.h"
#include "constants/songs.h"

#define CHECK(cond, msg) \
    do { \
        if (!(cond)) { \
            fprintf(stderr, "DEBUGTOOLS_MUSIC_HOST_TEST: FAIL: %s\n", msg); \
            return 1; \
        } \
    } while (0)

extern struct KeyStatusBuffer gDebugToolsMusicTestKeyStatus;
extern int gDebugToolsMusicStubRegisteredCount;
extern struct DebugToolsAction gDebugToolsMusicStubRegisteredAction;
extern const struct MenuDef * gDebugToolsMusicStubQueuedMenu;
extern int gDebugToolsMusicStubReturnToHubCount;
extern int gDebugToolsMusicStubDrawnTextId;
extern int gDebugToolsMusicStubTransientStartCount;
extern int gDebugToolsMusicStubRestoreCount;
extern int gDebugToolsMusicStubLogCount;
extern struct DebugToolsLogEntry gDebugToolsMusicStubLastLog;
extern void DebugToolsMusicStub_Reset(void);
extern void DebugToolsMusicStub_SetSound(
    u16 underlyingSong,
    u16 songId,
    s8 playing,
    s8 temporaryFade,
    s8 maxChannels);

static int RunPlayingContextCase(void)
{
    struct MenuProc menu;
    struct MenuItemProc item;
    struct SoundSt prior;
    u8 result;
    u32 rejectedBefore;

    memset(&menu, 0, sizeof(menu));
    memset(&item, 0, sizeof(item));
    menu.frontBg = 0;
    item.xTile = 1;
    item.yTile = 1;

    ExpansionBgm_StartExplicit(
        EXPANSION_BGM_CONTEXT_TITLE,
        42,
        0,
        NULL);
    DebugToolsMusicStub_SetSound(7, 42, TRUE, 1, 8);
    prior = gSoundSt;

    DebugTools_RegisterMusicPreviewAction();
    CHECK(gDebugToolsMusicStubRegisteredCount == 1,
          "the initializer must register exactly one action");
    CHECK(gDebugToolsMusicStubRegisteredAction.id == 10,
          "music preview must use stable built-in id 10");
    CHECK(strcmp(gDebugToolsMusicStubRegisteredAction.label, "Music Preview") == 0,
          "music preview must retain its fallback action label");

    result = gDebugToolsMusicStubRegisteredAction.onSelected(&menu, &item);
    CHECK(result == (MENU_ACT_SKIPCURSOR | MENU_ACT_END | MENU_ACT_SND6A | MENU_ACT_CLEAR),
          "opening music preview must close the hub through the deferred handoff");
    CHECK(gDebugToolsMusicStubQueuedMenu != NULL,
          "opening music preview must queue its one bounded submenu");
    CHECK(gDebugToolsMusicProbe.ownerActive == 1,
          "opening the submenu must acquire exactly one owner");
    CHECK(gDebugToolsMusicProbe.priorSongId == 42,
          "the prior playing song must be captured exactly");
    CHECK(gDebugToolsMusicProbe.priorWasPlaying == 1,
          "the prior playing state must be captured exactly");
    CHECK(gDebugToolsMusicProbe.priorContext == EXPANSION_BGM_CONTEXT_TITLE,
          "the prior typed title context must be captured exactly");
    CHECK(ExpansionBgm_GetPreviewOwner()
          == EXPANSION_BGM_PREVIEW_OWNER_DEBUGTOOLS_MUSIC,
          "the typed BGM seam must expose the debugtools owner");
    CHECK(ExpansionBgm_GetCurrentContext() == EXPANSION_BGM_CONTEXT_TITLE,
          "the typed title context must be retained while preview owns audio");

    item.def = &gDebugToolsMusicStubQueuedMenu->menuItems[0];
    item.def->onDraw(&menu, &item);
    CHECK(gDebugToolsMusicStubDrawnTextId == 100,
          "the first row must resolve its authoritative localized name");

    result = item.def->onSelected(&menu, &item);
    CHECK(result == MENU_ACT_SND6A,
          "a validated preview must acknowledge selection without closing");
    CHECK(gSoundSt.songId == 1 && gSoundSt.is_song_playing,
          "the first valid catalog song must become the transient BGM");
    CHECK(gDebugToolsMusicProbe.selectedSongId == 1,
          "selected-song telemetry must record the numeric song id");
    CHECK(gDebugToolsMusicProbe.previewCount == 1,
          "the first preview must increment the preview count once");

    gDebugToolsMusicTestKeyStatus.repeatedKeys = DPAD_LEFT;
    item.def->onIdle(&menu, &item);
    gDebugToolsMusicTestKeyStatus.repeatedKeys = 0;
    item.def->onDraw(&menu, &item);
    CHECK(gDebugToolsMusicStubDrawnTextId == MSG_COUNT - 1,
          "left from the first row must wrap to the boundary valid name");
    CHECK(gDebugToolsMusicProbe.rejectedEntryCount == 1,
          "navigation must reject and skip the malformed trailing row once");

    item.def->onSelected(&menu, &item);
    CHECK(gSoundSt.songId == 255,
          "the boundary valid song id must preview successfully");
    CHECK(gDebugToolsMusicProbe.previewCount == 2,
          "rapid boundary selection must increment deterministically");

    gDebugToolsMusicTestKeyStatus.repeatedKeys = DPAD_RIGHT;
    item.def->onIdle(&menu, &item);
    gDebugToolsMusicTestKeyStatus.repeatedKeys = 0;
    item.def->onSelected(&menu, &item);
    CHECK(gSoundSt.songId == 1,
          "rapid forward selection must wrap and replace the owned preview");
    CHECK(gDebugToolsMusicProbe.previewCount == 3,
          "rapid replacement must not recapture or lose preview telemetry");
    CHECK(gDebugToolsMusicStubTransientStartCount == 3,
          "each accepted A press must start exactly one transient song");

    rejectedBefore = gDebugToolsMusicProbe.rejectedEntryCount;
    result = gDebugToolsMusicStubRegisteredAction.onSelected(&menu, &item);
    CHECK(result == MENU_ACT_SND6B,
          "a nested preview session must be rejected without a second submenu");
    CHECK(gDebugToolsMusicProbe.rejectedEntryCount == rejectedBefore + 1,
          "nested-owner rejection must be observable");

    gSoundRoomTable[0].bgmId = SONG_NONE;
    rejectedBefore = gDebugToolsMusicProbe.rejectedEntryCount;
    result = item.def->onSelected(&menu, &item);
    CHECK(result == MENU_ACT_SND6B,
          "a sentinel song must fail closed");
    CHECK(gDebugToolsMusicProbe.previewCount == 3,
          "a rejected sentinel must not increment preview count");
    CHECK(gDebugToolsMusicProbe.rejectedEntryCount == rejectedBefore + 1,
          "a rejected sentinel must increment rejection telemetry");

    gSoundRoomTable[0].bgmId = -1;
    rejectedBefore = gDebugToolsMusicProbe.rejectedEntryCount;
    result = item.def->onSelected(&menu, &item);
    CHECK(result == MENU_ACT_SND6B,
          "the sound-room table terminator value must fail closed");
    CHECK(gDebugToolsMusicProbe.previewCount == 3,
          "the excluded terminator must not increment preview count");
    CHECK(gDebugToolsMusicProbe.rejectedEntryCount == rejectedBefore + 1,
          "the excluded terminator must increment rejection telemetry");

    gSoundRoomTable[0].bgmId = 1;
    gSoundRoomTable[0].nameTextId = MSG_COUNT;
    rejectedBefore = gDebugToolsMusicProbe.rejectedEntryCount;
    result = item.def->onSelected(&menu, &item);
    CHECK(result == MENU_ACT_SND6B,
          "an unsafe name id must fail closed before text lookup");
    CHECK(gDebugToolsMusicProbe.rejectedEntryCount == rejectedBefore + 1,
          "an unsafe name id must increment rejection telemetry");
    gSoundRoomTable[0].nameTextId = 100;

    gDebugToolsMusicStubQueuedMenu->onEnd(&menu);
    CHECK(gDebugToolsMusicStubRestoreCount == 1,
          "Back/cancel must restore one played preview exactly once");
    CHECK(memcmp(&gSoundSt, &prior, sizeof(prior)) == 0,
          "restore must recover the complete prior SoundSt context");
    CHECK(gDebugToolsMusicProbe.restoreCount == 1,
          "restore telemetry must increment once");
    CHECK(gDebugToolsMusicProbe.ownerActive == 0,
          "Back/cancel must release ownership");
    CHECK(gDebugToolsMusicStubReturnToHubCount == 1,
          "ordinary submenu Back must return to the same debug session");
    CHECK(ExpansionBgm_GetCurrentContext() == EXPANSION_BGM_CONTEXT_TITLE,
          "release must restore the exact typed title context");
    CHECK(gDebugToolsMusicStubLastLog.code == DEBUGTOOLS_LOG_MUSIC_RESTORE,
          "the bounded log must record the final restoration");

    return 0;
}

static int RunSilentAndForcedCleanupCases(void)
{
    struct MenuProc menu;
    struct MenuItemProc item;
    struct SoundSt silent;
    int restoresBefore;
    int returnsBefore;

    memset(&menu, 0, sizeof(menu));
    memset(&item, 0, sizeof(item));
    menu.frontBg = 0;

    ExpansionBgm_StartExplicit(
        EXPANSION_BGM_CONTEXT_MENU,
        SONG_NONE,
        0,
        NULL);
    DebugToolsMusicStub_SetSound(0, SONG_NONE, FALSE, 0, -1);
    silent = gSoundSt;

    gDebugToolsMusicStubRegisteredAction.onSelected(&menu, &item);
    item.def = &gDebugToolsMusicStubQueuedMenu->menuItems[0];
    item.def->onSelected(&menu, &item);
    gDebugToolsMusicStubQueuedMenu->onEnd(&menu);
    CHECK(memcmp(&gSoundSt, &silent, sizeof(silent)) == 0,
          "a no-prior-BGM context must restore to exact silence");
    CHECK(ExpansionBgm_GetCurrentContext() == EXPANSION_BGM_CONTEXT_MENU,
          "silent restoration must preserve its typed menu context");

    restoresBefore = gDebugToolsMusicStubRestoreCount;
    gDebugToolsMusicStubRegisteredAction.onSelected(&menu, &item);
    gDebugToolsMusicStubQueuedMenu->onEnd(&menu);
    CHECK(gDebugToolsMusicStubRestoreCount == restoresBefore,
          "cancel before preview must release ownership without restarting audio");

    gDebugToolsMusicStubRegisteredAction.onSelected(&menu, &item);
    item.def = &gDebugToolsMusicStubQueuedMenu->menuItems[0];
    item.def->onSelected(&menu, &item);
    returnsBefore = gDebugToolsMusicStubReturnToHubCount;
    DebugTools_CleanupMusicPreview();
    CHECK(gDebugToolsMusicProbe.ownerActive == 0,
          "forced cleanup must synchronously release the owner");
    CHECK(memcmp(&gSoundSt, &silent, sizeof(silent)) == 0,
          "forced cleanup must restore exact prior silence");
    DebugTools_CleanupMusicPreview();
    CHECK(gDebugToolsMusicStubRestoreCount == restoresBefore + 1,
          "forced cleanup must be idempotent");
    gDebugToolsMusicStubQueuedMenu->onEnd(&menu);
    CHECK(gDebugToolsMusicStubReturnToHubCount == returnsBefore,
          "a forcibly torn-down submenu must not reopen the hub");

    return 0;
}

int main(void)
{
    DebugToolsMusicStub_Reset();

    if (RunPlayingContextCase() != 0)
        return 1;

    if (RunSilentAndForcedCleanupCases() != 0)
        return 1;

    CHECK(gDebugToolsMusicStubLogCount > 0,
          "music preview must emit deterministic bounded log telemetry");
    printf("DEBUGTOOLS_MUSIC_HOST_TEST: PASS\n");
    return 0;
}
