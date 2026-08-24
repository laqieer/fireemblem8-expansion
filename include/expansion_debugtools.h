#ifndef GUARD_EXPANSION_DEBUGTOOLS_H
#define GUARD_EXPANSION_DEBUGTOOLS_H

#ifdef FE8_ARCHIVAL_BUILD
/*
 * The supported debug-tools subsystem is modern-only. Keep archival
 * callsites in their original source shape instead of linking disabled
 * stubs, state, menus, or libc dependencies into the byte-matched ROM.
 */
#else
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
 * Built-ins and contributors use separate bounded EWRAM arrays. IDs 1-10
 * occupy the ten built-in slots; contributors retain the originally
 * documented nine successful registrations in their own slots.
 *
 * MENU_ITEM_MAX is 11 (include/uimenu.h) and StartMenuCore (src/uimenu.c)
 * has no bounds check when it appends to MenuProc::menuItems. The hub
 * therefore renders at most nine actions per page plus Back (10 live rows),
 * leaving one MenuProc::menuItems slot as a safety margin. Pressing R queues
 * a page transition and freezes the current menu; a deferred Proc ends it
 * only after ProcessMenuSelectInput/Menu_OnIdle finish the current dispatch,
 * then starts the next bounded page. DEBUGTOOLS_HUB_MENU_SLOTS includes the
 * all-zero MenuItemsEnd terminator, which is not a live row. */
enum
{
    DEBUGTOOLS_BUILTIN_ACTION_MAX = 10,
    DEBUGTOOLS_CONTRIBUTOR_ACTION_MAX = 9,
    DEBUGTOOLS_ACTION_MAX = 19,
    DEBUGTOOLS_HUB_PAGE_ACTION_MAX = 9,
    DEBUGTOOLS_HUB_PAGE_MAX = 3,
    DEBUGTOOLS_DIAGNOSTICS_PAGE_COUNT = 2,
    DEBUGTOOLS_HUB_VIEW_MAX =
        DEBUGTOOLS_HUB_PAGE_MAX + DEBUGTOOLS_DIAGNOSTICS_PAGE_COUNT,
    DEBUGTOOLS_HUB_MENU_SLOTS = 11, /* nine actions + Back + terminator */
    DEBUGTOOLS_MENU_WIDTH_TILES = 18,
    DEBUGTOOLS_STATUS_TEXT_WIDTH_TILES = 17,
    DEBUGTOOLS_BUILTIN_ID_MIN = 1,
    DEBUGTOOLS_BUILTIN_ID_MAX = 10,
    DEBUGTOOLS_CONTRIBUTOR_ID_MIN = 11,
    DEBUGTOOLS_CONTRIBUTOR_ID_MAX = 0xFFFF,

    /* The default BG text font starts at tile 0x80 and Text uses two
     * 8x8 tiles per allocated text column. Tile indices are 10 bits, so
     * the allocator has (0x400 - 0x80) / 2 == 448 columns available.
     * Each maximum-size page uses ten 17-column rows (nine actions + Back)
     * plus one 17-column localized status line. Transitions reclaim this
     * bounded 187-column scope only after the old menu has ended. */
    DEBUGTOOLS_TEXT_ALLOC_CAPACITY = 448,
    DEBUGTOOLS_HUB_TEXT_ALLOC_BUDGET = 187,

    /* Issue #11 closure: explicit policy bound on a contributor label's
     * length (excluding the NUL terminator). Not a hard memory-safety
     * limit (DebugTools_RegisterAction never copies label bytes into a
     * fixed buffer -- only the pointer itself is stored, see
     * src/debugtools_registry.c), but a documented, enforced contract so
     * a pathologically long label is rejected with an explicit,
     * diagnosable result rather than silently accepted and left to
     * whatever the menu renderer happens to do with it. The longest
     * label shipped in this file (5 -- "Fast Boot: Chapter 2", 20 chars)
     * stays comfortably under this. */
    DEBUGTOOLS_LABEL_MAX_LENGTH = 24
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
    DEBUGTOOLS_ERR_CAPACITY_FULL,   /* the relevant built-in/contributor storage is full */
    DEBUGTOOLS_ERR_ALREADY_ACTIVE,  /* DebugTools_OpenHub called while the hub is already open */

    /* Issue #11 closure: appended at the end so every existing named
     * value above keeps its original integer (scenario JSON files probe
     * gDebugToolsProbe.lastRegisterResult by raw integer -- see
     * docs/debugtools.md -- so no existing value may ever be
     * renumbered). */
    DEBUGTOOLS_ERR_ID_INVALID,      /* action->id == 0 (reserved/uninitialized-looking sentinel) */
    DEBUGTOOLS_ERR_LABEL_INVALID,   /* label is empty, or longer than DEBUGTOOLS_LABEL_MAX_LENGTH */
    DEBUGTOOLS_ERR_ID_RESERVED,     /* contributor attempted to claim built-in id 1-10 */
    DEBUGTOOLS_ERR_TEXT_CAPACITY,   /* active font cannot fit one maximum hub allocation */
    DEBUGTOOLS_ERR_INVALID_ARGUMENT,
    DEBUGTOOLS_ERR_CONTEXT_UNAVAILABLE
};

/* A single contributor-registered debug action. label remains a raw C string
 * for ABI compatibility and third-party actions. IDs 1-10 are reserved for
 * immutable built-in identities; contributors must choose an ID in
 * [DEBUGTOOLS_CONTRIBUTOR_ID_MIN, DEBUGTOOLS_CONTRIBUTOR_ID_MAX]. Built-ins
 * have render-time expansion-message adapters; contributor IDs keep the
 * original MenuItemDef::name/Text_DrawString raw path. onSelected has the
 * exact MenuItemDef onSelected signature, so a registered action can drive
 * the hub menu (e.g. return MENU_ACT_END | MENU_ACT_CLEAR | ... ) exactly
 * like any other MenuItemDef handler. */
struct DebugToolsAction
{
    u16 id;                 /* stable, contributor-chosen identifier */
    const char* label;
    u8 (*onSelected)(struct MenuProc* menu, struct MenuItemProc* item);
};

/* Registers a new contributor debug action. IDs 1-10 are rejected with
 * DEBUGTOOLS_ERR_ID_RESERVED; ID 0 remains DEBUGTOOLS_ERR_ID_INVALID.
 * Valid contributor IDs are 11-65535. Exactly
 * DEBUGTOOLS_CONTRIBUTOR_ACTION_MAX contributor registrations can coexist
 * with all DEBUGTOOLS_BUILTIN_ACTION_MAX built-ins. Before accepting one,
 * the shipped built-ins are initialized once in deterministic ID/menu order
 * so an early contributor call can never displace a built-in. Returns
 * DEBUGTOOLS_OK on success, or an explicit DebugToolsResult error code
 * otherwise. Never silently drops a registration. Registration order is
 * preserved (deterministic ordering), and no heap allocation is used
 * anywhere in this subsystem. */
int DebugTools_RegisterAction(const struct DebugToolsAction* action);

/* Introspection, primarily for host tests and playtest probes. */
/* Returns the combined built-in-plus-contributor count. Indexed access lists
 * built-ins first in stable ID/menu order, then contributors in registration
 * order. The hub presents that same order in DEBUGTOOLS_HUB_PAGE_ACTION_MAX
 * rows per page; R cycles pages without growing the live menu. */
int DebugTools_GetRegisteredCount(void);
const struct DebugToolsAction* DebugTools_GetRegisteredAction(int index);
enum DebugToolsResult DebugTools_GetLastRegistrationResult(void);

/* Contributor submenu handoff contract.
 *
 * A registered action that opens another MenuDef must NOT call
 * StartOrphanMenu or StartMenu directly. Its onSelected callback must first call
 * DebugTools_QueueSubmenuTransition(menu, submenuDef), then return a result
 * containing MENU_ACT_END. Queueing marks the handoff before the ordinary
 * menu callback result ends the hub, so the hub's onEnd does not perform
 * final session cleanup or rewind Text storage while its MenuItemProc
 * objects are still live.
 *
 * An owned submenu may use the same queue function to hand off to another
 * bounded submenu while the debugtools session remains active. Queueing is
 * rejected while another transition is pending. On ordinary completion, an
 * owned submenu's MenuDef::onEnd callback calls
 * DebugTools_ReturnToHubAfterMenuEnd(menu); a validated forced-teardown path
 * may instead end the session. Both paths defer the matching rewind until
 * after the submenu and its Text objects have ended.
 * The row-allocation font and counter baseline are captured immediately
 * before the owner-child StartMenu call, before MenuDef::onInit can switch
 * gActiveFont.
 * Cleanup rewinds only that exact owner, restores the previously active
 * font as a separate step, and never writes a contributor font's unrelated
 * counter. A font-switching submenu remains responsible for any allocations
 * it deliberately makes from its own font. The hub then reopens while
 * retaining the same debug session/reentrancy ownership. The session is
 * finally released only when the reopened hub itself ends without another
 * queued transition.
 *
 * Calls outside an active hub/submenu session, duplicate transition requests,
 * and a NULL submenuDef are also safe no-ops. Disabled builds retain inline
 * no-op forms so unconditional contributors need no extra preprocessor guard,
 * while the linked hub implementation remains physically omitted. */
#if FE8_EXPANSION_DEBUGTOOLS_ENABLED
void DebugTools_QueueSubmenuTransition(
    struct MenuProc* menu,
    const struct MenuDef* submenuDef);
void DebugTools_ReturnToHubAfterMenuEnd(struct MenuProc* menu);

/* Finalizes an active submenu session without reopening the hub. The same
 * one-yield transition restores the owning font/counter and clears session
 * ownership; game-control handoffs may then consume their already-queued
 * typed request. Calls outside an active submenu session are safe no-ops. */
void DebugTools_EndSessionAfterMenuEnd(struct MenuProc* menu);
#else
static inline void DebugTools_QueueSubmenuTransition(
    struct MenuProc* menu,
    const struct MenuDef* submenuDef)
{
    (void)menu;
    (void)submenuDef;
}

static inline void DebugTools_ReturnToHubAfterMenuEnd(struct MenuProc* menu)
{
    (void)menu;
}

static inline void DebugTools_EndSessionAfterMenuEnd(struct MenuProc* menu)
{
    (void)menu;
}
#endif

/* Opens the debug hub as a blocking child menu of the session's display-owner
 * Proc. Lazily registers all shipped built-in actions on first call.
 *
 * This is the single authoritative reentrancy guard for the whole
 * subsystem: it returns DEBUGTOOLS_ERR_ALREADY_ACTIVE (an explicit,
 * observable no-op -- no menu construction, no second owner-child menu,
 * gDebugToolsProbe.hubOpenCount left unchanged) if any hub/submenu/
 * allocator-transition session is already active. A release-and-repress
 * of the title hotkey during that session must never spawn a second
 * concurrent MenuProc; guarding here
 * (rather than in each caller) protects every current and future caller
 * (DebugTools_TitleHotkeyCheck today, any later map/prep entry point)
 * without each needing its own check. Returns DEBUGTOOLS_ERR_DISABLED
 * (and remains a no-op) when the subsystem is compiled out. */
enum DebugToolsResult DebugTools_OpenHub(void);

/* True for the complete debug-menu session: hub, submenu, and the
 * one-yield allocator transition after each old menu ends. It clears only
 * after final deferred cleanup restores the pre-debug text allocation
 * counter. Title_IDLE (src/titlescreen.c) checks this to skip its own
 * A/START handling for the whole session: the context owner remains
 * schedulable beside the display-owner/menu descendant and both can read
 * newKeys in one frame, so without this guard a
 * single A press meant for a debug menu could race the vanilla
 * title-to-gameplay transition. Always returns 0 when the subsystem is
 * compiled out. */
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

/* --- Typed visual/status diagnostics (issue #127) -------------------------
 * Captures one bounded, read-only snapshot from an active debugtools session.
 * The provider is the only public extension seam for these diagnostics; the
 * built-in State/Engine views are private consumers and do not occupy a
 * DebugToolsAction ID or contributor slot.
 *
 * A successful capture increments sequence exactly once. Unsupported,
 * transient, battle, disabled, and out-of-session calls fail closed with
 * validMask == 0. Unavailable optional fields are zero and must be ignored
 * unless their corresponding validity bit is set. */
enum DebugToolsDiagnosticsContext
{
    DEBUGTOOLS_DIAG_CONTEXT_UNAVAILABLE = 0,
    DEBUGTOOLS_DIAG_CONTEXT_TITLE,
    DEBUGTOOLS_DIAG_CONTEXT_MAP,
    DEBUGTOOLS_DIAG_CONTEXT_PREP,
    DEBUGTOOLS_DIAG_CONTEXT_BATTLE,
};

enum DebugToolsDiagnosticsValid
{
    DEBUGTOOLS_DIAG_VALID_COMMON = (1 << 0),
    DEBUGTOOLS_DIAG_VALID_MAP = (1 << 1),
    DEBUGTOOLS_DIAG_VALID_CURSOR = (1 << 2),
    DEBUGTOOLS_DIAG_VALID_UNIT = (1 << 3),
};

struct DebugToolsDiagnosticsSnapshot
{
    /* 00 */ u32 sequence;
    /* 04 */ u32 validMask;
    /* 08 */ u32 gameClockFrames;
    /* 0C */ u32 procCount;
    /* 10 */ u32 logTotalWrites;
    /* 14 */ u32 lastLogCode;
    /* 18 */ u32 assertFailureCount;
    /* 1C */ u32 lastAssertCode;
    /* 20 */ u16 rngState[3];
    /* 26 */ u8 context;
    /* 27 */ u8 eventEngineActive;
    /* 28 */ s8 chapterIndex;
    /* 29 */ u8 faction;
    /* 2A */ u16 turn;
    /* 2C */ s16 cursorX;
    /* 2E */ s16 cursorY;
    /* 30 */ u8 cursorUnitId;
    /* 31 */ u8 characterId;
    /* 32 */ u8 classId;
    /* 33 */ u8 currentHp;
    /* 34 */ u8 maxHp;
    /* 35 */ u8 weatherId;
    /* 36 */ u8 fogRange;
    /* 37 */ u8 registeredActionCount;
    /* 38 */ u8 logRetainedCount;
    /* 39 */ u8 reserved[7];
};

enum DebugToolsResult DebugTools_CaptureDiagnostics(
    struct DebugToolsDiagnosticsSnapshot* out);

/* --- Pending Chapter 2 fast-boot launch request ---------------------------
 * Replaces the earlier "tear down and restart GameCtrlProc from inside a
 * MenuProc callback" launcher (proc-tree lifecycle corruption -- see
 * docs/debugtools.md). The hub action ("Fast Boot: Chapter 2",
 * src/debugtools_launcher.c) now only arms a pending request and closes the
 * hub; it never touches gProcScr_GameControl, gProc_BMapMain, units, or
 * events. The request is detected by Title_IDLE (src/titlescreen.c) after
 * the hub MenuProc has closed (even while the one-yield allocator cleanup
 * still retains session ownership), and consumed exactly once by
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
 * GameControl_PostIntro consumes it. Title_IDLE checks this before the
 * broader DebugTools_IsHubActive() session guard so the deliberate
 * one-yield allocator cleanup cannot delay the deterministic launch. The
 * request is armed only by the hub action immediately before that callback
 * returns MENU_ACT_END. Title_IDLE itself never clears this flag; it only
 * reacts to it by setting the ordinary
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

/* --- Legacy Chapter 4 request compatibility -------------------------------
 * The issue #11 Chapter 4 request remains available for runtime probes that
 * call it directly. Its former built-in action ID 4 is now the bounded
 * Chapter/Skirmish selector registered by src/debugtools_selector.c. The
 * selector uses a separate typed request and does not route through these
 * compatibility functions. */

/* Arms the second pending debug launch request. Same idempotent,
 * side-effect-free-until-consumed contract as
 * DebugTools_RequestChapter2Launch above -- see
 * gDebugToolsProbe.pendingCh4PrepLaunchRequest. No-op when the subsystem
 * is compiled out. */
void DebugTools_RequestChapter4PrepLaunch(void);

/* True from the frame the hub action arms this second request until
 * GameControl_PostIntro consumes it. Same contract as
 * DebugTools_IsChapter2LaunchPending above (independent flag -- arming one
 * launch request never affects the other). Always 0 when the subsystem is
 * compiled out. */
int DebugTools_IsChapter4PrepLaunchPending(void);

/* Consumes the second pending request exactly once. Same one-shot
 * contract as DebugTools_ConsumePendingChapter2Launch above. Always 0 when
 * the subsystem is compiled out. */
int DebugTools_ConsumePendingChapter4PrepLaunch(void);

/* Compatibility alias: registers the ID-4 Chapter/Skirmish selector.
 * Existing source that initialized the former Chapter 4 action explicitly
 * therefore keeps initializing the same stable built-in slot. */
void DebugTools_RegisterChapter4PrepAction(void);

/* --- Diagnostics: structured probe/log ring + assert record ---------------
 * Issue #11 closure requirement 6: "emulator logging/assertion/
 * crash-diagnostic/memory-inspection foundations". This is explicitly
 * NOT an mgba_printf/AGB-print-protocol implementation, NOT an
 * interactive debugger, and NOT an arbitrary memory editor -- see
 * docs/debugtools.md and reports/debugtools_issue11_closure.md for the
 * non-goals this deliberately stops short of. What it does provide:
 *
 *   - a small, fixed-capacity (DEBUGTOOLS_LOG_RING_SIZE), always-linked
 *     ring buffer of (code, a, b) event records that every mutating tool
 *     action below writes to (DebugTools_LogEvent) -- a structured,
 *     bounded probe/log surface, never a heap-growing log;
 *   - a bounded "assert" record (DebugTools_RecordAssertFailure /
 *     DEBUGTOOLS_ASSERT) that tools use to defensively re-validate a
 *     bound (e.g. an event-flag index) immediately before a mutation --
 *     on failure it records the failing code and safely skips the
 *     mutation (returns to the caller normally); it never aborts,
 *     crashes, or halts the game, matching every other tool's
 *     safe-return-to-game contract;
 *   - bounded, read-only, whitelisted introspection
 *     (DebugTools_GetLogEntry/DebugTools_GetLogCount/
 *     DebugTools_GetAssertFailureCount/DebugTools_GetLastAssertCode) --
 *     never a raw/arbitrary address reader.
 *
 * All state additionally mirrors into gDebugToolsProbe fields (see below)
 * so playtest scenarios can assert on it the same way as every other
 * probe field. Release-inert: every function below compiles to a trivial
 * disabled stub when the subsystem is disabled, same convention as every
 * other file in this subsystem. */

enum { DEBUGTOOLS_LOG_RING_SIZE = 8 };

/* Named log codes -- deliberately small and closed (an enum, not a
 * caller-supplied free-form string), so every log entry's `code` field is
 * one of a fixed, documented set. `a`/`b` carry the code-specific payload
 * (e.g. a unit's sampled HP, a convoy item id). */
enum DebugToolsLogCode
{
    DEBUGTOOLS_LOG_NONE = 0,
    DEBUGTOOLS_LOG_ASSERT_FAILURE,
    DEBUGTOOLS_LOG_UNIT_INSPECT,
    DEBUGTOOLS_LOG_UNIT_HEAL_APPLIED,
    DEBUGTOOLS_LOG_UNIT_HEAL_SKIPPED_INVALID,
    DEBUGTOOLS_LOG_CONVOY_INSPECT,
    DEBUGTOOLS_LOG_CONVOY_ADD_APPLIED,
    DEBUGTOOLS_LOG_CONVOY_ADD_SKIPPED_FULL,
    DEBUGTOOLS_LOG_FLAG_INSPECT,
    DEBUGTOOLS_LOG_FLAG_TOGGLE_APPLIED,
    DEBUGTOOLS_LOG_RNG_INSPECT,
    DEBUGTOOLS_LOG_RNG_RESEED_APPLIED,
    DEBUGTOOLS_LOG_SAVESTATE_INSPECT,
    /* Issue #125: cursor-selected unit editor telemetry. Payload `a`
     * packs operation:field:old as 8:8:16 bits; payload `b` packs
     * outcome:new as 8:24 bits. Exact values also mirror into the
     * appended gDebugToolsProbe fields below. */
    DEBUGTOOLS_LOG_UNIT_EDIT_PREVIEW,
    DEBUGTOOLS_LOG_UNIT_EDIT_APPLIED,
    DEBUGTOOLS_LOG_UNIT_EDIT_CANCELLED,
    DEBUGTOOLS_LOG_UNIT_EDIT_REJECTED,
    DEBUGTOOLS_LOG_UNIT_EDIT_FORCED_CLEANUP,
    DEBUGTOOLS_LOG_MUSIC_PREVIEW,
    DEBUGTOOLS_LOG_MUSIC_RESTORE,
    DEBUGTOOLS_LOG_MUSIC_REJECTED
};

/* Named assert codes -- checked by DEBUGTOOLS_ASSERT at each tool's own
 * defensive bound re-validation site immediately before a mutation. */
enum DebugToolsAssertCode
{
    DEBUGTOOLS_ASSERT_NONE = 0,
    DEBUGTOOLS_ASSERT_FLAG_ID_OUT_OF_RANGE,
    DEBUGTOOLS_ASSERT_UNIT_TARGET_INVALID,
    DEBUGTOOLS_ASSERT_CONVOY_INDEX_OUT_OF_RANGE,
    DEBUGTOOLS_ASSERT_UNIT_TARGET_DEAD,
    DEBUGTOOLS_ASSERT_UNIT_TARGET_STALE,
    DEBUGTOOLS_ASSERT_UNIT_EDIT_CONFLICT,
    DEBUGTOOLS_ASSERT_UNIT_EDIT_VALUE_OUT_OF_RANGE,
    DEBUGTOOLS_ASSERT_UNIT_EDIT_UNSUPPORTED
};

/* Issue #125: closed, typed identities for the cursor-selected unit editor.
 * Values are append-only telemetry ABI: runtime scenarios read them as raw
 * integers from gDebugToolsProbe. */
enum DebugToolsUnitEditOperation
{
    DEBUGTOOLS_UNIT_EDIT_OPERATION_NONE = 0,
    DEBUGTOOLS_UNIT_EDIT_OPERATION_HEAL,
    DEBUGTOOLS_UNIT_EDIT_OPERATION_SET_HP,
    DEBUGTOOLS_UNIT_EDIT_OPERATION_SET_STAT,
    DEBUGTOOLS_UNIT_EDIT_OPERATION_SET_AI,
    DEBUGTOOLS_UNIT_EDIT_OPERATION_CLEAR_STATUS
};

enum DebugToolsUnitEditField
{
    DEBUGTOOLS_UNIT_EDIT_FIELD_NONE = 0,
    DEBUGTOOLS_UNIT_EDIT_FIELD_CURRENT_HP,
    DEBUGTOOLS_UNIT_EDIT_FIELD_MAX_HP,
    DEBUGTOOLS_UNIT_EDIT_FIELD_POWER,
    DEBUGTOOLS_UNIT_EDIT_FIELD_SKILL,
    DEBUGTOOLS_UNIT_EDIT_FIELD_SPEED,
    DEBUGTOOLS_UNIT_EDIT_FIELD_DEFENSE,
    DEBUGTOOLS_UNIT_EDIT_FIELD_RESISTANCE,
    DEBUGTOOLS_UNIT_EDIT_FIELD_LUCK,
    DEBUGTOOLS_UNIT_EDIT_FIELD_AI_A,
    DEBUGTOOLS_UNIT_EDIT_FIELD_AI_B,
    DEBUGTOOLS_UNIT_EDIT_FIELD_STATUS,

    DEBUGTOOLS_UNIT_EDIT_FIELD_COUNT
};

enum DebugToolsUnitEditOutcome
{
    DEBUGTOOLS_UNIT_EDIT_OUTCOME_NONE = 0,
    DEBUGTOOLS_UNIT_EDIT_OUTCOME_INSPECTED,
    DEBUGTOOLS_UNIT_EDIT_OUTCOME_PREVIEWED,
    DEBUGTOOLS_UNIT_EDIT_OUTCOME_APPLIED,
    DEBUGTOOLS_UNIT_EDIT_OUTCOME_NO_CHANGE,
    DEBUGTOOLS_UNIT_EDIT_OUTCOME_CANCELLED,
    DEBUGTOOLS_UNIT_EDIT_OUTCOME_FORCED_CLEANUP,
    DEBUGTOOLS_UNIT_EDIT_OUTCOME_REJECTED_EMPTY,
    DEBUGTOOLS_UNIT_EDIT_OUTCOME_REJECTED_INVALID,
    DEBUGTOOLS_UNIT_EDIT_OUTCOME_REJECTED_DEAD,
    DEBUGTOOLS_UNIT_EDIT_OUTCOME_REJECTED_STALE,
    DEBUGTOOLS_UNIT_EDIT_OUTCOME_REJECTED_CONFLICT,
    DEBUGTOOLS_UNIT_EDIT_OUTCOME_REJECTED_RANGE,
    DEBUGTOOLS_UNIT_EDIT_OUTCOME_REJECTED_UNSUPPORTED
};

struct DebugToolsLogEntry
{
    u32 code; /* enum DebugToolsLogCode */
    u32 a;
    u32 b;
};

/* Appends one entry to the fixed-size ring (oldest entry silently
 * overwritten once full -- bounded by construction, never a growing
 * allocation). Always succeeds; mirrors gDebugToolsProbe.logEventCount
 * (unbounded running total) and gDebugToolsProbe.lastLogCode. No-op when
 * the subsystem is compiled out. */
void DebugTools_LogEvent(u32 code, u32 a, u32 b);

/* Number of entries currently readable via DebugTools_GetLogEntry -- 0 up
 * to DEBUGTOOLS_LOG_RING_SIZE (never more; the ring wraps rather than
 * growing past this). Always 0 when the subsystem is compiled out. */
int DebugTools_GetLogCount(void);

/* Bounds-checked, read-only, whitelisted introspection: index 0 is the
 * most recently logged entry, increasing index walks backward in time.
 * Returns NULL outside [0, DebugTools_GetLogCount()). Always NULL when
 * the subsystem is compiled out. */
const struct DebugToolsLogEntry* DebugTools_GetLogEntry(int index);

/* Records a bounded, non-fatal assertion failure: increments
 * gDebugToolsProbe.assertFailureCount, sets
 * gDebugToolsProbe.lastAssertCode, and logs a
 * DEBUGTOOLS_LOG_ASSERT_FAILURE event carrying `code`. Never
 * aborts/halts/crashes the game -- the caller is expected to safely skip
 * whatever mutation it was about to perform and return normally (every
 * DEBUGTOOLS_ASSERT call site in src/debugtools_tools.c does exactly
 * this). No-op when the subsystem is compiled out. */
void DebugTools_RecordAssertFailure(u32 code);

/* Total assert failures recorded so far (unbounded running total, mirrors
 * gDebugToolsProbe.assertFailureCount). Always 0 when the subsystem is
 * compiled out. */
u32 DebugTools_GetAssertFailureCount(void);

/* The most recently recorded assert failure code (enum
 * DebugToolsAssertCode), or DEBUGTOOLS_ASSERT_NONE if none has ever
 * fired. Always DEBUGTOOLS_ASSERT_NONE when the subsystem is compiled
 * out. */
u32 DebugTools_GetLastAssertCode(void);

/* Evaluates `cond`; on false, records `code` via
 * DebugTools_RecordAssertFailure. Callers must still act on the boolean
 * result themselves (this macro does not alter control flow) -- see
 * src/debugtools_tools.c for the "assert then bail out" idiom used at
 * every mutation site. */
#define DEBUGTOOLS_ASSERT(cond, code) \
    do { if (!(cond)) DebugTools_RecordAssertFailure((u32)(code)); } while (0)

/* --- Five bounded validated tools ------------------------------------
 * Issue #11 closure requirement 5. Each is a single registry action (see
 * src/debugtools_tools.c) that samples/displays read-only state on
 * selection. Convoy, Flag, and RNG retain their bounded Confirm/Back menus.
 * Unit has one fixed root plus HP/stat/AI preview menus, and A remains a
 * separate confirmation after inspection or preview. No tool performs a
 * raw/arbitrary address write or accepts an unvalidated index: fixed
 * operations use documented constants, while Unit resolves and revalidates
 * the live cursor target through engine helpers. Persistent SRAM is never
 * mutated (RNG/flags/units/convoy are ordinary EWRAM runtime state; Save
 * State is read-only). Registers ids 5-9 through the internal built-in path,
 * never through the contributor API or direct hub-menu edits. */
void DebugTools_RegisterExtendedToolActions(void);

/* Registers the bounded music-preview action (built-in id 10). The action
 * enumerates only validated gSoundRoomTable entries, owns at most one
 * transient preview through the typed BGM seam, and never writes unlock or
 * save state. */
void DebugTools_RegisterMusicPreviewAction(void);

/* Idempotently releases the music-preview owner and restores the exact
 * captured playing/stopped BGM context. Called by the submenu, final debug
 * session cleanup, title/proc teardown, and chapter transitions. */
void DebugTools_CleanupMusicPreview(void);

/* Forced lifecycle boundary for chapter/title/proc teardown. Restores any
 * music preview, cancels a queued debug-menu transition, and releases the
 * shared session guard without reopening a menu. Ordinary Back continues to
 * use the deferred allocator-safe cleanup path instead. */
void DebugTools_ForceSessionCleanup(void);

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

    /* --- Fast Boot: Chapter 4 (Prep) probe fields (issue #11 closure) --- */
    u32 pendingCh4PrepLaunchRequest; /* DEBUGTOOLS_LAUNCH_REQUEST_MAGIC while
                                       * armed, 0 once consumed -- see
                                       * DebugTools_RequestChapter4PrepLaunch */
    u32 ch4PrepLauncherArmed;        /* DEBUGTOOLS_LAUNCHER_ARMED_MAGIC once
                                       * GameControl_PostIntro commits to the
                                       * deterministic Chapter 4 boot */
    u32 ch4PrepLaunchRequestConsumedCount; /* increments exactly once per
                                             * consumed Chapter 4 request */
    u32 prepScreenObservedCount;     /* increments once the debugtools prep
                                       * hotkey call site observes
                                       * gPlaySt.chapterStateBits &
                                       * PLAY_FLAG_PREPSCREEN while the hub
                                       * is open -- see
                                       * DebugTools_PrepHotkeyCheck,
                                       * src/debugtools_registry.c */

    /* --- Diagnostics: log ring + assert record (issue #11 closure) --- */
    u32 logEventCount;      /* unbounded running total of
                              * DebugTools_LogEvent calls */
    u32 lastLogCode;        /* enum DebugToolsLogCode of the most recent
                              * DebugTools_LogEvent call */
    u32 assertFailureCount; /* unbounded running total of
                              * DEBUGTOOLS_ASSERT failures */
    u32 lastAssertCode;     /* enum DebugToolsAssertCode of the most recent
                              * assert failure, DEBUGTOOLS_ASSERT_NONE if
                              * none has ever fired */

    /* --- Unit inspector (issue #11, cursor target from issue #125) --- */
    u32 unitInspectTargetFound;   /* 1 if the bounded live cursor/map/GetUnit
                                    * resolution produced a valid target at
                                    * the most recent inspect, else 0 */
    u32 unitInspectLastCurHp;     /* curHP sampled at the most recent
                                    * inspect (0 if no valid target) */
    u32 unitInspectLastMaxHp;     /* maxHP sampled at the most recent
                                    * inspect (0 if no valid target) */
    u32 unitHealTransactionCount; /* increments once per valid confirmed
                                    * "Heal to Full" request, including an
                                    * already-full no-change outcome */

    /* --- Convoy inspector (issue #11 closure) --- */
    u32 convoyLastItemCount;       /* GetConvoyItemCount() sampled at the
                                     * most recent inspect */
    u32 convoyAddTransactionCount; /* increments once per confirmed "Add
                                     * test item" transaction that actually
                                     * added an item (AddItemToConvoy
                                     * returned a valid slot, not -1) */

    /* --- Flag/chapter/event state (issue #11 closure) --- */
    u32 chapterIndexSample;   /* gPlaySt.chapterIndex sampled at the most
                                * recent inspect */
    u32 debugFlagToggleCount; /* increments once per confirmed debug-flag
                                * toggle transaction */
    u32 debugFlagLastValue;   /* CheckFlag(DEBUGTOOLS_DEBUG_EVENT_FLAG_ID)
                                * sampled after the most recent
                                * inspect/toggle */

    /* --- RNG inspect/control (issue #11 closure) --- */
    u32 rngInspectSeedSample0;     /* StoreRNState()'s seeds[0] sampled at
                                     * the most recent inspect, before any
                                     * control action */
    u32 rngReseedTransactionCount; /* increments once per confirmed
                                     * "Reseed to debug value" transaction */

    /* --- Save compatibility/state inspection (issue #11 closure) ---
     * Read-only: never mutates SRAM or any save-block struct. */
    u32 saveCompatLastState;    /* ClassifySramSaveCompat() sampled at the
                                  * most recent inspect (enum
                                  * SaveCompatState) */
    u32 saveCompatInspectCount; /* increments once per inspect */
    u32 saveCompatBackMenuPreserved; /* 1 when Save State's only Back path
                                      * reaches the deferred hub return
                                      * without clearing its actual menu frame tile */
    u32 saveCompatBackReturnCount; /* completed owned Save State Back returns */
};

/* Cursor-selected unit inspector/editor telemetry (issue #125). This is a
 * separate appended-section probe so growing the feature cannot move issue
 * #11's gDebugToolsProbe or the established registry state that follows it.
 * No pointer is exposed. */
struct DebugToolsUnitEditorProbe
{
    u32 unitInspectTargetSlot;    /* canonical Unit::index, or 0 */
    u32 unitInspectLastCharacterNumber; /* CharacterData::number, or 0 */
    u32 unitInspectLastClassNumber; /* ClassData::number, or 0 */
    u32 unitInspectLastState;     /* read-only Unit::state sample */
    u32 unitInspectLastStatus;    /* read-only Unit::statusIndex sample */
    u32 unitInspectLastAiA;       /* read-only Unit::ai1 sample */
    u32 unitInspectLastAiB;       /* read-only Unit::ai2 sample */
    u32 unitEditPreviewCount;     /* preview telemetry records */
    u32 unitEditTransactionCount; /* actual one-field mutations */
    u32 unitEditCancelCount;      /* explicit Back/B cancellations */
    u32 unitEditRejectCount;      /* fail-closed target/value rejects */
    u32 unitEditForcedCleanupCount; /* menu ended without apply/cancel */
    u32 unitEditLastOperation;    /* enum DebugToolsUnitEditOperation */
    u32 unitEditLastField;        /* enum DebugToolsUnitEditField */
    u32 unitEditLastOldValue;     /* exact pre-transaction value */
    u32 unitEditLastNewValue;     /* exact preview/requested value */
    u32 unitEditLastOutcome;      /* enum DebugToolsUnitEditOutcome */
    u32 unitEditRefreshCount;     /* authoritative map refreshes */
};

enum
{
    DEBUGTOOLS_LAUNCHER_ARMED_MAGIC = 0x44424C31, /* ASCII "DBL1" */
    DEBUGTOOLS_LAUNCH_REQUEST_MAGIC = 0x44424C32  /* ASCII "DBL2" */
};

extern struct DebugToolsProbe gDebugToolsProbe;
extern struct DebugToolsUnitEditorProbe gDebugToolsUnitEditorProbe;

/* Kept separate from gDebugToolsProbe so issue #126 does not move any
 * established EWRAM symbol that follows the original probe. Its dedicated
 * input section is appended after the stable runtime layout. */
struct DebugToolsMusicProbe
{
    u32 selectedSongId;
    u32 previewCount;
    u32 restoreCount;
    u32 rejectedEntryCount;
    u32 ownerActive;
    u32 priorSongId;
    u32 priorWasPlaying;
    u32 priorContext;
};

extern struct DebugToolsMusicProbe gDebugToolsMusicProbe;

#endif

#endif /* GUARD_EXPANSION_DEBUGTOOLS_H */
