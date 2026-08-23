#include "global.h"
#include "expansion_debugtools.h"

#if FE8_EXPANSION_DEBUGTOOLS_ENABLED && !defined(FE8_ARCHIVAL_BUILD)

#include <stddef.h>
#include <string.h>

#include "agb_sram.h"
#include "bmlib.h"
#include "bmsave.h"
#include "proc.h"
#include "save_format.h"
#include "sram-layout.h"
#include "debug_save_fixture_internal.h"

extern u8 sGameStartSaveBuf[0x8000];
extern struct ProcCmd CONST_DATA gProcScr_TitleScreen[];

#define DEBUG_SAVE_FIXTURE_SCHEMA_VERSION 1
#define DEBUG_SAVE_FIXTURE_FNV_OFFSET_LO 0x84222325u
#define DEBUG_SAVE_FIXTURE_FNV_OFFSET_HI 0xCBF29CE4u
#define DEBUG_SAVE_FIXTURE_FNV_PRIME 0x00000100000001B3ULL

struct DebugSaveFixtureState
{
    enum DebugSaveFixturePhase phase;
    enum DebugSaveFixtureResult lastResult;
    u32 generationCounter;
    struct DebugSaveFixturePreview preview;
    struct GlobalSaveInfo fixtureGlobal;
};

SECTION("debug_save_fixture_data") static struct DebugSaveFixtureState
    sDebugSaveFixtureState = {0};
SECTION("debug_save_fixture_data") struct DebugSaveFixtureProbe
    gDebugSaveFixtureProbe = {0};

static struct SaveBlocks *DebugSaveFixture_GetImage(void)
{
    return (struct SaveBlocks *)sGameStartSaveBuf;
}

static void DebugSaveFixture_SetResult(enum DebugSaveFixtureResult result)
{
    sDebugSaveFixtureState.lastResult = result;
    gDebugSaveFixtureProbe.lastResult = result;
}

static void DebugSaveFixture_SetPhase(enum DebugSaveFixturePhase phase)
{
    sDebugSaveFixtureState.phase = phase;
    gDebugSaveFixtureProbe.phase = phase;
}

static void DebugSaveFixture_HashUpdate(u32 *lo, u32 *hi, const u8 *data, u32 size)
{
    unsigned long long hash;
    u32 i;

    hash = ((unsigned long long)*hi << 32) | *lo;

    for (i = 0; i < size; ++i)
    {
        hash ^= data[i];
        hash *= DEBUG_SAVE_FIXTURE_FNV_PRIME;
    }

    *lo = (u32)hash;
    *hi = (u32)(hash >> 32);
}

static void DebugSaveFixture_HashBuffer(
    const void *data,
    u32 size,
    u32 *outLo,
    u32 *outHi)
{
    u32 lo = DEBUG_SAVE_FIXTURE_FNV_OFFSET_LO;
    u32 hi = DEBUG_SAVE_FIXTURE_FNV_OFFSET_HI;

    DebugSaveFixture_HashUpdate(&lo, &hi, data, size);

    *outLo = lo;
    *outHi = hi;
}

static void DebugSaveFixture_HashSram(u32 *outLo, u32 *outHi)
{
    u32 lo = DEBUG_SAVE_FIXTURE_FNV_OFFSET_LO;
    u32 hi = DEBUG_SAVE_FIXTURE_FNV_OFFSET_HI;
    u32 offset;

    for (offset = 0; offset < CART_SRAM_SIZE; offset += sizeof(gGenericBuffer))
    {
        u32 size = sizeof(gGenericBuffer);

        if (size > CART_SRAM_SIZE - offset)
            size = CART_SRAM_SIZE - offset;

        ReadSramFast((const u8 *)gSram + offset, gGenericBuffer, size);
        DebugSaveFixture_HashUpdate(&lo, &hi, gGenericBuffer, size);
    }

    *outLo = lo;
    *outHi = hi;
}

static void *DebugSaveFixture_GetBlockAddress(struct SaveBlocks *image, int index)
{
    switch (index)
    {
    case SAVE_ID_GAME0:
    case SAVE_ID_GAME1:
    case SAVE_ID_GAME2:
        return &image->gameSaveBlocks[index - SAVE_ID_GAME0];

    case SAVE_ID_SUSPEND:
    case SAVE_ID_SUSPEND_ALT:
        return &image->suspendSaveBlocks[index - SAVE_ID_SUSPEND];

    default:
        return NULL;
    }
}

static u16 DebugSaveFixture_GetBlockOffset(int index)
{
    switch (index)
    {
    case SAVE_ID_GAME0:
        return (u16)offsetof(struct SaveBlocks, gameSaveBlocks[0]);

    case SAVE_ID_GAME1:
        return (u16)offsetof(struct SaveBlocks, gameSaveBlocks[1]);

    case SAVE_ID_GAME2:
        return (u16)offsetof(struct SaveBlocks, gameSaveBlocks[2]);

    case SAVE_ID_SUSPEND:
        return (u16)offsetof(struct SaveBlocks, suspendSaveBlocks[0]);

    case SAVE_ID_SUSPEND_ALT:
        return (u16)offsetof(struct SaveBlocks, suspendSaveBlocks[1]);

    default:
        return 0;
    }
}

static u16 DebugSaveFixture_GetBlockSize(int index)
{
    switch (index)
    {
    case SAVE_ID_GAME0:
    case SAVE_ID_GAME1:
    case SAVE_ID_GAME2:
        return sizeof(struct GameSaveBlock);

    case SAVE_ID_SUSPEND:
    case SAVE_ID_SUSPEND_ALT:
        return sizeof(struct SuspendSaveBlock);

    default:
        return 0;
    }
}

static u8 DebugSaveFixture_GetBlockKind(int index)
{
    switch (index)
    {
    case SAVE_ID_GAME0:
    case SAVE_ID_GAME1:
    case SAVE_ID_GAME2:
        return SAVEBLOCK_KIND_GAME;

    case SAVE_ID_SUSPEND:
    case SAVE_ID_SUSPEND_ALT:
        return SAVEBLOCK_KIND_SUSPEND;

    default:
        return (u8)SAVEBLOCK_KIND_INVALID;
    }
}

static int DebugSaveFixture_ValidateBlock(
    struct SaveBlocks *image,
    int index,
    struct SaveBlockInfo *out)
{
    struct SaveBlockInfo *info;
    void *payload;
    u16 size;

    if (index < SAVE_ID_GAME0 || index > SAVE_ID_SUSPEND_ALT)
        return FALSE;

    info = &image->saveBlockInfo[index];
    payload = DebugSaveFixture_GetBlockAddress(image, index);
    size = DebugSaveFixture_GetBlockSize(index);

    if (payload == NULL
        || info->magic32 != SAVEMAGIC32
        || info->magic16 != SAVEMAGIC16
        || info->kind != DebugSaveFixture_GetBlockKind(index)
        || info->offset != DebugSaveFixture_GetBlockOffset(index)
        || info->size != size
        || info->checksum32 != ComputeChecksum32(payload, size))
        return FALSE;

    if (out != NULL)
        *out = *info;

    return TRUE;
}

static int DebugSaveFixture_ReadPhysicalBlockInfo(
    int index,
    struct SaveBlockInfo *out)
{
    struct SaveBlockInfo info;
    u16 size;

    if (index < SAVE_ID_GAME0 || index > SAVE_ID_SUSPEND_ALT)
        return FALSE;

    ReadSramFast(
        &gSram->saveBlockInfo[index],
        &info,
        sizeof(info));
    size = DebugSaveFixture_GetBlockSize(index);

    if (info.magic32 != SAVEMAGIC32
        || info.magic16 != SAVEMAGIC16
        || info.kind != DebugSaveFixture_GetBlockKind(index)
        || info.offset != DebugSaveFixture_GetBlockOffset(index)
        || info.size != size)
        return FALSE;

    ReadSramFast(
        (const u8 *)gSram + info.offset,
        gGenericBuffer,
        size);

    if (info.checksum32 != ComputeChecksum32(
        (const u32 *)gGenericBuffer,
        size))
        return FALSE;

    if (out != NULL)
        *out = info;

    return TRUE;
}

static void DebugSaveFixture_WriteBlockInfo(struct SaveBlocks *image, int index)
{
    struct SaveBlockInfo *info = &image->saveBlockInfo[index];
    void *payload = DebugSaveFixture_GetBlockAddress(image, index);

    memset(info, 0, sizeof(*info));
    info->magic32 = SAVEMAGIC32;
    info->magic16 = SAVEMAGIC16;
    info->kind = DebugSaveFixture_GetBlockKind(index);
    info->offset = DebugSaveFixture_GetBlockOffset(index);
    info->size = DebugSaveFixture_GetBlockSize(index);
    info->checksum32 = ComputeChecksum32(payload, info->size);
}

static int DebugSaveFixture_TargetEquals(
    const struct DebugSaveFixtureTarget *a,
    const struct DebugSaveFixtureTarget *b)
{
    return a != NULL
        && b != NULL
        && a->generation == b->generation
        && a->sourceHashLo == b->sourceHashLo
        && a->sourceHashHi == b->sourceHashHi
        && a->sourceBlockChecksum == b->sourceBlockChecksum
        && a->fixtureImageChecksum == b->fixtureImageChecksum
        && a->sourceKind == b->sourceKind
        && a->sourceGameSlot == b->sourceGameSlot
        && a->resolvedSuspendSlot == b->resolvedSuspendSlot
        && a->backingGameSlot == b->backingGameSlot;
}

static void DebugSaveFixture_SetTacticianName(struct PlaySt *playSt)
{
    static const char sFixtureName[] = "FIXTURE";

    memset(playSt->playerName, 0, sizeof(playSt->playerName));
    memcpy(playSt->playerName, sFixtureName, sizeof(sFixtureName));
}

static void DebugSaveFixture_ApplyPlayStOverrides(
    struct PlaySt *playSt,
    const struct DebugSaveFixtureOverrides *overrides)
{
    playSt->unk_2C_2 = overrides->completionCount;

    if (overrides->tacticianMode == DEBUG_SAVE_FIXTURE_TACTICIAN_FIXED_MARKER)
        DebugSaveFixture_SetTacticianName(playSt);
}

static void DebugSaveFixture_BuildGlobalHeader(
    struct SaveBlocks *image,
    const struct GlobalSaveInfo *source,
    const struct DebugSaveFixtureTarget *target,
    const struct DebugSaveFixtureOverrides *overrides)
{
    struct GlobalSaveInfo *header = &image->globalSaveInfo;
    int i;

    memset(header, 0, sizeof(*header));
    memcpy(header->name, source->name, sizeof(header->name));
    header->magic32 = source->magic32;
    header->magic16 = source->magic16;
    header->last_game_save_id = target->backingGameSlot;

    if (target->resolvedSuspendSlot == SAVE_ID_SUSPEND_ALT)
        header->last_suspend_slot = 1;
    else
        header->last_suspend_slot = 0;

    for (i = 0; i < overrides->completionCount; ++i)
        header->cleared_playthroughs[i] = i + 1;

    header->completed = overrides->completionCount != 0;
    header->checksum = Checksum16(header, GLOBALSIZEINFO_SIZE_FOR_CHECKSUM);
}

static enum SaveCompatState DebugSaveFixture_ClassifyImage(struct SaveBlocks *image)
{
    return ClassifySaveCompatRaw(
        &image->globalSaveInfo,
        FALSE,
        &image->expansionSaveMeta,
        FALSE);
}

static int DebugSaveFixture_ResolveLatestSuspend(
    struct SaveBlocks *image,
    int *outSuspend,
    int *outBacking,
    struct SaveBlockInfo *outInfo)
{
    int first;
    int second;
    int selected;
    int backing;

    first = image->globalSaveInfo.last_suspend_slot == 1
        ? SAVE_ID_SUSPEND_ALT
        : SAVE_ID_SUSPEND;
    second = first == SAVE_ID_SUSPEND
        ? SAVE_ID_SUSPEND_ALT
        : SAVE_ID_SUSPEND;

    if (DebugSaveFixture_ValidateBlock(image, first, outInfo))
        selected = first;
    else if (DebugSaveFixture_ValidateBlock(image, second, outInfo))
        selected = second;
    else
        return FALSE;

    backing = image->suspendSaveBlocks[selected - SAVE_ID_SUSPEND].playSt.gameSaveSlot;
    if (backing < SAVE_ID_GAME0 || backing > SAVE_ID_GAME2)
        return FALSE;

    if (!DebugSaveFixture_ValidateBlock(image, backing, NULL))
        return FALSE;

    *outSuspend = selected;
    *outBacking = backing;
    return TRUE;
}

static void DebugSaveFixture_ClearUnselectedPayloads(
    struct SaveBlocks *image,
    int selected,
    int backing)
{
    int i;

    memset(image->saveBlockInfo, 0xFF, sizeof(image->saveBlockInfo));

    for (i = SAVE_ID_SUSPEND; i <= SAVE_ID_SUSPEND_ALT; ++i)
        if (i != selected)
            memset(
                &image->suspendSaveBlocks[i - SAVE_ID_SUSPEND],
                0xFF,
                sizeof(struct SuspendSaveBlock));

    for (i = SAVE_ID_GAME0; i <= SAVE_ID_GAME2; ++i)
        if (i != selected && i != backing)
            memset(
                &image->gameSaveBlocks[i - SAVE_ID_GAME0],
                0xFF,
                sizeof(struct GameSaveBlock));

    memset(
        &image->multiArenaBlock,
        0xFF,
        CART_SRAM_SIZE - offsetof(struct SaveBlocks, multiArenaBlock));
}

static int DebugSaveFixture_BuildSanitizedImage(
    struct SaveBlocks *image,
    const struct GlobalSaveInfo *sourceHeader,
    struct DebugSaveFixtureTarget *target,
    const struct DebugSaveFixtureOverrides *overrides)
{
    struct ExpansionSaveMeta meta;
    struct PlaySt *selectedPlaySt;
    struct PlaySt *backingPlaySt;

    DebugSaveFixture_ClearUnselectedPayloads(
        image,
        target->sourceKind == DEBUG_SAVE_FIXTURE_SOURCE_SUSPEND
            ? target->resolvedSuspendSlot
            : target->sourceGameSlot,
        target->backingGameSlot);

    BuildCurrentExpansionSaveMeta(&meta);
    image->expansionSaveMeta = meta;

    if (target->sourceKind == DEBUG_SAVE_FIXTURE_SOURCE_GAME)
    {
        selectedPlaySt =
            &image->gameSaveBlocks[target->sourceGameSlot - SAVE_ID_GAME0].playSt;
        selectedPlaySt->gameSaveSlot = target->sourceGameSlot;
        DebugSaveFixture_ApplyPlayStOverrides(selectedPlaySt, overrides);
        DebugSaveFixture_WriteBlockInfo(image, target->sourceGameSlot);
    }
    else
    {
        selectedPlaySt =
            &image->suspendSaveBlocks[
                target->resolvedSuspendSlot - SAVE_ID_SUSPEND].playSt;
        backingPlaySt =
            &image->gameSaveBlocks[target->backingGameSlot - SAVE_ID_GAME0].playSt;

        selectedPlaySt->gameSaveSlot = target->backingGameSlot;
        backingPlaySt->gameSaveSlot = target->backingGameSlot;
        DebugSaveFixture_ApplyPlayStOverrides(selectedPlaySt, overrides);
        DebugSaveFixture_ApplyPlayStOverrides(backingPlaySt, overrides);
        DebugSaveFixture_WriteBlockInfo(image, target->resolvedSuspendSlot);
        DebugSaveFixture_WriteBlockInfo(image, target->backingGameSlot);
    }

    DebugSaveFixture_BuildGlobalHeader(image, sourceHeader, target, overrides);

    if (DebugSaveFixture_ClassifyImage(image) != SAVE_COMPAT_CURRENT)
        return FALSE;

    if (target->sourceKind == DEBUG_SAVE_FIXTURE_SOURCE_GAME)
    {
        if (!DebugSaveFixture_ValidateBlock(
            image,
            target->sourceGameSlot,
            NULL))
            return FALSE;
    }
    else
    {
        if (!DebugSaveFixture_ValidateBlock(
                image,
                target->resolvedSuspendSlot,
                NULL)
            || !DebugSaveFixture_ValidateBlock(
                image,
                target->backingGameSlot,
                NULL))
            return FALSE;
    }

    target->fixtureImageChecksum =
        ComputeChecksum32((const u32 *)image, CART_SRAM_SIZE);
    return TRUE;
}

static void DebugSaveFixture_UpdateProbe(void)
{
    const struct DebugSaveFixturePreview *preview =
        &sDebugSaveFixtureState.preview;
    const struct SaveBlocks *image = DebugSaveFixture_GetImage();

    gDebugSaveFixtureProbe.generation = preview->target.generation;
    gDebugSaveFixtureProbe.sourceKind = preview->target.sourceKind;
    gDebugSaveFixtureProbe.sourceSlot = preview->target.sourceGameSlot;
    gDebugSaveFixtureProbe.resolvedSuspendSlot =
        preview->target.resolvedSuspendSlot;
    gDebugSaveFixtureProbe.backingGameSlot = preview->target.backingGameSlot;
    gDebugSaveFixtureProbe.sourceHashLo = preview->target.sourceHashLo;
    gDebugSaveFixtureProbe.sourceHashHi = preview->target.sourceHashHi;
    gDebugSaveFixtureProbe.fixtureImageChecksum =
        preview->target.fixtureImageChecksum;
    gDebugSaveFixtureProbe.fixtureCompatibility =
        preview->fixtureCompatibility;
    gDebugSaveFixtureProbe.fixtureCompletionCount =
        preview->overrides.completionCount;
    gDebugSaveFixtureProbe.imageMagic =
        (u32)image->expansionSaveMeta.magic[0]
        | ((u32)image->expansionSaveMeta.magic[1] << 8)
        | ((u32)image->expansionSaveMeta.magic[2] << 16)
        | ((u32)image->expansionSaveMeta.magic[3] << 24);
    gDebugSaveFixtureProbe.imageFormatEpoch =
        (u32)image->expansionSaveMeta.formatVersion
        | ((u32)image->expansionSaveMeta.compatEpoch << 16);
}

static enum DebugSaveFixtureResult DebugSaveFixture_Prepare(
    enum DebugSaveFixtureSourceKind kind,
    int gameSlot,
    const struct DebugSaveFixtureOverrides *overrides,
    struct DebugSaveFixturePreview *outPreview)
{
    struct SaveBlocks *image = DebugSaveFixture_GetImage();
    struct GlobalSaveInfo sourceHeader;
    struct ExpansionSaveMeta sourceMeta;
    struct SaveBlockInfo sourceInfo;
    struct DebugSaveFixtureTarget target;
    struct DebugSaveFixturePreview preview;
    enum SaveCompatState sourceCompatibility;
    int suspendSlot = SAVE_ID_MAX;
    int backingSlot = SAVE_ID_MAX;
    u32 generation;

    if (Proc_Find(gProcScr_TitleScreen) == NULL)
    {
        DebugSaveFixture_SetResult(DEBUG_SAVE_FIXTURE_ERR_NOT_TITLE);
        return DEBUG_SAVE_FIXTURE_ERR_NOT_TITLE;
    }

    if (DebugTools_IsChapter2LaunchPending()
        || DebugTools_IsChapter4PrepLaunchPending()
        || sDebugSaveFixtureState.phase == DEBUG_SAVE_FIXTURE_ACTIVE
        || sDebugSaveFixtureState.phase
            == DEBUG_SAVE_FIXTURE_PENDING_CONTINUE)
    {
        DebugSaveFixture_SetResult(DEBUG_SAVE_FIXTURE_ERR_BUSY);
        return DEBUG_SAVE_FIXTURE_ERR_BUSY;
    }

    if (sDebugSaveFixtureState.phase == DEBUG_SAVE_FIXTURE_PREVIEW
        || sDebugSaveFixtureState.phase == DEBUG_SAVE_FIXTURE_ARMED)
        DebugSaveFixture_Abort(DEBUG_SAVE_FIXTURE_ABORT_FORCED_TEARDOWN);

    if (overrides == NULL
        || overrides->completionCount > MAX_SAVED_GAME_CLEARS
        || overrides->tacticianMode > DEBUG_SAVE_FIXTURE_TACTICIAN_FIXED_MARKER)
    {
        DebugSaveFixture_SetResult(
            DEBUG_SAVE_FIXTURE_ERR_SOURCE_TARGET_INVALID);
        return DEBUG_SAVE_FIXTURE_ERR_SOURCE_TARGET_INVALID;
    }

    sourceCompatibility = ClassifySramSaveCompat();
    if (sourceCompatibility != SAVE_COMPAT_CURRENT)
    {
        DebugSaveFixture_SetResult(DEBUG_SAVE_FIXTURE_ERR_SOURCE_NOT_CURRENT);
        return DEBUG_SAVE_FIXTURE_ERR_SOURCE_NOT_CURRENT;
    }

    generation = sDebugSaveFixtureState.generationCounter + 1;
    if (generation == 0)
        generation = 1;

    memset(&preview, 0, sizeof(preview));
    memset(&target, 0, sizeof(target));

    ReadSramFast(gSram, image, CART_SRAM_SIZE);
    sourceHeader = image->globalSaveInfo;
    sourceMeta = image->expansionSaveMeta;

    if (DebugSaveFixture_ClassifyImage(image) != SAVE_COMPAT_CURRENT)
    {
        memset(image, 0, CART_SRAM_SIZE);
        DebugSaveFixture_SetResult(DEBUG_SAVE_FIXTURE_ERR_SOURCE_CHANGED);
        return DEBUG_SAVE_FIXTURE_ERR_SOURCE_CHANGED;
    }

    if (kind == DEBUG_SAVE_FIXTURE_SOURCE_GAME)
    {
        if (gameSlot < SAVE_ID_GAME0
            || gameSlot > SAVE_ID_GAME2
            || !DebugSaveFixture_ValidateBlock(image, gameSlot, &sourceInfo))
        {
            memset(image, 0, CART_SRAM_SIZE);
            DebugSaveFixture_SetResult(
                DEBUG_SAVE_FIXTURE_ERR_SOURCE_TARGET_INVALID);
            return DEBUG_SAVE_FIXTURE_ERR_SOURCE_TARGET_INVALID;
        }

        backingSlot = gameSlot;
    }
    else if (!DebugSaveFixture_ResolveLatestSuspend(
        image,
        &suspendSlot,
        &backingSlot,
        &sourceInfo))
    {
        memset(image, 0, CART_SRAM_SIZE);
        DebugSaveFixture_SetResult(
            DEBUG_SAVE_FIXTURE_ERR_SOURCE_TARGET_INVALID);
        return DEBUG_SAVE_FIXTURE_ERR_SOURCE_TARGET_INVALID;
    }

    target.generation = generation;
    target.sourceBlockChecksum = sourceInfo.checksum32;
    target.sourceKind = kind;
    target.sourceGameSlot =
        kind == DEBUG_SAVE_FIXTURE_SOURCE_GAME
            ? gameSlot
            : DEBUG_SAVE_FIXTURE_GAME_NONE;
    target.resolvedSuspendSlot =
        kind == DEBUG_SAVE_FIXTURE_SOURCE_SUSPEND
            ? suspendSlot
            : DEBUG_SAVE_FIXTURE_SUSPEND_NONE;
    target.backingGameSlot = backingSlot;
    DebugSaveFixture_HashBuffer(
        image,
        CART_SRAM_SIZE,
        &target.sourceHashLo,
        &target.sourceHashHi);

    if (!DebugSaveFixture_BuildSanitizedImage(
        image,
        &sourceHeader,
        &target,
        overrides))
    {
        memset(image, 0, CART_SRAM_SIZE);
        DebugSaveFixture_SetResult(DEBUG_SAVE_FIXTURE_ERR_IMAGE_INVALID);
        return DEBUG_SAVE_FIXTURE_ERR_IMAGE_INVALID;
    }

    preview.target = target;
    preview.provenance.schemaVersion = DEBUG_SAVE_FIXTURE_SCHEMA_VERSION;
    preview.provenance.formatVersion = sourceMeta.formatVersion;
    preview.provenance.compatEpoch = sourceMeta.compatEpoch;
    preview.provenance.sourceAbiId = sourceMeta.abiId;
    memcpy(
        preview.provenance.sourceConfigFingerprint,
        sourceMeta.configFingerprint,
        sizeof(preview.provenance.sourceConfigFingerprint));
    memcpy(
        preview.provenance.sourceBuildCommitShort,
        sourceMeta.buildCommitShort,
        sizeof(preview.provenance.sourceBuildCommitShort));
    preview.sourceCompatibility = sourceCompatibility;
    preview.fixtureCompatibility = DebugSaveFixture_ClassifyImage(image);
    preview.overrides = *overrides;

    memset(&sDebugSaveFixtureState.preview, 0, sizeof(sDebugSaveFixtureState.preview));
    sDebugSaveFixtureState.preview = preview;
    sDebugSaveFixtureState.generationCounter = generation;
    DebugSaveFixture_SetPhase(DEBUG_SAVE_FIXTURE_PREVIEW);
    DebugSaveFixture_SetResult(DEBUG_SAVE_FIXTURE_OK);
    gDebugSaveFixtureProbe.prepareCount++;
    DebugSaveFixture_UpdateProbe();

    if (outPreview != NULL)
        *outPreview = preview;

    return DEBUG_SAVE_FIXTURE_OK;
}

static int DebugSaveFixture_RevalidateSource(
    const struct DebugSaveFixtureTarget *target)
{
    struct SaveBlockInfo info;
    struct PlaySt playSt;
    u32 lo;
    u32 hi;
    int index;

    if (ClassifySramSaveCompat() != SAVE_COMPAT_CURRENT)
        return FALSE;

    index = target->sourceKind == DEBUG_SAVE_FIXTURE_SOURCE_GAME
        ? target->sourceGameSlot
        : target->resolvedSuspendSlot;

    if (!DebugSaveFixture_ReadPhysicalBlockInfo(index, &info)
        || info.checksum32 != target->sourceBlockChecksum)
        return FALSE;

    if (target->sourceKind == DEBUG_SAVE_FIXTURE_SOURCE_SUSPEND)
    {
        if (!DebugSaveFixture_ReadPhysicalBlockInfo(
            target->backingGameSlot,
            &info))
            return FALSE;

        ReadSramFast(
            &gSram->suspendSaveBlocks[
                target->resolvedSuspendSlot - SAVE_ID_SUSPEND].playSt,
            &playSt,
            sizeof(playSt));

        if (playSt.gameSaveSlot != target->backingGameSlot)
            return FALSE;
    }

    DebugSaveFixture_HashSram(&lo, &hi);
    return lo == target->sourceHashLo && hi == target->sourceHashHi;
}

static int DebugSaveFixture_ValidatePreparedImage(
    const struct DebugSaveFixtureTarget *target)
{
    struct SaveBlocks *image = DebugSaveFixture_GetImage();
    u32 checksum;

    if (DebugSaveFixture_ClassifyImage(image) != SAVE_COMPAT_CURRENT)
        return FALSE;

    if (target->sourceKind == DEBUG_SAVE_FIXTURE_SOURCE_GAME)
    {
        if (!DebugSaveFixture_ValidateBlock(
            image,
            target->sourceGameSlot,
            NULL))
            return FALSE;
    }
    else if (!DebugSaveFixture_ValidateBlock(
            image,
            target->resolvedSuspendSlot,
            NULL)
        || !DebugSaveFixture_ValidateBlock(
            image,
            target->backingGameSlot,
            NULL))
    {
        return FALSE;
    }

    checksum = ComputeChecksum32((const u32 *)image, CART_SRAM_SIZE);
    return checksum == target->fixtureImageChecksum;
}

enum DebugSaveFixtureResult DebugSaveFixture_PrepareGame(
    enum DebugSaveFixtureGameSlot slot,
    const struct DebugSaveFixtureOverrides *overrides,
    struct DebugSaveFixturePreview *preview)
{
    return DebugSaveFixture_Prepare(
        DEBUG_SAVE_FIXTURE_SOURCE_GAME,
        slot,
        overrides,
        preview);
}

enum DebugSaveFixtureResult DebugSaveFixture_PrepareLatestSuspend(
    const struct DebugSaveFixtureOverrides *overrides,
    struct DebugSaveFixturePreview *preview)
{
    return DebugSaveFixture_Prepare(
        DEBUG_SAVE_FIXTURE_SOURCE_SUSPEND,
        DEBUG_SAVE_FIXTURE_GAME_NONE,
        overrides,
        preview);
}

enum DebugSaveFixtureResult DebugSaveFixture_Arm(
    const struct DebugSaveFixtureTarget *target)
{
    if (sDebugSaveFixtureState.phase != DEBUG_SAVE_FIXTURE_PREVIEW)
    {
        DebugSaveFixture_SetResult(
            DEBUG_SAVE_FIXTURE_ERR_CONFIRMATION_ORDER);
        return DEBUG_SAVE_FIXTURE_ERR_CONFIRMATION_ORDER;
    }

    if (!DebugSaveFixture_TargetEquals(
        target,
        &sDebugSaveFixtureState.preview.target))
    {
        DebugSaveFixture_SetResult(DEBUG_SAVE_FIXTURE_ERR_STALE_TARGET);
        return DEBUG_SAVE_FIXTURE_ERR_STALE_TARGET;
    }

    if (!DebugSaveFixture_RevalidateSource(target))
    {
        DebugSaveFixture_Abort(DEBUG_SAVE_FIXTURE_ABORT_SOURCE_CHANGED);
        DebugSaveFixture_SetResult(DEBUG_SAVE_FIXTURE_ERR_SOURCE_CHANGED);
        return DEBUG_SAVE_FIXTURE_ERR_SOURCE_CHANGED;
    }

    if (!DebugSaveFixture_ValidatePreparedImage(target))
    {
        DebugSaveFixture_Abort(DEBUG_SAVE_FIXTURE_ABORT_FORCED_TEARDOWN);
        DebugSaveFixture_SetResult(DEBUG_SAVE_FIXTURE_ERR_IMAGE_INVALID);
        return DEBUG_SAVE_FIXTURE_ERR_IMAGE_INVALID;
    }

    DebugSaveFixture_SetPhase(DEBUG_SAVE_FIXTURE_ARMED);
    DebugSaveFixture_SetResult(DEBUG_SAVE_FIXTURE_OK);
    gDebugSaveFixtureProbe.armCount++;
    return DEBUG_SAVE_FIXTURE_OK;
}

enum DebugSaveFixtureResult DebugSaveFixture_RequestContinue(
    const struct DebugSaveFixtureTarget *target)
{
    if (sDebugSaveFixtureState.phase != DEBUG_SAVE_FIXTURE_ARMED)
    {
        DebugSaveFixture_SetResult(
            DEBUG_SAVE_FIXTURE_ERR_CONFIRMATION_ORDER);
        return DEBUG_SAVE_FIXTURE_ERR_CONFIRMATION_ORDER;
    }

    if (!DebugSaveFixture_TargetEquals(
        target,
        &sDebugSaveFixtureState.preview.target))
    {
        DebugSaveFixture_SetResult(DEBUG_SAVE_FIXTURE_ERR_STALE_TARGET);
        return DEBUG_SAVE_FIXTURE_ERR_STALE_TARGET;
    }

    if (!DebugSaveFixture_RevalidateSource(target))
    {
        DebugSaveFixture_Abort(DEBUG_SAVE_FIXTURE_ABORT_SOURCE_CHANGED);
        DebugSaveFixture_SetResult(DEBUG_SAVE_FIXTURE_ERR_SOURCE_CHANGED);
        return DEBUG_SAVE_FIXTURE_ERR_SOURCE_CHANGED;
    }

    if (!DebugSaveFixture_ValidatePreparedImage(target))
    {
        DebugSaveFixture_Abort(DEBUG_SAVE_FIXTURE_ABORT_FORCED_TEARDOWN);
        DebugSaveFixture_SetResult(DEBUG_SAVE_FIXTURE_ERR_IMAGE_INVALID);
        return DEBUG_SAVE_FIXTURE_ERR_IMAGE_INVALID;
    }

    DebugSaveFixture_SetPhase(DEBUG_SAVE_FIXTURE_PENDING_CONTINUE);
    DebugSaveFixture_SetResult(DEBUG_SAVE_FIXTURE_OK);
    gDebugSaveFixtureProbe.continueRequestCount++;
    return DEBUG_SAVE_FIXTURE_OK;
}

void DebugSaveFixture_Abort(enum DebugSaveFixtureAbortReason reason)
{
    u32 generation = sDebugSaveFixtureState.generationCounter;
    enum DebugSaveFixturePhase phase = sDebugSaveFixtureState.phase;

    (void)reason;

    if (phase == DEBUG_SAVE_FIXTURE_PREVIEW
        || phase == DEBUG_SAVE_FIXTURE_ARMED
        || phase == DEBUG_SAVE_FIXTURE_PENDING_CONTINUE)
        memset(DebugSaveFixture_GetImage(), 0, CART_SRAM_SIZE);

    memset(&sDebugSaveFixtureState, 0, sizeof(sDebugSaveFixtureState));
    sDebugSaveFixtureState.generationCounter = generation;
    DebugSaveFixture_SetPhase(DEBUG_SAVE_FIXTURE_EMPTY);
    DebugSaveFixture_SetResult(DEBUG_SAVE_FIXTURE_OK);
    gDebugSaveFixtureProbe.abortCount++;
    gDebugSaveFixtureProbe.liveCompletionCount = 0;
}

int DebugSaveFixture_CanPrepare(void)
{
    return Proc_Find(gProcScr_TitleScreen) != NULL
        && !DebugTools_IsChapter2LaunchPending()
        && !DebugTools_IsChapter4PrepLaunchPending()
        && sDebugSaveFixtureState.phase != DEBUG_SAVE_FIXTURE_ACTIVE
        && sDebugSaveFixtureState.phase != DEBUG_SAVE_FIXTURE_PENDING_CONTINUE;
}

int DebugSaveFixture_IsContinuePending(void)
{
    return sDebugSaveFixtureState.phase
        == DEBUG_SAVE_FIXTURE_PENDING_CONTINUE;
}

int DebugSaveFixture_IsActive(void)
{
    return sDebugSaveFixtureState.phase == DEBUG_SAVE_FIXTURE_ACTIVE;
}

int DebugSaveFixture_IsPersistenceBlocked(void)
{
    return DEBUG_SAVE_FIXTURE_WRITES_BLOCKED;
}

enum DebugSaveFixturePhase DebugSaveFixture_GetPhase(void)
{
    return sDebugSaveFixtureState.phase;
}

enum DebugSaveFixtureResult DebugSaveFixture_GetLastResult(void)
{
    return sDebugSaveFixtureState.lastResult;
}

const struct DebugSaveFixturePreview *DebugSaveFixture_GetPreview(void)
{
    if (sDebugSaveFixtureState.phase == DEBUG_SAVE_FIXTURE_EMPTY)
        return NULL;

    return &sDebugSaveFixtureState.preview;
}

enum DebugSaveFixtureContinueResult DebugSaveFixture_ConsumePendingContinue(void)
{
    struct SaveBlocks *image = DebugSaveFixture_GetImage();
    struct DebugSaveFixtureTarget target;
    enum DebugSaveFixtureContinueResult result;

    if (sDebugSaveFixtureState.phase
        != DEBUG_SAVE_FIXTURE_PENDING_CONTINUE)
        return DEBUG_SAVE_FIXTURE_CONTINUE_NONE;

    target = sDebugSaveFixtureState.preview.target;

    if (!DebugSaveFixture_ValidatePreparedImage(&target))
    {
        DebugSaveFixture_Abort(DEBUG_SAVE_FIXTURE_ABORT_CONSUME_FAILED);
        DebugSaveFixture_SetResult(DEBUG_SAVE_FIXTURE_ERR_IMAGE_INVALID);
        return DEBUG_SAVE_FIXTURE_CONTINUE_FAILED;
    }

    sDebugSaveFixtureState.fixtureGlobal = image->globalSaveInfo;

    if (target.sourceKind == DEBUG_SAVE_FIXTURE_SOURCE_GAME)
    {
        ReadGameSaveFromImage(
            target.sourceGameSlot,
            &image->gameSaveBlocks[target.sourceGameSlot - SAVE_ID_GAME0]);
        result = DEBUG_SAVE_FIXTURE_CONTINUE_GAME;
    }
    else
    {
        ReadSuspendSaveFromImage(
            target.resolvedSuspendSlot,
            &image->suspendSaveBlocks[
                target.resolvedSuspendSlot - SAVE_ID_SUSPEND],
            &image->gameSaveBlocks[target.backingGameSlot - SAVE_ID_GAME0]);
        result = DEBUG_SAVE_FIXTURE_CONTINUE_SUSPEND;
    }

    DebugSaveFixture_SetPhase(DEBUG_SAVE_FIXTURE_ACTIVE);
    DebugSaveFixture_SetResult(DEBUG_SAVE_FIXTURE_OK);
    gDebugSaveFixtureProbe.continueConsumeCount++;
    gDebugSaveFixtureProbe.liveCompletionCount =
        sDebugSaveFixtureState.preview.overrides.completionCount;
    memset(image, 0, CART_SRAM_SIZE);
    return result;
}

void DebugSaveFixture_NotifyTitleScreenStarting(void)
{
    if (sDebugSaveFixtureState.phase != DEBUG_SAVE_FIXTURE_EMPTY)
        DebugSaveFixture_Abort(DEBUG_SAVE_FIXTURE_ABORT_TITLE_RETURN);
}

int DebugSaveFixture_ShouldBlockSramWrite(const void *dest, u32 size)
{
    uintptr_t start;
    uintptr_t end;
    uintptr_t sramStart;
    uintptr_t sramEnd;

    if (!DebugSaveFixture_IsPersistenceBlocked() || size == 0)
        return FALSE;

    start = (uintptr_t)dest;
    end = start + size;
    sramStart = CART_SRAM_ADDR;
    sramEnd = CART_SRAM_ADDR + CART_SRAM_SIZE;

    if (end < start || (start < sramEnd && end > sramStart))
    {
        DebugSaveFixture_RecordBlockedWrite(
            DEBUG_SAVE_FIXTURE_WRITE_LOW_LEVEL);
        return TRUE;
    }

    return FALSE;
}

int DebugSaveFixture_RecordBlockedWrite(enum DebugSaveFixtureWriteKind kind)
{
    if (!DebugSaveFixture_IsPersistenceBlocked())
        return FALSE;

    gDebugSaveFixtureProbe.blockedWriteCount++;
    gDebugSaveFixtureProbe.lastBlockedWriteKind = kind;
    DebugSaveFixture_SetResult(
        DEBUG_SAVE_FIXTURE_ERR_PERSISTENCE_BLOCKED);
    return TRUE;
}

bool8 DebugSaveFixture_TryReadGlobalSaveInfo(struct GlobalSaveInfo *out)
{
    if (!DebugSaveFixture_IsActive())
        return FALSE;

    if (out != NULL)
        *out = sDebugSaveFixtureState.fixtureGlobal;

    return TRUE;
}

#endif
