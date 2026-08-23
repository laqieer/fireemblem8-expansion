#ifndef GUARD_SOUNDROOM_H
#define GUARD_SOUNDROOM_H

#include "bmsave.h"

#define SOUND_ROOM_CATALOG_CAPACITY SOUND_ROOM_SAVE_CAPACITY
#define SOUND_ROOM_CATALOG_FLAG_WORDS SOUND_ROOM_SAVE_FLAG_WORDS
#define SOUND_ROOM_SHUFFLE_END 0xFFFF

struct SoundRoomProc
{
    /* 00 */ PROC_HEADER;

    /* 29 */ u8 unk_29; // maybe padding?
    /* 2A */ u16 bgYOffset;
    /* 2C */ u16 currentSongTime;
    /* 2E */ u8 unk_2e;
    /* 2F */ u8 unk_2f;
    /* 30 */ s8 isSongPlaying;
    /* 31 */ u8 shuffleIndex;
    /* 32 */ s16 currentSongIdx;
    /* 34 */ u16 playableSongs;
    /* 36 */ u16 totalSongs;
    /* 38 */ u8 completionPercent;
    /* 39 */ u8 curIndex;
    /* 3A */ s8 unk_37;
    /* 3B */ u8 unk_38;
    /* 3C */ u8 unk_39;
    /* 3D */ s8 unk_3a;
    /* 3E */ u8 unk_3b;
    /* 3F */ s8 unk_3c;
    /* 40 */ s8 unk_3d;
    /* 41 */ s8 unk_3e;
    /* 42 */ u8 unk_3f;
    /* 43 */ u8 pad43;
    /* 44 */ u32 playableFlags[SOUND_ROOM_CATALOG_FLAG_WORDS];
};

struct SoundRoomEnt
{
    /* 00 */ int bgmId;
    /* 04 */ int songLength; // in frames
    /* 08 */ s8 (* displayCondFunc)(ProcPtr proc);
    /* 0C */ int nameTextId;
};

// ??? IsSoundRoomCompleted(???);
// ??? SoundRoom_RetFalse(???);
int CountTotalSoundRoomSongs(void);
bool IsSoundRoomCatalogValid(void);
bool IsSoundRoomCatalogEntryValid(int index);
// ??? CountSecretSoundRoomSongs(???);
// ??? IsSoundRoomSongPlayable(???);
// ??? CountDisplayedSoundRoomSongs(???);
// ??? InitSoundRoomSongData(???);
// ??? SoundRoom_Null(???);
// ??? SoundRoomSongChange_FadeOutPrevious(???);
// ??? SoundRoomSongChange_StartNext(???);
// ??? PlayNextShuffledSong(???);
// ??? InitSoundRoomShuffleBuffer(???);
// ??? SoundRoom_StartNextSong_Positive(???);
// ??? SoundRoom_StartNextSong_Negative(???);
// ??? UpdateVolumeGraphBuffer(???);
// ??? InitSoundRoomVolumeGraph(???);
// ??? VolumeGraphBuffer_Init(???);
// ??? VolumeGraphBuffer_Null(???);
// ??? VolumeGraphBuffer_Loop(???);
// ??? SoundRoom_UpdateScrollBar(???);
// ??? SoundRoom_PutHandCursor(???);
// ??? SoundRoom_GetScrollDirection(???);
// ??? SoundRoom_DrawSongList(???);
// ??? SoundRoom_DrawCompletionPercent(???);
// ??? TickCurrentSongTime(???);
// ??? SoundRoomUi_Init(???);
bool StartSoundRoomSong(struct SoundRoomProc * proc, int index, int flagsMaybe);
// ??? StopSoundRoomSong(???);
void TryDrawSoundRoomSongTitle(struct SoundRoomProc *);
// ??? SoundRoomUi_Loop_MainKeyHandler(???);
// ??? SoundRoomUi_RestartTitleMusic(???);
// ??? SoundRoomUi_OnEnd(???);
// ??? SoundRoom_DrawSlidingUi(???);
// ??? SoundRoomUi_0(???);
// ??? SoundRoomUi_Loop_MainUiSlideOut(???);
// ??? SoundRoomUi_1(???);
// ??? SoundRoomUi_2(???);
// ??? SoundRoomUi_Loop_MainUiSlideIn(???);
// ??? SoundRoomUi_3(???);
// ??? SoundRoomUi_Loop_ShufflePlayUiSlideIn(???);
// ??? SoundRoomUi_Loop_ShufflePlayKeyHandler(???);
// ??? SoundRoomUi_Loop_ShufflePlayUiSlideOut(???);
ProcPtr StartSoundRoomScreen(ProcPtr);
void SoundRoom_InitText(void);
void DrawSoundRoomSongTitle(int index);
// ??? SoundRoom_DrawSongTitleSprites(???);
// ??? DrawSoundRoomVolumeGraphSprites(???);
// ??? SoundRoom_DrawVolumeGraphSprites(???);
// ??? DrawMusicPlayerTime(???);
// ??? SoundRoom_DrawSprites_Init(???);
// ??? SoundRoom_DrawSprites_Loop(???);
ProcPtr DrawSoundRoomSprites(ProcPtr);

extern struct SoundRoomEnt gSoundRoomTable[];
extern const u32 gSoundRoomTableCount;

#endif // GUARD_SOUNDROOM_H
