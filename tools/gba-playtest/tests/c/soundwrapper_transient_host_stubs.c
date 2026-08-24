#include "global.h"
#include "m4a.h"
#include "proc.h"
#include "bmsave.h"
#include "soundwrapper.h"

struct PlaySt gPlaySt = {0};
struct MusicPlayerInfo gMPlayInfo_BGM1 = {0};
struct MusicPlayerInfo gMPlayInfo_BGM2 = {0};
struct MusicPlayerInfo gMPlayInfo_SE1_SYS1 = {0};
struct MusicPlayerInfo gMPlayInfo_SE2_SYS2 = {0};
struct MusicPlayerInfo gMPlayInfo_SE3_BMP1 = {0};
struct MusicPlayerInfo gMPlayInfo_SE4_BMP2 = {0};
struct MusicPlayerInfo gMPlayInfo_SE5_BMP3 = {0};
struct MusicPlayerInfo gMPlayInfo_SE6_BMP4 = {0};
struct MusicPlayerInfo gMPlayInfo_SE7_EVT = {0};
const struct Song gSongTable[256] = {{0}};

int gSoundTransientStubUnlockCount;
int gSoundTransientStubSongStartCount;
int gSoundTransientStubSongStopCount;
int gSoundTransientStubLastSong = -1;

bool IsSoundRoomSongIdValid(int songId)
{
    return songId >= 0 && songId < 256;
}

void UnlockSoundRoomSong(struct SoundRoomSaveData * data, int songId)
{
    (void)data;
    (void)songId;
    gSoundTransientStubUnlockCount++;
}

int DebugTools_IsBootstrapSuppressionActive(void)
{
    return 0;
}

void Proc_EndEach(const struct ProcCmd * script)
{
    (void)script;
}

void Proc_End(ProcPtr proc)
{
    (void)proc;
}

void m4aMPlayStop(struct MusicPlayerInfo * info)
{
    (void)info;
    gSoundTransientStubSongStopCount++;
}

void m4aMPlayImmInit(struct MusicPlayerInfo * info)
{
    (void)info;
}

void m4aSoundMode(u32 mode)
{
    (void)mode;
}

void m4aSongNumStart(u16 songId)
{
    gSoundTransientStubSongStartCount++;
    gSoundTransientStubLastSong = songId;
}

void MPlayStart(struct MusicPlayerInfo * info, struct SongHeader * header)
{
    (void)info;
    (void)header;
}
