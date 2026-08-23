#include <stddef.h>
#include <string.h>

#include "global.h"
#include "proc.h"
#include "bmsave.h"
#include "save_format.h"
#include "expansion_debugtools.h"
#include "expansion_debug_save_fixture.h"

u8 sGameStartSaveBuf[0x8000];
u8 gGenericBuffer[0x2000];
struct PlaySt gPlaySt;

static u8 sSourceBytes[0x8000];
CONST_DATA struct SaveBlocks *gSram = (struct SaveBlocks *)sSourceBytes;

static enum SaveCompatState sSourceCompatibility = SAVE_COMPAT_CURRENT;
static int sTitleActive = TRUE;
static int sGameLoadCount;
static int sSuspendLoadCount;
static int sLastLoadedSlot = -1;

struct ProcCmd CONST_DATA gProcScr_TitleScreen[] = {
    PROC_END
};

static void ReadSramFastHost(void const *src, void *dest, u32 size)
{
    memcpy(dest, src, size);
}

void (*ReadSramFast)(void const *src, void *dest, u32 size) =
    ReadSramFastHost;

u32 (*VerifySramFast)(void const *src, void *dest, u32 size);
#ifdef MODERN
u32 (*VerifySramValueFast)(void const *src, u8 value, u32 size);
#endif

struct SaveBlocks *DebugSaveFixtureHost_GetSource(void)
{
    return (struct SaveBlocks *)sSourceBytes;
}

void DebugSaveFixtureHost_SetCompatibility(enum SaveCompatState state)
{
    sSourceCompatibility = state;
}

void DebugSaveFixtureHost_SetTitleActive(int active)
{
    sTitleActive = active;
}

int DebugSaveFixtureHost_GetGameLoadCount(void)
{
    return sGameLoadCount;
}

int DebugSaveFixtureHost_GetSuspendLoadCount(void)
{
    return sSuspendLoadCount;
}

int DebugSaveFixtureHost_GetLastLoadedSlot(void)
{
    return sLastLoadedSlot;
}

ProcPtr Proc_Find(const struct ProcCmd *script)
{
    if (script == gProcScr_TitleScreen && sTitleActive)
        return (ProcPtr)1;

    return NULL;
}

int DebugTools_IsChapter2LaunchPending(void)
{
    return FALSE;
}

int DebugTools_IsChapter4PrepLaunchPending(void)
{
    return FALSE;
}

u16 Checksum16(void const *data, int size)
{
    u16 const *values = data;
    u32 add = 0;
    u32 x = 0;
    int i;

    for (i = 0; i < size / 2; ++i)
    {
        add += values[i];
        x ^= values[i];
    }

    return (u16)(add + x);
}

u32 ComputeChecksum32(const u32 *values, int size)
{
    u32 hash = 2166136261u;
    const u8 *bytes = (const u8 *)values;
    int i;

    for (i = 0; i < size; ++i)
    {
        hash ^= bytes[i];
        hash *= 16777619u;
    }

    return hash;
}

u16 ExpansionSaveMetaChecksum(struct ExpansionSaveMeta const *meta)
{
    return Checksum16(meta, EXPANSION_SAVE_META_SIZE_FOR_CHECKSUM);
}

void BuildCurrentExpansionSaveMeta(struct ExpansionSaveMeta *meta)
{
    memset(meta, 0, sizeof(*meta));
    memcpy(meta->magic, EXPANSION_SAVE_META_MAGIC, EXPANSION_SAVE_META_MAGIC_SIZE);
    meta->formatVersion = SAVE_FORMAT_VERSION_CURRENT;
    meta->compatEpoch = FE8_EXPANSION_SAVE_COMPAT_EPOCH;
    meta->abiId = SAVE_ABI_ID_AAPCS;
    memcpy(meta->configFingerprint, "0123456789abcdef", 17);
    memcpy(meta->buildCommitShort, "12345678", 9);
    meta->checksum = ExpansionSaveMetaChecksum(meta);
}

enum SaveCompatState ClassifySaveCompatRaw(
    struct GlobalSaveInfo const *header,
    bool headerRegionBlank,
    struct ExpansionSaveMeta const *meta,
    bool metaRegionBlank)
{
    (void)headerRegionBlank;
    (void)metaRegionBlank;

    if (memcmp(meta->magic, EXPANSION_SAVE_META_MAGIC, 4) != 0)
        return SAVE_COMPAT_VALID_LEGACY_OR_VANILLA;

    if (header->magic32 != SAVEMAGIC32
        || header->magic16 != SAVEMAGIC16
        || header->checksum
            != Checksum16(header, GLOBALSIZEINFO_SIZE_FOR_CHECKSUM))
        return SAVE_COMPAT_HEADER_CORRUPT;

    if (meta->checksum != ExpansionSaveMetaChecksum(meta))
        return SAVE_COMPAT_METADATA_CORRUPT;

    if (meta->formatVersion < SAVE_FORMAT_VERSION_CURRENT)
        return SAVE_COMPAT_MIGRATABLE_OLDER;

    if (meta->formatVersion > SAVE_FORMAT_VERSION_CURRENT)
        return SAVE_COMPAT_NEWER_UNSUPPORTED;

    if (meta->compatEpoch != FE8_EXPANSION_SAVE_COMPAT_EPOCH)
        return SAVE_COMPAT_SAVE_CONFIG_INCOMPATIBLE;

    return SAVE_COMPAT_CURRENT;
}

enum SaveCompatState ClassifySramSaveCompat(void)
{
    return sSourceCompatibility;
}

bool ReadSaveBlockInfo(struct SaveBlockInfo *out, int index)
{
    struct SaveBlockInfo info;
    const void *payload;

    if (index < SAVE_ID_GAME0 || index > SAVE_ID_SUSPEND_ALT)
        return false;

    info = gSram->saveBlockInfo[index];
    if (index <= SAVE_ID_GAME2)
        payload = &gSram->gameSaveBlocks[index];
    else
        payload = &gSram->suspendSaveBlocks[index - SAVE_ID_SUSPEND];

    if (info.magic32 != SAVEMAGIC32
        || info.magic16 != SAVEMAGIC16
        || info.checksum32 != ComputeChecksum32(payload, info.size))
        return false;

    if (out != NULL)
        *out = info;

    return true;
}

void ReadGameSaveFromImage(int slot, const struct GameSaveBlock *src)
{
    gPlaySt = src->playSt;
    sLastLoadedSlot = slot;
    sGameLoadCount++;
}

void ReadSuspendSaveFromImage(
    int resolvedSlot,
    const struct SuspendSaveBlock *src,
    const struct GameSaveBlock *backingGame)
{
    (void)backingGame;
    gPlaySt = src->playSt;
    sLastLoadedSlot = resolvedSlot;
    sSuspendLoadCount++;
}
