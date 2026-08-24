# Debug-tools tester cases

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
- The normalized SRAM hash immediately before the issue #125 tail equals the
  final hash. It excludes only the build-variable `ExpansionSaveMeta` config
  fingerprint, build commit, and dependent checksum; every save-state byte
  and compatibility field remains covered.

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
gate proves the live positive/negative semantics, metadata-normalized SRAM
equality, and map interactivity. All deterministic criteria are automated; there is no
manual-only visual or UX judgment.

### Cleanup and limitations

Close the hub with B and reset or discard the live debug fixture; the tool
does not save it. Remove only ignored build output with `make clean_fast`.
Class, items/inventory, level/EXP, ranks, supports, faction, rescue, movement,
AI config/AI3/AI4, Recovery/Condition guesses, raw structures, and arbitrary
addresses are deliberately unsupported.
