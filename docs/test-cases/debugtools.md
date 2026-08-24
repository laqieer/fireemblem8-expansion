# Debug-tools tester cases

## TC-DEBUGTOOLS-PROTOTYPE-001: Bounded chapter and skirmish selector

- **Feature / originating issue:** `debugtools-chapter-skirmish-selector` /
  [issue #123](https://github.com/laqieer/fireemblem8-expansion/issues/123),
  decomposed from
  [Discussion #122](https://github.com/laqieer/fireemblem8-expansion/discussions/122).
- **Supported configuration or artifact:** source-built modern AAPCS debug
  ROM (`MODERN_CONFIG=debug MODERN_ABI=aapcs`) for positive routes; the
  matching modern AAPCS release ROM is the disabled negative control. No
  downloadable artifact is required.
- **Prerequisites and clean starting state:** build from a clean checkout with
  the modern toolchain and libmGBA available. For the deterministic procedure,
  use the generated `debugtools-current.sav` fixture or erase emulator SRAM
  before starting, and make a separate copy of any personal save before manual
  testing.

### Actions

1. Run
   `make expansion-modern-debugtools-selector-check MODERN_CONFIG=debug MODERN_ABI=aapcs`.
2. Start the debug ROM at the title screen. Press Select+R, move down three
   rows to **Chapter/Skirmish**, and press A.
3. Confirm the selector opens on **Chapter 4 (Common route)**. Press Left/Right
   and observe only bounded metadata-backed chapter/skirmish targets, then
   return to Chapter 4 and press A.
4. Observe the selector and hub close before the existing GameControl enters
   Chapter 4. Advance the real beginning event until the prep screen is
   interactive.
5. Return to the deterministic Chapter 2 live-map fixture through **Fast
   Boot: Chapter 2**. At stable Player Phase press Select+L, move down three
   rows, press A, press Right once to the adjacent Chapter 4 skirmish, and
   press A.
6. Observe one deferred BMap-to-GameControl handoff, reach skirmish prep, press
   Start, and wait for the blue Player Phase. Press Select+L once more; the hub
   must reopen from the skirmish map, proving the destination remains
   interactive. Press B to close it.
7. Repeat the title procedure but press B in the selector, repeat the entry
   hotkey while a hub/submenu is active, and then run the release command from
   the Automation section.

### Expected result

The hub retains nine built-in rows and contributor capacity. ID 4 opens one
bounded selector; Left/Right wraps through 45 targets for the stock metadata.
The default Chapter 4 identity is `0x1104`; its adjacent supported skirmish is
`0x2104`. A selection queues one typed request, all debug MenuProcs and their
Text allocations end, and only then does the title owner or yielded live-map
handoff route the existing GameControl through `GameControl_PostIntro`.

The chapter reaches live prep. The skirmish reaches live prep and then a blue
Player Phase from which the map hotkey opens a third hub session. Host
instrumentation observes exactly one request, consume, map handoff, and
GameControl route; ROM probes show the stable target ID, pending transition,
bootstrap arm, and destination state. The 32 KiB SRAM hash remains
`fnv1a64-sram:1fb2612031f74d22` at every pre-launch, prep, Player Phase, and
post-hub checkpoint.

### Negative control

B or forced submenu teardown before selection returns to the hub and queues
nothing. Malformed and unavailable target IDs reject explicitly; a second arm
while one request is pending reports busy; a second consume or handoff schedule
is a no-op. Repeated hotkeys cannot create a second hub. The release object has
no selector/action/request/handoff bodies, private selector state, or
debug-only generated count, and the exact chapter-selector input leaves the
unchanged release debugtools probe zero.

### Interactions and save compatibility

This depends on the issue #11 registry, deferred menu ownership,
title/map/prep hotkeys, GameControl PostIntro handoff, bootstrap write
suppression, generated chapter metadata, typed world-map node/spawn metadata,
and chapter event groups. It has no dependent feature. It does not depend on
starter gameplay features, custom spells, localization selection, or autoplay.

It conflicts with raw prototype/debug patches, direct edits to engine-owned
menu tables, save-writing launchers, a parallel chapter catalog or action
registry, and restart/BMap teardown from a menu callback. It changes no save
field, layout, epoch, migration, completion state, tactician data, or ordinary
slot. All launch state is transient EWRAM.

### Automation

- `TMPDIR="$PWD/build/test-tmp" python3 -m unittest
  tools.gba-playtest.tests.test_debugtools_registry.DebugToolsChapterSelectorHostTests
  -v` executes authoritative enumeration, stable IDs, invalid/unavailable,
  duplicate, cancel/forced teardown, exact-once consume, deferred map handoff,
  source-boundary, generated-count, scenario-schema, no-save, and release
  omission checks.
- `make expansion-modern-debugtools-selector-check MODERN_CONFIG=debug
  MODERN_ABI=aapcs` verifies
  `debugtools-selector-chapter-modern-debug.json` and
  `debugtools-selector-skirmish-modern-debug.json` with exact-ELF symbolic
  probes and libmGBA.
- `make expansion-modern-debugtools-selector-check MODERN_CONFIG=release
  MODERN_ABI=aapcs` verifies the exact-input release negative.
- `python3 -m unittest scripts.localization.tests.test_debugtools_localization
  -v` and `python3 scripts/check_docs.py --check` cover localized geometry and
  the canonical catalog/procedure.

No manual-only visual, audio, or UX criterion is material.

### Cleanup and limitations

Close the hub, stop the emulator, and discard the deterministic SRAM fixture;
do not copy it over a personal save. The selector exposes only valid
world-map-node-backed chapter targets and encounter variant 0 at typed spawn
nodes with complete encounter data. It does not expose placeholder rows,
arbitrary project maps or memory, encounter variants 1/2, prior World Map
cinematics/progression, save initialization, completion mutation, or the
dormant prototype menu.
## TC-DEBUGTOOLS-PROTOTYPE-003: Cursor-selected bounded unit inspector/editor

- **Feature / originating issue:** `cursor-unit-debug-editor` /
  [issue #125](https://github.com/laqieer/fireemblem8-expansion/issues/125).
- **Supported configuration or artifact:** modern AAPCS debug source build
  (`MODERN_CONFIG=debug`, `MODERN_ABI=aapcs`) with the default
  `FE8_EXPANSION_DEBUGTOOLS_ENABLED=1`; the modern release build is the
  compiled-out negative.
- **Prerequisites and clean starting state:** start from a clean Chapter 2
  PlayerPhase map produced by the debugtools Fast Boot launcher with blank
  SRAM. Do not begin during an event, battle, prep screen, or with a selected
  moving unit. Reset the fixture before repeating.

### Actions

1. At the title screen, press `SELECT+R`, choose **Fast Boot: Chapter 2**, and
   wait for the stable live map. The scenario cursor begins at `(6,3)` over
   canonical slot 1 (character 6, class `0x48`) with `17/17` HP.
2. Press `SELECT+L`, move down four hub rows, and choose **Unit Inspect**.
   Read the disabled Unit/Class and State rows plus the HP/status/AI summary;
   press no edit confirmation yet.
3. Choose **Edit HP**, press Left once so Current HP previews `16`, then press
   A once to confirm. Return to the map hub.
4. Reopen **Unit Inspect** on the same tile and choose
   **Confirm Heal to Full**. Return to the hub, then close it with B.
5. Move Right to the empty `(7,3)` tile, reopen the hub, and choose
   **Unit Inspect** again. Close the still-live hub with B, then move Left.
6. Reset the fixture. For focused field coverage, run the host commands below:
   they preview/confirm every visible stat, both documented AI enum families,
   and every supported temporary status, then exercise all negative controls.

### Expected result

- Step 2 records slot/character/class/state/HP/status/AI without changing the
  unit, transaction counters, map, or SRAM.
- Step 3 leaves HP at `17` while previewing, then changes only current HP
  `17 -> 16` after A. Telemetry records Current HP, old `17`, new `16`,
  `APPLIED`, and exactly one map refresh.
- Step 4 changes only current HP `16 -> 17`; the legacy heal counter and new
  transaction/refresh counters advance once.
- Step 5 opens no unit submenu. It records `REJECTED_EMPTY`, preserves HP
  `17`, preserves two successful unit transactions, writes no save data, and
  leaves map input responsive.
- The whole-SRAM hash immediately before the issue #125 tail exactly equals
  the final hash.

### Negative control

The identical frame script on a modern release ROM leaves
`gDebugToolsProbe` all zero, physically omits the issue #125 editor
code/menu/state/probe symbols, and preserves its whole-SRAM hash. Host
negatives reject empty, invalid, dead, purple/link-arena,
noncanonical class, stale/moved/replaced and value-drift targets; active
standard/battle events and battle daemon ownership; out-of-range values;
cancel/no-change; `UNIT_STATUS_RECOVER`, `UNIT_STATUS_12`, and
`UNIT_STATUS_13`; and forced menu teardown. None advances the mutation or
refresh counter.

### Interactions and save compatibility

This depends only on issue #11's fixed registry/action/deferred-submenu seam,
live-map cursor/unit helpers, authoritative unit/stat/status/AI helpers, and
the expansion localization catalog. It has no dependency on gameplay flags,
generated-data ownership, #85/#87 faction control, or locale selection. It
conflicts with active event/battle ownership, external raw unit editors,
prototype debug patches, and duplicate mutation routers.

The capability changes active in-memory unit state only after confirmation.
It calls no save writer, performs no implicit save, and changes no save field,
layout, compatibility epoch, schema, or migration. Debug menus/probes use
fixed storage, share one value-menu array, stay below `MENU_ITEM_MAX`, and
allocate no heap memory. The reviewed debug delta is +8,256 linked ROM bytes
and +672 EWRAM bytes with 1,032 EWRAM bytes free; IWRAM and the modern release
budget are unchanged. Editor behavior/data are omitted from release. The
archival lane gains no runtime behavior.

### Automation

```bash
TMPDIR="$PWD/.test-tmp" python3 -m unittest \
  tools.gba-playtest.tests.test_debugtools_registry.DebugToolsExtendedToolsHostTests \
  tools.gba-playtest.tests.test_tools_scenario -v

make expansion-modern-debugtools-tools-check \
  MODERN_CONFIG=debug MODERN_ABI=aapcs

make expansion-modern-debugtools-tools-check \
  MODERN_CONFIG=release MODERN_ABI=aapcs

python3 -m unittest scripts.localization.tests.test_debugtools_localization -v
python3 scripts/check_docs.py --check
```

The first command executes the real editor callbacks and separately links and
executes the production `bmunit.c`/`eventscr3.c` helpers. The paired libmGBA
gate proves the live positive/negative semantics, exact SRAM equality, and map
interactivity. All deterministic criteria are automated; there is no
manual-only visual or UX judgment.

### Cleanup and limitations

Close the hub with B and reset or discard the live debug fixture; the tool
does not save it. Remove only ignored build output with `make clean_fast`.
Class, items/inventory, level/EXP, ranks, supports, faction, rescue, movement,
AI config/AI3/AI4, Recovery/Condition guesses, raw structures, and arbitrary
addresses are deliberately unsupported.
