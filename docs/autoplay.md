# Transient blue-phase computer control

Issue [#85](https://github.com/laqieer/fireemblem8-expansion/issues/85)
adds the framework root for blue-army automation. It exposes the existing
computer-phase executor to blue units through one typed runtime control and a
fixed-size semantic telemetry record. It does not add a strategy system,
chapter objective model, menu, persisted preference, or claim that the
existing AI can complete arbitrary chapters.

The canonical tester procedure is
[`TC-AUTOPLAY-001`](test-cases/autoplay.md#tc-autoplay-001-blue-computer-phase-smoke).
Issue [#86](https://github.com/laqieer/fireemblem8-expansion/issues/86)
builds on this telemetry with bounded semantic run-until scenarios; its
canonical procedure is
[`TC-AUTOPLAY-BOUNDS-001`](test-cases/autoplay.md#tc-autoplay-bounds-001-bounded-semantic-autoplay-termination).
Issue [#88](https://github.com/laqieer/fireemblem8-expansion/issues/88) is the
stacked accelerated-fidelity child of #86; its canonical paired-profile
procedure is
[`TC-AUTOPLAY-ACCEL-001`](test-cases/autoplay.md#tc-autoplay-accel-001-accelerated-fidelity-equivalence).

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

## Accelerated-fidelity profile

Schema version 3 adds an explicit `execution_profile` beside the existing
normal-fidelity baseline. It remains a bounded run-until scenario and preserves
all schema-v1/v2 scenario and fingerprint formats unchanged. Both profiles
call `core->runFrame()` for every emulated frame; acceleration is never a
simulation or an engine shortcut.

The `accelerated-fidelity` profile binds the existing `gPlaySt.config` word at
one declared frame and applies only `gameSpeed` plus the existing animation
option selected by `BANIM_PRESENTATION_POLICY_OFF`. Its emulator core and
temporary SRAM copy are destroyed after capture, so it cannot persist either
choice. `normal-fidelity` has no configuration write. The profile also names
semantic trace probes, which are canonicalized by binding and size so
equivalent input order produces one stable fingerprint shape. The backend
emits a complete snapshot only when one of those semantic values changes,
preserving action/RNG order without treating wall-clock timing as behavior.
The dedicated accelerated-fidelity test ROM alone records the first observed
state and every later bounded ordered state transition at the event
command-commit seam, including the command, slot-C/counter, and
named objective flags (`EVFLAG_WIN`, `EVFLAG_DEFEAT_ALL`, and
`EVFLAG_GAMEOVER`);
the terminal checkpoint compares every record and fails on overflow. Endpoints
also cover the active blue, red, and green unit slots. The declared frame bound
multiplied by trace-probe count may not exceed 450,000 records, bounding backend
output and the host's captured trace memory.

Profile plans use backend format 5. A semantic-only terminal checkpoint emits
its probes/SRAM state and no whole-framebuffer hash; the framebuffer remains
allocated and attached to libmGBA, and region/pixel/visual scenarios still
require the normal framebuffer capture path. Format-version-4 fingerprints
record the profile state and trace. The paired comparator ignores emulated
frame timestamps while requiring exact ROM provenance, terminal
reason/counters, endpoint semantic probes, and ordered trace values to match
exactly. Repeated samples of the same profile compare complete format-4
fingerprints, including terminal and trace frame timestamps; no snapshot may
claim configuration or trace activity after its terminal frame.
Accelerated-fidelity rejects framebuffer, region, and pixel evidence entirely;
schema-v3 normal fidelity retains those presentation-dependent contracts.

The existing presentation-policy seam adopts a raw `gPlaySt.config.animationType`
change when its cached selection disagrees. The accelerated test ROM additionally
probes `BanimPresentationPolicy_GetCurrent()` and requires
`BANIM_PRESENTATION_POLICY_OFF`, rather than treating config bits alone as proof.
That private profile alone reserves 1,316 EWRAM bytes for the transition
record, prior-state snapshot, and policy probe; ordinary debug/release,
starter/HQ, and archival builds omit all three.

The focused Chapter 2 fixture freezes 17,135 normal-fidelity frames and
16,869 accelerated-fidelity frames (a 266-frame reduction). The benchmark
records libmGBA/package version, host and runner identity, exact ROM
provenance/configuration, source commit, frame counts, and three non-gating
wall-clock samples. It rejects a deliberately perturbed trace even if that
candidate completes faster. Visual, audio, or presentation-timing cases must
remain on normal fidelity.

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
- **Dependents:** accelerated-fidelity comparison (#88), later integration
  work (#89), and strategy/batch/planner layers through their own contracts.
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
python3 -m unittest tools.gba-playtest.tests.test_run_until -v
make expansion-modern-autoplay-check MODERN_CONFIG=debug MODERN_ABI=aapcs
make expansion-modern-autoplay-check MODERN_CONFIG=release MODERN_ABI=aapcs
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

## Limitations and rollback

This foundation does not implement seize, recruitment, village, chest,
resource, survival, balance, or campaign planning. Existing low-level AI may
move or attack but is not a chapter-completion oracle. Remove the controller
module, its guarded hooks, runtime gate, and documentation to roll back; no
save or generated-data recovery is required.
