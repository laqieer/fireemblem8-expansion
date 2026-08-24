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

## TC-DEBUGTOOLS-PROTOTYPE-004: Preview bounded music and restore its owner

- **Feature / originating issue:** `debugtools-music-preview` /
  [issue #126](https://github.com/laqieer/fireemblem8-expansion/issues/126),
  following [Discussion #122](https://github.com/laqieer/fireemblem8-expansion/discussions/122).
- **Supported configuration or artifact:** modern AAPCS debug source build for
  the positive title/live-map procedure; matching modern AAPCS release build
  for omission. The procedure does not require a distributed artifact.
- **Prerequisites and clean starting state:** use an unchanged authoritative
  `gSoundRoomTable`, a clean deterministic SRAM fixture, and the default
  debugtools hotkeys. Record the current BGM song/playing state and the SRAM
  hash before opening the action. Reset the emulator between title and
  live-map runs.

### Actions

1. In a modern debug build at the title screen, press `SELECT+R`, then `R`
   once to reach hub page two. Select **Music Preview**.
2. Press `A` on the first song. Press `LEFT` once to wrap to the last valid
   authoritative entry and press `A` again; press `RIGHT`, then `A`, without
   leaving the submenu to exercise rapid replacement.
3. Press `B` to return to the hub, then `B` to close the debug session.
   Confirm ordinary title input remains responsive.
4. Reset, use the Chapter 2 debug fixture to reach an interactive player
   phase, and open the hub with `SELECT+L`. Repeat steps 1-3, then move the map
   cursor once after the hub closes.
5. Run the same input fixtures against a modern release build.

### Expected result

Every displayed row comes from a valid in-range `gSoundRoomTable` entry and
uses its localized game-message name. The first and last valid rows preview;
rapid selection keeps one owner and increments
`gDebugToolsMusicProbe.previewCount` once per accepted `A`. While open,
`gDebugToolsMusicProbe.ownerActive` is `1`. Back restores the exact prior
typed context, song/override state, playing or silent state, and channel mode;
it increments `gDebugToolsMusicProbe.restoreCount` once and clears owner state.
The title/map remains interactive, and the pre/post SRAM hashes are identical.
The title fixture hashes the complete deterministic image; the blank-SRAM map
fixture normalizes only established build-commit/checksum diagnostic bytes.

Locked or secret sound-room rows are intentionally previewable when their
catalog entry is otherwise valid. Preview never reads or changes unlock bits,
so this policy cannot reveal itself through an ordinary sound-room save.

### Negative control

Host fixtures reject `SONG_NONE`, the excluded `bgmId = -1` sentinel, song ID
`256`, zero/`MSG_COUNT` name IDs, a nested owner, and invalid catalog rows
without starting audio. Cancel before the first preview releases the owner
without restarting BGM. A no-prior-BGM fixture returns to exact silence.
Explicit forced cleanup and the chapter/title teardown hooks restore once and
are idempotent. In release, the action, submenu, and preview-owner symbols are
absent and every new probe field remains zero under identical input.

### Interactions and save compatibility

The action depends on the existing debugtools submenu lifecycle, the
authoritative sound-room catalog, and issue #37's typed BGM seam. It conflicts
with dormant BGM handlers, raw numeric song input, a second gameplay
router/preview owner, and external patches that bypass the typed seam. It does
not change BGM policy, sound-room unlock state, save layout, compatibility
epoch, generated content, or configuration identity. Built-in ID 10 is
reserved; contributor IDs are 11-65535, with all nine contributor slots
retained.

### Automation

- `python3 -m unittest tools.gba-playtest.tests.test_debugtools_music -v`
  executes the real action, typed owner, and transient sound helper on the
  host. It covers first/boundary entries, invalid/sentinel/unsafe-name rows,
  rapid replacement, nested ownership, exact playing and silent restoration,
  cancel-before-preview, forced teardown, no unlock calls, telemetry, and
  disabled symbol omission.
- `python3 -m unittest tools.gba-playtest.tests.test_debugtools_registry.DebugToolsRegistryHostTests.test_builtin_identity_and_text_allocator_lifecycle -v`
  executes the real shared registry transition/session code and proves forced
  cleanup releases the guard, cancels reopen behavior, and is idempotent.
- `make expansion-modern-debugtools-music-check MODERN_CONFIG=debug MODERN_ABI=aapcs`
  runs the title and live-map libmGBA semantic fixtures and compares owner,
  selected-song, restore, BGM state, SRAM, and interactivity evidence.
- `make expansion-modern-debugtools-music-check MODERN_CONFIG=release MODERN_ABI=aapcs`
  runs the identical release inputs and requires all-zero omission probes.

No manual-only criterion remains. Listening may supplement the result, but
recognizing or preferring a song is not evidence for bounds, ownership,
restoration, or save neutrality.

### Cleanup and limitations

Reset the emulator between title and map fixtures. Host artifacts stay under
`build/test-artifacts/` and are removed by the tests. The action enumerates
only songs with authoritative sound-room names; unnamed custom song-table
slots are unsupported until a project adds a valid sound-room catalog row.
The archival lane has no action or runtime behavior.

## TC-DEBUGTOOLS-DIAGNOSTICS-001: Typed State and Engine diagnostics

- **Feature / originating issue:** `debugtools-visual-status-diagnostics` /
  [issue #127](https://github.com/laqieer/fireemblem8-expansion/issues/127).
- **Supported configuration or artifact:** modern AAPCS debug source build
  with the existing debugtools gate; the identical release profile is the
  disabled control.
- **Prerequisites and clean starting state:** start from a clean title boot
  with the deterministic debugtools SRAM fixture and reset it between title,
  Chapter 2 map, and Chapter 4 prep legs.

### Actions

1. At title idle, press SELECT+R. Press R past the action pages to State and
   Engine, Refresh once, then Back.
2. On the Chapter 2 map and Chapter 4 prep routes, enter State on a valid
   cursor unit and on an empty in-bounds tile.
3. Force-end one map diagnostics session and move the cursor after teardown.
4. Compare the final whole-SRAM hash with the initial hash.

### Expected result

Title exposes only common scalars. Map and prep expose validated context,
cursor, unit, weather/fog, and RNG data; an empty tile keeps cursor validity
while clearing unit fields. Refresh captures once. Explicit teardown restores
the owned display/font/lock state with a zero mismatch mask, leaves the map
interactive, and preserves SRAM.

### Negative control

NULL output, stale/empty/out-of-range units, active event/fade, battle
ownership, repeated input, and forced teardown fail closed. The release build
exposes only the disabled provider stub and keeps all diagnostics probes zero.

### Automation

```sh
python3 -m unittest tools.gba-playtest.tests.test_debugtools_diagnostics -v
make expansion-modern-debugtools-diagnostics-check MODERN_CONFIG=debug MODERN_ABI=aapcs
make expansion-modern-debugtools-diagnostics-check MODERN_CONFIG=release MODERN_ABI=aapcs
```

No framebuffer, screenshot, pointer, or subjective visual criterion is an
acceptance oracle. Close or force-end the session and reset the disposable
fixture after testing.
