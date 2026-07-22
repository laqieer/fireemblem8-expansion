# Debug-tools foundation (issue #11 slice 1)

This document is the single reference for the debug-tools subsystem added in
issue #11 slice 1: the release-safe config gate, the fixed-capacity
contributor action-registration API, the title-screen hub hotkey, the one
built-in deterministic launcher, and the playtest/host-test evidence that
proves all of it. It intentionally covers **only** this first slice; see
"Remaining #11 scope" at the end for what is explicitly deferred.

## Files

| File | Role |
| --- | --- |
| `include/expansion_debugtools.h` | Public contract: config gate, hotkey mask + compile-time collision guards, registration API, pending-request/bootstrap-suppression API, `struct DebugToolsProbe` |
| `src/debugtools_registry.c` | Registry storage, hub menu construction/diagnostics, hotkey check, `gDebugToolsProbe` |
| `src/debugtools_launcher.c` | The one built-in action ("Fast Boot: Chapter 2"): arms/consumes the pending launch request, owns the bootstrap-suppression state and its observer proc |
| `src/titlescreen.c` | The single supported hotkey call site (`Title_IDLE`); also detects the pending launch request after the hub closes |
| `src/gamecontrol.c` | `GameControl_PostIntro` consumes the pending request exactly once and performs the actual deterministic boot |
| `src/bm.c`, `src/playerphase.c`, `src/soundwrapper.c` | Narrow, one-shot bootstrap-suppression guards on the automatic per-phase suspend-save calls (`BmMain_SuspendBeforePhase`, `PlayerPhase_Suspend`) and the song-room unlock write (`UnlockSoundRoomSong`) |
| `tools/gba-playtest/scenarios/debugtools-hub-modern-{debug,release}.json` | Playtest scenarios (input script + probe expectations) |
| `tools/gba-playtest/fingerprints/debugtools-hub-modern-{debug,release}.json` | Captured fingerprints for those scenarios |
| `tools/gba-playtest/tests/test_debugtools_registry.py` + `tools/gba-playtest/tests/c/*.c` | Host tests (see "Host tests" below) |

This slice deliberately does **not** touch `src/bmdebug.c`, `src/uidebug.c`,
or `src/menu_def.c` -- those dormant tools stay unreachable (see "Remaining
#11 scope").

## Config gate

`FE8_EXPANSION_DEBUGTOOLS_ENABLED` mirrors issue #8's own
`FE8_EXPANSION_DEBUG`/`NDEBUG` convention exactly:

```c
#ifndef FE8_EXPANSION_DEBUGTOOLS_ENABLED
#define FE8_EXPANSION_DEBUGTOOLS_ENABLED FE8_EXPANSION_DEBUG
#endif
```

- A supported modern **debug** build (`MODERN_CONFIG=debug`, no `-DNDEBUG`,
  `FE8_EXPANSION_DEBUG=1`) enables the subsystem by default.
- A supported modern **release** build (`MODERN_CONFIG=release`, `-DNDEBUG`,
  `FE8_EXPANSION_DEBUG=0`) disables it: every function in
  `src/debugtools_registry.c`/`src/debugtools_launcher.c` compiles to a
  trivial disabled-result stub under `#else /* !FE8_EXPANSION_DEBUGTOOLS_ENABLED */`,
  and the hub menu table (`gDebugToolsHubMenuDef`), every hub-internal static
  function, and the launcher's action body **do not exist in the link at
  all** -- not merely unreachable at runtime. Verified by `nm` on the linked
  release ELF (no `DebugToolsHub_*`/`gDebugToolsHubMenuDef`/
  `DebugToolsLauncher_*` symbols) and by an equivalent host-compiled check
  (`test_registry_disabled_path_behavior_and_symbol_omission`).
- The legacy agbcc build never defines `NDEBUG`, so it keeps compiling with
  the subsystem enabled -- same as every other `FE8_EXPANSION_*` gate today.
  This is not a new or contradictory release model.

`gDebugToolsProbe` (see "Playtest probe surface" below) is the one exception:
it is always linked, in every build, so a release scenario can assert it
stays all-zero for a whole run.

## Registration API

Fits the existing `MenuProc`/`MenuItemDef` engine (`include/uimenu.h`,
`src/uimenu.c`) rather than replacing it:

- `MENU_ITEM_MAX` is 11, and `StartMenuCore` has **no bounds check** when it
  appends to `MenuProc::menuItems` -- writing a 12th live item would corrupt
  adjacent `MenuProc` fields. The hub menu therefore reserves one live slot
  for a Back/Exit entry, leaving exactly `DEBUGTOOLS_ACTION_MAX` (9) slots
  for contributor actions, with 1 of the 11 total live slots kept as an
  untouched safety margin.
- Contributors register through `DebugTools_RegisterAction()` only -- they
  never edit an engine-owned `const MenuItemDef` table. A RAM-resident
  `MenuItemDef` adapter (`sHubMenuItemDefs`, sized
  `DEBUGTOOLS_HUB_MENU_SLOTS = DEBUGTOOLS_ACTION_MAX + 2`) is rebuilt from
  the registry every time the hub opens; raw string labels are supported
  directly (`nameMsgId == 0`, the same path `Text_DrawString` already takes
  for other raw-string menu items -- no text-asset/msg-id involvement).
- The array is fully zeroed before every rebuild, so the first unused slot
  (and everything after it) reads as an all-zero `MenuItemsEnd` -- exactly
  what stops `StartMenuCore`'s scan loop. The reserved Back entry is always
  written at `sHubMenuItemDefs[sActionCount]`, immediately after the last
  registered action; the terminator is the slot after that.
- Storage is a fixed-size EWRAM array (`sActions[DEBUGTOOLS_ACTION_MAX]`) --
  no heap allocation anywhere in this subsystem.
- Registration order is preserved exactly (append-only, no sorting/
  compaction) -- deterministic ordering.

### Result codes

`DebugTools_RegisterAction()` always returns one of `enum DebugToolsResult`;
a registration failure is never silently dropped:

| Code | Meaning |
| --- | --- |
| `DEBUGTOOLS_OK` | Registered successfully |
| `DEBUGTOOLS_ERR_DISABLED` | Subsystem compiled out (release build) |
| `DEBUGTOOLS_ERR_INVALID_ACTION` | `NULL` action pointer, `label`, or `onSelected` |
| `DEBUGTOOLS_ERR_DUPLICATE` | `id` or `label` already registered |
| `DEBUGTOOLS_ERR_CAPACITY_FULL` | `DEBUGTOOLS_ACTION_MAX` (9) already reached |
| `DEBUGTOOLS_ERR_ALREADY_ACTIVE` | `DebugTools_OpenHub()` called while the hub is already open |

`DebugTools_GetLastRegistrationResult()` always mirrors the most recent call.
`gDebugToolsProbe.lastRegisterResult` mirrors the same value for playtest
probes.

### Introspection

`DebugTools_GetRegisteredCount()` / `DebugTools_GetRegisteredAction(index)`
(bounds-checked, `NULL` outside `[0, count)`) let host tests and future
contributor code inspect the registry without touching its internals.

## Diagnostics / visible feedback

Registration/input failures are not silent: `DebugToolsHub_ShowDiagnostics()`
reuses the existing on-screen debug font (`SetupDebugFontForBG`/
`PrintDebugStringToBG`, `src/fontgrp.c` -- the same mechanism already proven
by the dormant debug menus) to print either `"DBGTOOLS ERR <code>"` (last
registration result was not `DEBUGTOOLS_OK`) or `"DBGTOOLS <n>/<max>"` on BG2
every time the hub opens. A full `mgba_printf`/AGB print-protocol
implementation was judged too broad for this slice and is explicitly
deferred (see "Remaining #11 scope"); this on-screen line is the retained,
always-visible feedback mechanism for now.

## Title-screen hub hotkey

Exactly one global hub entry path in this slice: `Title_IDLE`
(`src/titlescreen.c`), the single title-screen-only idle call site,
calls `DebugTools_TitleHotkeyCheck()` once per frame. Default mask is
`SELECT_BUTTON | R_BUTTON`, overridable via
`FE8_EXPANSION_DEBUGTOOLS_HOTKEY_MASK`:

```c
#ifndef FE8_EXPANSION_DEBUGTOOLS_HOTKEY_MASK
#define FE8_EXPANSION_DEBUGTOOLS_HOTKEY_MASK (SELECT_BUTTON | R_BUTTON)
#endif
```

Compile-time guardrails (`#error`, checked unconditionally, even in release
builds, so a misconfigured override is caught at compile time rather than
silently shipped):

- Must not be `0`.
- Must not exactly equal `L_BUTTON | R_BUTTON | A_BUTTON | B_BUTTON` (the
  existing soft-reset combo checked every frame by
  `SoftResetIfKeyComboPressed`, `src/hardware.c`).
- Must not exactly equal
  `A_BUTTON | B_BUTTON | SELECT_BUTTON | START_BUTTON` (the other existing
  soft-reset combo).

The hotkey fires exactly once, on the frame the combo *completes* (at least
one bit of the mask newly pressed while every bit is held) -- not every frame
the combo stays held. Entry is impossible in release builds:
`DebugTools_TitleHotkeyCheck()` compiles to an empty stub (no key read, no
`DebugTools_OpenHub()` call) when the subsystem is disabled.

### Sibling-proc race and `DebugTools_IsHubActive()`

`Title_IDLE` lives inside `gProcScr_GameControl`'s own proc tree
(`src/gamecontrol.c`'s script directly `PROC_CALL`s `StartTitleScreen_WithMusic`).
The hub's menu (`StartOrphanMenu`) is started *from inside* `Title_IDLE`, so
once open, the hub's menu proc and `Title_IDLE` are **independent sibling
procs** under that same tree -- both still read `newKeys` every frame. Without
a guard, an "A" press meant to select a hub action would also be seen, on the
same frame, by `Title_IDLE`'s own unconditional `A_BUTTON | START_BUTTON`
check below it, racing the vanilla title-to-gameplay transition against the
hub action.

`DebugTools_IsHubActive()` (backed by a static flag, set in
`DebugTools_OpenHub`, cleared in the hub's `MenuDef::onEnd`) closes this gap:
`Title_IDLE` returns immediately after the hotkey check for as long as the
hub is active, skipping its own vanilla A/START handling entirely. Always
returns 0 in a release build.

### Reentrancy guard: a repeated hotkey pulse can never spawn a second hub

`DebugTools_OpenHub()` is the single authoritative reentrancy guard for the
whole hub-entry surface: it checks `sHubActive` at the very top, before any
side effect (builtin-action registration, menu-item construction,
diagnostics, `hubOpenCount` increment, or `StartOrphanMenu()`), and returns
`DEBUGTOOLS_ERR_ALREADY_ACTIVE` as a pure no-op if the hub is already open.

This matters because `DebugTools_TitleHotkeyCheck()`'s edge-detection only
requires the mask to be *newly completed* -- releasing and re-pressing
`SELECT + R` while the hub remains open re-triggers `newKeys` exactly the
same as the original press, and therefore calls `DebugTools_OpenHub()` again.
Without the guard, that second call would unconditionally rebuild the menu
and start a second, concurrent `MenuProc` (a real defect reproduced during
review: `hubOpenCount` reached 2 from two separate hotkey pulses at frames
600-606 and 650-656 while the hub was still open from the first). The guard
lives in `DebugTools_OpenHub()` itself -- not duplicated in each caller -- so
every current (`DebugTools_TitleHotkeyCheck`) and future (map/prep) entry
point is protected automatically; a caller never needs its own busy check.

`tools/gba-playtest/scenarios/debugtools-hub-modern-debug.json` and the host
test `test_debugtools_registry.DebugToolsRegistryHostTests
.test_registry_capacity_order_and_errors` both exercise this directly: the
scenario replays two separate hotkey pulses before ever pressing A, and
asserts `hubOpenCount` stays exactly `1` after both; the host test calls
`DebugTools_OpenHub()` three times in a row and asserts the first returns
`DEBUGTOOLS_OK` (count becomes 1) while the second and third both return
`DEBUGTOOLS_ERR_ALREADY_ACTIVE` (count stays 1).

## Deterministic launcher: "Fast Boot: Chapter 2"

An earlier version of this launcher tore down and restarted the whole
`GameCtrlProc` tree (`Proc_EndEach(gProcScr_GameControl)` + `Proc_Start` +
`Proc_Goto(..., LGAMECTRL_EXEC_BM_EXT)`) from inside its own orphan
`MenuProc` `onSelected` callback -- proc-tree lifecycle corruption that hung
later prologue/Chapter 1/Chapter 2 experiments at unrelated event/fade
stages. The launcher is now a three-stage **pending-request handoff**
across the existing proc lifecycle, with no proc torn down or recreated
anywhere:

1. **Hub action arms only a flag** (`src/debugtools_launcher.c`,
   `DebugToolsLauncher_FastBootChapter2`). Selecting "Fast Boot: Chapter 2"
   calls `DebugTools_RequestChapter2Launch()` (idempotent -- a duplicate arm
   changes nothing observable) and returns `MENU_ACT_END` to close the hub.
   It never touches `gProcScr_GameControl`/`gProc_BMapMain`, never calls
   `Proc_EndEach`/`Proc_Start` on the game-control proc, never loads units,
   and never manipulates events.
2. **`Title_IDLE` detects the pending request only after the hub has fully
   closed** (`src/titlescreen.c`): `DebugTools_IsHubActive()` is checked
   (with an early return while still active) strictly before
   `DebugTools_IsChapter2LaunchPending()`. When pending, it reacts with the
   exact same `SetNextGameActionId(GAME_ACTION_EVENT_RETURN); Proc_Break(proc);`
   pair the ordinary `A`/`START` branch uses -- the normal fade/end/
   parent-unblock lifecycle of this `TitleScreen` proc runs completely
   unmodified. No `A`/`START` keypress is ever synthesized.
3. **`GameControl_PostIntro` consumes the request exactly once**
   (`src/gamecontrol.c`), before its ordinary `StartSaveMenu` branch:
   `DebugTools_ConsumePendingChapter2Launch()` gates a branch that seeds a
   fixed deterministic RNG (`SetLCGRNValue(DEBUGTOOLS_FASTBOOT_RNG_SEED)` +
   `InitRN`), reuses the same `InitPlayConfig`/`ResetPermanentFlags`/
   `ResetChapterFlags`/`InitUnits` bootstrap sequence
   `GameControl_InitTutorialGame` uses (`CHAPTER_MODE_COMMON`, non-tutorial),
   sets `gPlaySt.chapterIndex`/`proc->nextChapter = CHAPTER_L_2`, calls
   `GmDataInit()`, places the world-map party at `NODE_CASTLE_FRELIA` (the
   same rest stop the normal Chapter 1 -> Chapter 2 progression passes
   through -- `GmDataInit()` otherwise defaults to `NODE_BORDER_MULAN`, the
   Prologue's own rest node, and the ordinary `LGAMECTRL_EXEC_BM` path's own
   `WorldMap_CallBeginningEvent` unconditionally recomputes `chapterIndex`
   from the party's node, so without this the chapter set above would be
   silently overwritten back to the Prologue), arms the one-shot
   persistent-write suppression (`DebugTools_ArmBootstrapSuppression()`),
   and finally `Proc_Goto(proc, LGAMECTRL_EXEC_BM)` -- the **ordinary**
   proc-script state, on the **same** `GameCtrlProc` that has been running
   since boot. No proc tree is torn down or recreated, and no
   chapter-specific event/battle logic is bypassed: the real
   `EventScr_Ch2_BeginningScene`, the real interactive world map (including
   the player's own `L`-cursor-jump + `A`-node-confirm navigation to Ide),
   and the real Chapter 2 battle map all run completely unmodified from this
   point on.

### One-shot persistent-write suppression

Between the boot committing (`DebugTools_ArmBootstrapSuppression()`) and the
first stable Player Phase, a small **singleton** bootstrap observer proc
(`DebugToolsObserver_WaitForStablePlayerPhase`, `PROC_TREE_3`,
`src/debugtools_launcher.c`) is responsible for clearing the suppression
flag. `DebugTools_ArmBootstrapSuppression()` is idempotent: it first calls
`DebugTools_CleanupBootstrapObserver()` to end any stale observer/flag from a
prior, possibly-abandoned run, and only then `Proc_Start`s exactly one new
observer -- so repeated arming (or arming after an abandoned run) can never
leave two observers alive or a suppression flag stuck from a previous boot.

The observer's own per-frame poll body has exactly **two** termination
branches, each ending via `Proc_Break(proc)`:

- **success** -- `gPlaySt.faction == FACTION_BLUE && Proc_Find(gProcScr_PlayerPhase)
  != NULL` (first stable Player Phase reached), or
- **timeout** -- `DEBUGTOOLS_BOOTSTRAP_OBSERVER_TIMEOUT_FRAMES` (21600 frames,
  safely above any normal Chapter 2 boot-to-interactive duration) elapses
  without success, guaranteeing suppression can never be stuck on forever
  even if the boot hangs or the Player Phase is never reached.

A **third**, event-driven path handles returning to the title screen before
the boot completes (e.g. the player cancels out): `src/titlescreen.c`'s three
`StartTitleScreen_*` functions (`_WithMusic`, `_FlagFalse`, `_FlagTrue` --
the only call sites that ever (re)start `gProcScr_TitleScreen` as a blocking
child of `gProcScr_GameControl`) each call
`DebugTools_NotifyTitleScreenStarting()`, a no-op unless suppression is
active. This is deliberately **not** implemented as a `Proc_Find
(gProcScr_TitleScreen)` poll inside the observer: `FreeProcess` (`src/proc.c`)
never clears a freed proc's `proc_script` pointer, so a `Proc_Find` scan can
report a stale, already-ended proc's slot as a false-positive match on the
very frame it naturally ends -- which is exactly what happens to the real
`TitleScreen` proc around the time the observer starts. An earlier revision
of this feature used that poll and it produced a real regression (the
observer cleared suppression almost immediately, letting
`BmMain_SuspendBeforePhase` write mid-boot and stall Chapter 2 progression).
The direct function-call notification has no such staleness window.

All three termination paths funnel through a single end callback
(`DebugToolsObserver_OnEnd`, installed via `PROC_SET_END_CB`) that
unconditionally clears `sBootstrapSuppressionActive` -- so every way the
observer can stop (its own `Proc_Break`, or an external
`DebugTools_CleanupBootstrapObserver()` call) clears suppression exactly the
same way; there is exactly one place in the code that clears the flag. The
observer never redirects `gProcScr_PlayerPhase`, `gProc_BMapMain`, or any
other proc.

While suppression is active, `DebugTools_IsBootstrapSuppressionActive()`
gates three otherwise-unconditional automatic persistent-write call sites
that would otherwise fire during the deterministic boot's own opening
NPC-phase-into-Player-phase transition (proven necessary empirically: without
all three, the whole-SRAM hash changes between the pre-launch and
first-interactive checkpoints even though the boot performs no *user-visible*
save action):

- `BmMain_SuspendBeforePhase` (`src/bm.c`) -- the automatic suspend-save at
  the start of every battle-map phase.
- `PlayerPhase_Suspend` (`src/playerphase.c`) -- the analogous automatic
  suspend-save at the very start of every Player Phase (`PROC_LABEL(0)` of
  `gProcScr_PlayerPhase`), which runs before the observer proc above can
  possibly have cleared suppression on the same frame `gProcScr_PlayerPhase`
  first exists.
- `UnlockSoundRoomSong` (`src/soundwrapper.c`) -- the sound-room-unlock SRAM
  write triggered by songs that play during the boot sequence.

Outside this narrow one-shot boot window (before it's armed, and forever
after the observer clears it), all three call sites behave exactly as
before -- ordinary user-triggered Suspend saves and song-room unlocks are
never suppressed. `src/playerphase.c`'s guard is a deliberate, narrowly-
scoped extension of the same pattern already used by `src/bm.c`/
`src/soundwrapper.c`: it was not part of this feature's original allowed-file
list, but is required to satisfy the hard "zero SRAM diff" bar below --
without it, `PlayerPhase_Suspend`'s own unconditional write is reachable
inside the suppression window on any deterministic Chapter 2 boot that
reaches a real Player Phase at all (which this one always does).

## Playtest evidence

No existing scenario/fingerprint in this repository used address-based
probes before this slice -- every prior scenario relied solely on
`framebuffer_hash`/`sram_hash`. `struct DebugToolsProbe` (always linked, both
builds, zero-initialized EWRAM) is the new stable state-evidence surface:

```c
struct DebugToolsProbe
{
    u32 hubOpenCount;
    u32 registeredActionCount;
    u32 lastRegisterResult;         /* enum DebugToolsResult */
    u32 launcherArmed;              /* DEBUGTOOLS_LAUNCHER_ARMED_MAGIC ("DBL1") once the boot commits */
    u32 titleIdleTimerSample;       /* mirrors proc->timer_idle every Title_IDLE frame */
    u32 pendingLaunchRequest;       /* DEBUGTOOLS_LAUNCH_REQUEST_MAGIC ("DBL2") while armed, 0 once consumed */
    u32 launchRequestConsumedCount; /* increments once per GameControl_PostIntro consume */
    u32 bootstrapSuppressionActive; /* 1 while the one-shot suppression window is open */
    u32 playerPhaseObservedCount;   /* increments once, on the success termination path */
    u32 bootstrapObserverArmCount;  /* increments once per DebugTools_ArmBootstrapSuppression() call */
    u32 observerTitleReturnCount;   /* increments once per DebugTools_NotifyTitleScreenStarting() cleanup */
    u32 observerTimeoutCount;       /* increments once, on the timeout termination path */
};
extern struct DebugToolsProbe gDebugToolsProbe;
```

`tools/gba-playtest/scenarios/debugtools-hub-modern-{debug,release}.json`
probe this struct directly by its link address (different per
`MODERN_CONFIG` -- confirm with
`nm build/expansion-modern/<config>/aapcs/fireemblem8.elf | grep gDebugToolsProbe`
before touching either scenario). Unlike the shared
`title-progression.json` scenario (only its *fingerprint* path is
`$(MODERN_CONFIG)`-suffixed in `modern.mk`), **both** the scenario and the
fingerprint paths for this check are config-suffixed
(`MODERN_DEBUGTOOLS_SCENARIO`/`MODERN_DEBUGTOOLS_FINGERPRINT`), because the
debug and release scenarios assert genuinely different outcomes (hub really
opens vs. stays permanently absent), not just different addresses for the
same assertion.

### Input timing (why the hotkey pulse is not at frame 0)

Both scenarios replay the identical frame-for-frame input script:

1. The Health & Safety warning screen (`src/opanim-healthsafetyscreen.c`)
   masks all input except the hardcoded `A+B+START+SELECT` unlock combo via
   `SetKeyStatus_IgnoreMask(0x3FF)` until the intro sequence clears it.
2. `Title_IDLE` itself is only reachable after replaying the same
   intro-advance `A`/`START` taps `title-progression.json` already uses
   (its own "early-title-sequence" -> "title-input-progression" ->
   "intro-menu-progression" checkpoints).
3. The hotkey (`SELECT + R`) is pulsed at frame 600, then a **second** time
   at frame 650-656 (reentrancy-guard regression coverage -- see
   "Reentrancy guard" above), before an `A` press at frame 700 selects the
   hub's one entry and arms the pending Chapter 2 launch request.
4. From frame 750 onward, ordinary `A` taps (every 30 frames through the
   `EventScr_Ch2_BeginningScene` dialogue, then every 60 frames through the
   chapter-intro event and opening NPC phase) advance the real, unmodified
   Chapter 2 flow, plus one `L` (world-map cursor jump Castle Frelia -> Ide)
   and one `A` (node confirm) at frames 1650/1700 to enter the real battle
   map, and a final `RIGHT`/`DOWN` pair at frames 14700/14760 proving the
   battle-map cursor responds to ordinary input once the map is interactive.

The debug scenario has 7 checkpoints: a pre-hotkey baseline (frame 300, all
4 basic probes zero), one right after the first hotkey pulse (frame 630,
`hubOpenCount == 1`), one right after the second, repeated pulse (frame 680,
`hubOpenCount` still `== 1` -- the reentrancy regression-proof checkpoint),
`pre-launch` (frame 706, right after the request is armed and the hub
closes -- `pendingLaunchRequest == DEBUGTOOLS_LAUNCH_REQUEST_MAGIC`, whole-
SRAM hash captured), `gamecontrol-consumed-launch` (frame 950,
`launcherArmed == DEBUGTOOLS_LAUNCHER_ARMED_MAGIC`,
`launchRequestConsumedCount == 1`, `bootstrapSuppressionActive == 1`,
`chapterIndex == CHAPTER_L_2`), `chapter2-interactive-stable` (frame 14000,
`bootstrapSuppressionActive == 0`, `playerPhaseObservedCount == 1`, the full
Blue/Green/Red unit roster present by character ID -- Eirika, Seth, Gilliam,
Franz, Moulder, Vanessa in Blue; Ross in Green; Bone + generic Bandits in
Red -- Eirika's `items[0]` low byte `== ITEM_SWORD_RAPIER`, and a whole-SRAM
hash that is **byte-for-byte identical** to `pre-launch`'s, with zero
exclusions), and `post-interactive-cursor-response` (frame 14900, after the
`RIGHT`/`DOWN` taps, proving the battle-map cursor moved -- this checkpoint's
SRAM hash is expected to differ from the first two, since ordinary
suspend-saves are no longer suppressed once past
`chapter2-interactive-stable`). The release scenario replays the exact same
frame-for-frame input (all 259 frame entries, identical to the debug
scenario) against a release build and asserts every probe field stays
`0x00000000` at all 7 checkpoints -- proving the entire subsystem is
compiled out, not merely unreached, even across the full Chapter 2 tap
sequence (which a release build's own vanilla title-to-SaveMenu handling
still processes as ordinary input, since none of it is debugtools-specific
behavior).

### Title-idle-timer freeze (`titleIdleTimerSample`)

`gDebugToolsProbe.titleIdleTimerSample` (written every `Title_IDLE` frame by
`DebugTools_RecordTitleIdleTimer()`, the sole writer, mirroring the pattern
used for every other probe field) proves the review-fix MEDIUM defect stays
fixed: `Title_IDLE`'s `proc->timer_idle`/`proc->timer` increment is now
wrapped in `if (!DebugTools_IsHubActive())`, so the counter simply does not
advance for as long as the hub is open, rather than the vanilla `== 815`
attract-mode-transition check merely being skipped *after* the counter has
already sailed past 815 (which would permanently disable the transition for
the rest of that title-screen instance, since the check is an exact
equality against a monotonically increasing value).

`tools/gba-playtest/scenarios/debugtools-timer-freeze-modern-debug.json`
opens the hub once (frame 600-606), holds it open with no further input
across a hub-free run's empirically observed `timer_idle == frame - 477`
crossing of the 815 threshold (around frame 1292), and asserts
`titleIdleTimerSample` is byte-for-byte identical at frame 620 and again at
frame 1290 -- proving it did not advance at all while blocked. It then
closes the hub with a B press (`MenuCancelSelect`'s `MENU_ACT_END` path,
frame 1300-1306) and asserts a higher value at frame 1450, proving idle
progress resumes once the hub closes. `hubOpenCount` is asserted `== 1`
throughout: opening once, holding, and closing must never spawn or require a
second hub. This is the cheaper, explicitly-permitted evidence bar ("at
minimum the timer did not advance while blocked") rather than running all
the way to the actual attract-mode transition.

No release counterpart is needed: `DebugTools_IsHubActive()` compiles out to
a constant `0` in a release build, so the freeze branch in `Title_IDLE` is
provably dead code there, and `debugtools-hub-modern-release.json` already
demonstrates `gDebugToolsProbe` stays all-zero across an equivalent-length
input window.

### Deterministic pre-launch SRAM fixture

`expansion-modern-debugtools-check` (both configs, in `modern.mk`) boots the
hub scenario with `--sram-image` pointing at a generated
`debugtools-current.sav` under `$(MODERN_OUTPUT_DIR)/debugtools-fixtures/`,
instead of genuinely blank SRAM. Without this, `EnsureGlobalSaveInfoLoaded()`
(`src/bmsave-lib.c`) sees `SAVE_COMPAT_EMPTY` on first touch and calls
`BuildCurrentExpansionSaveMeta()`, which stamps the *live*
`FE8_EXPANSION_BUILD_COMMIT` into `ExpansionSaveMeta.buildCommitShort` --
changing the `pre-launch`/`chapter2-interactive-stable` whole-SRAM hashes on
every commit even though the hub scenario deliberately keeps zero
`sram_hash_exclude_ranges` (unlike `savecompat-current.json`, which
normalizes that field away instead). The fixture is generated by
`tools/gba-playtest/tests/sram_fixture.py write-deterministic-current
<path>`: it builds a `SAVE_COMPAT_CURRENT` image identical to the ordinary
`STATE_CURRENT` fixture except `buildCommitShort` is overridden to a fixed
sentinel (`00000000`) with the checksum recomputed, so SRAM already
classifies as non-empty and `BuildCurrentExpansionSaveMeta()` never runs.
The resulting whole-SRAM hash is therefore stable across commits by
construction -- no binary fixture is committed to the repository. It is a
normal `modern.mk` build-tree file target (`MODERN_DEBUGTOOLS_SRAM_FIXTURE`):
generated once under `build/` and then cached, and only regenerated when
missing or when one of its declared prerequisites changes --
`sram_fixture.py` itself, its real generation-logic imports
(`scripts/modernize/save_format_tool.py`,
`scripts/modernize/expansion_config.py`,
`scripts/modernize/tests/test_save_format_tool.py`), or `config.mk`. Both
configs of `expansion-modern-debugtools-check` are seeded with it (the
release scenario's own fingerprint is unaffected, since it has no
`sram_hash` checkpoints to begin with); `expansion-modern-debugtools-timer-check`
is deliberately NOT seeded, since that scenario never observes SRAM either
and seeding it would only add unnecessary WipeSram-timing risk for zero
benefit.

### Regenerating scenario/fingerprint files

```sh
python3 tools/gba-playtest/gba_playtest.py capture \
  --rom build/expansion-modern/<debug|release>/aapcs/fireemblem8.gba \
  --scenario tools/gba-playtest/scenarios/debugtools-hub-modern-<config>.json \
  --sram-image build/expansion-modern/<debug|release>/aapcs/debugtools-fixtures/debugtools-current.sav \
  -o tools/gba-playtest/fingerprints/debugtools-hub-modern-<config>.json
```

The timer-freeze scenario (debug-only) is regenerated the same way:

```sh
python3 tools/gba-playtest/gba_playtest.py capture \
  --rom build/expansion-modern/debug/aapcs/fireemblem8.gba \
  --scenario tools/gba-playtest/scenarios/debugtools-timer-freeze-modern-debug.json \
  -o tools/gba-playtest/fingerprints/debugtools-timer-freeze-modern-debug.json
```

Then verify:

```sh
make expansion-modern-debugtools-check MODERN_CONFIG=<debug|release> PREFIX=arm-none-eabi-
make expansion-modern-debugtools-timer-check MODERN_CONFIG=<debug|release> PREFIX=arm-none-eabi-
```

`expansion-modern-debugtools-check` and `expansion-modern-debugtools-timer-check`
are both wired into `expansion-modern-linker-check` alongside
`expansion-modern-boot-check`/`-title-check`/`-savefmt-check`, so the
existing CI linker-check workflow picks them up without any workflow-file
edit. `expansion-modern-debugtools-timer-check` is a documented no-op (a
printed skip message, not a missing/skipped-silently check) for
`MODERN_CONFIG=release`.

## Host tests

`tools/gba-playtest/tests/test_debugtools_registry.py` (run via
`python3 -m unittest discover -s tools/gba-playtest/tests -v`) exercises the
real, unmodified sources with a native host compiler rather than
re-implementing or pattern-matching their logic:

- **`DebugToolsRegistryHostTests`** compiles+links+executes the real
  `src/debugtools_registry.c` (enabled path) against a small driver
  (`tools/gba-playtest/tests/c/debugtools_registry_driver.c`) through the
  exact public API (`include/expansion_debugtools.h`), proving: capacity is
  exactly 9, deterministic append order, `NULL`-out-of-range reads,
  duplicate id/label rejection, invalid-action (`NULL` action/label/callback)
  rejection, and capacity-full rejection on the 10th attempt -- all without
  silently dropping a registration or changing the count on a rejected call.
  A second test compiles the same source with
  `-DFE8_EXPANSION_DEBUGTOOLS_ENABLED=0` and proves both behavior (every
  entry point degrades to its disabled stub, `gDebugToolsProbe` stays
  all-zero) and physical symbol omission (`nm` finds no
  `gDebugToolsHubMenuDef`/`DebugToolsHub_*` in the disabled object). Host
  compilation uses only `-I include -I include/generated` -- deliberately
  **not** `tools/agbcc/include`, whose newlib `stdio.h` shadows the system
  one and produces an unresolvable `_impure_ptr` link error against host
  libc.
- **`DebugToolsHotkeyCollisionHostTests`** compiles small snippets against
  the real `include/expansion_debugtools.h`, proving its `#error` guards
  actually fire (zero mask, both reserved soft-reset combos) and that the
  default and a legitimate custom mask still compile cleanly.
- **`DebugToolsOneEntryPathTests`** greps the real sources to prove exactly
  one call site of `DebugTools_TitleHotkeyCheck()` (in `titlescreen.c`) and
  exactly one enabled + one disabled definition, and that
  `bmdebug.c`/`uidebug.c`/`menu_def.c` remain untouched (no `DebugTools_`
  references).
- **`DebugToolsScenarioSchemaTests`** validates both hub scenario JSON files
  against `gba_playtest`'s own schema parser, and asserts the debug/release
  scenarios use the identical (now doubled-pulse) input script while probing
  their own config-specific addresses with complementary (not identical)
  expectations; asserts the debug scenario replays exactly two separate
  `SELECT + R` pulses before the `A` press (the reviewer's exact
  release-and-repress repro shape); and asserts `hubOpenCount` reads `1` at
  the checkpoint right after both the first and the second pulse (the
  critical regression-proof assertion for the reentrancy-guard fix).
- **`DebugToolsTimerFreezeTests`** proves the idle-timer-freeze fix two ways:
  structurally, by grepping `Title_IDLE`'s function body to confirm the
  `DebugTools_IsHubActive()` guard textually precedes, and actually wraps,
  the `proc->timer_idle++` statement (so the increment is truly conditional,
  not merely preceded by an unrelated check); and via the timer-freeze
  scenario's own JSON, parsing `titleIdleTimerSample`'s expected hex values
  as integers to assert the two "hub held open" checkpoints are exactly
  equal (frozen) and the post-close checkpoint is strictly greater (resumed)
  -- reasoning the scenario JSON schema itself cannot express (no relational
  operators), so it is asserted here in the host test instead.
- **`DebugToolsChapter2LaunchLifecycleHostTests`** proves the pending-request
  handoff itself, combining host-executed behavior with link-time and
  structural (comment-stripped, so explanatory prose about what the code
  must *not* do can never itself satisfy or fail an assertion) proofs:
  - `test_launcher_pending_request_lifecycle_host_executed` compiles+links+
    executes the real `src/debugtools_launcher.c` (enabled path) -- driven
    by `tools/gba-playtest/tests/c/debugtools_launcher_driver.c` calling
    the real public API directly -- against
    `tools/gba-playtest/tests/c/debugtools_launcher_host_stubs.c`, which
    instruments (call-counts) `Proc_Start`/`Proc_Find`/`Proc_Break` but
    **deliberately never defines** `Proc_EndEach`, `gProcScr_GameControl`,
    or `gProc_BMapMain` -- so any reference to them anywhere in the linked
    object graph would itself be a link failure, not just a missed
    assertion. It proves: arming the request never calls
    `Proc_Start`/`Proc_Break`; a duplicate arm changes nothing observable;
    consuming is one-shot (`DebugTools_ConsumePendingChapter2Launch()`
    returns true exactly once per arm, then false); consuming an empty
    request is a no-op; arming bootstrap suppression starts exactly one
    proc (the observer); and the whole cycle is re-armable across repeated
    boots.
  - `test_launcher_disabled_path_is_noop` compiles+links+executes
    `tools/gba-playtest/tests/c/debugtools_launcher_disabled_driver.c`
    standalone (no stubs/registry needed) with
    `-DFE8_EXPANSION_DEBUGTOOLS_ENABLED=0`, proving every public
    pending-request/suppression entry point degrades to an inert stub, and
    `nm` confirms the hub-internal symbols are physically omitted.
  - `test_launcher_never_references_gamectrl_or_proc_endeach` and
    `test_gamecontrol_chapter2_boot_never_bypasses_events_or_manually_loads_units`
    grep the real source text (with C comments stripped first) to
    structurally confirm `src/debugtools_launcher.c` never mentions
    `Proc_EndEach`/`gProcScr_GameControl`/`gProc_BMapMain` in code, and that
    `GameControl_PostIntro`'s Chapter 2 boot branch only ever writes
    `gGMData.units[0].location` (the single, documented, ordinary-world-map-
    traversal placement) and no other `gGMData.units[]` field.
  - `test_title_idle_defers_pending_request_check_until_hub_inactive` and
    `test_title_idle_pending_branch_never_synthesizes_input` grep
    `Title_IDLE`'s function body to confirm `DebugTools_IsHubActive()` is
    checked (with an early return) strictly before
    `DebugTools_IsChapter2LaunchPending()`, and that the pending branch
    reacts with the same `SetNextGameActionId`/`Proc_Break` pair the
    ordinary `A`/`START` branch uses, never a synthesized keypress.
  - `test_gamecontrol_consumes_pending_launch_exactly_once_before_savemenu`
    confirms `DebugTools_ConsumePendingChapter2Launch()` is called exactly
    once in `src/gamecontrol.c`, textually before the ordinary
    `LGAMECTRL_EXEC_SAVEMENU` branch.
- **Observer lifecycle-safety host tests** (added after a review gate found
  the original design could leak suppression/observers on an abandoned run)
  drive the same real `src/debugtools_launcher.c` through
  `tools/gba-playtest/tests/c/debugtools_launcher_driver.c` and prove, purely
  via the public API and `gDebugToolsProbe` counters:
  - arming twice in a row starts exactly one observer proc alive at a time
    (`bootstrapObserverArmCount` increments each call, but `Proc_Start`'s
    net-alive-observer count never exceeds one -- the second arm's internal
    `DebugTools_CleanupBootstrapObserver()` ends the first before starting
    the second) and suppression is active after both;
  - an explicit `DebugTools_CleanupBootstrapObserver()` call ends the
    observer and clears `bootstrapSuppressionActive` to false;
  - `DebugTools_NotifyTitleScreenStarting()` is a no-op while suppression is
    inactive, clears suppression (and increments `observerTitleReturnCount`)
    while active, and is safe to call repeatedly;
  - re-arming after a cleanup starts a fresh observer and suppression
    becomes active again (proving the cycle is not a one-shot-forever
    latch).
  - `test_wait_for_stable_player_phase_never_polls_title_screen_via_proc_find`,
    `test_observer_has_exactly_two_poll_termination_conditions_each_ending_via_proc_break`,
    and `test_notify_title_screen_starting_is_a_noop_unless_suppression_active`
    grep the real source text to structurally confirm the observer's poll
    body never references `gProcScr_TitleScreen`/`Proc_Find` for title
    detection (only the success/timeout branches remain, each ending via
    `Proc_Break`), and that title-return detection is exclusively the
    event-driven `DebugTools_NotifyTitleScreenStarting()` no-op-when-inactive
    entry point.
  - `test_titlescreen_calls_notify_title_screen_starting_from_all_three_start_functions`
    greps `src/titlescreen.c` to confirm all three `StartTitleScreen_*`
    functions call `DebugTools_NotifyTitleScreenStarting()` before starting
    `gProcScr_TitleScreen` -- so every real path that (re)shows the title
    screen is covered, not just one.

Test-only fixture sources under `tools/gba-playtest/tests/c/` (stub
implementations of the handful of hardware/menu-engine symbols the
registration logic references but a registration-focused host test never
needs to execute) are never referenced by `modern.mk`/`Makefile` and are not
part of the shipped feature.

## Safety boundaries / extension rules

- Contributors add debug actions **exclusively** through
  `DebugTools_RegisterAction()`. Never edit `gDebugToolsHubMenuDef` or
  `sHubMenuItemDefs` directly, and never add a second title-screen (or any
  other) hotkey call site -- this slice's one entry path is a hard
  constraint, not a starting convention.
- `src/bmdebug.c`, `src/uidebug.c`, and `src/menu_def.c` remain untouched and
  unreachable in this slice. No save/gold/unit/convoy/flag/event/RNG editors
  are exposed.
- No proc is ever torn down/recreated by this feature (no
  `Proc_EndEach`/`Proc_Start` on `gProcScr_GameControl`, no `gProc_BMapMain`
  redirect) -- the hub only arms/consumes a pending-request flag, and both
  `Title_IDLE`'s `Proc_Break` and `GameControl_PostIntro`'s `Proc_Goto` act
  on procs that already exist and keep running their own ordinary
  lifecycle.
- The one-shot bootstrap-suppression window (`src/bm.c`,
  `src/playerphase.c`, `src/soundwrapper.c`) only ever gates the automatic
  per-phase/song-unlock persistent writes that happen to fall inside the
  deterministic Chapter 2 boot's own opening transition; it is armed once
  per boot and is guaranteed to clear via one of three bounded paths: the
  first stable Player Phase, an abandoned run returning to the title screen
  (`DebugTools_NotifyTitleScreenStarting()`), or a timeout safely above any
  normal boot duration -- so no abandoned/failed launch can leave
  suppression (or its observer proc) stuck active for a later, real user
  session. Re-arming is idempotent: it always cleans up any stale
  observer/flag before starting exactly one new one. Outside the
  suppression window, and for every ordinary user-triggered Suspend or song
  unlock, nothing in this slice performs a persistent SRAM write, uses
  wall-clock/time-based state, or introduces nondeterministic RNG.

## Downstream usage: issue #2's deep write -> reload proof

This launcher is the general clean-boot game-state fixture issue #2's
save-format work depended on but did not own. `tools/gba-playtest/
scenarios/savesuspend-resume-modern-debug.json` reuses the hub scenario's
own frames verbatim (Fast Boot: Chapter 2 hotkey -> hub -> launcher) as
its prefix, then drives an ordinary manual Map Menu **Suspend**, a real
**soft-reset** combo, and the ordinary title/save-menu **Resume** path to
prove genuine SRAM write/read persistence through the engine's normal
save paths -- see `docs/save_format.md`'s "Save/load acceptance status"
section for the full proof and probes. This closes issue #2's
previously-deferred write -> reload acceptance gap, and is why the
launcher above stays debug-only rather than release-eligible: release
builds have no equivalent deterministic entry point to drive this
scenario from, so this proof (like the launcher itself) does not exist
for `MODERN_CONFIG=release`.

## Remaining #11 scope (explicitly NOT done in this slice)

This slice does **not** close issue #11. Deferred to later slices:

- Map/prep-screen debug entry points (this slice's one entry path is
  title-screen-only).
- Migrating the existing dormant chapter/weather/fog/BGM tools out of
  `bmdebug.c`/`uidebug.c`/`menu_def.c` into the new registration API.
- Any validated editors/viewers (save/gold/unit/convoy/flag/event/RNG or
  otherwise).
- Full logging/assert/crash/memory-inspection tooling.
- A complete `mgba_printf`/AGB debug print-protocol implementation (the
  on-screen BG2 diagnostic line is this slice's retained, always-visible
  substitute).
