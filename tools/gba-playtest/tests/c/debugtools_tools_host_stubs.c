/*
 * Issue #11 closure -- host-test-only stub implementations for the small
 * set of GBA-hardware/menu-engine/unit/convoy/event-flag/RNG/save-format
 * symbols that src/debugtools_tools.c (and src/debugtools_registry.c,
 * linked alongside it) reference but this host test never needs the real
 * engine subsystem for (real unit/convoy/flag/RNG/save-format state is
 * owned by src/bmunit.c, src/bmcontainer.c, src/eventinfo.c, src/rng.c,
 * src/bmsave-lib.c -- each with their own deep proc/hardware/SRAM
 * dependency graphs well outside what a debug-tool-behavior host test
 * needs). Each fake below is small, test-controllable (via the
 * DebugToolsHostStub_Set* setters), and -- where the real semantics are
 * simple/pure -- mirrors the real implementation's own logic exactly
 * (SetUnitHp/SetUnitStatus's clamping, see src/bmunit.c) so the host test
 * still proves real behavioral semantics, not just call-count wiring.
 *
 * This file is never compiled into the actual GBA ROM (not referenced by
 * modern.mk/Makefile) and is not itself part of the debug-tools feature.
 */
#define _GNU_SOURCE

#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>

#include "global.h"
#include "hardware.h"
#include "face.h"
#include "fontgrp.h"
#include "uimenu.h"
#include "proc.h"
#include "bmunit.h"
#include "bmmap.h"
#include "bmudisp.h"
#include "bmcontainer.h"
#include "event.h"
#include "ekrbattle.h"
#include "playerphase.h"
#include "cp_common.h"
#include "eventinfo.h"
#include "rng.h"
#include "bmsave.h"
#include "save_format.h"
#include "expansion_debugtools.h"
#include "debugtools_internal.h"

/* --- Hardware/menu stand-ins (mirrors debugtools_actions_host_stubs.c) - */

struct KeyStatusBuffer gDebugToolsToolsTestKeyStatus = {0};
struct KeyStatusBuffer * CONST_DATA gKeyStatusPtr = &gDebugToolsToolsTestKeyStatus;

struct LCDControlBuffer gLCDControlBuffer = {0};
static struct Font sDebugToolsToolsTestFont = {0};
struct Font* gActiveFont = &sDebugToolsToolsTestFont;

static u16 sToolsStubBgMap[32 * 32];
u16 gPaletteBuffer[0x200] = {0};

int gDebugToolsToolsHostStubPutFaceChibiCallCount = 0;
int gDebugToolsToolsHostStubLastFaceChibiId = -1;
int gDebugToolsToolsHostStubLastFaceChibiChr = -1;
int gDebugToolsToolsHostStubLastFaceChibiPal = -1;
int gDebugToolsToolsHostStubLastFaceChibiFlipped = -1;
int gDebugToolsToolsHostStubBgSyncCallCount = 0;
int gDebugToolsToolsHostStubLastBgSyncMask = 0;
int gDebugToolsToolsHostStubStartFace2CallCount = 0;
int gDebugToolsToolsHostStubLastStartFaceId = -1;
int gDebugToolsToolsHostStubLastEyeControl = -1;
int gDebugToolsToolsHostStubFaceMouthInitCount = 0;
int gDebugToolsToolsHostStubFaceMouthLoopCount = 0;

static struct FaceProc sDebugToolsToolsFakeFace;
static struct FaceBlinkProc sDebugToolsToolsFakeMouth;

static void DebugToolsHostStub_MapVram(void) __attribute__((constructor));

static void DebugToolsHostStub_MapVram(void)
{
    void* mapped;

    mapped = mmap(
        (void*)VRAM,
        VRAM_SIZE,
        PROT_READ | PROT_WRITE,
        MAP_PRIVATE | MAP_ANONYMOUS | MAP_FIXED,
        -1,
        0);
    if (mapped != (void*)VRAM)
        abort();
}

u16* BG_GetMapBuffer(int bg)
{
    (void)bg;
    return sToolsStubBgMap;
}

void BG_EnableSyncByMask(int bgMask)
{
    gDebugToolsToolsHostStubBgSyncCallCount++;
    gDebugToolsToolsHostStubLastBgSyncMask = bgMask;
}

void PutFaceChibi(int faceId, u16* tilemap, int chr, int pal, s8 isFlipped)
{
    (void)tilemap;

    gDebugToolsToolsHostStubPutFaceChibiCallCount++;
    gDebugToolsToolsHostStubLastFaceChibiId = faceId;
    gDebugToolsToolsHostStubLastFaceChibiChr = chr;
    gDebugToolsToolsHostStubLastFaceChibiPal = pal;
    gDebugToolsToolsHostStubLastFaceChibiFlipped = isFlipped;
    *(u32*)(VRAM + chr * CHR_SIZE + 0x20) = 0xE1A2B3C4;
    gPaletteBuffer[pal * 0x10] = 0x1234;
    gPaletteBuffer[pal * 0x10 + 1] = 0x5678;
}

struct FaceProc* StartFace2(int slot, int faceId, int x, int y, int displayBits)
{
    (void)slot;
    (void)x;
    (void)y;

    memset(&sDebugToolsToolsFakeFace, 0, sizeof(sDebugToolsToolsFakeFace));
    memset(&sDebugToolsToolsFakeMouth, 0, sizeof(sDebugToolsToolsFakeMouth));
    sDebugToolsToolsFakeFace.faceId = faceId;
    sDebugToolsToolsFakeFace.displayBits = displayBits;
    sDebugToolsToolsFakeFace.oam2 = 0x120;
    sDebugToolsToolsFakeFace.unk_44 = &sDebugToolsToolsFakeMouth;
    sDebugToolsToolsFakeMouth.pFaceProc = &sDebugToolsToolsFakeFace;
    gDebugToolsToolsHostStubStartFace2CallCount++;
    gDebugToolsToolsHostStubLastStartFaceId = faceId;
    return &sDebugToolsToolsFakeFace;
}

void SetFaceEyeControl(struct FaceProc* face, int control)
{
    gDebugToolsToolsHostStubLastEyeControl = control;
    face->pBlinkProc = &sDebugToolsToolsFakeMouth;
}

int GetFaceDisplayBits(struct FaceProc* face)
{
    return face->displayBits;
}

void FaceMouth_Init(struct FaceBlinkProc* mouth)
{
    (void)mouth;
    gDebugToolsToolsHostStubFaceMouthInitCount++;
}

void FaceMouth_Loop(struct FaceBlinkProc* mouth)
{
    u32* tiles = (u32*)(
        VRAM + (((mouth->pFaceProc->oam2 + 28) & 0x3FF) * CHR_SIZE));
    u32 frame = mouth->blinkControl ? 0xC3020100 : 0xA1000102;

    tiles[1] = frame;
    tiles[9] = 0;
    tiles[17] = 0;
    tiles[25] = 0;
    gDebugToolsToolsHostStubFaceMouthLoopCount++;
}

void SetupDebugFontForBG(int bg, int tileDataOffset)
{
    (void)bg;
    (void)tileDataOffset;
}

void PrintDebugStringToBG(u16* bg, const char* asciiStr)
{
    (void)bg;
    (void)asciiStr;
}

u8 MenuAlwaysEnabled(const struct MenuItemDef* def, int number)
{
    (void)def;
    (void)number;
    return 1;
}

u8 MenuCancelSelect(struct MenuProc* menu, struct MenuItemProc* item)
{
    (void)menu;
    (void)item;
    return 0;
}

struct Proc* EndMenu(struct MenuProc* proc)
{
    if (proc != NULL && proc->def != NULL && proc->def->onEnd != NULL)
        proc->def->onEnd(proc);

    return (struct Proc*)proc;
}

int gDebugToolsToolsHostStub_StartOrphanMenuCallCount = 0;
const struct MenuDef* gDebugToolsToolsHostStub_LastMenuDef = NULL;
static struct Proc sDebugToolsToolsTransitionProc;
static ProcPtr sDebugToolsToolsPendingTransition;

struct MenuProc* StartOrphanMenu(const struct MenuDef* def)
{
    gDebugToolsToolsHostStub_StartOrphanMenuCallCount++;
    gDebugToolsToolsHostStub_LastMenuDef = def;
    return NULL;
}

ProcPtr Proc_Start(const struct ProcCmd* script, ProcPtr parent)
{
    (void)parent;

    if (script == gProcScr_DebugToolsMenuTransition)
    {
        sDebugToolsToolsPendingTransition = &sDebugToolsToolsTransitionProc;
        return &sDebugToolsToolsTransitionProc;
    }

    return (ProcPtr)1;
}

void DebugToolsHostStub_RunPendingTransition(void)
{
    ProcPtr proc = sDebugToolsToolsPendingTransition;

    sDebugToolsToolsPendingTransition = NULL;
    DebugTools_RunMenuTransition(proc);
}

/* --- Live-map ownership and conflict stand-ins ------------------------- */

struct ProcCmd gProcScr_PlayerPhase[] = {
    PROC_END
};

static int sDebugToolsToolsPlayerPhaseActive = 1;
static int sDebugToolsToolsEventActive = 0;
static int sDebugToolsToolsBattleEventActive = 0;
static int sDebugToolsToolsBattleActive = 0;

ProcPtr Proc_Find(const struct ProcCmd* script)
{
    if (script == gProcScr_PlayerPhase && sDebugToolsToolsPlayerPhaseActive)
        return (ProcPtr)1;

    return NULL;
}

s8 EventEngineExists(void)
{
    return sDebugToolsToolsEventActive;
}

int BattleEventEngineExists(void)
{
    return sDebugToolsToolsBattleEventActive;
}

int IsBattleDeamonActive(void)
{
    return sDebugToolsToolsBattleActive;
}

void DebugToolsHostStub_SetUnitEditContext(
    int playerPhaseActive,
    int eventActive,
    int battleEventActive,
    int battleActive)
{
    sDebugToolsToolsPlayerPhaseActive = playerPhaseActive;
    sDebugToolsToolsEventActive = eventActive;
    sDebugToolsToolsBattleEventActive = battleEventActive;
    sDebugToolsToolsBattleActive = battleActive;
}

/* This driver links the real src/debugtools_registry.c alongside
 * src/debugtools_tools.c so the shared deferred return-to-hub path is the
 * real implementation -- but neither the
 * real launcher (src/debugtools_launcher.c) nor the real Weather/Fog
 * adapter (src/debugtools_actions.c) is linked here (out of scope for
 * this tools-focused driver), so their lazy-registration call sites still
 * need a stand-in. */
void DebugTools_RegisterBuiltinActions(void)
{
}

void DebugTools_RegisterChapter4PrepAction(void)
{
    /* Issue #11 closure: the real implementation (src/debugtools_launcher.c)
     * has its own dedicated host tests and is deliberately not linked
     * here; src/debugtools_registry.c's DebugTools_OpenHub calls this
     * function unconditionally, so a stand-in is needed here too. */
}

void DebugTools_RegisterWeatherFogActions(void)
{
}

/* --- gPlaySt: DebugTools_PrepHotkeyCheck (src/debugtools_registry.c) and
 * this driver's own Flag-tool inspect assertions read
 * gPlaySt.chapterStateBits/chapterIndex. --------------------------------- */
struct PlaySt gPlaySt = {0};
struct BmSt gBmSt = {0};

enum
{
    DEBUGTOOLS_TOOLS_MAP_WIDTH = 8,
    DEBUGTOOLS_TOOLS_MAP_HEIGHT = 8
};

static u8 sDebugToolsToolsMapUnits[DEBUGTOOLS_TOOLS_MAP_HEIGHT][DEBUGTOOLS_TOOLS_MAP_WIDTH];
static u8* sDebugToolsToolsMapUnitRows[DEBUGTOOLS_TOOLS_MAP_HEIGHT] = {
    sDebugToolsToolsMapUnits[0],
    sDebugToolsToolsMapUnits[1],
    sDebugToolsToolsMapUnits[2],
    sDebugToolsToolsMapUnits[3],
    sDebugToolsToolsMapUnits[4],
    sDebugToolsToolsMapUnits[5],
    sDebugToolsToolsMapUnits[6],
    sDebugToolsToolsMapUnits[7],
};

struct Vec2 gBmMapSize = {
    DEBUGTOOLS_TOOLS_MAP_WIDTH,
    DEBUGTOOLS_TOOLS_MAP_HEIGHT
};
u8** gBmMapUnit = sDebugToolsToolsMapUnitRows;

/* --- Unit inspector fakes -----------------------------------------------
 * GetUnitMaxHp/GetUnitCurrentHp/SetUnitHp/SetUnitStatus mirror
 * src/bmunit.c's own real clamping logic exactly (simple, pure struct
 * field access -- see that file's inline definitions) so this host test
 * proves real HP-clamp/status-clear semantics, not just call wiring.
 * GetUnitFromCharId is the one genuinely fake lookup: test-controllable
 * via DebugToolsHostStub_SetFakeUnit, so both the "target found" and
 * "target not found" paths are directly, deterministically testable. */

static struct Unit sDebugToolsToolsFakeUnit;
static struct CharacterData sDebugToolsToolsFakeCharacter;
static struct ClassData sDebugToolsToolsFakeClass;
static int sDebugToolsToolsFakeUnitPresent = 0;
int gDebugToolsToolsHostStubRefreshEntityMapCount = 0;
int gDebugToolsToolsHostStubRenderMapCount = 0;
int gDebugToolsToolsHostStubRefreshUnitSpritesCount = 0;
int gDebugToolsToolsHostStubUnitCheckStatCapsCount = 0;
int gDebugToolsToolsHostStubChangeUnitAiCount = 0;
int gDebugToolsToolsHostStubLastAiA = -1;
int gDebugToolsToolsHostStubLastAiB = -1;

void DebugToolsHostStub_SetFakeUnit(int present, int curHp, int maxHp)
{
    memset(sDebugToolsToolsMapUnits, 0, sizeof(sDebugToolsToolsMapUnits));
    sDebugToolsToolsFakeUnitPresent = present;
    gBmSt.playerCursor.x = 2;
    gBmSt.playerCursor.y = 3;
    DebugToolsHostStub_SetUnitEditContext(1, 0, 0, 0);

    if (present)
    {
        memset(&sDebugToolsToolsFakeUnit, 0, sizeof(sDebugToolsToolsFakeUnit));
        memset(&sDebugToolsToolsFakeCharacter, 0, sizeof(sDebugToolsToolsFakeCharacter));
        memset(&sDebugToolsToolsFakeClass, 0, sizeof(sDebugToolsToolsFakeClass));
        sDebugToolsToolsFakeCharacter.number = 1;
        sDebugToolsToolsFakeClass.number = 1;
        sDebugToolsToolsFakeClass.maxHP = 60;
        sDebugToolsToolsFakeClass.maxPow = 30;
        sDebugToolsToolsFakeClass.maxSkl = 30;
        sDebugToolsToolsFakeClass.maxSpd = 30;
        sDebugToolsToolsFakeClass.maxDef = 30;
        sDebugToolsToolsFakeClass.maxRes = 30;
        sDebugToolsToolsFakeClass.maxCon = 25;
        sDebugToolsToolsFakeClass.baseCon = 5;
        sDebugToolsToolsFakeClass.baseMov = 5;
        sDebugToolsToolsFakeUnit.pCharacterData = &sDebugToolsToolsFakeCharacter;
        sDebugToolsToolsFakeUnit.pClassData = &sDebugToolsToolsFakeClass;
        sDebugToolsToolsFakeUnit.index = 1;
        sDebugToolsToolsFakeUnit.xPos = 2;
        sDebugToolsToolsFakeUnit.yPos = 3;
        sDebugToolsToolsFakeUnit.curHP = (s8)curHp;
        sDebugToolsToolsFakeUnit.maxHP = (s8)maxHp;
        sDebugToolsToolsFakeUnit.pow = 5;
        sDebugToolsToolsFakeUnit.skl = 6;
        sDebugToolsToolsFakeUnit.spd = 7;
        sDebugToolsToolsFakeUnit.def = 8;
        sDebugToolsToolsFakeUnit.res = 9;
        sDebugToolsToolsFakeUnit.lck = 10;
        sDebugToolsToolsMapUnits[3][2] = 1;
    }
}

struct Unit* DebugToolsHostStub_GetFakeUnit(void)
{
    return &sDebugToolsToolsFakeUnit;
}

struct ClassData* DebugToolsHostStub_GetFakeClass(void)
{
    return &sDebugToolsToolsFakeClass;
}

void DebugToolsHostStub_SetCursor(int x, int y)
{
    gBmSt.playerCursor.x = x;
    gBmSt.playerCursor.y = y;
}

void DebugToolsHostStub_MoveFakeUnit(int x, int y)
{
    if (sDebugToolsToolsFakeUnit.xPos >= 0
        && sDebugToolsToolsFakeUnit.xPos < DEBUGTOOLS_TOOLS_MAP_WIDTH
        && sDebugToolsToolsFakeUnit.yPos >= 0
        && sDebugToolsToolsFakeUnit.yPos < DEBUGTOOLS_TOOLS_MAP_HEIGHT)
        sDebugToolsToolsMapUnits[sDebugToolsToolsFakeUnit.yPos][sDebugToolsToolsFakeUnit.xPos] = 0;

    sDebugToolsToolsFakeUnit.xPos = x;
    sDebugToolsToolsFakeUnit.yPos = y;
    if (x >= 0 && x < DEBUGTOOLS_TOOLS_MAP_WIDTH
        && y >= 0 && y < DEBUGTOOLS_TOOLS_MAP_HEIGHT)
        sDebugToolsToolsMapUnits[y][x] = (u8)sDebugToolsToolsFakeUnit.index;
}

struct Unit* GetUnitFromCharId(int characterNumber)
{
    (void)characterNumber;

    if (!sDebugToolsToolsFakeUnitPresent)
        return NULL;

    return &sDebugToolsToolsFakeUnit;
}

struct Unit* GetUnit(int slot)
{
    if (!sDebugToolsToolsFakeUnitPresent
        || slot != (u8)sDebugToolsToolsFakeUnit.index)
        return NULL;

    return &sDebugToolsToolsFakeUnit;
}

const struct CharacterData* GetCharacterData(int characterNumber)
{
    if (characterNumber != sDebugToolsToolsFakeCharacter.number)
        return NULL;

    return &sDebugToolsToolsFakeCharacter;
}

const struct ClassData* GetClassData(int classNumber)
{
    if (classNumber != sDebugToolsToolsFakeClass.number)
        return NULL;

    return &sDebugToolsToolsFakeClass;
}

int GetUnitMaxHp(struct Unit* unit)
{
    return unit->maxHP;
}

int GetUnitCurrentHp(struct Unit* unit)
{
    return unit->curHP;
}

void SetUnitHp(struct Unit* unit, int value)
{
    unit->curHP = (s8)value;

    if (unit->curHP > GetUnitMaxHp(unit))
        unit->curHP = (s8)GetUnitMaxHp(unit);
}

void SetUnitStatus(struct Unit* unit, int status)
{
    if (status == 0)
    {
        unit->statusIndex = 0;
        unit->statusDuration = 0;
    }
    else
    {
        unit->statusIndex = (u8)status;
        unit->statusDuration = 5;
    }
}

void UnitCheckStatCaps(struct Unit* unit)
{
    gDebugToolsToolsHostStubUnitCheckStatCapsCount++;

    if (unit->maxHP > 60)
        unit->maxHP = 60;
    if (unit->pow > unit->pClassData->maxPow)
        unit->pow = unit->pClassData->maxPow;
    if (unit->skl > unit->pClassData->maxSkl)
        unit->skl = unit->pClassData->maxSkl;
    if (unit->spd > unit->pClassData->maxSpd)
        unit->spd = unit->pClassData->maxSpd;
    if (unit->def > unit->pClassData->maxDef)
        unit->def = unit->pClassData->maxDef;
    if (unit->res > unit->pClassData->maxRes)
        unit->res = unit->pClassData->maxRes;
    if (unit->lck > 30)
        unit->lck = 30;
}

void ChangeUnitAi(struct Unit* unit, u8 aiA, u8 aiB, u8 unused)
{
    (void)unused;
    gDebugToolsToolsHostStubChangeUnitAiCount++;
    gDebugToolsToolsHostStubLastAiA = aiA;
    gDebugToolsToolsHostStubLastAiB = aiB;

    if (unit->state & (US_HIDDEN | US_DEAD))
        return;

    if (aiA != AI_A_INVALID)
    {
        unit->ai1 = aiA;
        unit->ai_a_pc = 0;
    }

    if (aiB != AI_B_INVALID)
    {
        unit->ai2 = aiB;
        unit->ai_b_pc = 0;
        if (aiB == AI_B_0C)
            unit->aiFlags |= AI_UNIT_FLAG_3;
    }
}

void RefreshEntityBmMaps(void)
{
    gDebugToolsToolsHostStubRefreshEntityMapCount++;
}

void RenderBmMap(void)
{
    gDebugToolsToolsHostStubRenderMapCount++;
}

void RefreshUnitSprites(void)
{
    gDebugToolsToolsHostStubRefreshUnitSpritesCount++;
}

/* --- Convoy inspector fakes ---------------------------------------------
 * A small test-controllable item count/capacity, mirroring
 * src/bmcontainer.c's own AddItemToConvoy contract (returns -1 without
 * mutating anything when full). */

static int sDebugToolsToolsFakeConvoyCount = 0;
static int sDebugToolsToolsFakeConvoyFull = 0;

void DebugToolsHostStub_SetFakeConvoy(int count, int full)
{
    sDebugToolsToolsFakeConvoyCount = count;
    sDebugToolsToolsFakeConvoyFull = full;
}

int GetConvoyItemCount(void)
{
    return sDebugToolsToolsFakeConvoyCount;
}

int AddItemToConvoy(int item)
{
    (void)item;

    if (sDebugToolsToolsFakeConvoyFull)
        return -1;

    sDebugToolsToolsFakeConvoyCount++;
    return sDebugToolsToolsFakeConvoyCount - 1;
}

/* --- Flag/chapter/event state fakes --------------------------------------
 * A small fixed-size chapter-scoped flag bit array, matching the real
 * GetChapterFlagBitsSize() == 5 (40 bits) exactly. */

static u8 sDebugToolsToolsFakeChapterFlagBits[5];

void DebugToolsHostStub_ClearFakeFlags(void)
{
    memset(sDebugToolsToolsFakeChapterFlagBits, 0, sizeof(sDebugToolsToolsFakeChapterFlagBits));
}

void SetFlag(int flag)
{
    sDebugToolsToolsFakeChapterFlagBits[flag / 8] |= (u8)(1 << (flag % 8));
}

void ClearFlag(int flag)
{
    sDebugToolsToolsFakeChapterFlagBits[flag / 8] &= (u8)~(1 << (flag % 8));
}

bool CheckFlag(int flag)
{
    return (sDebugToolsToolsFakeChapterFlagBits[flag / 8] >> (flag % 8)) & 1;
}

int GetChapterFlagBitsSize(void)
{
    return 5;
}

/* --- RNG inspect/control fakes -------------------------------------------
 * Test-controllable seed state; InitRN/SetLCGRNValue/AdvanceGetLCGRNValue
 * are wired so a reseed is directly observable (the new seed value is
 * mirrored into seeds[0]), without reimplementing the real LCG/Fibonacci
 * generator math src/rng.c owns. */

static u16 sDebugToolsToolsFakeRngSeeds[3] = {0x1111, 0x2222, 0x3333};
static s32 sDebugToolsToolsFakeLcgValue = 0;

void StoreRNState(u16* seeds)
{
    seeds[0] = sDebugToolsToolsFakeRngSeeds[0];
    seeds[1] = sDebugToolsToolsFakeRngSeeds[1];
    seeds[2] = sDebugToolsToolsFakeRngSeeds[2];
}

void LoadRNState(const u16* seeds)
{
    sDebugToolsToolsFakeRngSeeds[0] = seeds[0];
    sDebugToolsToolsFakeRngSeeds[1] = seeds[1];
    sDebugToolsToolsFakeRngSeeds[2] = seeds[2];
}

void SetLCGRNValue(s32 seed)
{
    sDebugToolsToolsFakeLcgValue = seed;
}

unsigned AdvanceGetLCGRNValue(void)
{
    return (unsigned)sDebugToolsToolsFakeLcgValue;
}

void InitRN(s32 seed)
{
    sDebugToolsToolsFakeRngSeeds[0] = (u16)seed;
    sDebugToolsToolsFakeRngSeeds[1] = (u16)(seed + 1);
    sDebugToolsToolsFakeRngSeeds[2] = (u16)(seed + 2);
}

int NextRN(void) { return 0; }
void InitRN_Unused(void) {}
int NextRN_100(void) { return 0; }
int NextRN_N(int max) { (void)max; return 0; }
s8 Roll1RN(int threshold) { (void)threshold; return 0; }
s8 Roll2RN(int threshold) { (void)threshold; return 0; }

/* --- Save compatibility/state inspection fakes --------------------------- */

static enum SaveCompatState sDebugToolsToolsFakeSaveCompatState = SAVE_COMPAT_CURRENT;

void DebugToolsHostStub_SetFakeSaveCompatState(enum SaveCompatState state)
{
    sDebugToolsToolsFakeSaveCompatState = state;
}

enum SaveCompatState ClassifySramSaveCompat(void)
{
    return sDebugToolsToolsFakeSaveCompatState;
}
