#include "global.h"

#ifndef FE8_ARCHIVAL_BUILD

#include <string.h>

#include "bm.h"
#include "bmmap.h"
#include "bmunit.h"
#include "ekrbattle.h"
#include "event.h"
#include "fontgrp.h"
#include "hardware.h"
#include "mapanim.h"
#include "playerphase.h"
#include "prepscreen.h"
#include "proc.h"
#include "rng.h"
#include "uimenu.h"
#include "expansion_debugtools.h"
#include "debugtools_internal.h"

#include "constants/video-global.h"

#if FE8_EXPANSION_DEBUGTOOLS_ENABLED

extern struct Font* gActiveFont;
extern struct ProcCmd gProcScr_TitleScreen[];
#if defined(FE8_DEBUGTOOLS_DIAGNOSTICS_RUNTIME_TEST)
static u8 sDebugToolsRuntimeForceBattleOwner;
#endif

enum
{
    DEBUGTOOLS_DISPLAY_X = 1,
    DEBUGTOOLS_DISPLAY_Y = 1,
    DEBUGTOOLS_DISPLAY_WIDTH = DEBUGTOOLS_MENU_WIDTH_TILES,
    DEBUGTOOLS_DISPLAY_HEIGHT = 22,
    DEBUGTOOLS_DISPLAY_HALFWORDS =
        DEBUGTOOLS_DISPLAY_WIDTH * DEBUGTOOLS_DISPLAY_HEIGHT * 2,
    DEBUGTOOLS_DISPLAY_OVERLAY_BYTES = 0x630,
    DEBUGTOOLS_DISPLAY_OVERLAY_HALFWORDS =
        DEBUGTOOLS_DISPLAY_OVERLAY_BYTES / sizeof(u16),
    DEBUGTOOLS_END_EXTERNAL = 0,
    DEBUGTOOLS_END_FORCED,
    DEBUGTOOLS_END_NORMAL,
};

struct DebugToolsDiagnosticsState
{
    u32 sequence;
    u8 context;
    u8 reserved[3];
};

union DebugToolsDiagnosticsScratch
{
    struct DebugToolsDiagnosticsSnapshot snapshot;
    char statusLine[64];
};

struct DebugToolsDisplayOwnerProc
{
    PROC_HEADER;
    u32 lcdHash;
    u32 bg2Hash;
    struct Font* restoreFont;
    u16 fontCounter;
    u16 bg0X;
    u16 bg0Y;
    u16 bg1X;
    u16 bg1Y;
    u16 greenTextColor;
    u8 lockBaseline;
    u8 ownerLock;
    u8 restoring;
    u8 endMode;
};

struct DebugToolsDiagnosticsScratchProc
{
    PROC_HEADER;
    union DebugToolsDiagnosticsScratch scratch;
};

EWRAM_OVERLAY(debugtools) static u16
    sDebugToolsDisplayBackup[DEBUGTOOLS_DISPLAY_OVERLAY_HALFWORDS];
SECTION("debugtools_diagnostics_data") static struct DebugToolsDiagnosticsState
    sDiagnosticsState = {0};
#if defined(FE8_DEBUGTOOLS_DIAGNOSTICS_RUNTIME_TEST) \
    || defined(FE8_DEBUGTOOLS_DIAGNOSTICS_PROBE_TEST)
SECTION("debugtools_diagnostics_data") struct DebugToolsDiagnosticsProbe
    gDebugToolsDiagnosticsProbe = {0};
#endif
typedef char DebugToolsDiagnosticsSnapshotSizeMustBe0x40[
    sizeof(struct DebugToolsDiagnosticsSnapshot) == 0x40 ? 1 : -1];
typedef char DebugToolsDisplayOwnerMustFitProcSlot[
    sizeof(struct DebugToolsDisplayOwnerProc) <= sizeof(struct Proc) ? 1 : -1];
typedef char DebugToolsDiagnosticsScratchMustFitProcSlot[
    sizeof(struct DebugToolsDiagnosticsScratchProc) <= sizeof(struct Proc) ? 1 : -1];
typedef char DebugToolsDiagnosticsPersistentBudgetMustFit[
    sizeof(struct DebugToolsDiagnosticsState) <= 0x08 ? 1 : -1];
#if defined(FE8_DEBUGTOOLS_DIAGNOSTICS_RUNTIME_TEST) \
    || defined(FE8_DEBUGTOOLS_DIAGNOSTICS_PROBE_TEST)
typedef char DebugToolsDiagnosticsProbeSizeMustBe0x64[
    sizeof(struct DebugToolsDiagnosticsProbe) == 0x64 ? 1 : -1];
#endif
typedef char DebugToolsDiagnosticsOverlayBudgetMustFit[
    sizeof(sDebugToolsDisplayBackup) == DEBUGTOOLS_DISPLAY_OVERLAY_BYTES ? 1 : -1];

static struct ProcCmd CONST_DATA sDebugToolsDisplayOwnerScript[] =
{
    PROC_BLOCK,
};

static struct ProcCmd CONST_DATA sDebugToolsDiagnosticsScratchScript[] =
{
    PROC_BLOCK,
};

static struct DebugToolsDisplayOwnerProc*
DebugToolsDiagnostics_FindOwner(void)
{
    return (struct DebugToolsDisplayOwnerProc*)Proc_Find(
        sDebugToolsDisplayOwnerScript);
}

static struct DebugToolsDiagnosticsScratchProc*
DebugToolsDiagnostics_FindScratch(void)
{
    return (struct DebugToolsDiagnosticsScratchProc*)Proc_Find(
        sDebugToolsDiagnosticsScratchScript);
}

u8 DebugTools_CancelMenu(struct MenuProc* menu, struct MenuItemProc* item)
{
    u16 mask = FE8_EXPANSION_DEBUGTOOLS_PREP_HOTKEY_MASK;

    (void)menu;
    (void)item;

    if (DebugToolsDiagnostics_GetSessionContext()
            == DEBUGTOOLS_DIAG_CONTEXT_PREP
        && (gKeyStatusPtr->heldKeys & mask) == mask
        && (gKeyStatusPtr->newKeys & mask) != 0)
    {
        DebugTools_OpenHub();
        gKeyStatusPtr->newKeys = 0;
        return MENU_ACT_SKIPCURSOR;
    }

    return MENU_ACT_SKIPCURSOR | MENU_ACT_END | MENU_ACT_SND6B;
}

static u32 DebugToolsDiagnostics_Hash(const void* data, u32 size)
{
    const u8* bytes = data;
    u32 hash = 2166136261u;
    u32 i;

    for (i = 0; i < size; ++i)
    {
        hash ^= bytes[i];
        hash *= 16777619u;
    }

    return hash;
}

static u16* DebugToolsDiagnostics_BackupAt(int index)
{
    return &sDebugToolsDisplayBackup[index];
}

static void DebugToolsDiagnostics_CaptureDisplay(void)
{
    int bg;
    int x;
    int y;
    int index = 0;

    for (bg = 0; bg < 2; ++bg)
    {
        u16* map = BG_GetMapBuffer(bg);

        for (y = 0; y < DEBUGTOOLS_DISPLAY_HEIGHT; ++y)
        {
            for (x = 0; x < DEBUGTOOLS_DISPLAY_WIDTH; ++x)
            {
                *DebugToolsDiagnostics_BackupAt(index++) =
                    map[TILEMAP_INDEX(
                        DEBUGTOOLS_DISPLAY_X + x,
                        DEBUGTOOLS_DISPLAY_Y + y)];
            }
        }
    }
}

static u32 DebugToolsDiagnostics_RestoreDisplay(
    const struct DebugToolsDisplayOwnerProc* owner)
{
    u32 mismatch = 0;
    int bg;
    int x;
    int y;
    int index = 0;

    for (bg = 0; bg < 2; ++bg)
    {
        u16* map = BG_GetMapBuffer(bg);

        for (y = 0; y < DEBUGTOOLS_DISPLAY_HEIGHT; ++y)
        {
            for (x = 0; x < DEBUGTOOLS_DISPLAY_WIDTH; ++x)
            {
                map[TILEMAP_INDEX(
                    DEBUGTOOLS_DISPLAY_X + x,
                    DEBUGTOOLS_DISPLAY_Y + y)] =
                    *DebugToolsDiagnostics_BackupAt(index++);
            }
        }
    }

    BG_SetPosition(0, owner->bg0X, owner->bg0Y);
    BG_SetPosition(1, owner->bg1X, owner->bg1Y);

    if (owner->restoreFont != NULL)
        owner->restoreFont->chr_counter = owner->fontCounter;
    gActiveFont = owner->restoreFont;

    PAL_BG_COLOR(BGPAL_TEXT_DEFAULT, 14) =
        owner->greenTextColor;
    EnablePaletteSync();
    BG_EnableSyncByMask(BG0_SYNC_BIT | BG1_SYNC_BIT);

    index = 0;
    for (bg = 0; bg < 2; ++bg)
    {
        u16* map = BG_GetMapBuffer(bg);

        for (y = 0; y < DEBUGTOOLS_DISPLAY_HEIGHT; ++y)
        {
            for (x = 0; x < DEBUGTOOLS_DISPLAY_WIDTH; ++x)
            {
                if (map[TILEMAP_INDEX(
                        DEBUGTOOLS_DISPLAY_X + x,
                        DEBUGTOOLS_DISPLAY_Y + y)]
                    != *DebugToolsDiagnostics_BackupAt(index++))
                {
                    mismatch |= bg == 0
                        ? DEBUGTOOLS_DIAG_RESTORE_BG0
                        : DEBUGTOOLS_DIAG_RESTORE_BG1;
                }
            }
        }
    }

    if (gActiveFont != owner->restoreFont
        || (gActiveFont != NULL
            && gActiveFont->chr_counter != owner->fontCounter))
        mismatch |= DEBUGTOOLS_DIAG_RESTORE_FONT;

    if (DebugToolsDiagnostics_Hash(
            &gLCDControlBuffer, sizeof(gLCDControlBuffer))
        != owner->lcdHash)
        mismatch |= DEBUGTOOLS_DIAG_RESTORE_LCD;

    if (PAL_BG_COLOR(BGPAL_TEXT_DEFAULT, 14)
        != owner->greenTextColor)
        mismatch |= DEBUGTOOLS_DIAG_RESTORE_PALETTE;

    if (DebugToolsDiagnostics_Hash(
            BG_GetMapBuffer(2), 32 * 32 * sizeof(u16))
        != owner->bg2Hash)
        mismatch |= DEBUGTOOLS_DIAG_RESTORE_BG2;

    return mismatch;
}

static int DebugToolsDiagnostics_HasBattleRenderer(void)
{
#if defined(FE8_DEBUGTOOLS_DIAGNOSTICS_RUNTIME_TEST)
    if (sDebugToolsRuntimeForceBattleOwner)
        return 1;
#endif

    return Proc_Find(gProc_ekrBattle) != NULL
        || Proc_Find(ProcScr_MapAnimBattle) != NULL
        || Proc_Find(ProcScr_MapAnimEventBattle) != NULL;
}

static int DebugToolsDiagnostics_HasBattleOwner(void)
{
    return DebugToolsDiagnostics_HasBattleRenderer()
        || Proc_Find(ProcScr_BattleEventEngine) != NULL;
}

static int DebugToolsDiagnostics_HasActiveEvent(
    enum DebugToolsDiagnosticsContext context)
{
    struct Proc* eventProc = Proc_Find(ProcScr_StdEventEngine);

    if (eventProc == NULL)
        return 0;

    if (context == DEBUGTOOLS_DIAG_CONTEXT_PREP)
    {
        struct Proc* prepProc = Proc_Find(gProcScr_SALLYCURSOR);

        if (prepProc != NULL
            && prepProc->proc_parent == eventProc
            && eventProc->proc_lockCnt != 0)
            return 0;
    }

    return 1;
}

static int DebugToolsDiagnostics_ContextAvailable(
    enum DebugToolsDiagnosticsContext context)
{
    if (context == DEBUGTOOLS_DIAG_CONTEXT_TITLE)
        return !DebugToolsDiagnostics_HasBattleOwner();

    if (context != DEBUGTOOLS_DIAG_CONTEXT_MAP
        && context != DEBUGTOOLS_DIAG_CONTEXT_PREP)
        return 0;

    /* Battle-related procs can remain discoverable after handing off to the
     * real prep MapIdle. The explicit live prep owner/flag below is
     * authoritative; every other context rejects any battle owner. */
    if (context != DEBUGTOOLS_DIAG_CONTEXT_PREP
        && DebugToolsDiagnostics_HasBattleOwner())
        return 0;

    if (DoesBMXFADEExist())
        return 0;

    if (DebugToolsDiagnostics_HasActiveEvent(context))
        return 0;

    if (Proc_Find(gProc_BMapMain) == NULL)
        return 0;

    if (context == DEBUGTOOLS_DIAG_CONTEXT_MAP)
        return Proc_Find(gProcScr_PlayerPhase) != NULL;

    if (!(gPlaySt.chapterStateBits & PLAY_FLAG_PREPSCREEN)
        || Proc_Find(gProcScr_SALLYCURSOR) == NULL)
        return 0;

    return 1;
}

static ProcPtr DebugToolsDiagnostics_GetContextOwner(
    enum DebugToolsDiagnosticsContext context)
{
    switch (context)
    {
    case DEBUGTOOLS_DIAG_CONTEXT_TITLE:
        return Proc_Find(gProcScr_TitleScreen);

    case DEBUGTOOLS_DIAG_CONTEXT_MAP:
        return Proc_Find(gProcScr_PlayerPhase);

    case DEBUGTOOLS_DIAG_CONTEXT_PREP:
        return Proc_Find(gProcScr_SALLYCURSOR);

    default:
        return NULL;
    }
}

static void DebugToolsDiagnostics_UpdateProbe(
    enum DebugToolsResult result,
    const struct DebugToolsDiagnosticsSnapshot* snapshot)
{
#if defined(FE8_DEBUGTOOLS_DIAGNOSTICS_RUNTIME_TEST) \
    || defined(FE8_DEBUGTOOLS_DIAGNOSTICS_PROBE_TEST)
    gDebugToolsDiagnosticsProbe.lastResult = result;
    gDebugToolsDiagnosticsProbe.lastContext = snapshot->context;
    gDebugToolsDiagnosticsProbe.lastValidMask = snapshot->validMask;
    gDebugToolsDiagnosticsProbe.lastSequence = snapshot->sequence;
    gDebugToolsDiagnosticsProbe.lastCursorUnitId =
        snapshot->cursorUnitId;

    if (result == DEBUGTOOLS_OK)
    {
        gDebugToolsDiagnosticsProbe.captureCount++;
        if (snapshot->context == DEBUGTOOLS_DIAG_CONTEXT_TITLE)
            gDebugToolsDiagnosticsProbe.titleCaptureCount++;
        if ((snapshot->validMask & DEBUGTOOLS_DIAG_VALID_CURSOR)
            && !(snapshot->validMask & DEBUGTOOLS_DIAG_VALID_UNIT))
            gDebugToolsDiagnosticsProbe.emptyUnitCaptureCount++;
    }
    else if (snapshot->context == DEBUGTOOLS_DIAG_CONTEXT_BATTLE)
    {
        gDebugToolsDiagnosticsProbe.battleRejectCount++;
    }
#else
    (void)result;
    (void)snapshot;
#endif
}

static void DebugToolsDisplayOwner_OnEnd(ProcPtr proc)
{
    struct DebugToolsDisplayOwnerProc* owner =
        (struct DebugToolsDisplayOwnerProc*)proc;
    u32 mismatch;

    owner->restoring = 1;

    if (owner->endMode != DEBUGTOOLS_END_NORMAL)
        Proc_EndEach(gProcScr_DebugToolsMenuTransition);
    DebugToolsActions_ForceCleanup();
    mismatch = DebugToolsDiagnostics_RestoreDisplay(owner);

    if (owner->ownerLock)
    {
        UnlockGame();
        owner->ownerLock = 0;
        if (gBmSt.lock != owner->lockBaseline)
            mismatch |= DEBUGTOOLS_DIAG_RESTORE_LOCK;
    }
#if defined(FE8_DEBUGTOOLS_DIAGNOSTICS_RUNTIME_TEST) \
    || defined(FE8_DEBUGTOOLS_DIAGNOSTICS_PROBE_TEST)
    gDebugToolsDiagnosticsProbe.lastLockAfterRestore = gBmSt.lock;
    gDebugToolsDiagnosticsProbe.restorationMismatchMask = mismatch;
    gDebugToolsDiagnosticsProbe.restorationCount++;
    gDebugToolsDiagnosticsProbe.ownerActive = 0;
#endif

    sDiagnosticsState.context = DEBUGTOOLS_DIAG_CONTEXT_UNAVAILABLE;
    owner->restoring = 0;
    owner->endMode = DEBUGTOOLS_END_EXTERNAL;
    DebugToolsDiagnostics_OnSessionRestored();
}

void DebugToolsDiagnostics_SetSessionContext(
    enum DebugToolsDiagnosticsContext context)
{
    if (DebugToolsDiagnostics_FindOwner() == NULL)
        sDiagnosticsState.context = (u8)context;
}

void DebugToolsDiagnostics_ClearSessionContext(void)
{
    if (DebugToolsDiagnostics_FindOwner() == NULL)
        sDiagnosticsState.context = DEBUGTOOLS_DIAG_CONTEXT_UNAVAILABLE;
}

enum DebugToolsDiagnosticsContext DebugToolsDiagnostics_GetSessionContext(void)
{
    return (enum DebugToolsDiagnosticsContext)sDiagnosticsState.context;
}

int DebugToolsDiagnostics_IsContextAvailable(void)
{
    return DebugToolsDiagnostics_ContextAvailable(
        DebugToolsDiagnostics_GetSessionContext());
}

enum DebugToolsResult DebugToolsDiagnostics_BeginSession(void)
{
    enum DebugToolsDiagnosticsContext context;
    struct DebugToolsDisplayOwnerProc* displayOwner;
    struct DebugToolsDiagnosticsScratchProc* scratch;
    ProcPtr parent;
    ProcPtr owner;

    if (DebugToolsDiagnostics_FindOwner() != NULL)
        return DEBUGTOOLS_ERR_ALREADY_ACTIVE;

    if (!DebugToolsDiagnostics_IsContextAvailable())
        return DEBUGTOOLS_ERR_CONTEXT_UNAVAILABLE;

    context = DebugToolsDiagnostics_GetSessionContext();
    parent = DebugToolsDiagnostics_GetContextOwner(context);
    if (parent == NULL)
        return DEBUGTOOLS_ERR_CONTEXT_UNAVAILABLE;

    if (context == DEBUGTOOLS_DIAG_CONTEXT_TITLE)
        owner = Proc_Start(sDebugToolsDisplayOwnerScript, parent);
    else
        owner = Proc_StartBlocking(sDebugToolsDisplayOwnerScript, parent);
    if (owner == NULL)
        return DEBUGTOOLS_ERR_CONTEXT_UNAVAILABLE;

    displayOwner = (struct DebugToolsDisplayOwnerProc*)owner;
    scratch = (struct DebugToolsDiagnosticsScratchProc*)Proc_Start(
        sDebugToolsDiagnosticsScratchScript,
        owner);
    if (scratch == NULL)
    {
        Proc_End(owner);
        return DEBUGTOOLS_ERR_CONTEXT_UNAVAILABLE;
    }

    displayOwner->ownerLock = 0;
    displayOwner->restoring = 0;
    displayOwner->endMode = DEBUGTOOLS_END_EXTERNAL;
    displayOwner->restoreFont = gActiveFont;
    displayOwner->fontCounter =
        gActiveFont == NULL ? 0 : gActiveFont->chr_counter;
    displayOwner->bg0X =
        gLCDControlBuffer.bgoffset[0].x;
    displayOwner->bg0Y =
        gLCDControlBuffer.bgoffset[0].y;
    displayOwner->bg1X =
        gLCDControlBuffer.bgoffset[1].x;
    displayOwner->bg1Y =
        gLCDControlBuffer.bgoffset[1].y;
    displayOwner->lockBaseline = gBmSt.lock;
#if defined(FE8_DEBUGTOOLS_DIAGNOSTICS_RUNTIME_TEST) \
    || defined(FE8_DEBUGTOOLS_DIAGNOSTICS_PROBE_TEST)
    gDebugToolsDiagnosticsProbe.lastLockBaseline = gBmSt.lock;
#endif
    displayOwner->greenTextColor =
        PAL_BG_COLOR(BGPAL_TEXT_DEFAULT, 14);
    displayOwner->lcdHash =
        DebugToolsDiagnostics_Hash(
            &gLCDControlBuffer, sizeof(gLCDControlBuffer));
    displayOwner->bg2Hash =
        DebugToolsDiagnostics_Hash(
            BG_GetMapBuffer(2), 32 * 32 * sizeof(u16));
    memset(&scratch->scratch, 0, sizeof(scratch->scratch));
    DebugToolsDiagnostics_CaptureDisplay();
    Proc_SetEndCb(owner, DebugToolsDisplayOwner_OnEnd);
    if (context == DEBUGTOOLS_DIAG_CONTEXT_TITLE)
    {
        LockGame();
        displayOwner->ownerLock = 1;
    }
#if defined(FE8_DEBUGTOOLS_DIAGNOSTICS_RUNTIME_TEST) \
    || defined(FE8_DEBUGTOOLS_DIAGNOSTICS_PROBE_TEST)
    gDebugToolsDiagnosticsProbe.ownerActive = 1;
    gDebugToolsDiagnosticsProbe.ownerStartCount++;
#endif

    return DEBUGTOOLS_OK;
}

void DebugToolsDiagnostics_EndSession(int forced)
{
    struct DebugToolsDisplayOwnerProc* owner =
        DebugToolsDiagnostics_FindOwner();

    if (owner == NULL)
        return;

    if (forced)
    {
#if defined(FE8_DEBUGTOOLS_DIAGNOSTICS_RUNTIME_TEST) \
    || defined(FE8_DEBUGTOOLS_DIAGNOSTICS_PROBE_TEST)
        gDebugToolsDiagnosticsProbe.forcedTeardownCount++;
#endif
        owner->endMode = DEBUGTOOLS_END_FORCED;
    }
    else
    {
        owner->endMode = DEBUGTOOLS_END_NORMAL;
    }

    Proc_End(owner);
}

void DebugToolsDiagnostics_ForceCloseSession(void)
{
    DebugToolsDiagnostics_EndSession(1);
}

int DebugToolsDiagnostics_IsRestoring(void)
{
    struct DebugToolsDisplayOwnerProc* owner =
        DebugToolsDiagnostics_FindOwner();

    return owner != NULL && owner->restoring;
}

void DebugToolsDiagnostics_SetActiveMenu(struct MenuProc* menu)
{
    (void)menu;
}

void DebugToolsDiagnostics_ClearActiveMenu(struct MenuProc* menu)
{
    (void)menu;
}

struct MenuProc* DebugToolsDiagnostics_GetActiveMenu(void)
{
    struct DebugToolsDisplayOwnerProc* owner =
        DebugToolsDiagnostics_FindOwner();

    return owner == NULL ? NULL : (struct MenuProc*)owner->proc_child;
}

struct MenuProc* DebugToolsDiagnostics_StartOwnedMenu(
    const struct MenuDef* menuDef)
{
    ProcPtr owner = (ProcPtr)DebugToolsDiagnostics_FindOwner();

    if (owner == NULL)
        return NULL;

    return StartMenu(menuDef, owner);
}

const struct DebugToolsDiagnosticsSnapshot* DebugToolsDiagnostics_GetSnapshot(void)
{
    struct DebugToolsDiagnosticsScratchProc* scratch =
        DebugToolsDiagnostics_FindScratch();

    return scratch == NULL ? NULL : &scratch->scratch.snapshot;
}

char* DebugToolsDiagnostics_GetStatusBuffer(void)
{
    struct DebugToolsDiagnosticsScratchProc* scratch =
        DebugToolsDiagnostics_FindScratch();

    return scratch == NULL ? NULL : scratch->scratch.statusLine;
}

void DebugToolsDiagnostics_DrawStatusText(
    struct MenuProc* menu,
    const char* text)
{
    struct Text statusText;

    InitText(
        &statusText,
        DEBUGTOOLS_STATUS_TEXT_WIDTH_TILES);
    ClearText(&statusText);
    Text_SetColor(
        &statusText,
        TEXT_COLOR_SYSTEM_WHITE);
    Text_DrawString(&statusText, text);
    PutText(
        &statusText,
        TILEMAP_LOCATED(
            BG_GetMapBuffer(menu->frontBg),
            menu->rect.x + 1,
            menu->rect.y));
    BG_EnableSyncByMask(BG_SYNC_BIT(menu->frontBg));
}

enum DebugToolsResult DebugTools_CaptureDiagnostics(
    struct DebugToolsDiagnosticsSnapshot* out)
{
    enum DebugToolsDiagnosticsContext context;
    enum DebugToolsResult result;
    struct Unit* unit;
    int x;
    int y;

    if (out == NULL)
        return DEBUGTOOLS_ERR_INVALID_ARGUMENT;

    memset(out, 0, sizeof(*out));
    context = DebugToolsDiagnostics_GetSessionContext();
    out->context = (u8)context;

    if (!DebugTools_IsHubActive()
        || !DebugToolsDiagnostics_ContextAvailable(context))
    {
        if (DebugToolsDiagnostics_HasBattleOwner())
            out->context = DEBUGTOOLS_DIAG_CONTEXT_BATTLE;

        result = DEBUGTOOLS_ERR_CONTEXT_UNAVAILABLE;
        DebugToolsDiagnostics_UpdateProbe(result, out);
        return result;
    }

    out->sequence = ++sDiagnosticsState.sequence;
    out->validMask = DEBUGTOOLS_DIAG_VALID_COMMON;
    out->gameClockFrames = GetGameClock();
    out->procCount = (u32)CountProcs(NULL);
    out->logTotalWrites = gDebugToolsProbe.logEventCount;
    out->lastLogCode = gDebugToolsProbe.lastLogCode;
    out->assertFailureCount = DebugTools_GetAssertFailureCount();
    out->lastAssertCode = DebugTools_GetLastAssertCode();
    StoreRNState(out->rngState);
    out->eventEngineActive = (u8)DebugToolsDiagnostics_HasActiveEvent(context);
    out->registeredActionCount = (u8)DebugTools_GetRegisteredCount();
    out->logRetainedCount = (u8)DebugTools_GetLogCount();

    if (context == DEBUGTOOLS_DIAG_CONTEXT_MAP
        || context == DEBUGTOOLS_DIAG_CONTEXT_PREP)
    {
        out->validMask |= DEBUGTOOLS_DIAG_VALID_MAP;
        out->chapterIndex = gPlaySt.chapterIndex;
        out->faction = gPlaySt.faction;
        out->turn = gPlaySt.chapterTurnNumber;
        out->weatherId = gPlaySt.chapterWeatherId;
        out->fogRange = gPlaySt.chapterVisionRange;
        x = gBmSt.playerCursor.x;
        y = gBmSt.playerCursor.y;

        if (gBmMapUnit != NULL
            && x >= 0 && y >= 0
            && x < gBmMapSize.x && y < gBmMapSize.y
            && gBmMapUnit[y] != NULL)
        {
            out->validMask |= DEBUGTOOLS_DIAG_VALID_CURSOR;
            out->cursorX = (s16)x;
            out->cursorY = (s16)y;
            out->cursorUnitId = gBmMapUnit[y][x];
            unit = GetUnit(out->cursorUnitId);

            if (out->cursorUnitId != 0 && UNIT_IS_VALID(unit))
            {
                out->validMask |= DEBUGTOOLS_DIAG_VALID_UNIT;
                out->characterId = UNIT_CHAR_ID(unit);
                out->classId = UNIT_CLASS_ID(unit);
                out->currentHp = (u8)GetUnitCurrentHp(unit);
                out->maxHp = (u8)GetUnitMaxHp(unit);
            }
        }
    }

    result = DEBUGTOOLS_OK;
    DebugToolsDiagnostics_UpdateProbe(result, out);
    return result;
}

enum DebugToolsResult DebugToolsDiagnostics_RefreshSnapshot(void)
{
    struct DebugToolsDiagnosticsScratchProc* scratch =
        DebugToolsDiagnostics_FindScratch();

    if (scratch == NULL)
        return DEBUGTOOLS_ERR_CONTEXT_UNAVAILABLE;

    return DebugTools_CaptureDiagnostics(&scratch->scratch.snapshot);
}

void DebugToolsDiagnostics_RecordViewOpen(int engineView)
{
#if defined(FE8_DEBUGTOOLS_DIAGNOSTICS_RUNTIME_TEST) \
    || defined(FE8_DEBUGTOOLS_DIAGNOSTICS_PROBE_TEST)
    if (engineView)
        gDebugToolsDiagnosticsProbe.enginePageOpenCount++;
    else
        gDebugToolsDiagnosticsProbe.statePageOpenCount++;
#else
    (void)engineView;
#endif
}

#if defined(FE8_DEBUGTOOLS_DIAGNOSTICS_RUNTIME_TEST)
static int DebugToolsDiagnostics_RuntimeTestFindTile(int wantUnit, int* xOut, int* yOut)
{
    int x;
    int y;

    if (gBmMapUnit == NULL)
        return 0;

    for (y = 0; y < gBmMapSize.y; ++y)
    {
        if (gBmMapUnit[y] == NULL)
            continue;

        for (x = 0; x < gBmMapSize.x; ++x)
        {
            if ((gBmMapUnit[y][x] != 0) == wantUnit)
            {
                *xOut = x;
                *yOut = y;
                return 1;
            }
        }
    }

    return 0;
}

static int DebugToolsDiagnostics_RuntimeTestCaptureMap(
    enum DebugToolsDiagnosticsContext context,
    int wantUnit)
{
    struct DebugToolsDiagnosticsSnapshot snapshot;
    int oldX = gBmSt.playerCursor.x;
    int oldY = gBmSt.playerCursor.y;
    int x;
    int y;
    int ok = 0;

    if (!DebugToolsDiagnostics_RuntimeTestFindTile(wantUnit, &x, &y))
        return 0;

    gBmSt.playerCursor.x = x;
    gBmSt.playerCursor.y = y;
    DebugToolsDiagnostics_SetSessionContext(context);
    if (DebugTools_OpenHub() == DEBUGTOOLS_OK
        && DebugToolsDiagnostics_BeginSession() == DEBUGTOOLS_OK)
    {
        if (DebugTools_CaptureDiagnostics(&snapshot) == DEBUGTOOLS_OK)
        {
            ok = wantUnit
                ? (snapshot.validMask & DEBUGTOOLS_DIAG_VALID_UNIT) != 0
                : ((snapshot.validMask & DEBUGTOOLS_DIAG_VALID_CURSOR) != 0
                    && (snapshot.validMask & DEBUGTOOLS_DIAG_VALID_UNIT) == 0);
        }
        DebugToolsDiagnostics_ForceCloseSession();
    }

    gBmSt.playerCursor.x = oldX;
    gBmSt.playerCursor.y = oldY;
    return ok;
}

static int DebugToolsDiagnostics_RuntimeTestCaptureCurrentMap(
    enum DebugToolsDiagnosticsContext context)
{
    struct DebugToolsDiagnosticsSnapshot snapshot;
    enum DebugToolsResult result;
    int ok = 0;

    DebugToolsDiagnostics_SetSessionContext(context);
    result = DebugTools_OpenHub();
    if (result == DEBUGTOOLS_OK)
    {
        result = DebugToolsDiagnostics_BeginSession();
        if (result == DEBUGTOOLS_OK)
        {
            result = DebugTools_CaptureDiagnostics(&snapshot);
            if (result == DEBUGTOOLS_OK)
                ok = (snapshot.validMask & DEBUGTOOLS_DIAG_VALID_MAP) != 0;
        }
        DebugToolsDiagnostics_ForceCloseSession();
    }

    return ok;
}

void DebugToolsDiagnostics_RuntimeTestBoot(void)
{
    struct DebugToolsDiagnosticsSnapshot snapshot;

    if (gDebugToolsDiagnosticsProbe.runtimeTestComplete)
        return;

    DebugToolsDiagnostics_SetSessionContext(DEBUGTOOLS_DIAG_CONTEXT_TITLE);
    if (DebugTools_OpenHub() != DEBUGTOOLS_OK
        || DebugToolsDiagnostics_BeginSession() != DEBUGTOOLS_OK)
        return;

    DebugTools_CaptureDiagnostics(&snapshot);
    DebugToolsDiagnostics_ForceCloseSession();

    sDebugToolsRuntimeForceBattleOwner = 1;
    DebugTools_CaptureDiagnostics(&snapshot);
    sDebugToolsRuntimeForceBattleOwner = 0;
    gDebugToolsDiagnosticsProbe.runtimeTestComplete = 1;
}

void DebugToolsDiagnostics_RuntimeTestMap(void)
{
    if (gDebugToolsDiagnosticsProbe.mapRuntimeComplete)
        return;

    if (DebugToolsDiagnostics_RuntimeTestCaptureMap(
            DEBUGTOOLS_DIAG_CONTEXT_MAP, 1))
        gDebugToolsDiagnosticsProbe.mapUnitCaptureCount++;

    DebugToolsDiagnostics_RuntimeTestCaptureMap(
        DEBUGTOOLS_DIAG_CONTEXT_MAP, 0);
    gDebugToolsDiagnosticsProbe.mapRuntimeComplete = 1;
}

void DebugToolsDiagnostics_RuntimeTestPrep(void)
{
    if (gDebugToolsDiagnosticsProbe.prepRuntimeComplete)
        return;

    if (DebugToolsDiagnostics_RuntimeTestCaptureCurrentMap(
            DEBUGTOOLS_DIAG_CONTEXT_PREP))
    {
        gDebugToolsDiagnosticsProbe.prepCaptureCount++;
        gDebugToolsDiagnosticsProbe.prepRuntimeComplete = 1;
    }
}
#endif

#else

enum DebugToolsResult DebugTools_CaptureDiagnostics(
    struct DebugToolsDiagnosticsSnapshot* out)
{
    u8* bytes;
    u32 i;

    if (out == NULL)
        return DEBUGTOOLS_ERR_INVALID_ARGUMENT;

    bytes = (u8*)out;
    for (i = 0; i < sizeof(*out); ++i)
        bytes[i] = 0;

    return DEBUGTOOLS_ERR_DISABLED;
}

#endif

#endif
