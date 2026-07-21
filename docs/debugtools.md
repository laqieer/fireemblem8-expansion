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
| `include/expansion_debugtools.h` | Public contract: config gate, hotkey mask + compile-time collision guards, registration API, `struct DebugToolsProbe` |
| `src/debugtools_registry.c` | Registry storage, hub menu construction/diagnostics, hotkey check, `gDebugToolsProbe` |
| `src/debugtools_launcher.c` | The slice's one built-in action ("Fast Boot: Prologue") |
| `src/titlescreen.c` | The single supported call site (`Title_IDLE`) |
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

## Deterministic launcher: "Fast Boot: Prologue"

The slice's one built-in action (`src/debugtools_launcher.c`), registered
lazily (idempotently) the first time the hub opens. Selecting it:

1. Sets `gDebugToolsProbe.launcherArmed = DEBUGTOOLS_LAUNCHER_ARMED_MAGIC`
   (`0x44424C31`, ASCII `"DBL1"`) -- committed-to-boot evidence for playtest
   probes.
2. **Deterministic RNG policy**: reseeds to the exact fixed constant
   `AgbMain`'s own clean-boot seed uses (`src/main.c`), regardless of how
   many RNG draws already happened this session -- the launcher's outcome is
   independent of prior play.
3. Reuses `GameControl_InitTutorialGame`'s own bootstrap sequence
   (`src/gamecontrol.c`) -- `InitPlayConfig`, `ResetPermanentFlags`,
   `ResetChapterFlags`, `InitUnits` -- minus the tutorial flag, then sets
   `gPlaySt.chapterIndex = CHAPTER_L_PROLOGUE`. None of these touch SRAM;
   `InitPlayConfig` only zeroes the EWRAM `gPlaySt` struct.
4. Tears down and restarts the entire `GameCtrlProc` tree via
   `Proc_EndEach(gProcScr_GameControl)` + `Proc_Start` + `Proc_Goto(...,
   LGAMECTRL_EXEC_BM_EXT)` -- the same "EndEach + Start + Goto" idiom already
   used by `RestartGameAndGoto8`/`RestartGameAndGoto12`, and already proven
   safe from inside a `MenuItemDef::onSelected` callback by the dormant
   `DebugContinueMenu_ManualContinue`/`InitializeFile` handlers in
   `src/bmdebug.c`. `LGAMECTRL_EXEC_BM_EXT` bypasses both the world map and
   the interactive Save Menu entirely, so **no SRAM is ever read or
   written**.

This is intentionally a thin reuse of existing game-control/chapter-start
routines, not a reimplementation of state setup, and is the mechanism issue
#2's later deep save/write-reload scenarios are expected to build on.

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
    u32 lastRegisterResult;  /* enum DebugToolsResult */
    u32 launcherArmed;       /* DEBUGTOOLS_LAUNCHER_ARMED_MAGIC once armed */
    u32 titleIdleTimerSample; /* mirrors proc->timer_idle every Title_IDLE frame */
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
   "intro-menu-progression" checkpoints) -- confirmed empirically: with no
   input, `Title_IDLE` never starts ticking inside a multi-thousand-frame
   window; with the intro taps replayed, it starts ticking almost
   immediately after the last of them and keeps ticking every frame (`newKeys`
   readable) until either 815 idle frames elapse (`GAME_ACTION_CLASS_REEL`,
   the vanilla attract-mode transition) or an `A`/`START` press advances past
   it.
3. The hotkey (`SELECT + R`) is therefore pulsed at frame 600 -- comfortably
   inside that window, well clear of both the input-masked intro and the
   815-frame idle timeout. It is then pulsed a **second** time at frame
   650-656 (review-fix regression coverage -- see "Reentrancy guard" above)
   before an `A` press at frame 700 selects the hub's one entry (the built-in
   launcher).

The debug scenario has 5 checkpoints: a pre-hotkey baseline (frame 300, all
probes zero), one right after the first hotkey pulse (frame 630,
`hubOpenCount == 1`), one right after the second, repeated pulse (frame 680,
`hubOpenCount` still `== 1` -- the critical regression-proof checkpoint), one
after the launcher is selected (frame 950, `lastRegisterResult ==
DEBUGTOOLS_OK (0)` and `launcherArmed == DEBUGTOOLS_LAUNCHER_ARMED_MAGIC`),
and a final frame-1550 stability checkpoint (the chapter map's own animation
cycling changes `framebuffer_hash` between checkpoints, as expected for a
live, running chapter -- the probe fields, not the hash, are the "stable"
evidence). The release scenario asserts every probe field stays
`0x00000000` at every one of the same 5 checkpoints, given the exact same
(now-doubled-pulse) input.

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

### Regenerating scenario/fingerprint files

```sh
python3 tools/gba-playtest/gba_playtest.py capture \
  --rom build/expansion-modern/<debug|release>/aapcs/fireemblem8.gba \
  --scenario tools/gba-playtest/scenarios/debugtools-hub-modern-<config>.json \
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
- Nothing in this slice performs a persistent SRAM write, uses wall-clock/
  time-based state, or introduces nondeterministic RNG.

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
