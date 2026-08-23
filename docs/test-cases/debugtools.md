# Debugtools tester-facing cases

These procedures cover bounded modern debug-only actions that are omitted
from supported release ROMs. They use semantic probes and save-byte evidence;
audio recognizability may be observed, but it is never the correctness oracle.

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
`gDebugToolsMusicProbe.ownerActive` is `1`. Back restores the
exact prior typed context, song/override state, playing or silent state, and
channel mode; it increments `gDebugToolsMusicProbe.restoreCount` once and
clears owner state. The title/map remains interactive, and the pre/post
whole-SRAM hashes are identical.

Locked or secret sound-room rows are intentionally previewable when their
catalog entry is otherwise valid. Preview never reads or changes unlock bits,
so this policy cannot reveal itself through an ordinary sound-room save.

### Negative control

Host fixtures reject `SONG_NONE`, the excluded `bgmId = -1` sentinel,
song ID `256`, zero/`MSG_COUNT` name IDs, a nested owner, and invalid catalog
rows without starting audio. Cancel before the first preview releases the
owner without restarting BGM. A no-prior-BGM fixture returns to exact silence.
Explicit forced cleanup and the chapter/title teardown hooks restore once and
are idempotent. In release, the action, submenu, and preview-owner symbols are
absent and every new probe field remains zero under identical input.

### Interactions and save compatibility

The action depends on the existing debugtools submenu lifecycle, the
authoritative sound-room catalog, and issue #37's typed BGM seam. It conflicts
with the dormant `DebugMenu_BgmDraw`/`DebugMenu_BgmIdle`, raw numeric song
input, a second gameplay router/preview owner, and external patches that bypass
the typed seam. It does not change BGM policy, sound-room unlock state, save
layout, compatibility epoch, generated content, or configuration identity.
Built-in ID 10 is reserved; contributor IDs are 11-65535, with all nine
contributor slots retained.

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
