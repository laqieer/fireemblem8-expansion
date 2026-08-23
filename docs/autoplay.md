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

## Typed chapter objectives and AI groups

Issue [#89](https://github.com/laqieer/fireemblem8-expansion/issues/89)
adds the generic authored-data seam that supplies objective state to bounded
autoplay. It does not add a strategy policy, a route, an AI assignment
precedence rule, player-visible objective text, or a project-specific chapter
record. The default `src/data/chapter_objectives.json` has no chapter records;
every existing chapter therefore remains objective-inactive.

The `chapterobjectives` generated-data table is owned by the existing
`chapterbundle` declaration and validates against the existing unit groups,
character constants, event flags, and typed event-helper catalog. Its only
initial kinds are:

- `protect`: keep one referenced character alive until another typed
  objective completes;
- `reach_area`: every live member of one AI group reaches an inclusive,
  bounded rectangle;
- `defeat_group`: every live member of one AI group is absent or defeated;
- `event_flag`: observe a named existing `EVFLAG_*`; and
- `hold_until_turn`: keep one group in an inclusive rectangle through a
  bounded chapter turn.

AI groups expose validated membership only. They do not choose targets,
movement, actions, scoring, or precedence; those decisions remain for the
later strategy layer. Group members reference both a symbolic character and
an existing chapter unit-group symbol, so an author cannot silently attach an
objective to a similarly named or unrelated unit.

Activation and deactivation are derived exclusively from existing event flags.
Event scripts set/clear those flags through the existing `flag.set` and
`flag.clear` helper operations (or existing hand-authored events); no
objective opcode, event router, or alternate manifest exists. The evaluator
recomputes pending/success/failure from the current chapter, flags, units,
and turn on every battle-map task tick and phase start. It stores no history: an authored event
that must remember a transition must latch that decision in an existing event
flag. Suspend/load is therefore safe by reconstruction and adds no hidden or
serialized state.

`gExpansionChapterObjectiveTelemetry` is a separate 16-byte IWRAM,
pointer-free record with the selected stable objective ID, state, progress,
and active-objective count. The generic #86 ELF probe resolver can bind all
four fields directly; this keeps #85/#86's existing 64-byte telemetry layout,
fixed fingerprints, and terminal contracts unchanged. A failure wins telemetry
selection over pending, which wins over success, with source order breaking
ties deterministically.

The generated table budgets are 32 chapter bundles, eight objectives and
eight groups per chapter, and 16 members per group. Modern generated data
uses 12 bytes per bundle, 12 bytes plus members per group, and 28 bytes per
objective; the default empty table is one 12-byte sentinel. The runtime adds
16 IWRAM bytes and no EWRAM, save, migration, compatibility epoch,
localization, configuration identity, or feature gate. It is excluded from
the archival lane.

The canonical procedure is
[`TC-AUTOPLAY-OBJECTIVE-001`](test-cases/autoplay.md#tc-autoplay-objective-001-typed-authored-objective-lifecycle).

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

## Typed autoplay strategy profiles

Issue [#90](https://github.com/laqieer/fireemblem8-expansion/issues/90)
adds one bounded generated registry and dispatch seam for reusable autoplay
policy. It is an optional reusable module: the registry/assignment types are
generic, while **Aggressive** and **Objective-first** are the two permanent
default-off references selected by `EXPANSION_AUTOPLAY_STRATEGIES=1`.
The default `src/data/autoplay_strategies.json` contains no strategy or
assignment records, preserving the existing `Unit.ai[]` computer decision
path exactly.

The generated `autoplaystrategies` table is owned by `chapterbundle` alongside
typed objectives. It has at most eight descriptors and eight group plus eight
unit assignments per chapter. A descriptor carries a stable FNV-1a symbolic
ID, callback, objective/action capabilities, and reference-profile marker.
The dispatcher chooses the first active assignment in this fixed order:
**unit, group, chapter, existing low-level `Unit.ai[]` fallback**. Source
order is never a tie-breaker between assignment scopes; duplicate targets are
rejected during generation. Active objective selection retains the existing
failure/pending/success priority and authored-record-order tie rule, never a
pointer or link address.

Aggressive calls the existing legal combat selector first, then leaves the
unchanged low-level AI to pursue or fall back. Objective-first handles only
the active `reach_area` or `hold_until_turn` group member: it projects the
unit onto the inclusive rectangle, uses the existing movement selector, then
falls back to Aggressive and the existing AI. The projection is coordinate
only (clamp X then Y); combat uses existing slot/item/map scans. Neither
profile consumes RNG or derives a decision from pointer/link addresses, so a
fixed seed/configuration repeats its trace exactly. Existing low-level
fallback behavior retains its own established RNG contract.

`ExpansionAutoplayStrategies_ValidateRegistry()` rejects capacity overflow,
zero/duplicate IDs, missing callbacks, and undeclared capability bits.
`ExpansionAutoplayStrategies_ValidateObjectiveSupport()` makes an
unknown/unsupported profile-objective pair explicit. Before a selected
strategy can commit a computer-phase action, dispatch checks those contracts.
It records a typed autoplay failure and terminates the strategy path rather
than selecting a success-shaped fallback. The typed event helper
`ExpansionAutoplayStrategies_ActivateAssignment()` validates a generated
strategy-ID/activation-flag pair, changes only that existing event flag, and
rejects calls during the active blue computer phase; the next phase is its
safe boundary. It introduces no event language, hidden state, save byte,
epoch, migration, localization string, or player UI.

For a third strategy, define one callback with the public context signature,
declare its stable ID/capabilities in `autoplay_strategies.json`, and add its
chapter/group/unit assignment. The generated registry emits its typed callback
reference; the shared dispatcher remains unchanged. The default references
are not a taxonomy: Balanced, EXP, Treasure, Support, campaign, and
project-specific character/chapter policies remain out of scope.

The runtime creates no EWRAM or IWRAM state. The empty generated registry and
bundle sentinels occupy 40 ROM bytes (20 bytes each). In the focused AAPCS
objects, profiles-off/on text is 1016/1196 bytes in debug and 1268/1468 bytes
in release; the reference callbacks therefore add 180 debug or 200 release
text bytes. Strategy activation reconstructs from existing generated data and
event flags. The strategy host/ARM selector enforces zero EWRAM and a 4 KiB
aggregate object-text ceiling for both profile states. The archival lane
excludes the runtime and generated table.

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

## Deterministic finite autoplay batch reports

Issue [#91](https://github.com/laqieer/fireemblem8-expansion/issues/91) adds
the `tools/gba-playtest/autoplay_batch.py` host collector. It is a framework
capability built directly on #85's telemetry, #86's bounded terminal contract,
#89's semantic group/objective telemetry, and #90's profile identity. Its
immediate stack parent is #90; #88 accelerated fidelity is explicitly not a
stack parent or requirement. `TC-AUTOPLAY-BATCH-001` uses only normal fidelity.

The batch CLI requires one exact ROM/ELF, one schema-version-2 bounded
scenario, one versioned specification, an explicit finite unique seed list,
bounded `--max-jobs`, and explicit frame/turn/action bounds. The values must
match the scenario's hard limits exactly. Each seed starts a new libmGBA core
from clean boot. A specification supplies the only permitted seed mechanism:
an exact linked EWRAM/IWRAM binding and a frame at which that value is written.
The report therefore identifies a real seed injection rather than claiming
that an arbitrary label changed the ROM's RNG.

The version-1 specification declares normal-fidelity profile/configuration
identity and semantic metric descriptors. Metric probes must be part of the
terminal checkpoint, keeping faction/group survivor and casualty counts,
recruitment/village/chest outcomes, and configured EXP/item/resource deltas
grounded in ROM-supplied semantic telemetry. Unsupported metrics, duplicate or
implicit seeds, omitted bounds, unresolved metric probes, reuse of a writable
SRAM image, and output collisions fail before execution.

The version-1 report contains sorted ROM/configuration/scenario/profile/bound
provenance and one sorted run record per seed. A terminal success, objective
failure, stall, or exhausted frame/turn/action budget is all retained with its
terminal counters and declared metrics; non-success records make the command
fail visibly rather than disappearing from deterministic terminal and
per-metric distributions. Parallel scheduling cannot affect report order or
bytes. The companion `compare` command reports
added/removed seeds and terminal/metric deltas without inferring statistical
significance, difficulty, or balance. It only creates a new ignored
`build/` output and cannot refresh either input report.

This host-only layer adds no ROM code, RAM allocation, feature gate,
configuration-identity field, generated game data, localization, save byte,
migration, compatibility epoch, or archival-lane behavior. Removing the batch
script, its bounded backend seed-write record, tests, and documentation rolls
back the feature without save recovery.
