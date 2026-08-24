#ifndef GUARD_SOUNDWRAPPER_H
#define GUARD_SOUNDWRAPPER_H

#include "global.h"
#include "proc.h"

#ifndef FE8_EXPANSION_DEBUGTOOLS_ENABLED
#define FE8_EXPANSION_DEBUGTOOLS_ENABLED FE8_EXPANSION_DEBUG
#endif

struct SoundSt {
    u8 filler0[2];
    u16 unk2;
    u16 songId;
    s8 is_song_playing;
    s8 unk7;
    s8 maxChannels;
};

extern struct SoundSt gSoundSt;

#if !defined(FE8_ARCHIVAL_BUILD) && FE8_EXPANSION_DEBUGTOOLS_ENABLED
struct SoundBgmContext
{
    struct SoundSt state;
};
#endif

int GetCurrentBgmSong(void);
s8 IsBgmPlaying(void);
void Sound_SetBGMVolume(int volume);
void Sound_SetSEVolume(int vol);
void Sound_FadeOutBGM(int speed);
// ??? Sound_FadeOutBGMAlt(???);
void Sound_FadeOutSE(int speed);
void StartBgmCore(int songId, struct MusicPlayerInfo * player);
void StartOrChangeBgm(int songId, int speed, struct MusicPlayerInfo * player);
void StartBgm(int songId, struct MusicPlayerInfo * player);
void StartBgmExt(int songId, int speed, struct MusicPlayerInfo * player);
void MusicFi_OnLoop(ProcPtr proc);
void StartBgmFadeIn(int songId, int b, struct MusicPlayerInfo * player);
void OverrideBgm(int songId);
void RestoreBgm(void);
void _RestoreBgm(u16 speed);
void MakeBgmOverridePersist(void);
void StartBgmVolumeChange(int volumeInit, int volumeEnd, int duration, ProcPtr parent);
// ??? MusicVc_OnLoop(???);
// ??? DelaySong_OnLoop(???);
void StartSongDelayed(int songId, int delay, struct MusicPlayerInfo * player);
void PlaySong(int songId, struct MusicPlayerInfo * player);
void Sound_SetDefaultMaxNumChannels(void);
void Sound_SetMaxNumChannels(int maxchn);
void Sound_SetupMaxChannelsForSong(int songId);
int IsMusicProc2Running(void);
// ??? ChangeBgm_FadeVolume(???);
// ??? ChangeBgm_StartNewSong(???);
void ChangeBgm(int songId, int vc_init_volume, int vc_end_volume, int duration, ProcPtr parent);
s8 MusicProc4Exists(void);
// ??? Sound_ForceChangeBgm(???);
void DeleteAll6CWaitMusicRelated(void);
void Sound_StopBgmImmediate(void);

#if !defined(FE8_ARCHIVAL_BUILD) && FE8_EXPANSION_DEBUGTOOLS_ENABLED
void Sound_CaptureBgmContext(struct SoundBgmContext * context);
bool Sound_StartTransientBgm(int songId, struct MusicPlayerInfo * player);
bool Sound_RestoreBgmContext(
    const struct SoundBgmContext * context,
    struct MusicPlayerInfo * player);
#endif

#define PlaySoundEffect(id) \
    if (!gPlaySt.config.disableSoundEffects) \
        m4aSongNumStart((id))

extern struct ProcCmd CONST_DATA gMusicProc3Script[];

#endif  // GUARD_SOUNDWRAPPER_H
