#include <stdio.h>
#include <string.h>

#include "global.h"
#include "soundwrapper.h"

#define CHECK(cond, msg) \
    do { \
        if (!(cond)) { \
            fprintf(stderr, "SOUND_TRANSIENT_HOST_TEST: FAIL: %s\n", msg); \
            return 1; \
        } \
    } while (0)

extern int gSoundTransientStubUnlockCount;
extern int gSoundTransientStubSongStartCount;
extern int gSoundTransientStubLastSong;

int main(void)
{
    struct SoundBgmContext context;
    struct SoundSt prior;

    memset(&gSoundSt, 0, sizeof(gSoundSt));
    gSoundSt.filler0[0] = 0x12;
    gSoundSt.filler0[1] = 0x34;
    gSoundSt.unk2 = 7;
    gSoundSt.songId = 42;
    gSoundSt.is_song_playing = TRUE;
    gSoundSt.unk7 = 1;
    gSoundSt.maxChannels = 8;
    prior = gSoundSt;

    Sound_CaptureBgmContext(&context);
    CHECK(memcmp(&context.state, &prior, sizeof(prior)) == 0,
          "capture must copy the complete sound context");

    CHECK(Sound_StartTransientBgm(1, NULL),
          "the first valid transient song must start");
    CHECK(Sound_StartTransientBgm(255, NULL),
          "the boundary valid transient song must replace rapidly");
    CHECK(gSoundTransientStubSongStartCount == 2,
          "rapid replacement must start exactly two songs");
    CHECK(gSoundTransientStubLastSong == 255,
          "rapid replacement must leave the boundary song current");
    CHECK(gSoundTransientStubUnlockCount == 0,
          "transient preview must never mutate sound-room unlock state");

    CHECK(Sound_RestoreBgmContext(&context, NULL),
          "a valid playing context must restore");
    CHECK(memcmp(&gSoundSt, &prior, sizeof(prior)) == 0,
          "playing restore must recover every SoundSt byte");
    CHECK(gSoundTransientStubLastSong == 42,
          "playing restore must restart the exact prior song");
    CHECK(gSoundTransientStubUnlockCount == 0,
          "restoring prior BGM must not write sound-room unlock state");

    memset(&gSoundSt, 0, sizeof(gSoundSt));
    gSoundSt.maxChannels = -1;
    prior = gSoundSt;
    Sound_CaptureBgmContext(&context);
    CHECK(Sound_StartTransientBgm(1, NULL),
          "a preview must start from silence");
    CHECK(Sound_RestoreBgmContext(&context, NULL),
          "a silent context must restore");
    CHECK(memcmp(&gSoundSt, &prior, sizeof(prior)) == 0,
          "silent restore must recover exact no-prior-BGM state");
    CHECK(gSoundTransientStubUnlockCount == 0,
          "silent restoration must remain unlock/save neutral");

    CHECK(!Sound_StartTransientBgm(0, NULL),
          "SONG_NONE must be rejected as a preview");
    CHECK(!Sound_StartTransientBgm(-1, NULL),
          "the sound-room sentinel must be rejected as a preview");
    CHECK(!Sound_StartTransientBgm(256, NULL),
          "the first invalid song above the boundary must be rejected");

    printf("SOUND_TRANSIENT_HOST_TEST: PASS\n");
    return 0;
}
