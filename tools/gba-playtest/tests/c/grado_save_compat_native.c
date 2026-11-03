#include "global.h"

#include "agb_sram.h"
#include "bmsave.h"
#include "eventinfo.h"

#include <string.h>

struct GradoSaveCompatRecord
{
    u32 input;
    u32 state;
    u32 preserved;
};

struct GradoSaveCompatProbe
{
    u32 phase;
    u32 count;
    u32 epoch;
    u32 envelope;
    struct GradoSaveCompatRecord records[13];
};

struct GradoSaveFlagsRecord
{
    u32 slot;
    u32 pattern;
    u32 stored;
    u32 bounded;
    u32 restored;
    u32 otherScope;
    u32 readonlyView;
    u32 clobbered;
};

struct GradoSaveFlagsProbe
{
    u32 phase;
    u32 count;
    u32 permanentBytes;
    u32 chapterBytes;
    u32 permanentAddress;
    u32 chapterAddress;
    struct GradoSaveFlagsRecord records[20];
};

static union
{
    struct SaveBlocks native;
    u8 bytes[CART_SRAM_SIZE];
} sSaveImage;
static u8 sBefore[CART_SRAM_SIZE];
volatile struct GradoSaveCompatProbe gGradoSaveCompatProbe;
volatile struct GradoSaveFlagsProbe gGradoSaveFlagsProbe;
extern bool gBoolSramWorking;

static void PrepareBlank(u8 fill)
{
    memset(sSaveImage.bytes, fill, sizeof(sSaveImage.bytes));
    SramInit();
}

static void PrepareHeader(void)
{
    struct GlobalSaveInfo * header;

    PrepareBlank(0);
    header = &gSram->globalSaveInfo;
    memcpy(header->name, "AGB-FE9", sizeof("AGB-FE9"));
    header->magic32 = SAVEMAGIC32;
    header->magic16 = SAVEMAGIC16;
    header->checksum = Checksum16(header, GLOBALSIZEINFO_SIZE_FOR_CHECKSUM);
}

static void PrepareCurrent(void)
{
    PrepareHeader();
    BuildCurrentExpansionSaveMeta(&gSram->expansionSaveMeta);
}

static void RefreshMetaChecksum(void)
{
    gSram->expansionSaveMeta.checksum = ExpansionSaveMetaChecksum(&gSram->expansionSaveMeta);
}

static void Record(unsigned int input)
{
    volatile struct GradoSaveCompatRecord * record;

    memcpy(sBefore, sSaveImage.bytes, sizeof(sBefore));
    record = &gGradoSaveCompatProbe.records[gGradoSaveCompatProbe.count];
    record->input = input;
    record->state = ClassifySramSaveCompat();
    record->preserved = memcmp(sBefore, sSaveImage.bytes, sizeof(sBefore)) == 0;
    gGradoSaveCompatProbe.count++;
}

static int BytesAre(const u8 * bytes, unsigned int size, u8 value)
{
    unsigned int i;

    for (i = 0; i < size; ++i)
        if (bytes[i] != value)
            return 0;
    return 1;
}

static void RecordFlagSave(unsigned int slot, unsigned int pattern)
{
    static const u8 seeds[] = { 0, 0xFF, 0xAA, 0x55 };
    u8 permanent[25];
    u8 chapter[5];
    struct
    {
        volatile u8 before;
        u8 bytes[25];
        volatile u8 after;
    } view;
    u8 * savedPermanent;
    u8 * savedChapter;
    volatile u8 * permanentGuard;
    volatile u8 * chapterGuard;
    unsigned int i;
    volatile struct GradoSaveFlagsRecord * record;

    for (i = 0; i < sizeof(permanent); ++i)
        permanent[i] = seeds[pattern] ^ (i * 29) ^ (slot * 3);
    for (i = 0; i < sizeof(chapter); ++i)
        chapter[i] = (u8)~seeds[pattern] ^ (i * 17) ^ (slot * 3);
    memcpy(GetPermanentFlagBits(), permanent, sizeof(permanent));
    memcpy(GetChapterFlagBits(), chapter, sizeof(chapter));
    permanentGuard = (volatile u8 *)((uintptr_t)GetPermanentFlagBits() + sizeof(permanent));
    chapterGuard = (volatile u8 *)((uintptr_t)GetChapterFlagBits() + sizeof(chapter));
    *permanentGuard = 0xD6;
    *chapterGuard = 0x6D;

    savedPermanent = slot < 3 ? gSram->gameSaveBlocks[slot].permanentFlags
        : gSram->suspendSaveBlocks[slot - 3].permanentFlags;
    savedChapter = slot < 3 ? 0 : gSram->suspendSaveBlocks[slot - 3].chapterFlags;
    WritePermanentFlags(savedPermanent);
    if (savedChapter != 0)
        WriteChapterFlags(savedChapter);
    memcpy(sBefore + (savedPermanent - sSaveImage.bytes), permanent, sizeof(permanent));
    if (savedChapter != 0)
        memcpy(sBefore + (savedChapter - sSaveImage.bytes), chapter, sizeof(chapter));

    record = &gGradoSaveFlagsProbe.records[gGradoSaveFlagsProbe.count];
    record->slot = slot;
    record->pattern = pattern;
    record->stored = memcmp(savedPermanent, permanent, sizeof(permanent)) == 0
        && (savedChapter == 0 || memcmp(savedChapter, chapter, sizeof(chapter)) == 0);
    record->bounded = memcmp(sBefore, sSaveImage.bytes, sizeof(sBefore)) == 0;

    memset(GetPermanentFlagBits(), 0xC3, sizeof(permanent));
    memset(GetChapterFlagBits(), 0x3C, sizeof(chapter));
    record->clobbered = memcmp(GetPermanentFlagBits(), permanent, sizeof(permanent)) != 0
        && memcmp(GetChapterFlagBits(), chapter, sizeof(chapter)) != 0;
    view.before = 0xE2;
    view.after = 0x2E;
    ReadPermanentFlags_ret(savedPermanent, view.bytes);
    record->readonlyView = memcmp(view.bytes, permanent, sizeof(permanent)) == 0
        && view.before == 0xE2 && view.after == 0x2E
        && *permanentGuard == 0xD6 && *chapterGuard == 0x6D
        && BytesAre(GetPermanentFlagBits(), sizeof(permanent), 0xC3)
        && BytesAre(GetChapterFlagBits(), sizeof(chapter), 0x3C);
    ReadPermanentFlags(savedPermanent);
    record->bounded = record->bounded && *permanentGuard == 0xD6 && *chapterGuard == 0x6D;
    record->otherScope = BytesAre(GetChapterFlagBits(), sizeof(chapter), 0x3C);
    if (savedChapter != 0)
    {
        ReadChapterFlags(savedChapter);
        record->bounded = record->bounded && *permanentGuard == 0xD6 && *chapterGuard == 0x6D;
        record->otherScope = record->otherScope
            && memcmp(GetPermanentFlagBits(), permanent, sizeof(permanent)) == 0;
    }
    record->restored = memcmp(GetPermanentFlagBits(), permanent, sizeof(permanent)) == 0
        && (savedChapter == 0 || memcmp(GetChapterFlagBits(), chapter, sizeof(chapter)) == 0);
    record->bounded = record->bounded
        && memcmp(sBefore, sSaveImage.bytes, sizeof(sBefore)) == 0;
    gGradoSaveFlagsProbe.count++;
}

static void ProbeFlagSaves(void)
{
    unsigned int slot;
    unsigned int pattern;

    PrepareCurrent();
    for (slot = 0; slot < 2; ++slot)
    {
        gSram->suspendSaveBlocks[slot].chapterFlags[5] = 0xA5;
        gSram->suspendSaveBlocks[slot].chapterFlags[6] = 0x5A;
    }
    memcpy(sBefore, sSaveImage.bytes, sizeof(sBefore));
    gGradoSaveFlagsProbe.phase = 1;
    gGradoSaveFlagsProbe.permanentBytes = GetPermanentFlagBitsSize();
    gGradoSaveFlagsProbe.chapterBytes = GetChapterFlagBitsSize();
    gGradoSaveFlagsProbe.permanentAddress = (uintptr_t)GetPermanentFlagBits();
    gGradoSaveFlagsProbe.chapterAddress = (uintptr_t)GetChapterFlagBits();
    if (gGradoSaveFlagsProbe.permanentBytes != 25 || gGradoSaveFlagsProbe.chapterBytes != 5)
    {
        gGradoSaveFlagsProbe.phase = 3;
        return;
    }
    for (slot = 0; slot < 5; ++slot)
        for (pattern = 0; pattern < 4; ++pattern)
            RecordFlagSave(slot, pattern);
    gGradoSaveFlagsProbe.phase = 2;
}

void AgbMain(void)
{
    gGradoSaveCompatProbe.phase = 1;
    gGradoSaveCompatProbe.epoch = FE8_EXPANSION_SAVE_COMPAT_EPOCH;
    gGradoSaveCompatProbe.envelope = SAVE_FORMAT_VERSION_CURRENT;
    gSram = &sSaveImage.native;

    PrepareBlank(0);
    Record(0);
    PrepareBlank(0xFF);
    Record(1);

    PrepareBlank(0);
    sSaveImage.bytes[0x400] = 0x5A;
    Record(2);

    PrepareHeader();
    Record(3);

    PrepareCurrent();
    Record(4);

    PrepareCurrent();
    gSram->expansionSaveMeta.compatEpoch = FE8_EXPANSION_SAVE_COMPAT_EPOCH == 256 ? 2 : 256;
    RefreshMetaChecksum();
    Record(5);

    PrepareCurrent();
    gSram->expansionSaveMeta.formatVersion = SAVE_FORMAT_VERSION_CURRENT - 1;
    gSram->expansionSaveMeta.compatEpoch = FE8_EXPANSION_SAVE_COMPAT_EPOCH == 256 ? 2 : 256;
    RefreshMetaChecksum();
    Record(6);

    PrepareCurrent();
    gSram->expansionSaveMeta.formatVersion = SAVE_FORMAT_VERSION_CURRENT + 1;
    RefreshMetaChecksum();
    Record(7);

    PrepareCurrent();
    gSram->expansionSaveMeta.checksum ^= 1;
    Record(8);

    PrepareCurrent();
    gSram->globalSaveInfo.checksum ^= 1;
    Record(9);

    PrepareCurrent();
    gSram->expansionSaveMeta.abiId ^= 1;
    gSram->expansionSaveMeta.frameworkVersionPacked ^= 0x100;
    gSram->expansionSaveMeta.configFingerprint[0] ^= 1;
    gSram->expansionSaveMeta.buildCommitShort[0] ^= 1;
    RefreshMetaChecksum();
    Record(10);

    PrepareCurrent();
    gBoolSramWorking = false;
    Record(11);

    PrepareBlank(0);
    memset(&gSram->globalSaveInfo, 0xFF, sizeof(gSram->globalSaveInfo));
    Record(12);

    gGradoSaveCompatProbe.phase = 2;
    ProbeFlagSaves();
    for (;;)
        __asm__ volatile ("nop");
}
