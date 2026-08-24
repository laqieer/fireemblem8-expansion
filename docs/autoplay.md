# Transient blue-phase computer control

Issue [#85](https://github.com/laqieer/fireemblem8-expansion/issues/85)
adds the framework root for blue-army automation. It exposes the existing
computer-phase executor to blue units through one typed runtime control and a
fixed-size semantic telemetry record. The optional issue
[#87](https://github.com/laqieer/fireemblem8-expansion/issues/87) module adds
one default-off, one-phase map-menu command on top of that API. Neither layer
adds a strategy system, chapter objective model, persisted ownership setting,
or claim that the existing AI can complete arbitrary chapters.

The canonical tester procedure is
[`TC-AUTOPLAY-001`](test-cases/autoplay.md#tc-autoplay-001-blue-computer-phase-smoke).
The optional command has the separate
[`TC-AUTOPLAY-CHARGE-001`](test-cases/autoplay.md#tc-autoplay-charge-001-one-phase-charge-delegation)
procedure.
Issue [#86](https://github.com/laqieer/fireemblem8-expansion/issues/86)
builds on this telemetry with bounded semantic run-until scenarios; its
canonical procedure is
[`TC-AUTOPLAY-BOUNDS-001`](test-cases/autoplay.md#tc-autoplay-bounds-001-bounded-semantic-autoplay-termination).

## Public API

Include [`expansion_autoplay.h`](../include/expansion_autoplay.h):

```c
enum ExpansionAutoplayResult result =
    ExpansionAutoplay_SetBlueControl(EXPANSION_BLUE_CONTROL_COMPUTER);

if (result != EXPANSION_AUTOPLAY_OK)
{
    /* Reject the request; no controller change occurred. */
}
```

The only accepted controller values are:

- `EXPANSION_BLUE_CONTROL_PLAYER` - the default and reset state;
- `EXPANSION_BLUE_CONTROL_COMPUTER` - route the next blue phase through the
  existing `gProcScr_CpPhase`.

`ExpansionAutoplay_GetBlueControl()` returns the current transient value.
`ExpansionAutoplay_Reset()` restores `PLAYER` and clears telemetry. The engine
calls that reset for a fresh map, a restarted map, a resumed map, and map
cleanup. The value is never serialized. A save or suspend load therefore
always resumes with `PLAYER`, even if the previous process had requested
`COMPUTER`. A controller change requested during an active blue computer phase
returns `EXPANSION_AUTOPLAY_ERR_PHASE_ACTIVE`, preserves the running
controller, and records an explicit invalid-phase failure.

`gExpansionAutoplayTelemetry` is a 64-byte IWRAM record containing only `u32`
scalars. It has no pointers, callback addresses, Proc handles, or dynamically
sized storage. `ExpansionAutoplay_GetTelemetry()` returns a read-only view for
runtime clients; ELF probes may bind the global symbol directly.

The telemetry reports:

- current controller, lifecycle state, and explicit failure code;
- blue computer-phase starts and completions;
- the bounded eligible-actor count from `BuildAiUnitList`;
- committed action count and the last actor, action, target, and relation;
- red-hostility, green-alliance, and invalid-relation observations from the
  existing `AreUnitsAllied` path;
- debug-scenario activation count; and
- automatic suspend writes deliberately suppressed during a transient blue
  computer phase.

All cumulative counters saturate at `0xffffffff`. An invalid controller,
over-capacity roster, unsupported action, invalid actor, or inconsistent
alliance result records `EXPANSION_AUTOPLAY_STATE_FAILURE` and a typed
`ExpansionAutoplayFailure`; it does not silently select a fallback.

## Phase and action behavior

`BmMain_StartPhase` remains the sole phase router:

- blue + `PLAYER` starts `gProcScr_PlayerPhase`, unchanged;
- blue + `COMPUTER` starts `gProcScr_CpPhase`;
- red and green always start `gProcScr_CpPhase`, unchanged.

The computer phase still uses the original order, decide, perform,
post-action trap/event, cleanup, and phase-transition pipeline. Blue actor
enumeration uses the existing 62-slot faction range and existing `Unit.ai1`,
`Unit.ai2`, and `ai_config` values. Target classification continues to use
`AreUnitsAllied`: blue and green are allied, while blue and red are hostile.
Committed-action target relations use the same faction-bit rule through the
pure `IsAllegianceAllied` helper, so recording an action cannot increment the
AI relation-check counters it is reporting.
Fog and camera handling uses the executor's already-visible non-red path.
Phase events, healing setup, combat/staff/item/pillage/steal/refresh/talk,
ballista, summon, pick, post-action traps/events, cleanup, and game-over checks
remain on their existing paths.

The capability check accepts exactly the existing executor actions that have
a defined perform path: move/wait, combat, steal, pillage, staff, use-item,
refresh, talk, ride/exit ballista, dark-knightmare, summon, and pick. Blue
escape is rejected explicitly because the engine has no blue escape-point
table. Rescue and canto are not computer-phase action IDs and are therefore
outside this low-level executor rather than synthesized by a second AI path.
Call `ExpansionAutoplay_IsActionSupported()` when a downstream extension may
produce an action outside the existing decision pipeline.

The original AI writes an automatic suspend after each computer-controlled
actor. That save cannot represent this transient controller, so blue
computer phases suppress only those per-actor automatic suspend writes and
count each suppression in telemetry. Red/green computer phases and ordinary
player actions keep their existing suspend behavior. No save structure,
compatibility epoch, or preference changes.

## Scenario activation

The public setter is the normal programmatic seam for later event or optional
UI modules. In a modern debug build, pressing `SELECT+START+R` while the player
phase is idle calls that same validated setter, records one
`debugActivationCount`, and ends the current player phase normally. Red and
green then run unchanged, and the next blue phase enters `gProcScr_CpPhase`
through `BmMain_StartPhase`.

Release builds compile this activation path as an inert return. The chord is
deliberately a developer-facing scenario seam: it has no label, menu entry,
save bit, preference, or release behavior.

## Optional one-phase Charge command

Issue #87 is an **optional reusable module/reference implementation**, not a
change to the controller default. Enable it persistently with:

```bash
./configure --enable-blue-phase-delegate
make
```

or for one invocation:

```bash
make expansion-modern-rom EXPANSION_BLUE_PHASE_DELEGATE=1
```

| Surface | Contract |
| --- | --- |
| Autoconf | `--enable-blue-phase-delegate` / `--disable-blue-phase-delegate` |
| Make | `EXPANSION_BLUE_PHASE_DELEGATE=0|1` |
| C | `FE8_EXPANSION_BLUE_PHASE_DELEGATE=0|1` |
| Default/lifecycle | `0`; permanent project choice |
| Dependency | Modern issue #85 controller/telemetry; enabling without the modern controller is a compile-time error |
| Identity | Included in the deterministic configuration fingerprint and metadata |
| Save | No field, preference, epoch, migration, or serialized controller |

Enabled C builds may include
[`expansion_blue_phase_delegate.h`](../include/expansion_blue_phase_delegate.h).
`ExpansionBluePhaseDelegate_GetAvailability()` returns a typed result for the
current map-menu state; `ExpansionBluePhaseDelegate_Start()` revalidates and
starts only an `OK` state; `ExpansionBluePhaseDelegate_IsPending()` identifies
the one transient restoration marker; and
`ExpansionBluePhaseDelegate_CountEligibleBlueUnits()` reports the bounded
shared-AI count. Rejections distinguish wrong faction, lock/event state,
another blocking action, no eligible unit, and a controller failure. No
rejected call changes the controller or phase.

When enabled, the map menu appends one localized **Charge** row. Its label and
help metadata use stable expansion message IDs `autoplay.charge.label` (79)
and `autoplay.charge.help` (80). The vanilla map-menu help renderer accepts
vanilla message IDs rather than independent `ExpansionMsgId` values, so `R`
on Charge is intentionally inert instead of resolving ID 80 through the wrong
catalog. A downstream presentation can consume the stable help ID through the
expansion resolver without changing the command contract. The row is shown
only while the ordinary map menu
owns the sole game lock in a live interactive blue `PLAYER` phase, no event,
fade, camera, action child, computer phase, berserk phase, or prior delegation
is active, and at least one blue unit passes the same eligibility predicate
used by `BuildAiUnitList`. Sleeping, berserk, hidden, unselectable/already
moved, dead, rescued, and empty slots are excluded.

Selection revalidates that state, sets `COMPUTER` through
`ExpansionAutoplay_SetBlueControl`, and redirects the already-blocked
`gProc_BMapMain` to its existing `BmMain_StartPhase` label without rerunning
phase-start events, healing, or turn increments. The current faction therefore
stays blue and only the remaining eligible units enter `gProcScr_CpPhase`.
A base `Proc` marker watches the parent telemetry; after the computer Proc
exits on success or explicit failure, the internal validated restore hook
returns the controller and telemetry controller field to `PLAYER` and ends the
marker. The next blue phase is the ordinary interactive player phase.

The marker uses an existing Proc-pool slot and adds no static EWRAM or IWRAM.
It is never serialized. A reset, suspend-resume, restart, or map cleanup uses
the issue #85 lifecycle reset and therefore resumes as `PLAYER`. The feature
does not add `NOBODY`, persistent autoplay, a settings screen, authored
strategy/objective data, chapter rules, or a second phase router.

Issue [#124](https://github.com/laqieer/fireemblem8-expansion/issues/124)
is the genuine debugtools child of this layer. It never requests blue
ownership: its red/green transient requests are accepted only while this
controller is in stable blue `PLAYER`, so a live Charge marker or blue
computer phase rejects the request. `BmMain_StartPhase` remains the sole
router for both layers, and either layer's normal map lifecycle reset clears
pending debugtools state without touching a save.

The optional Threat Range row composes with Charge: the base map menu has
eight visible rows, either option adds one, and both together use ten of
`MENU_ITEM_MAX == 11`. A downstream project adding or replacing another row
must make its capacity/order choice explicitly rather than silently displacing
either command.

## Bounded semantic run-until scenarios

`tools/gba-playtest` schema version 2 adds one opt-in `run_until` profile. It
does not change schema-version-1 fixed-frame scenarios or their
format-version-2 fingerprints. A bounded scenario declares:

- one unconditional positive `max_frames` count;
- one required `success` condition and optional mutually exclusive
  `objective_failure` / `controller_exhausted` conditions;
- conjunctions of `eq`, `ne`, `lt`, `le`, `gt`, or `ge` comparisons over
  bounded 1/2/4-byte literal or ELF-symbol probes;
- optional named turn/action counters with positive maxima;
- an optional monotonic progress epoch, a separate semantic
  `work_expected` comparison, and a positive unchanged-frame stall bound; and
- one terminal checkpoint template with the existing framebuffer, SRAM,
  region, pixel, and semantic-probe capture fields but no authored frame.

The backend evaluates state after every emulated frame. Explicit terminal
conditions win first, followed by `engine_stall`, `max_turns`, `max_actions`,
and `max_frames`. Success observed exactly at a hard bound remains success.
The resulting format-version-3 fingerprint captures one checkpoint and one
typed terminal record containing the reason, dynamic frame, and bound
turn/action values alongside normal scenario and ROM provenance.

The seven terminal reasons are deliberately not interchangeable:

- `success` means the authored objective state became observable;
- `objective_failure` means the ROM explicitly reported objective loss;
- `controller_exhausted` requires explicit no-legal-action telemetry;
- `engine_stall` means a ROM-supplied monotonic epoch stopped changing while
  work was explicitly expected; and
- `max_frames`, `max_turns`, and `max_actions` identify the exact exhausted
  budget.

Unit coordinates, pointers, source text, wall-clock duration, and framebuffer
similarity are not progress proxies. A stationary defend/wait state reports
work not expected and therefore does not accrue stall frames. Epoch regression
is a deterministic error. Malformed, duplicate, overlapping, impossible, or
unresolved conditions fail before ROM execution. Semantic failure and budget
outcomes are captured once and never retried; the existing retry option
remains limited to host process timeouts.

The real debug scenario reuses #85's clean Chapter 2 activation route and
pointer-free telemetry. It binds progress and action count to
`committedActionCount`, work expected to `COMPUTER_PHASE`, turn count to
`gPlaySt.chapterTurnNumber`, objective failure to the typed failure state, and
success to the first failure-free `COMPUTER_PHASE_COMPLETE`. The checked
capture stops at frame 17134 with turn 2, six actions, one start/completion,
104 red-hostile checks, 56 green-allied checks, and no invalid/failure record.
The equivalent fixed scenario previously observed its later checkpoint at
frame 18000.

Debug and release default-PLAYER controls use the same bounded schema but
never activate COMPUTER. Both terminate as `max_frames` at frame 3950 with
turn 1 and zero starts, completions, actions, failures, or debug activations.
The generated homebrew fixture independently reaches every terminal reason
and proves objective failure is selected before stall classification.

This is host/runtime-test infrastructure only:

- **Dependencies:** #85's public control/telemetry contract,
  `tools/gba-playtest`, existing ELF probe bindings, libmGBA, and the
  tester-case catalog.
- **Dependents:** authored objectives (#88), accelerated-fidelity work (#89),
  and later strategy/batch/planner layers through their own contracts.
- **Conflicts:** none; fixed-frame scenarios and fingerprints remain valid and
  are exercised unchanged.
- **Profiles:** generated homebrew/libmGBA host integration, modern AAPCS debug
  positive, and modern AAPCS debug/release default negatives. Linux/libmGBA
  remains the CI runtime.
- **Save/config/data:** no save field, migration, compatibility epoch,
  configuration identity, Autoconf/Make feature flag, generated game data, or
  localization change.
- **ROM/RAM/archival:** no target source, ROM bytes, RAM allocation, linker
  budget, or archival runtime behavior changes. The legacy lane remains a
  compile-compatibility check only.

## Compatibility and budgets

- **Dependencies:** `BmMain_StartPhase`, `gProcScr_CpPhase`,
  `BuildAiUnitList`, `Unit.ai[]`, `AreUnitsAllied`, `gba-playtest`, and the
  tester-case catalog.
- **Dependents:** issues #86, #87, #88, #89, and #90; #91 consumes the settled
  telemetry through its own prerequisites. Issue #92 must not introduce a
  competing observation/action seam.
- **Conflicts:** none known. The default `PLAYER` path is the required
  conflict-free negative.
- **Configuration:** the #85 foundation has no build flag. The optional #87
  command adds the strict default-off Autoconf/Make/C surface documented
  above and folds it into configuration identity.
- **Data/UI:** no generated-data, localization, menu, objective, or strategy
  record in the #85 foundation. The optional #87 module adds only two
  expansion-catalog messages and one gated map-menu row.
- **Save:** no field, migration, preference, or compatibility-epoch change.
- **Archival:** `src/expansion_autoplay.c` and all hooks are excluded from
  `FE8_ARCHIVAL_BUILD`; the agbcc lane is unchanged.
- **Modern budget:** 68 bytes persistent IWRAM total (64-byte telemetry plus
  4-byte controller), moved together so they survive every map, battle, and UI
  overlay. The tight all-locale debug profile retains 272 bytes of IWRAM static
  growth headroom above the required 4 KiB user-stack margin and recovers 68
  EWRAM bytes, turning the previous 24-byte overflow into 44 bytes of headroom.
  Default debug/release EWRAM remains unchanged from the immediate base. The
  module contributes 768 bytes of debug Thumb text and 692 bytes of release
  Thumb text.
- **Issue #87 budget:** the two stable catalog messages add 528 debug / 544
  release linked ROM bytes to the disabled build. Enabling Charge adds another
  840 debug / 808 release linked ROM bytes, including the dedicated module's
  400/420 bytes of Thumb text and 24 bytes of ROM-resident Proc script data.
  It adds zero static EWRAM/IWRAM: enabled/default builds retain 1,704/3,128
  EWRAM bytes free (debug/release) and 1,552 IWRAM static-growth bytes above
  the 4 KiB stack margin. The named all-locales/all-features release profile
  enables Charge and Threat Range together and retains 732 EWRAM bytes plus
  272 IWRAM static-growth bytes.

## Validation

```bash
python3 -m unittest tools.gba-playtest.tests.test_expansion_autoplay -v
make expansion-modern-autoplay-check MODERN_CONFIG=debug MODERN_ABI=aapcs
make expansion-modern-autoplay-check MODERN_CONFIG=release MODERN_ABI=aapcs
python3 -m unittest tools.gba-playtest.tests.test_expansion_blue_phase_delegate -v
make expansion-modern-blue-phase-delegate-check MODERN_CONFIG=debug MODERN_ABI=aapcs
make expansion-modern-blue-phase-delegate-check MODERN_CONFIG=release MODERN_ABI=aapcs
make expansion-modern-autoplay-bounds-check MODERN_CONFIG=debug MODERN_ABI=aapcs
make expansion-modern-autoplay-bounds-check MODERN_CONFIG=release MODERN_ABI=aapcs
make expansion-modern-localization-profile-headroom-check MODERN_CONFIG=debug MODERN_ABI=aapcs
make expansion-modern-localization-profile-headroom-check MODERN_CONFIG=release MODERN_ABI=aapcs
```

The debug positive uses a clean Chapter 2 fixture because it contains blue,
red, and green actors whose existing zero-valued AI bytes select the symbolic
`AI_A_00`/`AI_B_00` policies. It requests `COMPUTER` with the debug chord and
never selects a player-unit action. The checked telemetry records one
completed blue computer phase, six eligible actors, six committed legal
actions, red-hostility and green-alliance checks, and normal turn/faction
progression. The debug and release negatives use an ordinary clean Prologue
route and retain `PLAYER` with zero blue computer-phase starts, completions,
or actions.

The Charge positive follows the proven Chapter 2 dialogue route, opens the
real map menu, wraps to the last localized row, exercises its safe inert
`R` guard, and selects it without the #85 debug chord. Five eligible blue
actors commit five legal actions in the current phase; telemetry records one
start, one completion, 76 red-hostile checks, 17 green-allied checks, zero
invalid/failure records, and five suppressed suspend writes. Turn 2 then
reaches blue `PLAYER` control. The host fixture separately proves one
already-moved unit plus sleeping, berserk, rescued, dead,
hidden/unselectable, and empty slots do not enter the same shared eligibility
predicate.

## Limitations and rollback

This foundation does not implement seize, recruitment, village, chest,
resource, survival, balance, or campaign planning. Existing low-level AI may
move or attack but is not a chapter-completion oracle. Remove the controller
module, its guarded hooks, runtime gate, and documentation to roll back; no
save or generated-data recovery is required.
