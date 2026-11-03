#include "global.h"

#include "agb_sram.h"
#include "bm.h"
#include "bmitem.h"
#include "bmsave.h"
#include "bmunit.h"
#include "eventinfo.h"
#include "worldmap.h"

#include <string.h>

CONST_DATA struct CharacterData gCharacterData[] = {
    { .number = 1, .defaultClass = 1 },
};

CONST_DATA struct ClassData gClassData[] = {
    { .number = 1, .maxHP = 60 },
};

struct ItemData gItemData[1];
u8 gGenericBuffer[0x2000];
struct GMapNodeLink gUnk_10[ARRAY_COUNT(gGMData.nodes)];

struct GradoSavePathRecord
{
    u32 slot;
    u32 valid;
    u32 clobbered;
    u32 permanent;
    u32 chapter;
    u32 play;
    u32 unit;
    u32 corruptRefused;
    u32 refusalPreserved;
};

struct GradoSavePathProbe
{
    u32 phase;
    u32 count;
    struct GradoSavePathRecord records[5];
};

static union
{
    struct SaveBlocks native;
    u8 bytes[CART_SRAM_SIZE];
} sSaveImage;
static u8 sBefore[CART_SRAM_SIZE];
volatile struct GradoSavePathProbe gGradoSavePathProbe;

static void PrepareCurrent(void)
{
    struct GlobalSaveInfo * info;

    memset(sSaveImage.bytes, 0, sizeof(sSaveImage.bytes));
    SramInit();
    info = &gSram->globalSaveInfo;
    memcpy(info->name, "AGB-FE9", sizeof("AGB-FE9"));
    info->magic32 = SAVEMAGIC32;
    info->magic16 = SAVEMAGIC16;
    info->checksum = Checksum16(info, GLOBALSIZEINFO_SIZE_FOR_CHECKSUM);
    BuildCurrentExpansionSaveMeta(&gSram->expansionSaveMeta);
}

static void PrepareUnits(struct Unit * units, unsigned int count)
{
    unsigned int i;

    for (i = 0; i < count; ++i)
    {
        units[i].pCharacterData = gCharacterData;
        units[i].pClassData = gClassData;
        units[i].level = 7;
        units[i].exp = 33;
        units[i].xPos = 2;
        units[i].yPos = 3;
        units[i].maxHP = 23;
        units[i].curHP = 19;
    }
}

static void PrepareLive(unsigned int input, u8 * permanent, u8 * chapter)
{
    unsigned int i;

    memset(&gPlaySt, 0, sizeof(gPlaySt));
    InitUnits();
    PrepareUnits(gUnitArrayBlue, UNIT_SAVE_AMOUNT_BLUE);
    PrepareUnits(gUnitArrayRed, UNIT_SAVE_AMOUNT_RED);
    PrepareUnits(gUnitArrayGreen, UNIT_SAVE_AMOUNT_GREEN);
    gPlaySt.partyGoldAmount = 1234 + input * 73;
    gPlaySt.chapterIndex = 10;
    for (i = 0; i < 25; ++i)
        permanent[i] = 0xA5 ^ (i * 29) ^ (input * 7);
    for (i = 0; i < 5; ++i)
        chapter[i] = 0x5A ^ (i * 17) ^ (input * 7);
    memcpy(GetPermanentFlagBits(), permanent, 25);
    memcpy(GetChapterFlagBits(), chapter, 5);
}

static void RecordPath(unsigned int input, int suspend)
{
    u8 permanent[25];
    u8 chapter[5];
    u8 * savedPermanent;
    unsigned int gold;
    unsigned int slot;
    volatile struct GradoSavePathRecord * record;

    PrepareLive(input, permanent, chapter);
    gold = gPlaySt.partyGoldAmount;
    if (suspend)
    {
        WriteSuspendSave(SAVE_ID_SUSPEND);
        slot = SAVE_ID_SUSPEND + GetLastSuspendSaveId();
        IsValidSuspendSave(SAVE_ID_SUSPEND);
        savedPermanent = gSram->suspendSaveBlocks[slot - SAVE_ID_SUSPEND].permanentFlags;
    }
    else
    {
        slot = input;
        WriteGameSave(slot);
        savedPermanent = gSram->gameSaveBlocks[slot].permanentFlags;
    }

    record = &gGradoSavePathProbe.records[gGradoSavePathProbe.count];
    record->slot = slot;
    record->valid = ReadSaveBlockInfo(0, slot);
    memset(GetPermanentFlagBits(), 0, 25);
    if (suspend)
        memset(GetChapterFlagBits(), 0xC3, 5);
    gPlaySt.partyGoldAmount = 0;
    gPlaySt.chapterIndex = 0;
    memset(&gUnitArrayBlue[0], 0, sizeof(gUnitArrayBlue[0]));
    gUnitArrayBlue[0].level = 1;
    record->clobbered = memcmp(GetPermanentFlagBits(), permanent, 25) != 0
        && gPlaySt.partyGoldAmount != gold && gPlaySt.chapterIndex != 10
        && gUnitArrayBlue[0].pCharacterData == 0 && gUnitArrayBlue[0].level != 7;
    if (suspend)
        ReadSuspendSave(SAVE_ID_SUSPEND);
    else
        ReadGameSave(slot);
    record->permanent = memcmp(GetPermanentFlagBits(), permanent, 25) == 0;
    record->chapter = memcmp(GetChapterFlagBits(), chapter, 5) == 0;
    record->play = gPlaySt.partyGoldAmount == gold && gPlaySt.chapterIndex == 10;
    record->unit = gUnitArrayBlue[0].pCharacterData == gCharacterData
        && gUnitArrayBlue[0].pClassData == gClassData
        && gUnitArrayBlue[0].level == 7 && gUnitArrayBlue[0].exp == 33
        && gUnitArrayBlue[0].xPos == 2 && gUnitArrayBlue[0].yPos == 3;
    savedPermanent[0] ^= 1;
    memcpy(sBefore, sSaveImage.bytes, sizeof(sBefore));
    record->corruptRefused = !ReadSaveBlockInfo(0, slot);
    record->refusalPreserved = memcmp(sBefore, sSaveImage.bytes, sizeof(sBefore)) == 0;
    savedPermanent[0] ^= 1;
    ++gGradoSavePathProbe.count;
}

void AgbMain(void)
{
    unsigned int slot;
    u8 permanent[25];
    u8 chapter[5];

    gGradoSavePathProbe.phase = 1;
    gSram = &sSaveImage.native;
    for (slot = 0; slot < 3; ++slot)
    {
        PrepareCurrent();
        RecordPath(slot, 0);
    }
    PrepareCurrent();
    PrepareLive(0, permanent, chapter);
    WriteGameSave(SAVE_ID_GAME0);
    RecordPath(3, 1);
    RecordPath(4, 1);
    gGradoSavePathProbe.phase = 2;
    for (;;)
        __asm__ volatile ("nop");
}
