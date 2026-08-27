# Autoplay framework cases

## TC-AUTOPLAY-001: Blue computer-phase smoke

- **Feature / originating issue:** `transient-blue-computer-control` /
  [#85](https://github.com/laqieer/fireemblem8-expansion/issues/85).
- **Supported configuration or artifact:** modern AAPCS debug ROM for the
  COMPUTER positive; modern AAPCS debug and release ROMs for the default
  PLAYER negative.
- **Prerequisites and clean starting state:** repository root, libmGBA, no
  save or savestate. The positive follows the established clean-boot Chapter
  2 debug route, which supplies valid blue, red, and green actors. The
  negative follows the established clean-boot Prologue route.

### Actions

1. Run
   `python3 -m unittest tools.gba-playtest.tests.test_expansion_autoplay -v`.
2. Run
   `make expansion-modern-autoplay-check MODERN_CONFIG=debug MODERN_ABI=aapcs`.
3. Run
   `make expansion-modern-autoplay-check MODERN_CONFIG=release MODERN_ABI=aapcs`.
4. Run
   `make expansion-modern-localization-profile-headroom-check MODERN_CONFIG=debug MODERN_ABI=aapcs`.
5. Run
   `make expansion-modern-localization-profile-headroom-check MODERN_CONFIG=release MODERN_ABI=aapcs`.

The positive scenario presses the documented debug-only `SELECT+START+R`
activation chord after Chapter 2 reaches its ordinary interactive player
phase. The chapter's existing zero-valued blue AI bytes select the symbolic
`AI_A_00`/`AI_B_00` policies. The scenario never selects a player-unit action.
The default scenarios never invoke the activation chord.

### Expected result

The COMPUTER route enters the existing computer-phase Proc, enumerates six
eligible blue actors, commits six supported actions, completes once, and
progresses from the interactive blue turn 1 through blue computer control on
turn 2 into the following green phase. Its semantic telemetry
records 104 actual hostile checks against red actors, 56 actual allied checks
against green actors, no invalid relation, no failure, and six deliberately
suppressed blue-AI suspend writes.

### Negative control

The clean debug and release default routes both remain in the ordinary
interactive player phase. Controller is `PLAYER`; blue computer-phase starts,
completions, and committed actions remain zero.

### Interactions and save compatibility

The feature reuses existing actor AI bytes, faction relations, phase events,
post-action handling, and Proc cleanup. It has no feature dependency or
conflict beyond those engine seams. The controller and telemetry are
transient, no controller state is serialized, automatic per-actor suspend
writes are suppressed only during blue computer control, and no save layout,
preference, migration, or compatibility epoch changes.

### Automation

- `python3 -m unittest tools.gba-playtest.tests.test_expansion_autoplay -v`
  - real public API, validation, bounded counters, action capabilities, and
    fixed ARM ROM/RAM budget.
- `make expansion-modern-autoplay-check MODERN_CONFIG=debug MODERN_ABI=aapcs`
  - `tools/gba-playtest/run_autoplay_checks.py` plus
    `autoplay-computer-modern-debug.json` and
    `autoplay-player-default-modern-debug.json` checked fingerprints.
- `make expansion-modern-autoplay-check MODERN_CONFIG=release MODERN_ABI=aapcs`
  - default PLAYER negative with
    `autoplay-player-default-modern-release.json`.
- `make expansion-modern-localization-profile-headroom-check MODERN_CONFIG=debug MODERN_ABI=aapcs`
  and its `release` counterpart
  - every CJK/all-locale profile retains positive EWRAM and IWRAM headroom;
    the tight all-locale debug profile has 44 EWRAM bytes and 272 IWRAM
    static-growth bytes above the required stack margin.

### Cleanup and limitations

Use `make clean_fast` only if build artifacts must be removed. This case proves
the reusable low-level executor and telemetry, not seize, recruitment,
village, chest, resource, strategy, objective, balance, or campaign behavior.

## TC-AUTOPLAY-CHARGE-001: One-phase Charge delegation

- **Feature / originating issue:** `one-phase-blue-delegation` /
  [#87](https://github.com/laqieer/fireemblem8-expansion/issues/87).
- **Supported configuration or artifact:** modern AAPCS debug or release with
  `EXPANSION_BLUE_PHASE_DELEGATE=1`; the semantic positive uses debug, while
  matching debug/release default-disabled ROMs are the runtime negatives.
  Catalog validation covers every authored locale, including Japanese.
- **Prerequisites and clean starting state:** repository root, libmGBA, no
  save or savestate. For manual execution, use a deterministic map with one
  already-moved blue unit, at least two other eligible blue units, and red and
  green actors. The automated Chapter 2 run supplies all three factions; the
  real-source host driver independently fixes the moved/sleep/berserk/
  hidden/unselectable/dead/rescued exclusion matrix.

### Actions

1. Run
   `python3 -m unittest tools.gba-playtest.tests.test_expansion_blue_phase_delegate -v`.
2. Run
   `make expansion-modern-blue-phase-delegate-check MODERN_CONFIG=debug MODERN_ABI=aapcs`.
3. Run
   `make expansion-modern-blue-phase-delegate-check MODERN_CONFIG=release MODERN_ABI=aapcs`.
4. In an enabled ROM, finish one blue unit's action, move the cursor to an
   empty tile, open the map menu, use the localized **Charge** first row (or
   second row when Danger is also enabled), press `R` once to exercise the
   expansion/vanilla help-ID guard, then select the row and provide no unit
   commands until the next blue phase.

### Expected result

Charge is visible only in the valid interactive blue state. The already-moved
unit and every unavailable/rescued/dead/sleeping/berserk unit are absent from
the delegated actor list. Each remaining actor receives at most one action
from the existing bounded AI list, all committed actions use supported IDs,
red is hostile, green is allied, and the current blue phase completes once.
The next blue phase is interactive; controller and telemetry controller both
read `PLAYER`. The host driver also injects an unsupported blue escape and
proves failure telemetry is preserved while control still restores.
The checked Chapter 2 run records five eligible actors, five committed legal
actions, 76 red-hostile checks, 17 green-allied checks, one start/completion,
zero failures, and `PLAYER` on blue turn 2. `R` on Charge leaves the menu and
controller state intact rather than treating the expansion help ID as a
vanilla message ID.

### Negative control

With `EXPANSION_BLUE_PHASE_DELEGATE=0`, the compiled map-menu table adds no
Charge row, the module exports no delegate symbol, and combining neither/one/both
Charge and Danger gives exactly 8/9/10 static definitions within the 11-row
capacity. Status is hidden whenever the dungeon-only Records row can appear,
so those configurations expose at most 7/8/9 live rows even when Guide and
Retreat coexist. Optional rows precede the vanilla rows in stable
Danger-then-Charge order and End remains final. Clean debug and release
Prologue runs remain idle in `PLAYER`, with zero blue computer starts,
completions, or actions.
Invalid `-1`, `2`, or textual config values and an enabled non-modern C
profile fail before producing a ROM.

### Interactions and save compatibility

The only code dependency is issue #85. Danger is explicitly supported
at the same time; there are no other known feature conflicts or required
dependents. A downstream map-menu replacement must resolve row order/capacity
explicitly. Charge adds no save field, preference, migration, or compatibility
epoch; reset/suspend-resume returns through issue #85's `PLAYER` reset.

### Automation

- `tools/gba-playtest/tests/test_expansion_blue_phase_delegate.py` and its
  real C driver cover configuration identity, dependency failures, exact menu
  capacity, every localized label/help entry, shared AI eligibility, valid and
  invalid map states, current-phase routing, success/failure restoration, no
  static RAM, and disabled symbol absence.
- `tools/gba-playtest/run_blue_phase_delegate_checks.py` drives the real
  Chapter 2 map menu and checked
  `autoplay-charge-modern-debug.json` fingerprint, then reuses the issue #85
  debug/release `PLAYER` fingerprints as disabled runtime controls.

### Cleanup and limitations

Use `make clean_fast` only to remove build outputs. The command controls one
phase only. It adds no `NOBODY`, persistent ownership, strategy selector,
authored objective, campaign mode, fast-forward behavior, or claim that the
existing low-level AI can complete arbitrary chapters.

## TC-AUTOPLAY-BOUNDS-001: Bounded semantic autoplay termination

- **Feature / originating issue:** `bounded-semantic-run-until` /
  [#86](https://github.com/laqieer/fireemblem8-expansion/issues/86).
- **Supported configuration or artifact:** generated homebrew fixture with
  libmGBA for all terminal classes; modern AAPCS debug ROM for the real
  COMPUTER success; modern AAPCS debug and release ROMs for default PLAYER
  negatives.
- **Prerequisites and clean starting state:** repository root, Python 3.10+,
  host C compiler, libmGBA development files, and the modern ARM toolchain for
  the real ROM checks. Start without a committed ROM, save, or savestate. The
  runtime scripts use clean boot routes and repository-local ignored output.

### Actions

1. Run
   `python3 -m unittest tools.gba-playtest.tests.test_run_until -v`.
2. Run
   `make expansion-modern-autoplay-bounds-check MODERN_CONFIG=debug MODERN_ABI=aapcs`.
3. Run
   `make expansion-modern-autoplay-bounds-check MODERN_CONFIG=release MODERN_ABI=aapcs`.
4. Inspect the reported reason, frame, turn, and action count for each runtime
   scenario. Do not refresh a checked fingerprint to hide a mismatch.

The generated fixture runs success, objective failure, explicit controller
exhaustion, frozen expected-work progress, and each hard budget. Its success
input becomes observable at frame 2. The real debug route uses the same
Chapter 2 `SELECT+START+R` activation from `TC-AUTOPLAY-001`; no player-unit
action is selected.

### Expected result

The generated fixture emits each reason exactly once:
`success`, `objective_failure`, `controller_exhausted`, `engine_stall`,
`max_frames`, `max_turns`, and `max_actions`. Success stops on its first
matching frame, the frozen monotonic epoch stops as `engine_stall`, and every
fingerprint contains exactly one checkpoint whose frame equals the typed
terminal frame. Bound turn/action values use their symbolic probe identities.

The real debug COMPUTER route stops at its first semantic completion:
`success`, frame 17134, turn 2, six actions, one blue start/completion, 104
red-hostile checks, 56 green-allied checks, and no invalid or failure record.
This is earlier than the parent fixed checkpoint at frame 18000.

### Negative controls

Malformed or unbounded profiles, unsupported operators/reasons, duplicate or
overlapping terminal definitions, impossible success/counter combinations,
unresolved/aliasing symbols, and a regressing progress epoch fail
deterministically. Objective failure is selected before stall classification,
and a retry allowance still executes a semantic failure only once.

The clean debug and release default routes never activate COMPUTER. Both stop
at `max_frames` on frame 3950 with turn 1 and zero blue starts, completions,
actions, debug activations, invalid records, or failures. Existing schema-v1
fixed scenarios and format-v2 fingerprints continue to parse, capture, and
verify unchanged.

### Interactions and save compatibility

The real positive depends on #85's transient control and pointer-free
telemetry. The generic runner depends only on existing ELF probe binding and
libmGBA. There are no fixed-frame conflicts. Authored objectives and
accelerated/strategy layers may consume the terminal contract but are not
defined here.

The harness reads semantic probes only. It adds no target code, save field,
preference, migration, compatibility epoch, generated game data,
localization, configuration identity, ROM allocation, or RAM allocation. The
underlying #85 controller remains transient and reset to PLAYER by map
lifecycle boundaries.

### Automation

- `python3 -m unittest tools.gba-playtest.tests.test_run_until -v`
  - strict schema/fingerprint diagnostics, plan-version compatibility,
    generated libmGBA coverage for all seven reasons, first-frame capture,
    monotonic stall semantics, and non-retry behavior.
- `make expansion-modern-autoplay-bounds-check MODERN_CONFIG=debug MODERN_ABI=aapcs`
  - checked real COMPUTER success and checked default PLAYER `max_frames`
    negative.
- `make expansion-modern-autoplay-bounds-check MODERN_CONFIG=release MODERN_ABI=aapcs`
  - checked release default PLAYER `max_frames` negative.
- Existing host, boot, budget, save-format, localization/catalog, generated
  data, and legacy gates prove the unchanged compatibility surfaces.

### Cleanup and limitations

Use `make clean_fast` only if ignored build artifacts must be removed. The
runner classifies a ROM-supplied semantic state; it does not define chapter
objectives, choose AI actions, accelerate engine logic, score strategies,
claim balance, or prove arbitrary chapter completion. Explicit
`controller_exhausted` telemetry must come from the controller/objective
contract being tested and is not inferred from inactivity.

## TC-AUTOPLAY-OBJECTIVE-001: Typed authored objective lifecycle

- **Feature / originating issue:** `typed-chapter-autoplay-objectives` /
  [#89](https://github.com/laqieer/fireemblem8-expansion/issues/89).
- **Supported configuration or artifact:** default generated-data source and
  an isolated generated Chapter 2 fixture profile; modern AAPCS debug ROMs
  for the unchanged-chapter bounded negative and the authored Suspend/Resume
  positive.
- **Prerequisites and clean starting state:** repository root, Python 3, host
  C compiler, modern ARM toolchain, and libmGBA for the runtime negative. Do
  not reuse a save or savestate. The fixture output is build-local.

### Actions

1. Run
   `python3 -m unittest scripts.generated_data.tests.test_chapterobjectives_schema scripts.generated_data.tests.test_chapterbundle_schema scripts.generated_data.tests.test_cli_new_tables.CliChapterObjectivesTests -v`.
2. Run `python3 -m unittest tools.gba-playtest.tests.test_chapter_objectives -v`.
3. Run
   `make expansion-modern-chapter-objectives-check MODERN_CONFIG=debug MODERN_ABI=aapcs`.

### Expected result

The generated fixture resolves every initial generic kind: a known event
objective transitions pending -> success with progress 0 -> 1 after its
existing event flag is set; a real Chapter 2 group moves from reach progress
0 -> 1; defeating its protected unit produces a protect failure and a
defeat-group success. Leaving a hold rectangle latches its declared existing
failure flag, so re-entering before its deadline remains failure. The
evaluator publishes those known states and stable IDs through a test-only
profile probe. Resetting transient telemetry and refreshing it reproduces the
same live state, proving no objective lifecycle state is hidden outside
chapter units, flags, and turn state.

The bounded debug ROM negative follows #86's default PLAYER route. Its one
terminal checkpoint is `max_frames` and all four objective telemetry words
are zero: no existing chapter was opted into an objective/group bundle. The
isolated fixture profile links the same valid generated records used by the
host evaluator and replays the ordinary Chapter 2 Map Menu Suspend,
soft-reset, and Resume path. It observes a non-inactive authored objective
before the real `WriteSuspendSave`, then requires all four recomputed
telemetry words to match after the real `ReadSuspendSave`; no objective state
is serialized.

### Negative controls

Empty or oversized groups, duplicate IDs, unknown/mismatched
chapter-unit/character references (including a unit group owned by another
chapter), invalid rectangles or turns, missing or stale dependency
declarations, contradictory flags, protect cycles, and an unreachable
chapter-bundle symbol fail with source locations and JSON breadcrumbs.
Kind-specific unused fields, invalid non-ASCII stable IDs, and a protect
objective that completes by defeating its protected unit also fail closed.
The host reset/reconstruction assertion and the real fixture
Suspend/Resume route both clear only transient telemetry, then derive the
same state without serializing an objective field; a hold's already-set
existing failure flag remains the explicit persistent failure state.

### Interactions and save compatibility

The table depends on #85/#86 telemetry only as an ELF-probe consumer and
uses existing generated-data, chapter-bundle, `UnitDefinition`, and event
flag/helper seams. It conflicts with a second manifest, router, event
language, strategy policy, or save field; none is present. Existing chapters
without records stay inactive. No save bytes, preference, migration,
compatibility epoch, localization ID, or configuration identity changes.

### Cleanup and limitations

The host test removes its repository-local temporary fixture. Use
`make clean_fast` only to remove ignored build artifacts. This case does not
prove strategy quality, balance, a project route, recruitment/village/chest
policy, player-facing text, or a general expression language.

## TC-AUTOPLAY-STRATEGY-001: Deterministic strategy assignment and precedence

- **Feature / originating issue:** `typed-autoplay-strategy-profiles` /
  [#90](https://github.com/laqieer/fireemblem8-expansion/issues/90).
- **Supported configuration or artifact:** modern AAPCS debug and release
  artifacts with `EXPANSION_AUTOPLAY_STRATEGIES=0` and `=1`; the generated
  host fixture supplies the two reference profiles and one reach-area group.
- **Prerequisites and clean state:** Python 3, host C compiler, modern ARM
  toolchain, no save or savestate. The fixture starts one unit outside an
  inclusive objective rectangle with a fixed zero-RNG test double.

### Actions

1. Run
   `python3 -m unittest scripts.generated_data.tests.test_autoplaystrategies_schema scripts.generated_data.tests.test_chapterbundle_schema -v`.
2. Run
   `python3 -m unittest tools.gba-playtest.tests.test_autoplay_strategies -v`.
3. Run
   `python3 -m unittest tools.gba-playtest.tests.test_autoplay_strategies.AutoplayStrategiesRuntimeTests.test_arm_profiles_bound_pending_ewram_and_gate_reference_callbacks -v`
   to parse the ARM symbol set for both enabled and disabled profiles.
4. Run
   `make expansion-modern-autoplay-strategy-runtime-check MODERN_CONFIG=debug MODERN_ABI=aapcs`.
5. Run `make expansion-modern-autoplay-strategy-budget` to rebuild the
   current-tree router-absent/current-disabled/current-enabled full-link
   comparison and regenerate `reports/autoplay_strategy_budget.json` using
   only matched current-tree artifacts and no Git input. Repeat with caller
   `EXPANSION_AUTOPLAY_STRATEGIES=1`; absent/disabled sub-builds remain forced
   to `0`, enabled sub-builds remain `1`, and the report is identical.

### Expected result

Aggressive chooses the immediate legal combat decision. Objective-first accepts
a deterministic reachable movement decision only when its generated range
strictly decreases. It scans the whole authored rectangle, skips blocked,
occupied, and unreachable targets, then ranks candidates by path cost,
projection distance, Y, and X. A blocked projection advances through an
alternate legal tile; equal candidates are stable; all blocked/no-progress
cases produce a fully clean wait; consecutive units rebuild their maps; reach
and hold share the selector; and a completed objective returns to
Aggressive/current AI. A unit assignment overrides the group and chapter
assignments, a group overrides chapter, and the profiles repeat the same action
trace without an RNG draw. The typed event helpers accept only their declared
activation flag for both activation and deactivation. An active-blue-phase
request leaves current units unchanged, then sets or clears once at
computer-phase completion; duplicates coalesce, the last distinct valid
operation replaces the pending operation, invalid pairs cannot replace it,
and lifecycle/Suspend-resume reset discards it. Unknown IDs,
duplicate/missing callbacks, invalid capability bits, capacity overflow, and
Objective-first with an unsupported objective kind fail explicitly before an
action commits. Enabled reference helpers lower to generated event C; the same
helpers fail validation when references are disabled because their assignments
are absent from emitted strategy C. Strategy-assigned groups must be disjoint
by character and owned unit group; overlap fails identically under reversed
assignment order, equal strategies, and unit overrides, while unassigned
objective-only group overlap remains valid.
Event helpers additionally require the owning bundle's exact strategy source
members and symbol declarations; a same-chapter custom source with canonical
ownership fails. Disabled built-ins consume neither registry capacity nor
capability/overlap checks, while selected custom records retain diagnostics
and match the generated/ACTIVE counts.

The structured budget result reports the profiles-disabled shared router
separately from the enabled references and carries parsed symbol evidence that
the internal absent ELF omits the router hook/tables while both present ELFs
contain them. The matched current-tree deltas are +1,560 debug / +1,896
release bytes for the shared router and +856 debug / +680 release bytes for
the references.

### Negative control

With profiles disabled and the reference descriptors/assignments omitted,
`ExpansionAutoplayStrategies_TryDecide()` returns fallback and creates no
action; the original `Unit.ai[]` decision path remains authoritative. The
ARM selector confirms reference callback symbols are absent from the disabled
object set and both profile states allocate exactly the same bounded
eight-byte pending pair. Disabled reference flags are not reserved against raw
flag helpers, while emitted downstream custom assignments remain typed and
reserved.

### Interactions and save compatibility

The case depends on the #85 blue computer executor and #89 typed
objective/group records. It has no known feature conflicts, no player UI,
locale text, save field, migration, compatibility-epoch change, or archival
behavior. Generated activation flags are existing event state. The pending
operation is transient only: Suspend/load invokes the ordinary autoplay
lifecycle reset, discards any in-flight set/clear request, and reconstructs
assignment selection from generated data and event flags.

### Automation

- `test_autoplaystrategies_schema` validates the typed generated registry,
  assignments, frozen reference capability contracts, and malformed
  negatives.
- `test_chapterbundle_schema` proves the authored bundle surface remains
  coherent with its declared dependencies.
- `test_autoplay_strategies` executes the real callback/dispatch API for
  chapter/group/unit precedence, event-boundary, unsupported-profile, disabled
  fallback, and ARM ROM/RAM assertions.
- `expansion-modern-autoplay-strategy-runtime-check` executes repeated enabled
  and disabled bounded `CpDecide_Main` profiles, asserting strategy/objective
  selection, action telemetry, and the disabled fallback.

## TC-AUTOPLAY-BATCH-001: Deterministic finite autoplay batch report

- **Feature / originating issue:** `deterministic-autoplay-batch-reports` /
  [#91](https://github.com/laqieer/fireemblem8-expansion/issues/91).
- **Supported configuration or artifact:** normal-fidelity generated homebrew
  libmGBA fixture with three explicit seeds. Accelerated fidelity (#88) is not
  required and is rejected by this initial collector. The latest accepted
  issue #91 architecture-handoff correction supersedes the initial issue body
  and freezes this contract to normal-fidelity schema version 2.
- **Prerequisites and clean state:** Python 3, a host C compiler, libmGBA
  development files, one exact ROM/ELF/scenario/specification, and a new
  output path below ignored `build/`. Do not provide a save or savestate.

### Actions

1. Run
   `python3 -m unittest tools.gba-playtest.tests.test_autoplay_batch.AutoplayBatchHostTests -v`.
2. Run
   `python3 -m unittest tools.gba-playtest.tests.test_autoplay_batch.AutoplayBatchLibmGBAIntegrationTests.test_three_seed_clean_boot_fixture_is_serial_parallel_identical -v`.

### Expected result

The normal-fidelity fixture runs three declared seed writes from independent
clean boots. Serial and three-job parallel reports are byte-identical,
versioned, sorted JSON. Every seed has exact ROM/configuration/scenario/profile
provenance, including validated canonical scenario/specification SHA-256
identities. Inline checkpoint expectations round-trip into that identity:
absence remains absent, while adding or changing a value changes the digest.
Successful and terminal-failure runs contain terminal and
configured terminal/frame/turn/action, faction/group, event, EXP, item, and
resource metric records. An execution-failure record instead contains only
seed, status, stable error text, and ROM provenance. A comparison
distinguishes changed scenario/specification semantics even when their
names/versions match, and reports a deliberately changed metric without
claiming either result is balanced or statistically significant.
EXP/item/resource records contain unsigned baseline and terminal observations
from one clean execution plus their signed difference. The baseline is read at
the declared seed frame immediately before its input and seed write
(immediately after reset for this frame-0 fixture), covering gain, consumption,
and zero change without a second run. The collector resolves and deduplicates
symbolic/literal aliases with one numeric `(address, size)` into one baseline
observation; duplicate baseline entries passed directly to the capture API
fail before backend work.

### Negative controls

Duplicate or implicit seed lists, a missing/non-positive hard bound,
unsupported metric, schema version 3 or any execution profile, existing or
concurrently reserved output path, and `--sram-image` all fail before backend
or capture startup. Malformed nested report values fail comparison with status
2 and an exact path instead of a traceback. This includes missing/duplicate
required metric kinds, seeds outside their declared probe width, zero or more
than 256 imported runs, provenance bounds that differ from the canonical
scenario, unresolved/non-writable seed ranges or late seed frames,
self-consistent terminal/metric values beyond those bounds, width-backed
metrics beyond their 1/2/4-byte probes, and empty/duplicate/unsorted/over-64
faction, event, or delta lists. Imported metric definitions whose canonical
address/size is absent from the terminal checkpoint also fail even when their
digest and aggregates were updated consistently. Batch-report symbol-backed
counters normalize to resolved numeric literals; undeclared objective failure,
unconfigured or pre-threshold stall, early `max_frames`, and below-threshold
`max_turns`/`max_actions` are rejected.
Shared-backend/global setup failure returns 2 with no output or seed records;
an individual seed failure is retained as `execution_failure` and returns 1.
Random emulator/backend workspace paths in those errors normalize to a stable
placeholder while stable scenario, requested ROM basename, and error class
remain, so serial and parallel reports stay byte-identical.
A non-success terminal is retained as `terminal_failure`, contributes to the
failure count, and returns 1. A destination created during publication is
never overwritten; failed staging is removed for retry, while comparison
leaves both input report bytes untouched.
The `build/` root itself is rejected before creation/reservation when absent,
present, or reached through a symlink. A dangling requested-output symlink is
also an existing collision: its absent target remains absent, the symlink is
unchanged, no staging file remains, and a distinct corrected child succeeds.

The underlying plan negative also passes a scheduled write to a fixed-frame
scenario and requires an actionable rejection before plan serialization or
backend startup; the matching bounded scenario emits format 7 with
`RUN_UNTIL`, `SEED_WRITE`, and exactly one matching
`SEED_WRITE_APPLIED` acknowledgement. Missing, duplicate, mismatched,
pre-baseline, or post-terminal acknowledgements fail, including an early
terminal before the requested seed frame. A no-baseline scheduled write
remains valid.

### Interactions and save compatibility

The collector depends on #86's bounded terminal/checkpoint semantics and #90's
profile identity. Per the latest accepted issue #91 architecture-handoff
correction, which supersedes the initial issue body, #88 accelerated fidelity
is optional integration only, not a parent. The seed write is restricted to explicitly declared writable
EWRAM/IWRAM at one declared frame; no save is loaded or retained. There are no
ROM feature flags, target allocation, generated data, localization, save
layout, migration, compatibility epoch, or archival impact.

### Cleanup and limitations

The fixture removes its random per-test child beneath
`build/test-artifacts/autoplay-batch`; that ignored parent directory may
remain. Use `make clean_fast` for other build outputs. Three seeds prove report
determinism, complete per-run visibility, and comparison structure only; they
do not establish statistical power, difficulty, campaign quality, or balance.

## TC-AUTOPLAY-PLANNER-001: Local external planner step and replay contract

- **Feature / originating issue:** `local-external-autoplay-planner` /
  [#92](https://github.com/laqieer/fireemblem8-expansion/issues/92).
- **Supported configuration or artifact:** modern AAPCS debug with
  `EXPANSION_AUTOPLAY_PLANNER=1`; local production-linked ROM plus a
  fixed-symbol stdin/stdout libmGBA transport adapter.
  Release and archival builds are unsupported and omit the bridge.
- **Prerequisites and clean state:** Python 3, host C compiler, ARM toolchain,
  libmGBA development files, exact debug ROM/config provenance, and a fresh
  in-memory blank SRAM. No save fixture, savestate, network, or external
  service is permitted.

### Actions

1. Run
   `python3 -m unittest tools.gba-playtest.tests.test_autoplay_planner.PlannerBridgeTests -v`.
2. Run
   `python3 -m unittest tools.gba-playtest.tests.test_autoplay_planner.PlannerLibmGBAIntegrationTests -v`.
3. Run `make expansion-modern-autoplay-planner-check`.
4. In an isolated build directory, run `./configure
   --enable-autoplay-planner` and then bare `make`; verify it retains the
   release `all` target and fails closed before compilation with the explicit
   debug-target instruction. Then run
   `make expansion-modern-boot-check MODERN_CONFIG=debug` and verify the
   enabled debug ROM compiles, links, and boots. An explicit release request
   must also fail before compilation.
5. Start each Python planner through the typed adapter, read the summary plus
   every map, unit, inventory, resource, flag, and action page using `PAGE`,
   and commit only the four opaque token words returned by the chosen action.
   Export and import the canonical transcript, continue in the same emulator
   through the chapter-one transition, and inspect the chapter-two checkpoint.

### Expected result

The scripted reference chooser and bounded search chooser consume the same
pointer-free observation/action records and produce a deterministic
two-chapter transcript. The ROM's pure visitor enumerates every legal choice
in the declared six action families from current movement, terrain,
visibility, unit, item, objective, and resource state without mutating
decision, unit, map, or RNG state. Candidate records are unique and repeat in
canonical row-major then action/item/target order. The 996-byte page carries
either eight typed summary fields, 224 map cells, 56 units, 112
inventory/resource/flag values, or 22 40-byte actions; typed `PAGE` traversal
reaches all 512 candidate ordinals without an in-process list shortcut.

The summary and data pages expose actual map dimensions/terrain/occupancy,
visible unit identity/position/HP/state/inventory, objective state/progress,
every valid unit's five inventory slots, every bounded event flag, gold, all
100 convoy slots, telemetry, and RNG data with explicit availability on every
semantic field or record. `US_UNAVAILABLE` actors and targets remain
unavailable even with stale in-bounds coordinates. Empty and present items
retain their item IDs, uses, and availability. Both host planners echo the
ROM's four independently mixed token words unchanged, validate and retain a
digest of the complete typed semantic values before choosing, commit through
the #85 computer-action route, and preserve a
52-byte checkpoint through `MNCH`/`MNC2` into chapter two. The checkpoint
is recorded by the production event engine immediately before preserving map
teardown, exactly matches the settled prior chapter/run/turn/RNG state, records
chapter route/mode, and changes digest for route-only and convoy-only
mutations. It survives next-map re-arming and is not rewritten by an ordinary
chapter-two action. Torch, Warp, and Unlock enumerate multiple bounded
coordinate targets and execute the selected coordinate rather than stale
defaults. Hammerne enumerates and token-binds each repairable target inventory
slot on a same-faction unit and repairs only the selected slot; green allies
and enemies reject. Combat includes every legal Snag obstacle after ordinary
unit targets, binds `targetId=0` plus coordinates, and executes real obstacle
damage and destruction. Fortify requires an injured allied non-caster within
MAG/2, while Latona excludes its caster and retains every eligible non-caster
in the current phase domain. Rogue Pick opens chest/door targets without an
item; a non-Rogue key path binds and consumes the applicable
Lockpick, Chest Key, or Door Key; a thief without one publishes no such
candidate. The enabled full expansion ROM follows the established clean-boot
Prologue route, accepts a host-selected nontrivial action, reaches the next
planner observation with the actor at the committed destination, then cancels
safely. Explicit exit, restart, load, new-game, full-reset, and cancel paths
clear the run/checkpoint.
Valid zero-valued flag and convoy/resource digests remain available and
round-trip exactly; null backing domains and out-of-range flag sizes are
uninitialized instead.
Normal Summon enumerates each legal adjacent tile only when the real
`gSummonConfig`, existing-summon, movement-state, terrain, occupancy, and fog
contracts allow it. The executor preserves the chosen coordinates through
`UNIT_ACTION_SUMMON`, and the real summon effect creates/replaces the summon at
two tested destinations. Demon King summon remains a distinct coordinate-free
action with its existing population cap. Timeout and explicit cancel
invalidate and zero the complete checkpoint before control restoration; a
later START exposes no prior chapter, run, or digest.
The named-union C89 layout preserves the 996-byte observation, with start,
count, and payload at byte offsets 36, 40, and 100; command and checkpoint
remain 64 and 52 bytes. The host-only configure test actually executes bare
generated Make through a recursive-Make recorder, observes the release `all`
goal, and proves the persisted planner fails closed without an ARM compiler.
The toolchain gate separately executes the real explicit debug-target
compile/link/boot path.
Every wire page has a count from 1 through 92 and an in-range index. Both
planners traverse exactly one bounded, ordered summary/map/unit/inventory/
resource/flag/action sequence with contiguous record spans under the 64 MiB
host ceiling.
Each typed transport command first returns a matching monotonic `ACK`, then a
matching `COMPLETE`, then its observation. Both planners execute a synthetic
180-frame movement/camera/battle/event-style COMMIT and receive only the new
chapter-two WAITING observation, never the prior COMMITTED page. Ordinary
START/PAGE/CANCEL and rejected commands retain bounded fast completions.
The transcript begins with exactly one provenance session and binds its
ROM/configuration/scenario/seed plus ready/active run identities to subsequent
observations and accepted START. Every imported ACK must be exactly success
with no rejection or failure with one known nonzero rejection before an
accepted COMMIT token is interpreted.
The live backend applies the same exact `result/rejection` enum check before
emitting ACK, COMPLETE, or OBS.
Each command must then have one matching ACK, one matching COMPLETE, one exact
response page, and one settlement. Accepted PAGE commands bind the current
observation and requested index to that response; accepted COMMIT commands
resolve their action/token only from the named current observation.
The recorded accepted two-chapter run and rejection/cancel run are each
reissued through a newly booted production ROM/backend, and their complete
transcripts must match byte-for-byte.
Unknown numeric or text command kinds fail import before the replay factory is
called. Stale PAGE and forged COMMIT fields remain valid-kind rejection
records and replay exactly through the restricted backend.
Transcript JSON is iteratively bounded to 64 structural containers before
decode and canonicalization. Below-limit and exact-limit arrays/objects are
accepted by that boundary; excess depth and explicit parser/canonicalizer
recursion become typed invalid-transcript errors before transport creation.
After its fixed test-only pre-stdin boot routine, the enabled production
backend accepts only READ/status, START, PAGE, COMMIT, CANCEL, and QUIT.
Both immediate synthetic startup and delayed fixed bootstrap must publish the
exact READY control record before initial OBS. Delayed startup without
bootstrap, never-ready timeout, WAITING, and EXHAUSTED startup fail with no
stdout or stdin handling. Completion frame counts are exact integers bounded
to 600, except accepted COMMIT may use up to 18,000.

### Negative control

Stale observation IDs, unknown ordinals, forged tokens, unavailable
capabilities, malformed mailbox headers, cancellation, provenance mismatch,
same-ROM/config/seed requests for another scenario, duplicate START,
unexpected command kinds, empty enumerations, page overflow, and resource
overflow fail with explicit typed outcomes and no action commit. Repeated
valid PAGE or native malformed-mailbox traffic cannot postpone the 300-frame
deadline.
Zero-candidate and over-512 enumerations publish typed EXHAUSTED, clear the
checkpoint, deactivate, queue safe player restoration, execute no fallback,
and reject stale re-entry; a nonterminal legal set remains active.
Prospective host observations exceeding 2 MiB fail without changing the trace,
observation, or next ID. The transport accepts no address-bearing command and
has no arbitrary-memory API. Release configuration rejects
`EXPANSION_AUTOPLAY_PLANNER=1`; no bridge state is present when disabled.
The canonical production transcript rejects truncation, event reordering,
hash or semantic tampering, stale candidate/page identities, and mismatched
ACK/COMPLETE settlement. Failed prospective 64 KiB exchanges do not write the
mailbox or mutate transcript state. No-item actions encode both optional
inventory slots as `0xFF`; slot zero round-trips distinctly, while every other
invalid sentinel rejects. Forging any one of the four opaque token words
rejects without execution.
Page counts of zero, 93, one billion, or `0xFFFFFFFF`, negative/overflow words,
out-of-range or duplicate indices, missing spans, and reordered or incomplete
typed-page sequences fail before unbounded traversal or retention.
Empty/sessionless, late-session, duplicate-session, moved-provenance, invalid
ACK result/rejection pairs, unknown results or rejections, mismatched command
IDs/kinds, and a rejected-pair rewrite that retains a COMMITTED observation
all fail after recomputing the hash chain. Destructive exit/load/new-game,
timeout, and cancel paths never record a transition checkpoint; cancellation
after a real MNCH/MNC2 checkpoint zeros the entire record.
Recomputed-chain stale/prior/future observation IDs, PAGE index cross-swaps,
responses before completion, COMPLETE before ACK, duplicate ACK/COMPLETE,
interleaved commands, missing responses/settlements, and terminal disagreement
also fail before replay. A tampered transcript never starts the clean replay
factory.
Over-depth arrays/objects, parser or canonicalizer recursion, and re-chained
deep inputs fail without state mutation. Client `STEP`, `RUN`, raw-key, and
arbitrary-kind commands return an error and leave observation, RNG, mailbox,
checkpoint, and transcript byte-identical; such commands cannot be encoded in
a valid transcript.
Unknown numeric command kinds likewise fail import before transport creation.
Negative, oversized, boolean, string, float, or wrong-kind completion timings
fail before replay.
Out-of-range/occupied Warp destinations, opened or wrong Unlock tiles, stale
Torch coordinates, consumed keys, non-repairable or wrong Hammerne slots, and
a token copied from another Hammerne slot all reject without applying the
selected action.
Green-allied Hammerne targets, caster-only Fortify/Latona state, out-of-range
Fortify allies, and stale, destroyed, non-Snag, or out-of-range obstacle
coordinates reject. Unknown live ACK rejections are rejected before any ACK
or observation is emitted.
Null or over-bound flag storage and null convoy storage are unavailable, while
an available digest equal to zero must never be mistaken for absence.
The archival target object exposes no modern query predicates and preserves
the original Snag/heal/Hammerne/Latona call graph and pinned agbcc text.
Archival agbcc compiles the inactive planner translation unit and its public
header with warnings promoted to errors; unnamed no-instance unions or any
size/offset drift therefore fail the legacy job. Host-only configuration
coverage performs no target compilation, while the real configured build
remains mandatory in the toolchain-equipped gate.
Missing summon configuration, an already available summon, a moved or
non-summoner unit, occupied/hidden/non-adjacent tiles, stale coordinates, and
normal-vs-DK action substitution all reject. Public host validation errors
name protocol v2 for both chapter range and candidate-cap failures.
Two consecutive identical protocol rejections receive distinct ACK IDs and
complete independently. A ROM that never consumes START returns typed
`COMMAND_ACK_TIMEOUT` with no acknowledgement or observation. A ROM that
acknowledges COMMIT but never publishes a new WAITING/terminal observation
returns typed `ACTION_COMPLETION_TIMEOUT`, retains the successful ACK, omits
COMPLETE, emits no stale observation, and terminates the adapter.

### Interactions and save compatibility

This is a stacked child of #91 and hard-depends on #85, #86, #89, #90, and
#91. #87 is unrelated; #88 is an optional later accelerated-fidelity
comparison. The bridge reuses production controller/strategy paths and makes
no save-byte, preference, migration, compatibility-epoch, generated-data, or
localization change.

### Automation

The focused host selector validates schema bounds, semantic availability,
opaque token rejection, mailbox exclusivity, scenario/build/RNG provenance,
typed semantic paging at maximum unit, inventory, convoy, telemetry, and flag
boundaries, canonical transcript export/import and atomic limits, complete
non-mutating enumeration,
deadline accounting, coordinate/slot lowering and execution, key consumption,
C/Python v2 diagnostics, normal/DK summon availability, coordinate lowering,
real summon creation, destructive checkpoint invalidation,
C/host command acknowledgement, long-action completion, repeated rejection,
and explicit transport timeout behavior,
C89/agbcc and native/ARM layout, executable host-only recursive-Make routing,
real configured toolchain build, and lifecycle teardown. The libmGBA selectors
run both planner implementations and all negative commands against the
fixed-symbol host-driven transport from a fresh boot with blank in-memory
SRAM. No manual-only criterion remains.

### Cleanup and limitations

The generated ROM, ELF, and adapter are removed with their random directory
under ignored `build/test-artifacts`; no save or savestate is created. This
case proves only the bounded local contract; it does not ship a policy model,
claim human-like play, or establish campaign balance or solvability.
## TC-AUTOPLAY-ACCEL-001: Accelerated-fidelity equivalence

- **Feature / originating issue:** `accelerated-fidelity-harness` /
  [#88](https://github.com/laqieer/fireemblem8-expansion/issues/88).
- **Dependencies, dependents, and conflicts:** depends on
  [#85](https://github.com/laqieer/fireemblem8-expansion/issues/85) telemetry,
  [#86](https://github.com/laqieer/fireemblem8-expansion/issues/86) bounded
  terminal semantics, and tester-case catalog [#54](https://github.com/laqieer/fireemblem8-expansion/issues/54).
  [#91](https://github.com/laqieer/fireemblem8-expansion/issues/91) consumes
  this profile for deterministic batch reports; later external planner and
  campaign experiments may also consume it. It conflicts with visual, audio,
  or presentation-timing acceptance cases, which must remain normal fidelity.
- **Supported configuration or artifact:** one modern AAPCS debug ROM and
  exact linked ELF, run twice from the same clean boot, fixture, controller,
  and deterministic RNG state: `normal-fidelity` and
  `accelerated-fidelity`.
- **Prerequisites and clean starting state:** repository root, Python 3.10+,
  a host C compiler, libmGBA development files, and the modern ARM toolchain.
  Use no save or savestate. The runner owns only ignored output under
  `build/`.

### Actions

1. Run `python3 tools/gba-playtest/tests/test_accelerated_fidelity.py -v`.
2. Run
   `make expansion-modern-autoplay-accelerated-fidelity-check MODERN_CONFIG=debug MODERN_ABI=aapcs`.
3. Inspect
   `build/expansion-modern/debug/aapcs/autoplay-accelerated-fidelity-check/accelerated-fidelity-benchmark.json`
   for the reported libmGBA version, host/runner identity, ROM provenance,
   source commit, profile names, and three non-gating wall-clock samples. This
   success-only artifact is removed before each invocation and atomically
   replaced only after all semantic and frame checks pass.

Both profiles execute every frame through `core->runFrame()` and use the same
clean Chapter 2 route and debug-only COMPUTER activation. The accelerated
profile applies only the already-supported `gPlaySt.config.gameSpeed` setting
and `BANIM_PRESENTATION_POLICY_OFF`'s existing animation option in its
disposable emulator core after the route's fixed input cadence. It does not
skip movement, camera, Proc, battle, event, trap, phase, save, or controller
logic, and it never writes a user save fixture.

### Expected result

The baseline finishes with `success` after exactly **17,135** emulated frames.
Accelerated fidelity finishes after exactly **16,869** frames, a deterministic
**266-frame reduction**. The terminal objective result, turn/action counts,
all terminal semantic probes (including every pointer-free gameplay field for
active blue, red, and green units), ordered committed-action/event telemetry,
chapter/permanent and
named objective-result flag transitions (`EVFLAG_WIN`, `EVFLAG_DEFEAT_ALL`,
and `EVFLAG_GAMEOVER`), and every sampled RNG state are identical.
Every repeated same-profile sample also has identical terminal and ordered
trace frames. The dedicated accelerated test ROM has event command commits
append a bounded ordered telemetry record; overflow is a failure. The
accelerated runtime probes
`BanimPresentationPolicy_GetCurrent()->id` and requires OFF after the harness
applies its existing configuration. Semantic-only terminal checkpoints omit the
unused whole-frame hash while libmGBA still allocates and renders its framebuffer
normally.

### Negative control

The focused host/backend fixture rejects malformed profile configuration,
duplicate traces, unexpected framebuffer output, terminal-impossible
configuration/trace timestamps, and over-budget trace output. The paired
runner flips one committed semantic trace value after capture and requires the
comparator to reject it; same-profile samples with shifted terminal or trace
frames, an event-telemetry overflow, or a non-OFF cached presentation policy
also fail. ROM, VRAM, palette, OAM, SRAM, or ignored-write configuration
bindings also fail before any success-shaped profile record is emitted. A
faster divergent action/event/RNG/flag route is therefore never
accepted. Normal visual, audio, and presentation-timing scenarios remain on
their existing normal-fidelity paths and fingerprints.

### Interactions and save compatibility

This is a harness profile only. It depends on #85 telemetry, #86 bounded
terminal semantics, existing game-speed and battle-presentation policy seams,
ELF probes, and libmGBA. Its private accelerated-ROM build alone links bounded
event-transition and presentation-policy probes (1,316 EWRAM bytes);
ordinary debug/release, starter/HQ profiles, and the archival lane omit them.
It adds no player-facing switch, save field, preference, migration,
compatibility epoch, configuration identity, generated game content,
localization, or archival-lane behavior.

### Automation, cleanup, and limitations

- `tools/gba-playtest/tests/test_accelerated_fidelity.py` validates strict
  schema-v3/profile parsing, plan-v5 semantic-only hashing, one real
  `runFrame()` per fixture frame, profile application, and trace perturbation.
- `expansion-modern-autoplay-accelerated-fidelity-check` runs the paired
  modern-debug libmGBA fixture, freezes the frame target, and writes the
  reproducible benchmark report. Wall-clock samples are report-only rather
  than an exact-duration assertion.

Delete ignored build output with `make clean_fast` only when needed. This case
does not validate visual/audio/timing behavior, authorize emulator savestates,
add a game-speed or animation setting, or model/simulate a shortened game
path.
