#include "global.h"

#include "agb_sram.h"
#include "bm.h"
#include "bmudisp.h"
#include "bmitem.h"
#include "bmlib.h"
#include "bmsave.h"
#include "bmunit.h"
#include "eventinfo.h"
#include "hardware.h"
#include "helpbox.h"
#include "m4a.h"
#include "savemenu.h"
#include "sio.h"
#include "sio_core.h"
#include "uimenu.h"
#include "constants/msg.h"
#include "grado_campaign_state.h"
#include "statscreen.h"
#include "worldmap.h"

#include <string.h>

#include "../../../../scripts/modernize/tests/fixtures/grado_save_test_helpers.h"

u8 DebugContinueMenu_ContinueChapter(struct MenuProc * menuProc, struct MenuItemProc * menuItemProc);
void SaveMenu_SaveSlotSelectLoop(struct SaveMenuProc * proc);
void GradoChoiceCheckpoints_Run(void);
void GradoState_SaveMenu_NewGame(ProcPtr procPtr);

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
    u32 backing;
    u32 valid;
    u32 backingValid;
    u32 clobbered;
    u32 permanent;
    u32 chapter;
    u32 play;
    u32 unit;
    u32 bonus;
    u32 corruptRefused;
    u32 refusalPreserved;
};

struct GradoSavePathProbe
{
    u32 phase;
    u32 count;
    struct GradoSavePathRecord records[9];
};

struct GradoSemanticSaveRecord
{
    u32 route;
    u32 slot;
    u32 backing;
    u32 result;
    u32 state;
    u32 preserved;
    u32 valid;
    u32 backingValid;
    u32 bonus;
};

struct GradoSemanticSaveProbe
{
    u32 phase;
    u32 count;
    u32 normalRoute;
    u32 suspendRoute;
    u32 legacyRoute;
    u32 validResult;
    u32 incompatibleResult;
    u32 corruptResult;
    u32 missingResult;
    u32 conflictResult;
    u32 recoveryResult;
    u32 reopenResult;
    struct GradoSemanticSaveRecord records[9];
};

struct GradoNativeSaveRecord
{
    u32 route;
    u32 requestedSlot;
    u32 expectedBacking;
    u32 result;
    u32 sourceSlot;
    u32 backingSlot;
    u32 preserved;
};

struct GradoNativeSaveProbe
{
    u32 phase;
    u32 count;
    struct GradoNativeSaveRecord records[5];
};

struct GradoNativeLoadRefusalProbe
{
    u32 phase;
    u32 result;
    u32 helpBoxCalls;
    u32 helpBoxX;
    u32 helpBoxY;
    u32 expectedMessage;
    u32 message;
    u32 sramPreserved;
    u32 liveStatePreserved;
    u32 suspendCallerRefused;
    u32 suspendCallerPreserved;
    u32 suspendRestartCount;
    u32 normalCallerPreserved;
    u32 normalCallerTransitionCount;
    u32 callerHelpBoxCalls;
    u32 callerHelpBoxCorrect;
    u32 metadataCopyCount;
    u32 preflightResult;
    u32 linkArenaProcGotoCount;
    u32 linkArenaExitLabel;
    u32 saveMenuSuspendRefused;
    u32 saveMenuNormalRefused;
    u32 saveMenuPreserved;
    u32 saveMenuProcGotoCount;
    u32 reopenPreflightResult;
    u32 reopenLoadResult;
    u32 reopenChoiceRestored;
    u32 reopenGoldRestored;
    u32 reopenTryLoadAccepted;
    u32 reopenTryLoadStateRestored;
    u32 reopenTryLoadNoPopup;
    u32 reopenSaveMenuContinued;
    u32 linkArenaSelectionRefused;
    u32 saveMenuOption7Refused;
    u32 linkArenaOldBaseClobbers;
    u32 linkArenaNewBasePreserves;
    u32 linkArenaPopupDestinationCorrect;
    u32 linkArenaPopupPreservesTiles;
    u32 linkArenaPopupClosePreservesTiles;
    u32 corruptBlockLoadResult;
    u32 corruptBlockLoadPreserved;
    u32 corruptBlockMenuRefused;
    u32 corruptBlockMenuPreserved;
    u32 corruptBlockMenuMessage;
};

static union
{
    struct SaveBlocks native;
    u8 bytes[CART_SRAM_SIZE];
} sSaveImage;
static u8 sBefore[CART_SRAM_SIZE];
volatile struct GradoSavePathProbe gGradoSavePathProbe;
volatile struct GradoSemanticSaveProbe gGradoSemanticSaveProbe;
volatile struct GradoNativeSaveProbe gGradoNativeSaveProbe;
volatile struct GradoNativeLoadRefusalProbe gGradoNativeLoadRefusalProbe;
struct LinkArenaStMaybe gLinkArenaSt;
static u32 sHelpBoxCalls;
static int sHelpBoxX;
static int sHelpBoxY;
static int sHelpBoxMessage;
static void * sHelpBoxGfxDest;
static int sHelpBoxPalette;
static u32 sHelpBoxGfxLoads;
static u32 sHelpBoxGfxLoadsAtStart;
static u32 sHelpBoxCloseCalls;
static u32 sPopupStartCalls;
static const struct ProcCmd * sPopupScript;
static ProcPtr sPopupParent;
static ProcPtr sBrokenProc;
static struct SaveMenu8A20068Proc sSaveMenuPopupProc;
static u8 sLinkArenaTacticianGfxBefore[0x1800];
static u32 sRestartGameCalls;
static u32 sLinkArenaInitCalls;
static void (*sUnwrappedReadSramFast)(void const *, void *, u32);
static int sTraceExpansionMetaReads;
static u32 sExpansionMetaReadCount;
static u32 sProcGotoCalls;
static int sProcGotoLabel;

extern struct ProcCmd gProcScr_Savemenu_0[];
void GradoState_LoadProductionHelpBoxGfx(void * dest, int pal);

static void TraceSramRead(void const * src, void * dest, u32 size)
{
    if (sTraceExpansionMetaReads
        && src == &gSram->expansionSaveMeta
        && size == sizeof(gSram->expansionSaveMeta))
        ++sExpansionMetaReadCount;

    sUnwrappedReadSramFast(src, dest, size);
}

void Proc_Goto(ProcPtr proc, int label)
{
    (void)proc;
    ++sProcGotoCalls;
    sProcGotoLabel = label;
}

ProcPtr Proc_StartBlocking(const struct ProcCmd * script, ProcPtr parent)
{
    sPopupStartCalls++;
    sPopupScript = script;
    sPopupParent = parent;
    return &sSaveMenuPopupProc;
}

void Proc_Break(ProcPtr proc)
{
    sBrokenProc = proc;
}

void StartBgmVolumeChange(int volumeInit, int volumeEnd, int duration, ProcPtr parent)
{
    (void)volumeInit;
    (void)volumeEnd;
    (void)duration;
    (void)parent;
}

void StartHelpBoxExt_Unk(int x, int y, int mid)
{
    sHelpBoxCalls++;
    sHelpBoxX = x;
    sHelpBoxY = y;
    sHelpBoxMessage = mid;
    sHelpBoxGfxLoadsAtStart = sHelpBoxGfxLoads;
}

static int CompleteManagedRefusalPopup(
    ProcPtr parent,
    int expectedMessage,
    u32 startCallsBefore,
    u32 helpBoxCallsBefore,
    int linkArenaContext)
{
    void * expectedGfxDest = linkArenaContext
        ? OBJ_VRAM0 + 0x300 * TILE_SIZE_4BPP
        : OBJ_VRAM0 + OBJCHR_SAVEMENU_SLOTSEL_HELPBOX * TILE_SIZE_4BPP;
    int expectedPalette = linkArenaContext
        ? 6
        : OBJPAL_SAVEMENU_SLOTSEL_HELPBOX;
    u32 helpBoxGfxLoadsBefore = sHelpBoxGfxLoads;
    u32 helpBoxCloseCallsBefore = sHelpBoxCloseCalls;
    int linkArenaTilesPreserved = 1;

    if (sPopupStartCalls != startCallsBefore + 1
        || sPopupScript != gProcScr_Savemenu_0
        || sPopupParent != parent
        || sSaveMenuPopupProc.msgId != expectedMessage
        || sHelpBoxCalls != helpBoxCallsBefore)
        return 0;

    if (linkArenaContext)
    {
        Decompress(Img_TacticianSelObj, OBJ_CHR_ADDR(0x240));
        memcpy(sLinkArenaTacticianGfxBefore, OBJ_CHR_ADDR(0x240),
            sizeof(sLinkArenaTacticianGfxBefore));
        GradoState_LoadProductionHelpBoxGfx(
            OBJ_VRAM0 + 0x280 * TILE_SIZE_4BPP, 6);

        gGradoNativeLoadRefusalProbe.linkArenaOldBaseClobbers =
            memcmp(sLinkArenaTacticianGfxBefore, OBJ_CHR_ADDR(0x240),
                sizeof(sLinkArenaTacticianGfxBefore)) != 0;
        if (!gGradoNativeLoadRefusalProbe.linkArenaOldBaseClobbers)
            return 0;

        Decompress(Img_TacticianSelObj, OBJ_CHR_ADDR(0x240));
        memcpy(sLinkArenaTacticianGfxBefore, OBJ_CHR_ADDR(0x240),
            sizeof(sLinkArenaTacticianGfxBefore));
        GradoState_LoadProductionHelpBoxGfx(
            OBJ_VRAM0 + 0x300 * TILE_SIZE_4BPP, 6);

        gGradoNativeLoadRefusalProbe.linkArenaNewBasePreserves =
            memcmp(sLinkArenaTacticianGfxBefore, OBJ_CHR_ADDR(0x240),
                sizeof(sLinkArenaTacticianGfxBefore)) == 0;
        if (!gGradoNativeLoadRefusalProbe.linkArenaNewBasePreserves)
            return 0;

        Decompress(Img_TacticianSelObj, OBJ_CHR_ADDR(0x240));
        memcpy(sLinkArenaTacticianGfxBefore, OBJ_CHR_ADDR(0x240),
            sizeof(sLinkArenaTacticianGfxBefore));
    }

    SaveMenuPopupHelpBox_Init(&sSaveMenuPopupProc);
    if (linkArenaContext)
        gGradoNativeLoadRefusalProbe.linkArenaPopupDestinationCorrect =
            sHelpBoxGfxDest == expectedGfxDest;
    if (sHelpBoxCalls != helpBoxCallsBefore + 1
        || sHelpBoxGfxLoads != helpBoxGfxLoadsBefore + 1
        || sHelpBoxGfxDest != expectedGfxDest
        || sHelpBoxPalette != expectedPalette
        || sHelpBoxGfxLoadsAtStart != sHelpBoxGfxLoads)
        return 0;

    if (linkArenaContext)
    {
        linkArenaTilesPreserved =
            memcmp(sLinkArenaTacticianGfxBefore, OBJ_CHR_ADDR(0x240),
                sizeof(sLinkArenaTacticianGfxBefore)) == 0;
        gGradoNativeLoadRefusalProbe.linkArenaPopupPreservesTiles =
            linkArenaTilesPreserved;
    }

    gKeyStatusPtr->newKeys = A_BUTTON;
    sBrokenProc = NULL;
    SaveMenuPopupHelpBox_WaitInput(&sSaveMenuPopupProc);

    if (linkArenaContext)
    {
        linkArenaTilesPreserved = linkArenaTilesPreserved
            && memcmp(sLinkArenaTacticianGfxBefore, OBJ_CHR_ADDR(0x240),
                sizeof(sLinkArenaTacticianGfxBefore)) == 0;
        gGradoNativeLoadRefusalProbe.linkArenaPopupClosePreservesTiles =
            linkArenaTilesPreserved;
    }

    return sBrokenProc == &sSaveMenuPopupProc
        && sHelpBoxCloseCalls == helpBoxCloseCallsBefore + 1
        && linkArenaTilesPreserved;
}

void RestartGameAndGoto8(void)
{
    sRestartGameCalls++;
}

void LinkArenaBattleMap_InitChapter(void)
{
    sLinkArenaInitCalls++;
}

void ApplyUnitSpritePalettes(void)
{
    sLinkArenaInitCalls++;
}

void DisplayUiHand(int x, int y)
{
    (void)x;
    (void)y;
}

void SioPlaySoundEffect(int sound)
{
    (void)sound;
}

void InitPlayConfig(int isDifficult, s8 unk)
{
    (void)isDifficult;
    (void)unk;
}

void CloseHelpBox(void)
{
    sHelpBoxCloseCalls++;
}

void InitSpriteTextFont(struct Font * font, void * vramDest, int palette)
{
    (void)font;
    (void)vramDest;
    (void)palette;
}

void InitSpriteText(struct Text * text)
{
    (void)text;
}

void SetTextFont(struct Font * font)
{
    (void)font;
}

void LoadHelpBoxGfx(void * dest, int pal)
{
    sHelpBoxGfxDest = dest;
    sHelpBoxPalette = pal;
    sHelpBoxGfxLoads++;
    if (pal == 6)
        GradoState_LoadProductionHelpBoxGfx(dest, pal);
}

void StartItemHelpBox(int x, int y, int item)
{
    (void)x;
    (void)y;
    (void)item;
}

void SaveMenuDrawSubSelBox(struct SaveMenuProc * proc, s8 flag)
{
    (void)proc;
    (void)flag;
}

s8 SaveMenuTryMoveSaveSlotCursor(struct SaveMenuProc * proc, s8 position)
{
    (void)proc;
    (void)position;
    return 0;
}

void m4aSongNumStart(u16 song)
{
    (void)song;
}

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

static u32 BonusForBacking(unsigned int backing)
{
    return 0x9248A113u ^ (0x12345678u * backing);
}

static void RecordPath(unsigned int input, int suspend, unsigned int backing)
{
    u8 permanent[25];
    u8 chapter[5];
    u8 * savedPermanent;
    u32 bonus = BonusForBacking(backing);
    unsigned int gold;
    unsigned int slot;
    bool suspendValid = true;
    volatile struct GradoSavePathRecord * record;

    PrepareLive(input, permanent, chapter);
    gPlaySt.gameSaveSlot = backing;
    SetBonusContentClaimFlags(suspend ? ~bonus : bonus);
    gold = gPlaySt.partyGoldAmount;
    if (suspend)
    {
        WriteSuspendSave(SAVE_ID_SUSPEND);
        slot = SAVE_ID_SUSPEND + GetLastSuspendSaveId();
        suspendValid = IsValidSuspendSave(SAVE_ID_SUSPEND)
            && SAVE_ID_SUSPEND + gSuspendSaveIdOffset == slot;
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
    record->backing = backing;
    record->valid = suspendValid && ReadSaveBlockInfo(0, slot);
    record->backingValid = ReadSaveBlockInfo(NULL, backing)
        && gSram->gameSaveBlocks[backing].bonusClaimFlags == bonus
        && (!suspend || gSram->suspendSaveBlocks[
            slot - SAVE_ID_SUSPEND].playSt.gameSaveSlot == backing);
    memset(GetPermanentFlagBits(), 0, 25);
    if (suspend)
        memset(GetChapterFlagBits(), 0xC3, 5);
    SetBonusContentClaimFlags(~bonus);
    gPlaySt.partyGoldAmount = 0;
    gPlaySt.chapterIndex = 0;
    gPlaySt.gameSaveSlot = (backing + 1) % 3;
    memset(&gUnitArrayBlue[0], 0, sizeof(gUnitArrayBlue[0]));
    gUnitArrayBlue[0].level = 1;
    record->clobbered = memcmp(GetPermanentFlagBits(), permanent, 25) != 0
        && gPlaySt.partyGoldAmount != gold && gPlaySt.chapterIndex != 10
        && GetBonusContentClaimFlags() != bonus
        && gUnitArrayBlue[0].pCharacterData == 0 && gUnitArrayBlue[0].level != 7;
    if (suspend && suspendValid)
        ReadSuspendSave(SAVE_ID_SUSPEND);
    else if (!suspend)
        ReadGameSave(slot);
    record->permanent = memcmp(GetPermanentFlagBits(), permanent, 25) == 0;
    record->chapter = memcmp(GetChapterFlagBits(), chapter, 5) == 0;
    record->play = gPlaySt.partyGoldAmount == gold && gPlaySt.chapterIndex == 10
        && gPlaySt.gameSaveSlot == backing;
    record->unit = gUnitArrayBlue[0].pCharacterData == gCharacterData
        && gUnitArrayBlue[0].pClassData == gClassData
        && gUnitArrayBlue[0].level == 7 && gUnitArrayBlue[0].exp == 33
        && gUnitArrayBlue[0].xPos == 2 && gUnitArrayBlue[0].yPos == 3;
    record->bonus = GetBonusContentClaimFlags() == bonus;
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
    GradoState_WriteSymbol(GRADO_FLAG_FINAL_WITNESS, 21, 1);
}

static void PrepareSemanticSaves(void)
{
    unsigned int backing;
    u8 permanent[25];
    u8 chapter[5];

    PrepareCurrent();
    for (backing = SAVE_ID_GAME0; backing <= SAVE_ID_GAME2; ++backing)
    {
        PrepareLive(backing, permanent, chapter);
        PrepareSemanticState();
        gPlaySt.chapterIndex = 21;
        SetBonusContentClaimFlags(BonusForBacking(backing));
        WriteGameSave(backing);
    }
}

static void RecordSemantic(enum GradoStateLoadRoute route, unsigned int backing)
{
    struct GradoStateFlagView saved;
    struct GradoStateLoadDiagnostic diagnostic;
    volatile struct GradoSemanticSaveRecord * record;
    struct PlaySt livePlay;
    struct Unit liveUnit;
    unsigned int slot;
    u8 permanent[25];
    u8 chapter[5];
    enum GradoStateLoadResult result;
    u32 bonus = BonusForBacking(backing);
    u16 witness = 0;

    PrepareLive(backing, permanent, chapter);
    PrepareSemanticState();
    gPlaySt.chapterIndex = 21;
    gPlaySt.gameSaveSlot = backing;
    SetBonusContentClaimFlags(~bonus);
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
        slot = backing;
        saved.chapterFlags = NULL;
        saved.chapterBytes = 0;
        saved.permanentFlags = gSram->gameSaveBlocks[slot].permanentFlags;
        saved.permanentBytes = 25;
    }
    memcpy(permanent, GetPermanentFlagBits(), sizeof(permanent));
    memcpy(chapter, GetChapterFlagBits(), sizeof(chapter));
    livePlay = gPlaySt;
    liveUnit = gUnitArrayBlue[0];
    memcpy(sBefore, sSaveImage.bytes, sizeof(sBefore));
    record = &gGradoSemanticSaveProbe.records[gGradoSemanticSaveProbe.count++];
    record->route = route;
    record->slot = slot;
    record->backing = backing;
    record->valid = ReadSaveBlockInfo(NULL, slot)
        && (route != GRADO_LOAD_SUSPEND
            || (IsValidSuspendSave(SAVE_ID_SUSPEND)
                && SAVE_ID_SUSPEND + gSuspendSaveIdOffset == slot));
    record->backingValid = ReadSaveBlockInfo(NULL, backing)
        && gSram->gameSaveBlocks[backing].bonusClaimFlags == bonus
        && (route != GRADO_LOAD_SUSPEND
            || gSram->suspendSaveBlocks[
                slot - SAVE_ID_SUSPEND].playSt.gameSaveSlot == backing);
    result = GradoState_PreflightSave(
        ClassifySramSaveCompat(), &gSram->expansionSaveMeta,
        route, 21, &saved, &diagnostic);
    record->result = result;
    record->preserved = memcmp(sBefore, sSaveImage.bytes, sizeof(sBefore)) == 0;
    if (result == GRADO_STATE_LOAD_VALID)
    {
        enum GradoStateLoadResult endingResult = GRADO_STATE_LOAD_CORRUPT;

        memset(GetPermanentFlagBits(), 0, GetPermanentFlagBitsSize());
        memset(GetChapterFlagBits(), 0, GetChapterFlagBitsSize());
        SetBonusContentClaimFlags(~bonus);
        gPlaySt.gameSaveSlot = (backing + 1) % 3;
        if (route == GRADO_LOAD_SUSPEND)
            result = GradoState_LoadNativeSave(
                GRADO_LOAD_SUSPEND, SAVE_ID_SUSPEND, &diagnostic);
        else
            result = GradoState_LoadNativeSave(
                GRADO_LOAD_NORMAL, slot, &diagnostic);
        if (result == GRADO_STATE_LOAD_VALID)
            endingResult = GradoState_ResolveEnding(21, &diagnostic.missingSymbol);
        record->state = result == GRADO_STATE_LOAD_VALID
            && endingResult == GRADO_STATE_LOAD_VALID
            && diagnostic.missingSymbol == 4
            && gPlaySt.gameSaveSlot == backing
            && memcmp(GetPermanentFlagBits(), saved.permanentFlags, 25) == 0
            && (route != GRADO_LOAD_SUSPEND
                || (GradoState_ReadSymbol(GRADO_FLAG_FINAL_WITNESS, 21, &witness)
                        == GRADO_STATE_VALUE_VALID
                    && witness == 1
                    && memcmp(GetChapterFlagBits(), saved.chapterFlags, 5) == 0));
        record->bonus = GetBonusContentClaimFlags() == bonus;
    }
    else
    {
        record->state = memcmp(GetPermanentFlagBits(), permanent, sizeof(permanent)) == 0
            && memcmp(GetChapterFlagBits(), chapter, sizeof(chapter)) == 0
            && memcmp(&gPlaySt, &livePlay, sizeof(livePlay)) == 0
            && memcmp(&gUnitArrayBlue[0], &liveUnit, sizeof(liveUnit)) == 0;
        record->bonus = GetBonusContentClaimFlags() == ~bonus;
    }
}

static int WasRecovered(
    const struct GradoStateLoadDiagnostic * diagnostic, enum GradoStateSymbol symbol)
{
    unsigned int i;

    for (i = 0; i < diagnostic->recoveredCount; ++i)
        if (diagnostic->recoveredSymbols[i] == symbol)
            return 1;
    return 0;
}

static void RecordBackedRecovery(enum GradoStateLoadRoute route, int stale)
{
    const struct GradoStateFlagView live = {
        GetChapterFlagBits(), GetChapterFlagBitsSize(),
        GetPermanentFlagBits(), GetPermanentFlagBitsSize(),
    };
    struct GradoStateFlagView saved;
    struct GradoStateLoadDiagnostic diagnostic;
    struct GradoStateLoadDiagnostic nativeDiagnostic;
    struct GradoStateLoadDiagnostic liveDiagnostic;
    volatile struct GradoSemanticSaveRecord * record;
    struct PlaySt livePlay;
    struct Unit liveUnit;
    enum GradoStateLoadResult preflight;
    enum GradoStateLoadResult nativePreflight;
    enum GradoStateLoadResult applied;
    u8 expectedPermanent[25];
    u8 permanent[25];
    u8 chapter[5];
    u32 bonus = BonusForBacking(0);
    u16 legitimacy = 0;
    u16 renais = 0;
    u16 direction = 0;
    unsigned int slot;
    int preparedExpected;
    int liveValid;
    int preflightPreserved;
    int firstRecovery;
    int clobbered;
    int loaded;
    int correction;

    PrepareCurrent();
    PrepareLive(0, permanent, chapter);
    PrepareSemanticState();
    GradoState_WriteSymbol(GRADO_STATE_LEGITIMACY, 2, 0);
    GradoState_WriteSymbol(GRADO_STATE_RENAIS, 7, 0);
    GradoState_WriteSymbol(GRADO_FLAG_CH02_CLEAR, 0, 1);
    GradoState_WriteSymbol(GRADO_FLAG_CH06_CLEAR, 0, 1);
    GradoTestSetPermanentFlag(GetPermanentFlagBits(), GRADO_TEST_FIRST_GRADO_STATE_DIRECTION + 3);
    gPlaySt.chapterIndex = 21;
    gPlaySt.gameSaveSlot = 0;
    SetBonusContentClaimFlags(bonus);
    WriteGameSave(0);

    if (route == GRADO_LOAD_SUSPEND)
    {
        WriteSuspendSave(SAVE_ID_SUSPEND);
        slot = SAVE_ID_SUSPEND + GetLastSuspendSaveId();
        saved.chapterFlags = gSram->suspendSaveBlocks[
            slot - SAVE_ID_SUSPEND].chapterFlags;
        saved.chapterBytes = 5;
        saved.permanentFlags = gSram->suspendSaveBlocks[
            slot - SAVE_ID_SUSPEND].permanentFlags;
    }
    else
    {
        slot = 0;
        saved.chapterFlags = NULL;
        saved.chapterBytes = 0;
        saved.permanentFlags = gSram->gameSaveBlocks[0].permanentFlags;
    }
    saved.permanentBytes = 25;
    GradoTestExpectedTwoRecoveries(expectedPermanent, saved.permanentFlags);

    record = &gGradoSemanticSaveProbe.records[gGradoSemanticSaveProbe.count++];
    record->route = route;
    record->slot = slot;
    record->backing = 0;
    record->valid = ReadSaveBlockInfo(NULL, slot)
        && (route != GRADO_LOAD_SUSPEND
            || (IsValidSuspendSave(SAVE_ID_SUSPEND)
                && SAVE_ID_SUSPEND + gSuspendSaveIdOffset == slot));
    record->backingValid = ReadSaveBlockInfo(NULL, 0)
        && gSram->gameSaveBlocks[0].bonusClaimFlags == bonus;
    preparedExpected = GradoState_WriteSymbol(GRADO_STATE_LEGITIMACY, 21, 2)
            == GRADO_STATE_VALUE_VALID
        && GradoState_WriteSymbol(GRADO_STATE_RENAIS, 21, 2) == GRADO_STATE_VALUE_VALID;
    if (route == GRADO_LOAD_SUSPEND)
        preparedExpected = preparedExpected
            && GradoState_WriteSymbol(GRADO_FLAG_FINAL_ABANDON, 21, 1)
                == GRADO_STATE_VALUE_VALID;
    memcpy(permanent, GetPermanentFlagBits(), sizeof(permanent));
    memcpy(chapter, GetChapterFlagBits(), sizeof(chapter));
    livePlay = gPlaySt;
    liveUnit = gUnitArrayBlue[0];
    memcpy(sBefore, sSaveImage.bytes, sizeof(sBefore));
    liveValid = GradoState_PreflightSave(
        ClassifySramSaveCompat(), &gSram->expansionSaveMeta,
        route, 21, &live, &liveDiagnostic) == GRADO_STATE_LOAD_VALID;
    nativePreflight = GradoState_PreflightNativeSave(
        route,
        route == GRADO_LOAD_SUSPEND ? SAVE_ID_SUSPEND : SAVE_ID_GAME0,
        &nativeDiagnostic);
    preflight = GradoState_PreflightSave(
        ClassifySramSaveCompat(), &gSram->expansionSaveMeta,
        route, 21, &saved, &diagnostic);
    firstRecovery = diagnostic.missingSymbol == GRADO_STATE_LEGITIMACY
        || diagnostic.missingSymbol == GRADO_STATE_RENAIS;
    preflightPreserved = preparedExpected && liveValid
        && nativePreflight == preflight
        && nativeDiagnostic.sourceSlot == slot
        && nativeDiagnostic.backingSlot
            == (route == GRADO_LOAD_SUSPEND ? SAVE_ID_GAME0
                : GRADO_STATE_NO_NATIVE_SAVE_SLOT)
        && GradoState_ReadSymbolFrom(GRADO_STATE_DIRECTION, 21, &saved, &direction)
            == GRADO_STATE_VALUE_VALID && direction == 4
        && memcmp(expectedPermanent, saved.permanentFlags, sizeof(expectedPermanent)) != 0
        && (route != GRADO_LOAD_SUSPEND
            || memcmp(chapter, saved.chapterFlags, sizeof(chapter)) != 0)
        && memcmp(sBefore, sSaveImage.bytes, sizeof(sBefore)) == 0
        && memcmp(permanent, GetPermanentFlagBits(), sizeof(permanent)) == 0
        && memcmp(chapter, GetChapterFlagBits(), sizeof(chapter)) == 0
        && memcmp(&livePlay, &gPlaySt, sizeof(livePlay)) == 0
        && memcmp(&liveUnit, &gUnitArrayBlue[0], sizeof(liveUnit)) == 0;

    memset(GetPermanentFlagBits(), 0, sizeof(permanent));
    memset(GetChapterFlagBits(), 0, sizeof(chapter));
    gPlaySt.gameSaveSlot = 2;
    memset(&gUnitArrayBlue[0], 0, sizeof(gUnitArrayBlue[0]));
    SetBonusContentClaimFlags(~bonus);
    clobbered = memcmp(GetPermanentFlagBits(), saved.permanentFlags, sizeof(permanent)) != 0
        && gPlaySt.gameSaveSlot != 0 && gUnitArrayBlue[0].pCharacterData == NULL
        && GetBonusContentClaimFlags() != bonus;
    if (route == GRADO_LOAD_SUSPEND)
        ReadSuspendSave(SAVE_ID_SUSPEND);
    else
        ReadGameSave(slot);
    loaded = memcmp(GetPermanentFlagBits(), saved.permanentFlags, sizeof(permanent)) == 0
        && gPlaySt.gameSaveSlot == 0 && gUnitArrayBlue[0].pCharacterData == gCharacterData
        && GetBonusContentClaimFlags() == bonus
        && (route != GRADO_LOAD_SUSPEND
            || memcmp(GetChapterFlagBits(), saved.chapterFlags, sizeof(chapter)) == 0);

    if (stale)
        loaded = loaded && GradoState_WriteSymbol(
            route == GRADO_LOAD_SUSPEND ? GRADO_FLAG_FINAL_ABANDON : GRADO_FLAG_CH00_OMEN,
            21, 1) == GRADO_STATE_VALUE_VALID;
    memcpy(permanent, GetPermanentFlagBits(), sizeof(permanent));
    memcpy(chapter, GetChapterFlagBits(), sizeof(chapter));
    livePlay = gPlaySt;
    liveUnit = gUnitArrayBlue[0];
    memcpy(sBefore, sSaveImage.bytes, sizeof(sBefore));
    applied = GradoState_ApplyLoadRecovery(&nativeDiagnostic);
    if (stale)
        correction = applied == GRADO_STATE_LOAD_CONFLICT
            && nativeDiagnostic.recoveredCount == 0
            && nativeDiagnostic.sourceSlot == slot
            && nativeDiagnostic.backingSlot
                == (route == GRADO_LOAD_SUSPEND ? SAVE_ID_GAME0
                    : GRADO_STATE_NO_NATIVE_SAVE_SLOT)
            && memcmp(permanent, GetPermanentFlagBits(), sizeof(permanent)) == 0;
    else
        correction = applied == GRADO_STATE_LOAD_VALID
            && nativeDiagnostic.recoveredCount == 2
            && nativeDiagnostic.sourceSlot == slot
            && nativeDiagnostic.backingSlot
                == (route == GRADO_LOAD_SUSPEND ? SAVE_ID_GAME0
                    : GRADO_STATE_NO_NATIVE_SAVE_SLOT)
            && WasRecovered(&nativeDiagnostic, GRADO_STATE_LEGITIMACY)
            && WasRecovered(&nativeDiagnostic, GRADO_STATE_RENAIS)
            && GradoState_ReadSymbol(GRADO_STATE_LEGITIMACY, 21, &legitimacy)
                == GRADO_STATE_VALUE_VALID && legitimacy == 2
            && GradoState_ReadSymbol(GRADO_STATE_RENAIS, 21, &renais)
                == GRADO_STATE_VALUE_VALID && renais == 2
            && memcmp(expectedPermanent, GetPermanentFlagBits(), sizeof(expectedPermanent)) == 0;
    record->result = stale ? applied : preflight;
    record->state = preflight == GRADO_STATE_LOAD_RECOVERY && firstRecovery
        && preflightPreserved && clobbered && loaded && correction
        && memcmp(chapter, GetChapterFlagBits(), sizeof(chapter)) == 0
        && memcmp(&livePlay, &gPlaySt, sizeof(livePlay)) == 0
        && memcmp(&liveUnit, &gUnitArrayBlue[0], sizeof(liveUnit)) == 0;
    record->preserved = memcmp(sBefore, sSaveImage.bytes, sizeof(sBefore)) == 0;
    record->bonus = GetBonusContentClaimFlags() == bonus;
}

static void RecordChapterValidation(int savedCorrupt)
{
    const struct GradoStateFlagView live = {
        GetChapterFlagBits(), GetChapterFlagBitsSize(),
        GetPermanentFlagBits(), GetPermanentFlagBitsSize(),
    };
    struct GradoStateFlagView saved;
    struct GradoStateLoadDiagnostic diagnostic;
    volatile struct GradoSemanticSaveRecord * record;
    struct PlaySt livePlay;
    struct Unit liveUnit;
    enum GradoStateValueResult savedValueResult;
    enum GradoStateValueResult liveValueResult;
    enum GradoStateLoadResult savedResult;
    enum GradoStateLoadResult liveResult;
    u8 permanent[25];
    u8 chapter[5];
    u16 savedValue = 0xBEEF;
    u16 liveValue = 0xBEEF;
    u32 bonus = BonusForBacking(0);
    unsigned int slot;
    int otherRoutesValid;

    PrepareCurrent();
    PrepareLive(0, permanent, chapter);
    PrepareSemanticState();
    ResetChapterFlags();
    gPlaySt.chapterIndex = 14;
    gPlaySt.gameSaveSlot = 0;
    SetBonusContentClaimFlags(bonus);
    WriteGameSave(0);
    if (savedCorrupt)
        GradoTestCorruptChapterGuard(GetChapterFlagBits());
    WriteSuspendSave(SAVE_ID_SUSPEND);
    slot = SAVE_ID_SUSPEND + GetLastSuspendSaveId();
    saved.chapterFlags = gSram->suspendSaveBlocks[
        slot - SAVE_ID_SUSPEND].chapterFlags;
    saved.chapterBytes = 5;
    saved.permanentFlags = gSram->suspendSaveBlocks[
        slot - SAVE_ID_SUSPEND].permanentFlags;
    saved.permanentBytes = 25;
    ResetChapterFlags();
    if (!savedCorrupt)
        GradoTestCorruptChapterGuard(GetChapterFlagBits());
    memcpy(permanent, GetPermanentFlagBits(), sizeof(permanent));
    memcpy(chapter, GetChapterFlagBits(), sizeof(chapter));
    livePlay = gPlaySt;
    liveUnit = gUnitArrayBlue[0];
    memcpy(sBefore, sSaveImage.bytes, sizeof(sBefore));

    savedValueResult = GradoState_ReadSymbolFrom(
        GRADO_FLAG_CH13_GUARD, 14, &saved, &savedValue);
    liveValueResult = GradoState_ReadSymbolFrom(
        GRADO_FLAG_CH13_GUARD, 14, &live, &liveValue);
    savedResult = GradoState_PreflightSave(
        ClassifySramSaveCompat(), &gSram->expansionSaveMeta,
        GRADO_LOAD_SUSPEND, 14, &saved, &diagnostic);
    liveResult = GradoState_PreflightSave(
        ClassifySramSaveCompat(), &gSram->expansionSaveMeta,
        GRADO_LOAD_SUSPEND, 14, &live, &diagnostic);
    otherRoutesValid = GradoState_PreflightSave(
            ClassifySramSaveCompat(), &gSram->expansionSaveMeta,
            GRADO_LOAD_NORMAL, 14, &saved, &diagnostic) == GRADO_STATE_LOAD_VALID
        && GradoState_PreflightSave(
            ClassifySramSaveCompat(), &gSram->expansionSaveMeta,
            GRADO_LOAD_NORMAL, 14, &live, &diagnostic) == GRADO_STATE_LOAD_VALID
        && GradoState_PreflightSave(
            ClassifySramSaveCompat(), &gSram->expansionSaveMeta,
            GRADO_LOAD_LEGACY, 14, &saved, &diagnostic) == GRADO_STATE_LOAD_VALID
        && GradoState_PreflightSave(
            ClassifySramSaveCompat(), &gSram->expansionSaveMeta,
            GRADO_LOAD_LEGACY, 14, &live, &diagnostic) == GRADO_STATE_LOAD_VALID;

    record = &gGradoSemanticSaveProbe.records[gGradoSemanticSaveProbe.count++];
    record->route = GRADO_LOAD_SUSPEND;
    record->slot = slot;
    record->backing = 0;
    record->result = savedResult;
    record->state = savedResult == (savedCorrupt
            ? GRADO_STATE_LOAD_CORRUPT : GRADO_STATE_LOAD_VALID)
        && savedValueResult == (savedCorrupt
            ? GRADO_STATE_VALUE_CORRUPT : GRADO_STATE_VALUE_VALID)
        && savedValue == (savedCorrupt ? 0xBEEF : 0)
        && liveValueResult == (savedCorrupt
            ? GRADO_STATE_VALUE_VALID : GRADO_STATE_VALUE_CORRUPT)
        && liveValue == (savedCorrupt ? 0 : 0xBEEF)
        && liveResult == (savedCorrupt ? GRADO_STATE_LOAD_VALID : GRADO_STATE_LOAD_CORRUPT)
        && otherRoutesValid
        && memcmp(permanent, GetPermanentFlagBits(), sizeof(permanent)) == 0
        && memcmp(chapter, GetChapterFlagBits(), sizeof(chapter)) == 0
        && memcmp(&livePlay, &gPlaySt, sizeof(livePlay)) == 0
        && memcmp(&liveUnit, &gUnitArrayBlue[0], sizeof(liveUnit)) == 0;
    record->preserved = memcmp(sBefore, sSaveImage.bytes, sizeof(sBefore)) == 0;
    record->valid = ReadSaveBlockInfo(NULL, slot)
        && IsValidSuspendSave(SAVE_ID_SUSPEND)
        && SAVE_ID_SUSPEND + gSuspendSaveIdOffset == slot;
    record->backingValid = ReadSaveBlockInfo(NULL, 0)
        && gSram->gameSaveBlocks[0].bonusClaimFlags == bonus;
    record->bonus = GetBonusContentClaimFlags() == bonus;
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
    struct PlaySt livePlay;
    u8 permanent[25];
    u8 chapter[5];
    enum GradoStateLoadResult result;
    u32 bonus;
    u16 recoveredValue;
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
    memcpy(permanent, GetPermanentFlagBits(), sizeof(permanent));
    memcpy(chapter, GetChapterFlagBits(), sizeof(chapter));
    memcpy(sBefore, sSaveImage.bytes, sizeof(sBefore));
    livePlay = gPlaySt;
    unit = gUnitArrayBlue[0];
    bonus = GetBonusContentClaimFlags();
    result = GradoState_PreflightSave(
        ClassifySramSaveCompat(), &gSram->expansionSaveMeta,
        route, 21, &live, &diagnostic);
    record = &gGradoSemanticSaveProbe.records[gGradoSemanticSaveProbe.count++];
    record->route = route;
    record->slot = ~0u;
    record->backing = ~0u;
    record->result = result;
    record->state = memcmp(GetPermanentFlagBits(), permanent, sizeof(permanent)) == 0
        && memcmp(GetChapterFlagBits(), chapter, sizeof(chapter)) == 0
        && memcmp(&gPlaySt, &livePlay, sizeof(livePlay)) == 0
        && memcmp(&unit, &gUnitArrayBlue[0], sizeof(unit)) == 0
        && (route != GRADO_LOAD_LEGACY
            || ClassifySramSaveCompat() == SAVE_COMPAT_VALID_LEGACY_OR_VANILLA)
        && (!roderichRecovery
            || (diagnostic.recovery == GRADO_STATE_RECOVERY_RODERICH_EXILED
                && GradoState_ApplyLoadRecovery(&diagnostic) == GRADO_STATE_LOAD_VALID
                && GradoState_ReadSymbol(GRADO_STATE_RODERICH, 21, &recoveredValue)
                    == GRADO_STATE_VALUE_VALID
                && recoveredValue == GRADO_RODERICH_EXILED
                && memcmp(&gPlaySt, &livePlay, sizeof(livePlay)) == 0
                && memcmp(&unit, &gUnitArrayBlue[0], sizeof(unit)) == 0));
    record->preserved = memcmp(sBefore, sSaveImage.bytes, sizeof(sBefore)) == 0;
    record->valid = ~0u;
    record->backingValid = ~0u;
    record->bonus = GetBonusContentClaimFlags() == bonus;
}

static void ProbeSemanticSaves(int mode)
{
    unsigned int backing;
    unsigned int pass;

    gGradoSemanticSaveProbe.phase = 1;
    gGradoSemanticSaveProbe.normalRoute = GRADO_LOAD_NORMAL;
    gGradoSemanticSaveProbe.suspendRoute = GRADO_LOAD_SUSPEND;
    gGradoSemanticSaveProbe.legacyRoute = GRADO_LOAD_LEGACY;
    gGradoSemanticSaveProbe.validResult = GRADO_STATE_LOAD_VALID;
    gGradoSemanticSaveProbe.incompatibleResult = GRADO_STATE_LOAD_INCOMPATIBLE;
    gGradoSemanticSaveProbe.corruptResult = GRADO_STATE_LOAD_CORRUPT;
    gGradoSemanticSaveProbe.missingResult = GRADO_STATE_LOAD_MISSING;
    gGradoSemanticSaveProbe.conflictResult = GRADO_STATE_LOAD_CONFLICT;
    gGradoSemanticSaveProbe.recoveryResult = GRADO_STATE_LOAD_RECOVERY;
    gGradoSemanticSaveProbe.reopenResult = GRADO_STATE_LOAD_REOPEN;
    if (mode == 2)
    {
        RecordChapterValidation(0);
        RecordChapterValidation(1);
    }
    else if (mode == 1)
    {
        RecordSemanticNegative(GRADO_LOAD_NORMAL, GRADO_STATE_LOAD_MISSING, 0);
        RecordSemanticNegative(GRADO_LOAD_SUSPEND, GRADO_STATE_LOAD_CONFLICT, 0);
        RecordSemanticNegative(GRADO_LOAD_NORMAL, GRADO_STATE_LOAD_RECOVERY, 1);
        RecordSemanticNegative(GRADO_LOAD_LEGACY, GRADO_STATE_LOAD_INCOMPATIBLE, 0);
        RecordSemanticNegative(GRADO_LOAD_SUSPEND, GRADO_STATE_LOAD_REOPEN, 0);
        RecordBackedRecovery(GRADO_LOAD_NORMAL, 0);
        RecordBackedRecovery(GRADO_LOAD_SUSPEND, 0);
        RecordBackedRecovery(GRADO_LOAD_NORMAL, 1);
        RecordBackedRecovery(GRADO_LOAD_SUSPEND, 1);
    }
    else
    {
        PrepareSemanticSaves();
        for (backing = SAVE_ID_GAME0; backing <= SAVE_ID_GAME2; ++backing)
            RecordSemantic(GRADO_LOAD_NORMAL, backing);
        for (backing = SAVE_ID_GAME0; backing <= SAVE_ID_GAME2; ++backing)
            for (pass = 0; pass < 2; ++pass)
                RecordSemantic(GRADO_LOAD_SUSPEND, backing);
    }
    gGradoSemanticSaveProbe.phase = 2;
}

static void RecordNativeSavePreflight(
    enum GradoStateLoadRoute route,
    int requestedSlot,
    unsigned int expectedBacking)
{
    struct GradoStateLoadDiagnostic diagnostic;
    volatile struct GradoNativeSaveRecord * record;
    enum GradoStateLoadResult result;

    memcpy(sBefore, sSaveImage.bytes, sizeof(sBefore));
    result = GradoState_PreflightNativeSave(route, requestedSlot, &diagnostic);
    record = &gGradoNativeSaveProbe.records[gGradoNativeSaveProbe.count++];
    record->route = route;
    record->requestedSlot = requestedSlot;
    record->expectedBacking = expectedBacking;
    record->result = result;
    record->sourceSlot = diagnostic.sourceSlot;
    record->backingSlot = diagnostic.backingSlot;
    record->preserved = memcmp(sBefore, sSaveImage.bytes, sizeof(sBefore)) == 0;
}

static void ProbeNativeSavePreflight(void)
{
    unsigned int backing;
    u8 permanent[25];
    u8 chapter[5];
    struct SaveBlockInfo savedInfo;
    struct SaveBlockInfo info;
    struct SuspendSaveBlock * suspendSave;
    unsigned int slot;

    gGradoNativeSaveProbe.phase = 1;
    gGradoNativeSaveProbe.count = 0;

    PrepareCurrent();
    PrepareSemanticSaves();
    RecordNativeSavePreflight(
        GRADO_LOAD_NORMAL, SAVE_ID_GAME2, GRADO_STATE_NO_NATIVE_SAVE_SLOT);

    for (backing = SAVE_ID_GAME1; backing <= SAVE_ID_GAME2; ++backing)
    {
        PrepareLive(backing, permanent, chapter);
        PrepareSemanticState();
        gPlaySt.chapterIndex = 21;
        gPlaySt.gameSaveSlot = backing;
        SetBonusContentClaimFlags(BonusForBacking(backing));
        WriteSuspendSave(SAVE_ID_SUSPEND);
        RecordNativeSavePreflight(
            GRADO_LOAD_SUSPEND, SAVE_ID_SUSPEND, backing);
    }

    savedInfo = gSram->saveBlockInfo[SAVE_ID_GAME2];
    gSram->saveBlockInfo[SAVE_ID_GAME2].offset = 0xFFFF;
    RecordNativeSavePreflight(
        GRADO_LOAD_NORMAL, SAVE_ID_GAME2, GRADO_STATE_NO_NATIVE_SAVE_SLOT);
    gSram->saveBlockInfo[SAVE_ID_GAME2] = savedInfo;

    slot = SAVE_ID_SUSPEND + GetLastSuspendSaveId();
    suspendSave = &gSram->suspendSaveBlocks[slot - SAVE_ID_SUSPEND];
    suspendSave->playSt.gameSaveSlot = SAVE_ID_ARENA;
    memset(&info, 0, sizeof(info));
    info.magic32 = SAVEMAGIC32;
    info.kind = SAVEBLOCK_KIND_SUSPEND;
    WriteSaveBlockInfo(&info, slot);
    RecordNativeSavePreflight(
        GRADO_LOAD_SUSPEND, SAVE_ID_SUSPEND, GRADO_STATE_NO_NATIVE_SAVE_SLOT);

    gGradoNativeSaveProbe.phase = 2;
}

static void ProbeNativeDirectionReopen(
    volatile struct GradoNativeLoadRefusalProbe * probe)
{
    u8 permanent[25];
    u8 chapter[5];
    struct SaveMenuProc saveMenuProc;
    u16 legitimacy = 0;
    u32 savedGold = 2345;
    u32 helpBoxCallsBefore;
    u32 popupStartCallsBefore;
    u32 procGotoCallsBefore;
    enum GradoStateLoadResult result;

    PrepareSemanticSaves();
    PrepareLive(SAVE_ID_GAME0, permanent, chapter);
    PrepareSemanticState();
    GradoState_WriteSymbol(GRADO_STATE_LEGITIMACY, 0, 2);
    GradoState_WriteSymbol(GRADO_STATE_DIRECTION, GRADO_STATE_NO_CHAPTER, 0);
    GradoState_WriteSymbol(GRADO_FLAG_CH18_CLEAR, 0, 1);
    gPlaySt.chapterIndex = 21;
    gPlaySt.partyGoldAmount = savedGold;
    gPlaySt.gameSaveSlot = SAVE_ID_GAME0;
    WriteGameSave(SAVE_ID_GAME0);

    probe->reopenPreflightResult = GradoState_PreflightNativeSave(
        GRADO_LOAD_NORMAL, SAVE_ID_GAME0,
        &(struct GradoStateLoadDiagnostic){ 0 });
    memset(GetPermanentFlagBits(), 0, GetPermanentFlagBitsSize());
    memset(GetChapterFlagBits(), 0, GetChapterFlagBitsSize());
    gPlaySt.partyGoldAmount = 0x5555;
    gPlaySt.gameSaveSlot = SAVE_ID_GAME2;
    result = GradoState_LoadNativeSave(
        GRADO_LOAD_NORMAL, SAVE_ID_GAME0,
        &(struct GradoStateLoadDiagnostic){ 0 });
    probe->reopenLoadResult = result;
    probe->reopenChoiceRestored =
        result == GRADO_STATE_LOAD_REOPEN
        && GradoState_ReadSymbol(
            GRADO_STATE_LEGITIMACY, 0, &legitimacy) == GRADO_STATE_VALUE_VALID
        && legitimacy == 2;
    probe->reopenGoldRestored =
        result == GRADO_STATE_LOAD_REOPEN
        && gPlaySt.partyGoldAmount == savedGold
        && gPlaySt.chapterIndex == 21;

    memset(&saveMenuProc, 0, sizeof(saveMenuProc));
    saveMenuProc.main_sel_bitfile = MAIN_MENU_OPTION_RESTART;
    saveMenuProc.sus_slot = SAVE_ID_GAME0;
    procGotoCallsBefore = sProcGotoCalls;
    sProcGotoLabel = -1;
    GradoState_SaveMenu_NewGame(&saveMenuProc);
    probe->reopenSaveMenuContinued =
        sProcGotoCalls == procGotoCallsBefore + 1
        && sProcGotoLabel == PL_SAVEMENU_NEW_GAME;

    memset(GetPermanentFlagBits(), 0, GetPermanentFlagBitsSize());
    memset(GetChapterFlagBits(), 0, GetChapterFlagBitsSize());
    gPlaySt.partyGoldAmount = 0x5555;
    gPlaySt.gameSaveSlot = SAVE_ID_GAME2;
    helpBoxCallsBefore = sHelpBoxCalls;
    popupStartCallsBefore = sPopupStartCalls;
    memset(&saveMenuProc, 0, sizeof(saveMenuProc));
    probe->reopenTryLoadAccepted =
        GradoState_TryLoadNativeSaveFromProc(
            GRADO_LOAD_NORMAL, SAVE_ID_GAME0, &saveMenuProc);
    probe->reopenTryLoadStateRestored =
        probe->reopenTryLoadAccepted
        && GradoState_ReadSymbol(
            GRADO_STATE_LEGITIMACY, 0, &legitimacy) == GRADO_STATE_VALUE_VALID
        && legitimacy == 2
        && gPlaySt.partyGoldAmount == savedGold;
    probe->reopenTryLoadNoPopup =
        sHelpBoxCalls == helpBoxCallsBefore
        && sPopupStartCalls == popupStartCallsBefore;
}

static void ProbeNativeLoadRefusal(void)
{
    volatile struct GradoNativeLoadRefusalProbe * probe =
        &gGradoNativeLoadRefusalProbe;
    struct PlaySt livePlay;
    struct Unit liveUnit;
    struct MenuProc debugMenuProc;
    struct MenuItemProc menuItem;
    struct SaveMenuProc saveMenuProc;
    struct SioTermProc sioProc;
    struct LinkArenaStMaybe linkArena;
    u8 permanent[25];
    u8 chapter[5];
    u32 bonus = 0x13579BDF;
    int suspendSaveIdOffset;
    enum GradoStateLoadResult result;
    enum GradoStateLoadResult preflightResult;
    u32 helpBoxCallsBeforeMenu;
    u32 popupStartCallsBeforeMenu;
    u32 procGotoCallsBeforeMenu;
    u16 previousNewKeys;
    u32 attempt;
    int popupLifecyclesCorrect;
    int debugCallerRefused;
    int linkArenaCallerPreserved;
    int linkArenaSelectionRefused;

    PrepareSemanticSaves();
    PrepareLive(SAVE_ID_GAME2, permanent, chapter);
    PrepareSemanticState();
    gPlaySt.chapterIndex = 21;
    gPlaySt.gameSaveSlot = SAVE_ID_GAME2;
    SetBonusContentClaimFlags(bonus);
    SetFlag(166);
    WriteGameSave(SAVE_ID_GAME2);

    PrepareLive(1, permanent, chapter);
    PrepareSemanticState();
    gPlaySt.chapterIndex = 21;
    gPlaySt.gameSaveSlot = 1;
    SetBonusContentClaimFlags(bonus);
    SetFlag(166);
    WriteSuspendSave(SAVE_ID_SUSPEND);

    PrepareLive(SAVE_ID_GAME2, permanent, chapter);
    PrepareSemanticState();
    gPlaySt.chapterIndex = 21;
    gPlaySt.gameSaveSlot = SAVE_ID_GAME2;
    SetBonusContentClaimFlags(bonus);
#if FE8_EXPANSION_SAVE_COMPAT_EPOCH == 256
    probe->expectedMessage = MSG_SAVE_COMPAT_UNKNOWN;
#else
    probe->expectedMessage = MSG_SAVE_COMPAT_CONFIG_INCOMPATIBLE;
#endif

    memcpy(sBefore, sSaveImage.bytes, sizeof(sBefore));
    memcpy(permanent, GetPermanentFlagBits(), sizeof(permanent));
    memcpy(chapter, GetChapterFlagBits(), sizeof(chapter));
    livePlay = gPlaySt;
    liveUnit = gUnitArrayBlue[0];
    suspendSaveIdOffset = gSuspendSaveIdOffset;
    sHelpBoxCalls = 0;
    sHelpBoxX = 0;
    sHelpBoxY = 0;
    sHelpBoxMessage = 0;
    sPopupStartCalls = 0;

    sUnwrappedReadSramFast = ReadSramFast;
    sExpansionMetaReadCount = 0;
    sTraceExpansionMetaReads = 1;
    ReadSramFast = TraceSramRead;
    preflightResult = GradoState_PreflightNativeSave(
        GRADO_LOAD_NORMAL, SAVE_ID_GAME2,
        &(struct GradoStateLoadDiagnostic){ 0 });
    sTraceExpansionMetaReads = 0;
    ReadSramFast = sUnwrappedReadSramFast;
    probe->metadataCopyCount = sExpansionMetaReadCount;
    probe->preflightResult = preflightResult;

    memset(&debugMenuProc, 0, sizeof(debugMenuProc));
    helpBoxCallsBeforeMenu = sHelpBoxCalls;
    popupStartCallsBeforeMenu = sPopupStartCalls;
    result = GradoState_TryLoadNativeSaveFromProc(
        GRADO_LOAD_NORMAL, SAVE_ID_GAME2, &debugMenuProc);
    popupLifecyclesCorrect = CompleteManagedRefusalPopup(
        &debugMenuProc, probe->expectedMessage,
        popupStartCallsBeforeMenu, helpBoxCallsBeforeMenu, 0);
    probe->result = result;
    probe->helpBoxCalls = sHelpBoxCalls;
    probe->helpBoxX = sHelpBoxX;
    probe->helpBoxY = sHelpBoxY;
    probe->message = sHelpBoxMessage;
    probe->sramPreserved = memcmp(sBefore, sSaveImage.bytes, sizeof(sBefore)) == 0;
    probe->liveStatePreserved =
        memcmp(GetPermanentFlagBits(), permanent, sizeof(permanent)) == 0
        && memcmp(GetChapterFlagBits(), chapter, sizeof(chapter)) == 0
        && memcmp(&gPlaySt, &livePlay, sizeof(livePlay)) == 0
        && memcmp(&gUnitArrayBlue[0], &liveUnit, sizeof(liveUnit)) == 0
        && GetBonusContentClaimFlags() == bonus
        && gSuspendSaveIdOffset == suspendSaveIdOffset;

    memset(&menuItem, 0, sizeof(menuItem));
    menuItem.availability = MENU_ENABLED;
    debugCallerRefused = 1;
    for (attempt = 0; attempt < 2; ++attempt)
    {
        helpBoxCallsBeforeMenu = sHelpBoxCalls;
        popupStartCallsBeforeMenu = sPopupStartCalls;
        sRestartGameCalls = 0;
        if (DebugContinueMenu_ContinueChapter(&debugMenuProc, &menuItem) != MENU_ACT_SND6B
            || sRestartGameCalls != 0)
            debugCallerRefused = 0;
        if (!CompleteManagedRefusalPopup(
                &debugMenuProc, probe->expectedMessage,
                popupStartCallsBeforeMenu, helpBoxCallsBeforeMenu, 0))
            popupLifecyclesCorrect = 0;
    }
    probe->suspendCallerRefused = debugCallerRefused;
    probe->suspendRestartCount = sRestartGameCalls;
    probe->suspendCallerPreserved =
        memcmp(sBefore, sSaveImage.bytes, sizeof(sBefore)) == 0
        && memcmp(GetPermanentFlagBits(), permanent, sizeof(permanent)) == 0
        && memcmp(GetChapterFlagBits(), chapter, sizeof(chapter)) == 0
        && memcmp(&gPlaySt, &livePlay, sizeof(livePlay)) == 0
        && memcmp(&gUnitArrayBlue[0], &liveUnit, sizeof(liveUnit)) == 0
        && GetBonusContentClaimFlags() == bonus
        && gSuspendSaveIdOffset == suspendSaveIdOffset;

    memset(&sioProc, 0, sizeof(sioProc));
    sioProc.unk_48 = SAVE_ID_GAME2;
    linkArena = gLinkArenaSt;
    sLinkArenaInitCalls = 0;
    sProcGotoCalls = 0;
    sProcGotoLabel = -1;
    linkArenaCallerPreserved = 1;
    for (attempt = 0; attempt < 2; ++attempt)
    {
        helpBoxCallsBeforeMenu = sHelpBoxCalls;
        popupStartCallsBeforeMenu = sPopupStartCalls;
        LinkArenaTeamBuild_LoadSelectedSave(&sioProc);
        if (sProcGotoCalls != attempt + 1
            || sProcGotoLabel != 4
            || sLinkArenaInitCalls != 0)
            linkArenaCallerPreserved = 0;
        if (!CompleteManagedRefusalPopup(
                &sioProc, probe->expectedMessage,
                popupStartCallsBeforeMenu, helpBoxCallsBeforeMenu, 1))
            popupLifecyclesCorrect = 0;
    }
    probe->normalCallerPreserved = linkArenaCallerPreserved
        && memcmp(sBefore, sSaveImage.bytes, sizeof(sBefore)) == 0
        && memcmp(GetPermanentFlagBits(), permanent, sizeof(permanent)) == 0
        && memcmp(GetChapterFlagBits(), chapter, sizeof(chapter)) == 0
        && memcmp(&gPlaySt, &livePlay, sizeof(livePlay)) == 0
        && memcmp(&gUnitArrayBlue[0], &liveUnit, sizeof(liveUnit)) == 0
        && memcmp(&gLinkArenaSt, &linkArena, sizeof(linkArena)) == 0
        && GetBonusContentClaimFlags() == bonus
        && gSuspendSaveIdOffset == suspendSaveIdOffset;
    probe->normalCallerTransitionCount = sLinkArenaInitCalls;
    probe->linkArenaProcGotoCount = sProcGotoCalls;
    probe->linkArenaExitLabel = sProcGotoLabel;

    sioProc.unk_48 = SAVE_ID_GAME2;
    sioProc.unk_38[SAVE_ID_GAME2] = 21;
    gKeyStatusPtr->newKeys = A_BUTTON;
    linkArenaSelectionRefused = 1;
    for (attempt = 0; attempt < 2; ++attempt)
    {
        helpBoxCallsBeforeMenu = sHelpBoxCalls;
        popupStartCallsBeforeMenu = sPopupStartCalls;
        procGotoCallsBeforeMenu = sProcGotoCalls;
        sBrokenProc = NULL;
        LinkArenaTeamBuild_Loop(&sioProc);
        if (sBrokenProc != NULL
            || sProcGotoCalls != procGotoCallsBeforeMenu
            || !CompleteManagedRefusalPopup(
                &sioProc, probe->expectedMessage,
                popupStartCallsBeforeMenu, helpBoxCallsBeforeMenu, 1))
            linkArenaSelectionRefused = 0;
    }
    probe->linkArenaSelectionRefused = linkArenaSelectionRefused;

    memset(&saveMenuProc, 0, sizeof(saveMenuProc));
    saveMenuProc.main_sel_bitfile = MAIN_MENU_OPTION_RESUME;
    saveMenuProc.sus_slot = SAVE_ID_GAME2;
    previousNewKeys = gKeyStatusPtr->newKeys;
    gKeyStatusPtr->newKeys = A_BUTTON;
    procGotoCallsBeforeMenu = sProcGotoCalls;
    helpBoxCallsBeforeMenu = sHelpBoxCalls;
    popupStartCallsBeforeMenu = sPopupStartCalls;
    SaveMenu_SaveSlotSelectLoop(&saveMenuProc);
    probe->saveMenuSuspendRefused =
        sProcGotoCalls == procGotoCallsBeforeMenu
        && CompleteManagedRefusalPopup(
            &saveMenuProc, probe->expectedMessage,
            popupStartCallsBeforeMenu, helpBoxCallsBeforeMenu, 0);
    memset(&saveMenuProc, 0, sizeof(saveMenuProc));
    saveMenuProc.main_sel_bitfile = MAIN_MENU_OPTION_RESTART;
    saveMenuProc.sus_slot = SAVE_ID_GAME2;
    saveMenuProc.sus_slot_cur = 0;
    saveMenuProc.cursor_config = 1;
    saveMenuProc.chapter_idx[0] = 0;
    saveMenuProc.chapter_idx[1] = 1;
    saveMenuProc.chapter_idx[2] = 2;
    saveMenuProc.unk_44 = 0x10;
    saveMenuProc.hb_en = 1;
    gKeyStatusPtr->newKeys = A_BUTTON;
    procGotoCallsBeforeMenu = sProcGotoCalls;
    helpBoxCallsBeforeMenu = sHelpBoxCalls;
    popupStartCallsBeforeMenu = sPopupStartCalls;
    SaveMenu_SaveSlotSelectLoop(&saveMenuProc);
    probe->saveMenuNormalRefused =
        sProcGotoCalls == procGotoCallsBeforeMenu
        && CompleteManagedRefusalPopup(
            &saveMenuProc, probe->expectedMessage,
            popupStartCallsBeforeMenu, helpBoxCallsBeforeMenu, 0)
        && saveMenuProc.unk_44 == 0x100
        && saveMenuProc.sus_slot_cur == 0;

    memset(&saveMenuProc, 0, sizeof(saveMenuProc));
    saveMenuProc.main_sel_bitfile = MAIN_MENU_OPTION_7;
    saveMenuProc.sus_slot = SAVE_ID_GAME2;
    saveMenuProc.sus_slot_cur = 1;
    saveMenuProc.cursor_config = 1;
    saveMenuProc.chapter_idx[0] = 0;
    saveMenuProc.chapter_idx[1] = 1;
    saveMenuProc.chapter_idx[2] = 2;
    saveMenuProc.unk_44 = 0x10;
    saveMenuProc.hb_en = 1;
    gKeyStatusPtr->newKeys = A_BUTTON;
    procGotoCallsBeforeMenu = sProcGotoCalls;
    helpBoxCallsBeforeMenu = sHelpBoxCalls;
    popupStartCallsBeforeMenu = sPopupStartCalls;
    SaveMenu_SaveSlotSelectLoop(&saveMenuProc);
    probe->saveMenuOption7Refused =
        sProcGotoCalls == procGotoCallsBeforeMenu
        && CompleteManagedRefusalPopup(
            &saveMenuProc, probe->expectedMessage,
            popupStartCallsBeforeMenu, helpBoxCallsBeforeMenu, 0)
        && saveMenuProc.unk_44 == 0x100
        && saveMenuProc.sus_slot_cur == 1;

    gKeyStatusPtr->newKeys = previousNewKeys;
    probe->saveMenuPreserved =
        memcmp(sBefore, sSaveImage.bytes, sizeof(sBefore)) == 0
        && memcmp(GetPermanentFlagBits(), permanent, sizeof(permanent)) == 0
        && memcmp(GetChapterFlagBits(), chapter, sizeof(chapter)) == 0
        && memcmp(&gPlaySt, &livePlay, sizeof(livePlay)) == 0
        && memcmp(&gUnitArrayBlue[0], &liveUnit, sizeof(liveUnit)) == 0
        && GetBonusContentClaimFlags() == bonus
        && gSuspendSaveIdOffset == suspendSaveIdOffset;
    probe->saveMenuProcGotoCount = sProcGotoCalls;
    probe->callerHelpBoxCalls = sHelpBoxCalls;
    probe->callerHelpBoxCorrect =
        popupLifecyclesCorrect
        && probe->saveMenuSuspendRefused
        && probe->saveMenuNormalRefused
        && probe->saveMenuOption7Refused
        && probe->linkArenaSelectionRefused
        && sHelpBoxCalls == 10
        && sHelpBoxX == 0x30
        && sHelpBoxY == 0x30
        && sHelpBoxMessage == probe->expectedMessage;
    ProbeNativeDirectionReopen(probe);
#if FE8_EXPANSION_SAVE_COMPAT_EPOCH == 256
    {
        struct SaveBlockInfo validInfo;
        struct GradoStateLoadDiagnostic diagnostic;
        u32 popupStartCallsBefore;
        u32 helpBoxCallsBefore;
        u32 procGotoCallsBefore;
        int popupCorrect;

        PrepareSemanticSaves();
        PrepareLive(SAVE_ID_GAME0, permanent, chapter);
        PrepareSemanticState();
        gPlaySt.chapterIndex = 21;
        gPlaySt.gameSaveSlot = SAVE_ID_GAME0;
        SetBonusContentClaimFlags(bonus);
        validInfo = gSram->saveBlockInfo[SAVE_ID_GAME2];
        gSram->saveBlockInfo[SAVE_ID_GAME2].offset = 0xFFFF;

        memcpy(sBefore, sSaveImage.bytes, sizeof(sBefore));
        memcpy(permanent, GetPermanentFlagBits(), sizeof(permanent));
        memcpy(chapter, GetChapterFlagBits(), sizeof(chapter));
        livePlay = gPlaySt;
        liveUnit = gUnitArrayBlue[0];
        suspendSaveIdOffset = gSuspendSaveIdOffset;

        probe->corruptBlockLoadResult = GradoState_LoadNativeSave(
            GRADO_LOAD_NORMAL, SAVE_ID_GAME2, &diagnostic);
        probe->corruptBlockLoadPreserved =
            memcmp(sBefore, sSaveImage.bytes, sizeof(sBefore)) == 0
            && memcmp(GetPermanentFlagBits(), permanent, sizeof(permanent)) == 0
            && memcmp(GetChapterFlagBits(), chapter, sizeof(chapter)) == 0
            && memcmp(&gPlaySt, &livePlay, sizeof(livePlay)) == 0
            && memcmp(&gUnitArrayBlue[0], &liveUnit, sizeof(liveUnit)) == 0
            && GetBonusContentClaimFlags() == bonus
            && gSuspendSaveIdOffset == suspendSaveIdOffset;

        memset(&saveMenuProc, 0, sizeof(saveMenuProc));
        saveMenuProc.main_sel_bitfile = MAIN_MENU_OPTION_RESTART;
        saveMenuProc.sus_slot = SAVE_ID_GAME2;
        saveMenuProc.sus_slot_cur = 0;
        saveMenuProc.unk_44 = 0x10;
        popupStartCallsBefore = sPopupStartCalls;
        helpBoxCallsBefore = sHelpBoxCalls;
        procGotoCallsBefore = sProcGotoCalls;
        SaveMenu_NewGame(&saveMenuProc);
        popupCorrect = CompleteManagedRefusalPopup(
            &saveMenuProc, MSG_SAVE_COMPAT_UNKNOWN,
            popupStartCallsBefore, helpBoxCallsBefore, 0);
        probe->corruptBlockMenuRefused =
            popupCorrect && sProcGotoCalls == procGotoCallsBefore
            && saveMenuProc.unk_44 == 0x100;
        probe->corruptBlockMenuPreserved =
            memcmp(sBefore, sSaveImage.bytes, sizeof(sBefore)) == 0
            && memcmp(GetPermanentFlagBits(), permanent, sizeof(permanent)) == 0
            && memcmp(GetChapterFlagBits(), chapter, sizeof(chapter)) == 0
            && memcmp(&gPlaySt, &livePlay, sizeof(livePlay)) == 0
            && memcmp(&gUnitArrayBlue[0], &liveUnit, sizeof(liveUnit)) == 0
            && GetBonusContentClaimFlags() == bonus
            && gSuspendSaveIdOffset == suspendSaveIdOffset;
        probe->corruptBlockMenuMessage = sSaveMenuPopupProc.msgId;
        gSram->saveBlockInfo[SAVE_ID_GAME2] = validInfo;
    }
#endif
    probe->phase = 2;
}

void AgbMain(void)
{
    unsigned int pass;
    unsigned int slot;

    gSram = &sSaveImage.native;
    if (!(REG_KEYINPUT & R_BUTTON))
    {
        if (REG_KEYINPUT & SELECT_BUTTON)
        {
            ProbeNativeSavePreflight();
            ProbeNativeLoadRefusal();
        }
        else
            GradoChoiceCheckpoints_Run();
    }
    else if (!(REG_KEYINPUT & A_BUTTON))
    {
        if (REG_KEYINPUT & B_BUTTON)
            ProbeSemanticSaves(0);
        else if (REG_KEYINPUT & L_BUTTON)
            ProbeSemanticSaves(1);
        else
            ProbeSemanticSaves(2);
    }
    else
    {
        gGradoSavePathProbe.phase = 1;
        PrepareCurrent();
        for (slot = 0; slot < 3; ++slot)
            RecordPath(slot, 0, slot);
        for (slot = 0; slot < 3; ++slot)
            for (pass = 0; pass < 2; ++pass)
                RecordPath(3 + 2 * slot + pass, 1, slot);
        gGradoSavePathProbe.phase = 2;
    }
    for (;;)
        __asm__ volatile ("nop");
}
