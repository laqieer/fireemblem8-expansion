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

When enabled, the map menu prepends one localized **Charge** row before the
vanilla commands and keeps End final. If Danger is also enabled, Danger is
first and Charge is second. Its label and
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
the asynchronous helpbox. The helpbox measures that exact pointer rather than
the vanilla message scratch buffer, and every production locale authors two
bounded lines. It therefore remains valid through later menu-label resolves
without allocating a second EWRAM scratch buffer.

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

The optional Danger row composes with Charge: the base map menu defines eight
rows, conditionally shows at most seven at once, and both options produce at
most nine visible rows within `MENU_ITEM_MAX == 11`. Optional rows always
precede the vanilla rows in stable Danger-then-Charge order, while End remains
the final visible command. The nine-row non-story combination shifts upward
only by the amount needed to keep the frame and both tiles of End on-screen;
all shorter configurations retain vanilla vertical geometry. A downstream
project adding or replacing another row must make its capacity/order and
scrolling choice explicitly rather than silently displacing either command.

Charge disappearing while Danger remains is intentional, not a capacity
failure. `ExpansionBluePhaseDelegate_GetAvailability()` returns a typed reason
and the menu hides Charge unless all of these facts hold simultaneously: blue
faction; `PLAYER` controller and player-phase telemetry with no failure; the
ordinary map menu owns exactly one game lock; no event, fade, camera move,
blocking player action, computer/berserk phase, or pending delegation exists;
and at least one blue unit passes the shared AI eligibility predicate.
Sleeping, berserk, hidden, unselectable/already-moved, dead, rescued, and empty
slots do not count. Restoring the exact predicate makes Charge reappear
without rebuilding or changing locale.

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
  enables Charge and Danger together and retains 672 EWRAM bytes plus
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
rejected during generation. Within the group scope, every strategy-assigned
group must be disjoint by both character and owned unit-group identity.
Overlaps fail even when both groups select the same strategy or the character
also has a unit override, so JSON assignment order can never route a unit.
Objective-only groups with no strategy assignment retain #89's existing
overlap contract. Active objective selection retains the existing
failure/pending/success priority and authored-record-order tie rule, never a
pointer or link address.

Aggressive calls the existing legal combat selector first, then leaves the
unchanged low-level AI to pursue or fall back. Objective-first handles only
the active `reach_area` or `hold_until_turn` group member: it projects the
unit onto the inclusive rectangle, generates one extended movement map for the
current unit, and scans every unoccupied path-reachable rectangle tile. Target
ranking is deterministic: lowest path cost, then shortest Manhattan distance
from the projection, then lowest Y, then lowest X. Thus the projection wins an
equal-cost tie only when legal; blocked, occupied, or unreachable projection
tiles cannot hide another legal target. The chosen target is always inside the
rectangle, and the existing movement helper must still produce a strict range
reduction before its intermediate move is consumed. If no legal target or
strictly progressive move exists, the callback returns one fully cleared
intentional wait.

A pending reach or hold accepts combat only when its resulting decision stays
in that rectangle; otherwise it waits, never falling through to unconstrained
Aggressive. The rectangle scan uses constant stack state, one current-unit
extended map, and one final selected-target path map; it allocates no candidate
array and consumes no RNG. Neither profile derives a decision from JSON order,
pointer/link addresses, or stale prior-unit maps, so a fixed seed/configuration
repeats its trace exactly. Every consumed wait calls `AiClearDecision()`, so
rejected movement/combat coordinates, action IDs, targets, and item slots
remain zero in runtime probes and telemetry. Existing low-level fallback
behavior retains its own established RNG contract.

`ExpansionAutoplayStrategies_ValidateRegistry()` rejects capacity overflow,
zero/duplicate IDs, missing callbacks, and undeclared capability bits.
`ExpansionAutoplayStrategies_ValidateObjectiveSupport()` makes an
unknown/unsupported profile-objective pair explicit. Before a selected
strategy can commit a computer-phase action, dispatch checks those contracts.
It records a typed autoplay failure and terminates the strategy path rather
than selecting a success-shaped fallback. The typed event helpers
`strategy.activate` and `strategy.deactivate` lower a symbolic,
schema-validated assignment to `AUTOPLAY_STRATEGY_ACTIVATE` /
`AUTOPLAY_STRATEGY_DEACTIVATE` and the matching typed C bridge. They validate
the same generated strategy-ID/activation-flag pair, set or clear only that
existing event flag, and return `ERR_PHASE_ACTIVE` without queueing when
called directly from C during an active blue computer phase. Only the
`EventActivate` / `EventDeactivate` wrappers convert that validated result
into one pending pair plus operation without changing the current units.
Computer-phase completion is the next safe boundary and applies that operation
exactly once.
One later valid request replaces the pending operation, duplicates coalesce,
and invalid pairs cannot replace it. Raw `flag.set` and `flag.clear` cannot
target a declared strategy activation flag. The pending operation is cleared
by every map/chapter lifecycle reset, including Suspend resume, and is never
serialized; there is no save byte, epoch, migration, localization string,
player UI, or second event language.

Event-list helper validation consumes the same profile-selected descriptor and
assignment view that generates `data_autoplay_strategies.c`. A disabled
reference profile therefore cannot authorize `strategy.activate` /
`strategy.deactivate` or reserve its flag, while emitted downstream custom
records continue to validate. Profile flips participate in the event-list
freshness stamp for both file and directory strategy sources.
Authorization also requires the event-list owner's exact
`autoplayStrategies.source` member set and declared chapter symbol; sharing a
chapter ID alone cannot authorize a custom source. Missing, stale, or
cross-source ownership fails before event C generation.

### Downstream strategy example

A callback returns `false` to request the clean existing `Unit.ai[]` fallback.
It must leave no tentative decision behind (the router also clears before the
fallback). Returning `true` consumes the strategy decision. That includes an
intentional wait: clear `gAiDecision`, return `true`, and no low-level fallback
runs.

This movement-only strategy handles a reach objective, consumes a legal move,
and intentionally waits when the movement helper finds no action:

```c
#include "global.h"

#include "cp_common.h"
#include "cp_utility.h"
#include "expansion_autoplay_strategies.h"

bool ExpansionAutoplayStrategy_AdvanceOnly(
    const struct ExpansionAutoplayStrategyContext* context)
{
    const struct ExpansionChapterObjective* objective = context->objective;

    if (objective == NULL
        || objective->kind != EXPANSION_CHAPTER_OBJECTIVE_REACH_AREA)
        return false;

    AiTryMoveTowards(objective->xMin, objective->yMin, 0, 0, 1);
    if (!gAiDecision.actionPerformed)
    {
        AiClearDecision();
        return true;
    }

    if (gAiDecision.actionId != AI_ACTION_NONE)
    {
        AiClearDecision();
        return false;
    }

    return true;
}
```

The matching descriptor and assignment use a group and event flag that the
downstream project defines and owns in the same chapter bundle:

```json
{
  "$schema": "fe8.autoplaystrategies.v1",
  "strategies": [
    {
      "id": "AUTOPLAY_STRATEGY_ADVANCE_ONLY",
      "callback": "ExpansionAutoplayStrategy_AdvanceOnly",
      "objectiveKinds": ["reach_area"],
      "actionKinds": ["objective_move"]
    }
  ],
  "chapters": [
    {
      "chapter": "CHAPTER_L_2",
      "symbol": "AutoplayStrategies_ProjectL2",
      "groupAssignments": [
        {
          "group": "AI_GROUP_PROJECT_ESCORT",
          "strategy": "AUTOPLAY_STRATEGY_ADVANCE_ONLY",
          "activationFlag": "EVFLAG_PROJECT_ESCORT"
        }
      ],
      "unitAssignments": []
    }
  ]
}
```

Declare `AutoplayStrategies_ProjectL2` in the owning bundle's
`autoplayStrategies.symbols`. At runtime, `AI_ACTION_COMBAT` requires the
descriptor's `combat` action capability and `AI_ACTION_NONE` (a movement
decision) requires `objective_move`. A consumed intentional wait has
`actionPerformed == false` and no action ID to classify. Staff, item, talk, or
other action IDs are outside the current strategy action taxonomy and fail
runtime capability validation; extend the typed schema/router contract before
returning one. The default references are not a taxonomy: Balanced, EXP,
Treasure, Support, campaign, and project-specific character/chapter policies
remain out of scope.

The runtime uses one bounded eight-byte EWRAM pending operation (strategy ID,
activation flag, owning chapter ID, and set/clear operation) and no IWRAM. The
empty generated registry and bundle sentinels occupy 40 ROM bytes (20 bytes
each). Strategy selection reconstructs from generated data and existing event
flags; only an in-flight active-phase request uses the transient record. The
strategy host/ARM selector enforces exactly eight strategy EWRAM bytes and a
4 KiB aggregate object-text ceiling for both profile states. The archival
lane excludes the runtime and generated table.

The parsed full-link evidence is
`reports/autoplay_strategy_budget.json`. The owner builds three matched
variants from the current tree: an internal router-absent technical baseline,
the normal router-present build with references disabled, and the
references-enabled build. It derives both deltas from their `__floating_end`
assignments and verifies with parsed ELF symbols that the absent variant omits
the router hook and both generated tables while both present variants contain
them. No source/object/ROM/commit snapshot, hash, branch, or Git identity is an
input. The absent seam is private to this measurement target: it is not an
Autoconf/Make project option, configuration identity, save behavior, shipped
runtime, or alternate fallback. The measured shared-router and reference
increments are reported separately so later unrelated ROM changes cancel out.
Every recursive router-absent and profiles-disabled build explicitly forces
`EXPANSION_AUTOPLAY_STRATEGIES=0`; references-enabled builds force `=1`.
Therefore a persisted `./configure` choice, environment value, or caller
override cannot contaminate the matched variants.
The current matched result is +1,560 debug / +1,896 release ROM bytes for the
profiles-disabled shared router, then another +856 debug / +680 release bytes
for the enabled reference descriptors/callbacks.

### Strategy compatibility and budgets

- **Dependencies:** typed chapter objectives/groups, `CpDecide_Main`, the
  existing AI action helpers, generated-data ownership validation, and the
  tester-case catalog.
- **Configuration:** `EXPANSION_AUTOPLAY_STRATEGIES` selects only the two
  reference descriptors/callbacks. It participates in ROM identity and has no
  save migration or compatibility-epoch impact. Capacity, capability,
  assignment-overlap, active-manifest counts, and event-helper validation all
  consume the exact selected view emitted for that profile; disabled
  references do not consume those contracts, while selected custom records do.
- **Data/UI:** downstream custom strategy records remain available with the
  reference profiles disabled; no player UI, localization string, or second
  event language is introduced.
- **ROM/RAM:** reference profiles add only generated ROM descriptors and
  callback text. The shared router uses one eight-byte EWRAM pending pair and
  no IWRAM; the focused object gate enforces that exact bound and a 4 KiB text
  cap. Parsed full-link evidence records the profiles-disabled shared router
  separately from the incremental enabled references using matched
  current-tree technical variants (+1,560/+1,896 debug/release router bytes,
  then +856/+680 reference bytes).
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
  enables Charge and Danger together and retains 672 EWRAM bytes plus
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
This follows the latest accepted issue #91 architecture-handoff correction,
which supersedes the initial issue body: #88 remains optional integration and
the initial batch collector is frozen to normal-fidelity schema version 2.

The batch CLI requires one exact ROM/ELF, one bounded scenario with
`schema_version` exactly 2 and no `execution_profile`, one versioned
specification, an explicit finite unique seed list, bounded `--max-jobs`, and
explicit frame/turn/action bounds. The values must match the scenario's hard
limits exactly. Each seed starts a new libmGBA core from clean boot, using one
shared backend compiled and validated before any worker starts. A
specification supplies the only permitted seed mechanism: an exact linked
EWRAM/IWRAM binding, its canonical resolved numeric address, and a frame at
which that value is written. The full write range must remain in writable work
RAM and the frame must precede canonical `max_frames`. The report therefore
identifies a real seed injection rather than claiming that an arbitrary label
changed the ROM's RNG. Every seed must fit that declared 1/2/4-byte field
before backend setup. The underlying capture API rejects scheduled writes for
fixed-frame scenarios before plan or backend work; plan format 7 always
contains bounded `RUN_UNTIL` and requires one matching
`SEED_WRITE_APPLIED` record before a terminal result is accepted. The backend
continues accepting format 6 input plans for compatibility.

The version-1 specification declares normal-fidelity profile/configuration
identity and semantic metric descriptors. Metric probes must be part of the
terminal checkpoint, keeping faction/group survivor and casualty counts,
recruitment/village/chest outcomes, and configured EXP/item/resource deltas
grounded in ROM-supplied semantic telemetry. Unsupported metrics, duplicate or
implicit seeds, omitted bounds, unresolved metric probes, missing or duplicate
required metric/delta kinds, version 3/profile scenarios, reuse of a writable
SRAM image, and output collisions fail before execution.
Faction/group, event-outcome, and each delta list contain 1 through 64 sorted
unique definitions. Imported survivor/casualty and delta values must fit their
declared probe widths before aggregate validation. Every imported
faction/event/delta probe retains its canonical resolved address and size and
must match a terminal-checkpoint probe; `run_batch` deduplicates intentional
sharing by numeric `(address, size)`, while a missing or textually
renamed-to-unrelated RAM probe is rejected.

EXP, item, and resource metrics are signed changes. Before serializing the
current format-7 plan, `run_batch` resolves and deduplicates intentional
symbolic/literal metric aliases by numeric `(address, size)`. The backend
rejects duplicate baseline records in the serialized plan, reads each declared
probe once at the seed frame immediately before that frame's input and seed
write (immediately after reset for the canonical frame-0 fixture), then reads
the terminal checkpoint in that same execution. Format-6 input compatibility
does not own or imply collector deduplication.
Reports retain both unsigned width-bounded observations and compute `delta =
terminal - baseline`, permitting gain, consumption, and zero change without a
second divergent emulator run.

The version-2 report contains sorted ROM/configuration/scenario/profile/bound
provenance, canonical normalized scenario and specification definitions with
validated SHA-256 identities, and one sorted run record per seed. Inline
terminal-checkpoint probe expectations participate in that canonical identity;
absence remains absent, while adding or changing an expectation changes the
digest. A terminal success, objective failure, stall, or exhausted frame/turn/action budget is
retained with its terminal counters and declared metrics; an individual seed
execution failure remains a status-1 record containing only seed, status,
stable error text, and ROM provenance because it has no trustworthy
terminal/metric observation. Compiler, libmGBA,
backend-build, or global setup failure returns 2 before seed records exist.
Parallel scheduling cannot affect report order or bytes. The companion
`compare` command deeply validates nested provenance, ROM, terminal, metric,
aggregate, and run shapes, then reports provenance-definition,
added/removed-seed, terminal, and metric deltas without inferring statistical
significance, difficulty, or balance. Imported reports require 1 through 256
unique ascending seeds; provenance limits must exactly match the canonical
scenario's required frame/turn/action bounds and counter probes, and terminal
plus metric values must remain within them. Batch-report counter addresses are
serialized as resolved numeric literals, so symbolic aliases validate against executable
identity. Objective failure must be scenario-declared, engine stall requires a
stall detector and cannot occur before its configured unchanged-frame limit,
`max_frames` occurs only on the final bounded frame, and
turn/action exhaustion must reach its declared threshold. Output is exclusively staged
beside the requested ignored `build/` path, fsynced, and hard-linked to an
absent destination without clobbering; a competing creator is preserved and
failure removes only this invocation's staging/link.
The requested output must be a strict child of `build/`; the build root itself
is rejected whether missing, present, or reached through a symlink. Per-seed
execution errors preserve stable scenario/ROM/error-class context but
canonicalize only random emulator/backend workspace paths.

This host-only layer adds no ROM code, RAM allocation, feature gate,
configuration-identity field, generated game data, localization, save byte,
migration, compatibility epoch, or archival-lane behavior. Removing the batch
script, its bounded backend seed-write record, tests, and documentation rolls
back the feature without save recovery.

## Local external planner bridge

Issue [#92](https://github.com/laqieer/fireemblem8-expansion/issues/92)
provides a default-off, **modern-debug-only** local planner bridge. It builds
on the one blue `COMPUTER` controller from #85, #86's bounded terminal
contract, #89's objective telemetry, #90's decision callback seam, and #91's
provenance/report vocabulary. The bridge is not a new phase router. At the
existing `CpDecide` boundary, a narrow visitor API enumerates every legal
choice in the declared `MOVE_WAIT`, `COMBAT`, `STAFF`, `USE_ITEM`, `PICK`, and
`SUMMON` families from the live movement, terrain, fog, unit, item, and
objective state. Enumeration is row-major, then action-kind/item/target order;
it neither calls perform nor writes decision, unit, map, target-list, or RNG
state. If the complete set exceeds 512 entries, the observation fails with
`RESOURCE_LIMIT` rather than silently truncating. A valid typed commit
reconstructs the chosen ordinal and returns that `AiDecision` to the unchanged
`CpPerform` / `ApplyUnitAction` path.
`MOVE_WAIT` includes exactly one candidate for the active unit's current tile.
An otherwise immobile valid actor can therefore end its turn normally instead
of producing a false empty-set terminal. The accepted stationary action still
enters `CpPerform`, runs ordinary wait-event, trap, status, map, equipment, and
telemetry cleanup, and consumes no RN.

Coordinate-sensitive choices remain distinct candidates. Torch publishes
every in-bounds tile in the acting unit's staff range; Warp publishes every
empty, visible, traversable destination in range of the selected allied unit;
and Unlock publishes every closed door in staff range. The committed
coordinates are revalidated against live state and lowered to
`ActionData.xOther/yOther` before their existing executors run. Hammerne
publishes one candidate per repairable inventory slot on a same-faction unit;
blue-to-green allied targets remain ineligible exactly like the production
Hammerne builder. It revalidates that slot and lowers it to
`ActionData.trapType`. Weapon actions include every in-range Snag and both
damaged-wall cells associated with each obstacle trap. Ballista-capable units
also enumerate every target from each reachable usable ballista with
`BU_ISLOT_AUTO`; its exact weapon/uses bind the opaque identity and lower to
`BU_ISLOT_BALLISTA`. Obstacle actions bind `targetId=0` and coordinates, then
reuse the existing damage/destruction path. Mine and Light Rune enumerate
their exact adjacent production tiles; all four dance rings enumerate eligible
blue adjacent units. Their target tile/unit, coordinates, and selected item
are revalidated and lowered before the unchanged item executor. Rogue Pick candidates
remain one direct no-item action per target. Non-Rogue targets emit every
applicable inventory slot in target-then-slot order: thief Lockpicks, Chest
Keys/bundles for chests, and Door Keys for doors; bridges remain Lockpick-only.
Every item-using candidate binds its actor slot and exact raw item/uses to both
its token and candidate-set identity. Hammerne additionally binds the selected
target slot and raw target item. Changes, replacement, emptying, or slot
swapping reject COMMIT before execution; unrelated unusable slots do not.
Only the selected Pick stack is consumed.
Fortify uses the production ranged-heal predicate and therefore requires an
injured allied non-caster from range 1 through MAG/2. Latona scans only the
current phase's bounded 0x80-slot domain and excludes its caster while
accepting non-casters with missing HP or status. Physic and other ordinary
staff actions continue through their owning range and target predicates.
Normal Summon is a separate `AI_ACTION_SUMMON` / `UNIT_ACTION_SUMMON` route:
the active blue unit must have `CA_SUMMON`, an exact `gSummonConfig` entry, no
available existing configured summon, and at least one legal adjacent tile.
Every empty, visible, traversable tile is a distinct deterministic candidate;
the selected coordinates are revalidated, lowered to
`ActionData.xOther/yOther`, and consumed by the existing map-animation summon
effect. An unavailable prior summon remains reusable without mutating it
during enumeration. Demon King summon remains the distinct, coordinate-free
`AI_ACTION_DKSUMMON` / `UNIT_ACTION_SUMMON_DK` route and retains its existing
red-unit capacity rule; neither action can be lowered through the other.

`EXPANSION_AUTOPLAY_PLANNER=1` is valid only with `MODERN_CONFIG=debug`.
It participates in configuration identity but adds no save field, migration,
epoch, localization, generated chapter data, or archival behavior. Release
and archival builds omit its state and hooks. The separate
`EXPANSION_AUTOPLAY_PLANNER_SCENARIO_ID` build value namespaces the runtime
scenario contract. Enabled modern builds require an integer constant from `0`
through `0xFFFFFFFF` and reject negative, oversized, or invalid expressions
at compilation; archival/inactive builds remain unaffected. The published
identity also binds initialized chapter/map dimensions. The only exported records are
the fixed-width, pointer-free `PlannerObservationV2`, `PlannerCommandV2`, and
`PlannerCampaignCheckpointV2`; the host may read those symbols and may submit
only one typed mailbox command. There is no raw-address, arbitrary-memory,
save, savestate, socket, HTTP, model, or upload API.

The 1,024-byte pre-release v2 observation is a tagged fixed-width page. This
unreleased extension changes v2 in place; there is no deployed v2 peer to
migrate. Page zero contains eight semantic fields. Further pages carry up to
231 row-major map cells, 23 typed 40-byte unit records, 115 typed value
records, or 23 pointer-free action records. Unit records expose availability,
faction/character/class, position/HP, raw state plus explicit deployed, dead,
moved, acted, rescued and rescuing flags, rescue partner, status/duration,
level/EXP, equipped slot/raw item, power/skill/speed/luck/defense/resistance/
constitution/movement, all eight weapon ranks, and the inventory digest.
`INVENTORY` pages expose all five raw slots; `RESOURCES` exposes gold, all 100
convoy slots, and telemetry; `FLAGS` exposes every bounded flag ID and state.
The summary retains map, active-unit, objective, flag, and resource integrity
fields; per-unit inventory digests remain only on unit records.
Its otherwise-unused payload contains a fixed 812-byte campaign record: phase,
chapter/route mode, up to eight complete #89 objectives and eight 16-member
groups, the eight-entry #90 strategy registry, and seventeen chapter/group/
unit assignments with activation/current source and capabilities. Every domain
and nested record carries availability; no pointer crosses the wire.
Flag and convoy/resource availability derives only from the backing pointer
and bounded domain sizes. A valid 32-bit digest of zero remains `AVAILABLE`
and is published unchanged; null storage or an out-of-range flag domain is
`UNINITIALIZED`. Each flag byte domain is capped at 256 bytes for semantic
pages and campaign-checkpoint hashing. Zero bytes is a valid available domain
and emits one explicit zero-record `FLAGS` page; 1- and 256-byte domains are
read exactly, while negative, 257-byte, larger, or null domains are never
dereferenced.

Every semantic field or record has an explicit `AVAILABLE`, `NOT_APPLICABLE`,
`NOT_VISIBLE`, `UNSUPPORTED_RULE`, `OUT_OF_RANGE`, `UNINITIALIZED`,
`UNAVAILABLE`, or `EMPTY` state. `US_UNAVAILABLE` units, including benched
units whose stale coordinates remain in bounds, are always `UNAVAILABLE` and
are excluded before actor or target enumeration.

Each 40-byte action record carries six action-identity words and four opaque
token words. `destination` packs acting X/Y; `target` packs target ID and X/Y;
`itemSlot` packs acting and Hammerne-target slots. The shared transcript/live
validator requires the exact kind-to-engine-action mapping, canonical unit IDs,
coordinates strictly inside the available published width/height, exact
coordinate/slot sentinels, and zero reserved bits before creating an action or
invoking either planner. Each absent slot is exactly `0xFF`, so
Summon, MOVE_WAIT, Rogue Pick, and other no-item actions remain distinct from
slot zero. All four independently mixed token words bind every identity field,
remain opaque to the host, and are echoed unchanged. A malformed page produces
no selection, follow-up command, or transcript mutation.

The observation's overlapping start/count aliases and tagged payload are
declared as named C89 unions (`start`, `count`, and `payload`), not anonymous
members. Public wire offsets remain 36, 40, and 100 while unreleased v2 grows
atomically to 1,024 bytes; inactive headers still compile under archival agbcc.
The archival planner translation unit retains compile-time size and offset
checks even though release and archival builds emit no planner runtime state.

The 64-byte command overlays a 24-byte typed payload after its common 32-byte
header: START uses four expected identity words, while COMMIT uses the four
opaque token words. Result and rejection remain at offsets 56 and 60. The host
obtains all data only by sending typed `PAGE` commands with a fixed
`page_index`; there is no in-process action-list shortcut. Up to 512 actions
use at most 23 action pages. Maximum map, 132 rich units, inventory, resources,
4,096 flags, campaign registries, and actions still use exactly 92 pages.
Before planner selection, transcript mutation, or replay transport creation,
one shared whole-observation validator requires `1 <= page_count <= 92`,
`page_index < page_count`, exact `u32` words, and a projected capture within
64 MiB. It requires one summary followed by contiguous typed spans; canonical
unique field, map, roster, inventory-slot, resource, flag, action-ordinal, and
candidate identities; row-major map dimensions/counts; roster-owned inventory;
map-relative action destinations/targets; and matching page totals. Invalid,
duplicate, missing, cross-page, or reordered records reject without an
unbounded exchange or retained partial observation. PAGE collection snapshots
the transcript and host transport state after page zero, then restores both
exactly if any later exchange, settlement, or whole-observation check fails.
Global ordinals and all four token words returned by ROM are opaque to host
planners and are echoed unchanged. The host never sends coordinates, targets,
item IDs, `ActionData`, unit pointers, or RNG state: a commit carries only
`{run_id, observation_id, ordinal, token[4]}`. Stale pages, unknown commands,
duplicate START, forged tokens, unsupported actions, cancellation, and
resource overflow produce typed rejection and do not execute an action.
The ROM does not retain a candidate array. It retains only compact count,
deadline, run, and trace state, freezes normal AI while waiting, and invokes
the pure enumerator for each action page and commit. Native adversarial tests
digest decision, unit, map, and RNG state before/after enumeration and also
prove candidate uniqueness and repeatable ordering.
Each run is capped at 4,096 accepted commits. Both host planners and the live
transport use one canonical hash-chained transcript capped at 2 MiB. It
records every full semantic observation and candidate/page-set identity, exact
command, acknowledgement, completion or rejection, settled telemetry and RNG,
checkpoint, and terminal state. A production command reserves and
prospectively sizes one bounded 64 KiB exchange before any mailbox write,
sequence increment, or transcript mutation. Canonical export/import validates
the chain and event order. Exactly one provenance session must be event zero;
empty, sessionless, late-session, and duplicate-session transcripts fail even
after an attacker recomputes the chain. Its ROM/configuration identities bind
every observation, and its initial scenario, seed, ready-run, and active-run
identities bind READY, accepted START, and the first active observation before
chapter-local identities may advance. Deep replay requires equivalent
observations, commands, settlements, and terminal state; truncation,
reordering, or field tampering fails. The bounded-search reference accepts at
most 512 nodes and enforces the 64 MiB host-search ceiling.
Production strictness is trusted API context, never transcript data:
`import_production_bytes` and clean live replay select `PRODUCTION`, while
bounded host fixtures must explicitly select `SYNTHETIC`. Restricted libmGBA
transport likewise selects production validation before either planner runs.
Production mode requires nonzero ROM/configuration/scenario/seed provenance
and the complete field, map, campaign, coordinate, page, and record contract
even when a re-chained transcript zeroes its published identities. Validation
mode is not serialized, and an attempted mode field is an unknown schema key.
The original single-argument `import_bytes` call remains positionally
compatible and now defaults safely to production validation; fixtures use the
explicit `import_synthetic_bytes` entrypoint.
Transcript JSON has an explicit maximum structural depth of 64 containers.
An iterative byte preflight runs before decoding and an iterative object
validator runs before canonicalization. Excess depth plus specifically
JSON-decode, Unicode-decode, parser-recursion, and canonicalizer-recursion
failures become stable invalid-transcript errors before state mutation or
transport creation.
Wire-v2 transcript objects also use exact allowed and required key sets at the
envelope, event, provenance/source/ROM/scenario, command/token, observation
and every record, ACK, completion, settlement, RNG, terminal, and transport
error boundary. Unknown extension keys and missing required keys fail before a
clean replay transport is created, even when an attacker recomputes the hash
chain. Every numeric scalar must be an exact Python/JSON integer within its
wire width, enum, coordinate, page, slot, or sentinel domain; booleans,
strings, floats, and out-of-range values reject before transport creation.
`NaN` and positive/negative infinity are rejected during decode and canonical
emission, with no transcript mutation.
The importer is an explicit command state machine: each command must be
followed by its matching ACK, matching COMPLETE, one response page, and that
response's settlement. It rejects responses before completion, duplicate or
interleaved stages, missing settlements, and terminal/settled disagreement.
Every accepted non-START command names the current observation. COMMIT looks
up its action and token only in that exact observation's candidate set; PAGE
binds both the command observation and requested index to the returned page.
Accepted START must leave READY for the declared active run and produce its
first WAITING page or a documented terminal. Accepted COMMIT must produce a
strictly newer WAITING observation in the same run or a documented terminal;
reused observations, COMMITTED responses, and invalid provenance reject
before replay transport creation.
Rejected START/PAGE/COMMIT/CANCEL responses must retain the prior wire page
byte-for-semantic-byte except for their documented rejection and terminal
state; nonterminal checkpoints remain identical and terminal checkpoints zero.
Re-chaining stale, cross-swapped, or rejected-response data cannot bypass this.
Only the four protocol command kinds are transcript-representable. Unknown
numeric or text kinds reject during import before a replay factory starts;
malformed fields on START/PAGE/COMMIT/CANCEL remain representable when the
restricted backend can reproduce their typed rejection.
Before selecting an action, both reference planners validate the complete
typed inventory/resource/flag set, reject any candidate that names an
unavailable actor or target, and retain the semantic digest they consumed.
The digests therefore audit real bounded values received through PAGE rather
than a disconnected Python-only mirror.
The Python bridge and every public validation diagnostic consistently identify
this as protocol v2; invalid chapter and action-cap inputs report the v2 range
or resource boundary rather than the obsolete v1 label.

START carries expected fixed-width ROM, configuration, scenario, and seed
identities. READY/WAITING observations publish the actual runtime identities
derived from immutable build provenance plus ROM header, configuration
fingerprint, scenario namespace plus initialized chapter/map, and all three RN
words plus LCG state. READY is published only after map, fog/weather RNG, and
map-display initialization; a command prepared from an earlier identity
rejects before computer control activation.

Other action families remain unavailable and are never silently lowered to a
raw engine call. A zero-candidate enumeration reports
`EXHAUSTED/CAPABILITY_UNAVAILABLE` before entering `WAITING`; a legal set above
512 reports `EXHAUSTED/RESOURCE_LIMIT`. Both terminal paths atomically clear
the checkpoint, deactivate the planner, and queue player-control restoration
at the next safe phase without running fallback AI. The terminal observation
remains stable and a stale START cannot reactivate it. Ordinary nonterminal
observations remain active. Cancellation is observed only at a decision safe
point; it never interrupts a battle, event, movement, or Proc halfway through.
Once an observation is published,
`CpDecide` moves to a dedicated mailbox-poll state. Every poll advances the
single 300-frame deadline, including valid `PAGE` and native malformed-mailbox
traffic, while
never rerunning AI, consuming RN, or advancing a unit. Accepted commits alone
rejoin the normal perform state.

Replay begins from a fresh emulator and a blank in-memory SRAM image. It
replays the complete chapter-one action prefix through normal game control,
records a 52-byte semantic chapter-two checkpoint
(chapter/route-mode/turn/RN/trace digest), and continues the same live
emulator. Branching replays a clean prefix; it never loads a save fixture or
savestate. `rng.c` remains the authority: the bridge only snapshots its public
RN state and read-only consumption counter. The checkpoint digest includes
`gPlaySt.chapterModeIndex` and the complete bounded 100-slot convoy in addition
to roster, held items, gold, flags, RNG, and accepted-token state. Route-only
and convoy-only changes therefore produce distinct checkpoints. Only the
`MNCH`, `MNC2`, and `MNC3` paths set the typed transition flag. For `MNCH` and
`MNC2`, the production event engine records settled campaign state immediately
before `EndBMapMainForChapterTransition` changes any map state. Because
`MNC3`'s `GotoChapterWithoutSave` changes chapter identity synchronously, that
path records immediately before the call, then the scheduled
`StartBattleMap` reset preserves and re-arms the same run. Ordinary actions do
not rewrite a transition checkpoint. Other map exits, restart, suspend load,
new game, full reset, timeout, resource termination, and CANCEL remain
destructive boundaries and never use a recording path. Timeout and explicit
CANCEL first publish an invalid checkpoint magic value, then zero the entire
52-byte record before deactivating the planner or restoring player control. A
later START also clears the record before activation, so no cancelled-run
checkpoint can become readable again.

`TC-AUTOPLAY-PLANNER-001` proves both a scripted chooser and a bounded search
chooser consume the same page/token contract, reject negative commands, and
produce deterministic two-chapter semantic transcripts. A separately compiled
libmGBA adapter is bound to the exact linked observation, command, and
checkpoint symbols and accepts only the restricted typed/status operations
above over stdin/stdout. Both
Python planners drive the production-linked ROM through that adapter; the ROM
does not self-write commands. The integration covers all semantic/action
pages, opaque-token acceptance and rejection, same-ROM/config/seed scenario
mismatch, non-idle PAGE timeout, cancellation, and same-run chapter-two
checkpoint continuation.
Canonical replay is transport-driven rather than a hard-coded observation
fixture. It imports a recorded transcript, starts a new clean-boot ROM and
libmGBA backend, reissues its START/PAGE/COMMIT/CANCEL or malformed commands at
the recorded semantic boundaries, reconstructs complete observations from the
newly returned pages, and requires the new transcript bytes to match. This
compares every semantic page, ACK/result, completion, telemetry, RNG,
checkpoint, and terminal state without a save, savestate, emulator snapshot,
or raw-memory operation.
The post-startup backend accepts only `READ`, `START`, `PAGE`, `COMMIT`,
`CANCEL`, and `QUIT`. It exposes no frame-step, arbitrary-frame, keypad, or
arbitrary-kind command. The enabled full-ROM test reaches its fixed mailbox
READY boundary through a separately linked test-only bootstrap routine before
stdin is exposed. Sending `STEP`, `RUN`, raw keys, or equivalent unknown input
returns an error without advancing the emulator or changing observation, RNG,
mailbox, checkpoint, or transcript state.
Input is newline framed with at most 511 bytes before the delimiter. An
overlong or NUL-bearing line is drained through its one newline or EOF and
produces exactly one error; no suffix can become a second typed command.
Numeric command words use only unsigned hexadecimal digits, consume the whole
token, and may not exceed `0xFFFFFFFF`; signs, prefixes, whitespace inside a
token, overflow, and trailing junk reject before any mailbox write.
Before any initial OBS or stdin handling, one shared validator requires the
complete 1,024-byte READY record: exact protocol/header/control values,
rejection `NONE`, zero run/observation/count/runtime/payload/reserved words,
and four nonzero identities matching the fixed bootstrap snapshot. The
four-frame synthetic path and optional launcher both use that validator.
Any malformed word terminates with an explicit startup error and no stdout or
stdin handling.

Every typed mailbox command now produces three distinct line-protocol stages:
`ACK command_id kind result rejection` after the ROM consumes the exact
nonzero command kind, `COMPLETE command_id kind response_frames` only after
the requested response condition is true, and then `OBS`. The backend writes
the command kind last, assigns monotonically increasing fixed-width host ACK
IDs, and accepts the acknowledgement only after the ROM clears that kind and
publishes its command result. Repeated commands with the same rejection code
therefore remain distinct without comparing rejection values.
Before emitting ACK, COMPLETE, or OBS, the live backend accepts only
`result=1/rejection=0` or `result=0/rejection=1..10`; zero, unknown,
out-of-range, and `0xFFFFFFFF` rejection values terminate with
`INVALID_COMMAND_ACK`.
Transcript import applies the same invariant before interpreting an ACK:
success is exactly `result=1/rejection=NONE`, while rejection is exactly
`result=0` with one known nonzero rejection. Zero/zero, success plus rejection,
unknown results or rejections, and command-ID/kind mismatches fail. Every
accepted COMMIT then validates its ordinal and all four token words; changing a
pair to look rejected cannot bypass token validation while retaining a
COMMITTED settlement.
Completion timing is also typed protocol data: `response_frames` must be an
exact nonnegative integer, at most 600 for ordinary or rejected commands, and
at most 18,000 only for an accepted COMMIT. Booleans, strings, floats,
negative values, overflow, and kind/bound mismatches reject before replay.

START, PAGE, CANCEL, and rejected commands retain bounded fast-response
handling. An accepted COMMIT instead waits up to 18,000 execution frames for a
genuinely new WAITING observation or a terminal planner state, allowing
movement, camera, battle, trap, and event Procs to finish. The 120-frame
mailbox-acknowledgement bound and 600-frame fast-response bound are separate
from both that execution bound and the ROM's 300-frame/five-second decision
deadline. While WAITING, the restricted backend polls stdin with
`CLOCK_MONOTONIC`, runs the ROM at a fixed 60 Hz cadence without keypad input,
and keeps one absolute five-second deadline per observation. Silence, partial
lines, READ, malformed, or unknown floods cannot reset it; expiry publishes
the ROM-owned timeout terminal and cleared checkpoint. An unacknowledged command emits
`TRANSPORT_ERROR COMMAND_ACK_TIMEOUT`; an acknowledged COMMIT that never
completes emits `TRANSPORT_ERROR ACTION_COMPLETION_TIMEOUT`. Either error
terminates the adapter without emitting or serializing the old COMMITTED page.
Transcript import requires that terminal error to be final and bound to an
active command's exact ID/kind. ACK timeout and invalid ACK occur only before
an ACK; ordinary response timeout requires a matching ACK; action completion
timeout requires an accepted COMMIT ACK. No error may follow COMPLETE,
response, or settlement, and clean fault replay must reproduce the same event.

Host-only configuration coverage runs the generated GNUmakefile normally but
replaces its recursive `$(MAKE)` boundary with a hermetic recorder. The
recorder executes the child Makefile's variable probes with the same arguments
and proves that bare Make selected `all` and release, then failed closed on the
persisted debug-only planner before invoking an ARM compiler. The
toolchain-equipped planner gate separately runs the real out-of-tree
`configure --enable-autoplay-planner` followed by
`make expansion-modern-boot-check MODERN_CONFIG=debug` to compile, link, and
boot the enabled ROM. An explicit release request executes normally and
rejects during configuration validation before compilation.
The Python configuration resolver appends `autoplay_planner` after every
pre-existing positional parameter, preserving the established BGM-policy and
item-cap slots while also supporting keyword use.
The authoritative Make gate invokes the complete `PlannerBridgeTests` class,
then the individually selected toolchain/libmGBA scenarios. Class discovery
therefore includes every future Bridge method rather than a name allowlist.

The authoritative resource gate builds otherwise-identical enabled and
disabled debug profiles, parses both linker reports/maps/ELFs, and compares
their real `__floating_end` plus EWRAM/IWRAM occupancy. The current complete
linked delta is 12,176/12,288 ROM bytes (112 headroom), 1,172/4,096 EWRAM
bytes (2,924 headroom), and zero IWRAM. This naturally includes every planner
hook, including `cp_decide`, targeting/item/menu/action/map/lifecycle/RNG
owners omitted by the old object subtotal. Both linked maps must contain every
representative hook, and the inclusive 12,288-byte comparator rejects the
first byte over the cap. The debug-only planner translation units use `-Os` to
retain the frozen cap without changing behavior or release/archival code.
Disabled release and archival builds omit planner state and the normal-summon
executor hook while retaining their original player/executor paths.
The shared target-query functions are modern-only. `FE8_ARCHIVAL_BUILD`
retains the original inline Snag, heal, Hammerne, and Latona bodies and call
graph; pinned agbcc produces text identical to the pre-refactor archival
object and exports none of the modern query symbols.
