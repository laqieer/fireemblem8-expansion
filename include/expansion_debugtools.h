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

/* --- Map-phase and prep-screen hub hotkeys --------------------------------
 * Issue #11 slice 2. Separate, independently overridable masks -- one
 * per entry point -- called from the single supported map-phase call
 * site (PlayerPhase_MainIdle, src/playerphase.c) and the single
 * supported prep-screen call site (PrepScreenProc_MapIdle,
 * src/prep_sallycursor.c). Both phases already read bare L_BUTTON,
 * R_BUTTON, and (ungated at the prep screen) START_BUTTON for their own
 * vanilla controls (view-unit swap, stat screen, minimap), so each
 * default below is a SELECT-qualified two-button combo distinct from
 * every one of those bare single-button reads, from the title-screen
 * mask above, and from each other. */
#ifndef FE8_EXPANSION_DEBUGTOOLS_MAP_HOTKEY_MASK
#define FE8_EXPANSION_DEBUGTOOLS_MAP_HOTKEY_MASK (SELECT_BUTTON | L_BUTTON)
#endif

#ifndef FE8_EXPANSION_DEBUGTOOLS_PREP_HOTKEY_MASK
#define FE8_EXPANSION_DEBUGTOOLS_PREP_HOTKEY_MASK (SELECT_BUTTON | B_BUTTON)
#endif

/* Same unconditional compile-time guardrails as the title mask above,
 * plus mutual-distinctness checks: no two of the three masks may be
 * equal (a shared combo would make two different debug-hub entry points
 * indistinguishable), and neither new mask may equal a single bare
 * R/L/START read already live in the phase it gates. */
#if (FE8_EXPANSION_DEBUGTOOLS_MAP_HOTKEY_MASK) == 0
#error "FE8_EXPANSION_DEBUGTOOLS_MAP_HOTKEY_MASK must not be 0 (see docs/debugtools.md)"
#endif
#if (FE8_EXPANSION_DEBUGTOOLS_MAP_HOTKEY_MASK) == (L_BUTTON | R_BUTTON | A_BUTTON | B_BUTTON)
#error "FE8_EXPANSION_DEBUGTOOLS_MAP_HOTKEY_MASK collides with the L+R+A+B soft-reset combo"
#endif
#if (FE8_EXPANSION_DEBUGTOOLS_MAP_HOTKEY_MASK) == (A_BUTTON | B_BUTTON | SELECT_BUTTON | START_BUTTON)
#error "FE8_EXPANSION_DEBUGTOOLS_MAP_HOTKEY_MASK collides with the A+B+SELECT+START soft-reset combo"
#endif
#if (FE8_EXPANSION_DEBUGTOOLS_MAP_HOTKEY_MASK) == (R_BUTTON)
#error "FE8_EXPANSION_DEBUGTOOLS_MAP_HOTKEY_MASK collides with bare R at the map phase (stat screen)"
#endif
#if (FE8_EXPANSION_DEBUGTOOLS_MAP_HOTKEY_MASK) == (L_BUTTON)
#error "FE8_EXPANSION_DEBUGTOOLS_MAP_HOTKEY_MASK collides with bare L at the map phase (view-unit swap)"
#endif
#if (FE8_EXPANSION_DEBUGTOOLS_MAP_HOTKEY_MASK) == (START_BUTTON)
#error "FE8_EXPANSION_DEBUGTOOLS_MAP_HOTKEY_MASK collides with bare START at the map phase (minimap)"
#endif
#if (FE8_EXPANSION_DEBUGTOOLS_MAP_HOTKEY_MASK) == (FE8_EXPANSION_DEBUGTOOLS_HOTKEY_MASK)
#error "FE8_EXPANSION_DEBUGTOOLS_MAP_HOTKEY_MASK collides with the title-screen hub mask"
#endif

#if (FE8_EXPANSION_DEBUGTOOLS_PREP_HOTKEY_MASK) == 0
#error "FE8_EXPANSION_DEBUGTOOLS_PREP_HOTKEY_MASK must not be 0 (see docs/debugtools.md)"
#endif
#if (FE8_EXPANSION_DEBUGTOOLS_PREP_HOTKEY_MASK) == (L_BUTTON | R_BUTTON | A_BUTTON | B_BUTTON)
#error "FE8_EXPANSION_DEBUGTOOLS_PREP_HOTKEY_MASK collides with the L+R+A+B soft-reset combo"
#endif
#if (FE8_EXPANSION_DEBUGTOOLS_PREP_HOTKEY_MASK) == (A_BUTTON | B_BUTTON | SELECT_BUTTON | START_BUTTON)
#error "FE8_EXPANSION_DEBUGTOOLS_PREP_HOTKEY_MASK collides with the A+B+SELECT+START soft-reset combo"
#endif
#if (FE8_EXPANSION_DEBUGTOOLS_PREP_HOTKEY_MASK) == (R_BUTTON)
#error "FE8_EXPANSION_DEBUGTOOLS_PREP_HOTKEY_MASK collides with bare R at the prep screen (stat screen)"
#endif
#if (FE8_EXPANSION_DEBUGTOOLS_PREP_HOTKEY_MASK) == (L_BUTTON)
#error "FE8_EXPANSION_DEBUGTOOLS_PREP_HOTKEY_MASK collides with bare L at the prep screen (view-unit swap)"
#endif
#if (FE8_EXPANSION_DEBUGTOOLS_PREP_HOTKEY_MASK) == (START_BUTTON)
#error "FE8_EXPANSION_DEBUGTOOLS_PREP_HOTKEY_MASK collides with bare START at the prep screen (minimap)"
#endif
#if (FE8_EXPANSION_DEBUGTOOLS_PREP_HOTKEY_MASK) == (FE8_EXPANSION_DEBUGTOOLS_HOTKEY_MASK)
#error "FE8_EXPANSION_DEBUGTOOLS_PREP_HOTKEY_MASK collides with the title-screen hub mask"
#endif
#if (FE8_EXPANSION_DEBUGTOOLS_PREP_HOTKEY_MASK) == (FE8_EXPANSION_DEBUGTOOLS_MAP_HOTKEY_MASK)
#error "FE8_EXPANSION_DEBUGTOOLS_PREP_HOTKEY_MASK collides with the map-phase hub mask"
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

/* Issue #11 slice 2 -- the single supported map-phase call site
 * (PlayerPhase_MainIdle in src/playerphase.c) and the single supported
 * prep-screen call site (PrepScreenProc_MapIdle in
 * src/prep_sallycursor.c). Each is called as the very first statement of
 * its caller, before any of that function's own vanilla key handling;
 * the caller then checks DebugTools_IsHubActive() and returns immediately
 * if true, so a combo that completes this frame can never also be read,
 * on the same frame, by the vanilla L/R/A/B/START handling further down
 * (both because the hub is now active, and because DebugTools_OpenHub()
 * itself zeroes gKeyStatusPtr->newKeys the instant it starts the hub's
 * MenuProc -- see StartMenuCore, src/uimenu.c). Same reentrancy guard and
 * disabled-build behavior as DebugTools_TitleHotkeyCheck above: safe to
 * call unconditionally every frame regardless of hub state, and a no-op
 * (no key read, nothing reachable) when the subsystem is compiled out. */
void DebugTools_MapHotkeyCheck(void);
void DebugTools_PrepHotkeyCheck(void);

/* Registers the slice's one built-in deterministic launcher action
 * (implemented in src/debugtools_launcher.c). Idempotent -- safe to call
 * more than once. */
void DebugTools_RegisterBuiltinActions(void);

/* Registers this slice's Weather and Fog built-in actions (implemented in
 * src/debugtools_actions.c). Idempotent -- safe to call more than once.
 * Called from DebugTools_OpenHub() (src/debugtools_registry.c) alongside
 * DebugTools_RegisterBuiltinActions() above, so both built-in groups are
 * always registered together regardless of which entry point (title,
 * map, or prep) first opens the hub. A no-op in a release build. */
void DebugTools_RegisterWeatherFogActions(void);

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

/* --- Pending Chapter 2 fast-boot launch request ---------------------------
 * Replaces the earlier "tear down and restart GameCtrlProc from inside a
 * MenuProc callback" launcher (proc-tree lifecycle corruption -- see
 * docs/debugtools.md). The hub action ("Fast Boot: Chapter 2",
 * src/debugtools_launcher.c) now only arms a pending request and closes the
 * hub; it never touches gProcScr_GameControl, gProc_BMapMain, units, or
 * events. The request is detected by Title_IDLE (src/titlescreen.c) only
 * after the hub has closed, and consumed exactly once by
 * GameControl_PostIntro (src/gamecontrol.c), which performs the actual
 * deterministic boot and hands off to the ordinary LGAMECTRL_EXEC_BM
 * transition -- the existing GameCtrlProc's own normal lifecycle runs
 * unmodified throughout. */

/* Arms the one pending debug launch request. Called only from the hub
 * action's own onSelected callback. Idempotent: calling it again while a
 * request is already pending leaves the single pending request unchanged
 * (no queued second launch, no double-arm) -- see
 * gDebugToolsProbe.pendingLaunchRequest for the observable evidence. No-op
 * when the subsystem is compiled out. */
void DebugTools_RequestChapter2Launch(void);

/* True from the frame the hub action arms the request until the frame
 * GameControl_PostIntro consumes it. Checked by Title_IDLE only after
 * DebugTools_IsHubActive() reports the hub has closed -- Title_IDLE itself
 * never clears this flag, it only reacts to it by setting the ordinary
 * GAME_ACTION_EVENT_RETURN next-action and calling Proc_Break on itself
 * (exactly the effect a real A/START press would have, without
 * synthesizing that keypress). Always 0 when the subsystem is compiled
 * out. */
int DebugTools_IsChapter2LaunchPending(void);

/* Consumes the pending request exactly once: returns nonzero and clears
 * the pending flag the first time a request is pending, returns 0 (a
 * no-op, gDebugToolsProbe.launchRequestConsumedCount left unchanged) on
 * every subsequent call until DebugTools_RequestChapter2Launch() arms a new
 * one. Called only from GameControl_PostIntro's GAME_ACTION_EVENT_RETURN
 * case, before its ordinary StartSaveMenu branch. Always 0 when the
 * subsystem is compiled out. */
int DebugTools_ConsumePendingChapter2Launch(void);

/* Arms the one-shot persistent-write suppression that skips
 * BmMain_SuspendBeforePhase's WriteSuspendSave (src/bm.c),
 * PlayerPhase_Suspend's WriteSuspendSave (src/playerphase.c), and
 * UnlockSoundRoomSong's SRAM write (src/soundwrapper.c) for exactly the
 * window between the Chapter 2 fast-boot committing and the bootstrap
 * observer proc (src/debugtools_launcher.c) clearing it (see
 * DebugTools_CleanupBootstrapObserver below for every way that can
 * happen). Also starts that observer proc (a small independent
 * PROC_TREE_3 proc -- it never redirects gProc_BMapMain or any other
 * proc, it only polls state and clears suppression once). Fail-safe
 * singleton: always calls DebugTools_CleanupBootstrapObserver() first, so
 * a repeated call -- whether a genuinely new launch or one arriving while
 * a stale observer/flag is still live from a prior aborted/incomplete
 * boot -- can never leave two observers alive at once, nor leave
 * suppression stuck on. gDebugToolsProbe.bootstrapObserverArmCount
 * increments on every call (explicit, observable evidence that a repeat
 * arm was handled, not silently ignored or silently duplicated). Called
 * only from GameControl_PostIntro, once per consumed launch request,
 * immediately after DebugTools_ConsumePendingChapter2Launch() returns
 * nonzero. No-op when the subsystem is compiled out. */
void DebugTools_ArmBootstrapSuppression(void);

/* Explicit, idempotent, synchronous cleanup: ends the bootstrap observer
 * proc (if one is currently alive -- a plain no-op otherwise) and clears
 * the one-shot suppression flag. This is the fail-safe half of the
 * lifecycle: the observer itself already calls the equivalent cleanup on
 * every one of its own termination paths --
 *   1. success (first stable Player Phase seen),
 *   2. abandoned/failed run (DebugTools_NotifyTitleScreenStarting below
 *      reports the title screen (re)starting while suppression is still
 *      active, meaning the run returned to title before a stable Player
 *      Phase was ever reached -- normal Chapter 2 flow never re-enters
 *      the title screen), or
 *   3. a bounded timeout (DEBUGTOOLS_BOOTSTRAP_OBSERVER_TIMEOUT_FRAMES in
 *      src/debugtools_launcher.c, set comfortably above any normal
 *      Chapter 2 boot's own arm-to-interactive window) --
 * so suppression can never outlive the observer through any of those
 * paths either. This function additionally lets DebugTools_
 * ArmBootstrapSuppression() guarantee its own singleton property (see
 * above), and gives host tests/tooling an explicit call site to force
 * cleanup deterministically without waiting on any of the three
 * conditions. No-op when the subsystem is compiled out. */
void DebugTools_CleanupBootstrapObserver(void);

/* True for as long as the bootstrap suppression above is armed. Checked by
 * the narrow guards in BmMain_SuspendBeforePhase (src/bm.c),
 * PlayerPhase_Suspend (src/playerphase.c), and UnlockSoundRoomSong
 * (src/soundwrapper.c). Always 0 when the subsystem is compiled out or
 * once the bootstrap observer has cleared it -- ordinary user-triggered
 * suspend saves and song-room unlocks are never suppressed outside this
 * narrow one-shot boot window. */
int DebugTools_IsBootstrapSuppressionActive(void);

/* Called only from src/titlescreen.c's StartTitleScreen_WithMusic,
 * StartTitleScreen_FlagFalse, and StartTitleScreen_FlagTrue -- the three
 * (and only) places gProcScr_TitleScreen is ever (re)started as a
 * blocking child of gProcScr_GameControl. Normal Chapter 2 flow (event ->
 * world map -> chapter-intro event -> battle map -> NPC phase -> Player
 * Phase) never re-enters the title screen, so any one of these three
 * being called while the bootstrap suppression above is still active
 * means the run was abandoned/returned to title before a stable Player
 * Phase was ever reached (soft reset, a debug/error path back to
 * LGAMECTRL_TITLE_DIRECT, etc.). A no-op whenever suppression is not
 * currently active (title screen starting normally, e.g. at first boot,
 * is not a "return"). This is a genuine, unambiguous event -- unlike
 * scanning the proc tree for a live gProcScr_TitleScreen instance, which
 * cannot reliably distinguish "the title screen is actually running
 * again" from "a just-freed proc slot's stale proc_script field still
 * happens to equal that pointer" (FreeProcess, src/proc.c, never clears
 * proc_script on free). No-op when the subsystem is compiled out. */
void DebugTools_NotifyTitleScreenStarting(void);

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
    u32 launcherArmed;       /* DEBUGTOOLS_LAUNCHER_ARMED_MAGIC once
                               * GameControl_PostIntro has consumed the
                               * pending request and committed to the
                               * deterministic Chapter 2 boot (see
                               * src/gamecontrol.c) */
    u32 titleIdleTimerSample; /* mirrors Title_IDLE's proc->timer_idle
                               * every frame (see
                               * DebugTools_RecordTitleIdleTimer) --
                               * identical values across a widely-spaced
                               * pair of checkpoints while the hub is open
                               * is the stable evidence that the idle/
                               * attract timer is frozen, not silently
                               * advancing, while the hub is active */
    u32 pendingLaunchRequest;       /* DEBUGTOOLS_LAUNCH_REQUEST_MAGIC from
                                      * the moment the hub action arms the
                                      * request until GameControl_PostIntro
                                      * consumes it (see
                                      * DebugTools_RequestChapter2Launch /
                                      * DebugTools_ConsumePendingChapter2Launch) */
    u32 launchRequestConsumedCount; /* increments exactly once per
                                      * consumed request -- proves
                                      * GameControl_PostIntro never
                                      * double-applies a single arm */
    u32 bootstrapSuppressionActive; /* 1 while the one-shot persistent-
                                      * write suppression armed alongside
                                      * the Chapter 2 boot is active, 0
                                      * once the bootstrap observer proc
                                      * clears it -- via success, a
                                      * detected return to title, an
                                      * explicit DebugTools_
                                      * CleanupBootstrapObserver() call, or
                                      * the bounded timeout (see the three
                                      * counters below) */
    u32 playerPhaseObservedCount;   /* increments once per observer,
                                      * when it detects a stable Player
                                      * Phase and clears suppression (the
                                      * success path) */
    u32 bootstrapObserverArmCount;   /* increments on every
                                      * DebugTools_ArmBootstrapSuppression()
                                      * call, including repeats over a
                                      * still-live or stale observer --
                                      * explicit, observable evidence that
                                      * a repeat arm was handled (cleaned
                                      * up + restarted exactly one fresh
                                      * observer), never silently ignored
                                      * or silently duplicated */
    u32 observerTitleReturnCount;    /* increments once per observer, if
                                      * it ends via
                                      * DebugTools_NotifyTitleScreenStarting
                                      * reporting the title screen
                                      * (re)starting while suppression was
                                      * still active (the run was
                                      * abandoned/returned to title before
                                      * a stable Player Phase was ever
                                      * reached) */
    u32 observerTimeoutCount;        /* increments once per observer, if
                                      * it ends via the bounded
                                      * DEBUGTOOLS_BOOTSTRAP_OBSERVER_
                                      * TIMEOUT_FRAMES fail-safe (neither
                                      * success nor a detected title
                                      * return happened in time) */
};

enum
{
    DEBUGTOOLS_LAUNCHER_ARMED_MAGIC = 0x44424C31, /* ASCII "DBL1" */
    DEBUGTOOLS_LAUNCH_REQUEST_MAGIC = 0x44424C32  /* ASCII "DBL2" */
};

extern struct DebugToolsProbe gDebugToolsProbe;

#endif /* GUARD_EXPANSION_DEBUGTOOLS_H */
