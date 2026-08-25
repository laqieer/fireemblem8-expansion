#include "global.h"

#ifndef FE8_ARCHIVAL_BUILD
#include <string.h>
#include <stdio.h>

#include "proc.h"
#include "uimenu.h"
#include "fontgrp.h"
#include "hardware.h"
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
#include "bm.h"
#include "cp_common.h"
#include "event.h"
#include "playerphase.h"
#include "expansion_autoplay.h"
#if defined(FE8_PORTRAIT_PACKAGE_RUNTIME_TEST)
#include "face.h"
#endif
#include "expansion_debugtools.h"
#include "debugtools_internal.h"

#include "constants/characters.h"
#include "constants/items.h"

#ifdef MODERN
#include "expansion_locale.h"
#include "expansion_msg_ids.h"
#define DEBUGTOOLS_LOCALIZED_TEXT(message, fallback) \
    ExpansionLocale_ResolveCurrent(message)
#else
#define DEBUGTOOLS_LOCALIZED_TEXT(message, fallback) (fallback)
#endif

/*
 * Issue #11 closure -- the five bounded, validated debug tools:
 *   5. Unit inspection/edit
 *   6. Convoy inspection/edit
 *   7. Flag/chapter/event state action
 *   8. RNG inspection/control
 *   9. Save compatibility/state inspection
 *
 * Each is a single registry action using the shared deferred submenu owner.
 * Every mutation requires a bounded preview and a separate confirmation
 * input; tool 9 remains read-only. The unit editor uses the same seam for
 * cursor selection and typed HP/stat/AI/status edits.
 *
 * No tool performs a raw/arbitrary address write. Fixed constants remain for
 * convoy/flag/RNG operations; the unit editor resolves a canonical live-map
 * unit through gBmMapUnit/GetUnit and revalidates its complete typed identity
 * immediately before each mutation. This file never edits
 * src/bmdebug.c, src/menu_def.c, or src/uidebug.c, and never touches
 * SRAM/any save-block struct directly.
 */

#if FE8_EXPANSION_DEBUGTOOLS_ENABLED

#if defined(FE8_PORTRAIT_PACKAGE_RUNTIME_TEST)
EWRAM_DATA struct PortraitPackageRuntimeProbe gPortraitPackageRuntimeProbe = {0};
#endif

enum
{
    /* Follow Weather/Fog (0xE0-0xE1) and the unit editor (0xE2-0xF5)
     * without colliding with another debug menu override. */
    DEBUGTOOLS_UNIT_OVERRIDE_ID = 0xE2,
    DEBUGTOOLS_CONVOY_OVERRIDE_ID = 0xE3,
    DEBUGTOOLS_FLAG_OVERRIDE_ID = 0xE4,
    DEBUGTOOLS_RNG_OVERRIDE_ID = 0xE5,
    DEBUGTOOLS_UNIT_CURRENT_HP_OVERRIDE_ID = 0xE6,
    DEBUGTOOLS_UNIT_MAX_HP_OVERRIDE_ID = 0xE7,
    DEBUGTOOLS_UNIT_POWER_OVERRIDE_ID = 0xE8,
    DEBUGTOOLS_UNIT_SKILL_OVERRIDE_ID = 0xE9,
    DEBUGTOOLS_UNIT_SPEED_OVERRIDE_ID = 0xEA,
    DEBUGTOOLS_UNIT_DEFENSE_OVERRIDE_ID = 0xEB,
    DEBUGTOOLS_UNIT_RESISTANCE_OVERRIDE_ID = 0xEC,
    DEBUGTOOLS_UNIT_LUCK_OVERRIDE_ID = 0xED,
    DEBUGTOOLS_UNIT_AI_A_OVERRIDE_ID = 0xEE,
    DEBUGTOOLS_UNIT_AI_B_OVERRIDE_ID = 0xEF,
    DEBUGTOOLS_UNIT_EDIT_HP_OVERRIDE_ID = 0xF0,
    DEBUGTOOLS_UNIT_EDIT_STATS_OVERRIDE_ID = 0xF1,
    DEBUGTOOLS_UNIT_EDIT_AI_OVERRIDE_ID = 0xF2,
    DEBUGTOOLS_UNIT_CLEAR_STATUS_OVERRIDE_ID = 0xF3,
    DEBUGTOOLS_UNIT_IDENTITY_OVERRIDE_ID = 0xF4,
    DEBUGTOOLS_UNIT_STATE_OVERRIDE_ID = 0xF5
};

#define DEBUGTOOLS_CONVOY_TEST_ITEM ITEM_VULNERARY

/* Highest valid chapter-scoped event-flag bit: GetChapterFlagBitsSize()
 * (src/eventinfo.c) is 5 bytes == 40 bits (indices 0-39), and
 * include/constants/event-flags.h documents indices 7-40 as free/scratch
 * (0-6 are real, meaningful gameplay flags). 39 is deliberately the
 * highest in-range bit, re-validated at every mutation site via
 * DEBUGTOOLS_ASSERT rather than trusted as a compile-time-only fact. */
#define DEBUGTOOLS_DEBUG_EVENT_FLAG_ID 39

/* Arbitrary fixed debug reseed value, distinct from
 * DEBUGTOOLS_FASTBOOT_RNG_SEED (src/gamecontrol.c) so the two are never
 * confused in logs/tests. */
#define DEBUGTOOLS_TOOLS_RNG_SEED 0x1EE7C0DEu
#if defined(FE8_PORTRAIT_PACKAGE_RUNTIME_TEST)
#define DEBUGTOOLS_PORTRAIT_PROBE_FACE_ID 2
#define DEBUGTOOLS_PORTRAIT_PROBE_CHR 0x280
#define DEBUGTOOLS_PORTRAIT_PROBE_PAL 2

static u32 DebugToolsTools_ReadU32(const u16* values)
{
    return (u32)values[0] | ((u32)values[1] << 16);
}
#endif

#ifdef MODERN
static int DebugToolsTools_LocalizedMenuItemDraw(
    struct MenuProc* menu,
    struct MenuItemProc* item)
{
    if (item->availability == MENU_DISABLED)
        Text_SetColor(&item->text, TEXT_COLOR_SYSTEM_GRAY);

    Text_DrawString(
        &item->text,
        ExpansionLocale_ResolveCurrent((ExpansionMsgId)item->def->helpMsgId));
    PutText(
        &item->text,
        TILEMAP_LOCATED(
            BG_GetMapBuffer(menu->frontBg),
            item->xTile,
            item->yTile));

    return 0;
}

static void DebugToolsTools_LocalizeMenuItem(
    struct MenuItemDef* item,
    ExpansionMsgId message)
{
    item->helpMsgId = (u16)message;
    item->onDraw = DebugToolsTools_LocalizedMenuItemDraw;
}
#define DEBUGTOOLS_LOCALIZE_ITEM(item, message) \
    DebugToolsTools_LocalizeMenuItem((item), (message))

static int DebugToolsTools_UsesCjkText(void)
{
    ExpansionLocaleId locale = ExpansionLocale_GetCurrent();

    return locale == EXPANSION_LOCALE_JA || locale == EXPANSION_LOCALE_ZH_HANS;
}

static void DebugToolsTools_MenuOnInit(struct MenuProc* menu)
{
    char* status = DebugToolsDiagnostics_GetStatusBuffer();

    if (status != NULL)
        DebugToolsDiagnostics_DrawStatusText(menu, status);
}

static void DebugToolsTools_DrawCjkStatusLine(const char* text)
{
    BG_Fill(BG_GetMapBuffer(2), 0);
    PutDrawText(
        NULL,
        BG_GetMapBuffer(2) + TILEMAP_INDEX(1, 1),
        TEXT_COLOR_SYSTEM_WHITE,
        0,
        DEBUGTOOLS_STATUS_TEXT_WIDTH_TILES,
        text);
    BG_EnableSyncByMask(BG2_SYNC_BIT);
    gLCDControlBuffer.dispcnt.bg2_on = 1;
}

static void DebugToolsTools_DrawCjkFlagStatus(
    const char* values,
    const char* redMode,
    const char* greenMode)
{
    BG_Fill(BG_GetMapBuffer(2), 0);
    PutDrawText(
        NULL,
        BG_GetMapBuffer(2) + TILEMAP_INDEX(1, 1),
        TEXT_COLOR_SYSTEM_WHITE,
        0,
        DEBUGTOOLS_FLAG_STATUS_CJK_WIDTH_TILES,
        values);
    PutDrawText(
        NULL,
        BG_GetMapBuffer(2) + TILEMAP_INDEX(1, 3),
        TEXT_COLOR_SYSTEM_WHITE,
        0,
        DEBUGTOOLS_FLAG_STATUS_CJK_WIDTH_TILES,
        redMode);
    PutDrawText(
        NULL,
        BG_GetMapBuffer(2) + TILEMAP_INDEX(1, 5),
        TEXT_COLOR_SYSTEM_WHITE,
        0,
        DEBUGTOOLS_FLAG_STATUS_CJK_WIDTH_TILES,
        greenMode);
    BG_EnableSyncByMask(BG2_SYNC_BIT);
    gLCDControlBuffer.dispcnt.bg2_on = 1;
}

static void DebugToolsTools_FormatFlagStatus(
    char* values,
    char* redMode,
    char* greenMode);
static void DebugToolsTools_DrawFlagStatus(
    const char* values,
    const char* redMode,
    const char* greenMode);

static void DebugToolsTools_UnitMenuOnInit(struct MenuProc* menu)
{
    char buf[64];

    DebugToolsTools_MenuOnInit(menu);
    if (!DebugToolsTools_UsesCjkText())
        return;

    if (gDebugToolsProbe.unitInspectTargetFound)
        sprintf(buf, "%s %d/%d",
            ExpansionLocale_ResolveCurrent(EXP_MSG_DEBUG_STATUS_UNIT_HP),
            (int)gDebugToolsProbe.unitInspectLastCurHp,
            (int)gDebugToolsProbe.unitInspectLastMaxHp);
    else
        sprintf(buf, "%s", ExpansionLocale_ResolveCurrent(
            EXP_MSG_DEBUG_STATUS_UNIT_UNAVAILABLE));

    DebugToolsTools_DrawCjkStatusLine(buf);
}

static void DebugToolsTools_ConvoyMenuOnInit(struct MenuProc* menu)
{
    char buf[64];

    DebugToolsTools_MenuOnInit(menu);
    if (!DebugToolsTools_UsesCjkText())
        return;
    sprintf(buf, "%s %d/%d",
        ExpansionLocale_ResolveCurrent(EXP_MSG_DEBUG_STATUS_CONVOY),
        (int)gDebugToolsProbe.convoyLastItemCount,
        (int)CONVOY_ITEM_COUNT);
    DebugToolsTools_DrawCjkStatusLine(buf);
}

static void DebugToolsTools_FlagMenuOnInit(struct MenuProc* menu)
{
    char values[48];
    char redMode[32];
    char greenMode[32];

    DebugToolsTools_MenuOnInit(menu);
    DebugToolsTools_FormatFlagStatus(values, redMode, greenMode);
    DebugToolsTools_DrawFlagStatus(values, redMode, greenMode);
}

static void DebugToolsTools_RngMenuOnInit(struct MenuProc* menu)
{
    char buf[64];

    DebugToolsTools_MenuOnInit(menu);
    if (!DebugToolsTools_UsesCjkText())
        return;
    sprintf(buf, "%s %04X",
        ExpansionLocale_ResolveCurrent(EXP_MSG_DEBUG_STATUS_RNG_SEED),
        (unsigned int)gDebugToolsProbe.rngInspectSeedSample0);
    DebugToolsTools_DrawCjkStatusLine(buf);
}

static void DebugToolsTools_SaveStateMenuOnInit(struct MenuProc* menu)
{
    char buf[64];

    DebugToolsTools_MenuOnInit(menu);
    if (!DebugToolsTools_UsesCjkText())
        return;
    sprintf(buf, "%s %d",
        ExpansionLocale_ResolveCurrent(EXP_MSG_DEBUG_STATUS_SAVE_STATE),
        (int)gDebugToolsProbe.saveCompatLastState);
    DebugToolsTools_DrawCjkStatusLine(buf);
}

#define DEBUGTOOLS_UNIT_MENU_ON_INIT DebugToolsTools_UnitMenuOnInit
#define DEBUGTOOLS_CONVOY_MENU_ON_INIT DebugToolsTools_ConvoyMenuOnInit
#define DEBUGTOOLS_FLAG_MENU_ON_INIT DebugToolsTools_FlagMenuOnInit
#define DEBUGTOOLS_RNG_MENU_ON_INIT DebugToolsTools_RngMenuOnInit
#define DEBUGTOOLS_SAVE_MENU_ON_INIT DebugToolsTools_SaveStateMenuOnInit
#else
#define DEBUGTOOLS_LOCALIZE_ITEM(item, message) ((void)0)
#define DEBUGTOOLS_UNIT_MENU_ON_INIT 0
#define DEBUGTOOLS_CONVOY_MENU_ON_INIT 0
#define DEBUGTOOLS_FLAG_MENU_ON_INIT 0
#define DEBUGTOOLS_RNG_MENU_ON_INIT 0
#define DEBUGTOOLS_SAVE_MENU_ON_INIT 0
static void DebugToolsTools_MenuOnInit(struct MenuProc* menu)
{
    char* status = DebugToolsDiagnostics_GetStatusBuffer();

    if (status != NULL)
        DebugToolsDiagnostics_DrawStatusText(menu, status);
}
#endif

static void DebugToolsTools_FormatFlagStatus(
    char* values,
    char* redMode,
    char* greenMode)
{
    DebugToolsPhaseControl_Sample();
    sprintf(values, "%s %d C:%d F:%d",
        DEBUGTOOLS_LOCALIZED_TEXT(EXP_MSG_DEBUG_STATUS_TURN, "TURN"),
        (int)gDebugToolsProbe.phaseControlTurnSample,
        (int)gDebugToolsProbe.chapterIndexSample,
        (int)gDebugToolsProbe.debugFlagLastValue);
#ifdef MODERN
    sprintf(redMode, "R:%s",
        ExpansionLocale_ResolveCurrent(
            gDebugToolsProbe.phaseControlRedModeSample
                    == DEBUGTOOLS_PHASE_CONTROL_BLOCKED
                ? EXP_MSG_DEBUG_MODE_BLOCKED
                : EXP_MSG_DEBUG_MODE_COMPUTER));
    sprintf(greenMode, "G:%s",
        ExpansionLocale_ResolveCurrent(
            gDebugToolsProbe.phaseControlGreenModeSample
                    == DEBUGTOOLS_PHASE_CONTROL_BLOCKED
                ? EXP_MSG_DEBUG_MODE_BLOCKED
                : EXP_MSG_DEBUG_MODE_COMPUTER));
#else
    sprintf(redMode, "R:%d", (int)gDebugToolsProbe.phaseControlRedModeSample);
    sprintf(greenMode, "G:%d", (int)gDebugToolsProbe.phaseControlGreenModeSample);
#endif
}

static void DebugToolsTools_DrawFlagStatus(
    const char* values,
    const char* redMode,
    const char* greenMode)
{
#ifdef MODERN
    if (DebugToolsTools_UsesCjkText())
    {
        DebugToolsTools_DrawCjkFlagStatus(values, redMode, greenMode);
        return;
    }
#endif

    SetupDebugFontForBG(2, 0);
    PrintDebugStringToBG(
        BG_GetMapBuffer(2) + TILEMAP_INDEX(1, 1),
        values);
    PrintDebugStringToBG(
        BG_GetMapBuffer(2) + TILEMAP_INDEX(1, 3),
        redMode);
    PrintDebugStringToBG(
        BG_GetMapBuffer(2) + TILEMAP_INDEX(1, 5),
        greenMode);
    gLCDControlBuffer.dispcnt.bg2_on = 1;
}

static void DebugToolsTools_ShowStatusLine(const char* text)
{
    char* status = DebugToolsDiagnostics_GetStatusBuffer();

    if (status != NULL)
    {
        strncpy(status, text, 63);
        status[63] = '\0';
        return;
    }

#ifdef MODERN
    if (DebugToolsTools_UsesCjkText())
    {
        BG_Fill(BG_GetMapBuffer(2), 0);
        PutDrawText(
            NULL,
            BG_GetMapBuffer(2) + TILEMAP_INDEX(1, 1),
            TEXT_COLOR_SYSTEM_WHITE,
            0,
            DEBUGTOOLS_STATUS_TEXT_WIDTH_TILES,
            text);
        BG_EnableSyncByMask(BG2_SYNC_BIT);
        gLCDControlBuffer.dispcnt.bg2_on = 1;
        return;
    }
#endif

    SetupDebugFontForBG(2, 0);
    PrintDebugStringToBG(BG_GetMapBuffer(2) + TILEMAP_INDEX(1, 1), text);
    gLCDControlBuffer.dispcnt.bg2_on = 1;
}

/* --- Transient turn/faction phase control (issue #124) ---------------- */

enum
{
    DEBUGTOOLS_TURN_MIN = 1,
    DEBUGTOOLS_TURN_MAX = 999,
    DEBUGTOOLS_TURN_OVERRIDE_ID = 0xF6,
    DEBUGTOOLS_TURN_DECREMENT_OVERRIDE_ID = 0xFC,
    DEBUGTOOLS_RED_COMPUTER_OVERRIDE_ID = 0xF7,
    DEBUGTOOLS_RED_BLOCKED_OVERRIDE_ID = 0xF8,
    DEBUGTOOLS_GREEN_COMPUTER_OVERRIDE_ID = 0xF9,
    DEBUGTOOLS_GREEN_BLOCKED_OVERRIDE_ID = 0xFA,
};

struct DebugToolsPhaseControlRequest
{
    int turn;
    int faction;
    enum DebugToolsPhaseControlMode mode;
    enum DebugToolsPhaseControlRequestKind kind;
};

EWRAM_DATA static struct DebugToolsPhaseControlRequest sPhaseControlRequest = {0};

struct DebugToolsPhaseControlSuspendTurn
{
    u16 originalTurn;
    u16 liveTurn;
    bool hasOriginalTurn;
    u8 serializationDepth;
};

EWRAM_DATA static struct DebugToolsPhaseControlSuspendTurn sPhaseControlSuspendTurn = {0};

static void DebugToolsPhaseControl_RecordResult(enum DebugToolsPhaseControlResult result)
{
    gDebugToolsProbe.phaseControlLastResult = result;
}

static void DebugToolsPhaseControl_RefreshProbe(void)
{
    gDebugToolsProbe.phaseControlTurnSample = gPlaySt.chapterTurnNumber;
    gDebugToolsProbe.phaseControlRedModeSample =
        (sPhaseControlRequest.kind == DEBUGTOOLS_PHASE_CONTROL_REQUEST_FACTION
            && sPhaseControlRequest.faction == FACTION_RED)
        ? sPhaseControlRequest.mode
        : DEBUGTOOLS_PHASE_CONTROL_COMPUTER;
    gDebugToolsProbe.phaseControlGreenModeSample =
        (sPhaseControlRequest.kind == DEBUGTOOLS_PHASE_CONTROL_REQUEST_FACTION
            && sPhaseControlRequest.faction == FACTION_GREEN)
        ? sPhaseControlRequest.mode
        : DEBUGTOOLS_PHASE_CONTROL_COMPUTER;
}

static enum DebugToolsPhaseControlResult DebugToolsPhaseControl_Reject(
    enum DebugToolsPhaseControlResult result)
{
    gDebugToolsProbe.phaseControlRejectedCount++;
    DebugToolsPhaseControl_RecordResult(result);
    DebugToolsPhaseControl_RefreshProbe();
    return result;
}

static bool DebugToolsPhaseControl_IsSafeRequestBoundary(void)
{
    const struct ExpansionAutoplayTelemetry* telemetry =
        ExpansionAutoplay_GetTelemetry();
    int gameLock = GetGameLock();

    if (gPlaySt.faction != FACTION_BLUE
        || ExpansionAutoplay_GetBlueControl() != EXPANSION_BLUE_CONTROL_PLAYER
        || telemetry->state != EXPANSION_AUTOPLAY_STATE_PLAYER_PHASE
        || telemetry->failure != EXPANSION_AUTOPLAY_FAILURE_NONE)
        return false;

    if (gameLock != 1
        && !(gameLock == 2 && DebugTools_IsHubActive()))
        return false;

    if (!Proc_Find(gProc_BMapMain) || !Proc_Find(gProcScr_PlayerPhase)
        || Proc_Find(gProcScr_Playerphase_0) || Proc_Find(gProcScr_CpPhase)
        || Proc_Find(gProcScr_BerserkCpPhase) || EventEngineExists()
        || BattleEventEngineExists() || IsBattleDeamonActive()
        || DoesBMXFADEExist() || Proc_Find(ProcScr_CamMove))
        return false;

    return true;
}

static enum DebugToolsPhaseControlResult DebugToolsPhaseControl_QueueRequest(
    enum DebugToolsPhaseControlRequestKind kind,
    int turn,
    int faction,
    enum DebugToolsPhaseControlMode mode)
{
    if (sPhaseControlRequest.kind != DEBUGTOOLS_PHASE_CONTROL_REQUEST_NONE)
        return DebugToolsPhaseControl_Reject(DEBUGTOOLS_PHASE_CONTROL_ERR_PENDING);

    if (!DebugToolsPhaseControl_IsSafeRequestBoundary())
        return DebugToolsPhaseControl_Reject(DEBUGTOOLS_PHASE_CONTROL_ERR_UNSAFE_BOUNDARY);

    sPhaseControlRequest.kind = kind;
    sPhaseControlRequest.turn = turn;
    sPhaseControlRequest.faction = faction;
    sPhaseControlRequest.mode = mode;

    gDebugToolsProbe.phaseControlRequestedCount++;
    gDebugToolsProbe.phaseControlLastRequestKind = kind;
    gDebugToolsProbe.phaseControlLastFaction = faction;
    gDebugToolsProbe.phaseControlLastMode = mode;
    DebugToolsPhaseControl_RecordResult(DEBUGTOOLS_PHASE_CONTROL_OK);
    DebugToolsPhaseControl_RefreshProbe();
    return DEBUGTOOLS_PHASE_CONTROL_OK;
}

enum DebugToolsPhaseControlResult DebugToolsPhaseControl_RequestTurn(int turn)
{
    if (turn < DEBUGTOOLS_TURN_MIN || turn > DEBUGTOOLS_TURN_MAX)
        return DebugToolsPhaseControl_Reject(DEBUGTOOLS_PHASE_CONTROL_ERR_INVALID_TURN);

    return DebugToolsPhaseControl_QueueRequest(
        DEBUGTOOLS_PHASE_CONTROL_REQUEST_TURN,
        turn,
        FACTION_BLUE,
        DEBUGTOOLS_PHASE_CONTROL_COMPUTER);
}

enum DebugToolsPhaseControlResult DebugToolsPhaseControl_RequestFactionMode(
    int faction,
    enum DebugToolsPhaseControlMode mode)
{
    if (faction != FACTION_RED && faction != FACTION_GREEN)
        return DebugToolsPhaseControl_Reject(DEBUGTOOLS_PHASE_CONTROL_ERR_INVALID_FACTION);

    if (mode != DEBUGTOOLS_PHASE_CONTROL_COMPUTER
        && mode != DEBUGTOOLS_PHASE_CONTROL_BLOCKED)
        return DebugToolsPhaseControl_Reject(DEBUGTOOLS_PHASE_CONTROL_ERR_UNSUPPORTED_MODE);

    return DebugToolsPhaseControl_QueueRequest(
        DEBUGTOOLS_PHASE_CONTROL_REQUEST_FACTION,
        0,
        faction,
        mode);
}

static void DebugToolsPhaseControl_CompleteRequest(void)
{
    gDebugToolsProbe.phaseControlAppliedCount++;
    gDebugToolsProbe.phaseControlRestoredCount++;
    DebugToolsPhaseControl_RecordResult(DEBUGTOOLS_PHASE_CONTROL_OK);
    sPhaseControlRequest.kind = DEBUGTOOLS_PHASE_CONTROL_REQUEST_NONE;
    DebugToolsPhaseControl_RefreshProbe();
}

void DebugToolsPhaseControl_ApplyTurnBeforePhaseEvents(void)
{
    if (sPhaseControlRequest.kind != DEBUGTOOLS_PHASE_CONTROL_REQUEST_TURN)
        return;

    if (!sPhaseControlSuspendTurn.hasOriginalTurn)
    {
        sPhaseControlSuspendTurn.originalTurn = gPlaySt.chapterTurnNumber;
        sPhaseControlSuspendTurn.hasOriginalTurn = TRUE;
    }
    gPlaySt.chapterTurnNumber = (u16)sPhaseControlRequest.turn;
    DebugToolsPhaseControl_CompleteRequest();
}

enum DebugToolsPhaseControlStartAction DebugToolsPhaseControl_ApplyAtPhaseStart(int faction)
{
    enum DebugToolsPhaseControlStartAction action = DEBUGTOOLS_PHASE_CONTROL_START_NORMAL;

    if (sPhaseControlRequest.kind != DEBUGTOOLS_PHASE_CONTROL_REQUEST_FACTION)
        return action;

    if (sPhaseControlRequest.faction != faction)
        return action;

    if (sPhaseControlRequest.mode == DEBUGTOOLS_PHASE_CONTROL_BLOCKED)
        action = DEBUGTOOLS_PHASE_CONTROL_START_BLOCKED;

    DebugToolsPhaseControl_CompleteRequest();
    return action;
}

void DebugToolsPhaseControl_Reset(void)
{
    while (sPhaseControlSuspendTurn.serializationDepth != 0)
        DebugToolsPhaseControl_EndSuspendSerialization();

    if (sPhaseControlRequest.kind != DEBUGTOOLS_PHASE_CONTROL_REQUEST_NONE)
    {
        gDebugToolsProbe.phaseControlExpiredCount++;
        gDebugToolsProbe.phaseControlRestoredCount++;
        DebugToolsPhaseControl_RecordResult(DEBUGTOOLS_PHASE_CONTROL_EXPIRED);
    }

    sPhaseControlRequest.kind = DEBUGTOOLS_PHASE_CONTROL_REQUEST_NONE;
    sPhaseControlSuspendTurn.hasOriginalTurn = FALSE;
    DebugToolsPhaseControl_RefreshProbe();
}

void DebugToolsPhaseControl_Sample(void)
{
    DebugToolsPhaseControl_RefreshProbe();
}

void DebugToolsPhaseControl_BeginSuspendSerialization(void)
{
    if (!sPhaseControlSuspendTurn.hasOriginalTurn)
        return;

    if (sPhaseControlSuspendTurn.serializationDepth == 0)
    {
        sPhaseControlSuspendTurn.liveTurn = gPlaySt.chapterTurnNumber;
        gPlaySt.chapterTurnNumber = sPhaseControlSuspendTurn.originalTurn;
    }
    sPhaseControlSuspendTurn.serializationDepth++;
}

bool DebugToolsPhaseControl_GetSerializedSuspendTurn(u16 *turn)
{
    if (!sPhaseControlSuspendTurn.hasOriginalTurn || turn == NULL)
        return FALSE;

    *turn = sPhaseControlSuspendTurn.originalTurn;
    return TRUE;
}

void DebugToolsPhaseControl_EndSuspendSerialization(void)
{
    if (sPhaseControlSuspendTurn.serializationDepth == 0)
        return;

    sPhaseControlSuspendTurn.serializationDepth--;
    if (sPhaseControlSuspendTurn.serializationDepth == 0)
        gPlaySt.chapterTurnNumber = sPhaseControlSuspendTurn.liveTurn;
}

static u8 DebugToolsPhaseControl_ConfirmTurnIncrement(
    struct MenuProc* menu,
    struct MenuItemProc* item)
{
    (void)item;

    if (DebugToolsPhaseControl_RequestTurn((int)gPlaySt.chapterTurnNumber + 1)
        != DEBUGTOOLS_PHASE_CONTROL_OK)
        return MENU_ACT_SND6B;

    DebugTools_EndSessionAfterMenuEnd(menu);
    return MENU_ACT_SKIPCURSOR | MENU_ACT_END | MENU_ACT_SND6A | MENU_ACT_CLEAR;
}

static u8 DebugToolsPhaseControl_ConfirmTurnDecrement(
    struct MenuProc* menu,
    struct MenuItemProc* item)
{
    (void)item;

    if (DebugToolsPhaseControl_RequestTurn((int)gPlaySt.chapterTurnNumber - 1)
        != DEBUGTOOLS_PHASE_CONTROL_OK)
        return MENU_ACT_SND6B;

    DebugTools_EndSessionAfterMenuEnd(menu);
    return MENU_ACT_SKIPCURSOR | MENU_ACT_END | MENU_ACT_SND6A | MENU_ACT_CLEAR;
}

static u8 DebugToolsPhaseControl_ConfirmFactionMode(
    struct MenuProc* menu,
    int faction,
    enum DebugToolsPhaseControlMode mode)
{
    if (DebugToolsPhaseControl_RequestFactionMode(faction, mode)
        != DEBUGTOOLS_PHASE_CONTROL_OK)
        return MENU_ACT_SND6B;

    DebugTools_EndSessionAfterMenuEnd(menu);
    return MENU_ACT_SKIPCURSOR | MENU_ACT_END | MENU_ACT_SND6A | MENU_ACT_CLEAR;
}

static u8 DebugToolsPhaseControl_ConfirmRedComputer(
    struct MenuProc* menu,
    struct MenuItemProc* item)
{
    (void)item;
    return DebugToolsPhaseControl_ConfirmFactionMode(
        menu,
        FACTION_RED, DEBUGTOOLS_PHASE_CONTROL_COMPUTER);
}

static u8 DebugToolsPhaseControl_ConfirmRedBlocked(
    struct MenuProc* menu,
    struct MenuItemProc* item)
{
    (void)item;
    return DebugToolsPhaseControl_ConfirmFactionMode(
        menu,
        FACTION_RED, DEBUGTOOLS_PHASE_CONTROL_BLOCKED);
}

static u8 DebugToolsPhaseControl_ConfirmGreenComputer(
    struct MenuProc* menu,
    struct MenuItemProc* item)
{
    (void)item;
    return DebugToolsPhaseControl_ConfirmFactionMode(
        menu,
        FACTION_GREEN, DEBUGTOOLS_PHASE_CONTROL_COMPUTER);
}

static u8 DebugToolsPhaseControl_ConfirmGreenBlocked(
    struct MenuProc* menu,
    struct MenuItemProc* item)
{
    (void)item;
    return DebugToolsPhaseControl_ConfirmFactionMode(
        menu,
        FACTION_GREEN, DEBUGTOOLS_PHASE_CONTROL_BLOCKED);
}

/* --- 5. Unit inspection/edit -------------------------------------------- */

struct DebugToolsUnitEditorState
{
    u32 targetState;
    s8 oldValues[DEBUGTOOLS_UNIT_EDIT_FIELD_COUNT];
    s8 previewValues[DEBUGTOOLS_UNIT_EDIT_FIELD_COUNT];
    u8 active;
    u8 closeExpected;
    u8 targetSlot;
    u8 targetCharacterNumber;
    u8 targetClassNumber;
    u8 targetX;
    u8 targetY;
    u8 previewField;
};

typedef char DebugToolsUnitEditorStateLayoutAssert[
    sizeof(struct DebugToolsUnitEditorState) == 0x24 ? 1 : -1];
typedef char DebugToolsUnitEditorProbeLayoutAssert[
    sizeof(struct DebugToolsUnitEditorProbe) == 0x48 ? 1 : -1];

EWRAM_DATA static struct DebugToolsUnitEditorState sUnitEditor = {0};

extern CONST_DATA struct MenuDef gDebugToolsUnitMenuDef;
extern CONST_DATA struct MenuDef gDebugToolsUnitHpMenuDef;
extern CONST_DATA struct MenuDef gDebugToolsUnitStatsMenuDef;
extern CONST_DATA struct MenuDef gDebugToolsUnitAiMenuDef;

static u8 DebugToolsUnit_CloseFlags(void)
{
    return MENU_ACT_SKIPCURSOR | MENU_ACT_END | MENU_ACT_SND6A | MENU_ACT_CLEAR;
}

static int DebugToolsUnit_HasConflict(void)
{
    if (Proc_Find(gProcScr_PlayerPhase) == NULL)
        return 1;

    return EventEngineExists() || BattleEventEngineExists() || IsBattleDeamonActive();
}

static enum DebugToolsUnitEditOutcome DebugToolsUnit_ResolveCursorTarget(
    struct Unit** unitOut)
{
    struct Unit* unit;
    int x;
    int y;
    int targetSlot;
    int characterNumber;
    int classNumber;
    int maxHp;

    *unitOut = NULL;

    if (DebugToolsUnit_HasConflict())
        return DEBUGTOOLS_UNIT_EDIT_OUTCOME_REJECTED_CONFLICT;

    if (gBmMapUnit == NULL || gBmMapSize.x <= 0 || gBmMapSize.y <= 0)
        return DEBUGTOOLS_UNIT_EDIT_OUTCOME_REJECTED_INVALID;

    x = gBmSt.playerCursor.x;
    y = gBmSt.playerCursor.y;

    if (x < 0 || y < 0 || x >= gBmMapSize.x || y >= gBmMapSize.y)
        return DEBUGTOOLS_UNIT_EDIT_OUTCOME_REJECTED_INVALID;

    targetSlot = gBmMapUnit[y][x];
    if (targetSlot == 0)
        return DEBUGTOOLS_UNIT_EDIT_OUTCOME_REJECTED_EMPTY;

    if ((targetSlot & 0xC0) == FACTION_PURPLE)
        return DEBUGTOOLS_UNIT_EDIT_OUTCOME_REJECTED_UNSUPPORTED;

    unit = GetUnit(targetSlot);
    if (!UNIT_IS_VALID(unit) || unit->pClassData == NULL)
        return DEBUGTOOLS_UNIT_EDIT_OUTCOME_REJECTED_INVALID;

    if ((u8)unit->index != targetSlot || unit->xPos != x || unit->yPos != y)
        return DEBUGTOOLS_UNIT_EDIT_OUTCOME_REJECTED_INVALID;

    if (unit->state & (US_HIDDEN | US_UNAVAILABLE | US_RESCUED))
        return DEBUGTOOLS_UNIT_EDIT_OUTCOME_REJECTED_DEAD;

    if (unit->curHP <= 0)
        return DEBUGTOOLS_UNIT_EDIT_OUTCOME_REJECTED_DEAD;

    characterNumber = UNIT_CHAR_ID(unit);
    classNumber = UNIT_CLASS_ID(unit);
    if (characterNumber <= 0 || classNumber <= 0
        || GetCharacterData(characterNumber) != unit->pCharacterData
        || GetClassData(classNumber) != unit->pClassData)
        return DEBUGTOOLS_UNIT_EDIT_OUTCOME_REJECTED_UNSUPPORTED;

    maxHp = GetUnitMaxHp(unit);
    if (maxHp < 1 || maxHp > 0x7F || unit->curHP > maxHp)
        return DEBUGTOOLS_UNIT_EDIT_OUTCOME_REJECTED_INVALID;

    *unitOut = unit;
    return DEBUGTOOLS_UNIT_EDIT_OUTCOME_INSPECTED;
}

static int DebugToolsUnit_ReadField(
    const struct Unit* unit,
    enum DebugToolsUnitEditField field)
{
    switch (field)
    {
        case DEBUGTOOLS_UNIT_EDIT_FIELD_CURRENT_HP:
            return unit->curHP;

        case DEBUGTOOLS_UNIT_EDIT_FIELD_MAX_HP:
            return unit->maxHP;

        case DEBUGTOOLS_UNIT_EDIT_FIELD_POWER:
            return unit->pow;

        case DEBUGTOOLS_UNIT_EDIT_FIELD_SKILL:
            return unit->skl;

        case DEBUGTOOLS_UNIT_EDIT_FIELD_SPEED:
            return unit->spd;

        case DEBUGTOOLS_UNIT_EDIT_FIELD_DEFENSE:
            return unit->def;

        case DEBUGTOOLS_UNIT_EDIT_FIELD_RESISTANCE:
            return unit->res;

        case DEBUGTOOLS_UNIT_EDIT_FIELD_LUCK:
            return unit->lck;

        case DEBUGTOOLS_UNIT_EDIT_FIELD_AI_A:
            return unit->ai1;

        case DEBUGTOOLS_UNIT_EDIT_FIELD_AI_B:
            return unit->ai2;

        case DEBUGTOOLS_UNIT_EDIT_FIELD_STATUS:
            return unit->statusIndex;

        default:
            return 0;
    }
}

static int DebugToolsUnit_GetFieldBounds(
    struct Unit* unit,
    enum DebugToolsUnitEditField field,
    int* minOut,
    int* maxOut)
{
    int min = 0;
    int max;

    switch (field)
    {
        case DEBUGTOOLS_UNIT_EDIT_FIELD_CURRENT_HP:
            min = 1;
            max = GetUnitMaxHp(unit);
            break;

        case DEBUGTOOLS_UNIT_EDIT_FIELD_MAX_HP:
            min = unit->curHP;
            if (min < 1)
                min = 1;
            max = unit->pClassData->maxHP;
            break;

        case DEBUGTOOLS_UNIT_EDIT_FIELD_POWER:
            max = UNIT_POW_MAX(unit);
            break;

        case DEBUGTOOLS_UNIT_EDIT_FIELD_SKILL:
            max = UNIT_SKL_MAX(unit);
            break;

        case DEBUGTOOLS_UNIT_EDIT_FIELD_SPEED:
            max = UNIT_SPD_MAX(unit);
            break;

        case DEBUGTOOLS_UNIT_EDIT_FIELD_DEFENSE:
            max = UNIT_DEF_MAX(unit);
            break;

        case DEBUGTOOLS_UNIT_EDIT_FIELD_RESISTANCE:
            max = UNIT_RES_MAX(unit);
            break;

        case DEBUGTOOLS_UNIT_EDIT_FIELD_LUCK:
            max = UNIT_LCK_MAX(unit);
            break;

        case DEBUGTOOLS_UNIT_EDIT_FIELD_AI_A:
            max = AI_A_INVALID - 1;
            break;

        case DEBUGTOOLS_UNIT_EDIT_FIELD_AI_B:
            max = AI_B_INVALID - 1;
            break;

        default:
            return 0;
    }

    if (max < min || max > 0x7F)
        return 0;

    *minOut = min;
    *maxOut = max;
    return 1;
}

static int DebugToolsUnit_IsStatField(enum DebugToolsUnitEditField field)
{
    return field >= DEBUGTOOLS_UNIT_EDIT_FIELD_MAX_HP
        && field <= DEBUGTOOLS_UNIT_EDIT_FIELD_LUCK;
}

static int DebugToolsUnit_IsClearableStatus(int status)
{
    switch (status)
    {
        case UNIT_STATUS_POISON:
        case UNIT_STATUS_SLEEP:
        case UNIT_STATUS_SILENCED:
        case UNIT_STATUS_BERSERK:
        case UNIT_STATUS_ATTACK:
        case UNIT_STATUS_DEFENSE:
        case UNIT_STATUS_CRIT:
        case UNIT_STATUS_AVOID:
        case UNIT_STATUS_SICK:
        case UNIT_STATUS_PETRIFY:
            return 1;

        default:
            return 0;
    }
}

static u32 DebugToolsUnit_PackOld(
    enum DebugToolsUnitEditOperation operation,
    enum DebugToolsUnitEditField field,
    int oldValue)
{
    return ((u32)operation << 24) | ((u32)field << 16) | ((u32)oldValue & 0xFFFF);
}

static u32 DebugToolsUnit_PackNew(
    enum DebugToolsUnitEditOutcome outcome,
    int newValue)
{
    return ((u32)outcome << 24) | ((u32)newValue & 0x00FFFFFF);
}

static void DebugToolsUnit_RecordTelemetry(
    enum DebugToolsLogCode code,
    enum DebugToolsUnitEditOperation operation,
    enum DebugToolsUnitEditField field,
    int oldValue,
    int newValue,
    enum DebugToolsUnitEditOutcome outcome)
{
    gDebugToolsUnitEditorProbe.unitEditLastOperation = operation;
    gDebugToolsUnitEditorProbe.unitEditLastField = field;
    gDebugToolsUnitEditorProbe.unitEditLastOldValue = (u32)oldValue;
    gDebugToolsUnitEditorProbe.unitEditLastNewValue = (u32)newValue;
    gDebugToolsUnitEditorProbe.unitEditLastOutcome = outcome;

    if (outcome == DEBUGTOOLS_UNIT_EDIT_OUTCOME_PREVIEWED)
        gDebugToolsUnitEditorProbe.unitEditPreviewCount++;
    else if (outcome == DEBUGTOOLS_UNIT_EDIT_OUTCOME_APPLIED)
        gDebugToolsUnitEditorProbe.unitEditTransactionCount++;
    else if (outcome == DEBUGTOOLS_UNIT_EDIT_OUTCOME_CANCELLED)
        gDebugToolsUnitEditorProbe.unitEditCancelCount++;
    else if (outcome == DEBUGTOOLS_UNIT_EDIT_OUTCOME_FORCED_CLEANUP)
        gDebugToolsUnitEditorProbe.unitEditForcedCleanupCount++;
    else if (outcome >= DEBUGTOOLS_UNIT_EDIT_OUTCOME_REJECTED_EMPTY)
        gDebugToolsUnitEditorProbe.unitEditRejectCount++;

    DebugTools_LogEvent(
        code,
        DebugToolsUnit_PackOld(operation, field, oldValue),
        DebugToolsUnit_PackNew(outcome, newValue));
}

static u32 DebugToolsUnit_GetAssertCode(enum DebugToolsUnitEditOutcome outcome)
{
    switch (outcome)
    {
        case DEBUGTOOLS_UNIT_EDIT_OUTCOME_REJECTED_DEAD:
            return DEBUGTOOLS_ASSERT_UNIT_TARGET_DEAD;

        case DEBUGTOOLS_UNIT_EDIT_OUTCOME_REJECTED_STALE:
            return DEBUGTOOLS_ASSERT_UNIT_TARGET_STALE;

        case DEBUGTOOLS_UNIT_EDIT_OUTCOME_REJECTED_CONFLICT:
            return DEBUGTOOLS_ASSERT_UNIT_EDIT_CONFLICT;

        case DEBUGTOOLS_UNIT_EDIT_OUTCOME_REJECTED_RANGE:
            return DEBUGTOOLS_ASSERT_UNIT_EDIT_VALUE_OUT_OF_RANGE;

        case DEBUGTOOLS_UNIT_EDIT_OUTCOME_REJECTED_UNSUPPORTED:
            return DEBUGTOOLS_ASSERT_UNIT_EDIT_UNSUPPORTED;

        default:
            return DEBUGTOOLS_ASSERT_UNIT_TARGET_INVALID;
    }
}

static void DebugToolsUnit_RecordCommitReject(
    enum DebugToolsUnitEditOperation operation,
    enum DebugToolsUnitEditField field,
    int oldValue,
    int newValue,
    enum DebugToolsUnitEditOutcome outcome)
{
    DebugTools_RecordAssertFailure(DebugToolsUnit_GetAssertCode(outcome));
    DebugToolsUnit_RecordTelemetry(
        DEBUGTOOLS_LOG_UNIT_EDIT_REJECTED,
        operation,
        field,
        oldValue,
        newValue,
        outcome);
}

static void DebugToolsUnit_ClearProbeTarget(void)
{
    gDebugToolsProbe.unitInspectTargetFound = 0;
    gDebugToolsProbe.unitInspectLastCurHp = 0;
    gDebugToolsProbe.unitInspectLastMaxHp = 0;
    gDebugToolsUnitEditorProbe.unitInspectTargetSlot = 0;
    gDebugToolsUnitEditorProbe.unitInspectLastCharacterNumber = 0;
    gDebugToolsUnitEditorProbe.unitInspectLastClassNumber = 0;
    gDebugToolsUnitEditorProbe.unitInspectLastState = 0;
    gDebugToolsUnitEditorProbe.unitInspectLastStatus = 0;
    gDebugToolsUnitEditorProbe.unitInspectLastAiA = 0;
    gDebugToolsUnitEditorProbe.unitInspectLastAiB = 0;
}

static void DebugToolsUnit_LoadValues(struct Unit* unit)
{
    int field;

    for (field = DEBUGTOOLS_UNIT_EDIT_FIELD_CURRENT_HP;
         field < DEBUGTOOLS_UNIT_EDIT_FIELD_COUNT;
         field++)
    {
        sUnitEditor.oldValues[field] = (s8)DebugToolsUnit_ReadField(unit, field);
        sUnitEditor.previewValues[field] = sUnitEditor.oldValues[field];
    }

    sUnitEditor.previewField = DEBUGTOOLS_UNIT_EDIT_FIELD_NONE;
}

static void DebugToolsUnit_SnapshotTarget(struct Unit* unit)
{
    sUnitEditor.active = 1;
    sUnitEditor.closeExpected = 0;
    sUnitEditor.targetSlot = (u8)unit->index;
    sUnitEditor.targetCharacterNumber = UNIT_CHAR_ID(unit);
    sUnitEditor.targetClassNumber = UNIT_CLASS_ID(unit);
    sUnitEditor.targetX = unit->xPos;
    sUnitEditor.targetY = unit->yPos;
    sUnitEditor.targetState = unit->state;
    DebugToolsUnit_LoadValues(unit);

    gDebugToolsProbe.unitInspectTargetFound = 1;
    gDebugToolsProbe.unitInspectLastCurHp = unit->curHP;
    gDebugToolsProbe.unitInspectLastMaxHp = GetUnitMaxHp(unit);
    gDebugToolsUnitEditorProbe.unitInspectTargetSlot = sUnitEditor.targetSlot;
    gDebugToolsUnitEditorProbe.unitInspectLastCharacterNumber =
        sUnitEditor.targetCharacterNumber;
    gDebugToolsUnitEditorProbe.unitInspectLastClassNumber = sUnitEditor.targetClassNumber;
    gDebugToolsUnitEditorProbe.unitInspectLastState = unit->state;
    gDebugToolsUnitEditorProbe.unitInspectLastStatus = unit->statusIndex;
    gDebugToolsUnitEditorProbe.unitInspectLastAiA = unit->ai1;
    gDebugToolsUnitEditorProbe.unitInspectLastAiB = unit->ai2;
    gDebugToolsUnitEditorProbe.unitEditLastOperation = DEBUGTOOLS_UNIT_EDIT_OPERATION_NONE;
    gDebugToolsUnitEditorProbe.unitEditLastField = DEBUGTOOLS_UNIT_EDIT_FIELD_NONE;
    gDebugToolsUnitEditorProbe.unitEditLastOldValue = 0;
    gDebugToolsUnitEditorProbe.unitEditLastNewValue = 0;
    gDebugToolsUnitEditorProbe.unitEditLastOutcome =
        DEBUGTOOLS_UNIT_EDIT_OUTCOME_INSPECTED;
}

static enum DebugToolsUnitEditOutcome DebugToolsUnit_RevalidateTarget(
    struct Unit** unitOut)
{
    struct Unit* unit;
    enum DebugToolsUnitEditOutcome outcome;

    outcome = DebugToolsUnit_ResolveCursorTarget(&unit);
    if (outcome == DEBUGTOOLS_UNIT_EDIT_OUTCOME_REJECTED_CONFLICT)
        return outcome;

    if (outcome == DEBUGTOOLS_UNIT_EDIT_OUTCOME_REJECTED_DEAD)
        return outcome;

    if (outcome != DEBUGTOOLS_UNIT_EDIT_OUTCOME_INSPECTED)
        return DEBUGTOOLS_UNIT_EDIT_OUTCOME_REJECTED_STALE;

    if (!sUnitEditor.active
        || (u8)unit->index != sUnitEditor.targetSlot
        || UNIT_CHAR_ID(unit) != sUnitEditor.targetCharacterNumber
        || UNIT_CLASS_ID(unit) != sUnitEditor.targetClassNumber
        || unit->xPos != sUnitEditor.targetX
        || unit->yPos != sUnitEditor.targetY
        || unit->state != sUnitEditor.targetState)
        return DEBUGTOOLS_UNIT_EDIT_OUTCOME_REJECTED_STALE;

    *unitOut = unit;
    return DEBUGTOOLS_UNIT_EDIT_OUTCOME_INSPECTED;
}

static void DebugToolsUnit_RefreshMap(void)
{
    RefreshEntityBmMaps();
    RenderBmMap();
    RefreshUnitSprites();
    gDebugToolsUnitEditorProbe.unitEditRefreshCount++;
}

static void DebugToolsUnit_WriteStatField(
    struct Unit* unit,
    enum DebugToolsUnitEditField field,
    int value)
{
    switch (field)
    {
        case DEBUGTOOLS_UNIT_EDIT_FIELD_MAX_HP:
            unit->maxHP = value;
            break;

        case DEBUGTOOLS_UNIT_EDIT_FIELD_POWER:
            unit->pow = value;
            break;

        case DEBUGTOOLS_UNIT_EDIT_FIELD_SKILL:
            unit->skl = value;
            break;

        case DEBUGTOOLS_UNIT_EDIT_FIELD_SPEED:
            unit->spd = value;
            break;

        case DEBUGTOOLS_UNIT_EDIT_FIELD_DEFENSE:
            unit->def = value;
            break;

        case DEBUGTOOLS_UNIT_EDIT_FIELD_RESISTANCE:
            unit->res = value;
            break;

        case DEBUGTOOLS_UNIT_EDIT_FIELD_LUCK:
            unit->lck = value;
            break;

        default:
            break;
    }
}

static int DebugToolsUnit_ApplyStatField(
    struct Unit* unit,
    enum DebugToolsUnitEditField field,
    int newValue)
{
    struct Unit checked = *unit;
    enum DebugToolsUnitEditField checkField;

    DebugToolsUnit_WriteStatField(&checked, field, newValue);
    UnitCheckStatCaps(&checked);

    for (checkField = DEBUGTOOLS_UNIT_EDIT_FIELD_MAX_HP;
         checkField <= DEBUGTOOLS_UNIT_EDIT_FIELD_LUCK;
         checkField++)
    {
        int expected = checkField == field
            ? newValue
            : DebugToolsUnit_ReadField(unit, checkField);

        if (DebugToolsUnit_ReadField(&checked, checkField) != expected)
            return 0;
    }

    if (checked.conBonus != unit->conBonus || checked.movBonus != unit->movBonus)
        return 0;

    DebugToolsUnit_WriteStatField(unit, field, newValue);
    UnitCheckStatCaps(unit);
    return DebugToolsUnit_ReadField(unit, field) == newValue;
}

static enum DebugToolsUnitEditField DebugToolsUnit_GetFieldForOverride(
    u8 overrideId)
{
    switch (overrideId)
    {
        case DEBUGTOOLS_UNIT_CURRENT_HP_OVERRIDE_ID:
            return DEBUGTOOLS_UNIT_EDIT_FIELD_CURRENT_HP;

        case DEBUGTOOLS_UNIT_MAX_HP_OVERRIDE_ID:
            return DEBUGTOOLS_UNIT_EDIT_FIELD_MAX_HP;

        case DEBUGTOOLS_UNIT_POWER_OVERRIDE_ID:
            return DEBUGTOOLS_UNIT_EDIT_FIELD_POWER;

        case DEBUGTOOLS_UNIT_SKILL_OVERRIDE_ID:
            return DEBUGTOOLS_UNIT_EDIT_FIELD_SKILL;

        case DEBUGTOOLS_UNIT_SPEED_OVERRIDE_ID:
            return DEBUGTOOLS_UNIT_EDIT_FIELD_SPEED;

        case DEBUGTOOLS_UNIT_DEFENSE_OVERRIDE_ID:
            return DEBUGTOOLS_UNIT_EDIT_FIELD_DEFENSE;

        case DEBUGTOOLS_UNIT_RESISTANCE_OVERRIDE_ID:
            return DEBUGTOOLS_UNIT_EDIT_FIELD_RESISTANCE;

        case DEBUGTOOLS_UNIT_LUCK_OVERRIDE_ID:
            return DEBUGTOOLS_UNIT_EDIT_FIELD_LUCK;

        case DEBUGTOOLS_UNIT_AI_A_OVERRIDE_ID:
            return DEBUGTOOLS_UNIT_EDIT_FIELD_AI_A;

        case DEBUGTOOLS_UNIT_AI_B_OVERRIDE_ID:
            return DEBUGTOOLS_UNIT_EDIT_FIELD_AI_B;

        default:
            return DEBUGTOOLS_UNIT_EDIT_FIELD_NONE;
    }
}

static enum DebugToolsUnitEditField DebugToolsUnit_GetFieldForItem(
    const struct MenuItemProc* item)
{
    return DebugToolsUnit_GetFieldForOverride(item->def->overrideId);
}

#ifdef MODERN
static int DebugToolsUnit_ValueMenuItemDraw(
    struct MenuProc* menu,
    struct MenuItemProc* item)
{
    enum DebugToolsUnitEditField field = DebugToolsUnit_GetFieldForItem(item);

    ClearText(&item->text);
    if (item->availability == MENU_DISABLED)
        Text_SetColor(&item->text, TEXT_COLOR_SYSTEM_GRAY);

    Text_DrawString(
        &item->text,
        ExpansionLocale_ResolveCurrent((ExpansionMsgId)item->def->helpMsgId));
    Text_InsertDrawNumberOrBlank(
        &item->text,
        112,
        TEXT_COLOR_SYSTEM_BLUE,
        sUnitEditor.previewValues[field]);
    PutText(
        &item->text,
        TILEMAP_LOCATED(
            BG_GetMapBuffer(menu->frontBg),
            item->xTile,
            item->yTile));

    return 0;
}

static int DebugToolsUnit_ReadOnlyMenuItemDraw(
    struct MenuProc* menu,
    struct MenuItemProc* item)
{
    char value[16];

    ClearText(&item->text);
    Text_SetColor(&item->text, TEXT_COLOR_SYSTEM_GRAY);
    Text_DrawString(
        &item->text,
        ExpansionLocale_ResolveCurrent((ExpansionMsgId)item->def->helpMsgId));

    if (item->def->overrideId == DEBUGTOOLS_UNIT_IDENTITY_OVERRIDE_ID)
    {
        sprintf(
            value,
            "%d/%d",
            (int)sUnitEditor.targetCharacterNumber,
            (int)sUnitEditor.targetClassNumber);
        Text_InsertDrawString(&item->text, 96, TEXT_COLOR_SYSTEM_BLUE, value);
    }
    else
    {
        sprintf(value, "%08X", (unsigned int)sUnitEditor.targetState);
        Text_InsertDrawString(&item->text, 72, TEXT_COLOR_SYSTEM_BLUE, value);
    }

    PutText(
        &item->text,
        TILEMAP_LOCATED(
            BG_GetMapBuffer(menu->frontBg),
            item->xTile,
            item->yTile));

    return 0;
}

#endif

static void DebugToolsUnit_ResetSession(void)
{
    memset(&sUnitEditor, 0, sizeof(sUnitEditor));
}

static void DebugToolsUnit_OnEnd(struct MenuProc* menu)
{
    if (!DebugTools_IsMenuTransitionScheduled())
    {
        int forcedCleanup = sUnitEditor.active && !sUnitEditor.closeExpected;

        if (sUnitEditor.active && !sUnitEditor.closeExpected)
        {
            enum DebugToolsUnitEditField field = sUnitEditor.previewField;

            DebugToolsUnit_RecordTelemetry(
                DEBUGTOOLS_LOG_UNIT_EDIT_FORCED_CLEANUP,
                DEBUGTOOLS_UNIT_EDIT_OPERATION_NONE,
                field,
                sUnitEditor.oldValues[field],
                sUnitEditor.previewValues[field],
                DEBUGTOOLS_UNIT_EDIT_OUTCOME_FORCED_CLEANUP);
        }

        DebugToolsUnit_ResetSession();
        if (forcedCleanup)
        {
            DebugTools_EndSessionAfterMenuEnd(menu);
            return;
        }
    }

    DebugTools_ReturnToHubAfterMenuEnd(menu);
}

static u8 DebugToolsUnit_CloseSelected(
    struct MenuProc* menu,
    struct MenuItemProc* item)
{
    (void)menu;
    (void)item;

    DebugToolsUnit_RecordTelemetry(
        DEBUGTOOLS_LOG_UNIT_EDIT_CANCELLED,
        DEBUGTOOLS_UNIT_EDIT_OPERATION_NONE,
        DEBUGTOOLS_UNIT_EDIT_FIELD_NONE,
        0,
        0,
        DEBUGTOOLS_UNIT_EDIT_OUTCOME_CANCELLED);
    sUnitEditor.closeExpected = 1;
    return DebugToolsUnit_CloseFlags();
}

static void DebugToolsUnit_RecordPreviewCancellation(void)
{
    enum DebugToolsUnitEditField field = sUnitEditor.previewField;

    if (field == DEBUGTOOLS_UNIT_EDIT_FIELD_NONE)
        return;

    if (sUnitEditor.previewValues[field] != sUnitEditor.oldValues[field])
    {
        DebugToolsUnit_RecordTelemetry(
            DEBUGTOOLS_LOG_UNIT_EDIT_CANCELLED,
            DebugToolsUnit_IsStatField(field)
                ? DEBUGTOOLS_UNIT_EDIT_OPERATION_SET_STAT
                : (field == DEBUGTOOLS_UNIT_EDIT_FIELD_CURRENT_HP
                    ? DEBUGTOOLS_UNIT_EDIT_OPERATION_SET_HP
                    : DEBUGTOOLS_UNIT_EDIT_OPERATION_SET_AI),
            field,
            sUnitEditor.oldValues[field],
            sUnitEditor.previewValues[field],
            DEBUGTOOLS_UNIT_EDIT_OUTCOME_CANCELLED);
    }

    sUnitEditor.previewValues[field] = sUnitEditor.oldValues[field];
    sUnitEditor.previewField = DEBUGTOOLS_UNIT_EDIT_FIELD_NONE;
}

static u8 DebugToolsUnit_ReturnToRootSelected(
    struct MenuProc* menu,
    struct MenuItemProc* item)
{
    struct Unit* unit;
    enum DebugToolsUnitEditOutcome outcome;

    (void)item;

    DebugToolsUnit_RecordPreviewCancellation();
    outcome = DebugToolsUnit_RevalidateTarget(&unit);
    if (outcome != DEBUGTOOLS_UNIT_EDIT_OUTCOME_INSPECTED)
    {
        DebugToolsUnit_RecordCommitReject(
            DEBUGTOOLS_UNIT_EDIT_OPERATION_NONE,
            DEBUGTOOLS_UNIT_EDIT_FIELD_NONE,
            0,
            0,
            outcome);
        sUnitEditor.closeExpected = 1;
        return DebugToolsUnit_CloseFlags();
    }

    DebugToolsUnit_LoadValues(unit);
    DebugTools_QueueSubmenuTransition(menu, &gDebugToolsUnitMenuDef);
    if (!DebugTools_IsMenuTransitionScheduled())
        return MENU_ACT_SND6B;

    return DebugToolsUnit_CloseFlags();
}

static u8 DebugToolsUnit_ClearStatusAvailable(
    const struct MenuItemDef* item,
    int number)
{
    (void)item;
    (void)number;

    if (!sUnitEditor.active)
        return MENU_DISABLED;

    return DebugToolsUnit_IsClearableStatus(
        sUnitEditor.oldValues[DEBUGTOOLS_UNIT_EDIT_FIELD_STATUS])
        ? MENU_ENABLED
        : MENU_DISABLED;
}

static u8 DebugToolsUnit_ValueAvailable(
    const struct MenuItemDef* item,
    int number)
{
    struct Unit* unit;
    enum DebugToolsUnitEditField field;
    enum DebugToolsUnitEditOutcome outcome;
    int min;
    int max;

    (void)number;

    field = DebugToolsUnit_GetFieldForOverride(item->overrideId);
    outcome = DebugToolsUnit_RevalidateTarget(&unit);
    if (outcome != DEBUGTOOLS_UNIT_EDIT_OUTCOME_INSPECTED)
        return MENU_DISABLED;

    return DebugToolsUnit_GetFieldBounds(unit, field, &min, &max)
        ? MENU_ENABLED
        : MENU_DISABLED;
}

static u8 DebugToolsUnit_AdjustValue(
    struct MenuProc* menu,
    struct MenuItemProc* item)
{
    struct Unit* unit;
    enum DebugToolsUnitEditField field;
    enum DebugToolsUnitEditOperation operation;
    enum DebugToolsUnitEditOutcome outcome;
    int min;
    int max;
    int oldPreview;
    int newPreview;

    if (!(gKeyStatusPtr->repeatedKeys & (DPAD_LEFT | DPAD_RIGHT)))
        return 0;

    field = DebugToolsUnit_GetFieldForItem(item);
    outcome = DebugToolsUnit_RevalidateTarget(&unit);
    if (outcome != DEBUGTOOLS_UNIT_EDIT_OUTCOME_INSPECTED
        || !DebugToolsUnit_GetFieldBounds(unit, field, &min, &max))
    {
        if (outcome == DEBUGTOOLS_UNIT_EDIT_OUTCOME_INSPECTED)
            outcome = DEBUGTOOLS_UNIT_EDIT_OUTCOME_REJECTED_UNSUPPORTED;

        DebugToolsUnit_RecordTelemetry(
            DEBUGTOOLS_LOG_UNIT_EDIT_REJECTED,
            DEBUGTOOLS_UNIT_EDIT_OPERATION_NONE,
            field,
            sUnitEditor.oldValues[field],
            sUnitEditor.previewValues[field],
            outcome);
        return MENU_ACT_SND6B;
    }

    oldPreview = sUnitEditor.previewValues[field];
    newPreview = oldPreview;

    if ((gKeyStatusPtr->repeatedKeys & DPAD_LEFT) && newPreview > min)
        newPreview--;
    if ((gKeyStatusPtr->repeatedKeys & DPAD_RIGHT) && newPreview < max)
        newPreview++;

    if (newPreview == oldPreview)
        return 0;

    sUnitEditor.previewValues[field] = (s8)newPreview;
    sUnitEditor.previewField = field;

    operation = DebugToolsUnit_IsStatField(field)
        ? DEBUGTOOLS_UNIT_EDIT_OPERATION_SET_STAT
        : (field == DEBUGTOOLS_UNIT_EDIT_FIELD_CURRENT_HP
            ? DEBUGTOOLS_UNIT_EDIT_OPERATION_SET_HP
            : DEBUGTOOLS_UNIT_EDIT_OPERATION_SET_AI);

    DebugToolsUnit_RecordTelemetry(
        DEBUGTOOLS_LOG_UNIT_EDIT_PREVIEW,
        operation,
        field,
        sUnitEditor.oldValues[field],
        newPreview,
        DEBUGTOOLS_UNIT_EDIT_OUTCOME_PREVIEWED);

#ifdef MODERN
    DebugToolsUnit_ValueMenuItemDraw(menu, item);
#else
    (void)menu;
#endif

    return 0;
}

static u8 DebugToolsUnit_CommitValueSelected(
    struct MenuProc* menu,
    struct MenuItemProc* item)
{
    struct Unit* unit;
    enum DebugToolsUnitEditField field;
    enum DebugToolsUnitEditOperation operation;
    enum DebugToolsUnitEditOutcome outcome;
    int min;
    int max;
    int oldValue;
    int newValue;
    int applied = 0;

    (void)menu;

    field = DebugToolsUnit_GetFieldForItem(item);
    operation = DebugToolsUnit_IsStatField(field)
        ? DEBUGTOOLS_UNIT_EDIT_OPERATION_SET_STAT
        : (field == DEBUGTOOLS_UNIT_EDIT_FIELD_CURRENT_HP
            ? DEBUGTOOLS_UNIT_EDIT_OPERATION_SET_HP
            : DEBUGTOOLS_UNIT_EDIT_OPERATION_SET_AI);
    oldValue = sUnitEditor.oldValues[field];
    newValue = sUnitEditor.previewValues[field];

    outcome = DebugToolsUnit_RevalidateTarget(&unit);
    if (outcome != DEBUGTOOLS_UNIT_EDIT_OUTCOME_INSPECTED)
    {
        DebugToolsUnit_RecordCommitReject(
            operation, field, oldValue, newValue, outcome);
        sUnitEditor.closeExpected = 1;
        return DebugToolsUnit_CloseFlags();
    }

    if (DebugToolsUnit_ReadField(unit, field) != oldValue)
    {
        DebugToolsUnit_RecordCommitReject(
            operation,
            field,
            oldValue,
            newValue,
            DEBUGTOOLS_UNIT_EDIT_OUTCOME_REJECTED_STALE);
        sUnitEditor.closeExpected = 1;
        return DebugToolsUnit_CloseFlags();
    }

    if (!DebugToolsUnit_GetFieldBounds(unit, field, &min, &max)
        || newValue < min || newValue > max)
    {
        DebugToolsUnit_RecordCommitReject(
            operation,
            field,
            oldValue,
            newValue,
            DEBUGTOOLS_UNIT_EDIT_OUTCOME_REJECTED_RANGE);
        sUnitEditor.closeExpected = 1;
        return DebugToolsUnit_CloseFlags();
    }

    DebugToolsUnit_RecordTelemetry(
        DEBUGTOOLS_LOG_UNIT_EDIT_PREVIEW,
        operation,
        field,
        oldValue,
        newValue,
        DEBUGTOOLS_UNIT_EDIT_OUTCOME_PREVIEWED);

    if (oldValue == newValue)
    {
        DebugToolsUnit_RecordTelemetry(
            DEBUGTOOLS_LOG_UNIT_EDIT_APPLIED,
            operation,
            field,
            oldValue,
            newValue,
            DEBUGTOOLS_UNIT_EDIT_OUTCOME_NO_CHANGE);
        sUnitEditor.closeExpected = 1;
        return DebugToolsUnit_CloseFlags();
    }

    if (field == DEBUGTOOLS_UNIT_EDIT_FIELD_CURRENT_HP)
    {
        SetUnitHp(unit, newValue);
        applied = unit->curHP == newValue;
    }
    else if (DebugToolsUnit_IsStatField(field))
    {
        applied = DebugToolsUnit_ApplyStatField(unit, field, newValue);
    }
    else if (field == DEBUGTOOLS_UNIT_EDIT_FIELD_AI_A)
    {
        ChangeUnitAi(unit, newValue, AI_B_INVALID, 0);
        applied = unit->ai1 == newValue && unit->ai_a_pc == 0;
    }
    else if (field == DEBUGTOOLS_UNIT_EDIT_FIELD_AI_B)
    {
        ChangeUnitAi(unit, AI_A_INVALID, newValue, 0);
        applied = unit->ai2 == newValue && unit->ai_b_pc == 0;
    }

    if (!applied)
    {
        DebugToolsUnit_RecordCommitReject(
            operation,
            field,
            oldValue,
            newValue,
            DEBUGTOOLS_UNIT_EDIT_OUTCOME_REJECTED_UNSUPPORTED);
        sUnitEditor.closeExpected = 1;
        return DebugToolsUnit_CloseFlags();
    }

    DebugToolsUnit_RefreshMap();
    DebugToolsUnit_RecordTelemetry(
        DEBUGTOOLS_LOG_UNIT_EDIT_APPLIED,
        operation,
        field,
        oldValue,
        newValue,
        DEBUGTOOLS_UNIT_EDIT_OUTCOME_APPLIED);
    sUnitEditor.closeExpected = 1;
    return DebugToolsUnit_CloseFlags();
}

static u8 DebugToolsUnit_HealSelected(
    struct MenuProc* menu,
    struct MenuItemProc* item)
{
    struct Unit* unit;
    enum DebugToolsUnitEditOutcome outcome;
    int oldValue;
    int newValue;

    (void)menu;
    (void)item;

    oldValue = sUnitEditor.oldValues[DEBUGTOOLS_UNIT_EDIT_FIELD_CURRENT_HP];
    newValue = sUnitEditor.oldValues[DEBUGTOOLS_UNIT_EDIT_FIELD_CURRENT_HP];
    outcome = DebugToolsUnit_RevalidateTarget(&unit);
    if (outcome == DEBUGTOOLS_UNIT_EDIT_OUTCOME_INSPECTED)
        newValue = GetUnitMaxHp(unit);

    if (outcome != DEBUGTOOLS_UNIT_EDIT_OUTCOME_INSPECTED
        || unit->curHP != oldValue)
    {
        if (outcome == DEBUGTOOLS_UNIT_EDIT_OUTCOME_INSPECTED)
            outcome = DEBUGTOOLS_UNIT_EDIT_OUTCOME_REJECTED_STALE;

        DebugTools_LogEvent(DEBUGTOOLS_LOG_UNIT_HEAL_SKIPPED_INVALID, oldValue, newValue);
        DebugToolsUnit_RecordCommitReject(
            DEBUGTOOLS_UNIT_EDIT_OPERATION_HEAL,
            DEBUGTOOLS_UNIT_EDIT_FIELD_CURRENT_HP,
            oldValue,
            newValue,
            outcome);
        sUnitEditor.closeExpected = 1;
        return DebugToolsUnit_CloseFlags();
    }

    DebugToolsUnit_RecordTelemetry(
        DEBUGTOOLS_LOG_UNIT_EDIT_PREVIEW,
        DEBUGTOOLS_UNIT_EDIT_OPERATION_HEAL,
        DEBUGTOOLS_UNIT_EDIT_FIELD_CURRENT_HP,
        oldValue,
        newValue,
        DEBUGTOOLS_UNIT_EDIT_OUTCOME_PREVIEWED);

    if (oldValue != newValue)
    {
        SetUnitHp(unit, newValue);
        if (unit->curHP != newValue)
        {
            DebugToolsUnit_RecordCommitReject(
                DEBUGTOOLS_UNIT_EDIT_OPERATION_HEAL,
                DEBUGTOOLS_UNIT_EDIT_FIELD_CURRENT_HP,
                oldValue,
                newValue,
                DEBUGTOOLS_UNIT_EDIT_OUTCOME_REJECTED_RANGE);
            sUnitEditor.closeExpected = 1;
            return DebugToolsUnit_CloseFlags();
        }

        DebugToolsUnit_RefreshMap();
        DebugTools_LogEvent(DEBUGTOOLS_LOG_UNIT_HEAL_APPLIED, oldValue, newValue);
        DebugToolsUnit_RecordTelemetry(
            DEBUGTOOLS_LOG_UNIT_EDIT_APPLIED,
            DEBUGTOOLS_UNIT_EDIT_OPERATION_HEAL,
            DEBUGTOOLS_UNIT_EDIT_FIELD_CURRENT_HP,
            oldValue,
            newValue,
            DEBUGTOOLS_UNIT_EDIT_OUTCOME_APPLIED);
    }
    else
    {
        DebugTools_LogEvent(DEBUGTOOLS_LOG_UNIT_HEAL_APPLIED, oldValue, newValue);
        DebugToolsUnit_RecordTelemetry(
            DEBUGTOOLS_LOG_UNIT_EDIT_APPLIED,
            DEBUGTOOLS_UNIT_EDIT_OPERATION_HEAL,
            DEBUGTOOLS_UNIT_EDIT_FIELD_CURRENT_HP,
            oldValue,
            newValue,
            DEBUGTOOLS_UNIT_EDIT_OUTCOME_NO_CHANGE);
    }

    gDebugToolsProbe.unitHealTransactionCount++;
    sUnitEditor.closeExpected = 1;
    return DebugToolsUnit_CloseFlags();
}

static u8 DebugToolsUnit_ClearStatusSelected(
    struct MenuProc* menu,
    struct MenuItemProc* item)
{
    struct Unit* unit;
    enum DebugToolsUnitEditOutcome outcome;
    int oldValue = sUnitEditor.oldValues[DEBUGTOOLS_UNIT_EDIT_FIELD_STATUS];

    (void)menu;
    (void)item;

    outcome = DebugToolsUnit_RevalidateTarget(&unit);
    if (outcome != DEBUGTOOLS_UNIT_EDIT_OUTCOME_INSPECTED)
    {
        DebugToolsUnit_RecordCommitReject(
            DEBUGTOOLS_UNIT_EDIT_OPERATION_CLEAR_STATUS,
            DEBUGTOOLS_UNIT_EDIT_FIELD_STATUS,
            oldValue,
            UNIT_STATUS_NONE,
            outcome);
        sUnitEditor.closeExpected = 1;
        return DebugToolsUnit_CloseFlags();
    }

    if (unit->statusIndex != oldValue
        || !DebugToolsUnit_IsClearableStatus(oldValue))
    {
        DebugToolsUnit_RecordCommitReject(
            DEBUGTOOLS_UNIT_EDIT_OPERATION_CLEAR_STATUS,
            DEBUGTOOLS_UNIT_EDIT_FIELD_STATUS,
            oldValue,
            UNIT_STATUS_NONE,
            unit->statusIndex != oldValue
                ? DEBUGTOOLS_UNIT_EDIT_OUTCOME_REJECTED_STALE
                : DEBUGTOOLS_UNIT_EDIT_OUTCOME_REJECTED_UNSUPPORTED);
        sUnitEditor.closeExpected = 1;
        return DebugToolsUnit_CloseFlags();
    }

    DebugToolsUnit_RecordTelemetry(
        DEBUGTOOLS_LOG_UNIT_EDIT_PREVIEW,
        DEBUGTOOLS_UNIT_EDIT_OPERATION_CLEAR_STATUS,
        DEBUGTOOLS_UNIT_EDIT_FIELD_STATUS,
        oldValue,
        UNIT_STATUS_NONE,
        DEBUGTOOLS_UNIT_EDIT_OUTCOME_PREVIEWED);
    SetUnitStatus(unit, UNIT_STATUS_NONE);

    if (unit->statusIndex != UNIT_STATUS_NONE || unit->statusDuration != 0)
    {
        DebugToolsUnit_RecordCommitReject(
            DEBUGTOOLS_UNIT_EDIT_OPERATION_CLEAR_STATUS,
            DEBUGTOOLS_UNIT_EDIT_FIELD_STATUS,
            oldValue,
            UNIT_STATUS_NONE,
            DEBUGTOOLS_UNIT_EDIT_OUTCOME_REJECTED_UNSUPPORTED);
        sUnitEditor.closeExpected = 1;
        return DebugToolsUnit_CloseFlags();
    }

    DebugToolsUnit_RefreshMap();
    DebugToolsUnit_RecordTelemetry(
        DEBUGTOOLS_LOG_UNIT_EDIT_APPLIED,
        DEBUGTOOLS_UNIT_EDIT_OPERATION_CLEAR_STATUS,
        DEBUGTOOLS_UNIT_EDIT_FIELD_STATUS,
        oldValue,
        UNIT_STATUS_NONE,
        DEBUGTOOLS_UNIT_EDIT_OUTCOME_APPLIED);
    sUnitEditor.closeExpected = 1;
    return DebugToolsUnit_CloseFlags();
}

static u8 DebugToolsUnit_OpenEditorSelected(
    struct MenuProc* menu,
    struct MenuItemProc* item)
{
    struct Unit* unit;
    const struct MenuDef* menuDef = NULL;
    enum DebugToolsUnitEditOutcome outcome;

    outcome = DebugToolsUnit_RevalidateTarget(&unit);
    if (outcome != DEBUGTOOLS_UNIT_EDIT_OUTCOME_INSPECTED)
    {
        DebugToolsUnit_RecordCommitReject(
            DEBUGTOOLS_UNIT_EDIT_OPERATION_NONE,
            DEBUGTOOLS_UNIT_EDIT_FIELD_NONE,
            0,
            0,
            outcome);
        sUnitEditor.closeExpected = 1;
        return DebugToolsUnit_CloseFlags();
    }

    DebugToolsUnit_LoadValues(unit);

    switch (item->def->overrideId)
    {
        case DEBUGTOOLS_UNIT_EDIT_HP_OVERRIDE_ID:
            menuDef = &gDebugToolsUnitHpMenuDef;
            break;

        case DEBUGTOOLS_UNIT_EDIT_STATS_OVERRIDE_ID:
            menuDef = &gDebugToolsUnitStatsMenuDef;
            break;

        case DEBUGTOOLS_UNIT_EDIT_AI_OVERRIDE_ID:
            menuDef = &gDebugToolsUnitAiMenuDef;
            break;

        default:
            DebugToolsUnit_RecordCommitReject(
                DEBUGTOOLS_UNIT_EDIT_OPERATION_NONE,
                DEBUGTOOLS_UNIT_EDIT_FIELD_NONE,
                0,
                0,
                DEBUGTOOLS_UNIT_EDIT_OUTCOME_REJECTED_UNSUPPORTED);
            sUnitEditor.closeExpected = 1;
            return DebugToolsUnit_CloseFlags();
    }

    DebugTools_QueueSubmenuTransition(menu, menuDef);
    if (!DebugTools_IsMenuTransitionScheduled())
        return MENU_ACT_SND6B;

    return DebugToolsUnit_CloseFlags();
}

#ifdef MODERN
#define DEBUGTOOLS_UNIT_LOCALIZED_FIELDS(message, draw) \
    .helpMsgId = (u16)(message), .onDraw = (draw),
#else
#define DEBUGTOOLS_UNIT_LOCALIZED_FIELDS(message, draw)
#endif

static const struct MenuItemDef sUnitMenuItemDefs[] = {
    {
        .name = "Confirm Heal to Full",
        .overrideId = DEBUGTOOLS_UNIT_OVERRIDE_ID,
        .isAvailable = MenuAlwaysEnabled,
        .onSelected = DebugToolsUnit_HealSelected,
        DEBUGTOOLS_UNIT_LOCALIZED_FIELDS(
            EXP_MSG_DEBUG_CONFIRM_HEAL_FULL,
            DebugToolsTools_LocalizedMenuItemDraw)
    },
    {
        .name = "Edit HP",
        .overrideId = DEBUGTOOLS_UNIT_EDIT_HP_OVERRIDE_ID,
        .isAvailable = MenuAlwaysEnabled,
        .onSelected = DebugToolsUnit_OpenEditorSelected,
        DEBUGTOOLS_UNIT_LOCALIZED_FIELDS(
            EXP_MSG_DEBUG_UNIT_EDIT_HP,
            DebugToolsTools_LocalizedMenuItemDraw)
    },
    {
        .name = "Edit Stats",
        .overrideId = DEBUGTOOLS_UNIT_EDIT_STATS_OVERRIDE_ID,
        .isAvailable = MenuAlwaysEnabled,
        .onSelected = DebugToolsUnit_OpenEditorSelected,
        DEBUGTOOLS_UNIT_LOCALIZED_FIELDS(
            EXP_MSG_DEBUG_UNIT_EDIT_STATS,
            DebugToolsTools_LocalizedMenuItemDraw)
    },
    {
        .name = "Edit AI",
        .overrideId = DEBUGTOOLS_UNIT_EDIT_AI_OVERRIDE_ID,
        .isAvailable = MenuAlwaysEnabled,
        .onSelected = DebugToolsUnit_OpenEditorSelected,
        DEBUGTOOLS_UNIT_LOCALIZED_FIELDS(
            EXP_MSG_DEBUG_UNIT_EDIT_AI,
            DebugToolsTools_LocalizedMenuItemDraw)
    },
    {
        .name = "Confirm Clear Status",
        .overrideId = DEBUGTOOLS_UNIT_CLEAR_STATUS_OVERRIDE_ID,
        .isAvailable = DebugToolsUnit_ClearStatusAvailable,
        .onSelected = DebugToolsUnit_ClearStatusSelected,
        DEBUGTOOLS_UNIT_LOCALIZED_FIELDS(
            EXP_MSG_DEBUG_UNIT_CLEAR_STATUS,
            DebugToolsTools_LocalizedMenuItemDraw)
    },
    {
        .name = "Unit/Class",
        .overrideId = DEBUGTOOLS_UNIT_IDENTITY_OVERRIDE_ID,
        .isAvailable = MenuAlwaysDisabled,
        DEBUGTOOLS_UNIT_LOCALIZED_FIELDS(
            EXP_MSG_DEBUG_UNIT_IDENTITY,
            DebugToolsUnit_ReadOnlyMenuItemDraw)
    },
    {
        .name = "State",
        .overrideId = DEBUGTOOLS_UNIT_STATE_OVERRIDE_ID,
        .isAvailable = MenuAlwaysDisabled,
        DEBUGTOOLS_UNIT_LOCALIZED_FIELDS(
            EXP_MSG_DEBUG_UNIT_STATE,
            DebugToolsUnit_ReadOnlyMenuItemDraw)
    },
    {
        .name = "Back",
        .isAvailable = MenuAlwaysEnabled,
        .onSelected = DebugToolsUnit_CloseSelected,
        DEBUGTOOLS_UNIT_LOCALIZED_FIELDS(
            EXP_MSG_FRAMEWORK_BACK,
            DebugToolsTools_LocalizedMenuItemDraw)
    },
    {0}
};

static const struct MenuItemDef sUnitHpMenuItemDefs[] = {
    {
        .name = "Current HP",
        .overrideId = DEBUGTOOLS_UNIT_CURRENT_HP_OVERRIDE_ID,
        .isAvailable = DebugToolsUnit_ValueAvailable,
        .onSelected = DebugToolsUnit_CommitValueSelected,
        .onIdle = DebugToolsUnit_AdjustValue,
        DEBUGTOOLS_UNIT_LOCALIZED_FIELDS(
            EXP_MSG_DEBUG_UNIT_CURRENT_HP,
            DebugToolsUnit_ValueMenuItemDraw)
    },
    {
        .name = "Back",
        .isAvailable = MenuAlwaysEnabled,
        .onSelected = DebugToolsUnit_ReturnToRootSelected,
        DEBUGTOOLS_UNIT_LOCALIZED_FIELDS(
            EXP_MSG_FRAMEWORK_BACK,
            DebugToolsTools_LocalizedMenuItemDraw)
    },
    {0}
};

static const struct MenuItemDef sUnitStatsMenuItemDefs[] = {
    {
        .name = "Max HP",
        .overrideId = DEBUGTOOLS_UNIT_MAX_HP_OVERRIDE_ID,
        .isAvailable = DebugToolsUnit_ValueAvailable,
        .onSelected = DebugToolsUnit_CommitValueSelected,
        .onIdle = DebugToolsUnit_AdjustValue,
        DEBUGTOOLS_UNIT_LOCALIZED_FIELDS(
            EXP_MSG_DEBUG_UNIT_MAX_HP,
            DebugToolsUnit_ValueMenuItemDraw)
    },
    {
        .name = "Power",
        .overrideId = DEBUGTOOLS_UNIT_POWER_OVERRIDE_ID,
        .isAvailable = DebugToolsUnit_ValueAvailable,
        .onSelected = DebugToolsUnit_CommitValueSelected,
        .onIdle = DebugToolsUnit_AdjustValue,
        DEBUGTOOLS_UNIT_LOCALIZED_FIELDS(
            EXP_MSG_DEBUG_UNIT_POWER,
            DebugToolsUnit_ValueMenuItemDraw)
    },
    {
        .name = "Skill",
        .overrideId = DEBUGTOOLS_UNIT_SKILL_OVERRIDE_ID,
        .isAvailable = DebugToolsUnit_ValueAvailable,
        .onSelected = DebugToolsUnit_CommitValueSelected,
        .onIdle = DebugToolsUnit_AdjustValue,
        DEBUGTOOLS_UNIT_LOCALIZED_FIELDS(
            EXP_MSG_DEBUG_UNIT_SKILL,
            DebugToolsUnit_ValueMenuItemDraw)
    },
    {
        .name = "Speed",
        .overrideId = DEBUGTOOLS_UNIT_SPEED_OVERRIDE_ID,
        .isAvailable = DebugToolsUnit_ValueAvailable,
        .onSelected = DebugToolsUnit_CommitValueSelected,
        .onIdle = DebugToolsUnit_AdjustValue,
        DEBUGTOOLS_UNIT_LOCALIZED_FIELDS(
            EXP_MSG_DEBUG_UNIT_SPEED,
            DebugToolsUnit_ValueMenuItemDraw)
    },
    {
        .name = "Defense",
        .overrideId = DEBUGTOOLS_UNIT_DEFENSE_OVERRIDE_ID,
        .isAvailable = DebugToolsUnit_ValueAvailable,
        .onSelected = DebugToolsUnit_CommitValueSelected,
        .onIdle = DebugToolsUnit_AdjustValue,
        DEBUGTOOLS_UNIT_LOCALIZED_FIELDS(
            EXP_MSG_DEBUG_UNIT_DEFENSE,
            DebugToolsUnit_ValueMenuItemDraw)
    },
    {
        .name = "Resistance",
        .overrideId = DEBUGTOOLS_UNIT_RESISTANCE_OVERRIDE_ID,
        .isAvailable = DebugToolsUnit_ValueAvailable,
        .onSelected = DebugToolsUnit_CommitValueSelected,
        .onIdle = DebugToolsUnit_AdjustValue,
        DEBUGTOOLS_UNIT_LOCALIZED_FIELDS(
            EXP_MSG_DEBUG_UNIT_RESISTANCE,
            DebugToolsUnit_ValueMenuItemDraw)
    },
    {
        .name = "Luck",
        .overrideId = DEBUGTOOLS_UNIT_LUCK_OVERRIDE_ID,
        .isAvailable = DebugToolsUnit_ValueAvailable,
        .onSelected = DebugToolsUnit_CommitValueSelected,
        .onIdle = DebugToolsUnit_AdjustValue,
        DEBUGTOOLS_UNIT_LOCALIZED_FIELDS(
            EXP_MSG_DEBUG_UNIT_LUCK,
            DebugToolsUnit_ValueMenuItemDraw)
    },
    {
        .name = "Back",
        .isAvailable = MenuAlwaysEnabled,
        .onSelected = DebugToolsUnit_ReturnToRootSelected,
        DEBUGTOOLS_UNIT_LOCALIZED_FIELDS(
            EXP_MSG_FRAMEWORK_BACK,
            DebugToolsTools_LocalizedMenuItemDraw)
    },
    {0}
};

static const struct MenuItemDef sUnitAiMenuItemDefs[] = {
    {
        .name = "AI A",
        .overrideId = DEBUGTOOLS_UNIT_AI_A_OVERRIDE_ID,
        .isAvailable = DebugToolsUnit_ValueAvailable,
        .onSelected = DebugToolsUnit_CommitValueSelected,
        .onIdle = DebugToolsUnit_AdjustValue,
        DEBUGTOOLS_UNIT_LOCALIZED_FIELDS(
            EXP_MSG_DEBUG_UNIT_AI_A,
            DebugToolsUnit_ValueMenuItemDraw)
    },
    {
        .name = "AI B",
        .overrideId = DEBUGTOOLS_UNIT_AI_B_OVERRIDE_ID,
        .isAvailable = DebugToolsUnit_ValueAvailable,
        .onSelected = DebugToolsUnit_CommitValueSelected,
        .onIdle = DebugToolsUnit_AdjustValue,
        DEBUGTOOLS_UNIT_LOCALIZED_FIELDS(
            EXP_MSG_DEBUG_UNIT_AI_B,
            DebugToolsUnit_ValueMenuItemDraw)
    },
    {
        .name = "Back",
        .isAvailable = MenuAlwaysEnabled,
        .onSelected = DebugToolsUnit_ReturnToRootSelected,
        DEBUGTOOLS_UNIT_LOCALIZED_FIELDS(
            EXP_MSG_FRAMEWORK_BACK,
            DebugToolsTools_LocalizedMenuItemDraw)
    },
    {0}
};

CONST_DATA struct MenuDef gDebugToolsUnitMenuDef = {
    {1, 2, DEBUGTOOLS_MENU_WIDTH_TILES, 0},
    0,
    sUnitMenuItemDefs,
    DEBUGTOOLS_UNIT_MENU_ON_INIT,
    DebugToolsUnit_OnEnd,
    0,
    DebugToolsUnit_CloseSelected,
    0,
    0
};

CONST_DATA struct MenuDef gDebugToolsUnitHpMenuDef = {
    {1, 2, DEBUGTOOLS_MENU_WIDTH_TILES, 0},
    0,
    sUnitHpMenuItemDefs,
    DEBUGTOOLS_UNIT_MENU_ON_INIT,
    DebugToolsUnit_OnEnd,
    0,
    DebugToolsUnit_ReturnToRootSelected,
    0,
    0
};

CONST_DATA struct MenuDef gDebugToolsUnitStatsMenuDef = {
    {1, 2, DEBUGTOOLS_MENU_WIDTH_TILES, 0},
    0,
    sUnitStatsMenuItemDefs,
    DEBUGTOOLS_UNIT_MENU_ON_INIT,
    DebugToolsUnit_OnEnd,
    0,
    DebugToolsUnit_ReturnToRootSelected,
    0,
    0
};

CONST_DATA struct MenuDef gDebugToolsUnitAiMenuDef = {
    {1, 2, DEBUGTOOLS_MENU_WIDTH_TILES, 0},
    0,
    sUnitAiMenuItemDefs,
    DEBUGTOOLS_UNIT_MENU_ON_INIT,
    DebugToolsUnit_OnEnd,
    0,
    DebugToolsUnit_ReturnToRootSelected,
    0,
    0
};

static u8 DebugToolsActions_UnitInspectSelected(struct MenuProc* menu, struct MenuItemProc* item)
{
    struct Unit* unit;
    enum DebugToolsUnitEditOutcome outcome;
#if defined(FE8_PORTRAIT_PACKAGE_RUNTIME_TEST)
    struct FaceProc* face;
    struct FaceBlinkProc* mouth;
    u16* mouthTiles;
#endif
    char buf[64];

    (void)item;

    DebugToolsUnit_ResetSession();
    outcome = DebugToolsUnit_ResolveCursorTarget(&unit);

    if (outcome == DEBUGTOOLS_UNIT_EDIT_OUTCOME_INSPECTED)
    {
        DebugToolsUnit_SnapshotTarget(unit);
#if defined(FE8_PORTRAIT_PACKAGE_RUNTIME_TEST)
        PutFaceChibi(
            DEBUGTOOLS_PORTRAIT_PROBE_FACE_ID,
            TILEMAP_LOCATED(BG_GetMapBuffer(2), 1, 4),
            DEBUGTOOLS_PORTRAIT_PROBE_CHR,
            DEBUGTOOLS_PORTRAIT_PROBE_PAL,
            FALSE);
        BG_EnableSyncByMask(BG2_SYNC_BIT);
#endif

#if defined(FE8_PORTRAIT_PACKAGE_RUNTIME_TEST)
        gPortraitPackageRuntimeProbe.faceId = DEBUGTOOLS_PORTRAIT_PROBE_FACE_ID;
        gPortraitPackageRuntimeProbe.minimugRenderCount++;
        gPortraitPackageRuntimeProbe.minimugVramWord = DebugToolsTools_ReadU32(
            (const u16*)(VRAM + (DEBUGTOOLS_PORTRAIT_PROBE_CHR * CHR_SIZE) + 0x20));
        gPortraitPackageRuntimeProbe.minimugPaletteWord =
            DebugToolsTools_ReadU32(gPaletteBuffer + (DEBUGTOOLS_PORTRAIT_PROBE_PAL * 0x10));
        face = StartFace2(
            0,
            DEBUGTOOLS_PORTRAIT_PROBE_FACE_ID,
            48,
            24,
            FACE_DISP_KIND(FACE_96x80) | FACE_DISP_TALK_1);
        if (face != NULL)
        {
            SetFaceEyeControl(face, 2);
            gPortraitPackageRuntimeProbe.fullFaceRenderCount++;
            gPortraitPackageRuntimeProbe.mouthDisplayBits =
                GetFaceDisplayBits(face) & (FACE_DISP_TALK_1 | FACE_DISP_TALK_2);
            gPortraitPackageRuntimeProbe.eyeControl = 2;
            gPortraitPackageRuntimeProbe.faceOam2 = face->oam2;

            mouth = (struct FaceBlinkProc*)face->unk_44;
            FaceMouth_Init(mouth);
            mouth->unk_32 = -1;
            mouth->blinkControl = 0;
            FaceMouth_Loop(mouth);
            mouthTiles = (u16*)(
                VRAM + (((face->oam2 + 28) & 0x3FF) * CHR_SIZE));
            gPortraitPackageRuntimeProbe.mouthFrame0 =
                mouthTiles[2] ^ mouthTiles[18] ^ mouthTiles[34] ^ mouthTiles[50];

            mouth->unk_32 = -1;
            mouth->blinkControl = 1;
            FaceMouth_Loop(mouth);
            gPortraitPackageRuntimeProbe.mouthFrame2 =
                mouthTiles[2] ^ mouthTiles[18] ^ mouthTiles[34] ^ mouthTiles[50];
        }
#endif
        sprintf(buf, "%s %d/%d",
            DEBUGTOOLS_LOCALIZED_TEXT(EXP_MSG_DEBUG_STATUS_UNIT_HP, "UNIT HP"),
            unit->curHP, GetUnitMaxHp(unit));
    }
    else
    {
        DebugToolsUnit_ClearProbeTarget();
        DebugToolsUnit_RecordTelemetry(
            DEBUGTOOLS_LOG_UNIT_EDIT_REJECTED,
            DEBUGTOOLS_UNIT_EDIT_OPERATION_NONE,
            DEBUGTOOLS_UNIT_EDIT_FIELD_NONE,
            0,
            0,
            outcome);
        sprintf(buf, "%s", DEBUGTOOLS_LOCALIZED_TEXT(
            EXP_MSG_DEBUG_STATUS_UNIT_UNAVAILABLE, "UNIT N/A"));
    }

    DebugTools_LogEvent(DEBUGTOOLS_LOG_UNIT_INSPECT,
        gDebugToolsProbe.unitInspectLastCurHp, gDebugToolsProbe.unitInspectLastMaxHp);
    DebugToolsTools_ShowStatusLine(buf);

    if (outcome != DEBUGTOOLS_UNIT_EDIT_OUTCOME_INSPECTED)
        return MENU_ACT_SND6B;

    DebugTools_QueueSubmenuTransition(menu, &gDebugToolsUnitMenuDef);

    return MENU_ACT_SKIPCURSOR | MENU_ACT_END | MENU_ACT_SND6A | MENU_ACT_CLEAR;
}

CONST_DATA static struct DebugToolsAction sUnitInspectAction = {
    5, "Unit Inspect", DebugToolsActions_UnitInspectSelected
};

/* --- 6. Convoy inspection/edit ------------------------------------------ */

/* Convoy and RNG are mutually exclusive and each needs at most Back plus the
 * terminator. #124's larger Flag/Chapter menu
 * reuses gDebugToolsMenuItemDefs after the hub is ended by the deferred
 * transition builder below. */
EWRAM_DATA static struct MenuItemDef sDebugToolsToolMenuItemDefs[3] = {{0}};

#define sConvoyMenuItemDefs sDebugToolsToolMenuItemDefs
#define sFlagMenuItemDefs gDebugToolsMenuItemDefs
#define sRngMenuItemDefs sDebugToolsToolMenuItemDefs

static void DebugToolsConvoy_OnEnd(struct MenuProc* menu)
{
    DebugTools_ReturnToHubAfterMenuEnd(menu);
}

CONST_DATA struct MenuDef gDebugToolsConvoyMenuDef = {
    {1, 1, DEBUGTOOLS_MENU_WIDTH_TILES, 0},
    0,
    sConvoyMenuItemDefs,
    DebugToolsTools_MenuOnInit,
    DebugToolsConvoy_OnEnd,
    0,
    DebugTools_CancelMenu,
    0,
    0
};

static u8 DebugToolsConvoy_ConfirmSelected(struct MenuProc* menu, struct MenuItemProc* item)
{
    int slot;

    (void)item;

    /* AddItemToConvoy (src/bmcontainer.c) already bounds-checks capacity
     * internally and returns -1 without mutating anything when full --
     * no separate DEBUGTOOLS_ASSERT is needed to keep this bounded, but
     * the result is still explicitly branched on so a full convoy is a
     * logged, visible no-op rather than a silently ignored request. */
    slot = AddItemToConvoy(DEBUGTOOLS_CONVOY_TEST_ITEM);

    if (slot != -1)
    {
        gDebugToolsProbe.convoyAddTransactionCount++;
        DebugTools_LogEvent(DEBUGTOOLS_LOG_CONVOY_ADD_APPLIED,
            (u32)DEBUGTOOLS_CONVOY_TEST_ITEM, (u32)slot);
    }
    else
    {
        DebugTools_LogEvent(DEBUGTOOLS_LOG_CONVOY_ADD_SKIPPED_FULL,
            (u32)DEBUGTOOLS_CONVOY_TEST_ITEM, 0);
    }

    return MENU_ACT_SKIPCURSOR | MENU_ACT_END | MENU_ACT_SND6A | MENU_ACT_CLEAR;
}

static void DebugToolsConvoy_BuildMenuItems(void)
{
    memset(sConvoyMenuItemDefs, 0, sizeof(sConvoyMenuItemDefs));

    sConvoyMenuItemDefs[0].name = "Confirm Add Item";
    sConvoyMenuItemDefs[0].overrideId = DEBUGTOOLS_CONVOY_OVERRIDE_ID;
    sConvoyMenuItemDefs[0].isAvailable = MenuAlwaysEnabled;
    sConvoyMenuItemDefs[0].onSelected = DebugToolsConvoy_ConfirmSelected;
    DEBUGTOOLS_LOCALIZE_ITEM(
        &sConvoyMenuItemDefs[0],
        EXP_MSG_DEBUG_CONFIRM_ADD_ITEM);

    sConvoyMenuItemDefs[1].name = "Back";
    sConvoyMenuItemDefs[1].isAvailable = MenuAlwaysEnabled;
    sConvoyMenuItemDefs[1].onSelected = DebugTools_CancelMenu;
    DEBUGTOOLS_LOCALIZE_ITEM(
        &sConvoyMenuItemDefs[1],
        EXP_MSG_FRAMEWORK_BACK);

}

static u8 DebugToolsActions_ConvoyInspectSelected(struct MenuProc* menu, struct MenuItemProc* item)
{
    int count;
    char buf[64];

    (void)item;

    count = GetConvoyItemCount();
    gDebugToolsProbe.convoyLastItemCount = (u32)count;
    sprintf(buf, "%s %d/%d",
        DEBUGTOOLS_LOCALIZED_TEXT(EXP_MSG_DEBUG_STATUS_CONVOY, "CONVOY"),
        count, (int)CONVOY_ITEM_COUNT);

    DebugTools_LogEvent(DEBUGTOOLS_LOG_CONVOY_INSPECT, (u32)count, 0);
    DebugToolsTools_ShowStatusLine(buf);

    DebugToolsConvoy_BuildMenuItems();
    DebugTools_QueueSubmenuTransition(menu, &gDebugToolsConvoyMenuDef);

    return MENU_ACT_SKIPCURSOR | MENU_ACT_END | MENU_ACT_SND6A | MENU_ACT_CLEAR;
}

CONST_DATA static struct DebugToolsAction sConvoyInspectAction = {
    6, "Convoy Inspect", DebugToolsActions_ConvoyInspectSelected
};

/* --- 7. Flag/chapter/turn/faction state action --------------------------- */

static void DebugToolsFlag_OnEnd(struct MenuProc* menu)
{
    DebugTools_ReturnToHubAfterMenuEnd(menu);
}

CONST_DATA struct MenuDef gDebugToolsFlagMenuDef = {
    {1, 1, DEBUGTOOLS_MENU_WIDTH_TILES, 0},
    0,
    sFlagMenuItemDefs,
    DEBUGTOOLS_FLAG_MENU_ON_INIT,
    DebugToolsFlag_OnEnd,
    0,
    DebugTools_CancelMenu,
    0,
    0
};

static u8 DebugToolsFlag_ConfirmSelected(struct MenuProc* menu, struct MenuItemProc* item)
{
    int inRange;

    (void)item;

    /* Defense in depth: SetFlag/ClearFlag (src/eventinfo.c) do not
     * themselves bounds-check a chapter-scoped index against
     * GetChapterFlagBitsSize()*8 -- this re-validation is what makes the
     * fixed DEBUGTOOLS_DEBUG_EVENT_FLAG_ID constant an explicitly
     * range-validated target rather than a trusted-by-convention one. */
    inRange = (DEBUGTOOLS_DEBUG_EVENT_FLAG_ID >= 0)
        && (DEBUGTOOLS_DEBUG_EVENT_FLAG_ID < GetChapterFlagBitsSize() * 8);

    DEBUGTOOLS_ASSERT(inRange, DEBUGTOOLS_ASSERT_FLAG_ID_OUT_OF_RANGE);

    if (inRange)
    {
        if (CheckFlag(DEBUGTOOLS_DEBUG_EVENT_FLAG_ID))
            ClearFlag(DEBUGTOOLS_DEBUG_EVENT_FLAG_ID);
        else
            SetFlag(DEBUGTOOLS_DEBUG_EVENT_FLAG_ID);

        gDebugToolsProbe.debugFlagToggleCount++;
        gDebugToolsProbe.debugFlagLastValue = (u32)CheckFlag(DEBUGTOOLS_DEBUG_EVENT_FLAG_ID);

        DebugTools_LogEvent(DEBUGTOOLS_LOG_FLAG_TOGGLE_APPLIED,
            gDebugToolsProbe.debugFlagLastValue, 0);
    }

    return MENU_ACT_SKIPCURSOR | MENU_ACT_END | MENU_ACT_SND6A | MENU_ACT_CLEAR;
}

static void DebugToolsFlag_BuildMenuItems(void)
{
    memset(sFlagMenuItemDefs, 0, sizeof(sFlagMenuItemDefs));

    sFlagMenuItemDefs[0].name = "Confirm Toggle Flag";
    sFlagMenuItemDefs[0].overrideId = DEBUGTOOLS_FLAG_OVERRIDE_ID;
    sFlagMenuItemDefs[0].isAvailable = MenuAlwaysEnabled;
    sFlagMenuItemDefs[0].onSelected = DebugToolsFlag_ConfirmSelected;
    DEBUGTOOLS_LOCALIZE_ITEM(
        &sFlagMenuItemDefs[0],
        EXP_MSG_DEBUG_CONFIRM_TOGGLE_FLAG);

    sFlagMenuItemDefs[1].name = "Apply Turn +1";
    sFlagMenuItemDefs[1].overrideId = DEBUGTOOLS_TURN_OVERRIDE_ID;
    sFlagMenuItemDefs[1].isAvailable = MenuAlwaysEnabled;
    sFlagMenuItemDefs[1].onSelected = DebugToolsPhaseControl_ConfirmTurnIncrement;
    DEBUGTOOLS_LOCALIZE_ITEM(
        &sFlagMenuItemDefs[1],
        EXP_MSG_DEBUG_CONFIRM_TURN_INCREMENT);

    sFlagMenuItemDefs[2].name = "Apply Turn -1";
    sFlagMenuItemDefs[2].overrideId = DEBUGTOOLS_TURN_DECREMENT_OVERRIDE_ID;
    sFlagMenuItemDefs[2].isAvailable = MenuAlwaysEnabled;
    sFlagMenuItemDefs[2].onSelected = DebugToolsPhaseControl_ConfirmTurnDecrement;
    DEBUGTOOLS_LOCALIZE_ITEM(
        &sFlagMenuItemDefs[2],
        EXP_MSG_DEBUG_CONFIRM_TURN_DECREMENT);

    sFlagMenuItemDefs[3].name = "Apply R CPU";
    sFlagMenuItemDefs[3].overrideId = DEBUGTOOLS_RED_COMPUTER_OVERRIDE_ID;
    sFlagMenuItemDefs[3].isAvailable = MenuAlwaysEnabled;
    sFlagMenuItemDefs[3].onSelected = DebugToolsPhaseControl_ConfirmRedComputer;
    DEBUGTOOLS_LOCALIZE_ITEM(
        &sFlagMenuItemDefs[3],
        EXP_MSG_DEBUG_CONFIRM_RED_COMPUTER);

    sFlagMenuItemDefs[4].name = "Apply R Block";
    sFlagMenuItemDefs[4].overrideId = DEBUGTOOLS_RED_BLOCKED_OVERRIDE_ID;
    sFlagMenuItemDefs[4].isAvailable = MenuAlwaysEnabled;
    sFlagMenuItemDefs[4].onSelected = DebugToolsPhaseControl_ConfirmRedBlocked;
    DEBUGTOOLS_LOCALIZE_ITEM(
        &sFlagMenuItemDefs[4],
        EXP_MSG_DEBUG_CONFIRM_RED_BLOCKED);

    sFlagMenuItemDefs[5].name = "Apply G CPU";
    sFlagMenuItemDefs[5].overrideId = DEBUGTOOLS_GREEN_COMPUTER_OVERRIDE_ID;
    sFlagMenuItemDefs[5].isAvailable = MenuAlwaysEnabled;
    sFlagMenuItemDefs[5].onSelected = DebugToolsPhaseControl_ConfirmGreenComputer;
    DEBUGTOOLS_LOCALIZE_ITEM(
        &sFlagMenuItemDefs[5],
        EXP_MSG_DEBUG_CONFIRM_GREEN_COMPUTER);

    sFlagMenuItemDefs[6].name = "Apply G Block";
    sFlagMenuItemDefs[6].overrideId = DEBUGTOOLS_GREEN_BLOCKED_OVERRIDE_ID;
    sFlagMenuItemDefs[6].isAvailable = MenuAlwaysEnabled;
    sFlagMenuItemDefs[6].onSelected = DebugToolsPhaseControl_ConfirmGreenBlocked;
    DEBUGTOOLS_LOCALIZE_ITEM(
        &sFlagMenuItemDefs[6],
        EXP_MSG_DEBUG_CONFIRM_GREEN_BLOCKED);

    sFlagMenuItemDefs[7].name = "Back";
    sFlagMenuItemDefs[7].isAvailable = MenuAlwaysEnabled;
    sFlagMenuItemDefs[7].onSelected = MenuCancelSelect;
    DEBUGTOOLS_LOCALIZE_ITEM(
        &sFlagMenuItemDefs[7],
        EXP_MSG_FRAMEWORK_BACK);

}

static u8 DebugToolsActions_FlagInspectSelected(struct MenuProc* menu, struct MenuItemProc* item)
{
    char values[48];
    char redMode[32];
    char greenMode[32];

    (void)item;

    gDebugToolsProbe.chapterIndexSample = (u32)(u8)gPlaySt.chapterIndex;
    gDebugToolsProbe.debugFlagLastValue = (u32)CheckFlag(DEBUGTOOLS_DEBUG_EVENT_FLAG_ID);
    DebugToolsTools_FormatFlagStatus(values, redMode, greenMode);

    DebugTools_LogEvent(DEBUGTOOLS_LOG_FLAG_INSPECT,
        gDebugToolsProbe.chapterIndexSample, gDebugToolsProbe.debugFlagLastValue);
    DebugToolsTools_DrawFlagStatus(values, redMode, greenMode);

    DebugTools_QueueSubmenuTransitionWithBuilder(
        menu,
        &gDebugToolsFlagMenuDef,
        DebugToolsFlag_BuildMenuItems);

    return MENU_ACT_SKIPCURSOR | MENU_ACT_END | MENU_ACT_SND6A | MENU_ACT_CLEAR;
}

CONST_DATA static struct DebugToolsAction sFlagInspectAction = {
    7, "Flag/Chapter", DebugToolsActions_FlagInspectSelected
};

/* --- 8. RNG inspection/control ------------------------------------------ */

static void DebugToolsRng_OnEnd(struct MenuProc* menu)
{
    DebugTools_ReturnToHubAfterMenuEnd(menu);
}

CONST_DATA struct MenuDef gDebugToolsRngMenuDef = {
    {1, 1, DEBUGTOOLS_MENU_WIDTH_TILES, 0},
    0,
    sRngMenuItemDefs,
    DebugToolsTools_MenuOnInit,
    DebugToolsRng_OnEnd,
    0,
    DebugTools_CancelMenu,
    0,
    0
};

static u8 DebugToolsRng_ConfirmSelected(struct MenuProc* menu, struct MenuItemProc* item)
{
    (void)menu;
    (void)item;

    /* Same reseed idiom src/gamecontrol.c's Chapter 2/4 launchers already
     * use (SetLCGRNValue then InitRN(AdvanceGetLCGRNValue())) -- a
     * bounded, fully-deterministic RNG control action, never a raw seed
     * write outside of rng.c's own public API. */
    SetLCGRNValue((s32)DEBUGTOOLS_TOOLS_RNG_SEED);
    InitRN((s32)AdvanceGetLCGRNValue());

    gDebugToolsProbe.rngReseedTransactionCount++;
    DebugTools_LogEvent(DEBUGTOOLS_LOG_RNG_RESEED_APPLIED, DEBUGTOOLS_TOOLS_RNG_SEED, 0);

    return MENU_ACT_SKIPCURSOR | MENU_ACT_END | MENU_ACT_SND6A | MENU_ACT_CLEAR;
}

static void DebugToolsRng_BuildMenuItems(void)
{
    memset(sRngMenuItemDefs, 0, sizeof(sRngMenuItemDefs));

    sRngMenuItemDefs[0].name = "Confirm Reseed";
    sRngMenuItemDefs[0].overrideId = DEBUGTOOLS_RNG_OVERRIDE_ID;
    sRngMenuItemDefs[0].isAvailable = MenuAlwaysEnabled;
    sRngMenuItemDefs[0].onSelected = DebugToolsRng_ConfirmSelected;
    DEBUGTOOLS_LOCALIZE_ITEM(
        &sRngMenuItemDefs[0],
        EXP_MSG_DEBUG_CONFIRM_RESEED);

    sRngMenuItemDefs[1].name = "Back";
    sRngMenuItemDefs[1].isAvailable = MenuAlwaysEnabled;
    sRngMenuItemDefs[1].onSelected = DebugTools_CancelMenu;
    DEBUGTOOLS_LOCALIZE_ITEM(
        &sRngMenuItemDefs[1],
        EXP_MSG_FRAMEWORK_BACK);

}

static u8 DebugToolsActions_RngInspectSelected(struct MenuProc* menu, struct MenuItemProc* item)
{
    u16 seeds[3];
    char buf[64];

    (void)item;

    StoreRNState(seeds);
    gDebugToolsProbe.rngInspectSeedSample0 = (u32)seeds[0];
    sprintf(buf, "%s %04X",
        DEBUGTOOLS_LOCALIZED_TEXT(EXP_MSG_DEBUG_STATUS_RNG_SEED, "RNG SEED"),
        (unsigned int)seeds[0]);

    DebugTools_LogEvent(DEBUGTOOLS_LOG_RNG_INSPECT, (u32)seeds[0], 0);
    DebugToolsTools_ShowStatusLine(buf);

    DebugToolsRng_BuildMenuItems();
    DebugTools_QueueSubmenuTransition(menu, &gDebugToolsRngMenuDef);

    return MENU_ACT_SKIPCURSOR | MENU_ACT_END | MENU_ACT_SND6A | MENU_ACT_CLEAR;
}

CONST_DATA static struct DebugToolsAction sRngInspectAction = {
    8, "RNG Inspect", DebugToolsActions_RngInspectSelected
};

/* --- 9. Save compatibility/state inspection (read-only) ------------------ */

EWRAM_DATA static struct MenuItemDef sSaveStateMenuItemDefs[2] = {{0}};
extern struct MenuDef CONST_DATA gDebugToolsSaveStateMenuDef;
static u16 sSaveStateFrameTile;
static struct MenuRect sSaveStateFrameRect;
static u8 sSaveStateFrameBg;
static int sSaveStateBackPending;

static void DebugToolsSaveState_CaptureFrame(struct MenuProc* menu)
{
    sSaveStateFrameBg = menu->backBg;
    sSaveStateFrameRect = menu->rect;
    sSaveStateFrameTile = BG_GetMapBuffer(sSaveStateFrameBg)[
        TILEMAP_INDEX(sSaveStateFrameRect.x + 1, sSaveStateFrameRect.y)];
}

static void DebugToolsSaveState_OnEnd(struct MenuProc* menu)
{
    DebugToolsSaveState_CaptureFrame(menu);
    sSaveStateBackPending = 1;
    DebugTools_ReturnToHubAfterMenuEnd(menu);
}

void DebugToolsSaveState_OnHubReturn(void)
{
    u16 currentTile;

    if (!sSaveStateBackPending)
        return;

    currentTile = BG_GetMapBuffer(sSaveStateFrameBg)[
        TILEMAP_INDEX(sSaveStateFrameRect.x + 1, sSaveStateFrameRect.y)];
    /* The owner-backed menu surface can legitimately contain an empty tile. */
    gDebugToolsProbe.saveCompatBackMenuPreserved =
        sSaveStateFrameTile == currentTile;
    gDebugToolsProbe.saveCompatBackReturnCount++;
    sSaveStateBackPending = 0;
}

CONST_DATA struct MenuDef gDebugToolsSaveStateMenuDef = {
    {1, 1, DEBUGTOOLS_MENU_WIDTH_TILES, 0},
    0,
    sSaveStateMenuItemDefs,
    DebugToolsTools_MenuOnInit,
    DebugToolsSaveState_OnEnd,
    0,
    DebugTools_CancelMenu,
    0,
    0
};

static void DebugToolsSaveState_BuildMenuItems(void)
{
    memset(sSaveStateMenuItemDefs, 0, sizeof(sSaveStateMenuItemDefs));

    sSaveStateMenuItemDefs[0].name = "Back";
    sSaveStateMenuItemDefs[0].isAvailable = MenuAlwaysEnabled;
    sSaveStateMenuItemDefs[0].onSelected = DebugTools_CancelMenu;
    DEBUGTOOLS_LOCALIZE_ITEM(
        &sSaveStateMenuItemDefs[0],
        EXP_MSG_FRAMEWORK_BACK);

}

static u8 DebugToolsActions_SaveStateInspectSelected(struct MenuProc* menu, struct MenuItemProc* item)
{
    enum SaveCompatState state;
    char buf[64];

    (void)item;

    /* Read-only: ClassifySramSaveCompat (src/bmsave-lib.c) only inspects
     * the global save header/expansion metadata record and never mutates
     * SRAM or any save-block struct -- see include/save_format.h. This
     * tool never calls BuildCurrentExpansionSaveMeta with a live SRAM
     * target, InitGlobalSaveInfodata, or any writer. */
    state = ClassifySramSaveCompat();
    gDebugToolsProbe.saveCompatLastState = (u32)state;
    gDebugToolsProbe.saveCompatInspectCount++;
    sprintf(buf, "%s %d",
        DEBUGTOOLS_LOCALIZED_TEXT(EXP_MSG_DEBUG_STATUS_SAVE_STATE, "SAVE STATE"),
        (int)state);

    DebugTools_LogEvent(DEBUGTOOLS_LOG_SAVESTATE_INSPECT, (u32)state, 0);
    DebugToolsTools_ShowStatusLine(buf);

    DebugToolsSaveState_BuildMenuItems();
    DebugTools_QueueSubmenuTransition(menu, &gDebugToolsSaveStateMenuDef);

    return MENU_ACT_SKIPCURSOR | MENU_ACT_END | MENU_ACT_SND6A | MENU_ACT_CLEAR;
}

CONST_DATA static struct DebugToolsAction sSaveStateInspectAction = {
    9, "Save State", DebugToolsActions_SaveStateInspectSelected
};

/* --- Registration -------------------------------------------------------- */

void DebugTools_RegisterExtendedToolActions(void)
{
    /* Internal built-in registration keeps ids 5-9 unavailable to the
     * contributor API and marks these rows as localized built-ins.
     * Exact repeats are successful no-ops. */
    DebugTools_RegisterBuiltinAction(&sUnitInspectAction);
    DebugTools_RegisterBuiltinAction(&sConvoyInspectAction);
    DebugTools_RegisterBuiltinAction(&sFlagInspectAction);
    DebugTools_RegisterBuiltinAction(&sRngInspectAction);
    DebugTools_RegisterBuiltinAction(&sSaveStateInspectAction);
}

#else /* !FE8_EXPANSION_DEBUGTOOLS_ENABLED */

void DebugTools_RegisterExtendedToolActions(void)
{
    /* No-op: nothing to register in a release build. */
}

#endif /* FE8_EXPANSION_DEBUGTOOLS_ENABLED */

#endif /* FE8_ARCHIVAL_BUILD */
