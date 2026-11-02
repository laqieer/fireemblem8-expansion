#include "global.h"

#include "agb_sram.h"
#include "bmsave.h"

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

static union
{
    struct SaveBlocks native;
    u8 bytes[CART_SRAM_SIZE];
} sSaveImage;
static u8 sBefore[CART_SRAM_SIZE];
volatile struct GradoSaveCompatProbe gGradoSaveCompatProbe;
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
    for (;;)
        __asm__ volatile ("nop");
}
