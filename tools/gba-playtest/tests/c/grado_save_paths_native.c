#include "global.h"

#include "agb_sram.h"
#include "bm.h"
#include "bmitem.h"
#include "bmsave.h"
#include "bmunit.h"
#include "eventinfo.h"
#include "grado_campaign_state.h"
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

struct GradoSemanticSaveRecord
{
    u32 route;
    u32 result;
    u32 restored;
    u32 preserved;
};

struct GradoSemanticSaveProbe
{
    u32 phase;
    u32 count;
    struct GradoSemanticSaveRecord records[8];
};

static union
{
    struct SaveBlocks native;
    u8 bytes[CART_SRAM_SIZE];
} sSaveImage;
static u8 sBefore[CART_SRAM_SIZE];
volatile struct GradoSavePathProbe gGradoSavePathProbe;
volatile struct GradoSemanticSaveProbe gGradoSemanticSaveProbe;

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
    bool suspendValid = true;
    volatile struct GradoSavePathRecord * record;

    PrepareLive(input, permanent, chapter);
    gold = gPlaySt.partyGoldAmount;
    if (suspend)
    {
        WriteSuspendSave(SAVE_ID_SUSPEND);
        slot = SAVE_ID_SUSPEND + GetLastSuspendSaveId();
        suspendValid = IsValidSuspendSave(SAVE_ID_SUSPEND);
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
    record->valid = suspendValid && ReadSaveBlockInfo(0, slot);
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

static void PrepareSemanticState(void)
{
    GradoState_BeginLifecycle(GRADO_LOAD_NEW_GAME);
    GradoState_WriteSymbol(GRADO_STATE_LEGITIMACY, 2, 1);
    GradoState_WriteSymbol(GRADO_STATE_RENAIS, 7, 2);
    GradoState_WriteSymbol(GRADO_FLAG_CH07_GUARD_KEPT, 8, 2);
    GradoState_WriteSymbol(GRADO_STATE_SALTROAD, 9, 1);
    GradoState_WriteSymbol(GRADO_FLAG_CH09_MEDNET, 10, 2);
    GradoState_WriteSymbol(GRADO_STATE_TRUTH, 11, 3);
    GradoState_WriteSymbol(GRADO_STATE_RODERICH, 11, 2);
    GradoState_WriteSymbol(GRADO_STATE_RATION, 12, 1);
    GradoState_WriteSymbol(GRADO_STATE_WYVERN, 14, 1);
    GradoState_WriteSymbol(GRADO_STATE_VILLAGE, 15, 2);
    GradoState_WriteSymbol(GRADO_STATE_RAUSTEN_MED, 16, 1);
    GradoState_WriteSymbol(GRADO_STATE_RAUSTEN_DATA, 16, 2);
    GradoState_WriteSymbol(GRADO_STATE_DIRECTION, 19, 4);
}

static void RecordSemantic(
    enum GradoStateLoadRoute route,
    enum GradoStateLoadResult expected)
{
    struct GradoStateFlagView saved;
    struct GradoStateLoadDiagnostic diagnostic;
    volatile struct GradoSemanticSaveRecord * record;
    unsigned int slot;
    u8 permanent[25];
    u8 chapter[5];
    enum GradoStateLoadResult result;

    PrepareCurrent();
    PrepareLive(0, permanent, chapter);
    PrepareSemanticState();
    gPlaySt.chapterIndex = 21;
    if (route == GRADO_LOAD_SUSPEND)
    {
        WriteSuspendSave(SAVE_ID_SUSPEND);
        slot = SAVE_ID_SUSPEND + GetLastSuspendSaveId();
        saved.chapterFlags = gSram->suspendSaveBlocks[
            slot - SAVE_ID_SUSPEND].chapterFlags;
        saved.chapterBytes = 5;
        saved.permanentFlags = gSram->suspendSaveBlocks[
            slot - SAVE_ID_SUSPEND].permanentFlags;
        saved.permanentBytes = 25;
    }
    else
    {
        slot = SAVE_ID_GAME0;
        WriteGameSave(slot);
        saved.chapterFlags = NULL;
        saved.chapterBytes = 0;
        saved.permanentFlags = gSram->gameSaveBlocks[slot].permanentFlags;
        saved.permanentBytes = 25;
    }
    memcpy(sBefore, sSaveImage.bytes, sizeof(sBefore));
    record = &gGradoSemanticSaveProbe.records[gGradoSemanticSaveProbe.count++];
    record->route = route;
    result = GradoState_PreflightSave(
        ClassifySramSaveCompat(), &gSram->expansionSaveMeta,
        route, 21, &saved, &diagnostic);
    record->result = result == expected ? 1 : 100 + result;
    record->preserved = memcmp(sBefore, sSaveImage.bytes, sizeof(sBefore)) == 0;
    if (result == GRADO_STATE_LOAD_VALID)
    {
        enum GradoStateLoadResult endingResult;

        memset(GetPermanentFlagBits(), 0, GetPermanentFlagBitsSize());
        memset(GetChapterFlagBits(), 0, GetChapterFlagBitsSize());
        if (route == GRADO_LOAD_SUSPEND)
        {
            if (!IsValidSuspendSave(SAVE_ID_SUSPEND))
                result = GRADO_STATE_LOAD_CORRUPT;
            ReadSuspendSave(SAVE_ID_SUSPEND);
        }
        else
            ReadGameSave(slot);
        endingResult = GradoState_ResolveEnding(21, &diagnostic.missingSymbol);
        record->restored = result == GRADO_STATE_LOAD_VALID
            && endingResult == GRADO_STATE_LOAD_VALID
            && diagnostic.missingSymbol == 4
            ? 1 : 100 + diagnostic.missingSymbol;
    }
    else
    {
        record->restored = 1;
    }
}

static void RecordSemanticNegative(
    enum GradoStateLoadRoute route,
    enum GradoStateLoadResult expected,
    int roderichRecovery)
{
    const struct GradoStateFlagView live = {
        GetChapterFlagBits(), GetChapterFlagBitsSize(),
        GetPermanentFlagBits(), GetPermanentFlagBitsSize(),
    };
    struct GradoStateLoadDiagnostic diagnostic;
    volatile struct GradoSemanticSaveRecord * record;
    u8 permanent[25];
    u8 chapter[5];
    enum GradoStateLoadResult result;
    unsigned int gold;
    struct Unit unit;

    PrepareCurrent();
    PrepareLive(0, permanent, chapter);
    PrepareSemanticState();
    if (roderichRecovery)
    {
        GradoState_RecordResolution(GRADO_STATE_RODERICH, 19);
        GradoState_WriteSymbol(GRADO_STATE_RODERICH, 11, 0);
        GradoState_WriteSymbol(GRADO_FLAG_CH02_CLEAR, 0, 1);
        GradoState_WriteSymbol(GRADO_FLAG_CH06_CLEAR, 0, 1);
        GradoState_WriteSymbol(GRADO_FLAG_CH07_CLEAR, 0, 1);
        GradoState_WriteSymbol(GRADO_FLAG_CH08_CLEAR, 0, 1);
        GradoState_WriteSymbol(GRADO_FLAG_CH09_CLEAR, 0, 1);
        GradoState_WriteSymbol(GRADO_FLAG_CH10_CLEAR, 0, 1);
    }
    else if (expected == GRADO_STATE_LOAD_CONFLICT)
    {
        SetFlag(139);
        SetFlag(140);
    }
    else if (expected == GRADO_STATE_LOAD_MISSING)
    {
        GradoState_WriteSymbol(GRADO_STATE_RAUSTEN_MED, 16, 0);
        GradoState_WriteSymbol(GRADO_FLAG_CH15_MED_NATASHA_ABSENT, 16, 1);
        GradoState_WriteSymbol(GRADO_FLAG_CH15_CLEAR, 0, 1);
    }
    else if (expected == GRADO_STATE_LOAD_REOPEN)
    {
        GradoState_WriteSymbol(GRADO_STATE_DIRECTION, 19, 0);
        GradoState_WriteSymbol(GRADO_FLAG_CH02_CLEAR, 0, 1);
        GradoState_WriteSymbol(GRADO_FLAG_CH06_CLEAR, 0, 1);
        GradoState_WriteSymbol(GRADO_FLAG_CH07_CLEAR, 0, 1);
        GradoState_WriteSymbol(GRADO_FLAG_CH08_CLEAR, 0, 1);
        GradoState_WriteSymbol(GRADO_FLAG_CH09_CLEAR, 0, 1);
        GradoState_WriteSymbol(GRADO_FLAG_CH10_CLEAR, 0, 1);
        GradoState_WriteSymbol(GRADO_FLAG_CH11_CLEAR, 0, 1);
        GradoState_WriteSymbol(GRADO_FLAG_CH13_CLEAR, 0, 1);
        GradoState_WriteSymbol(GRADO_FLAG_CH14_CLEAR, 0, 1);
        GradoState_WriteSymbol(GRADO_FLAG_CH15_CLEAR, 0, 1);
        GradoState_WriteSymbol(GRADO_FLAG_CH18_CLEAR, 0, 1);
    }
    if (route == GRADO_LOAD_LEGACY)
        memset(&gSram->expansionSaveMeta, 0, sizeof(gSram->expansionSaveMeta));
    memcpy(sBefore, sSaveImage.bytes, sizeof(sBefore));
    gold = gPlaySt.partyGoldAmount;
    unit = gUnitArrayBlue[0];
    result = GradoState_PreflightSave(
        ClassifySramSaveCompat(), &gSram->expansionSaveMeta,
        route, 21, &live, &diagnostic);
    record = &gGradoSemanticSaveProbe.records[gGradoSemanticSaveProbe.count++];
    record->route = route;
    record->result = result == expected
        && (route != GRADO_LOAD_LEGACY
            || ClassifySramSaveCompat() == SAVE_COMPAT_VALID_LEGACY_OR_VANILLA);
    record->restored = !roderichRecovery
        || (diagnostic.recovery == GRADO_STATE_RECOVERY_RODERICH_EXILED
            && GradoState_ApplyLoadRecovery(&diagnostic) == GRADO_STATE_LOAD_VALID
            && gPlaySt.partyGoldAmount == gold
            && memcmp(&unit, &gUnitArrayBlue[0], sizeof(unit)) == 0);
    record->preserved = memcmp(sBefore, sSaveImage.bytes, sizeof(sBefore)) == 0;
}

static void ProbeSemanticSaves(void)
{
    unsigned int epoch;

    gGradoSemanticSaveProbe.phase = 1;
    epoch = FE8_EXPANSION_SAVE_COMPAT_EPOCH;
    RecordSemantic(GRADO_LOAD_NORMAL,
        epoch == 256 ? GRADO_STATE_LOAD_VALID : GRADO_STATE_LOAD_INCOMPATIBLE);
    RecordSemantic(GRADO_LOAD_SUSPEND,
        epoch == 256 ? GRADO_STATE_LOAD_VALID : GRADO_STATE_LOAD_INCOMPATIBLE);
    if (epoch == 256)
    {
        RecordSemanticNegative(GRADO_LOAD_NORMAL, GRADO_STATE_LOAD_MISSING, 0);
        RecordSemanticNegative(GRADO_LOAD_SUSPEND, GRADO_STATE_LOAD_CONFLICT, 0);
        RecordSemanticNegative(GRADO_LOAD_NORMAL, GRADO_STATE_LOAD_RECOVERY, 1);
        RecordSemanticNegative(GRADO_LOAD_LEGACY, GRADO_STATE_LOAD_INCOMPATIBLE, 0);
        RecordSemanticNegative(GRADO_LOAD_SUSPEND, GRADO_STATE_LOAD_REOPEN, 0);
    }
    gGradoSemanticSaveProbe.phase = 2;
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
    ProbeSemanticSaves();
    gGradoSavePathProbe.phase = 2;
    for (;;)
        __asm__ volatile ("nop");
}
