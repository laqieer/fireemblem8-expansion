#include "global.h"

#include <string.h>
#include <stdio.h>

#include "hardware.h"
#include "fontgrp.h"
#include "proc.h"
#include "uimenu.h"
#include "expansion_debugtools.h"
#include "debugtools_internal.h"

#ifdef MODERN
/* Issue #18 sprint 3: render-time-only ExpansionMsgId mapping for the
 * nine title/map/prep-reachable *builtin* action ids (1-9, see
 * src/debugtools_launcher.c/src/debugtools_actions.c/
 * src/debugtools_tools.c) -- purely additive to how those specific
 * rows are *drawn*; struct DebugToolsAction/DebugToolsResult/
 * DebugTools_RegisterAction's ABI and every registered action's own
 * `id`/`label`/`onSelected` fields are completely untouched. Any
 * third-party/unmapped id (0, or >9) keeps the exact original
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

EWRAM_DATA static struct DebugToolsAction sActions[DEBUGTOOLS_ACTION_MAX] = {0};
EWRAM_DATA static int sActionCount = 0;
EWRAM_DATA static enum DebugToolsResult sLastResult = DEBUGTOOLS_OK;
EWRAM_DATA static u32 sDebugMenuState = 0;

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
    /* 30 */ struct Font* font;
    /* 34 */ u16 textBase;
    /* 36 */ u8 target;
};

static void DebugTools_StartMenuTransition(
    struct MenuProc* menu,
    int target,
    const struct MenuDef* menuDef);

/* RAM-resident MenuItemDef adapter (rebuilt from sActions[] every time the
 * hub is opened) -- this is how contributor actions reach the existing
 * MenuProc engine without any contributor ever editing an engine-owned
 * const MenuItemDef table. Sized DEBUGTOOLS_HUB_MENU_SLOTS (actions + one
 * reserved Back entry + one MenuItemsEnd-equivalent terminator); zeroing
 * the whole array before every rebuild guarantees the first unused slot
 * (and everything after it) reads as an all-zero MenuItemsEnd, since
 * isAvailable == NULL is exactly what stops StartMenuCore's scan loop
 * (src/uimenu.c). */
EWRAM_DATA static struct MenuItemDef sHubMenuItemDefs[DEBUGTOOLS_HUB_MENU_SLOTS] = {0};

static u8 DebugToolsHub_BackSelected(struct MenuProc* menu, struct MenuItemProc* item)
{
    return MenuCancelSelect(menu, item);
}

static void DebugToolsHub_OnEnd(struct MenuProc* proc)
{
    /* Restore bg2 to the off state Title_EnableMainScreenDisplay left it
     * in (src/titlescreen.c) -- our diagnostics line is the only thing
     * that ever turns it on while the hub is the active menu. */
    gLCDControlBuffer.dispcnt.bg2_on = 0;

    sDebugMenuState &= ~DEBUGTOOLS_STATE_HUB_ACTIVE;

    if (!(sDebugMenuState & DEBUGTOOLS_STATE_TRANSITION_SCHEDULED))
        DebugTools_StartMenuTransition(proc, DEBUGTOOLS_TRANSITION_CLEANUP, NULL);
}

CONST_DATA struct MenuDef gDebugToolsHubMenuDef = {
    {1, 1, DEBUGTOOLS_MENU_WIDTH_TILES, 0},
    0,
    sHubMenuItemDefs,
    0,
    DebugToolsHub_OnEnd,
    0,
    MenuCancelSelect,
    0,
    0
};

#ifdef MODERN
/* Parallel-indexed by builtin action id (1-9); id 0 is never a real
 * action (see DEBUGTOOLS_ERR_ID_INVALID), so slot 0 is an unused
 * placeholder. Every entry here is one of the nine builtin ids -- a
 * contributor/third-party registration always uses some other id and
 * therefore is never looked up in this table (see
 * DebugToolsHub_ResolveBuiltinLabelMsgId below). */
static const ExpansionMsgId sBuiltinActionLabelMsgIds[DEBUGTOOLS_ACTION_MAX + 1] =
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
};

/* Returns EXPANSION_MSG_ID_INVALID for any id outside the builtin
 * 1-9 range (every third-party/contributor id included) -- never an
 * out-of-bounds table read. */
static ExpansionMsgId DebugToolsHub_ResolveBuiltinLabelMsgId(int index)
{
    u16 id;

    id = sActions[index].id;

    if (id < DEBUGTOOLS_BUILTIN_ID_MIN || id > DEBUGTOOLS_BUILTIN_ID_MAX)
        return EXPANSION_MSG_ID_INVALID;

    return sBuiltinActionLabelMsgIds[id];
}

static int DebugToolsHub_UsesCjkText(void)
{
    ExpansionLocaleId locale = ExpansionLocale_GetCurrent();

    return locale == EXPANSION_LOCALE_JA || locale == EXPANSION_LOCALE_ZH_HANS;
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
#endif /* MODERN */

static void DebugToolsHub_BuildMenuItems(void)
{
    int i;

    memset(sHubMenuItemDefs, 0, sizeof(sHubMenuItemDefs));

    for (i = 0; i < sActionCount; ++i)
    {
        struct MenuItemDef* def = &sHubMenuItemDefs[i];
#ifdef MODERN
        ExpansionMsgId builtinMsgId = DebugToolsHub_ResolveBuiltinLabelMsgId(i);
#endif

        def->name = sActions[i].label;
        def->nameMsgId = 0;
        def->helpMsgId = 0;
        def->color = 0;
        def->overrideId = 0;
        def->isAvailable = MenuAlwaysEnabled;
        def->onDraw = NULL;
        def->onSelected = sActions[i].onSelected;
        def->onIdle = NULL;
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
    sHubMenuItemDefs[sActionCount].name = "Back";
#ifdef MODERN
    sHubMenuItemDefs[sActionCount].helpMsgId = EXP_MSG_FRAMEWORK_BACK;
    sHubMenuItemDefs[sActionCount].onDraw =
        DebugToolsHub_BuiltinActionRowDraw;
#endif
    sHubMenuItemDefs[sActionCount].isAvailable = MenuAlwaysEnabled;
    sHubMenuItemDefs[sActionCount].onSelected = DebugToolsHub_BackSelected;

    /* sHubMenuItemDefs[sActionCount + 1] stays all-zero: the terminator. */
}

static void DebugToolsHub_ShowDiagnostics(void)
{
    char buf[64];

#ifdef MODERN
    if (sLastResult != DEBUGTOOLS_OK)
        sprintf(buf, "%s %d",
            ExpansionLocale_ResolveCurrent(EXP_MSG_DEBUG_STATUS_HUB_ERROR),
            (int)sLastResult);
    else
        sprintf(buf, "%s %d/%d",
            ExpansionLocale_ResolveCurrent(EXP_MSG_DEBUG_STATUS_HUB),
            sActionCount, DEBUGTOOLS_ACTION_MAX);

    if (DebugToolsHub_UsesCjkText())
    {
        BG_Fill(BG_GetMapBuffer(2), 0);
        PutDrawText(
            NULL,
            BG_GetMapBuffer(2) + TILEMAP_INDEX(1, 1),
            TEXT_COLOR_SYSTEM_WHITE,
            0,
            DEBUGTOOLS_STATUS_TEXT_WIDTH_TILES,
            buf);
        BG_EnableSyncByMask(BG2_SYNC_BIT);
        gLCDControlBuffer.dispcnt.bg2_on = 1;
        return;
    }
#else
    if (sLastResult != DEBUGTOOLS_OK)
        sprintf(buf, "DBGTOOLS ERR %d", (int)sLastResult);
    else
        sprintf(buf, "DBGTOOLS %d/%d", sActionCount, DEBUGTOOLS_ACTION_MAX);
#endif

    SetupDebugFontForBG(2, 0);
    PrintDebugStringToBG(BG_GetMapBuffer(2) + TILEMAP_INDEX(1, 1), buf);

    gLCDControlBuffer.dispcnt.bg2_on = 1;
}

static int DebugTools_SetLastResult(enum DebugToolsResult result)
{
    sLastResult = result;
    gDebugToolsProbe.lastRegisterResult = result;
    return result;
}

static int DebugTools_RegisterActionCore(const struct DebugToolsAction* action, int isBuiltin)
{
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
     * sActions[sActionCount] = *action below) -- contributors are
     * responsible for passing a label with static/persistent storage
     * duration (every action in this file uses a plain string literal,
     * which always satisfies this); this length check is a rendering/
     * policy bound, not a lifetime check C89 can perform at runtime. */
    if (action->label[0] == '\0' || strlen(action->label) > DEBUGTOOLS_LABEL_MAX_LENGTH)
        return DebugTools_SetLastResult(DEBUGTOOLS_ERR_LABEL_INVALID);

    for (i = 0; i < sActionCount; ++i)
    {
        if (sActions[i].id == action->id || strcmp(sActions[i].label, action->label) == 0)
            return DebugTools_SetLastResult(DEBUGTOOLS_ERR_DUPLICATE);
    }

    if (sActionCount >= DEBUGTOOLS_ACTION_MAX)
        return DebugTools_SetLastResult(DEBUGTOOLS_ERR_CAPACITY_FULL);

    sActions[sActionCount] = *action;
    sActionCount++;

    gDebugToolsProbe.registeredActionCount = (u32)sActionCount;

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
    return sActionCount;
}

const struct DebugToolsAction* DebugTools_GetRegisteredAction(int index)
{
    if (index < 0 || index >= sActionCount)
        return NULL;

    return &sActions[index];
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

static u16 DebugTools_GetMenuTextBase(struct MenuProc* menu)
{
    if (menu != NULL && menu->itemCount != 0 && menu->menuItems[0] != NULL)
        return menu->menuItems[0]->text.chr_position;

    return gActiveFont == NULL ? 0 : gActiveFont->chr_counter;
}

static void DebugTools_StartMenuTransition(
    struct MenuProc* menu,
    int target,
    const struct MenuDef* menuDef)
{
    struct DebugToolsMenuTransitionProc* proc;

    if (sDebugMenuState & DEBUGTOOLS_STATE_TRANSITION_SCHEDULED)
        return;

    proc = Proc_Start(gProcScr_DebugToolsMenuTransition, PROC_TREE_3);
    proc->menuDef = menuDef;
    proc->font = gActiveFont;
    proc->textBase = DebugTools_GetMenuTextBase(menu);
    proc->target = target;

    sDebugMenuState |= DEBUGTOOLS_STATE_TRANSITION_SCHEDULED;
}

void DebugTools_QueueSubmenuTransition(struct MenuProc* menu, const struct MenuDef* menuDef)
{
    if (menuDef == NULL)
        return;

    DebugTools_StartMenuTransition(menu, DEBUGTOOLS_TRANSITION_SUBMENU, menuDef);
}

void DebugTools_ReturnToHubAfterMenuEnd(struct MenuProc* menu)
{
    if (sDebugMenuState
        & (DEBUGTOOLS_STATE_HUB_ACTIVE | DEBUGTOOLS_STATE_TRANSITION_SCHEDULED))
        return;

    DebugTools_StartMenuTransition(menu, DEBUGTOOLS_TRANSITION_HUB, NULL);
}

static enum DebugToolsResult DebugTools_OpenHubInternal(void)
{
    DebugToolsHub_BuildMenuItems();
    gDebugToolsProbe.hubOpenCount++;
    sDebugMenuState |= DEBUGTOOLS_STATE_HUB_ACTIVE;

#ifdef MODERN
    if (DebugToolsHub_UsesCjkText())
    {
        StartOrphanMenu(&gDebugToolsHubMenuDef);
        DebugToolsHub_ShowDiagnostics();
        return DEBUGTOOLS_OK;
    }
#endif

    DebugToolsHub_ShowDiagnostics();
    StartOrphanMenu(&gDebugToolsHubMenuDef);

    return DEBUGTOOLS_OK;
}

void DebugTools_RunMenuTransition(ProcPtr proc)
{
    struct DebugToolsMenuTransitionProc* transition = proc;
    const struct MenuDef* submenuDef = transition->menuDef;
    int target = transition->target;

    sDebugMenuState &= ~DEBUGTOOLS_STATE_TRANSITION_SCHEDULED;

    if (transition->font != NULL)
    {
        gActiveFont = transition->font;
        transition->font->chr_counter = transition->textBase;
    }

    if (target == DEBUGTOOLS_TRANSITION_SUBMENU && submenuDef != NULL)
    {
        StartOrphanMenu(submenuDef);
        return;
    }

    if (target == DEBUGTOOLS_TRANSITION_HUB)
    {
        DebugTools_OpenHubInternal();
        return;
    }

    sDebugMenuState &= ~(DEBUGTOOLS_STATE_SESSION_ACTIVE | DEBUGTOOLS_STATE_HUB_ACTIVE);
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
     * press cycle, each of which would otherwise call StartOrphanMenu()
     * again. Guarding here -- rather than in each caller -- protects
     * every current and future entry path. */
    if (sDebugMenuState & DEBUGTOOLS_STATE_SESSION_ACTIVE)
        return DEBUGTOOLS_ERR_ALREADY_ACTIVE;

    DebugTools_EnsureBuiltinActionsRegistered();

    if (!DebugTools_HasTextCapacity())
        return DebugTools_SetLastResult(DEBUGTOOLS_ERR_TEXT_CAPACITY);

    sDebugMenuState |= DEBUGTOOLS_STATE_SESSION_ACTIVE;
    return DebugTools_OpenHubInternal();
}

int DebugTools_IsHubActive(void)
{
    return sDebugMenuState & DEBUGTOOLS_STATE_SESSION_ACTIVE;
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
        DebugTools_OpenHub();
}

/* Issue #11 slice 2: map-phase and prep-screen hub entry points. Same
 * edge-detected combo check and same DebugTools_OpenHub() reentrancy
 * guard as DebugTools_TitleHotkeyCheck above -- only the mask and the
 * one call site each is read from differ. */
void DebugTools_MapHotkeyCheck(void)
{
    u16 mask = FE8_EXPANSION_DEBUGTOOLS_MAP_HOTKEY_MASK;

    if ((gKeyStatusPtr->heldKeys & mask) == mask && (gKeyStatusPtr->newKeys & mask) != 0)
        DebugTools_OpenHub();
}

void DebugTools_PrepHotkeyCheck(void)
{
    u16 mask = FE8_EXPANSION_DEBUGTOOLS_PREP_HOTKEY_MASK;

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

        DebugTools_OpenHub();
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

int DebugTools_IsHubActive(void)
{
    return 0;
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
