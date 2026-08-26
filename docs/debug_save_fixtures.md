# Volatile debug save fixtures

Issue [#128](https://github.com/laqieer/fireemblem8-expansion/issues/128)
adds a modern-debug-only, one-shot save-fixture sandbox to built-in debugtools
action 9 (**Save State**). It never allocates or reserves SRAM. It reads one
already-valid source target, creates a sanitized full image in the existing
32 KiB `ewram_overlay_gamestartsave`, and lets the existing `GameCtrlProc`
continue that image once while every cartridge write is blocked.

The accepted design and complete byte contract are recorded in
[issue comment 5384426549](https://github.com/laqieer/fireemblem8-expansion/issues/128#issuecomment-5384426549).
The tester procedure is
[`TC-DEBUGSAVE-001`](test-cases/debug-save-fixtures.md#tc-debugsave-001-volatile-save-fixture-isolation-and-recovery).

## Supported surface

- Modern AAPCS debug builds with `FE8_EXPANSION_DEBUGTOOLS_ENABLED=1`.
- Game source slot 0, 1, or 2, or the latest valid alternating suspend plus
  its valid backing game slot.
- Source SRAM must classify `SAVE_COMPAT_CURRENT`; the selected block must
  have its canonical index, kind, offset, size, magic, and checksum.
- Temporary completion count `0..12`.
- Tactician name unchanged or replaced by the fixed, NUL-padded marker
  `FIXTURE`.
- One same-session continue through the already-running `GameCtrlProc`.

Preparation is title-only. The map and prep hub retain read-only compatibility
inspection and show no fixture source rows.

## Storage and target identity

`src/debug_save_fixture.c` owns no persistent image buffer. It borrows
`sGameStartSaveBuf`, the existing `0x8000`-byte
`ewram_overlay_gamestartsave` object, only while the title/debugtools session
owns the screen. The typed public contract is
`include/expansion_debug_save_fixture.h`.

Every preview pins:

- a nonzero generation;
- exact whole-source FNV-1a hash;
- source block checksum;
- game source or resolved suspend index;
- valid backing game index;
- sanitized fixture checksum;
- source format, epoch, ABI, config fingerprint, and build diagnostics.

No API accepts a raw image pointer, SRAM offset, physical destination, or
generic integer target. Unselected payloads and global/user records are
erased from the volatile copy. The fixture receives current expansion
metadata and fixture-owned completion state; current runtime UI/locale
preferences are preserved instead of being loaded from the fixture.

## Compatibility behavior

The operation uses the existing classifier:

1. `ClassifySramSaveCompat()` must return `SAVE_COMPAT_CURRENT`.
2. Source-neutral block helpers enforce the existing save-block
   magic/checksum rules plus canonical offsets and sizes.
3. The sanitized image is stamped with
   `BuildCurrentExpansionSaveMeta()` and rechecked with
   `ClassifySaveCompatRaw()`.

Format version and `EXPANSION_SAVE_COMPAT_EPOCH` remain the compatibility
gates. ABI, framework version, config fingerprint, and build commit remain
diagnostic-only. Older, newer, wrong-epoch, corrupt, legacy, blank, or invalid
target images fail closed. There is no migration, format bump, epoch bump, or
on-media schema change.

## Preview and confirmation

Initial Save State selection still samples the compatibility state. At title
it then offers Game 0/1/2, Suspend, and Back:

1. Source selection creates the volatile preview. Completion and tactician
   rows cycle only bounded fixture fields.
2. **Arm RAM** re-hashes physical SRAM, revalidates source identity and the
   fixture image, and records the first confirmation.
3. A separate final menu defaults to Back. **Run RAM** requires a fresh
   `L+R+A`; it repeats every validation and queues the typed one-shot request.

Back, B, wrong input, stale source, forced menu teardown, or any validation
failure zeroizes all 32 KiB and releases the normal deferred debugtools
session. `DebugTools_EndSessionAfterMenuEnd()` performs the same allocator
cleanup as final Back without reopening the hub.

## Game-control and persistence ownership

The debug menu never ends BMap, restarts game control, performs chapter
cleanup, or calls `SoftReset`. `Title_IDLE` observes the one-shot request, and
`GameControl_PostIntro` is the sole consumer:

- `ReadGameSaveFromImage()` and `ReadSuspendSaveFromImage()` reuse the normal
  decode logic without invalidating suspend, writing last-slot metadata, or
  applying physical preference side effects.
- Game sources continue through the ordinary game branch.
- Suspend sources continue through the ordinary resumed-game branch.
- The image is zeroized before gameplay overlays reuse its addresses.
- Fixture global completion state is cached while active.

While active, `WriteSramFast()` and `WriteAndVerifySramFast()` reject every
range intersecting `0x0E000000..0x0E007FFF`. High-level game, suspend, global,
preference, and wipe paths also report a blocked operation. The map Suspend
row is disabled. Ordinary release/archival writes are unaffected.

## Interruption and recovery

Preview, Arm, and pending state exist only in EWRAM. Cancel and forced teardown
zeroize them. Power loss or soft reset clears all EWRAM before boot; the
physical source is unchanged and can be loaded normally. A consume-time
failure does not start the map. Returning to title clears active fixture
global state before releasing the write guard.

There is no rollback journal or recovery slot because no fixture byte is
written to persistent storage.

## Build and resource impact

- Existing `FE8_EXPANSION_DEBUGTOOLS_ENABLED`; no new option or registry.
- Modern release omits all `DebugSaveFixture_*`,
  `gDebugSaveFixtureProbe`, and source-neutral fixture decoder symbols.
- Archival builds compile the original save readers and link no fixture code.
- No SRAM/IWRAM allocation and no heap allocation.
- The existing 32 KiB overlay is reused; persistent state/probe remains within
  the accepted 256-byte cap.
- Expansion catalog IDs 121-134 provide all source, preview, confirmation, and
  status labels for supported locales.

## Validation

```bash
python3 -m unittest \
  tools.gba-playtest.tests.test_debug_save_fixture \
  tools.gba-playtest.tests.test_sram_fixture \
  tools.gba-playtest.tests.test_debugtools_registry.DebugSaveFixtureHostTests -v

make expansion-modern-debug-save-fixture-check \
  MODERN_CONFIG=debug MODERN_ABI=aapcs
make expansion-modern-debug-save-fixture-check \
  MODERN_CONFIG=release MODERN_ABI=aapcs
```

The debug gate runs positive, cancel, invalid-target, wrong-epoch, and
pre-Arm reset scenarios. The release gate verifies symbol omission and inert
hotkeys. Every persistent checkpoint uses exact `fnv1a64-sram` with no
excluded ranges.

## Non-goals and conflicts

No blank-file initializer, live-map capture, fixture export, save-back,
reserved SRAM, externally backed runtime image, raw memory editor, per-slot
erase, in-console migration, or dormant prototype/retail callback is exposed.

External patches that write cartridge memory without the repository's
`WriteSramFast` path, another simultaneous owner of
`ewram_overlay_gamestartsave`, or a second compatibility classifier are
unsupported conflicts.
