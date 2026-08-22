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
    fixed ARM ROM/EWRAM budget.
- `make expansion-modern-autoplay-check MODERN_CONFIG=debug MODERN_ABI=aapcs`
  - `tools/gba-playtest/run_autoplay_checks.py` plus
    `autoplay-computer-modern-debug.json` and
    `autoplay-player-default-modern-debug.json` checked fingerprints.
- `make expansion-modern-autoplay-check MODERN_CONFIG=release MODERN_ABI=aapcs`
  - default PLAYER negative with
    `autoplay-player-default-modern-release.json`.

### Cleanup and limitations

Use `make clean_fast` only if build artifacts must be removed. This case proves
the reusable low-level executor and telemetry, not seize, recruitment,
village, chest, resource, strategy, objective, balance, or campaign behavior.
