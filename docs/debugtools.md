# Debug-tools subsystem (issues #11, #125, and #126)

This document is the single reference for the debug-tools subsystem built
for issue #11: a release-safe config gate; a fixed-capacity, hardened
contributor action-registration API (capacity/id/label/callback/reentrancy
all explicitly validated); title/map/prep-screen hub hotkeys; the Chapter 2
deterministic launcher; the issue #123 bounded chapter/skirmish selector
(which supersedes the former standalone Chapter 4 action at stable built-in
ID 4); the Weather/Fog actions; a bounded diagnostics foundation (log
ring + non-fatal assert record); five bounded validated tools (unit,
convoy, flag/chapter, RNG, save-state); issue #125's cursor-selected typed
unit inspector/editor; issue #126's bounded reversible music-preview action;
and the playtest/host-test evidence that proves all of it. See
`reports/debugtools_issue11_closure.md` for issue #11's original
frozen-checklist mapping. The tester procedures are
[`TC-DEBUGTOOLS-PROTOTYPE-003`](test-cases/debugtools.md#tc-debugtools-prototype-003-cursor-selected-bounded-unit-inspectoreditor)
and
[`TC-DEBUGTOOLS-PROTOTYPE-004`](test-cases/debugtools.md#tc-debugtools-prototype-004-preview-bounded-music-and-restore-its-owner).

## Files

| File | Role |
| --- | --- |
| `include/expansion_debugtools.h` | Public contract: config gate, title/map/prep hotkey masks + compile-time collision guards, registration API, pending-request/bootstrap-suppression API, `struct DebugToolsProbe` |
| `src/debugtools_registry.c` | Registry storage, hub menu construction/diagnostics, title/map/prep hotkey checks, `gDebugToolsProbe` |
| `src/debugtools_launcher.c` | The built-in "Fast Boot: Chapter 2" action, bootstrap-suppression observer, and source-compatible Chapter 4 initializer alias |
| `src/debugtools_selector.c` | Built-in ID-4 Chapter/Skirmish submenu, metadata-derived target enumeration, typed one-shot request, and deferred live-map/prep GameControl handoff |
| `src/debugtools_actions.c` (slice 2) | Built-in Weather/Fog actions: registers each as a bounded one-item submenu whose `MenuItemDef` reuses the dormant `DebugMenu_Weather*`/`DebugMenu_Fog*` functions in `src/bmdebug.c` by pointer, with its own Back/B handling |
| `src/titlescreen.c` | The title-screen hotkey call site (`Title_IDLE`); also detects the pending launch request after the hub MenuProc closes, before deferred allocator cleanup releases session ownership |
| `src/playerphase.c` (slice 2) | The map-phase hotkey call site: `PlayerPhase_MainIdle` calls `DebugTools_MapHotkeyCheck()` and returns immediately while the hub is active, as its first statements |
| `src/prep_sallycursor.c` (slice 2) | The prep-screen hotkey call site: `PrepScreenProc_MapIdle` calls `DebugTools_PrepHotkeyCheck()` and returns immediately while the hub is active, as its first statements |
| `src/gamecontrol.c` | `GameControl_PostIntro` consumes the pending request exactly once and performs the actual deterministic boot |
| `src/bm.c`, `src/playerphase.c`, `src/soundwrapper.c` | Narrow, one-shot bootstrap-suppression guards on the automatic per-phase suspend-save calls (`BmMain_SuspendBeforePhase`, `PlayerPhase_Suspend`) and the song-room unlock write (`UnlockSoundRoomSong`) |
| `tools/gba-playtest/scenarios/debugtools-hub-modern-{debug,release}.json` | Slice 1 title-hub playtest scenarios (input script + probe expectations) |
| `tools/gba-playtest/scenarios/debugtools-map-hub-modern-debug.json` (slice 2) | Live map-phase hub scenario: opens the hub with the map mask on the real, interactive Chapter 2 map, exercises Weather then Fog, and proves the map stays interactive after the hub closes |
| `tools/gba-playtest/scenarios/debugtools-{map,prep}-hub-modern-release.json` (slice 2) | Release mirrors proving both new hotkeys are compiled out (`gDebugToolsProbe` stays all-zero) atop the live opening world map -- semantic `gPlaySt`/cursor progress probes, no framebuffer oracle |
| `tools/gba-playtest/fingerprints/debugtools-{hub,map-hub,prep-hub}-modern-{debug,release}.json` | Captured fingerprints for the scenarios above |
| `src/debugtools_diag.c` (closure) | Diagnostics foundation: bounded log ring (`DebugTools_LogEvent`/`GetLogEntry`/`GetLogCount`) and non-fatal assert record (`DEBUGTOOLS_ASSERT`/`DebugTools_RecordAssertFailure`) |
| `src/debugtools_tools.c` (closure + issue #125) | The five bounded validated tools. Unit Inspect resolves the live cursor target and exposes typed HP/stat/AI/status edits; Convoy Inspect, Flag/Chapter, RNG Inspect, and read-only Save State retain their issue #11 contracts |
| `src/gamecontrol.c` (issue #123) | `GameControl_PostIntro` consumes the validated typed selector request, creates only transient chapter/skirmish state, arms boot-write suppression, and selects the direct or explicit Chapter 4 compatibility route from request provenance |
| `tools/gba-playtest/scenarios/debugtools-selector-{chapter,skirmish}-modern-debug.json` | Title-origin Chapter 4 and live-map-origin Chapter 4 skirmish routes, including exact-once request/consume/handoff, interactive destinations, and byte-identical pre/post SRAM hashes |
| `tools/gba-playtest/scenarios/debugtools-selector-modern-release.json` | Exact-input release mirror proving selector/action/request/handoff behavior and the private selector state symbol are omitted while the unchanged modern release debugtools probe stays zero |
| `src/debugtools_music.c` (issue #126) | Built-in ID 10: authoritative song navigation, localized names, one typed preview owner, exact restoration, telemetry, and forced cleanup |
| `tools/gba-playtest/scenarios/debugtools-ch4-prep-positive-modern-debug.json` (closure) | Live prep-screen arrival (debug-only): drives the Chapter 4 world-map traversal + the real `PREP` opcode to a live `PrepScreenProc_MapIdle`, then fires the SELECT+B prep hotkey; proves `prepScreenObservedCount` 0->1 and `PLAY_FLAG_PREPSCREEN` held throughout. Gate: DEBUG branch of `expansion-modern-debugtools-prep-check` |
| `tools/gba-playtest/tests/test_debugtools_registry.py` + `tools/gba-playtest/tests/c/*.c` | Host tests (see "Host tests" below) |

This feature deliberately does **not** touch `src/bmdebug.c`, `src/uidebug.c`,
or `src/menu_def.c` -- those dormant tools stay unreachable, and the
Weather/Fog actions only ever reference their existing
`DebugMenu_WeatherDraw/Idle`/`DebugMenu_FogDraw/Idle` functions **by
pointer** from `src/debugtools_actions.c`. `src/debugtools_diag.c` and
`src/debugtools_tools.c` (issue #11 closure) follow the identical
by-pointer/never-copy discipline for the engine helpers they call
(`SetUnitHp`, `UnitCheckStatCaps`, `ChangeUnitAi`, `SetUnitStatus`,
`AddItemToConvoy`, `SetFlag`/`ClearFlag`, `SetLCGRNValue`,
`ClassifySramSaveCompat`, etc. -- see "Five bounded validated tools" below).

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
  `src/debugtools_registry.c`/`src/debugtools_launcher.c`/
  `src/debugtools_music.c` compiles to a
  trivial disabled-result stub under `#else /* !FE8_EXPANSION_DEBUGTOOLS_ENABLED */`,
  and the hub menu table (`gDebugToolsHubMenuDef`), every hub-internal static
  function, and the launcher's action body **do not exist in the link at
  all** -- not merely unreachable at runtime. Verified by `nm` on the linked
  release ELF (no `DebugToolsHub_*`/`gDebugToolsHubMenuDef`/
  `DebugToolsLauncher_*`/`DebugToolsMusic_*`/typed preview-owner and transient
  sound-helper/selector symbols) and by equivalent host-compiled checks
  (`test_registry_disabled_path_behavior_and_symbol_omission`).
- The archival agbcc lane excludes the supported subsystem behind
  `FE8_ARCHIVAL_BUILD`; issues #123, #125, and #126 add no archival runtime
  behavior or byte-match requirement.

`gDebugToolsProbe` (see "Playtest probe surface" below) is the one exception:
it is always linked in modern debug and release builds, so a release scenario can assert it
stays all-zero for a whole run.

## Registration API

Fits the existing `MenuProc`/`MenuItemDef` engine (`include/uimenu.h`,
`src/uimenu.c`) rather than replacing it:

- `MENU_ITEM_MAX` is 11, and `StartMenuCore` has **no bounds check** when it
  appends to `MenuProc::menuItems` -- writing a 12th live item would corrupt
  adjacent `MenuProc` fields. Each hub page therefore renders at most
  `DEBUGTOOLS_HUB_PAGE_ACTION_MAX` (9) actions plus Back/Exit, leaving 1 of
  the 11 live slots as an untouched safety margin. The shipped built-ins
  occupy page one; pressing R queues the contributor page when present.
  `ProcessMenuSelectInput()` intentionally ignores `onRPress`'s return
  value, and `Menu_OnIdle()` continues using the current `MenuProc` after
  that callback. The hub therefore only records the target page, schedules
  its transition Proc, and freezes input in `onRPress`; after the current
  dispatcher call returns, the Proc's leading yield reaches a safe point,
  ends the old menu, and starts the next page. It never calls `EndMenu()`
  synchronously from `onRPress`.
- Contributors register through `DebugTools_RegisterAction()` only -- they
  never edit an engine-owned `const MenuItemDef` table. A RAM-resident
  `MenuItemDef` adapter (`sHubMenuItemDefs`, sized
  `DEBUGTOOLS_HUB_MENU_SLOTS = 11`) is rebuilt from the current registry page
  every time the hub opens. Built-in actions, Back/Confirm rows,
  transfer progress, and status diagnostics resolve stable expansion message
  IDs for `en`/`ja`/`zh-Hans`; CJK diagnostics use the UTF-8-aware system text
  renderer. IDs 1-10 are reserved built-in identities and can only enter
  through the private built-in registration path, so localized label lookup
  cannot be selected by a contributor-controlled ID. Contributor IDs are
  explicitly limited to 11-65535 and keep the original raw-string
  ABI/rendering path.
- The array is fully zeroed before every rebuild, so the first unused slot
  (and everything after it) reads as an all-zero `MenuItemsEnd` -- exactly
  what stops `StartMenuCore`'s scan loop. The reserved Back entry is always
  written immediately after the last action visible on that page; the
  terminator is the slot after Back.
- Storage is two fixed-size EWRAM arrays: ten immutable-identity built-in
  slots (`DEBUGTOOLS_BUILTIN_ACTION_MAX`) and nine public contributor slots
  (`DEBUGTOOLS_CONTRIBUTOR_ACTION_MAX`). `DEBUGTOOLS_ACTION_MAX` is their
  combined introspection capacity (19). The added contributor/page state is
  linked at the end of the existing EWRAM layout so public probes and later
  runtime state keep their established addresses. There is no heap allocation.
- Built-in storage is ID-indexed: ID `N` always occupies slot `N-1`.
  Introspection scans sparse slots in ascending ID order, so built-ins stay in
  ID/menu order 1-10 even when any public built-in initializer
  (`DebugTools_RegisterBuiltinActions`, `DebugTools_RegisterWeatherFogActions`,
  `DebugTools_RegisterChapter4PrepAction`, or
  `DebugTools_RegisterExtendedToolActions`) is called first. Weather and Fog
  therefore remain hub row indices 1 and 2. Repeating any initializer is a
  successful no-op that preserves the current count, result, labels, and
  callbacks. Contributors follow in append-only registration order on their
  separate page.

### Result codes

`DebugTools_RegisterAction()` always returns one of `enum DebugToolsResult`;
a registration failure is never silently dropped:

| Code | Meaning |
| --- | --- |
| `DEBUGTOOLS_OK` | Registered successfully |
| `DEBUGTOOLS_ERR_DISABLED` | Subsystem compiled out (release build) |
| `DEBUGTOOLS_ERR_INVALID_ACTION` | `NULL` action pointer, `label`, or `onSelected` |
| `DEBUGTOOLS_ERR_DUPLICATE` | `id` or `label` already registered |
| `DEBUGTOOLS_ERR_CAPACITY_FULL` | The nine-slot contributor storage (or private built-in storage) is already full |
| `DEBUGTOOLS_ERR_ALREADY_ACTIVE` | `DebugTools_OpenHub()` called while the hub is already open |
| `DEBUGTOOLS_ERR_ID_INVALID` (closure) | `action->id == 0` (reserved/uninitialized-looking sentinel; every shipped action uses ids 1-10) |
| `DEBUGTOOLS_ERR_LABEL_INVALID` (closure) | `label` is empty (`""`) or longer than `DEBUGTOOLS_LABEL_MAX_LENGTH` (24) |
| `DEBUGTOOLS_ERR_ID_RESERVED` | Public contributor attempted to claim built-in ID 1-10; valid contributor IDs are 11-65535 |
| `DEBUGTOOLS_ERR_TEXT_CAPACITY` | The active font cannot fit one maximum hub/status allocation |

All added closure codes are appended at the **end** of `enum DebugToolsResult` so
every pre-existing named value keeps its original integer -- several
scenario JSON files probe `gDebugToolsProbe.lastRegisterResult` by raw
integer, so no existing value may ever be renumbered. Label validation does
not copy or retain any bytes beyond the pointer itself
(`sContributorActions[sContributorActionCount] = *action` in
`src/debugtools_registry.c` still only stores the pointer) --
contributors remain responsible for passing a label with static/persistent
storage duration, which every action in this codebase already does by using
a plain C string literal; the length bound is a rendering/policy contract,
not a lifetime check C89 can perform at runtime. See
`tools/gba-playtest/tests/c/debugtools_registry_label_validation_driver.c`
for the host-executed proof of both new codes (including the exact
boundary: a label of exactly `DEBUGTOOLS_LABEL_MAX_LENGTH` characters is
accepted, one character over is rejected).

`DebugTools_GetLastRegistrationResult()` always mirrors the most recent call.
`gDebugToolsProbe.lastRegisterResult` mirrors the same value for playtest
probes.

Built-ins are initialized exactly once, in menu/ID order 1-10, before a
valid public contributor registration is admitted. A contributor call made
before the first hub open therefore cannot occupy a built-in slot and later
acquire that built-in's localized label while retaining a different
callback. The first valid contributor ID (11 or greater) succeeds, all nine
documented contributor slots can coexist with all ten built-ins, and only
the tenth contributor receives `DEBUGTOOLS_ERR_CAPACITY_FULL`.

## Text allocator lifecycle

`StartMenuCore()` calls `InitText(&item->text, rect.w - 1)` for every live
row, and `InitText` monotonically advances the active font's
`chr_counter`. The debug hub used to start each submenu before the hub had
ended and reopen a fresh hub from the submenu's `onEnd`; repeated
hub→submenu→hub cycles therefore accumulated allocations indefinitely.

Immediately before every debug-owned `StartOrphanMenu()`,
`DebugTools_StartOwnedMenu()` captures the exact active font that will own
the row allocations, its pre-allocation `chr_counter`, and the active font
that must be restored later. This happens synchronously with
`StartMenuCore()`'s row allocation and before the menu Proc reaches
`MenuDef::onInit`; a contributor submenu may therefore switch `gActiveFont`
in `onInit` without changing which font owns those already-allocated rows.

Selecting a submenu starts `gProcScr_DebugToolsMenuTransition`; Back starts
the same helper from `onEnd`. R pagination also starts it, but unlike
ordinary `MENU_ACT_END` selection it stores the still-live hub pointer,
freezes the menu, and lets the transition Proc call `EndMenu()` only after
its leading `PROC_YIELD`. The transition then rewinds only the captured row
owner's counter, restores the captured active font as a separate operation,
and starts the next menu. It never rewinds the font that merely happens to
be global after a contributor `onInit`, and never overwrites that unrelated
font's counter. Final Back uses the same deferred path before releasing the
session guard. No live Text is rewound early, and the title/map/prep input
guard remains active across submenus and the one-frame transition.

The default BG font has 448 allocator columns available from tile `0x80`
through tile index `0x3FF` (two 8x8 tiles per text column). Each maximum hub
page uses `10 * 18 = 180` columns for nine actions plus Back; the largest
CJK status line adds 24, for a checked worst-case budget of 204. Opening is
rejected with `DEBUGTOOLS_ERR_TEXT_CAPACITY` if the current baseline plus
that budget would exceed the active font's capacity. Host tests fill all
18 registrations, page between both full rows, execute 64
hub→submenu→hub/page cycles, and prove every reopened page returns to the
same 204-column peak while final cleanup restores the original baseline.

### Contributor submenu contract

A contributor action that needs its own `MenuDef` must use the public handoff
pair in `include/expansion_debugtools.h`; directly calling `StartOrphanMenu`
from the action callback is unsupported because it bypasses allocator/session
ownership:

```c
static struct Font gMyDebugFont;

static void MyDebugSubmenu_OnInit(struct MenuProc* menu)
{
    (void)menu;
    SetTextFont(&gMyDebugFont);
    /* Any gMyDebugFont allocations remain contributor-owned. */
}

static void MyDebugSubmenu_OnEnd(struct MenuProc* menu)
{
    DebugTools_ReturnToHubAfterMenuEnd(menu);
}

static u8 MyDebugAction_Selected(struct MenuProc* menu, struct MenuItemProc* item)
{
    (void)item;

    MyDebugSubmenu_BuildMenuItems();
    DebugTools_QueueSubmenuTransition(menu, &gMyDebugSubmenuDef);

    return MENU_ACT_SKIPCURSOR | MENU_ACT_END | MENU_ACT_SND6A | MENU_ACT_CLEAR;
}
```

The action must queue the submenu **before** returning a result containing
`MENU_ACT_END`. That queued ownership marker makes the hub's ordinary
`MenuDef::onEnd` skip final cleanup, so no live hub `Text` is rewound
prematurely. The submenu's own `MenuDef::onEnd` must call
`DebugTools_ReturnToHubAfterMenuEnd`; this waits one yield for the submenu
objects to die, reclaims the same bounded row-allocation scope, restores the
font that was active before `MyDebugSubmenu_OnInit`, and reopens the hub
without releasing the session/reentrancy guard. It does not reset
`gMyDebugFont.chr_counter`; allocations deliberately made from that font are
the contributor's responsibility. Disabled builds expose inert stubs, and
calls outside an active debug session are safe no-ops.

### Introspection

`DebugTools_GetRegisteredCount()` / `DebugTools_GetRegisteredAction(index)`
(bounds-checked, `NULL` outside `[0, count)`) expose the combined sequence:
the ten built-ins first, then contributors in registration order.

## Diagnostics / visible feedback

Registration/input failures are not silent: `DebugToolsHub_ShowDiagnostics()`
reuses the existing on-screen debug font (`SetupDebugFontForBG`/
`PrintDebugStringToBG`, `src/fontgrp.c` -- the same mechanism already proven
by the dormant debug menus) to print either `"DBGTOOLS ERR <code>"` (last
registration result was not `DEBUGTOOLS_OK`) or the count/page form
`"DBGTOOLS <n>/18 <page>/2"` when contributors are present. The built-in-only
profile retains its existing `"DBGTOOLS 9/9"` line. A full
`mgba_printf`/AGB print-protocol
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
whole hub-entry surface: it checks the complete debug-menu session state at
the very top, before any side effect (built-in initialization, menu-item
construction, diagnostics, `hubOpenCount` increment, or
`StartOrphanMenu()`), and returns `DEBUGTOOLS_ERR_ALREADY_ACTIVE` as a pure
no-op if the hub, a submenu, or an allocator transition is already active.

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

## Map/prep-screen hub entry points (slice 2)

Slice 2 adds two more, equally narrow, entry paths -- one per phase --
both routed through the same `DebugTools_OpenHub()` reentrancy guard
documented above, so no new busy-check is needed at either call site:

```c
#ifndef FE8_EXPANSION_DEBUGTOOLS_MAP_HOTKEY_MASK
#define FE8_EXPANSION_DEBUGTOOLS_MAP_HOTKEY_MASK (SELECT_BUTTON | L_BUTTON)
#endif
#ifndef FE8_EXPANSION_DEBUGTOOLS_PREP_HOTKEY_MASK
#define FE8_EXPANSION_DEBUGTOOLS_PREP_HOTKEY_MASK (SELECT_BUTTON | B_BUTTON)
#endif
```

- `PlayerPhase_MainIdle` (`src/playerphase.c`) calls
  `DebugTools_MapHotkeyCheck()` as its **first** statement, then
  `if (DebugTools_IsHubActive()) return;` immediately after -- before any of
  its own cursor/menu handling runs -- so a triggering keypress can never
  also be seen by the ordinary map controls on the same frame.
- `PrepScreenProc_MapIdle` (`src/prep_sallycursor.c`) follows the identical
  pattern with `DebugTools_PrepHotkeyCheck()`, ahead of
  `HandlePlayerCursorMovement()`.
- Each mask has its own full set of compile-time `#error` guards, checked
  unconditionally (even in release builds): must not be `0`; must not equal
  either existing soft-reset combo (`L+R+A+B` or `A+B+SELECT+START`); must
  not equal bare `R_BUTTON` (map/prep stat screen), bare `L_BUTTON`
  (view-unit swap), or bare `START_BUTTON` (minimap) at that phase; and must
  not equal the title mask nor **each other** -- so no two of the three
  hotkeys can ever be confused for one another by a single keypress.
- Both defaults (`SELECT+L`, `SELECT+B`) are deliberately distinct from the
  title mask (`SELECT+R`) and from each other, and from both existing
  soft-reset combos.
- Both compile to an empty, explicit disabled/release stub -- no key read,
  no `DebugTools_OpenHub()` call -- when the subsystem is disabled, exactly
  like `DebugTools_TitleHotkeyCheck()`.
- Registration remains deterministic regardless of which phase opens the
  hub: the registry runs the built-in initialization sequence exactly once,
  in final menu order. Subsequent hub opens do not re-register or perturb
  `lastRegisterResult`; total registered actions remain exactly the fixed
  capacity of 9.

## Weather/Fog debug actions (slice 2)

`src/debugtools_actions.c` registers two built-in actions, id `2`
("Weather") and id `3` ("Fog"), through the internal built-in path; the
public API rejects those reserved IDs, keeping their label/callback identity
immutable to contributors -- no direct edits to `gDebugToolsHubMenuDef`/
`sHubMenuItemDefs`, `src/bmdebug.c`, `src/menu_def.c`, or `src/uidebug.c`.

Because registry actions are `onSelected`-only (a single callback fired when
the hub's own menu selects that row), and the dormant `DebugMenu_Weather*`/
`DebugMenu_Fog*` functions in `src/bmdebug.c` are themselves full
`MenuItemDef` callback sets (`onDraw`/`onIdle`), each action's
`onSelected` handler (`DebugToolsActions_WeatherSelected`/
`DebugToolsActions_FogSelected`) opens a **bounded, one-item submenu**
(`StartOrphanMenu` over a single-entry `MenuDef`) whose one `MenuItemDef`
reuses the existing dormant `DebugMenu_WeatherDraw`/`DebugMenu_WeatherIdle`
(or `DebugMenu_FogDraw`/`DebugMenu_FogIdle`) function pointers directly --
the dormant code itself is never edited, copied, or reimplemented. Each
submenu adds its own `B`/Back handling (`onCancel`/the submenu's own "Back"
row). Its `onEnd` schedules the shared one-yield transition, which reclaims
text only after the submenu dies and then reopens the hub.

### Two proven, honest, pre-existing dormant-code/data limitations

Both Weather and Fog are visibly **dormant** in practice -- toggling them
produces no on-screen change on Chapter 2 -- for two different, fully
root-caused, pre-existing reasons that this slice does not (and, per its
WHERE constraints, cannot) fix:

- **Weather**: `DebugMenu_WeatherIdle`/`DebugMenu_WeatherDraw`
  (`src/bmdebug.c`) both dereference `Proc_Find(ProcScr_DebugMonitor)`, a
  proc the same file's `DebugMenu_WeatherSelected` (or, here, this slice's
  submenu open) starts via `Proc_Start(ProcScr_DebugMonitor, PROC_TREE_3)`.
  `ProcScr_DebugMonitor`'s own script (`src/bmdebug.c`) is a single
  `PROC_END` command; `Proc_Start()` runs the script synchronously before
  returning, and `PROC_END`'s handler (`ProcCmd_DELETE`, `src/proc.c`) ends
  the proc immediately -- so the proc is created *and* destroyed within the
  same `Proc_Start()` call, before `Proc_Find()` can ever see it non-`NULL`.
  This is a genuine, pre-existing defect in untouchable `bmdebug.c` (the
  identical `ProcScr_DebugMonitor` pattern is also used, and has the same
  effect, in `src/sio_menu.c` and `src/mapanim_debug.c`) -- not something
  this slice introduces or can fix without editing `bmdebug.c`.
- **Fog**: `DebugMenu_FogIdle` (`src/bmdebug.c`) recomputes vision range
  from `GetROMChapterStruct(gPlaySt.chapterIndex)->initialFogLevel` on
  non-skirmish maps. Chapter 2's `initialFogLevel` is `0` in
  `src/data/chapter_settings.h` (a handful of other chapters use `3`) -- so
  toggling fog on Chapter 2 legitimately settles back to its unchanged data
  value. This is pre-existing chapter data, not a code defect.

Both are documented here, proven via the playtest evidence below (probes
stay at their pre-toggle value across a Weather/Fog cycle, exactly as this
root-cause analysis predicts), and left exactly as-is -- fixing either is
out of this slice's WHERE/HOW MUCH ("Weather/Fog... dormant, non-persistent"
was itself given as the reason these two were chosen as safe to expose).

## Bounded chapter/skirmish selector (issue #123)

Issue #123 is a **framework capability**: projects, playtest automation, and
content authors can enter an authoritative chapter or supported world-map
encounter without restoring the prototype/retail debug menu. It uses the
existing hub, submenu transition, and GameControl seams. It does not add a
second registry, feature flag, chapter catalog, or startup callback.

### Action and controls

Built-in ID 4 is now `Chapter/Skirmish`. This intentionally evolves the old
single-purpose `Fast Boot: Ch4 Prep` row without changing any stable built-in
ID, the nine built-in slots, contributor IDs `10..65535`, the public
`struct DebugToolsAction` ABI, the nine-row page limit, or the 18-action total
capacity. `DebugTools_RegisterChapter4PrepAction()` remains a source-compatible
initializer alias for the ID-4 selector, and
`expansion-modern-debugtools-ch4prep-check` remains a compatibility build
target that runs the selector gate.

Selecting ID 4 uses the existing deferred hub-to-submenu handoff. The submenu
has one live row plus the all-zero terminator:

- Left/Right selects the previous/next target with wraparound.
- The row shows localized Chapter/Skirmish and route labels plus the numeric
  chapter identity.
- A revalidates and queues exactly one typed request, then closes the submenu
  and the complete debug session through deferred Text cleanup.
- B or forced teardown before A queues nothing and returns through the same
  bounded hub session.

The immutable two-entry `MenuItemDef` table (one row plus terminator) is
ROM-resident. The selector allocates only one 18-column menu `Text`; Left/Right
redraws reuse it and never call `InitText` again. Final cleanup rewinds the
captured owner font only after both MenuProcs have ended. No heap allocation is
used.

### Authoritative target enumeration

There is no hand-authored chapter list. `src/debugtools_selector.c` scans the
typed runtime sources already owned by the engine:

- `gWMNodeData[0..NODE_MAX)` supplies node, route, encounter, and
  route-specific chapter identities.
- `gChapterDataTable` and its generated debug-only
  `gChapterDataCount = ARRAY_COUNT(gChapterDataTable)` bound chapter lookup and
  require a non-empty internal name, map-event reference, chapter-title text
  reference, normal ally list, and beginning event.
- Nodes whose Eirika/Ephraim chapter identities match produce one Common
  target. A route split produces independent Eirika and Ephraim targets.
- A skirmish target additionally requires membership in the bounded
  `gWMMonsterSpawnLocations[0..min(gWMMonsterSpawnsSize, WM_MON_LOC_MAX))`
  table, `GMAP_ENCOUNTERS_MONSTERS`, and all three typed ally plus all three
  typed enemy encounter lists in the selected `ChapterEventGroup`.

The stock metadata yields 45 targets, but that count is not compiled into the
selector. A generated-data project changes the result by changing the owning
chapter/world-map/event metadata, not by editing debugtools. Invalid,
placeholder, out-of-range, non-spawn, or incomplete encounter rows are never
displayed.

Each emitted target has a stable typed identity:

```text
(DebugToolsLaunchTargetKind << 12) |
(chapterMode << 8) |
worldMapNode
```

The four-byte private state stores only that ID plus pending/handoff bits.
Consume re-resolves it against current metadata and constructs the typed
kind/mode/node/chapter request on the GameControl stack. A malformed ID is
`DEBUGTOOLS_LAUNCH_REQUEST_INVALID`; a well-formed identity
whose metadata is unavailable is `DEBUGTOOLS_LAUNCH_REQUEST_UNAVAILABLE`; and
any second arm while one request is pending is
`DEBUGTOOLS_LAUNCH_REQUEST_BUSY`. Host automation asserts all three explicit
results and that they leave the active menu/owner untouched.

### Lifecycle-safe GameControl handoff

The submenu callback never calls `EndBMapMain`, `Proc_Goto`, `Proc_EndEach`,
`StartBattleMap`, a restart helper, or a save API.

1. **Title:** A queues the typed request. The submenu `onEnd` schedules final
   debug-session cleanup. `Title_IDLE` reacts only after
   `DebugTools_IsHubActive()` is false, then takes the ordinary
   `GAME_ACTION_EVENT_RETURN`/`Proc_Break` path.
2. **Live map or prep:** the owning `PlayerPhase_MainIdle` or
   `PrepScreenProc_MapIdle` sees the pending request only after session
   cleanup and schedules one yielded handoff Proc. That independent Proc
   verifies BMap and GameControl still exist, ends BMap once, and routes the
   existing GameControl to `LGAMECTRL_POST_TITLE_IDLE`. It never destroys or
   recreates GameControl. A duplicate schedule is a no-op; a lost owner is
   bounded by a 60-frame cancellation timeout.
3. **GameControl:** `GameControl_PostIntro` consumes once before the ordinary
   save-menu branch and seeds the deterministic RNG. Ordinary selector
   confirmation initializes transient play state and enters
   `LGAMECTRL_EXEC_BM_EXT`, the engine's existing direct battle-map label.
   Holding L while confirming the default Common-route Chapter 4 target marks
   that request as compatibility-origin and preserves the established
   `NODE_BORGO_RIDGE` traversal through `LGAMECTRL_EXEC_BM`. Story targets mark
   only their transient selected node as valid/cleared. Skirmishes mark the
   authoritative spawn node valid/not cleared, select encounter variant 0, and
   set `PLAY_FLAG_EXTRA_MAP`.
   Encounter ally lists reposition an existing party, so GameControl first
   seeds that party from the selected chapter's authoritative normal ally
   list; it never maintains a separate unit fixture.

The existing bootstrap observer/write suppression covers the transition until
the first stable Player Phase. Chapter and skirmish libmGBA scenarios compare
the full 32 KiB SRAM hash before selection with prep/map interactive
checkpoints; every value is exactly
`fnv1a64-sram:1fb2612031f74d22`.

### Compatibility, limits, and conflicts

| Surface | Contract |
| --- | --- |
| Configuration | Existing `FE8_EXPANSION_DEBUGTOOLS_ENABLED`; no new Autoconf option, Make variable, or C gate |
| Modern debug | ID-4 selector/action/request/handoff and generated count are linked |
| Modern release | Selector bodies/call sites, private selector state, and `gChapterDataCount` are omitted; exact input leaves the unchanged release probe zero |
| Archival | `FE8_ARCHIVAL_BUILD` compiles the selector empty; no source/layout/identity requirement changes |
| Save | No field, epoch, migration, slot write, completion mutation, or tactician mutation |
| Generated data | Reads the generated chapter table/count and typed world-map/event metadata; no generated output is committed |
| Localization | Stable expansion message IDs for the action, kind, route, and unavailable labels in all seven authored locales |
| ROM/RAM | 4 bytes fixed debug EWRAM for selected target/pending/handoff state, leaving 1,700 bytes in default debug, 112 in en+ja/en+zh-Hans, 76 in en+ja+zh-Hans, 40 in all-locale+pseudo, and 8 in the expanded-item runtime profile; immutable menu definitions live in ROM; one transient Proc; no heap; release EWRAM layout unchanged |
| Dependencies | Issue #11 registry, deferred menu ownership, title/map/prep hotkeys, GameControl PostIntro, bootstrap suppression, generated chapter metadata, typed world-map/event metadata |
| Dependents | None |
| Conflicts | Prototype/debug patches, direct engine-owned menu edits, save-writing launchers, duplicate registries/catalogs, or direct restart/BMap teardown from a menu callback |
| Explicit none | Starter gameplay features, custom spells, locale selection, autoplay strategy, and save migration |

Only world-map-node-backed chapter identities are selectable. Placeholder
chapter-table rows, arbitrary memory addresses, unregistered project maps,
non-spawn encounters, encounter variants 1/2, and World Map progression
cinematics are intentionally unsupported. Direct entry starts the selected
battle/prep lifecycle; it does not pretend to complete preceding story nodes.

The canonical procedure is
[`TC-DEBUGTOOLS-PROTOTYPE-001`](test-cases/debugtools.md#tc-debugtools-prototype-001-bounded-chapter-and-skirmish-selector).
Host automation covers metadata bounds, stable IDs, invalid/unavailable,
duplicate, cancel, forced teardown, reentrancy, exact-once consume, and release
symbol omission. `make expansion-modern-debugtools-selector-check
MODERN_CONFIG=debug MODERN_ABI=aapcs` covers the title Chapter 4 and live-map
Chapter 4 skirmish routes; the same target with `MODERN_CONFIG=release` covers
the disabled path. There is no manual-only criterion.

Rollback is a single-commit revert: ID 4 returns to the standalone Chapter 4
action and the generated count, typed request, selector UI, scenarios, and
catalog IDs disappear. No save/content conversion or ID migration is needed.

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
2. **`Title_IDLE` detects the request without waiting an extra cleanup
   frame** (`src/titlescreen.c`): the action can only arm the request
   immediately before returning `MENU_ACT_END`, so the hub `MenuProc` has
   ended by the next `Title_IDLE` turn. The pending check intentionally
   precedes the broader `DebugTools_IsHubActive()` session guard because
   deferred text cleanup retains allocator/reentrancy ownership for one
   additional yield. When pending, `Title_IDLE` reacts with the
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
`chapter2-interactive-stable`).

The **release** scenario (`debugtools-hub-modern-release.json`) replays the
exact same frame-for-frame input (all 259 frame entries, identical to the
debug scenario) against a release build, where the whole debugtools
subsystem is compiled out, so `gDebugToolsProbe` stays all-zero at all 7
checkpoints (the compiled-out proof). It carries **no** framebuffer oracle.
An earlier revision asserted the frozen-screen hash
`fnv1a64-rgb24:d11078d0ec60076d` at its last two checkpoints, but that was
vacuous: the world-map UB (a `Proc_FindNext` NULL-deref the `-O2` release
build mis-optimised into an infinite loop, fixed in `src/worldmap_rm.c` /
`src/worldmap_automu.c`; see `reports/issue6_foundation_evidence.md`) had
frozen the screen, so a frozen-screen hash matched whether or not
debugtools linked -- and once the fix unfroze the screen the hash became a
false negative. Because the debug hotkeys are inert in release, the same
`START`/`A` taps simply drive the vanilla title-to-new-game path into the
game's **live opening world-map sequence**, and the checkpoints now assert
relocation-independent semantic `gPlaySt`/cursor scalars for it:
`chapterIndex` advances from `0x00` (title, frames 300-950) to a real
non-title `0x10` with `faction == 0x40` (NPC phase) and the map cursor
initialised to `0x0e` by frames 14000-14900. That title->world-map
progression is impossible on the pre-fix build (which locked with
`chapterIndex` stuck at `0x00`), so the scenario genuinely **fails on the
frozen build and passes on the fixed one**, instead of passing vacuously on
both. This world-map sequence is the plain vanilla path -- **not** a debug
hub and **not** a real chapter map (`gBmSt`'s tactical main loop is not
active) -- so the release checkpoints were renamed
(`title-idle-preboot-inert`, `title-hotkey-pulse{1,2}-inert`,
`title-a-press-inert`, `newgame-intro-pre-worldmap-inert`,
`worldmap-intro-live-progress`, `worldmap-intro-live-progress-sustained`)
from the old, misleading debug-shaped names.

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

### Map/prep hub playtest evidence (slice 2)

`tools/gba-playtest/scenarios/debugtools-map-hub-modern-debug.json` (13
checkpoints, debug-only, live) reuses the same intro/Chapter-2-launch prefix
as the title hub scenario, then once Chapter 2's map is interactive: pulses
the *map* hotkey mask twice (reentrancy-guard proof, same pattern as the
title hub), selects the Weather action, cycles it through
`DebugMenu_WeatherEffect`/`Idle`/`Draw` and asserts the underlying weather
state actually changes, returns (`B`) to the action list, selects Fog,
cycles it and asserts the vision/fog state changes, returns again, closes
the hub, and finally proves the battle map is still fully interactive
afterward (cursor move probes identical in shape to the title-hub scenario's
`post-interactive-cursor-response`). This is the concrete evidence that
Weather/Fog are reachable and functional through the registry-backed
adapters in `src/debugtools_actions.c`, and that opening/closing the map hub
never desyncs ordinary map-phase input handling.

`debugtools-map-hub-modern-release.json` and
`debugtools-prep-hub-modern-release.json` are release-mirror scenarios: each
replays `debugtools-hub-modern-release.json`'s own exact input script
verbatim, then appends a hotkey tail (map mask + `B`/direction taps, or prep
mask + `B`/direction taps respectively). In a release build the map/prep
hotkey masks are compiled out, so `gDebugToolsProbe` -- including the
prep-specific `pendingCh4PrepLaunchRequest`/`ch4PrepLauncherArmed`/
`ch4PrepLaunchRequestConsumedCount`/`prepScreenObservedCount` fields --
stays all-zero across the pulses. These scenarios carry **no** framebuffer
oracle. An earlier revision asserted the frozen-screen hash
`fnv1a64-rgb24:d11078d0ec60076d` at every checkpoint, reasoning the tail
input "changes nothing further"; but that hash was frozen only because the
world-map UB had locked the screen, so it proved nothing about debugtools
being compiled out (and became a false negative once the fix unfroze it).
The shared prefix has already driven the vanilla path into the live opening
world-map sequence (`gPlaySt.chapterIndex == 0x10`, `faction == 0x40` NPC,
map cursor `== 0x0e`); the appended hotkey tail leaves each of those
semantic progress scalars **unchanged** across all four checkpoints, which
is the real proof that the map/prep hotkey tail is inert on top of a
genuinely running, unfrozen world map (the pre-fix build locked here with
`chapterIndex` stuck at `0x00`). **Honest scope:** this release input
reaches the opening world-map cinematic, never a debug hub and never a real
prep screen (`gPlaySt.chapterStateBits` never sets `PLAY_FLAG_PREPSCREEN`);
the checkpoint names reflect that.

A live prep-screen scenario reaching a real, engine-active prep screen is
now **achieved** in this closure via Chapter 4 (see "Fast Boot: Chapter 4
(Prep)" below). `hasPrepScreen` is `FALSE` for all 79 chapters in
`src/data/chapter_settings.h` (a vestigial FE7-only field), but the `PREP`
event-script command (`src/eventscr.c`'s `EventScr_CommonPrep`) is **not**
dead code -- it is genuinely `CALL`ed from many linked, compiled-in chapter
scripts (e.g. `ch4`/`ch5`/`ch6`/`ch7`/`tower`/`ruin`-eventscript.h and
others), so a real prep screen is reachable through ordinary gameplay. This
slice's own launcher was Chapter 2 only, and Chapter 2's own event script
(`src/events/ch2-eventscript.h`) never calls `EventScr_CommonPrep`; the
closure's second launcher targets Chapter 4, whose
`EventScr_Ch4_BeginningScene` does call it, so the live prep screen is
reached deterministically without a chapter-data edit or skirmish selector.
`PrepScreenProc_MapIdle`'s hotkey entry point is added and unit-tested
(call-site ordering, mask collision checks, disabled/release stubs), its
release-mirror scenario proves the hotkey is inert on a release build, and
the live, in-ROM debug playtest proof against a real prep screen is now
provided by `debugtools-ch4-prep-positive-modern-debug.json`.

## Historical Chapter 4 launcher compatibility

Issue #11 introduced a second, independent Chapter 4 request pair because
Chapter 2 never reaches `EventScr_CommonPrep`. Those request functions and
their probe fields remain available to specialized runtime probes, but issue
#123 supersedes the visible ID-4 action with the selector above.
`DebugTools_RegisterChapter4PrepAction()` now initializes that selector, and
the old `expansion-modern-debugtools-ch4prep-check` target aliases
`expansion-modern-debugtools-selector-check`.

Weather and Fog therefore remain rows 1 and 2, while row 3 is now
Chapter/Skirmish. The full first page remains nine bounded actions:
Chapter 2 (0), Weather (1), Fog (2), Chapter/Skirmish (3), Unit Inspect (4),
Convoy Inspect (5), Flag/Chapter (6), RNG Inspect (7), Save State (8), then
Back (9). Contributor ordering and capacity are unchanged.

The selector chapter scenario now owns the Chapter 4 request/commit/no-save
evidence and reaches live prep directly. The existing
`debugtools-ch4-prep-positive-modern-debug.json` remains an independent live
prep hotkey/lifecycle proof and continues to pass against the evolved row.

## Bounded music preview (issue #126)

`src/debugtools_music.c` registers one action through the same private
built-in path as every other shipped row. Its submenu has exactly two live
rows: the selected song and localized Back. `LEFT`/`RIGHT` wraps through
`gSoundRoomTable`; `A` previews; `B`/Back returns through the existing
deferred submenu lifecycle. It never calls the dormant `DebugMenu_BgmDraw`
or `DebugMenu_BgmIdle`, accepts no raw numeric ID, allocates no heap memory,
and does not add a gameplay router.

`IsSoundRoomCatalogEntryValid()` is the authoritative bound shared with the
sound room. Valid secret/locked rows are intentionally included because this
is an author-facing catalog inspector, while malformed rows are skipped and
record `DEBUGTOOLS_LOG_MUSIC_REJECTED`. Neither enumeration nor preview reads,
changes, or writes sound-room unlock bits.

The preview acquires
`EXPANSION_BGM_PREVIEW_OWNER_DEBUGTOOLS_MUSIC` and captures the complete typed
BGM context plus playing/stopped, song, override, fade, and channel state.
Rapid selections retain that one snapshot. Back/cancel restores it and clears
the owner; cancel before preview only releases ownership; an initially silent
context returns to exact silence. Final debug-session cleanup, title reset,
and chapter transitions call `DebugTools_ForceSessionCleanup`, which restores
preview audio once, cancels a queued transition, and releases the session
guard. The appended `gDebugToolsMusicProbe` and bounded diagnostic events
provide semantic correctness evidence; audio recognizability is supplementary.


### Live prep-screen arrival: `DebugTools_PrepHotkeyCheck`'s observation (achieved)

Independently of the launcher above, `DebugTools_PrepHotkeyCheck()`
(`src/debugtools_registry.c`) now observes `gPlaySt.chapterStateBits &
PLAY_FLAG_PREPSCREEN` (`include/types.h`) at the exact moment the prep
hotkey fires, incrementing `gDebugToolsProbe.prepScreenObservedCount` only
when it is genuinely set. `PLAY_FLAG_PREPSCREEN` is set by the real engine
prep-screen lifecycle (`InitPrepScreenUnitsAndCamera`,
`src/prep_sallycursor.c`) for as long as a genuine `PrepScreenProc`
(`gProcScr_SALLYCURSOR`) is active -- so this counter is concrete,
host/runtime-provable evidence *of the hotkey call site itself*: it can
never read nonzero unless the hub was opened while a real, live prep
screen was running. The committed live scenario
`debugtools-ch4-prep-positive-modern-debug.json` now drives a real prep
screen far enough to exercise this path: it observes
`gDebugToolsProbe.prepScreenObservedCount` (`0x02031854`) transition
`0 -> 1` on the SELECT+B hotkey while `gPlaySt.chapterStateBits`
(`0x020210b8`) holds `PLAY_FLAG_PREPSCREEN` (`0x10`), the hub opens
(`hubOpenCount` `0x02031818` `1 -> 2`, `sHubActive` `0x02031614`
`0 -> 1`), a 2nd SELECT+B is idempotent (`hubOpenCount` stays `2`), and the
hub owns that input so its prep observation count is exactly `2`, then closes
(`sHubActive -> 0`) with prep still live -- a safe return to prep. The field
is always-linked and mirrors every other probe's
zero-by-default contract in a release build.

## Diagnostics: structured probe/log ring + non-fatal assert record (issue #11 closure)

Issue #11 closure requirement 6: "emulator logging/assertion/
crash-diagnostic/memory-inspection foundations". `src/debugtools_diag.c` is
explicitly **not** an `mgba_printf`/AGB-print-protocol implementation,
**not** an interactive debugger, and **not** an arbitrary memory editor --
seethe explicit non-goals in `reports/debugtools_issue11_closure.md`. What
it provides instead:

- **A bounded log ring** (`DEBUGTOOLS_LOG_RING_SIZE` = 8 entries of
  `struct DebugToolsLogEntry { code; a; b; }`) -- `DebugTools_LogEvent(code,
  a, b)` always succeeds; the oldest entry is silently overwritten once
  full (bounded by construction, never a growing allocation).
  `DebugTools_GetLogCount()`/`DebugTools_GetLogEntry(index)` give bounded,
  read-only, index-0-is-most-recent introspection (`NULL`/0 outside
  range). `gDebugToolsProbe.logEventCount` mirrors the unbounded running
  total (proving eviction doesn't stop counting) and
  `gDebugToolsProbe.lastLogCode` mirrors the most recent code.
- **A non-fatal assert record** -- `DEBUGTOOLS_ASSERT(cond, code)` evaluates
  `cond` and, on failure, calls `DebugTools_RecordAssertFailure(code)`:
  increments `gDebugToolsProbe.assertFailureCount`, sets
  `gDebugToolsProbe.lastAssertCode`, and itself appends a
  `DEBUGTOOLS_LOG_ASSERT_FAILURE` ring entry carrying the failing code.
  This **never aborts, crashes, or halts the game** -- every call site in
  `src/debugtools_tools.c` uses the "assert then bail out" idiom (skip the
  mutation, return normally), matching every tool's own safe-return-to-game
  contract. This is the bounded "crash-diagnostic" foundation: a record of
  what would have gone wrong, not a debugger break or a fatal stop.
- **Bounded, read-only, whitelisted introspection** --
  `DebugTools_GetLogEntry`/`DebugTools_GetLogCount`/
  `DebugTools_GetAssertFailureCount`/`DebugTools_GetLastAssertCode` are the
  only read surface; there is no address parameter anywhere in this API,
  so it is structurally impossible to use it as an arbitrary memory reader.

No dedicated hub menu row is spent on a "Diagnostics" viewer: the first page
remains the original nine built-ins listed in "Hub menu ordering" above, preserving
their established row identities and existing framebuffer/navigation
expectations. The ring/
assert state is instead exposed purely through `gDebugToolsProbe` fields and
the plain introspection functions above, which is sufficient for both host
tests (`DebugToolsDiagHostTests`, `tools/gba-playtest/tests/test_debugtools_registry.py`)
and future runtime scenarios to assert against.

Every bounded tool calls `DebugTools_LogEvent` for inspect and applicable
confirmation steps. Flag uses `DEBUGTOOLS_ASSERT` for its fixed index; the
unit editor uses typed rejection outcomes plus non-fatal assert records for
commit-time target/conflict/value failures.

Release-inert: every function in `src/debugtools_diag.c` compiles to a
trivial disabled stub (returning 0/`NULL`, recording nothing) when
`FE8_EXPANSION_DEBUGTOOLS_ENABLED` is 0 -- confirmed by `nm` (the disabled
translation unit defines exactly the six public entry points, no
`sLogRing`/`sLogRingTotalWrites` storage at all).

## Five bounded validated tools (issue #11 closure)

Issue #11 closure requirement 5. Each is a single registry action
(`src/debugtools_tools.c`) registered through the internal built-in path
(ids 5-9) -- no direct edits to `gDebugToolsHubMenuDef`/
`sHubMenuItemDefs`. Each samples/displays
read-only state immediately on selection (logged via
`DEBUGTOOLS_LOG_*_INSPECT`). Convoy, Flag, and RNG use their original bounded
Confirm/Back menus. Unit uses one fixed root plus fixed HP/stat/AI menus;
left/right changes preview state only and A is the separate confirmation.
No tool performs a raw/arbitrary address write or accepts an unvalidated
index. Fixed operations use documented constants; Unit resolves and
revalidates the cursor target through typed engine helpers. None touches SRAM
or a save-block struct directly (RNG/flags/units/convoy are ordinary EWRAM
runtime state; Save State is read-only).

1. **Unit Inspect** (id 5, extended by issue #125) -- resolves only the unit
   at the real live-map cursor: bounds-check `gBmSt.playerCursor`, read the
   slot from `gBmMapUnit`, then resolve it through `GetUnit`. The action
   rejects an empty tile, purple/link-arena slot, missing/noncanonical
   character or class pointer, mismatched slot/coordinates/map cell,
   hidden/rescued/not-deployed/dead/zero-HP unit, title/prep context, and an
   active standard event, battle event, or battle daemon. A valid inspect is
   read-only and snapshots slot, character/class numbers, position, full
   `Unit::state`, current/max HP, status, and AI A/B.

   The fixed root menu keeps **Confirm Heal to Full**, adds bounded HP, stat,
   and AI submenus, exposes character/class/state as disabled read-only rows,
   and enables **Confirm Clear Status** only for the named temporary statuses
   Poison, Sleep, Silence, Berserk, Attack, Defense, Crit, Avoid, Sick, and
   Petrify. `UNIT_STATUS_RECOVER`, `UNIT_STATUS_12`, and `UNIT_STATUS_13`
   remain unavailable: the tool never guesses prototype Recovery/Condition
   semantics.

   Each editable row uses left/right only to change an EWRAM preview. A
   distinct A confirmation revalidates the live-map context, complete target
   identity, original field value, current class cap, and enum range before
   the first write. Current HP is `1..GetUnitMaxHp(unit)` through
   `SetUnitHp`; raw max HP/power/skill/speed/defense/resistance/luck use the
   current class/engine limits and then `UnitCheckStatCaps`; AI A/B are
   restricted to `AI_A_00..AI_A_INVALID-1` and
   `AI_B_00..AI_B_INVALID-1` and go through `ChangeUnitAi`; status clearing
   goes through `SetUnitStatus(..., UNIT_STATUS_NONE)`. Lowering raw max HP
   below current HP is rejected rather than silently editing two fields.
   Heal changes current HP only--it no longer hides a status clear.

   Successful mutations call `RefreshEntityBmMaps`, `RenderBmMap`, and
   `RefreshUnitSprites`. Appended probe fields and closed structured log
   codes record operation, field, exact old/new values, and outcome
   (previewed, applied, no-change, cancelled, forced cleanup, or a typed
   rejection) without moving issue #11's existing probe offsets. Cancel,
   target movement/replacement/value drift, a newly active event/battle,
   out-of-range preview, unsupported status, and forced teardown are all
   no-write paths. Forced teardown schedules final session cleanup rather than
   reopening the hub. The implementation allocates no heap memory, never
   writes SRAM, never edits class/items/inventory/supports, and never accepts
   an address, offset, raw structure, arbitrary class, or arbitrary item.

   The reviewed modern-debug budget delta against issue #125's exact base is
   +8,256 linked floating ROM bytes and +672 EWRAM bytes (1,032 EWRAM bytes
   still free; IWRAM unchanged). The mutually exclusive HP/stat/AI submenus
   share one nine-slot `MenuItemDef` array. Modern release retains its prior
   budget exactly because the editor code, menus, state, and telemetry probe
   are omitted.
2. **Convoy Inspect** (id 6) -- inspect samples `GetConvoyItemCount()` into
   `gDebugToolsProbe.convoyLastItemCount`. Confirm calls
   `AddItemToConvoy(ITEM_VULNERARY)` (a fixed, safe consumable constant);
   `AddItemToConvoy` (`src/bmcontainer.c`) already bounds-checks capacity
   internally and returns `-1` without mutating anything when full -- a
   full convoy is therefore a safe, logged
   (`DEBUGTOOLS_LOG_CONVOY_ADD_SKIPPED_FULL`) no-op, never an overflow.
   A successful add increments `gDebugToolsProbe.convoyAddTransactionCount`.
3. **Flag/Chapter** (id 7) -- inspect samples `gPlaySt.chapterIndex` into
   `gDebugToolsProbe.chapterIndexSample` (read-only) and
   `CheckFlag(DEBUGTOOLS_DEBUG_EVENT_FLAG_ID)` into
   `gDebugToolsProbe.debugFlagLastValue`. `DEBUGTOOLS_DEBUG_EVENT_FLAG_ID`
   (39) is a single fixed, documented, chapter-scoped (never permanent)
   event-flag index, deliberately within `include/constants/event-flags.h`'s
   documented "free"/scratch range (indices 7-40; 0-6 are real gameplay
   flags) and never a caller-supplied index. Confirm re-validates
   `DEBUGTOOLS_DEBUG_EVENT_FLAG_ID < GetChapterFlagBitsSize() * 8` via
   `DEBUGTOOLS_ASSERT(..., DEBUGTOOLS_ASSERT_FLAG_ID_OUT_OF_RANGE)` --
   defense in depth, since `SetFlag`/`ClearFlag` (`src/eventinfo.c`) do not
   themselves bounds-check a chapter-scoped index -- then toggles it
   (`ClearFlag` if currently set, else `SetFlag`), incrementing
   `gDebugToolsProbe.debugFlagToggleCount`.
4. **RNG Inspect** (id 8) -- inspect calls `StoreRNState(seeds)` (`src/rng.c`)
   and samples `seeds[0]` into `gDebugToolsProbe.rngInspectSeedSample0`
   (the real, current LCG/Fibonacci generator state -- never reimplemented).
   Confirm calls `SetLCGRNValue(DEBUGTOOLS_TOOLS_RNG_SEED)` +
   `InitRN(AdvanceGetLCGRNValue())` -- the exact same fixed-reseed idiom
   `GameControl_PostIntro`'s own Chapter 2/4 launchers already use for
   their deterministic boot (`DEBUGTOOLS_TOOLS_RNG_SEED` is a distinct
   constant from `DEBUGTOOLS_FASTBOOT_RNG_SEED`, so the two are never
   confused in logs/tests) -- incrementing
   `gDebugToolsProbe.rngReseedTransactionCount`.
5. **Save State** (id 9) -- **read-only**, no Confirm item at all (nothing
   to confirm): calls `ClassifySramSaveCompat()` (`src/bmsave-lib.c`),
   which only inspects the global save header/expansion metadata record and
   never mutates SRAM or any save-block struct, sampling the result into
   `gDebugToolsProbe.saveCompatLastState` and incrementing
   `gDebugToolsProbe.saveCompatInspectCount`. This tool never calls
   `BuildCurrentExpansionSaveMeta` against a live SRAM target,
   `InitGlobalSaveInfodata`, or any writer -- the safest of the five by
   construction. Its only **Back** row and B handler both use
   `DebugTools_CancelMenu`, never `MenuCancelSelect`, so their deferred
   return-to-hub path cannot clear BG0/BG1. The tools host driver fills its
   synthetic BG map before Back, verifies the no-clear result flags, and
   verifies the same map survives the deferred hub return; the runtime
   save-back checkpoint verifies the read-only return-to-hub route remains
   interactive and reports a preserved nonzero BG1 frame tile before the
   deferred hub redraw.

### Host-executed evidence

`DebugToolsExtendedToolsHostTests` (`tools/gba-playtest/tests/test_debugtools_registry.py`)
compiles+links+executes the real, unmodified `src/debugtools_tools.c`
together with the real `src/debugtools_registry.c` and `src/debugtools_diag.c`
against `tools/gba-playtest/tests/c/debugtools_tools_driver.c` and
`debugtools_tools_host_stubs.c` (small, test-controllable fakes for the
engine subsystems each tool calls into -- cursor/map/context ownership,
`GetConvoyItemCount`/`AddItemToConvoy`, `SetFlag`/`ClearFlag`/`CheckFlag`,
`StoreRNState`/`SetLCGRNValue`/`InitRN`, `ClassifySramSaveCompat` -- mirroring
the relevant helper semantics and recording exact call effects). The unit
driver executes every HP/stat/AI/status callback and covers empty, dead,
purple, noncanonical, stale, value-drift, range, Recovery/Condition,
event/battle conflict, cancel, no-change, and forced-teardown controls.
`test_unit_editor_executes_authoritative_engine_helpers` separately links
the real `src/bmunit.c` and `src/eventscr3.c` with section garbage
collection and host-executes `SetUnitHp`, `UnitCheckStatCaps`,
`SetUnitStatus[Ext]`, and `ChangeUnitAi` themselves--not fixture copies.
The same suite proves registration (ids 5-9, deterministic and idempotent),
the remaining tools' semantics, full-Convoy rejection, and Save State's
read-only contract. A disabled-path test compiles the
disabled path and proves both behavior and physical symbol omission -- the
disabled object defines exactly the one no-op
`DebugTools_RegisterExtendedToolActions()` entry point and links clean with
**no** engine/menu/hardware stub of any kind (an undefined reference there
would mean the disabled path grew a real runtime dependency). A live
runtime scenario driving all five tools through the map hub (mirroring
`debugtools-map-hub-modern-debug.json`'s own live Weather/Fog proof) is now
included: `debugtools-tools-modern-debug.json` (gate
`expansion-modern-debugtools-tools-check`, host test
`tools/gba-playtest/tests/test_tools_scenario.py`) now also proves a real
cursor target, HP `17 -> 16 -> 17`, empty-tile rejection, matching whole-SRAM
hashes, release-zero behavior, and post-cleanup map interactivity. The
canonical human procedure and automation mapping are
`TC-DEBUGTOOLS-PROTOTYPE-003`.


## Host tests

`tools/gba-playtest/tests/test_debugtools_registry.py` (run via
`python3 -m unittest discover -s tools/gba-playtest/tests -v`) exercises the
real, unmodified sources with a native host compiler rather than
re-implementing or pattern-matching their logic. Issue #11 closure added
`DebugToolsDiagHostTests` (log ring/assert record), `DebugToolsExtendedToolsHostTests`
(the five bounded tools), `test_registry_id_and_label_validation` (the two
new `DebugToolsResult` codes), and `DebugToolsCh4PrepLaunchScenarioSchemaTests`
(the Ch4-Prep-launch scenarios) alongside the slice 1/2 classes below --
see "Fast Boot: Chapter 4 (Prep)"/"Diagnostics"/"Five bounded validated
tools" above for what each proves.

- `scripts/localization/tests/test_debugtools_localization.py` loads the
  generated `en`/`ja`/`zh-Hans`/`qps-ploc` catalogs and committed system-font
  metrics, then checks every hub/confirmation/Back label against the actual
  `(MenuDef.rect.w - 1) * 8` Text allocation. It also checks composed status
  lines against their real BG geometry and the localized Weather/Fog
  label/value columns. The shared debug menu width is 19 tiles (18 text
  tiles); the CJK status allocation is 24 tiles, both still within the
  30-tile GBA screen.
- **`DebugToolsRegistryHostTests`** compiles+links+executes the real
  `src/debugtools_registry.c` (enabled path) against a small driver
  (`tools/gba-playtest/tests/c/debugtools_registry_driver.c`) through the
  exact public API (`include/expansion_debugtools.h`), proving: contributor
  capacity is exactly 9 beside separate built-in storage, deterministic
  append order, `NULL`-out-of-range reads, duplicate id/label rejection,
  invalid-action (`NULL` action/label/callback) rejection, and capacity-full
  rejection on the 10th contributor attempt -- all without silently dropping
  a registration or changing the count on a rejected call.
  Its lifecycle case also links the real, unmodified `src/uimenu.c`
  `ProcessMenuSelectInput()`/`Menu_OnIdle()`/`EndMenu()` path and executes
  under `qps-ploc`: all ten built-ins and all nine contributors retain
  capacity/order/callback identity; QPS adapts only built-in rows while
  contributor labels remain on the raw renderer; R dispatch completes with
  the old menu alive and only the yielded Proc ends it; and 64
  hub/page/font-switching-submenu cycles restore the exact row-owner font
  baseline while preserving every allocation made from the contributor's
  separate font.
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
  - `test_title_idle_consumes_pending_request_before_session_guard` and
    `test_title_idle_pending_branch_never_synthesizes_input` grep
    `Title_IDLE`'s function body to confirm the pending request is consumed
    before the broader session guard can defer it by one allocator-cleanup
    yield, and that the pending branch reacts with the same
    `SetNextGameActionId`/`Proc_Break` pair the ordinary `A`/`START` branch
    uses, never a synthesized keypress.
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

### Slice 2 host tests: map/prep masks and Weather/Fog actions

- **`DebugToolsMapPrepHotkeyCollisionHostTests`** mirrors
  `DebugToolsHotkeyCollisionHostTests` for the new
  `FE8_EXPANSION_DEBUGTOOLS_MAP_HOTKEY_MASK`/`_PREP_HOTKEY_MASK` guards:
  proves the zero-mask and both soft-reset-combo `#error`s fire for each
  mask, proves each mask's own bare `R`/`L`/`START` collision guard fires
  (the map/prep phases' own vanilla controls), proves both masks reject a
  collision against the title mask, proves the prep mask additionally
  rejects a collision against the map mask, and proves a legitimate custom
  override of either still compiles cleanly.
- **`DebugToolsMapPrepOneEntryPathTests`** mirrors
  `DebugToolsOneEntryPathTests` for the two new call sites: greps for
  exactly one call site each of `DebugTools_MapHotkeyCheck()`
  (`src/playerphase.c`) and `DebugTools_PrepHotkeyCheck()`
  (`src/prep_sallycursor.c`), exactly one enabled + one disabled definition
  of each in `src/debugtools_registry.c`, and -- the WHERE constraint's
  "first statement, immediately returning while hub active" requirement --
  parses `PlayerPhase_MainIdle`'s and `PrepScreenProc_MapIdle`'s function
  bodies to confirm the hotkey check is textually the first statement and
  `DebugTools_IsHubActive()` appears in the guard immediately after it.
  Also re-asserts `bmdebug.c`/`uidebug.c`/`menu_def.c` stay untouched.
- **`DebugToolsWeatherFogActionsHostTests`** compiles+links+executes the
  real, unmodified `src/debugtools_actions.c` (enabled path) together with
  the real `src/debugtools_registry.c` against
  `tools/gba-playtest/tests/c/debugtools_actions_driver.c`, proving:
  idempotent registration of both actions (ids 2/3, "Weather"/"Fog"), the
  private built-in registry remains bounded alongside a simulated
  Chapter-2-launcher-sized filler set, exact
  `MenuDef`/`MenuItemDef` sentinel and `onDraw`/`onIdle`/`onSelected`
  callback wiring for both one-item submenus (reusing the real dormant
  `DebugMenu_Weather*`/`DebugMenu_Fog*` function pointers, never a
  hand-rolled copy), `ProcScr_DebugMonitor` lifecycle ownership (Weather
  starts it only if not already alive and ends it only if this module
  started it; Fog never touches it at all), and Back/`B` returning to the
  hub's action list. A second test compiles the disabled path and proves
  every adapter internal (both static action/menu-item tables, both
  `MenuDef`s, both `*Selected`/`BuildMenuItems`/`OnEnd` functions, the
  monitor-ownership flag) is physically omitted -- the disabled object
  defines exactly the one no-op `DebugTools_RegisterWeatherFogActions()`
  entry point, and links clean with **no** menu/proc/hardware stub at all
  (an undefined reference there would mean the disabled path grew a real
  runtime dependency). A third, structural test greps the real source
  (comments stripped) to confirm it never mentions
  `SaveGame`/`Sram`/`WriteSaveBlock`/`Proc_Break`/`Proc_End`/`Proc_Delete`,
  and that its only `Proc_EndEach` target is `ProcScr_DebugMonitor`. A
  fourth confirms `src/debugtools_actions.o`/`src/debugtools_actions.c`
  are wired into both `ldscript.txt` (legacy) and `modern.mk` (modern).
- **`DebugToolsMapPrepScenarioSchemaTests`** mirrors
  `DebugToolsScenarioSchemaTests` for the three new scenario files:
  validates all three against `gba_playtest`'s schema parser; confirms
  `debugtools-map-hub-modern-debug.json`'s two hotkey pulses reuse the
  same reentrancy-guard shape as the title hub (`hubOpenCount` unchanged
  across the repeated pulse) and that its final checkpoints prove the map
  remains cursor-interactive after the hub closes; confirms both
  `-modern-release.json` mirror scenarios reuse
  `debugtools-hub-modern-release.json`'s own frame script verbatim as a
  prefix (not a hand-authored approximation) and carry no framebuffer
  oracle -- instead asserting all-zero `gDebugToolsProbe` fields plus the
  live world-map semantic progress scalars (`chapterIndex`/`faction`/
  cursor) held constant across the appended hotkey tail (a dedicated
  standing guard,
  `test_release_negatives_forbid_any_framebuffer_and_require_semantic_probes`,
  also rejects any reintroduced framebuffer or pointer oracle); and
  confirms the map/prep masks used in the input scripts match
  the header's actual configured default masks (so the scenario can never
  silently drift from the real compiled-in hotkey).

Test-only fixture sources under `tools/gba-playtest/tests/c/` (stub
implementations of the handful of hardware/menu-engine symbols the
registration logic references but a registration-focused host test never
needs to execute) are never referenced by `modern.mk`/`Makefile` and are not
part of the shipped feature.

## Safety boundaries / extension rules

- Contributors add debug actions **exclusively** through
  `DebugTools_RegisterAction()` using IDs 11-65535; IDs 1-10 are reserved
  built-ins and return `DEBUGTOOLS_ERR_ID_RESERVED`. Never edit
  `gDebugToolsHubMenuDef` or
  `sHubMenuItemDefs` directly, and never add a second title-screen (or any
  other) hotkey call site -- this slice's one entry path is a hard
  constraint, not a starting convention.
- `src/bmdebug.c`, `src/uidebug.c`, and `src/menu_def.c` remain untouched and
  unreachable. The supported unit/convoy/flag/RNG tools are typed, bounded,
  debug-only actions; there is no save/gold/raw-memory editor.
- No gameplay proc is ever torn down/recreated by this feature (no
  `Proc_EndEach`/`Proc_Start` on `gProcScr_GameControl`, no `gProc_BMapMain`
  redirect). The only added proc is the bounded one-yield menu-transition
  helper described above. The hub only arms/consumes a pending-request flag, and both
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

## Remaining #11 scope (issue #11 closure)

Issue #11's frozen closure checklist is addressed end to end -- see
`reports/debugtools_issue11_closure.md` for the full item-by-item mapping
to code and test/command evidence. The previously-open live prep-screen
arrival is now **achieved** (first bullet). What otherwise remains
explicitly, honestly open is narrow:

- **Live prep-screen arrival -- ACHIEVED.** Both halves are proven live:
  the selector's direct Chapter 4 typed request/commit and prep arrival
  (`debugtools-selector-chapter-modern-debug.json`: target `0x1104`,
  exact-once consume, `gPlaySt.chapterIndex == CHAPTER_L_4`, prep bit set,
  unchanged SRAM), and the SELECT+B prep hotkey
  (`debugtools-ch4-prep-positive-modern-debug.json`, host test
  `tools/gba-playtest/tests/test_prep_positive_scenario.py`). The positive
  scenario rests `gProcScr_SALLYCURSOR` in `PrepScreenProc_MapIdle` and fires
  SELECT+B there: `DebugTools_PrepHotkeyCheck()`'s
  `PLAY_FLAG_PREPSCREEN` observation
  (`gDebugToolsProbe.prepScreenObservedCount`, `0x02031854`) is observed
  `0 -> 1` at runtime while `gPlaySt.chapterStateBits` (`0x020210b8`) holds
  `PLAY_FLAG_PREPSCREEN` (`0x10`), with an idempotent second SELECT+B and a
  safe return to the still-live prep. Gate: the DEBUG branch of
  `expansion-modern-debugtools-prep-check` (RELEASE verifies the
  compiled-out mirror). Debug-only because the launcher + hotkey are
  compiled out of a release build.
- **A live runtime scenario driving all five bounded tools through the map
  hub -- ACHIEVED.** `debugtools-tools-modern-debug.json` (gate
  `expansion-modern-debugtools-tools-check`, host test
  `tools/gba-playtest/tests/test_tools_scenario.py`) reuses the proven Fast
  Boot: Chapter 2 map-hub prefix, opens the real map hub
  (`registeredActionCount == 10`), and drives every tool from its real hub row,
  each with an asserted semantic effect AND a safe hub return (all
  relocation-independent `gDebugToolsProbe`/`gPlaySt`/`gBmSt` scalars):
  Unit inspect resolves Eirika (16/16) then a separate confirm applies
  Heal-to-Full (`unitHealTransactionCount 0 -> 1`; the byte-exact wounded->full
  HP delta stays the host proof since Eirika starts full); Convoy count
  `0 -> 1` across inspect/confirm-add/re-inspect; Flag `0 -> 1` on
  inspect/confirm-toggle (`chapterIndex == 2`); RNG seed `0x0000ee77 ->
  0x0000690b` across inspect/confirm-reseed/re-inspect; Save State classifies
  `SAVE_COMPAT_CURRENT` read-only (inspect count `0 -> 1`, unchanged on Back).
  After the last tool a final `B` closes the hub and the map is still
  interactive (player cursor `0x06 -> 0x07`). The host-executed tests remain
  the byte-exact mutation/invalid-input proof (real, unmodified
  `src/debugtools_tools.c`) that complements the live runtime; the
  config-parametrized release sibling proves the identical input is a
  compiled-out all-zero no-op.
- **Nintendo's legacy `AGBPrint*` print protocol, an interactive debugger,
  and an arbitrary memory editor** remain explicit, deliberate non-goals.
  Issue #68's separate, bounded mGBA debug-register transport does not give
  the legacy declarations mGBA-specific semantics. See
  `reports/debugtools_issue11_closure.md`'s "Explicit non-goals" section
  for the reasoning. The on-screen BG2 diagnostic line plus the bounded
  log ring/assert record (`src/debugtools_diag.c`) are this subsystem's
  retained, always-visible/queryable substitutes.
- **Migrating the remaining dormant chapter-selector/BGM-commit tools** out
  of `bmdebug.c`/`uidebug.c`/`menu_def.c` into the new registration API
  (Weather/Fog were migrated first; a chapter/skirmish selector specifically
  would also unlock the live-prep-screen gap above) is not part of this
  closure's WHAT and remains available as clearly-scoped future work.

## Release-safe mGBA logging (issue #68)

`include/expansion_log.h` provides the modern framework's small, typed,
main-thread-only diagnostic transport. It is separate from the structured
debug-tools ring: the ring remains the bounded in-ROM diagnostic record,
while this API sends transient developer messages to mGBA's `gba.debug`
logger.

The API accepts `EXPANSION_LOG_FATAL` through `EXPANSION_LOG_DEBUG` and
returns an explicit `enum ExpansionLogStatus`. `ExpansionLog_Write` accepts a
NUL-terminated string; `ExpansionLog_Printf`/`ExpansionLog_VPrintf` support
bounded `%`, `%c`, `%s`, `%d`/`%i`, `%u`, `%x`/`%X`, and `%p` formatting.
Messages hold at most 255 visible ASCII bytes plus their terminator. Longer
messages return `EXPANSION_LOG_TRUNCATED`; unsupported formatting and invalid
arguments return explicit errors.

Only the existing `FE8_EXPANSION_LOGGING_ENABLED` gate controls it. Modern
debug builds enable it; modern release builds compile every convenience macro
to `((void)0)` without evaluating arguments, and omit the backend, mGBA
register access, and debug-only message literals. The archival agbcc lane
does not compile `src/expansion_log.c`, so it has no object, layout, or
behavioral impact. There is intentionally no second logging feature flag.

On an enabled build, `ExpansionLog_Init` writes mGBA's official `0xC0DE`
handshake value to `0x04FFF780` and requires a `0x1DEA` readback before it
ever writes the 256-byte payload region at `0x04FFF600` or send register at
`0x04FFF700`. Availability is cached. A failed handshake returns
`EXPANSION_LOG_UNAVAILABLE` and sends nothing, making real hardware and
unsupported emulators fail closed rather than pretending a message was
delivered. Do not call the API from an interrupt handler or reentrantly.

For interactive mGBA SDL capture, use an isolated configuration/log path:

```bash
mgba -C logToStdout=1 -C logLevel.gba.debug=127 build/expansion-modern/debug/aapcs/fireemblem8.gba
mgba -C logToFile=1 -C logFile=build/mgba-debug.log \
  -C logLevel.gba.debug=127 build/expansion-modern/debug/aapcs/fireemblem8.gba
```

`expansion-modern-debuglog-check MODERN_CONFIG=debug MODERN_ABI=aapcs`
captures the deterministic `FE8LOG ready` boot message through the real
libmGBA core. Run the matching release command to prove the negative:

```bash
make expansion-modern-debuglog-check MODERN_CONFIG=debug MODERN_ABI=aapcs
make expansion-modern-debuglog-check MODERN_CONFIG=release MODERN_ABI=aapcs
```

The first command implements **TC-DEBUGLOG-001**: from a clean debug ROM,
boot normally and observe one `FE8LOG ready` entry through mGBA's `gba.debug`
output at its info level. Host stubs also prove successful and failed
handshakes, every severity encoding, formatting, and the 256-byte boundary.
The second implements
**TC-DEBUGLOG-002**: boot the same route in release, observe no entry, and
verify the target ELF omits the backend symbols and ready string. Both cases
have no save, generated-data, localization, resource allocation, or
debug-tools-ring interaction; the only debug resource cost is code/ROM data
measured by the normal linker budget check.

## Tester-facing procedure

[`TC-CORE-006`](test-cases/core-framework.md#tc-core-006-debug-tools-are-debug-only)
records the clean-boot debug-hub procedure, each bounded tool's semantic
effect, safe map return, and the release compiled-out negative control.

## Transient turn and faction control (issue #124)

Issue [#124](https://github.com/laqieer/fireemblem8-expansion/issues/124)
is a **framework capability** layered directly on the existing debugtools
session and the #85/#87 phase-control contract. It extends the existing
localized **Flag/Chapter** submenu rather than adding a hub, phase router,
prototype callback, or persistent configuration surface.

The submenu samples the current turn and red/green mode in its status line.
Its bounded, explicit confirmation rows queue exactly one request:

- **Apply Turn +1** and **Apply Turn -1** accept only the engine range
  `1..999`. Their menu override IDs are independently reserved as `0xF6`
  and `0xFC`; `0xFC` must not alias Music Preview's `0xFB` override.
- **Apply Red/Green CPU** records an explicit one-phase ordinary computer
  route.
- **Apply Red/Green Block** suppresses only the requested next red or green
  computer phase, then restores the ordinary computer route before the next
  phase begins.

Requests are accepted only while the map is in the stable interactive blue
`PLAYER` phase: the map and player-phase Procs must be live; no player action,
computer/berserk phase, event, battle, fade, or camera Proc may own the map;
and #85 must report no failure. `GetGameLock()` must equal the normal stable
map count of one, or exactly two while the active debugtools session owns the
one additional lock; orphan and extra locks reject. Direct request API calls
under those ownership guards fail closed: they increment rejected telemetry,
record `DEBUGTOOLS_PHASE_CONTROL_ERR_UNSAFE_BOUNDARY`, and refresh sampled
turn/modes, but never queue, apply, or restore a request. Only one request
may be pending: a second returns the typed pending error without overwriting
the original request, which then still consumes or expires through its
ordinary lifecycle. The request is not applied from the menu callback.
`BmMain_StartPhase` remains the sole faction router and consumes a matching
red/green request at the corresponding boundary. A turn request applies after
the phase switch but before `RunPhaseSwitchEvents`, so destination-faction
events observe the requested turn. A `BLOCKED` request jumps map-main directly
to the pre-trap tail, bypassing both its ordinary and berserk computer
children. Red therefore proceeds without invented trap work; green retains
its vanilla `gProcScr_UpdateTraps` and `DecayTraps` processing before the
next phase.

`PLAYER` is a closed typed rejection for red and green. The existing
`PlayerPhase_CommitActiveUnitMove` only commits blue units, so routing another
faction through it would not have proved safe semantics. Blue is likewise a
closed rejection: #85 is the sole blue controller and #87 remains its
one-phase delegation adapter. A live #85/#87 blue computer phase therefore
rejects this submenu's request rather than creating overlapping owners.

The request is fixed-size EWRAM state with no pointers or heap allocation.
It is cleared by fresh-map, restart, resume, `EndBMapMain`, and forced
debugtools-session cleanup. Resetting a queued request records
expiration/restoration and cannot carry it into a save, suspend, chapter
change, title return, or soft reset. A completed turn edit retains its
pre-edit turn only in debug-owned EWRAM: every `WriteSuspendSave` path
temporarily serializes that original turn through an immutable play-state
snapshot, then restores the live requested turn for phase events. The final
suspend field is written from the same retained value before the save block
is published. A fresh Resume therefore restores the original persistent turn;
control and edited full SRAM images are equal apart from the established
build-metadata normalization ranges. In debug builds, the probe records
sampled turn/modes plus requested, applied, rejected, expired, and restored
counters with the last typed request and result. Release builds keep the
pre-#124 `gDebugToolsProbe` size and zero contract while physically omitting
the request state, phase-control probe extension, and all
`DebugToolsPhaseControl*` code. No
`gPlaySt.config.debugControlRed`/`debugControlGreen` bit is read or written,
so save layout, epoch, configuration identity, generated data, and archival
behavior are unchanged.

The existing submenu remains bounded: it has eight live rows plus its
terminator, below `MENU_ITEM_MAX == 11`. All nine new message IDs (121--129)
remain stable and are translated in every authored catalog, but their
`debug_only` emission omits their catalog indices and UTF-8 payloads from
release ROMs. The debug-only request state is bounded to 16 bytes by the ARM
object check; its 48-byte probe extension is debug-only, preserving release
EWRAM/layout.

### TC-DEBUGTOOLS-PROTOTYPE-002: transient turn and faction control

- **Feature / originating issue:** `transient-debugtools-phase-control` /
  [#124](https://github.com/laqieer/fireemblem8-expansion/issues/124).
- **Supported configuration or artifact:** modern AAPCS debug ROM for the
  positive path; matching modern AAPCS release ROM for the omission negative.
- **Prerequisites and clean starting state:** for manual use, start a clean
  live map in an ordinary blue `PLAYER` phase, with no event, battle, fade,
  camera move, or active unit action. The deterministic automation reuses
  #87's resumed Chapter 2 map fixture to reach the same stable state.
- **Actions:** open the map debug hub, open **Flag/Chapter**, inspect the
  turn/red/green status, select one localized **Apply** row, close the hub,
  and complete the current blue phase. For `BLOCKED`, observe the requested
  red or green phase pass without either computer child acting. Green still
  performs its ordinary trap update/decay tail; then observe the next
  same-faction phase return to ordinary computer control.
- **Expected result:** one confirmed request produces one requested, applied,
  and restored telemetry transition. Turn changes after the switch and before
  destination-faction phase events; a blocked faction is skipped once;
  red/green CPU remains the existing route; map input returns after the
  submenu/hub close. The automatic boundary suspend saves the pre-edit turn,
  while a fresh ordinary Resume restores that persistent original even though
  the live boundary observed the requested turn.
- **Negative control:** Back/cancel leaves state unchanged. Out-of-range turn,
  blue/unknown faction, `PLAYER` mode, active event/battle/camera state,
  pending request, live #85/#87 blue computer ownership, forced lifecycle
  reset, and release input reject/expire without a persistent write. Release
  objects omit the request symbols and leave probe fields zero. Repeated,
  failed, expired, and reset suspend paths restore the live turn and leave no
  serialization swap state behind.
- **Interactions and save compatibility:** depends on #85 and is a stacked
  child of #87; it has no independent controller, no conflict with the #87
  blue marker, no feature flag, no save field, migration, epoch, or config
  identity input.
- **Automation:** `test_debugtools_phase_control.py` executes the real
  request functions, `BmMain_ChangePhase`, `BmMain_StartPhase`, forced map
  teardown, and forced debugtools-session cleanup with valid/adversarial
  states; it verifies phase-event ordering, an eligible berserk child is
  bypassed while green trap update/decay remains live under BLOCKED,
  battle/fade rejection, ARM request-state bounds, release
  layout/symbol omission, every authored locale, and repeated/failed/expired
  suspend-writer adversaries. The
  `expansion-modern-debugtools-phase-control-check` libmGBA scenario selects
  **Apply Turn +1** from the live submenu, uses #87's native one-phase Charge
  profile and resumed-map fixture to cross a real red boundary, captures the
  real automatic suspend, compares edited/control full SRAM images with only
  existing metadata ranges normalized, then starts a fresh emulator process
  and resumes the original saved turn. It also runs the matching release
  all-zero negative.
- **Cleanup and limitations:** exit with Back; use `make clean_fast` only for
  ignored artifacts. This is not red/green manual play, persistent AI policy,
  arbitrary turn editing, a strategy system, or a raw prototype menu import.

## Typed visual/status diagnostics (issue #127)

Issue #127 adds a read-only, fixed-layout diagnostics provider and two bounded
views to the existing hub. It does not register an action: built-in IDs
`1..10`, contributor IDs `11..65535`, both nine-entry capacities, and combined
registry introspection remain unchanged.

```c
enum DebugToolsResult DebugTools_CaptureDiagnostics(
    struct DebugToolsDiagnosticsSnapshot* out);
```

`struct DebugToolsDiagnosticsSnapshot` is exactly `0x40` bytes. Its validity
mask separates common, map, cursor, and unit fields so unavailable values are
never mistaken for real zeroes. Common fields are capture sequence, game
clock, Proc count, three-word RNG state, registered action count, structured
log counts/last code, and assert count/last code. Map/prep fields are chapter,
turn, faction, weather, fog range, cursor coordinates, and a bounds-checked
cursor unit's slot, character/class IDs, and current/max HP.

The provider is main-thread-only and succeeds only during a session opened by
an authoritative hotkey call site. `NULL` returns
`DEBUGTOOLS_ERR_INVALID_ARGUMENT`; title/map/prep conflicts return
`DEBUGTOOLS_ERR_CONTEXT_UNAVAILABLE`; disabled builds zero a non-NULL output
and return `DEBUGTOOLS_ERR_DISABLED`. Capture never advances RNG, writes a
log, mutates gameplay state, or touches SRAM.

Press `R` after the registered action page(s) to reach State, then Engine, and
then return to the first action page. Each diagnostics view has eight
read-only rows, Refresh, and Back: ten live rows within `MENU_ITEM_MAX == 11`.
A capture runs on view entry and once per edge-detected Refresh press; there
is no timer/per-frame refresh.

| Context | Availability |
| --- | --- |
| Title | `Title_IDLE`; common/RNG/log/assert/proc/action fields only |
| Live map | `PlayerPhase_MainIdle`; full map/cursor fields, unit fields only for a valid cursor unit |
| Prep | `PrepScreenProc_MapIdle` with `PLAY_FLAG_PREPSCREEN`; the same validated map/unit fields |
| Battle, battle event, map animation, fade, other screen | unavailable before display writes |

Battle remains unavailable because its renderer owns BG, OBJ, window, blend,
palette, and sometimes HBlank state. The dormant monochrome/status shortcuts,
retail `DebugMapMenu_DisplayInfo*`, arbitrary event/memory/proc browsers,
VRAM/OAM estimates, and performance profiling remain non-goals.

### Display owner and restoration

One Proc with `PROC_SET_END_CB` owns the built-in session and every menu is
its blocking child, so normal Back, deferred action/view/submenu transitions,
`EndAllMenus`, explicit forced close, owner `Proc_End`, and soft reset converge
on the idempotent owner end callback. Before the first menu it captures the
BG0/BG1 rectangle, BG offsets, active font pointer/counter, animated palette
entry, and game-lock baseline. It restores them, schedules BG sync, and only
then releases the session guard.

The diagnostics owner and views use ordinary `MenuProc`/`Text` rendering
inside the captured rectangle, independently of the cursor unit editor's
bounded submenu state. The feature adds `0x08` bytes of persistent
sequence/context state to normal debug builds; its dedicated scalar runtime
artifact adds the probe only under its test define. Release omits the
owner/views/backup and retains only the public disabled stub. The archival
lane sees no source, linker, or layout change.

The canonical human procedure and scalar-only host/libmGBA mapping is
[`TC-DEBUGTOOLS-DIAGNOSTICS-001`](test-cases/debugtools.md#tc-debugtools-diagnostics-001-typed-state-and-engine-diagnostics).
No screenshot or framebuffer hash is an acceptance oracle.
