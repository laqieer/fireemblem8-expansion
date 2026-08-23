#include "global.h"

#ifndef FE8_ARCHIVAL_BUILD
#include <string.h>
#include <stdio.h>

#include "proc.h"
#include "uimenu.h"
#include "fontgrp.h"
#include "hardware.h"
#include "bmunit.h"
#include "bmcontainer.h"
#include "eventinfo.h"
#include "rng.h"
#include "bmsave.h"
#include "save_format.h"
#include "expansion_debug_save_fixture.h"
#include "debug_save_fixture_internal.h"
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
 *   9. Save compatibility/state inspection and volatile fixture continue
 *
 * Each is a single registry action, following the exact same
 * StartOrphanMenu submenu idiom src/debugtools_actions.c's Weather/Fog
 * actions already use. Every mutating tool (5-8) opens a bounded
 * two-item "Confirm <action>" / "Back" submenu -- a mutation only ever
 * happens after that explicit, separate confirmation input, never on the
 * initial hub selection alone. Tool 9 is read-only (no Confirm item at
 * all unless the title-only volatile fixture flow is selected). See
 * include/expansion_debugtools.h's "Five
 * bounded validated tools" block comment, docs/debugtools.md, and
 * reports/debugtools_issue11_closure.md.
 *
 * No tool ever performs a raw/arbitrary address write, nor accepts an
 * unvalidated numeric index from outside this file: every target is
 * either a fixed in-range constant (DEBUGTOOLS_UNIT_TARGET_CHARACTER,
 * DEBUGTOOLS_CONVOY_TEST_ITEM, DEBUGTOOLS_DEBUG_EVENT_FLAG_ID,
 * DEBUGTOOLS_TOOLS_RNG_SEED below) or produced by an existing engine
 * lookup helper (GetUnitFromCharId) that itself returns NULL on failure
 * -- callers below always re-check via UNIT_IS_VALID/DEBUGTOOLS_ASSERT
 * immediately before any mutation. This file never edits
 * src/bmdebug.c, src/menu_def.c, or src/uidebug.c, and never touches
 * SRAM/any save-block struct directly. The Save State action delegates its
 * isolated fixture image/state machine to expansion_debug_save_fixture.h.
 */

#if FE8_EXPANSION_DEBUGTOOLS_ENABLED

#if defined(FE8_PORTRAIT_PACKAGE_RUNTIME_TEST)
EWRAM_DATA struct PortraitPackageRuntimeProbe gPortraitPackageRuntimeProbe = {0};
#endif

enum
{
    /* Distinct from Weather (0xE0) and Fog (0xE1), src/debugtools_actions.c,
     * and from every static src/menu_def.c overrideId -- see that file's
     * own comment on DEBUGTOOLS_WEATHER_OVERRIDE_ID for why this must
     * never collide. */
    DEBUGTOOLS_UNIT_OVERRIDE_ID = 0xE2,
    DEBUGTOOLS_CONVOY_OVERRIDE_ID = 0xE3,
    DEBUGTOOLS_FLAG_OVERRIDE_ID = 0xE4,
    DEBUGTOOLS_RNG_OVERRIDE_ID = 0xE5
};

/* Fixed, documented, always-in-range targets -- never a contributor- or
 * player-supplied numeric index. */
#define DEBUGTOOLS_UNIT_TARGET_CHARACTER CHARACTER_EIRIKA
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

static void DebugToolsTools_UnitMenuOnInit(struct MenuProc* menu)
{
    char buf[64];

    (void)menu;
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

    (void)menu;
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
    char buf[64];
    char chapterLabel[24];

    (void)menu;
    if (!DebugToolsTools_UsesCjkText())
        return;
    strcpy(
        chapterLabel,
        ExpansionLocale_ResolveCurrent(EXP_MSG_DEBUG_STATUS_CHAPTER));
    sprintf(buf, "%s %d %s %d",
        chapterLabel,
        (int)gDebugToolsProbe.chapterIndexSample,
        ExpansionLocale_ResolveCurrent(EXP_MSG_DEBUG_STATUS_FLAG),
        (int)gDebugToolsProbe.debugFlagLastValue);
    DebugToolsTools_DrawCjkStatusLine(buf);
}

static void DebugToolsTools_RngMenuOnInit(struct MenuProc* menu)
{
    char buf[64];

    (void)menu;
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

    (void)menu;
    if (!DebugToolsTools_UsesCjkText())
        return;

    if (DebugSaveFixture_IsPersistenceBlocked())
        sprintf(buf, "%s",
            ExpansionLocale_ResolveCurrent(
                EXP_MSG_DEBUG_SAVE_FIXTURE_BLOCKED));
    else if (!DebugSaveFixture_CanPrepare())
        sprintf(buf, "%s",
            ExpansionLocale_ResolveCurrent(
                EXP_MSG_DEBUG_SAVE_FIXTURE_TITLE_ONLY));
    else
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
#endif

static void DebugToolsTools_ShowStatusLine(const char* text)
{
#ifdef MODERN
    if (DebugToolsTools_UsesCjkText())
        return;
#endif

    SetupDebugFontForBG(2, 0);
    PrintDebugStringToBG(BG_GetMapBuffer(2) + TILEMAP_INDEX(1, 1), text);
    gLCDControlBuffer.dispcnt.bg2_on = 1;
}

/* Every submenu transition ends the current menu first, so all five tools and
 * the Save State flow can share one six-item persistent buffer. */
EWRAM_DATA static struct MenuItemDef sDebugToolsMenuItemDefs[6] = {{0}};

#define sUnitMenuItemDefs sDebugToolsMenuItemDefs
#define sConvoyMenuItemDefs sDebugToolsMenuItemDefs
#define sFlagMenuItemDefs sDebugToolsMenuItemDefs
#define sRngMenuItemDefs sDebugToolsMenuItemDefs
#define sSaveFixtureMenuItemDefs sDebugToolsMenuItemDefs

/* Retain the pre-#128 72-byte Save State capacity while reusing it for the
 * fixture preview and title-only menu controls. */
EWRAM_DATA union DebugSaveFixtureStableStorage sSaveStateStableLayout
    __attribute__((used)) = {0};

#define sSaveFixtureOverrides sSaveStateStableLayout.fixture.preview.overrides
#define sSaveFixtureSourceKind sSaveStateStableLayout.fixture.preview.target.sourceKind
#define sSaveFixtureSourceGameSlot sSaveStateStableLayout.fixture.preview.target.sourceGameSlot
#define sSaveFixtureOpeningPreview sSaveStateStableLayout.fixture.openingPreview
#define sSaveFixtureOpeningFinal sSaveStateStableLayout.fixture.openingFinal
#define sSaveFixtureHandoff sSaveStateStableLayout.fixture.handoff

/* --- 5. Unit inspection/edit -------------------------------------------- */

static void DebugToolsUnit_OnEnd(struct MenuProc* menu)
{
    DebugTools_ReturnToHubAfterMenuEnd(menu);
}

CONST_DATA struct MenuDef gDebugToolsUnitMenuDef = {
    {1, 1, DEBUGTOOLS_MENU_WIDTH_TILES, 0},
    0,
    sUnitMenuItemDefs,
    DEBUGTOOLS_UNIT_MENU_ON_INIT,
    DebugToolsUnit_OnEnd,
    0,
    MenuCancelSelect,
    0,
    0
};

static u8 DebugToolsUnit_ConfirmSelected(struct MenuProc* menu, struct MenuItemProc* item)
{
    struct Unit* unit;

    (void)item;

    unit = GetUnitFromCharId(DEBUGTOOLS_UNIT_TARGET_CHARACTER);

    DEBUGTOOLS_ASSERT(UNIT_IS_VALID(unit), DEBUGTOOLS_ASSERT_UNIT_TARGET_INVALID);

    if (UNIT_IS_VALID(unit))
    {
        SetUnitHp(unit, GetUnitMaxHp(unit));
        SetUnitStatus(unit, 0);

        gDebugToolsProbe.unitHealTransactionCount++;
        DebugTools_LogEvent(DEBUGTOOLS_LOG_UNIT_HEAL_APPLIED,
            (u32)GetUnitCurrentHp(unit), (u32)GetUnitMaxHp(unit));
    }
    else
    {
        DebugTools_LogEvent(DEBUGTOOLS_LOG_UNIT_HEAL_SKIPPED_INVALID, 0, 0);
    }

    return MENU_ACT_SKIPCURSOR | MENU_ACT_END | MENU_ACT_SND6A | MENU_ACT_CLEAR;
}

static void DebugToolsUnit_BuildMenuItems(void)
{
    memset(sUnitMenuItemDefs, 0, sizeof(sUnitMenuItemDefs));

    sUnitMenuItemDefs[0].name = "Confirm Heal to Full";
    sUnitMenuItemDefs[0].overrideId = DEBUGTOOLS_UNIT_OVERRIDE_ID;
    sUnitMenuItemDefs[0].isAvailable = MenuAlwaysEnabled;
    sUnitMenuItemDefs[0].onSelected = DebugToolsUnit_ConfirmSelected;
    DEBUGTOOLS_LOCALIZE_ITEM(
        &sUnitMenuItemDefs[0],
        EXP_MSG_DEBUG_CONFIRM_HEAL_FULL);

    sUnitMenuItemDefs[1].name = "Back";
    sUnitMenuItemDefs[1].isAvailable = MenuAlwaysEnabled;
    sUnitMenuItemDefs[1].onSelected = MenuCancelSelect;
    DEBUGTOOLS_LOCALIZE_ITEM(
        &sUnitMenuItemDefs[1],
        EXP_MSG_FRAMEWORK_BACK);

    /* sUnitMenuItemDefs[2] stays all-zero: the terminator. */
}

static u8 DebugToolsActions_UnitInspectSelected(struct MenuProc* menu, struct MenuItemProc* item)
{
    struct Unit* unit;
#if defined(FE8_PORTRAIT_PACKAGE_RUNTIME_TEST)
    struct FaceProc* face;
    struct FaceBlinkProc* mouth;
    u16* mouthTiles;
#endif
    char buf[64];

    (void)item;

    unit = GetUnitFromCharId(DEBUGTOOLS_UNIT_TARGET_CHARACTER);

    if (UNIT_IS_VALID(unit))
    {
#if defined(FE8_PORTRAIT_PACKAGE_RUNTIME_TEST)
        PutFaceChibi(
            DEBUGTOOLS_PORTRAIT_PROBE_FACE_ID,
            TILEMAP_LOCATED(BG_GetMapBuffer(2), 1, 4),
            DEBUGTOOLS_PORTRAIT_PROBE_CHR,
            DEBUGTOOLS_PORTRAIT_PROBE_PAL,
            FALSE);
        BG_EnableSyncByMask(BG2_SYNC_BIT);
#endif

        gDebugToolsProbe.unitInspectTargetFound = 1;
        gDebugToolsProbe.unitInspectLastCurHp = (u32)GetUnitCurrentHp(unit);
        gDebugToolsProbe.unitInspectLastMaxHp = (u32)GetUnitMaxHp(unit);

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
            GetUnitCurrentHp(unit), GetUnitMaxHp(unit));
    }
    else
    {
        gDebugToolsProbe.unitInspectTargetFound = 0;
        gDebugToolsProbe.unitInspectLastCurHp = 0;
        gDebugToolsProbe.unitInspectLastMaxHp = 0;
        sprintf(buf, "%s", DEBUGTOOLS_LOCALIZED_TEXT(
            EXP_MSG_DEBUG_STATUS_UNIT_UNAVAILABLE, "UNIT N/A"));
    }

    DebugTools_LogEvent(DEBUGTOOLS_LOG_UNIT_INSPECT,
        gDebugToolsProbe.unitInspectLastCurHp, gDebugToolsProbe.unitInspectLastMaxHp);
    DebugToolsTools_ShowStatusLine(buf);

    DebugToolsUnit_BuildMenuItems();
    DebugTools_QueueSubmenuTransition(menu, &gDebugToolsUnitMenuDef);

    return MENU_ACT_SKIPCURSOR | MENU_ACT_END | MENU_ACT_SND6A | MENU_ACT_CLEAR;
}

CONST_DATA static struct DebugToolsAction sUnitInspectAction = {
    5, "Unit Inspect", DebugToolsActions_UnitInspectSelected
};

/* --- 6. Convoy inspection/edit ------------------------------------------ */

static void DebugToolsConvoy_OnEnd(struct MenuProc* menu)
{
    DebugTools_ReturnToHubAfterMenuEnd(menu);
}

CONST_DATA struct MenuDef gDebugToolsConvoyMenuDef = {
    {1, 1, DEBUGTOOLS_MENU_WIDTH_TILES, 0},
    0,
    sConvoyMenuItemDefs,
    DEBUGTOOLS_CONVOY_MENU_ON_INIT,
    DebugToolsConvoy_OnEnd,
    0,
    MenuCancelSelect,
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
    sConvoyMenuItemDefs[1].onSelected = MenuCancelSelect;
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

/* --- 7. Flag/chapter/event state action ---------------------------------- */

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
    MenuCancelSelect,
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

    sFlagMenuItemDefs[1].name = "Back";
    sFlagMenuItemDefs[1].isAvailable = MenuAlwaysEnabled;
    sFlagMenuItemDefs[1].onSelected = MenuCancelSelect;
    DEBUGTOOLS_LOCALIZE_ITEM(
        &sFlagMenuItemDefs[1],
        EXP_MSG_FRAMEWORK_BACK);
}

static u8 DebugToolsActions_FlagInspectSelected(struct MenuProc* menu, struct MenuItemProc* item)
{
    char buf[64];
#ifdef MODERN
    char chapterLabel[24];
#endif

    (void)item;

    gDebugToolsProbe.chapterIndexSample = (u32)(u8)gPlaySt.chapterIndex;
    gDebugToolsProbe.debugFlagLastValue = (u32)CheckFlag(DEBUGTOOLS_DEBUG_EVENT_FLAG_ID);
#ifdef MODERN
    strcpy(
        chapterLabel,
        ExpansionLocale_ResolveCurrent(EXP_MSG_DEBUG_STATUS_CHAPTER));
    sprintf(buf, "%s %d %s %d",
        chapterLabel,
        (int)(u8)gPlaySt.chapterIndex,
        ExpansionLocale_ResolveCurrent(EXP_MSG_DEBUG_STATUS_FLAG),
        (int)gDebugToolsProbe.debugFlagLastValue);
#else
    sprintf(buf, "CH %d FLAG %d", (int)(u8)gPlaySt.chapterIndex,
        (int)gDebugToolsProbe.debugFlagLastValue);
#endif

    DebugTools_LogEvent(DEBUGTOOLS_LOG_FLAG_INSPECT,
        gDebugToolsProbe.chapterIndexSample, gDebugToolsProbe.debugFlagLastValue);
    DebugToolsTools_ShowStatusLine(buf);

    DebugToolsFlag_BuildMenuItems();
    DebugTools_QueueSubmenuTransition(menu, &gDebugToolsFlagMenuDef);

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
    DEBUGTOOLS_RNG_MENU_ON_INIT,
    DebugToolsRng_OnEnd,
    0,
    MenuCancelSelect,
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
    sRngMenuItemDefs[1].onSelected = MenuCancelSelect;
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

/* --- 9. Save compatibility/state + volatile fixture --------------------- */

enum
{
    DEBUGTOOLS_SAVE_GAME0_OVERRIDE_ID = 0xE6,
    DEBUGTOOLS_SAVE_GAME1_OVERRIDE_ID = 0xE7,
    DEBUGTOOLS_SAVE_GAME2_OVERRIDE_ID = 0xE8,
    DEBUGTOOLS_SAVE_SUSPEND_OVERRIDE_ID = 0xE9,
    DEBUGTOOLS_SAVE_COMPLETION_OVERRIDE_ID = 0xEA,
    DEBUGTOOLS_SAVE_TACTICIAN_OVERRIDE_ID = 0xEB,
    DEBUGTOOLS_SAVE_ARM_OVERRIDE_ID = 0xEC,
    DEBUGTOOLS_SAVE_CONTINUE_OVERRIDE_ID = 0xED
};

static void DebugToolsSaveFixtureSource_BuildMenuItems(void);
static void DebugToolsSaveFixturePreview_BuildMenuItems(void);
static void DebugToolsSaveFixtureFinal_BuildMenuItems(void);
static void DebugToolsSaveState_BuildReadOnlyMenuItems(void);

static void DebugToolsSaveFixture_ShowStatus(void)
{
    const struct DebugSaveFixturePreview* preview =
        DebugSaveFixture_GetPreview();
    char buf[64];

    if (preview == NULL)
    {
        enum DebugSaveFixtureResult result =
            DebugSaveFixture_GetLastResult();
        const char* label = result
            == DEBUG_SAVE_FIXTURE_ERR_PERSISTENCE_BLOCKED
                ? DEBUGTOOLS_LOCALIZED_TEXT(
                    EXP_MSG_DEBUG_SAVE_FIXTURE_BLOCKED,
                    "Save Blocked")
                : DEBUGTOOLS_LOCALIZED_TEXT(
                    EXP_MSG_DEBUG_SAVE_FIXTURE_INVALID,
                    "Invalid");

        sprintf(buf, "%s %d", label, (int)result);
    }
    else
    {
        if (DebugSaveFixture_GetPhase() == DEBUG_SAVE_FIXTURE_ARMED)
        {
            sprintf(buf, "%s H%04X V%d/E%d",
                DEBUGTOOLS_LOCALIZED_TEXT(
                    EXP_MSG_DEBUG_SAVE_FIXTURE_PREVIEW,
                    "RAM Fixture"),
                (unsigned int)(preview->target.sourceHashLo & 0xFFFF),
                (int)preview->provenance.formatVersion,
                (int)preview->provenance.compatEpoch);
        }
        else
        {
            sprintf(buf, "%s %c%d/G%d #%02X",
                DEBUGTOOLS_LOCALIZED_TEXT(
                    EXP_MSG_DEBUG_SAVE_FIXTURE_PREVIEW,
                    "RAM Fixture"),
                preview->target.sourceKind == DEBUG_SAVE_FIXTURE_SOURCE_GAME
                    ? 'G'
                    : 'S',
                preview->target.sourceKind == DEBUG_SAVE_FIXTURE_SOURCE_GAME
                    ? (int)preview->target.sourceGameSlot
                    : (int)preview->target.resolvedSuspendSlot,
                (int)preview->target.backingGameSlot,
                (unsigned int)(preview->target.generation & 0xFF));
        }
    }

#ifdef MODERN
    if (DebugToolsTools_UsesCjkText())
    {
        DebugToolsTools_DrawCjkStatusLine(buf);
        return;
    }
#endif

    DebugToolsTools_ShowStatusLine(buf);
}

static void DebugToolsSaveFixture_MenuOnInit(struct MenuProc* menu)
{
    (void)menu;
    DebugToolsSaveFixture_ShowStatus();
}

static enum DebugSaveFixtureResult DebugToolsSaveFixture_PrepareSelected(void)
{
    struct DebugSaveFixturePreview preview;

    if (sSaveFixtureSourceKind == DEBUG_SAVE_FIXTURE_SOURCE_GAME)
        return DebugSaveFixture_PrepareGame(
            (enum DebugSaveFixtureGameSlot)sSaveFixtureSourceGameSlot,
            &sSaveFixtureOverrides,
            &preview);

    return DebugSaveFixture_PrepareLatestSuspend(
        &sSaveFixtureOverrides,
        &preview);
}

static void DebugToolsSaveState_OnEnd(struct MenuProc* menu)
{
    if (sSaveFixtureOpeningPreview)
    {
        sSaveFixtureOpeningPreview = 0;
    }
    else
    {
        DebugSaveFixture_Abort(DEBUG_SAVE_FIXTURE_ABORT_CANCEL);
    }

    DebugTools_ReturnToHubAfterMenuEnd(menu);
}

static void DebugToolsSaveFixturePreview_OnEnd(struct MenuProc* menu)
{
    if (sSaveFixtureOpeningFinal)
    {
        sSaveFixtureOpeningFinal = 0;
    }
    else
    {
        DebugSaveFixture_Abort(DEBUG_SAVE_FIXTURE_ABORT_CANCEL);
    }

    DebugTools_ReturnToHubAfterMenuEnd(menu);
}

static void DebugToolsSaveFixtureFinal_OnEnd(struct MenuProc* menu)
{
    if (sSaveFixtureHandoff)
    {
        sSaveFixtureHandoff = 0;
        DebugTools_EndSessionAfterMenuEnd(menu);
        return;
    }

    DebugSaveFixture_Abort(DEBUG_SAVE_FIXTURE_ABORT_CANCEL);
    DebugTools_ReturnToHubAfterMenuEnd(menu);
}

CONST_DATA struct MenuDef gDebugToolsSaveStateMenuDef = {
    {1, 1, DEBUGTOOLS_MENU_WIDTH_TILES, 0},
    0,
    sSaveFixtureMenuItemDefs,
    DEBUGTOOLS_SAVE_MENU_ON_INIT,
    DebugToolsSaveState_OnEnd,
    0,
    MenuCancelSelect,
    0,
    0
};

CONST_DATA struct MenuDef gDebugToolsSaveFixtureSourceMenuDef = {
    {1, 1, DEBUGTOOLS_MENU_WIDTH_TILES, 0},
    0,
    sSaveFixtureMenuItemDefs,
    DEBUGTOOLS_SAVE_MENU_ON_INIT,
    DebugToolsSaveState_OnEnd,
    0,
    MenuCancelSelect,
    0,
    0
};

CONST_DATA struct MenuDef gDebugToolsSaveFixturePreviewMenuDef = {
    {1, 1, DEBUGTOOLS_MENU_WIDTH_TILES, 0},
    0,
    sSaveFixtureMenuItemDefs,
    DebugToolsSaveFixture_MenuOnInit,
    DebugToolsSaveFixturePreview_OnEnd,
    0,
    MenuCancelSelect,
    0,
    0
};

CONST_DATA struct MenuDef gDebugToolsSaveFixtureFinalMenuDef = {
    {1, 1, DEBUGTOOLS_MENU_WIDTH_TILES, 0},
    0,
    sSaveFixtureMenuItemDefs,
    DebugToolsSaveFixture_MenuOnInit,
    DebugToolsSaveFixtureFinal_OnEnd,
    0,
    MenuCancelSelect,
    0,
    0
};

static u8 DebugToolsSaveFixture_PrepareSource(
    struct MenuProc* menu,
    enum DebugSaveFixtureSourceKind kind,
    int gameSlot)
{
    enum DebugSaveFixtureResult result;

    sSaveFixtureSourceKind = kind;
    sSaveFixtureSourceGameSlot = gameSlot;
    result = DebugToolsSaveFixture_PrepareSelected();
    DebugToolsSaveFixture_ShowStatus();

    if (result != DEBUG_SAVE_FIXTURE_OK)
        return MENU_ACT_SND6B;

    sSaveFixtureOpeningPreview = 1;
    DebugToolsSaveFixturePreview_BuildMenuItems();
    DebugTools_QueueSubmenuTransition(
        menu,
        &gDebugToolsSaveFixturePreviewMenuDef);
    return MENU_ACT_SKIPCURSOR | MENU_ACT_END | MENU_ACT_SND6A
        | MENU_ACT_CLEAR;
}

static u8 DebugToolsSaveFixture_Game0Selected(
    struct MenuProc* menu,
    struct MenuItemProc* item)
{
    (void)item;
    return DebugToolsSaveFixture_PrepareSource(
        menu,
        DEBUG_SAVE_FIXTURE_SOURCE_GAME,
        DEBUG_SAVE_FIXTURE_GAME0);
}

static u8 DebugToolsSaveFixture_Game1Selected(
    struct MenuProc* menu,
    struct MenuItemProc* item)
{
    (void)item;
    return DebugToolsSaveFixture_PrepareSource(
        menu,
        DEBUG_SAVE_FIXTURE_SOURCE_GAME,
        DEBUG_SAVE_FIXTURE_GAME1);
}

static u8 DebugToolsSaveFixture_Game2Selected(
    struct MenuProc* menu,
    struct MenuItemProc* item)
{
    (void)item;
    return DebugToolsSaveFixture_PrepareSource(
        menu,
        DEBUG_SAVE_FIXTURE_SOURCE_GAME,
        DEBUG_SAVE_FIXTURE_GAME2);
}

static u8 DebugToolsSaveFixture_SuspendSelected(
    struct MenuProc* menu,
    struct MenuItemProc* item)
{
    (void)item;
    return DebugToolsSaveFixture_PrepareSource(
        menu,
        DEBUG_SAVE_FIXTURE_SOURCE_SUSPEND,
        DEBUG_SAVE_FIXTURE_GAME_NONE);
}

static int DebugToolsSaveFixture_CompletionDraw(
    struct MenuProc* menu,
    struct MenuItemProc* item)
{
    char buf[64];

    sprintf(buf, "%s %d",
        DEBUGTOOLS_LOCALIZED_TEXT(
            EXP_MSG_DEBUG_SAVE_FIXTURE_COMPLETION,
            "Clears:"),
        (int)sSaveFixtureOverrides.completionCount);
    Text_DrawString(&item->text, buf);
    PutText(
        &item->text,
        TILEMAP_LOCATED(
            BG_GetMapBuffer(menu->frontBg),
            item->xTile,
            item->yTile));
    return 0;
}

static int DebugToolsSaveFixture_TacticianDraw(
    struct MenuProc* menu,
    struct MenuItemProc* item)
{
    char buf[64];
    const char* value;

    value = sSaveFixtureOverrides.tacticianMode
        == DEBUG_SAVE_FIXTURE_TACTICIAN_KEEP
        ? DEBUGTOOLS_LOCALIZED_TEXT(
            EXP_MSG_DEBUG_SAVE_FIXTURE_KEEP,
            "Keep")
        : DEBUGTOOLS_LOCALIZED_TEXT(
            EXP_MSG_DEBUG_SAVE_FIXTURE_MARKER,
            "Fixture");

    sprintf(buf, "%s %s",
        DEBUGTOOLS_LOCALIZED_TEXT(
            EXP_MSG_DEBUG_SAVE_FIXTURE_TACTICIAN,
            "Name:"),
        value);
    Text_DrawString(&item->text, buf);
    PutText(
        &item->text,
        TILEMAP_LOCATED(
            BG_GetMapBuffer(menu->frontBg),
            item->xTile,
            item->yTile));
    return 0;
}

static u8 DebugToolsSaveFixture_CompletionSelected(
    struct MenuProc* menu,
    struct MenuItemProc* item)
{
    (void)menu;
    (void)item;

    sSaveFixtureOverrides.completionCount++;
    if (sSaveFixtureOverrides.completionCount > MAX_SAVED_GAME_CLEARS)
        sSaveFixtureOverrides.completionCount = 0;

    DebugToolsSaveFixture_PrepareSelected();
    DebugToolsSaveFixture_ShowStatus();
    DebugToolsSaveFixture_CompletionDraw(menu, item);
    return MENU_ACT_SND6A;
}

static u8 DebugToolsSaveFixture_TacticianSelected(
    struct MenuProc* menu,
    struct MenuItemProc* item)
{
    (void)menu;
    (void)item;

    sSaveFixtureOverrides.tacticianMode =
        sSaveFixtureOverrides.tacticianMode
        == DEBUG_SAVE_FIXTURE_TACTICIAN_KEEP
            ? DEBUG_SAVE_FIXTURE_TACTICIAN_FIXED_MARKER
            : DEBUG_SAVE_FIXTURE_TACTICIAN_KEEP;

    DebugToolsSaveFixture_PrepareSelected();
    DebugToolsSaveFixture_ShowStatus();
    DebugToolsSaveFixture_TacticianDraw(menu, item);
    return MENU_ACT_SND6A;
}

static u8 DebugToolsSaveFixture_ArmSelected(
    struct MenuProc* menu,
    struct MenuItemProc* item)
{
    const struct DebugSaveFixturePreview* preview =
        DebugSaveFixture_GetPreview();
    enum DebugSaveFixtureResult result;

    (void)item;

    if (preview == NULL)
        return MENU_ACT_SND6B;

    result = DebugSaveFixture_Arm(&preview->target);
    DebugToolsSaveFixture_ShowStatus();

    if (result != DEBUG_SAVE_FIXTURE_OK)
        return MENU_ACT_SND6B;

    sSaveFixtureOpeningFinal = 1;
    DebugToolsSaveFixtureFinal_BuildMenuItems();
    DebugTools_QueueSubmenuTransition(
        menu,
        &gDebugToolsSaveFixtureFinalMenuDef);
    return MENU_ACT_SKIPCURSOR | MENU_ACT_END | MENU_ACT_SND6A
        | MENU_ACT_CLEAR;
}

static u8 DebugToolsSaveFixture_ContinueSelected(
    struct MenuProc* menu,
    struct MenuItemProc* item)
{
    const struct DebugSaveFixturePreview* preview =
        DebugSaveFixture_GetPreview();
    u16 requiredModifiers = L_BUTTON | R_BUTTON;

    (void)menu;
    (void)item;

    if ((gKeyStatusPtr->heldKeys & requiredModifiers) != requiredModifiers
        || !(gKeyStatusPtr->newKeys & A_BUTTON)
        || preview == NULL)
        return MENU_ACT_SND6B;

    if (DebugSaveFixture_RequestContinue(&preview->target)
        != DEBUG_SAVE_FIXTURE_OK)
    {
        DebugToolsSaveFixture_ShowStatus();
        return MENU_ACT_SND6B;
    }

    sSaveFixtureHandoff = 1;
    return MENU_ACT_SKIPCURSOR | MENU_ACT_END | MENU_ACT_SND6A
        | MENU_ACT_CLEAR;
}

static void DebugToolsSaveFixturePreview_BuildMenuItems(void)
{
    memset(
        sSaveFixtureMenuItemDefs,
        0,
        sizeof(sSaveFixtureMenuItemDefs));

    sSaveFixtureMenuItemDefs[0].name = "Clears:";
    sSaveFixtureMenuItemDefs[0].overrideId =
        DEBUGTOOLS_SAVE_COMPLETION_OVERRIDE_ID;
    sSaveFixtureMenuItemDefs[0].isAvailable = MenuAlwaysEnabled;
    sSaveFixtureMenuItemDefs[0].onDraw =
        DebugToolsSaveFixture_CompletionDraw;
    sSaveFixtureMenuItemDefs[0].onSelected =
        DebugToolsSaveFixture_CompletionSelected;

    sSaveFixtureMenuItemDefs[1].name = "Name:";
    sSaveFixtureMenuItemDefs[1].overrideId =
        DEBUGTOOLS_SAVE_TACTICIAN_OVERRIDE_ID;
    sSaveFixtureMenuItemDefs[1].isAvailable = MenuAlwaysEnabled;
    sSaveFixtureMenuItemDefs[1].onDraw =
        DebugToolsSaveFixture_TacticianDraw;
    sSaveFixtureMenuItemDefs[1].onSelected =
        DebugToolsSaveFixture_TacticianSelected;

    sSaveFixtureMenuItemDefs[2].name = "Arm RAM";
    sSaveFixtureMenuItemDefs[2].overrideId =
        DEBUGTOOLS_SAVE_ARM_OVERRIDE_ID;
    sSaveFixtureMenuItemDefs[2].isAvailable = MenuAlwaysEnabled;
    sSaveFixtureMenuItemDefs[2].onSelected =
        DebugToolsSaveFixture_ArmSelected;
    DEBUGTOOLS_LOCALIZE_ITEM(
        &sSaveFixtureMenuItemDefs[2],
        EXP_MSG_DEBUG_SAVE_FIXTURE_ARM);

    sSaveFixtureMenuItemDefs[3].name = "Back";
    sSaveFixtureMenuItemDefs[3].isAvailable = MenuAlwaysEnabled;
    sSaveFixtureMenuItemDefs[3].onSelected = MenuCancelSelect;
    DEBUGTOOLS_LOCALIZE_ITEM(
        &sSaveFixtureMenuItemDefs[3],
        EXP_MSG_FRAMEWORK_BACK);
}

static void DebugToolsSaveFixtureFinal_BuildMenuItems(void)
{
    memset(
        sSaveFixtureMenuItemDefs,
        0,
        sizeof(sSaveFixtureMenuItemDefs));

    sSaveFixtureMenuItemDefs[0].name = "Back";
    sSaveFixtureMenuItemDefs[0].isAvailable = MenuAlwaysEnabled;
    sSaveFixtureMenuItemDefs[0].onSelected = MenuCancelSelect;
    DEBUGTOOLS_LOCALIZE_ITEM(
        &sSaveFixtureMenuItemDefs[0],
        EXP_MSG_FRAMEWORK_BACK);

    sSaveFixtureMenuItemDefs[1].name = "Run RAM";
    sSaveFixtureMenuItemDefs[1].overrideId =
        DEBUGTOOLS_SAVE_CONTINUE_OVERRIDE_ID;
    sSaveFixtureMenuItemDefs[1].isAvailable = MenuAlwaysEnabled;
    sSaveFixtureMenuItemDefs[1].onSelected =
        DebugToolsSaveFixture_ContinueSelected;
    DEBUGTOOLS_LOCALIZE_ITEM(
        &sSaveFixtureMenuItemDefs[1],
        EXP_MSG_DEBUG_SAVE_FIXTURE_CONTINUE);
}

static void DebugToolsSaveState_BuildReadOnlyMenuItems(void)
{
    memset(
        sSaveFixtureMenuItemDefs,
        0,
        sizeof(sSaveFixtureMenuItemDefs));
    sSaveFixtureMenuItemDefs[0].name = "Back";
    sSaveFixtureMenuItemDefs[0].isAvailable = MenuAlwaysEnabled;
    sSaveFixtureMenuItemDefs[0].onSelected = MenuCancelSelect;
    DEBUGTOOLS_LOCALIZE_ITEM(
        &sSaveFixtureMenuItemDefs[0],
        EXP_MSG_FRAMEWORK_BACK);
}

static void DebugToolsSaveFixtureSource_BuildMenuItems(void)
{
    int item = 0;

    memset(sSaveFixtureMenuItemDefs, 0, sizeof(sSaveFixtureMenuItemDefs));

    sSaveFixtureMenuItemDefs[item].name = "Game 0";
    sSaveFixtureMenuItemDefs[item].overrideId =
        DEBUGTOOLS_SAVE_GAME0_OVERRIDE_ID;
    sSaveFixtureMenuItemDefs[item].isAvailable = MenuAlwaysEnabled;
    sSaveFixtureMenuItemDefs[item].onSelected =
        DebugToolsSaveFixture_Game0Selected;
    DEBUGTOOLS_LOCALIZE_ITEM(
        &sSaveFixtureMenuItemDefs[item],
        EXP_MSG_DEBUG_SAVE_FIXTURE_GAME0);
    item++;

    sSaveFixtureMenuItemDefs[item].name = "Game 1";
    sSaveFixtureMenuItemDefs[item].overrideId =
        DEBUGTOOLS_SAVE_GAME1_OVERRIDE_ID;
    sSaveFixtureMenuItemDefs[item].isAvailable = MenuAlwaysEnabled;
    sSaveFixtureMenuItemDefs[item].onSelected =
        DebugToolsSaveFixture_Game1Selected;
    DEBUGTOOLS_LOCALIZE_ITEM(
        &sSaveFixtureMenuItemDefs[item],
        EXP_MSG_DEBUG_SAVE_FIXTURE_GAME1);
    item++;

    sSaveFixtureMenuItemDefs[item].name = "Game 2";
    sSaveFixtureMenuItemDefs[item].overrideId =
        DEBUGTOOLS_SAVE_GAME2_OVERRIDE_ID;
    sSaveFixtureMenuItemDefs[item].isAvailable = MenuAlwaysEnabled;
    sSaveFixtureMenuItemDefs[item].onSelected =
        DebugToolsSaveFixture_Game2Selected;
    DEBUGTOOLS_LOCALIZE_ITEM(
        &sSaveFixtureMenuItemDefs[item],
        EXP_MSG_DEBUG_SAVE_FIXTURE_GAME2);
    item++;

    sSaveFixtureMenuItemDefs[item].name = "Suspend";
    sSaveFixtureMenuItemDefs[item].overrideId =
        DEBUGTOOLS_SAVE_SUSPEND_OVERRIDE_ID;
    sSaveFixtureMenuItemDefs[item].isAvailable = MenuAlwaysEnabled;
    sSaveFixtureMenuItemDefs[item].onSelected =
        DebugToolsSaveFixture_SuspendSelected;
    DEBUGTOOLS_LOCALIZE_ITEM(
        &sSaveFixtureMenuItemDefs[item],
        EXP_MSG_DEBUG_SAVE_FIXTURE_LATEST_SUSPEND);
    item++;

    sSaveFixtureMenuItemDefs[item].name = "Back";
    sSaveFixtureMenuItemDefs[item].isAvailable = MenuAlwaysEnabled;
    sSaveFixtureMenuItemDefs[item].onSelected = MenuCancelSelect;
    DEBUGTOOLS_LOCALIZE_ITEM(
        &sSaveFixtureMenuItemDefs[item],
        EXP_MSG_FRAMEWORK_BACK);
}

static u8 DebugToolsActions_SaveStateInspectSelected(
    struct MenuProc* menu,
    struct MenuItemProc* item)
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
    if (DebugSaveFixture_IsPersistenceBlocked())
    {
        sprintf(buf, "%s",
            DEBUGTOOLS_LOCALIZED_TEXT(
                EXP_MSG_DEBUG_SAVE_FIXTURE_BLOCKED,
                "Save Blocked"));
    }
    else if (DebugSaveFixture_CanPrepare())
    {
        sSaveFixtureOverrides.completionCount = 3;
        sSaveFixtureOverrides.tacticianMode =
            DEBUG_SAVE_FIXTURE_TACTICIAN_FIXED_MARKER;
    }
    else
    {
        sprintf(buf, "%s",
            DEBUGTOOLS_LOCALIZED_TEXT(
                EXP_MSG_DEBUG_SAVE_FIXTURE_TITLE_ONLY,
                "Title Only"));
    }

    DebugToolsTools_ShowStatusLine(buf);

    if (DebugSaveFixture_CanPrepare())
    {
        DebugToolsSaveFixtureSource_BuildMenuItems();
        DebugTools_QueueSubmenuTransition(
            menu,
            &gDebugToolsSaveFixtureSourceMenuDef);
    }
    else
    {
        DebugToolsSaveState_BuildReadOnlyMenuItems();
        DebugTools_QueueSubmenuTransition(
            menu,
            &gDebugToolsSaveStateMenuDef);
    }

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
