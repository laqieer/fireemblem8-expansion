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
   `python3 -m unittest scripts.generated_data.tests.test_chapterobjectives_schema scripts.generated_data.tests.test_chapterbundle_schema.ChapterObjectivesBundleTests scripts.generated_data.tests.test_cli_new_tables.CliChapterObjectivesTests -v`.
2. Run `python3 -m unittest tools.gba-playtest.tests.test_chapter_objectives -v`.
3. Run
   `make expansion-modern-chapter-objectives-check MODERN_CONFIG=debug MODERN_ABI=aapcs`.

### Expected result

The generated fixture resolves every initial generic kind: a known event
objective transitions pending -> success with progress 0 -> 1 after its
existing event flag is set; a real Chapter 2 group moves from reach progress
0 -> 1; defeating its protected unit produces a protect failure and a
defeat-group success. The evaluator publishes those known states and stable
IDs through a test-only profile probe. Resetting transient telemetry and
refreshing it reproduces the same live state, proving no objective lifecycle
state is hidden outside chapter units, flags, and turn state.

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
chapter-unit/character references, invalid rectangles or turns, missing or
stale dependency declarations, contradictory flags, protect cycles, and an
unreachable chapter-bundle symbol fail with source locations and JSON
breadcrumbs. Kind-specific unused fields, invalid non-ASCII stable IDs, and a
protect objective that completes by defeating its protected unit also fail
closed. The host reset/reconstruction assertion and the real fixture
Suspend/Resume route both clear only transient telemetry, then derive the
same state without serializing an objective field.

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
   `python3 -m unittest scripts.generated_data.tests.test_autoplaystrategies_schema scripts.generated_data.tests.test_chapterbundle_schema.ChapterAutoplayStrategiesBundleTests -v`.
2. Run
   `python3 -m unittest tools.gba-playtest.tests.test_autoplay_strategies -v`.
3. Build the bounded debug/release enabled and disabled objects with
   `make expansion-modern-autoplay-strategies-objects MODERN_CONFIG=<debug|release> MODERN_ABI=aapcs EXPANSION_AUTOPLAY_STRATEGIES=<0|1> MODERN_BUILD_ROOT=<isolated-root>`.

### Expected result

Aggressive chooses the immediate legal combat decision. Objective-first chooses
the deterministic nearest in-rectangle movement decision. A unit assignment
overrides the group and chapter assignments, a group overrides chapter, and
the profiles repeat the same action trace without an RNG draw. The typed event
helper accepts only its declared activation flag and defers active-blue-phase
changes. Unknown IDs, duplicate/missing callbacks, invalid capability bits,
capacity overflow, and Objective-first with an unsupported objective kind
fail explicitly before an action commits.

### Negative control

With profiles disabled and the default empty assignment source,
`ExpansionAutoplayStrategies_TryDecide()` returns fallback and creates no
action; the original `Unit.ai[]` decision path remains authoritative. The
ARM selector confirms reference callback symbols are absent from the disabled
object set and both profile states allocate zero EWRAM.

### Interactions and save compatibility

The case depends on the #85 blue computer executor and #89 typed
objective/group records. It has no known feature conflicts, no player UI,
locale text, save field, migration, compatibility-epoch change, or archival
behavior. Generated activation flags are existing event state, so suspend/load
reconstructs assignment selection without hidden runtime storage.

### Automation

- `test_autoplaystrategies_schema` validates the typed generated registry,
  assignments, frozen reference capability contracts, and malformed
  negatives.
- `ChapterAutoplayStrategiesBundleTests` proves assignment bundles remain
  reachable only through the existing chapter bundle.
- `test_autoplay_strategies` executes the real callback/dispatch API for
  chapter/group/unit precedence, event-boundary, unsupported-profile, disabled
  fallback, and ARM ROM/RAM assertions.

## TC-AUTOPLAY-BATCH-001: Deterministic finite autoplay batch report

- **Feature / originating issue:** `deterministic-autoplay-batch-reports` /
  [#91](https://github.com/laqieer/fireemblem8-expansion/issues/91).
- **Supported configuration or artifact:** normal-fidelity generated homebrew
  libmGBA fixture with three explicit seeds. Accelerated fidelity (#88) is not
  required and is rejected by this initial collector.
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
provenance, a terminal record, and configured terminal/frame/turn/action,
faction/group, event, EXP, item, and resource metric records. A comparison
reports a deliberately changed metric for its seed without claiming that either
result is balanced or statistically significant.

### Negative controls

Duplicate or implicit seed lists, a missing/non-positive hard bound,
unsupported metric, existing output path, and `--sram-image` all fail before
the capture function runs. A non-success terminal is retained as
`terminal_failure`, contributes to the failure count, and makes the command
return 1. Comparison leaves both input report bytes untouched.

### Interactions and save compatibility

The collector depends on #86's bounded terminal/checkpoint semantics and #90's
profile identity. #88 accelerated fidelity is optional integration only, not a
parent. The seed write is restricted to explicitly declared writable
EWRAM/IWRAM at one declared frame; no save is loaded or retained. There are no
ROM feature flags, target allocation, generated data, localization, save
layout, migration, compatibility epoch, or archival impact.

### Cleanup and limitations

The test fixture removes its temporary directory beneath `build/test-artifacts`.
Use
`make clean_fast` for other build outputs. Three seeds prove report
determinism, complete per-run visibility, and comparison structure only; they
do not establish statistical power, difficulty, campaign quality, or balance.

## TC-AUTOPLAY-PLANNER-001: Local external planner step and replay contract

- **Feature / originating issue:** `local-external-autoplay-planner` /
  [#92](https://github.com/laqieer/fireemblem8-expansion/issues/92).
- **Supported configuration or artifact:** modern AAPCS debug with
  `EXPANSION_AUTOPLAY_PLANNER=1`; local generated libmGBA transport fixture.
  Release and archival builds are unsupported and omit the bridge.
- **Prerequisites and clean state:** Python 3, host C compiler, ARM toolchain,
  libmGBA development files, exact debug ROM/config provenance, and a fresh
  in-memory blank SRAM. No save fixture, savestate, network, or external
  service is permitted.

### Actions

1. Run
   `python3 -m unittest tools.gba-playtest.tests.test_autoplay_planner.PlannerBridgeTests -v`.
2. Run
   `python3 -m unittest tools.gba-playtest.tests.test_autoplay_planner.PlannerLibmGBAIntegrationTests.test_two_chapter_fixture_replays_from_clean_boot_without_save_or_snapshot -v`.
3. Start a typed mailbox run, read its observation page, and commit only the
   returned action token. Replay from a fresh boot through chapter one and the
   semantic chapter-two checkpoint.

### Expected result

The scripted reference chooser and bounded search chooser consume the same
pointer-free observation/action records and produce a deterministic
two-chapter trace. The C adapter holds a #90-produced legal decision until its
matching token commits it through the #85 computer action route. The clean
libmGBA transport fixture reaches its second chapter and replays byte-for-byte
without loading a save or snapshot.

### Negative control

Stale observation IDs, unknown ordinals, forged tokens, unavailable
capabilities, malformed mailbox headers, cancellation, provenance mismatch,
and resource overflow fail with explicit typed outcomes and no action commit.
The mailbox has no raw address/write method. Release configuration rejects
`EXPANSION_AUTOPLAY_PLANNER=1`; no bridge state is present when disabled.

### Interactions and save compatibility

This is a stacked child of #91 and hard-depends on #85, #86, #89, #90, and
#91. #87 is unrelated; #88 is an optional later accelerated-fidelity
comparison. The bridge reuses production controller/strategy paths and makes
no save-byte, preference, migration, compatibility-epoch, generated-data, or
localization change.

### Automation

The focused host selector validates schema bounds, unavailable states, token
rejections, mailbox exclusivity, C/ARM adapter linkage, scripted/search
interoperability, cancellation, and read-only RNG/campaign checkpoints. The
single libmGBA selector validates fresh two-chapter clean boot/replay. No
manual-only criterion remains.

### Cleanup and limitations

The generated fixture is removed from ignored `build/test-artifacts`. This
case proves only the bounded local contract; it does not ship a policy model,
claim human-like play, or establish campaign balance or solvability.
