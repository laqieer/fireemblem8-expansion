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

1. Run
   `python3 tools/gba-playtest/tests/test_accelerated_fidelity.py -v`.
2. Run
   `make expansion-modern-autoplay-accelerated-fidelity-check MODERN_CONFIG=debug MODERN_ABI=aapcs`.
3. Inspect
   `build/expansion-modern/debug/aapcs/autoplay-accelerated-fidelity-check/accelerated-fidelity-benchmark.json`
   for the reported libmGBA version, host/runner identity, ROM provenance,
   source commit, profile names, and three non-gating wall-clock samples.

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
also fail. A faster divergent action/event/RNG/flag route is therefore never
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
