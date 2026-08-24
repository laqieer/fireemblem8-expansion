#include "global.h"

#ifndef FE8_ARCHIVAL_BUILD
#include <string.h>
#include <stdio.h>

#include "hardware.h"
#include "fontgrp.h"
#include "bm.h"
#include "bmunit.h"
#include "proc.h"
#include "uimenu.h"
#include "expansion_debugtools.h"
#include "debugtools_internal.h"

#ifdef MODERN
/* Issue #18 sprint 3: render-time-only ExpansionMsgId mapping for the
 * title/map/prep-reachable *builtin* action ids
 * (1..DEBUGTOOLS_BUILTIN_ID_MAX, see
 * src/debugtools_launcher.c/src/debugtools_actions.c/
 * src/debugtools_tools.c/src/debugtools_music.c) -- purely additive to how those specific
 * rows are *drawn*; struct DebugToolsAction/DebugToolsResult/
 * DebugTools_RegisterAction's ABI and every registered action's own
 * `id`/`label`/`onSelected` fields are completely untouched. Any
 * third-party/unmapped id (0, or >DEBUGTOOLS_BUILTIN_ID_MAX) keeps the exact original
 * pointer-based (def->name = action->label, onDraw = NULL) rendering
 * below -- never redirected through this table. */
#include "expansion_locale.h"
#include "expansion_msg_ids.h"
#endif

/*
 * Issue #11 slice 1 -- contributor debug-action registry and the
 * title-screen debug hub.
 *
 * The whole "enabled" implementation below is compiled out down to
 * trivial disabled-result stubs whenever FE8_EXPANSION_DEBUGTOOLS_ENABLED
 * is 0 (a supported modern release build): no registry storage body, no
 * hub menu construction, no hotkey read. gDebugToolsProbe still exists in
 * every build (always zero-initialized EWRAM) so playtest scenarios can
 * assert it stays all-zero for a whole release-build run.
 */

/* Always linked, in every build -- see docs/debugtools.md "Playtest
 * evidence". Zero-initialized EWRAM is guaranteed on every boot (see
 * src/main.c's unconditional CpuFastFill of all of EWRAM before any
 * gameplay code runs), so this struct reliably starts all-zero. */
EWRAM_DATA struct DebugToolsProbe gDebugToolsProbe = {0};

#if FE8_EXPANSION_DEBUGTOOLS_ENABLED

SECTION("debugtools_contributor_data") struct DebugToolsUnitEditorProbe
    gDebugToolsUnitEditorProbe = {0};

enum
{
    DEBUGTOOLS_STABLE_BUILTIN_ACTION_MAX = 9,
    DEBUGTOOLS_ADDITIONAL_BUILTIN_ACTION_MAX =
        DEBUGTOOLS_BUILTIN_ACTION_MAX - DEBUGTOOLS_STABLE_BUILTIN_ACTION_MAX
};

/* Keep added contributor/page state in a dedicated input section that the
 * linker appends after the pre-existing EWRAM layout. This adds bounded
 * capacity without moving gDebugToolsProbe or any later probe/state symbol
 * whose address is consumed by runtime scenarios. */
SECTION("debugtools_contributor_data") static struct DebugToolsAction
    sContributorActions[DEBUGTOOLS_CONTRIBUTOR_ACTION_MAX] = {0};
SECTION("debugtools_contributor_data") static struct DebugToolsAction
    sAdditionalBuiltinActions[DEBUGTOOLS_ADDITIONAL_BUILTIN_ACTION_MAX] = {0};
SECTION("debugtools_contributor_data") static int sContributorActionCount = 0;
SECTION("debugtools_contributor_data") static int sHubPage = 0;
SECTION("debugtools_contributor_data") static u32 sDebugMenuState = 0;

struct DebugToolsMenuTextScope
{
    struct Font* ownerFont;
    struct Font* restoreFont;
    u16 counterBase;
};

/* StartMenuCore allocates every row synchronously inside StartMenu,
 * before the menu Proc reaches MenuDef::onInit. Capture that exact owner
 * here rather than consulting gActiveFont after a contributor onInit may
 * have switched it. */
SECTION("debugtools_contributor_data") static struct DebugToolsMenuTextScope
    sMenuTextScope = {0};

/* Preserve the established registry EWRAM layout as one explicit object.
 * Separate top-level statics are compiler-reordered in modern builds, so
 * renaming sActions to sBuiltinActions moved the old sHubActive byte away
 * from the address consumed by existing runtime scenarios even though total
 * size was unchanged. These fields reproduce the original active/result/
 * count/actions/menu order while making built-in action slot N-1
 * deterministic. The richer session state lives in the appended contributor
 * section above, so the established one-byte hub probe remains 0/1. */
struct DebugToolsRegistryState
{
    /* 000 */ u32 hubActive;
    /* 004 */ enum DebugToolsResult lastResult;
    /* 008 */ int builtinActionCount;
    /* 00C */ struct DebugToolsAction builtinActions[DEBUGTOOLS_STABLE_BUILTIN_ACTION_MAX];
    /* 078 */ struct MenuItemDef hubMenuItemDefs[DEBUGTOOLS_HUB_MENU_SLOTS];
};

EWRAM_DATA static struct DebugToolsRegistryState sRegistryState = {0};

#define sHubActive sRegistryState.hubActive
#define sLastResult sRegistryState.lastResult
#define sBuiltinActionCount sRegistryState.builtinActionCount
#define sBuiltinActions sRegistryState.builtinActions
#define sHubMenuItemDefs sRegistryState.hubMenuItemDefs

extern struct Font* gActiveFont;

enum
{
    DEBUGTOOLS_TRANSITION_CLEANUP,
    DEBUGTOOLS_TRANSITION_SUBMENU,
    DEBUGTOOLS_TRANSITION_HUB
};

enum
{
    DEBUGTOOLS_STATE_SESSION_ACTIVE = (1 << 0),
    DEBUGTOOLS_STATE_HUB_ACTIVE = (1 << 1),
    DEBUGTOOLS_STATE_TRANSITION_SCHEDULED = (1 << 2),
    DEBUGTOOLS_STATE_BUILTINS_INITIALIZED = (1 << 3)
};

struct DebugToolsMenuTransitionProc
{
    PROC_HEADER;

    /* 2C */ const struct MenuDef* menuDef;
    /* 30 */ struct MenuProc* menuToEnd;
    /* 34 */ struct Font* textOwnerFont;
    /* 38 */ struct Font* restoreFont;
    /* 3C */ u16 textBase;
    /* 3E */ u8 target;
};

static int DebugTools_StartMenuTransition(
    struct MenuProc* menu,
    int target,
    const struct MenuDef* menuDef,
    int deferMenuEnd);
static struct MenuProc* DebugTools_StartOwnedMenu(const struct MenuDef* menuDef);
static int DebugTools_GetActionCount(void);
static const struct DebugToolsAction* DebugTools_GetAction(int index);
static int DebugTools_SetLastResult(enum DebugToolsResult result);

#if defined(FE8_DEBUGTOOLS_DIAGNOSTICS_RUNTIME_TEST)
static struct MenuProc* sDebugToolsRuntimeMenu;
#endif

static u8 DebugToolsHub_BackSelected(struct MenuProc* menu, struct MenuItemProc* item)
{
    return DebugTools_CancelMenu(menu, item);
}

static int DebugToolsHub_GetActionPageCount(void)
{
    int count = DebugTools_GetActionCount();

    if (count == 0)
        return 1;

    return (count + DEBUGTOOLS_HUB_PAGE_ACTION_MAX - 1)
        / DEBUGTOOLS_HUB_PAGE_ACTION_MAX;
}

static int DebugToolsHub_GetPageCount(void)
{
    return DebugToolsHub_GetActionPageCount()
        + DEBUGTOOLS_DIAGNOSTICS_PAGE_COUNT;
}

static int DebugToolsHub_IsDiagnosticsPage(void)
{
    return sHubPage >= DebugToolsHub_GetActionPageCount();
}

static int DebugToolsHub_IsEnginePage(void)
{
    return sHubPage == DebugToolsHub_GetActionPageCount() + 1;
}

static u8 DebugToolsHub_ConsumeEntryCombo(
    struct MenuProc* menu,
    struct MenuItemProc* item)
{
    enum DebugToolsDiagnosticsContext context =
        DebugToolsDiagnostics_GetSessionContext();
    u16 mask;

    (void)menu;
    (void)item;

    switch (context)
    {
    case DEBUGTOOLS_DIAG_CONTEXT_TITLE:
        mask = FE8_EXPANSION_DEBUGTOOLS_HOTKEY_MASK;
        break;

    case DEBUGTOOLS_DIAG_CONTEXT_MAP:
        mask = FE8_EXPANSION_DEBUGTOOLS_MAP_HOTKEY_MASK;
        break;

    case DEBUGTOOLS_DIAG_CONTEXT_PREP:
        mask = FE8_EXPANSION_DEBUGTOOLS_PREP_HOTKEY_MASK;
        break;

    default:
        return 0;
    }

    if ((gKeyStatusPtr->heldKeys & mask) != mask
        || (gKeyStatusPtr->newKeys & mask) == 0)
        return 0;

    if (context == DEBUGTOOLS_DIAG_CONTEXT_PREP
        && (gPlaySt.chapterStateBits & PLAY_FLAG_PREPSCREEN))
        gDebugToolsProbe.prepScreenObservedCount++;

    DebugTools_OpenHub();
    gKeyStatusPtr->newKeys = 0;
    return 0;
}

static u8 DebugToolsHub_NextPage(struct MenuProc* menu)
{
    int pageCount = DebugToolsHub_GetPageCount();
    int nextPage;
    enum DebugToolsResult result;

    if (pageCount <= 1)
        return 0;

    if (sDebugMenuState & DEBUGTOOLS_STATE_TRANSITION_SCHEDULED)
        return 0;

    nextPage = (sHubPage + 1) % pageCount;

    if (nextPage >= DebugToolsHub_GetActionPageCount())
    {
        result = DebugToolsDiagnostics_BeginSession();
        if (result != DEBUGTOOLS_OK && result != DEBUGTOOLS_ERR_ALREADY_ACTIVE)
        {
            DebugTools_SetLastResult(result);
            return 0;
        }
    }

    sHubPage = nextPage;
    DebugTools_StartMenuTransition(menu, DEBUGTOOLS_TRANSITION_HUB, NULL, 1);
    menu->state |= MENU_STATE_FROZEN;

    return 0;
}

static void DebugToolsHub_OnEnd(struct MenuProc* proc)
{
    DebugToolsDiagnostics_ClearActiveMenu(proc);
#if defined(FE8_DEBUGTOOLS_DIAGNOSTICS_RUNTIME_TEST)
    if (sDebugToolsRuntimeMenu == proc)
        sDebugToolsRuntimeMenu = NULL;
#endif
    sHubActive = 0;
    sDebugMenuState &= ~DEBUGTOOLS_STATE_HUB_ACTIVE;

    if (!(sDebugMenuState & DEBUGTOOLS_STATE_SESSION_ACTIVE))
        return;

    if (!DebugToolsDiagnostics_IsRestoring()
        && !(sDebugMenuState & DEBUGTOOLS_STATE_TRANSITION_SCHEDULED))
        DebugTools_StartMenuTransition(
            proc,
            DEBUGTOOLS_TRANSITION_CLEANUP,
            NULL,
            0);
}

static void DebugToolsHub_OnInit(struct MenuProc* proc);

CONST_DATA struct MenuDef gDebugToolsHubMenuDef = {
    {1, 1, DEBUGTOOLS_MENU_WIDTH_TILES, 0},
    0,
    sHubMenuItemDefs,
    DebugToolsHub_OnInit,
    DebugToolsHub_OnEnd,
    0,
    DebugTools_CancelMenu,
    DebugToolsHub_NextPage,
    0
};

#ifdef MODERN
/* Parallel-indexed by builtin action id (1-10); id 0 is never a real
 * action (see DEBUGTOOLS_ERR_ID_INVALID), so slot 0 is an unused
 * placeholder. Every entry here is one of the ten builtin ids -- a
 * contributor/third-party registration always uses some other id and
 * therefore is never looked up in this table (see
 * DebugToolsHub_ResolveBuiltinLabelMsgId below). */
static const ExpansionMsgId sBuiltinActionLabelMsgIds[DEBUGTOOLS_BUILTIN_ACTION_MAX + 1] =
{
    EXPANSION_MSG_ID_INVALID,               /* id 0: never a real action */
    EXP_MSG_DEBUG_ACTION_FASTBOOT_CH2,      /* id 1 */
    EXP_MSG_DEBUG_ACTION_WEATHER,           /* id 2 */
    EXP_MSG_DEBUG_ACTION_FOG,               /* id 3 */
    EXP_MSG_DEBUG_ACTION_FASTBOOT_CH4PREP,  /* id 4 */
    EXP_MSG_DEBUG_ACTION_UNIT_INSPECT,      /* id 5 */
    EXP_MSG_DEBUG_ACTION_CONVOY_INSPECT,    /* id 6 */
    EXP_MSG_DEBUG_ACTION_FLAG_CHAPTER,      /* id 7 */
    EXP_MSG_DEBUG_ACTION_RNG_INSPECT,       /* id 8 */
    EXP_MSG_DEBUG_ACTION_SAVE_STATE,        /* id 9 */
    EXP_MSG_DEBUG_ACTION_MUSIC_PREVIEW,     /* id 10 */
};

/* Returns EXPANSION_MSG_ID_INVALID for any id outside the builtin
 * 1..DEBUGTOOLS_BUILTIN_ID_MAX range (every third-party/contributor id
 * included) -- never an
 * out-of-bounds table read. */
static ExpansionMsgId DebugToolsHub_ResolveBuiltinLabelMsgId(
    const struct DebugToolsAction* action)
{
    u16 id;

    id = action->id;

    if (id < DEBUGTOOLS_BUILTIN_ID_MIN || id > DEBUGTOOLS_BUILTIN_ID_MAX)
        return EXPANSION_MSG_ID_INVALID;

    return sBuiltinActionLabelMsgIds[id];
}

/* onDraw for a builtin action's hub row only -- resolved fresh every
 * redraw (menu redraws happen on every hub open/locale-settings
 * round-trip), so a locale switch is picked up on the very next render
 * with no cached scratch pointer held across frames. Mirrors the
 * engine's own default per-item draw (src/uimenu.c) except for the
 * label source. */
static int DebugToolsHub_BuiltinActionRowDraw(struct MenuProc* proc, struct MenuItemProc* item)
{
    if (item->def->color)
        Text_SetColor(&item->text, item->def->color);

    if (item->availability == MENU_DISABLED)
        Text_SetColor(&item->text, TEXT_COLOR_SYSTEM_GRAY);

    Text_DrawString(
        &item->text,
        ExpansionLocale_ResolveCurrent((ExpansionMsgId)item->def->helpMsgId));

    PutText(&item->text, TILEMAP_LOCATED(BG_GetMapBuffer(proc->frontBg), item->xTile, item->yTile));

    return 0;
}
#endif /* FE8_ARCHIVAL_BUILD */

#ifdef MODERN
#define DEBUGTOOLS_TEXT(message, fallback) \
    ExpansionLocale_ResolveCurrent((message))
#else
#define DEBUGTOOLS_TEXT(message, fallback) (fallback)
#endif

static const char* DebugToolsHub_ContextText(u8 context)
{
    switch (context)
    {
    case DEBUGTOOLS_DIAG_CONTEXT_TITLE:
        return DEBUGTOOLS_TEXT(EXP_MSG_DEBUG_VALUE_CONTEXT_TITLE, "TITLE");

    case DEBUGTOOLS_DIAG_CONTEXT_MAP:
        return DEBUGTOOLS_TEXT(EXP_MSG_DEBUG_VALUE_CONTEXT_MAP, "MAP");

    case DEBUGTOOLS_DIAG_CONTEXT_PREP:
        return DEBUGTOOLS_TEXT(EXP_MSG_DEBUG_VALUE_CONTEXT_PREP, "PREP");

    case DEBUGTOOLS_DIAG_CONTEXT_BATTLE:
        return DEBUGTOOLS_TEXT(EXP_MSG_DEBUG_VALUE_CONTEXT_BATTLE, "BATTLE");

    default:
        return DEBUGTOOLS_TEXT(EXP_MSG_DEBUG_VALUE_UNAVAILABLE, "N/A");
    }
}

static const char* DebugToolsHub_PhaseText(u8 faction)
{
    switch (faction)
    {
    case FACTION_BLUE:
        return DEBUGTOOLS_TEXT(EXP_MSG_DEBUG_VALUE_PHASE_PLAYER, "PLAYER");

    case FACTION_RED:
        return DEBUGTOOLS_TEXT(EXP_MSG_DEBUG_VALUE_PHASE_ENEMY, "ENEMY");

    case FACTION_GREEN:
        return DEBUGTOOLS_TEXT(EXP_MSG_DEBUG_VALUE_PHASE_NPC, "NPC");

    default:
        return DEBUGTOOLS_TEXT(EXP_MSG_DEBUG_VALUE_PHASE_OTHER, "OTHER");
    }
}

static void DebugToolsHub_FormatUnavailable(char* buf)
{
    sprintf(buf, "%s",
        DEBUGTOOLS_TEXT(EXP_MSG_DEBUG_VALUE_UNAVAILABLE, "N/A"));
}

static void DebugToolsHub_FormatStateRow(
    char* buf,
    int row,
    const struct DebugToolsDiagnosticsSnapshot* snapshot)
{
    if (!(snapshot->validMask & DEBUGTOOLS_DIAG_VALID_COMMON))
    {
        DebugToolsHub_FormatUnavailable(buf);
        return;
    }

    switch (row)
    {
    case 0:
        sprintf(buf, "%s %s %s %u",
            DEBUGTOOLS_TEXT(EXP_MSG_DEBUG_FIELD_CONTEXT, "CTX"),
            DebugToolsHub_ContextText(snapshot->context),
            DEBUGTOOLS_TEXT(EXP_MSG_DEBUG_FIELD_CLOCK, "CLK"),
            (unsigned int)snapshot->gameClockFrames);
        return;

    case 1:
        if (!(snapshot->validMask & DEBUGTOOLS_DIAG_VALID_MAP))
            break;
        sprintf(buf, "%s %d %s %u",
            DEBUGTOOLS_TEXT(EXP_MSG_DEBUG_FIELD_CHAPTER, "CH"),
            (int)snapshot->chapterIndex,
            DEBUGTOOLS_TEXT(EXP_MSG_DEBUG_FIELD_TURN, "T"),
            (unsigned int)snapshot->turn);
        return;

    case 2:
        if (!(snapshot->validMask & DEBUGTOOLS_DIAG_VALID_MAP))
            break;
        sprintf(buf, "%s %s",
            DEBUGTOOLS_TEXT(EXP_MSG_DEBUG_FIELD_PHASE, "PH"),
            DebugToolsHub_PhaseText(snapshot->faction));
        return;

    case 3:
        if (!(snapshot->validMask & DEBUGTOOLS_DIAG_VALID_CURSOR))
            break;
        sprintf(buf, "%s %d,%d",
            DEBUGTOOLS_TEXT(EXP_MSG_DEBUG_FIELD_CURSOR, "XY"),
            (int)snapshot->cursorX,
            (int)snapshot->cursorY);
        return;

    case 4:
        if (!(snapshot->validMask & DEBUGTOOLS_DIAG_VALID_UNIT))
            break;
        sprintf(buf, "%s %u %s %u %s %u",
            DEBUGTOOLS_TEXT(EXP_MSG_DEBUG_FIELD_UNIT, "U"),
            (unsigned int)snapshot->cursorUnitId,
            DEBUGTOOLS_TEXT(EXP_MSG_DEBUG_FIELD_CHARACTER, "C"),
            (unsigned int)snapshot->characterId,
            DEBUGTOOLS_TEXT(EXP_MSG_DEBUG_FIELD_CLASS, "J"),
            (unsigned int)snapshot->classId);
        return;

    case 5:
        if (!(snapshot->validMask & DEBUGTOOLS_DIAG_VALID_UNIT))
            break;
        sprintf(buf, "%s %u/%u",
            DEBUGTOOLS_TEXT(EXP_MSG_DEBUG_FIELD_HP, "HP"),
            (unsigned int)snapshot->currentHp,
            (unsigned int)snapshot->maxHp);
        return;

    case 6:
        if (!(snapshot->validMask & DEBUGTOOLS_DIAG_VALID_MAP))
            break;
        sprintf(buf, "%s %u %s %u",
            DEBUGTOOLS_TEXT(EXP_MSG_DEBUG_FIELD_WEATHER, "W"),
            (unsigned int)snapshot->weatherId,
            DEBUGTOOLS_TEXT(EXP_MSG_DEBUG_FIELD_FOG, "F"),
            (unsigned int)snapshot->fogRange);
        return;

    case 7:
        sprintf(buf, "%s %04X %04X %04X",
            DEBUGTOOLS_TEXT(EXP_MSG_DEBUG_FIELD_RNG, "RNG"),
            (unsigned int)snapshot->rngState[0],
            (unsigned int)snapshot->rngState[1],
            (unsigned int)snapshot->rngState[2]);
        return;
    }

    DebugToolsHub_FormatUnavailable(buf);
}

static void DebugToolsHub_FormatEngineRow(
    char* buf,
    int row,
    const struct DebugToolsDiagnosticsSnapshot* snapshot)
{
    if (!(snapshot->validMask & DEBUGTOOLS_DIAG_VALID_COMMON))
    {
        DebugToolsHub_FormatUnavailable(buf);
        return;
    }

    switch (row)
    {
    case 0:
        sprintf(buf, "%s %u",
            DEBUGTOOLS_TEXT(EXP_MSG_DEBUG_FIELD_PROC_COUNT, "PROC"),
            (unsigned int)snapshot->procCount);
        return;

    case 1:
        sprintf(buf, "%s %u",
            DEBUGTOOLS_TEXT(EXP_MSG_DEBUG_FIELD_EVENT, "EVENT"),
            (unsigned int)snapshot->eventEngineActive);
        return;

    case 2:
        sprintf(buf, "%s %u",
            DEBUGTOOLS_TEXT(EXP_MSG_DEBUG_FIELD_ACTIONS, "ACT"),
            (unsigned int)snapshot->registeredActionCount);
        return;

    case 3:
        sprintf(buf, "%s %u/%u",
            DEBUGTOOLS_TEXT(EXP_MSG_DEBUG_FIELD_LOG_RETAINED, "LOG"),
            (unsigned int)snapshot->logRetainedCount,
            (unsigned int)DEBUGTOOLS_LOG_RING_SIZE);
        return;

    case 4:
        sprintf(buf, "%s %u",
            DEBUGTOOLS_TEXT(EXP_MSG_DEBUG_FIELD_LOG_WRITES, "WRITES"),
            (unsigned int)snapshot->logTotalWrites);
        return;

    case 5:
        sprintf(buf, "%s %u",
            DEBUGTOOLS_TEXT(EXP_MSG_DEBUG_FIELD_LOG_LAST, "LAST LOG"),
            (unsigned int)snapshot->lastLogCode);
        return;

    case 6:
        sprintf(buf, "%s %u",
            DEBUGTOOLS_TEXT(EXP_MSG_DEBUG_FIELD_ASSERT_COUNT, "ASSERT"),
            (unsigned int)snapshot->assertFailureCount);
        return;

    default:
        sprintf(buf, "%s %u",
            DEBUGTOOLS_TEXT(EXP_MSG_DEBUG_FIELD_ASSERT_LAST, "LAST ASSERT"),
            (unsigned int)snapshot->lastAssertCode);
        return;
    }
}

static int DebugToolsHub_DiagnosticsRowDraw(
    struct MenuProc* menu,
    struct MenuItemProc* item)
{
    const struct DebugToolsDiagnosticsSnapshot* snapshot =
        DebugToolsDiagnostics_GetSnapshot();
    char buf[64];

    ClearText(&item->text);
    Text_SetColor(&item->text, TEXT_COLOR_SYSTEM_WHITE);

    if (DebugToolsHub_IsEnginePage())
        DebugToolsHub_FormatEngineRow(buf, item->itemNumber, snapshot);
    else
        DebugToolsHub_FormatStateRow(buf, item->itemNumber, snapshot);

    Text_DrawString(&item->text, buf);
    PutText(
        &item->text,
        TILEMAP_LOCATED(
            BG_GetMapBuffer(menu->frontBg),
            item->xTile,
            item->yTile));
    return 0;
}

static u8 DebugToolsHub_RefreshSelected(
    struct MenuProc* menu,
    struct MenuItemProc* item)
{
    (void)item;
    DebugToolsDiagnostics_RefreshSnapshot();
    RedrawMenu(menu);
    return MENU_ACT_SKIPCURSOR | MENU_ACT_SND6A;
}

#if defined(FE8_DEBUGTOOLS_DIAGNOSTICS_RUNTIME_TEST)
static u8 sDebugToolsRuntimeViewStage;
static u8 sDebugToolsRuntimeViewStarted;
static u8 sDebugToolsRuntimeEngineWait;

static void DebugToolsHub_RuntimeTestViews(ProcPtr proc)
{
    struct MenuProc* menu;

    if (sDebugMenuState & DEBUGTOOLS_STATE_TRANSITION_SCHEDULED)
        return;

    menu = DebugToolsDiagnostics_GetActiveMenu();
    if (menu == NULL)
        menu = sDebugToolsRuntimeMenu;
    switch (sDebugToolsRuntimeViewStage)
    {
    case 0:
        DebugToolsDiagnostics_SetSessionContext(DEBUGTOOLS_DIAG_CONTEXT_MAP);
        if (DebugTools_OpenHub() == DEBUGTOOLS_OK)
            sDebugToolsRuntimeViewStage = 1;
        return;

    case 1:
        if (menu == NULL)
            return;

        if (!DebugToolsHub_IsDiagnosticsPage())
        {
            /* The lifecycle host driver traverses every action page. This
             * runtime-only scalar fixture needs State/Engine before its
             * fixed checkpoint, so start the real page transition from the
             * final catalog-derived action page. */
            sHubPage = DebugToolsHub_GetActionPageCount() - 1;
            DebugToolsHub_NextPage(menu);
            return;
        }

        sDebugToolsRuntimeViewStage = 2;
        return;

    case 2:
        if (menu == NULL || !DebugToolsHub_IsDiagnosticsPage())
            return;
        DebugToolsHub_RefreshSelected(menu, NULL);
        DebugToolsHub_NextPage(menu);
        sDebugToolsRuntimeEngineWait = 0;
        sDebugToolsRuntimeViewStage = 3;
        return;

    case 3:
        if (menu == NULL || !DebugToolsHub_IsEnginePage())
            return;
        if (++sDebugToolsRuntimeEngineWait < 2)
            return;
        DebugToolsDiagnostics_ForceCloseSession();
        gDebugToolsDiagnosticsProbe.viewRuntimeComplete = 1;
        Proc_Break(proc);
        return;
    }
}

static struct ProcCmd CONST_DATA sDebugToolsRuntimeViewScript[] =
{
    PROC_REPEAT(DebugToolsHub_RuntimeTestViews),
    PROC_END,
};

static void DebugToolsHub_StartRuntimeTestViews(void)
{
    if (sDebugToolsRuntimeViewStarted
        || !gDebugToolsDiagnosticsProbe.mapRuntimeComplete)
        return;

    sDebugToolsRuntimeViewStarted = 1;
    Proc_Start(sDebugToolsRuntimeViewScript, PROC_TREE_3);
}
#endif

static void DebugToolsHub_BuildDiagnosticsMenuItems(void)
{
    int i;

    for (i = 0; i < 8; ++i)
    {
        sHubMenuItemDefs[i].name = "";
        sHubMenuItemDefs[i].isAvailable = MenuAlwaysDisabled;
        sHubMenuItemDefs[i].onDraw = DebugToolsHub_DiagnosticsRowDraw;
        sHubMenuItemDefs[i].onIdle = DebugToolsHub_ConsumeEntryCombo;
    }

    sHubMenuItemDefs[8].name = "Refresh";
    sHubMenuItemDefs[8].isAvailable = MenuAlwaysEnabled;
    sHubMenuItemDefs[8].onSelected = DebugToolsHub_RefreshSelected;
    sHubMenuItemDefs[8].onIdle = DebugToolsHub_ConsumeEntryCombo;
    sHubMenuItemDefs[9].name = "Back";
    sHubMenuItemDefs[9].isAvailable = MenuAlwaysEnabled;
    sHubMenuItemDefs[9].onSelected = DebugToolsHub_BackSelected;
    sHubMenuItemDefs[9].onIdle = DebugToolsHub_ConsumeEntryCombo;

#ifdef MODERN
    sHubMenuItemDefs[8].helpMsgId = EXP_MSG_DEBUG_ACTION_REFRESH;
    sHubMenuItemDefs[8].onDraw = DebugToolsHub_BuiltinActionRowDraw;
    sHubMenuItemDefs[9].helpMsgId = EXP_MSG_FRAMEWORK_BACK;
    sHubMenuItemDefs[9].onDraw = DebugToolsHub_BuiltinActionRowDraw;
#endif

    DebugToolsDiagnostics_RefreshSnapshot();
    DebugToolsDiagnostics_RecordViewOpen(DebugToolsHub_IsEnginePage());
}

static void DebugToolsHub_BuildMenuItems(void)
{
    const struct DebugToolsAction* action;
#ifdef MODERN
    ExpansionMsgId builtinMsgId;
#endif
    int actionCount;
    int firstAction;
    int visibleCount;
    int i;

    memset(sHubMenuItemDefs, 0, sizeof(sHubMenuItemDefs));

    actionCount = DebugTools_GetActionCount();
    if (sHubPage >= DebugToolsHub_GetPageCount())
        sHubPage = 0;

    if (DebugToolsHub_IsDiagnosticsPage())
    {
        DebugToolsHub_BuildDiagnosticsMenuItems();
        return;
    }

    firstAction = sHubPage * DEBUGTOOLS_HUB_PAGE_ACTION_MAX;
    visibleCount = actionCount - firstAction;
    if (visibleCount > DEBUGTOOLS_HUB_PAGE_ACTION_MAX)
        visibleCount = DEBUGTOOLS_HUB_PAGE_ACTION_MAX;

    for (i = 0; i < visibleCount; ++i)
    {
        struct MenuItemDef* def = &sHubMenuItemDefs[i];
        action = DebugTools_GetAction(firstAction + i);
#ifdef MODERN
        builtinMsgId = DebugToolsHub_ResolveBuiltinLabelMsgId(action);
#endif

        def->name = action->label;
        def->nameMsgId = 0;
        def->helpMsgId = 0;
        def->color = 0;
        def->overrideId = 0;
        def->isAvailable = MenuAlwaysEnabled;
        def->onDraw = NULL;
        def->onSelected = action->onSelected;
        def->onIdle = DebugToolsHub_ConsumeEntryCombo;
        def->onSwitchIn = NULL;
        def->onSwitchOut = NULL;

#ifdef MODERN
        /* Builtin action ids only -- every third-party/contributor
         * registration keeps the exact original pointer-based rendering
         * (def->name/onDraw = NULL) set just above, completely
         * untouched. */
        if (builtinMsgId != EXPANSION_MSG_ID_INVALID)
        {
            def->helpMsgId = (u16)builtinMsgId;
            def->onDraw = DebugToolsHub_BuiltinActionRowDraw;
        }
#endif
    }

    /* Reserved Back/Exit entry -- always the entry right after the last
     * registered action, never edited by contributors. */
    sHubMenuItemDefs[visibleCount].name = "Back";
#ifdef MODERN
    sHubMenuItemDefs[visibleCount].helpMsgId = EXP_MSG_FRAMEWORK_BACK;
    sHubMenuItemDefs[visibleCount].onDraw =
        DebugToolsHub_BuiltinActionRowDraw;
#endif
    sHubMenuItemDefs[visibleCount].isAvailable = MenuAlwaysEnabled;
    sHubMenuItemDefs[visibleCount].onSelected = DebugToolsHub_BackSelected;
    sHubMenuItemDefs[visibleCount].onIdle =
        DebugToolsHub_ConsumeEntryCombo;

    /* sHubMenuItemDefs[visibleCount + 1] stays all-zero: the terminator. */
}

static void DebugToolsHub_ShowDiagnostics(struct MenuProc* menu)
{
    char buf[64];
    int actionCount = DebugTools_GetActionCount();
    int pageCount = DebugToolsHub_GetActionPageCount();

#ifdef MODERN
    if (sLastResult != DEBUGTOOLS_OK)
        sprintf(buf, "%s %d",
            ExpansionLocale_ResolveCurrent(EXP_MSG_DEBUG_STATUS_HUB_ERROR),
            (int)sLastResult);
    else if (sContributorActionCount == 0)
        sprintf(buf, "%s %d/%d",
            ExpansionLocale_ResolveCurrent(EXP_MSG_DEBUG_STATUS_HUB),
            sBuiltinActionCount, DEBUGTOOLS_BUILTIN_ACTION_MAX);
    else
        sprintf(buf, "%s %d %d/%d",
            ExpansionLocale_ResolveCurrent(EXP_MSG_DEBUG_STATUS_HUB),
            actionCount, sHubPage + 1, pageCount);
#else
    if (sLastResult != DEBUGTOOLS_OK)
        sprintf(buf, "DBGTOOLS ERR %d", (int)sLastResult);
    else if (sContributorActionCount == 0)
        sprintf(buf, "DBGTOOLS %d/%d",
            sBuiltinActionCount, DEBUGTOOLS_BUILTIN_ACTION_MAX);
    else
        sprintf(buf, "DBGTOOLS %d %d/%d",
            actionCount, sHubPage + 1, pageCount);
#endif

    DebugToolsDiagnostics_DrawStatusText(menu, buf);
}

static void DebugToolsHub_ShowViewHeader(struct MenuProc* menu)
{
    const char* text = DebugToolsHub_IsEnginePage()
        ? DEBUGTOOLS_TEXT(EXP_MSG_DEBUG_VIEW_ENGINE, "ENGINE")
        : DEBUGTOOLS_TEXT(EXP_MSG_DEBUG_VIEW_STATE, "STATE");

    DebugToolsDiagnostics_DrawStatusText(menu, text);
}

static void DebugToolsHub_OnInit(struct MenuProc* menu)
{
    if (!DebugToolsHub_IsDiagnosticsPage())
    {
        DebugToolsHub_ShowDiagnostics(menu);
        return;
    }

    DebugToolsHub_ShowViewHeader(menu);
    DrawMenuItemHover(menu, menu->itemCurrent, FALSE);
    menu->itemPrevious = menu->itemCurrent;
    menu->itemCurrent = 8;
    DrawMenuItemHover(menu, menu->itemCurrent, TRUE);
}

static int DebugTools_SetLastResult(enum DebugToolsResult result)
{
    sLastResult = result;
    gDebugToolsProbe.lastRegisterResult = result;
    return result;
}

static int DebugTools_GetActionCount(void)
{
    return sBuiltinActionCount + sContributorActionCount;
}

static struct DebugToolsAction* DebugTools_GetBuiltinSlot(u16 id)
{
    int slot = id - DEBUGTOOLS_BUILTIN_ID_MIN;

    if (id < DEBUGTOOLS_BUILTIN_ID_MIN || id > DEBUGTOOLS_BUILTIN_ID_MAX)
        return NULL;

    if (slot < DEBUGTOOLS_STABLE_BUILTIN_ACTION_MAX)
        return &sBuiltinActions[slot];

    return &sAdditionalBuiltinActions[
        slot - DEBUGTOOLS_STABLE_BUILTIN_ACTION_MAX];
}

static const struct DebugToolsAction* DebugTools_GetBuiltinAction(int index)
{
    struct DebugToolsAction* action;
    int id;

    if (index < 0 || index >= sBuiltinActionCount)
        return NULL;

    for (id = DEBUGTOOLS_BUILTIN_ID_MIN; id <= DEBUGTOOLS_BUILTIN_ID_MAX; ++id)
    {
        action = DebugTools_GetBuiltinSlot((u16)id);
        if (action->id == 0)
            continue;

        if (index == 0)
            return action;

        index--;
    }

    return NULL;
}

static const struct DebugToolsAction* DebugTools_GetAction(int index)
{
    if (index < 0 || index >= DebugTools_GetActionCount())
        return NULL;

    if (index < sBuiltinActionCount)
        return DebugTools_GetBuiltinAction(index);

    return &sContributorActions[index - sBuiltinActionCount];
}

static int DebugTools_RegisterActionCore(const struct DebugToolsAction* action, int isBuiltin)
{
    const struct DebugToolsAction* registered;
    int actionCount;
    int i;

    if (action == NULL || action->label == NULL || action->onSelected == NULL)
        return DebugTools_SetLastResult(DEBUGTOOLS_ERR_INVALID_ACTION);

    if (action->id == 0)
        return DebugTools_SetLastResult(DEBUGTOOLS_ERR_ID_INVALID);

    if (isBuiltin)
    {
        if (action->id < DEBUGTOOLS_BUILTIN_ID_MIN
            || action->id > DEBUGTOOLS_BUILTIN_ID_MAX)
            return DebugTools_SetLastResult(DEBUGTOOLS_ERR_ID_INVALID);
    }
    else if (action->id >= DEBUGTOOLS_BUILTIN_ID_MIN
        && action->id <= DEBUGTOOLS_BUILTIN_ID_MAX)
    {
        return DebugTools_SetLastResult(DEBUGTOOLS_ERR_ID_RESERVED);
    }

    /* Issue #11 closure: label must be non-empty and within the
     * documented DEBUGTOOLS_LABEL_MAX_LENGTH policy bound. This does not
     * copy or retain any bytes beyond the pointer itself (see
     * sContributorActions[sContributorActionCount] = *action below) --
     * contributors are responsible for passing a label with static/
     * persistent storage duration (every action in this file uses a plain
     * string literal, which always satisfies this); this length check is a
     * rendering/policy bound, not a lifetime check C89 can perform at
     * runtime. */
    if (action->label[0] == '\0' || strlen(action->label) > DEBUGTOOLS_LABEL_MAX_LENGTH)
        return DebugTools_SetLastResult(DEBUGTOOLS_ERR_LABEL_INVALID);

    if (isBuiltin)
    {
        registered = DebugTools_GetBuiltinSlot(action->id);
        if (registered->id != 0)
        {
            if (registered->id == action->id
                && strcmp(registered->label, action->label) == 0
                && registered->onSelected == action->onSelected)
                return DebugTools_SetLastResult(DEBUGTOOLS_OK);

            return DebugTools_SetLastResult(DEBUGTOOLS_ERR_DUPLICATE);
        }
    }

    actionCount = DebugTools_GetActionCount();
    for (i = 0; i < actionCount; ++i)
    {
        registered = DebugTools_GetAction(i);
        if (registered->id == action->id
            || strcmp(registered->label, action->label) == 0)
            return DebugTools_SetLastResult(DEBUGTOOLS_ERR_DUPLICATE);
    }

    if (isBuiltin)
    {
        struct DebugToolsAction* slot = DebugTools_GetBuiltinSlot(action->id);

        if (sBuiltinActionCount >= DEBUGTOOLS_BUILTIN_ACTION_MAX)
            return DebugTools_SetLastResult(DEBUGTOOLS_ERR_CAPACITY_FULL);

        *slot = *action;
        sBuiltinActionCount++;
    }
    else
    {
        if (sContributorActionCount >= DEBUGTOOLS_CONTRIBUTOR_ACTION_MAX)
            return DebugTools_SetLastResult(DEBUGTOOLS_ERR_CAPACITY_FULL);

        sContributorActions[sContributorActionCount] = *action;
        sContributorActionCount++;
    }

    gDebugToolsProbe.registeredActionCount = (u32)DebugTools_GetActionCount();

    return DebugTools_SetLastResult(DEBUGTOOLS_OK);
}

int DebugTools_RegisterBuiltinAction(const struct DebugToolsAction* action)
{
    return DebugTools_RegisterActionCore(action, 1);
}

static void DebugTools_EnsureBuiltinActionsRegistered(void)
{
    if (sDebugMenuState & DEBUGTOOLS_STATE_BUILTINS_INITIALIZED)
        return;

    DebugTools_RegisterBuiltinActions();
    DebugTools_RegisterWeatherFogActions();
    DebugTools_RegisterChapter4PrepAction();
    DebugTools_RegisterExtendedToolActions();
#ifndef FE8_ARCHIVAL_BUILD
    DebugTools_RegisterMusicPreviewAction();
#endif
    sDebugMenuState |= DEBUGTOOLS_STATE_BUILTINS_INITIALIZED;
}

int DebugTools_RegisterAction(const struct DebugToolsAction* action)
{
    if (action == NULL || action->label == NULL || action->onSelected == NULL)
        return DebugTools_SetLastResult(DEBUGTOOLS_ERR_INVALID_ACTION);

    if (action->id == 0)
        return DebugTools_SetLastResult(DEBUGTOOLS_ERR_ID_INVALID);

    if (action->id >= DEBUGTOOLS_BUILTIN_ID_MIN
        && action->id <= DEBUGTOOLS_BUILTIN_ID_MAX)
        return DebugTools_SetLastResult(DEBUGTOOLS_ERR_ID_RESERVED);

    if (action->label[0] == '\0' || strlen(action->label) > DEBUGTOOLS_LABEL_MAX_LENGTH)
        return DebugTools_SetLastResult(DEBUGTOOLS_ERR_LABEL_INVALID);

    DebugTools_EnsureBuiltinActionsRegistered();
    return DebugTools_RegisterActionCore(action, 0);
}

int DebugTools_GetRegisteredCount(void)
{
    return DebugTools_GetActionCount();
}

const struct DebugToolsAction* DebugTools_GetRegisteredAction(int index)
{
    return DebugTools_GetAction(index);
}

enum DebugToolsResult DebugTools_GetLastRegistrationResult(void)
{
    return sLastResult;
}

static int DebugTools_HasTextCapacity(void)
{
    int firstTile;
    int capacity;

    if (gActiveFont == NULL)
        return 0;

    firstTile = gActiveFont->tileref & 0x3FF;
    capacity = (0x400 - firstTile) / 2;

    if (gActiveFont->chr_counter + DEBUGTOOLS_HUB_TEXT_ALLOC_BUDGET > capacity)
        return 0;

    return 1;
}

static struct MenuProc* DebugTools_StartOwnedMenu(const struct MenuDef* menuDef)
{
    struct MenuProc* menu;

    sMenuTextScope.ownerFont = gActiveFont;
    sMenuTextScope.restoreFont = gActiveFont;
    sMenuTextScope.counterBase =
        gActiveFont == NULL ? 0 : gActiveFont->chr_counter;

    menu = DebugToolsDiagnostics_StartOwnedMenu(menuDef);
    if (menu == NULL)
        menu = StartOrphanMenu(menuDef);

    DebugToolsDiagnostics_SetActiveMenu(menu);
#if defined(FE8_DEBUGTOOLS_DIAGNOSTICS_RUNTIME_TEST)
    sDebugToolsRuntimeMenu = menu;
#endif
    return menu;
}

static int DebugTools_StartMenuTransition(
    struct MenuProc* menu,
    int target,
    const struct MenuDef* menuDef,
    int deferMenuEnd)
{
    struct DebugToolsMenuTransitionProc* proc;

    if (sDebugMenuState & DEBUGTOOLS_STATE_TRANSITION_SCHEDULED)
        return 0;

    proc = Proc_Start(gProcScr_DebugToolsMenuTransition, PROC_TREE_3);
    proc->menuDef = menuDef;
    proc->menuToEnd = deferMenuEnd ? menu : NULL;
    proc->textOwnerFont = sMenuTextScope.ownerFont;
    proc->restoreFont = sMenuTextScope.restoreFont;
    proc->textBase = sMenuTextScope.counterBase;
    proc->target = target;

    sDebugMenuState |= DEBUGTOOLS_STATE_TRANSITION_SCHEDULED;
    return 1;
}

void DebugTools_QueueSubmenuTransition(struct MenuProc* menu, const struct MenuDef* menuDef)
{
    if (menuDef == NULL
        || !(sDebugMenuState & DEBUGTOOLS_STATE_SESSION_ACTIVE)
        || (sDebugMenuState & DEBUGTOOLS_STATE_TRANSITION_SCHEDULED))
        return;

    DebugTools_StartMenuTransition(
        menu,
        DEBUGTOOLS_TRANSITION_SUBMENU,
        menuDef,
        0);
}

int DebugTools_IsMenuTransitionScheduled(void)
{
    return sDebugMenuState & DEBUGTOOLS_STATE_TRANSITION_SCHEDULED;
}

void DebugTools_EndSessionAfterMenuEnd(struct MenuProc* menu)
{
    if (!(sDebugMenuState & DEBUGTOOLS_STATE_SESSION_ACTIVE)
        || (sDebugMenuState & DEBUGTOOLS_STATE_TRANSITION_SCHEDULED))
        return;

    DebugTools_StartMenuTransition(menu, DEBUGTOOLS_TRANSITION_CLEANUP, NULL, 0);
}

void DebugTools_ReturnToHubAfterMenuEnd(struct MenuProc* menu)
{
    DebugToolsDiagnostics_ClearActiveMenu(menu);

    if (!(sDebugMenuState & DEBUGTOOLS_STATE_SESSION_ACTIVE)
        || DebugToolsDiagnostics_IsRestoring()
        || (sDebugMenuState
        & (DEBUGTOOLS_STATE_HUB_ACTIVE | DEBUGTOOLS_STATE_TRANSITION_SCHEDULED))
    )
        return;

    DebugTools_StartMenuTransition(menu, DEBUGTOOLS_TRANSITION_HUB, NULL, 0);
}

static enum DebugToolsResult DebugTools_OpenHubInternal(void)
{
    DebugToolsHub_BuildMenuItems();
    gDebugToolsProbe.hubOpenCount++;
    sHubActive = 1;
    sDebugMenuState |= DEBUGTOOLS_STATE_HUB_ACTIVE;

    DebugTools_StartOwnedMenu(&gDebugToolsHubMenuDef);

    return DEBUGTOOLS_OK;
}

void DebugTools_RunMenuTransition(ProcPtr proc)
{
    struct DebugToolsMenuTransitionProc* transition = proc;
    const struct MenuDef* submenuDef = transition->menuDef;
    int target = transition->target;

    if (transition->menuToEnd != NULL)
        EndMenu(transition->menuToEnd);

    if (transition->textOwnerFont != NULL)
        transition->textOwnerFont->chr_counter = transition->textBase;

    gActiveFont = transition->restoreFont;
    sDebugMenuState &= ~DEBUGTOOLS_STATE_TRANSITION_SCHEDULED;

    if (target == DEBUGTOOLS_TRANSITION_SUBMENU && submenuDef != NULL)
    {
        DebugTools_StartOwnedMenu(submenuDef);
        return;
    }

    if (target == DEBUGTOOLS_TRANSITION_HUB)
    {
        DebugToolsSaveState_OnHubReturn();
        DebugTools_OpenHubInternal();
        return;
    }

    sHubPage = 0;
#ifndef FE8_ARCHIVAL_BUILD
    DebugTools_CleanupMusicPreview();
#endif
    sDebugMenuState &= ~(DEBUGTOOLS_STATE_SESSION_ACTIVE | DEBUGTOOLS_STATE_HUB_ACTIVE);
    DebugToolsDiagnostics_EndSession(0);
    DebugToolsDiagnostics_ClearSessionContext();
}

struct ProcCmd CONST_DATA gProcScr_DebugToolsMenuTransition[] =
{
    PROC_YIELD,
    PROC_CALL(DebugTools_RunMenuTransition),
    PROC_END
};

enum DebugToolsResult DebugTools_OpenHub(void)
{
    /* Single authoritative reentrancy guard. A release-and-repress of
     * the title hotkey (or any other future caller) while the hub is
     * already open must never start a second concurrent MenuProc:
     * without this, the title hotkey check's edge-detected newKeys
     * condition re-fires on every subsequent complete press/release/
     * press cycle, each of which would otherwise start another owned menu
     * again. Guarding here -- rather than in each caller -- protects
     * every current and future entry path. */
    if (sDebugMenuState & DEBUGTOOLS_STATE_SESSION_ACTIVE)
        return DEBUGTOOLS_ERR_ALREADY_ACTIVE;

    DebugTools_EnsureBuiltinActionsRegistered();

    if (!DebugTools_HasTextCapacity())
        return DebugTools_SetLastResult(DEBUGTOOLS_ERR_TEXT_CAPACITY);

    sHubPage = 0;
    sDebugMenuState |= DEBUGTOOLS_STATE_SESSION_ACTIVE;
    return DebugTools_OpenHubInternal();
}

int DebugTools_IsHubActive(void)
{
    return sDebugMenuState & DEBUGTOOLS_STATE_SESSION_ACTIVE;
}

void DebugToolsDiagnostics_OnSessionRestored(void)
{
#ifndef FE8_ARCHIVAL_BUILD
    DebugTools_CleanupMusicPreview();
#endif
    sHubPage = 0;
    sHubActive = 0;
    sDebugMenuState &=
        ~(DEBUGTOOLS_STATE_SESSION_ACTIVE
            | DEBUGTOOLS_STATE_HUB_ACTIVE
            | DEBUGTOOLS_STATE_TRANSITION_SCHEDULED);
}

static void DebugTools_TryOpenFromContext(
    enum DebugToolsDiagnosticsContext context)
{
    enum DebugToolsResult result;
    u16 mask;

    if (context == DEBUGTOOLS_DIAG_CONTEXT_TITLE)
        mask = FE8_EXPANSION_DEBUGTOOLS_HOTKEY_MASK;
    else if (context == DEBUGTOOLS_DIAG_CONTEXT_MAP)
        mask = FE8_EXPANSION_DEBUGTOOLS_MAP_HOTKEY_MASK;
    else
        mask = FE8_EXPANSION_DEBUGTOOLS_PREP_HOTKEY_MASK;

    if (DebugTools_IsHubActive())
    {
        if (DebugTools_OpenHub() == DEBUGTOOLS_ERR_ALREADY_ACTIVE
            && context != DEBUGTOOLS_DIAG_CONTEXT_PREP)
            gKeyStatusPtr->newKeys &= (u16)~mask;
        return;
    }

    DebugToolsDiagnostics_SetSessionContext(context);
    result = DebugTools_OpenHub();
    if (result != DEBUGTOOLS_OK)
        DebugToolsDiagnostics_ClearSessionContext();
}

void DebugTools_ForceSessionCleanup(void)
{
#ifndef FE8_ARCHIVAL_BUILD
    DebugTools_CleanupMusicPreview();
#endif

    if (!(sDebugMenuState & DEBUGTOOLS_STATE_SESSION_ACTIVE))
        return;

    if (sDebugMenuState & DEBUGTOOLS_STATE_TRANSITION_SCHEDULED)
    {
        ProcPtr transition = Proc_Find(gProcScr_DebugToolsMenuTransition);

        if (transition != NULL)
            Proc_End(transition);
    }

    DebugToolsDiagnostics_ForceCloseSession();
    gLCDControlBuffer.dispcnt.bg2_on = 0;
    sHubActive = 0;
    sHubPage = 0;
    sDebugMenuState &= DEBUGTOOLS_STATE_BUILTINS_INITIALIZED;
    DebugToolsDiagnostics_ClearSessionContext();
}

void DebugTools_TitleHotkeyCheck(void)
{
    u16 mask = FE8_EXPANSION_DEBUGTOOLS_HOTKEY_MASK;

    /* Triggers exactly once, on the frame the combo completes (at least
     * one of the mask's buttons must be newly pressed this frame, while
     * every bit of the mask is currently held) -- not every frame the
     * combo stays held. Safe to call unconditionally whether or not the
     * hub is already open: DebugTools_OpenHub() itself is the
     * authoritative reentrancy guard (returns DEBUGTOOLS_ERR_ALREADY_ACTIVE,
     * a no-op, rather than starting a second concurrent MenuProc). */
    if ((gKeyStatusPtr->heldKeys & mask) == mask && (gKeyStatusPtr->newKeys & mask) != 0)
        DebugTools_TryOpenFromContext(DEBUGTOOLS_DIAG_CONTEXT_TITLE);
}

/* Issue #11 slice 2: map-phase and prep-screen hub entry points. Same
 * edge-detected combo check and same DebugTools_OpenHub() reentrancy
 * guard as DebugTools_TitleHotkeyCheck above -- only the mask and the
 * one call site each is read from differ. */
void DebugTools_MapHotkeyCheck(void)
{
    u16 mask = FE8_EXPANSION_DEBUGTOOLS_MAP_HOTKEY_MASK;

#if defined(FE8_DEBUGTOOLS_DIAGNOSTICS_RUNTIME_TEST)
    DebugToolsDiagnostics_RuntimeTestMap();
    DebugToolsHub_StartRuntimeTestViews();
    if (gDebugToolsDiagnosticsProbe.viewRuntimeComplete)
        gDebugToolsDiagnosticsProbe.postViewMapIdleCount++;
    {
        u16 forceMask = SELECT_BUTTON | START_BUTTON;

        if (DebugTools_IsHubActive()
            && (gKeyStatusPtr->heldKeys & forceMask) == forceMask
            && (gKeyStatusPtr->newKeys & forceMask) != 0)
        {
            DebugToolsDiagnostics_ForceCloseSession();
            return;
        }
    }
#endif

    if ((gKeyStatusPtr->heldKeys & mask) == mask && (gKeyStatusPtr->newKeys & mask) != 0)
        DebugTools_TryOpenFromContext(DEBUGTOOLS_DIAG_CONTEXT_MAP);
}

void DebugTools_PrepHotkeyCheck(void)
{
    u16 mask = FE8_EXPANSION_DEBUGTOOLS_PREP_HOTKEY_MASK;

#if defined(FE8_DEBUGTOOLS_DIAGNOSTICS_RUNTIME_TEST)
    DebugToolsDiagnostics_RuntimeTestPrep();
#endif

    if ((gKeyStatusPtr->heldKeys & mask) == mask && (gKeyStatusPtr->newKeys & mask) != 0)
    {
        /* Issue #11 closure: gPlaySt.chapterStateBits & PLAY_FLAG_PREPSCREEN
         * (include/types.h) is set by the real engine prep-screen
         * lifecycle (InitPrepScreenUnitsAndCamera, src/prep_sallycursor.c)
         * for as long as a genuine PrepScreenProc (gProcScr_SALLYCURSOR)
         * is active. Observing it set at the exact moment this hotkey
         * fires is concrete, host/runtime-provable evidence that the
         * debug hub was opened while a real, live prep screen was
         * running -- not merely that this call site exists and is
         * reachable. See gDebugToolsProbe.prepScreenObservedCount. */
        if (gPlaySt.chapterStateBits & PLAY_FLAG_PREPSCREEN)
            gDebugToolsProbe.prepScreenObservedCount++;

        DebugTools_TryOpenFromContext(DEBUGTOOLS_DIAG_CONTEXT_PREP);
    }
}

void DebugTools_RecordTitleIdleTimer(u32 timerIdle)
{
    gDebugToolsProbe.titleIdleTimerSample = timerIdle;
}

#else /* !FE8_EXPANSION_DEBUGTOOLS_ENABLED */

int DebugTools_RegisterAction(const struct DebugToolsAction* action)
{
    (void)action;

    gDebugToolsProbe.lastRegisterResult = DEBUGTOOLS_ERR_DISABLED;

    return DEBUGTOOLS_ERR_DISABLED;
}

int DebugTools_GetRegisteredCount(void)
{
    return 0;
}

const struct DebugToolsAction* DebugTools_GetRegisteredAction(int index)
{
    (void)index;

    return NULL;
}

enum DebugToolsResult DebugTools_GetLastRegistrationResult(void)
{
    return DEBUGTOOLS_ERR_DISABLED;
}

enum DebugToolsResult DebugTools_OpenHub(void)
{
    /* No-op: no hub, no menu construction, nothing reachable. */
    return DEBUGTOOLS_ERR_DISABLED;
}

void DebugTools_QueueSubmenuTransition(struct MenuProc* menu, const struct MenuDef* menuDef)
{
    (void)menu;
    (void)menuDef;
}

void DebugTools_ReturnToHubAfterMenuEnd(struct MenuProc* menu)
{
    (void)menu;
}

int DebugTools_IsHubActive(void)
{
    return 0;
}

void DebugTools_ForceSessionCleanup(void)
{
}

void DebugTools_TitleHotkeyCheck(void)
{
    /* No-op: the hotkey is never read in a release build. */
}

void DebugTools_MapHotkeyCheck(void)
{
    /* No-op: the hotkey is never read in a release build. */
}

void DebugTools_PrepHotkeyCheck(void)
{
    /* No-op: the hotkey is never read in a release build. */
}

void DebugTools_RecordTitleIdleTimer(u32 timerIdle)
{
    /* No-op: gDebugToolsProbe.titleIdleTimerSample stays 0 for the whole
     * release-build run, same as every other probe field. */
    (void)timerIdle;
}

#endif /* FE8_EXPANSION_DEBUGTOOLS_ENABLED */

#endif /* MODERN */
