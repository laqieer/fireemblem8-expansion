# Transient blue-phase computer control

Issue [#85](https://github.com/laqieer/fireemblem8-expansion/issues/85)
adds the framework root for blue-army automation. It exposes the existing
computer-phase executor to blue units through one typed runtime control and a
fixed-size semantic telemetry record. It does not add a strategy system,
chapter objective model, menu, persisted preference, or claim that the
existing AI can complete arbitrary chapters.

The canonical tester procedure is
[`TC-AUTOPLAY-001`](test-cases/autoplay.md#tc-autoplay-001-blue-computer-phase-smoke).

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

## Compatibility and budgets

- **Dependencies:** `BmMain_StartPhase`, `gProcScr_CpPhase`,
  `BuildAiUnitList`, `Unit.ai[]`, `AreUnitsAllied`, `gba-playtest`, and the
  tester-case catalog.
- **Dependents:** issues #86, #87, #88, #89, and #90; #91 consumes the settled
  telemetry through its own prerequisites. Issue #92 must not introduce a
  competing observation/action seam.
- **Conflicts:** none known. The default `PLAYER` path is the required
  conflict-free negative.
- **Configuration:** no Autoconf option, Make variable, C feature macro, or
  configuration-identity field.
- **Data/UI:** no generated-data, localization, menu, objective, or strategy
  record.
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

## Validation

```bash
python3 -m unittest tools.gba-playtest.tests.test_expansion_autoplay -v
make expansion-modern-autoplay-check MODERN_CONFIG=debug MODERN_ABI=aapcs
make expansion-modern-autoplay-check MODERN_CONFIG=release MODERN_ABI=aapcs
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

## Limitations and rollback

This foundation does not implement seize, recruitment, village, chest,
resource, survival, balance, or campaign planning. Existing low-level AI may
move or attack but is not a chapter-completion oracle. Remove the controller
module, its guarded hooks, runtime gate, and documentation to roll back; no
save or generated-data recovery is required.
