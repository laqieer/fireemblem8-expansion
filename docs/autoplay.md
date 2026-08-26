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
help metadata use stable expansion message IDs `autoplay.charge.label` (80)
and `autoplay.charge.help` (81). The vanilla map-menu help renderer accepts
vanilla message IDs rather than independent `ExpansionMsgId` values, so `R`
on Charge invokes the custom expansion helpbox handler. That handler resolves
ID 81 through the expansion ROM catalog and opens `StartHelpBoxString`; it
never passes the expansion ID through the vanilla catalog or changes the
command contract. The row is shown
only while the ordinary map menu
owns the sole game lock in a live interactive blue `PLAYER` phase, no event,
fade, camera, action child, computer phase, berserk phase, or prior delegation
is active, and at least one blue unit passes the same eligibility predicate
used by `BuildAiUnitList`. Sleeping, berserk, hidden, unselectable/already
moved, dead, rescued, and empty slots are excluded.

Charge help passes the resolver's persistent ROM-catalog pointer directly to
the asynchronous helpbox. It therefore remains valid through later menu-label
resolves without allocating a second EWRAM scratch buffer.

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
choice. The binding is an aligned 4-byte writable EWRAM/IWRAM word; ROM, VRAM,
palette, OAM, and SRAM bindings fail before execution, and the backend reads
the word back before emitting `PROFILE`. Format-4 validation requires
`config_after` to be the exact accelerated transformation of `config_before`:
game speed set, animation OFF, and every unrelated bit unchanged.
`normal-fidelity` has no
configuration write. The profile also names
semantic trace probes, which are canonicalized by binding and size so
equivalent input order produces one stable fingerprint shape. The backend
emits an initial complete snapshot at frame 0 and another only when one of
those semantic values changes. External format-4 traces must retain that
frame-0 snapshot, strictly increase later snapshot frames, keep each snapshot
at or below 512 probes, and remain within the 450,000-record aggregate limit.
This preserves action/RNG order without treating wall-clock timing as behavior.
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

## Typed chapter objectives and AI groups

Issue [#89](https://github.com/laqieer/fireemblem8-expansion/issues/89)
adds the generic authored-data seam that supplies objective state to bounded
autoplay. It does not add a strategy policy, a route, an AI assignment
precedence rule, player-visible objective text, or a project-specific chapter
record. The default `src/data/chapter_objectives.json` has no chapter records;
every existing chapter therefore remains objective-inactive. Its sentinel-only
generated table also omits objective phase and map-task hooks, preserving the
existing default combat-frame timing.

The `chapterobjectives` generated-data table is owned by the existing
chapter-bundle collection: every authored `src/data/*_bundle.json` is indexed
by chapter identity, and each objective record resolves only through its one
matching owner bundle. That bundle loads its own declared table sources,
including unit and event-list data, before validation; it never inherits
another chapter's defaults. Objective areas are additionally bounded by the
owner chapter's authored map width and height. Its only initial kinds are:

- `protect`: keep one referenced character alive until another typed
  objective completes, latching a pre-completion violation in a required
  existing `failureFlag` and latching its first completion in a distinct
  `completionFlag`;
- `reach_area`: every live member of one AI group reaches an inclusive,
  bounded rectangle;
- `defeat_group`: every live member of one AI group is absent or defeated;
- `event_flag`: observe a named existing `EVFLAG_*`; and
- `hold_until_turn`: keep one group in an inclusive rectangle through a
  bounded chapter turn, latching its first violation in a required existing
  `failureFlag`.

AI groups expose validated membership only. They do not choose targets,
movement, actions, scoring, or precedence; those decisions remain for the
later strategy layer. Group members reference both a symbolic character and
an existing unit-group symbol owned by the same chapter bundle, so an author
cannot silently attach an objective to a similarly named or unrelated unit.

Activation and deactivation are derived exclusively from existing event flags.
Event scripts set/clear those flags through the existing `flag.set` and
`flag.clear` helper operations (or existing hand-authored events); no
objective opcode, event router, or alternate manifest exists. The evaluator
recomputes pending/success/failure from the current chapter, flags, units,
and turn on every battle-map task tick and phase start. `hold_until_turn`
additionally sets its declared existing `failureFlag` on the first missing,
rescued, dead, or out-of-area member; later re-entry cannot clear that latch.
After an unfailed hold reaches its deadline it is terminal success before
later unit-area checks. Objective evaluation may reset/show setup telemetry
while chapter beginning events run, but cannot set protect/hold flags until
the post-`CallBeginningEvents` readiness hook. All other authored history
must likewise be represented by an existing event flag. Suspend/load therefore
reconstructs the same state without hidden or serialized objective data.

Recruitment, village, and chest events remain authored event-script behavior:
their existing success path must set a named, persistent `EVFLAG_*`, and an
`event_flag` objective observes that latched flag. Objectives never infer
those outcomes from animation, menus, inventory, or unit appearance.

`gExpansionChapterObjectiveTelemetry` is a separate 16-byte EWRAM,
pointer-free record with the selected stable objective ID, state, progress,
and active-objective count. The generic #86 ELF probe resolver can bind all
four fields directly; this keeps #85/#86's existing 64-byte telemetry layout,
fixed fingerprints, and terminal contracts unchanged. A failure wins telemetry
selection over pending, which wins over success, with source order breaking
ties deterministically.

The generated table budgets are 32 chapter bundles, eight objectives and
eight groups per chapter, and 16 members per group. Modern generated data
uses 12 bytes per bundle, 12 bytes plus members per group, and 28 bytes per
objective; the default empty table is one 12-byte sentinel. The telemetry
record remains 16 EWRAM bytes; both modern debug and release profiles allocate
20 EWRAM bytes after the two transient readiness bytes and alignment. Neither
changes IWRAM, save, migration, compatibility epoch,
localization, configuration identity, or feature gate. Each authored
telemetry refresh uses a bounded 1 KiB stack index and scans the 255 unit
slots once, eliminating per-member character scans while remaining within the
4 KiB stack bound. It is excluded from the archival lane.

The canonical procedure is
[`TC-AUTOPLAY-OBJECTIVE-001`](test-cases/autoplay.md#tc-autoplay-objective-001-typed-authored-objective-lifecycle).

This is production runtime infrastructure with host/runtime test coverage:

- **Dependencies:** #85/#86's public control/telemetry and bounded probe
  contracts, the generated-data registry/chapter-bundle owner model,
  `tools/gba-playtest`, existing ELF probe bindings, libmGBA, and the
  tester-case catalog.
- **Dependents:** strategy profiles (#90), batch simulation (#91), and
  planner/campaign work (#92) consume this typed status seam through their
  own contracts.
- **Conflicts:** none; fixed-frame scenarios and fingerprints remain valid and
  are exercised unchanged.
- **Profiles:** generated homebrew/libmGBA host integration, modern AAPCS debug
  positive, and modern AAPCS debug/release default negatives. Linux/libmGBA
  remains the CI runtime.
- **Save/config/data:** no save field, migration, compatibility epoch,
  configuration identity, or Autoconf/Make feature flag. Generated
  `chapterobjectives` and `chapterbundle` data own the emitted objective
  tables; localization remains unchanged.
- **ROM/RAM/archival:** the modern generated objective table and evaluator add
  ROM data/code; the default table is a 12-byte sentinel and authored
  objectives are 28 bytes each. Telemetry remains a 16-byte EWRAM record with
  two transient readiness bytes; linker-budget owner reports capture the
  resulting profile totals. The archival lane excludes objective runtime
  behavior and remains a compile-compatibility check only.

## Typed autoplay strategy profiles

Issue [#90](https://github.com/laqieer/fireemblem8-expansion/issues/90)
adds one bounded generated registry and dispatch seam for reusable autoplay
policy. It is an optional reusable module: the registry/assignment types are
generic, while **Aggressive** and **Objective-first** are the two permanent
default-off references selected by `EXPANSION_AUTOPLAY_STRATEGIES=1`.
The canonical `src/data/autoplay_strategies.json` records their typed
descriptors once. Disabled builds omit only those reference descriptors and
their assignments while retaining the generic strategy router for downstream
custom records; enabled builds emit the references from that same source.

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
unit onto the inclusive rectangle and uses the existing movement selector. A
pending reach or hold accepts combat only when its resulting decision stays in that
rectangle; otherwise it waits, never falling through to unconstrained
Aggressive. The projection is coordinate only (clamp X then Y); combat uses
existing slot/item/map scans. Neither profile consumes RNG or derives a
decision from pointer/link addresses, so a fixed seed/configuration repeats
its trace exactly. Existing low-level fallback behavior retains its own
established RNG contract.

`ExpansionAutoplayStrategies_ValidateRegistry()` rejects capacity overflow,
zero/duplicate IDs, missing callbacks, and undeclared capability bits.
`ExpansionAutoplayStrategies_ValidateObjectiveSupport()` makes an
unknown/unsupported profile-objective pair explicit. Before a selected
strategy can commit a computer-phase action, dispatch checks those contracts.
It records a typed autoplay failure and terminates the strategy path rather
than selecting a success-shaped fallback. The typed event helper
`strategy.activate` lowers a symbolic, schema-validated assignment to
`AUTOPLAY_STRATEGY_ACTIVATE` and
`ExpansionAutoplayStrategies_ActivateAssignment()`. It validates a generated
strategy-ID/activation-flag pair, changes only that existing event flag, and
queues one validated pair during an active blue computer phase without
changing the current units. Computer-phase completion is the next safe
boundary and applies that pair exactly once. One later valid request replaces
the pending pair, duplicates coalesce, and invalid pairs cannot replace it.
Raw `flag.set` cannot target a declared strategy activation flag. The pending
pair is cleared by every map/chapter lifecycle reset, including Suspend
resume, and is never serialized; there is no save byte, epoch, migration,
localization string, player UI, or second event language.

For a third strategy, define one callback with the public context signature,
declare its stable ID/capabilities in `autoplay_strategies.json`, and add its
chapter/group/unit assignment. The generated registry emits its typed callback
reference; the shared dispatcher remains unchanged. The default references
are not a taxonomy: Balanced, EXP, Treasure, Support, campaign, and
project-specific character/chapter policies remain out of scope.

The runtime uses one bounded eight-byte EWRAM pending pair (strategy ID,
activation flag, and owning chapter ID) and no IWRAM. The empty generated
registry and bundle sentinels occupy 40 ROM bytes (20 bytes each). Strategy
selection reconstructs from generated data and existing event flags; only an
in-flight active-phase request uses the transient pair. The strategy host/ARM
selector enforces exactly eight strategy EWRAM bytes and a 4 KiB aggregate
object-text ceiling for both profile states. The archival lane excludes the
runtime and generated table.

### Strategy compatibility and budgets

- **Dependencies:** typed chapter objectives/groups, `CpDecide_Main`, the
  existing AI action helpers, generated-data ownership validation, and the
  tester-case catalog.
- **Configuration:** `EXPANSION_AUTOPLAY_STRATEGIES` selects only the two
  reference descriptors/callbacks. It participates in ROM identity and has no
  save migration or compatibility-epoch impact.
- **Data/UI:** downstream custom strategy records remain available with the
  reference profiles disabled; no player UI, localization string, or second
  event language is introduced.
- **ROM/RAM:** reference profiles add only generated ROM descriptors and
  callback text. The shared router uses one eight-byte EWRAM pending pair and
  no IWRAM; the focused object gate enforces that exact bound and a 4 KiB text
  cap.
- **Runtime evidence:** the debug strategy profile and default-disabled ROMs
  execute bounded fixed-seed `CpDecide_Main` scenarios twice; their action and
  telemetry captures must repeat exactly.

## Autoplay controller and Charge compatibility and budgets

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
make expansion-modern-autoplay-strategy-runtime-check MODERN_CONFIG=debug MODERN_ABI=aapcs
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
