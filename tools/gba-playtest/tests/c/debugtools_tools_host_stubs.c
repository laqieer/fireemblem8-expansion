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
#include "bmcontainer.h"
#include "eventinfo.h"
#include "rng.h"
#include "bmsave.h"
#include "save_format.h"
#include "expansion_debugtools.h"
#include "expansion_debug_save_fixture.h"
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

void Text_DrawString(struct Text* text, const char* str)
{
    (void)text;
    (void)str;
}

void PutText(struct Text* text, u16* dest)
{
    (void)text;
    (void)dest;
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

enum DebugSaveFixtureResult DebugSaveFixture_PrepareGame(
    enum DebugSaveFixtureGameSlot slot,
    const struct DebugSaveFixtureOverrides* overrides,
    struct DebugSaveFixturePreview* preview)
{
    (void)slot;
    (void)overrides;
    (void)preview;
    return DEBUG_SAVE_FIXTURE_ERR_NOT_TITLE;
}

enum DebugSaveFixtureResult DebugSaveFixture_PrepareLatestSuspend(
    const struct DebugSaveFixtureOverrides* overrides,
    struct DebugSaveFixturePreview* preview)
{
    (void)overrides;
    (void)preview;
    return DEBUG_SAVE_FIXTURE_ERR_NOT_TITLE;
}

enum DebugSaveFixtureResult DebugSaveFixture_Arm(
    const struct DebugSaveFixtureTarget* target)
{
    (void)target;
    return DEBUG_SAVE_FIXTURE_ERR_CONFIRMATION_ORDER;
}

enum DebugSaveFixtureResult DebugSaveFixture_RequestContinue(
    const struct DebugSaveFixtureTarget* target)
{
    (void)target;
    return DEBUG_SAVE_FIXTURE_ERR_CONFIRMATION_ORDER;
}

void DebugSaveFixture_Abort(enum DebugSaveFixtureAbortReason reason)
{
    (void)reason;
}

int DebugSaveFixture_CanPrepare(void)
{
    return FALSE;
}

int DebugSaveFixture_IsPersistenceBlocked(void)
{
    return FALSE;
}

enum DebugSaveFixturePhase DebugSaveFixture_GetPhase(void)
{
    return DEBUG_SAVE_FIXTURE_EMPTY;
}

enum DebugSaveFixtureResult DebugSaveFixture_GetLastResult(void)
{
    return DEBUG_SAVE_FIXTURE_ERR_NOT_TITLE;
}

const struct DebugSaveFixturePreview* DebugSaveFixture_GetPreview(void)
{
    return NULL;
}

/* --- gPlaySt: DebugTools_PrepHotkeyCheck (src/debugtools_registry.c) and
 * this driver's own Flag-tool inspect assertions read
 * gPlaySt.chapterStateBits/chapterIndex. --------------------------------- */
struct PlaySt gPlaySt = {0};

/* --- Unit inspector fakes -----------------------------------------------
 * GetUnitMaxHp/GetUnitCurrentHp/SetUnitHp/SetUnitStatus mirror
 * src/bmunit.c's own real clamping logic exactly (simple, pure struct
 * field access -- see that file's inline definitions) so this host test
 * proves real HP-clamp/status-clear semantics, not just call wiring.
 * GetUnitFromCharId is the one genuinely fake lookup: test-controllable
 * via DebugToolsHostStub_SetFakeUnit, so both the "target found" and
 * "target not found" paths are directly, deterministically testable. */

static struct Unit sDebugToolsToolsFakeUnit;
static int sDebugToolsToolsFakeUnitPresent = 0;

void DebugToolsHostStub_SetFakeUnit(int present, int curHp, int maxHp)
{
    sDebugToolsToolsFakeUnitPresent = present;

    if (present)
    {
        memset(&sDebugToolsToolsFakeUnit, 0, sizeof(sDebugToolsToolsFakeUnit));
        /* Any non-NULL pointer satisfies UNIT_IS_VALID's pCharacterData
         * check; this driver never dereferences it. */
        sDebugToolsToolsFakeUnit.pCharacterData = (const struct CharacterData*)&sDebugToolsToolsFakeUnit;
        sDebugToolsToolsFakeUnit.curHP = (s8)curHp;
        sDebugToolsToolsFakeUnit.maxHP = (s8)maxHp;
    }
}

struct Unit* GetUnitFromCharId(int charId)
{
    (void)charId;

    if (!sDebugToolsToolsFakeUnitPresent)
        return NULL;

    return &sDebugToolsToolsFakeUnit;
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
