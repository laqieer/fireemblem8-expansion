#ifndef GUARD_EXPANSION_DEBUGTOOLS_H
#define GUARD_EXPANSION_DEBUGTOOLS_H

/*
 * Issue #11 slice 1 -- supported debug-tools foundation.
 *
 * This header is the single public contract for the new debug-tools
 * subsystem: a release-safe config gate, a fixed-capacity contributor
 * action-registration API that fits the existing MenuProc engine
 * (include/uimenu.h), and the title-screen hub hotkey. See
 * docs/debugtools.md for the full design rationale and safety
 * boundaries.
 *
 * This slice intentionally does NOT touch src/bmdebug.c, src/uidebug.c,
 * or src/menu_def.c -- those dormant tools stay unreachable. Contributors
 * add debug actions exclusively through DebugTools_RegisterAction(); they
 * must never edit an engine-owned MenuItemDef table to add one.
 */

#include "uimenu.h"

/* --- Master gate ---------------------------------------------------------
 * Mirrors include/expansion_config.h's own FE8_EXPANSION_DEBUG/NDEBUG
 * convention exactly (issue #8): a supported modern debug build compiles
 * with FE8_EXPANSION_DEBUG=1 and therefore enables this subsystem by
 * default; a supported modern release build compiles with NDEBUG (hence
 * FE8_EXPANSION_DEBUG=0) and therefore physically omits every debug-tools
 * behavior/data body (see the "#if FE8_EXPANSION_DEBUGTOOLS_ENABLED"
 * guards in src/debugtools_registry.c and src/debugtools_launcher.c) down
 * to trivial disabled-result stubs, with no hotkey/menu reachable. The
 * legacy agbcc build never defines NDEBUG, so it keeps compiling with the
 * subsystem enabled, same as today's other FE8_EXPANSION_* gates -- this
 * is not a new or contradictory release model. */
#ifndef FE8_EXPANSION_DEBUGTOOLS_ENABLED
#define FE8_EXPANSION_DEBUGTOOLS_ENABLED FE8_EXPANSION_DEBUG
#endif

/* --- Title-screen hub hotkey ----------------------------------------------
 * Single global hub entry path for this slice (see Title_IDLE in
 * src/titlescreen.c, the only title-screen-only idle call site). Default
 * is SELECT+R, overridable via a build define. */
#ifndef FE8_EXPANSION_DEBUGTOOLS_HOTKEY_MASK
#define FE8_EXPANSION_DEBUGTOOLS_HOTKEY_MASK (SELECT_BUTTON | R_BUTTON)
#endif

/* Compile-time guardrails (checked unconditionally, even in release
 * builds, so a misconfigured override is caught at compile time rather
 * than silently shipped): the hotkey mask must be a real, nonzero combo,
 * and it must not exactly equal either hardcoded soft-reset combo already
 * checked every frame by SoftResetIfKeyComboPressed (src/hardware.c). A
 * collision would make the debug hub indistinguishable from -- or would
 * itself trigger -- a console soft reset. */
#if (FE8_EXPANSION_DEBUGTOOLS_HOTKEY_MASK) == 0
#error "FE8_EXPANSION_DEBUGTOOLS_HOTKEY_MASK must not be 0 (see docs/debugtools.md)"
#endif
#if (FE8_EXPANSION_DEBUGTOOLS_HOTKEY_MASK) == (L_BUTTON | R_BUTTON | A_BUTTON | B_BUTTON)
#error "FE8_EXPANSION_DEBUGTOOLS_HOTKEY_MASK collides with the L+R+A+B soft-reset combo"
#endif
#if (FE8_EXPANSION_DEBUGTOOLS_HOTKEY_MASK) == (A_BUTTON | B_BUTTON | SELECT_BUTTON | START_BUTTON)
#error "FE8_EXPANSION_DEBUGTOOLS_HOTKEY_MASK collides with the A+B+SELECT+START soft-reset combo"
#endif

/* --- Registration capacity -------------------------------------------------
 * MENU_ITEM_MAX is 11 (include/uimenu.h) and StartMenuCore (src/uimenu.c)
 * has no bounds check when it appends to MenuProc::menuItems -- writing an
 * 12th live item would corrupt adjacent MenuProc fields. The hub menu
 * therefore reserves one live slot for a Back/Exit entry, leaving exactly
 * DEBUGTOOLS_ACTION_MAX (9) slots for contributor actions, with 1 of the
 * 11 total live slots kept as an untouched safety margin. The
 * DEBUGTOOLS_HUB_MENU_SLOTS def-array additionally needs a MenuItemsEnd
 * terminator, which StartMenuCore's scan loop stops at and therefore never
 * turns into a live MenuItemProc/menuItems[] slot. */
enum
{
    DEBUGTOOLS_ACTION_MAX = 9,
    DEBUGTOOLS_HUB_MENU_SLOTS = DEBUGTOOLS_ACTION_MAX + 2 /* actions + Back + terminator */
};

/* Explicit result/error codes -- DebugTools_RegisterAction() always
 * returns one of these; registration failures are never silently
 * dropped. */
enum DebugToolsResult
{
    DEBUGTOOLS_OK = 0,
    DEBUGTOOLS_ERR_DISABLED,        /* subsystem compiled out (release build) */
    DEBUGTOOLS_ERR_INVALID_ACTION,  /* NULL action, label, or callback */
    DEBUGTOOLS_ERR_DUPLICATE,       /* id already registered */
    DEBUGTOOLS_ERR_CAPACITY_FULL,   /* DEBUGTOOLS_ACTION_MAX already reached */
    DEBUGTOOLS_ERR_ALREADY_ACTIVE   /* DebugTools_OpenHub called while the hub is already open */
};

/* A single contributor-registered debug action. label is a raw C string
 * (no msg-id/text-asset system involved -- see MenuItemDef::name /
 * Text_DrawString in src/uimenu.c, which already draws nameMsgId==0 items
 * directly from a raw string). onSelected has the exact MenuItemDef
 * onSelected signature, so a registered action can drive the hub menu
 * (e.g. return MENU_ACT_END | MENU_ACT_CLEAR | ... ) exactly like any
 * other MenuItemDef handler. */
struct DebugToolsAction
{
    u16 id;                 /* stable, contributor-chosen identifier */
    const char* label;
    u8 (*onSelected)(struct MenuProc* menu, struct MenuItemProc* item);
};

/* Registers a new contributor debug action. Returns DEBUGTOOLS_OK on
 * success, or an explicit DebugToolsResult error code otherwise. Never
 * silently drops a registration. Registration order is preserved
 * (deterministic ordering), and no heap allocation is used anywhere in
 * this subsystem. */
int DebugTools_RegisterAction(const struct DebugToolsAction* action);

/* Introspection, primarily for host tests and playtest probes. */
int DebugTools_GetRegisteredCount(void);
const struct DebugToolsAction* DebugTools_GetRegisteredAction(int index);
enum DebugToolsResult DebugTools_GetLastRegistrationResult(void);

/* Opens the debug hub menu (a StartOrphanMenu-based menu, same idiom as
 * the existing dormant debug menus in src/menu_def.c). Lazily registers
 * the slice's one built-in launcher action on first call.
 *
 * This is the single authoritative reentrancy guard for the whole
 * subsystem: it returns DEBUGTOOLS_ERR_ALREADY_ACTIVE (an explicit,
 * observable no-op -- no menu construction, no second StartOrphanMenu,
 * gDebugToolsProbe.hubOpenCount left unchanged) if the hub is already
 * open. A release-and-repress of the title hotkey while the hub remains
 * open must never spawn a second concurrent MenuProc; guarding here
 * (rather than in each caller) protects every current and future caller
 * (DebugTools_TitleHotkeyCheck today, any later map/prep entry point)
 * without each needing its own check. Returns DEBUGTOOLS_ERR_DISABLED
 * (and remains a no-op) when the subsystem is compiled out. */
enum DebugToolsResult DebugTools_OpenHub(void);

/* True from the frame the hub opens until the frame it ends (Back, a
 * built-in action's own MENU_ACT_END, or the launcher tearing down the
 * whole gProcScr_GameControl tree the hub itself lives in -- see
 * docs/debugtools.md "Title tree ownership"). Title_IDLE (src/titlescreen.c)
 * checks this to skip its own A/START handling for the whole time the hub
 * is up: the hub's menu proc and Title_IDLE are independent sibling procs
 * under the same gProcScr_GameControl tree that both still read newKeys
 * every frame, so without this guard a single A press meant to select a
 * hub action would also be seen -- on the same frame -- by Title_IDLE's
 * own unconditional newKeys check, racing the vanilla title-to-gameplay
 * transition against the hub's own action. Always returns 0 when the
 * subsystem is compiled out. */
int DebugTools_IsHubActive(void);

/* Call once per frame from the single supported title-screen-only call
 * site (Title_IDLE in src/titlescreen.c). Checks the hotkey combo and
 * opens the hub when it completes. Compiles to an empty stub (no key
 * read, no hub, nothing reachable) when the subsystem is disabled. */
void DebugTools_TitleHotkeyCheck(void);

/* Registers the slice's one built-in deterministic launcher action
 * (implemented in src/debugtools_launcher.c). Idempotent -- safe to call
 * more than once. */
void DebugTools_RegisterBuiltinActions(void);

/* Called once per frame from Title_IDLE (src/titlescreen.c) to mirror its
 * current proc->timer_idle value into gDebugToolsProbe.titleIdleTimerSample
 * -- the stable probe evidence that the idle/attract timer pair is frozen
 * for as long as DebugTools_IsHubActive() is true (Title_IDLE checks hub
 * state before incrementing, not after), and resumes incrementing again,
 * unchanged, once the hub closes. Keeps this module the sole writer of
 * every gDebugToolsProbe field (Title_IDLE calls this setter rather than
 * writing the struct directly). No-op when the subsystem is compiled out
 * (gDebugToolsProbe.titleIdleTimerSample then stays 0 for the whole
 * release-build run, same as every other probe field). */
void DebugTools_RecordTitleIdleTimer(u32 timerIdle);

/* --- Playtest / host probe surface -----------------------------------
 * A small, stable, always-linked (both enabled and disabled builds)
 * diagnostic struct meant to be read directly by address by
 * tools/gba-playtest probes (see docs/debugtools.md). This gives
 * scenarios stable state evidence instead of relying on input playback
 * alone: a release build's gDebugToolsProbe must stay all-zero for the
 * whole scenario (hub never opened, no actions ever registered, launcher
 * never armed), while a debug scenario can assert concrete nonzero
 * values at each step of hotkey -> hub -> launcher. This module is the
 * only writer of every field. */
struct DebugToolsProbe
{
    u32 hubOpenCount;
    u32 registeredActionCount;
    u32 lastRegisterResult;  /* enum DebugToolsResult */
    u32 launcherArmed;       /* DEBUGTOOLS_LAUNCHER_ARMED_MAGIC once the
                               * built-in launcher has committed to its
                               * deterministic boot (see
                               * src/debugtools_launcher.c) */
    u32 titleIdleTimerSample; /* mirrors Title_IDLE's proc->timer_idle
                               * every frame (see
                               * DebugTools_RecordTitleIdleTimer) --
                               * identical values across a widely-spaced
                               * pair of checkpoints while the hub is open
                               * is the stable evidence that the idle/
                               * attract timer is frozen, not silently
                               * advancing, while the hub is active */
};

enum
{
    DEBUGTOOLS_LAUNCHER_ARMED_MAGIC = 0x44424C31 /* ASCII "DBL1" */
};

extern struct DebugToolsProbe gDebugToolsProbe;

#endif /* GUARD_EXPANSION_DEBUGTOOLS_H */
