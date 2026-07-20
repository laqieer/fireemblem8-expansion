# Expansion save-format foundation (issue #2 slice 1)

This document is the single reference for the expansion-owned save
metadata record, the raw-byte compatibility classifier, the destructive
boot-path fix, and the host-side `scripts/modernize/save_format_tool.py`
CLI introduced in issue #2 slice 1.

**This slice is a foundation only.** It defines the on-media format, makes
detection non-destructive, and ships a host-side (out-of-console)
migration tool. It does **not** add save-menu incompatible-data UI (that
is slice 2), and it does **not** provide automatic in-console structural
migration of older save layouts -- see "Limitations" below.

## On-media format

### Where it lives

`struct SaveBlocks` (`include/bmsave.h`) previously reserved a 4-byte
`reserved` field at SRAM offset `0x73A0` followed by an unused 0x5C-byte
pad at `0x73A4`, ending exactly where `xmap` begins at `0x7400`:

```c
/* 0x73A0 */ u8 reserved[4];
/* 0x73A4 */ u8 _pad_[0x7400 - 0x73A4];   /* was: unused, 0x5C bytes */
/* 0x7400 */ struct ExtraMapSaveHead xmap;
```

This slice replaces only that pad with `struct ExpansionSaveMeta`
(`include/save_format.h`), which is exactly 0x5C bytes -- every other
offset in `struct SaveBlocks` (including `reserved` at `0x73A0` and `xmap`
at `0x7400`), and the struct's total size, is unchanged:

```c
/* 0x73A0 */ u8 reserved[4];
/* 0x73A4 */ struct ExpansionSaveMeta expansionSaveMeta;
/* 0x7400 */ struct ExtraMapSaveHead xmap;
```

`include/sram-layout.h` derives `SRAM_OFFSET_EXPANSION_SAVE_META` /
`SRAM_SIZE_EXPANSION_SAVE_META` from the pre-existing `SRAM_OFFSET_XMAP`
enum rather than a bare magic number.

### `struct ExpansionSaveMeta` layout

Fixed-width, explicit little-endian (native GBA byte order), zero implicit
padding -- every byte of the 0x5C-byte record is accounted for by a named
field (`STRUCT_PAD` fills the small alignment gaps between them):

| Offset | Size | Field | Kind |
| --- | --- | --- | --- |
| `0x00` | 4 | `magic` (`"FSAV"`, not NUL-terminated) | compatibility gate |
| `0x04` | 1 | `formatVersion` | compatibility gate |
| `0x05` | 1 | *(pad)* | -- |
| `0x06` | 2 | `compatEpoch` | compatibility gate |
| `0x08` | 1 | `abiId` (0=apcs-gnu, 1=aapcs) | diagnostic only |
| `0x09` | 3 | *(pad)* | -- |
| `0x0C` | 4 | `frameworkVersionPacked` | diagnostic only |
| `0x10` | 17 | `configFingerprint` (16 hex chars + NUL) | diagnostic only |
| `0x21` | 3 | *(pad)* | -- |
| `0x24` | 9 | `buildCommitShort` (8 hex chars + NUL) | diagnostic only |
| `0x2D` | 1 | *(pad)* | -- |
| `0x2E` | 2 | `checksum` (`Checksum16` over `[0x00, 0x2E)`) | -- |
| `0x30` | 44 | `reserved` (zero-filled) | -- |

Total: `0x5C` bytes exactly.

### Compatibility vs. diagnostic fields

Only three fields ever gate compatibility: `magic`, `formatVersion`, and
`compatEpoch`. Every other field (`abiId`, `frameworkVersionPacked`,
`configFingerprint`, `buildCommitShort`) is stored **only** for
diagnostics/tooling and **never** influences classification. This is a
deliberate design constraint (issue #2 slice 1's DON'T guardrails):

* The framework's full issue #8 config fingerprint mixes in settings
  (e.g. `config_preset` debug/release) that do not affect the save
  layout at all. Gating save compatibility on it would make an otherwise
  identical save look incompatible after a routine debug/release rebuild.
* `EXPANSION_SAVE_COMPAT_EPOCH` (see below) is therefore its own,
  independent, rarely-bumped value: it changes only when a save-layout or
  serialization change actually requires it.

`ClassifySaveCompatRawTests.test_diagnostic_field_differences_never_change_classification`
(`scripts/modernize/tests/test_save_format_tool.py`) proves this directly:
two metadata records with identical `magic`/`formatVersion`/`compatEpoch`
but completely different diagnostic fields both classify `SAVE_COMPAT_CURRENT`.

### `EXPANSION_SAVE_COMPAT_EPOCH`

A new, independent setting in `config.mk` (default `1`), exposed to C as
`FE8_EXPANSION_SAVE_COMPAT_EPOCH` (`include/expansion_config.h`) via the
same `#ifndef`-guarded-fallback pattern issue #8 established for the other
`FE8_EXPANSION_*` values, and validated/parsed by
`scripts/modernize/expansion_config.py` (`validate_save_compat_epoch()`,
range `[0, 65535]`) alongside the existing issue #8 settings -- see
`docs/config_identity.md`'s settings table.

**Bump `EXPANSION_SAVE_COMPAT_EPOCH` whenever a save-layout or
serialization change makes an existing save's raw bytes no longer mean
what this build's code assumes they mean** -- e.g. reordering/resizing/
reinterpreting fields inside any current save-block struct, changing a
checksum domain, or changing how a block is packed. Do **not** bump it for
build/title/debug/ROM-size-only changes; those must never make a current
save look incompatible (this is the entire reason it is independent of
the issue #8 config fingerprint).

**Fixed (review finding #4):** `modern.mk` now threads
`EXPANSION_SAVE_COMPAT_EPOCH` through the same
`scripts/modernize/expansion_config.py resolve` shell-out used for the
other `FE8_EXPANSION_*` values, adds
`-DFE8_EXPANSION_SAVE_COMPAT_EPOCH=$(MODERN_SAVE_COMPAT_EPOCH)` to
`MODERN_CFLAGS`, and includes `save_compat_epoch=...` in
`MODERN_COMPILE_SETTINGS` (the issue #8 content-addressed recompilation
stamp). This means `make ... EXPANSION_SAVE_COMPAT_EPOCH=2` changes the
value a modern build compiles in *and* invalidates every previously-built
object observing `include/expansion_config.h`, without requiring `make
clean` first. The legacy (non-modern) build still always uses the
`#ifndef` fallback from `include/expansion_config.h` (never receives any
`-D` override), matching every other `FE8_EXPANSION_*` value's treatment.
As with the rest of these settings, `EXPANSION_SAVE_COMPAT_EPOCH` remains
excluded from `ExpansionIdentity.fingerprint_fields()`/the issue #8
config fingerprint -- it is a distinct, independent compatibility gate,
never something a build/title/debug/ROM-size-only change can perturb.
See `scripts/modernize/tests/test_save_compat_epoch_modern_build.py` and
`scripts/modernize/tests/test_expansion_config.py`'s
`test_resolve_save_compat_epoch_override_changes_token_not_fingerprint`.

## Raw-byte compatibility classifier

`enum SaveCompatState` (`include/save_format.h`) has 8 explicit states,
decided in this precedence order by `ClassifySaveCompatRaw()`
(`src/bmsave-lib.c`) from **only** the global save header
(`struct GlobalSaveInfo`) and the metadata record above -- never any
current save-block struct (game/suspend/arena/xmap):

1. **`SAVE_COMPAT_EMPTY`** -- both the header region and the metadata
   region are entirely `0xFF`, matching `WipeSram()`'s fill pattern. This
   is the **only** state that may trigger an automatic full-SRAM wipe.
2. **`SAVE_COMPAT_HEADER_CORRUPT`** -- not blank, and the header itself
   fails the existing `ReadGlobalSaveInfo()`-style validation (name,
   `magic32`, `magic16`, checksum).
3. **`SAVE_COMPAT_VALID_LEGACY_OR_VANILLA`** -- header is valid, but the
   metadata record's `magic` is not `"FSAV"`. Covers both true vanilla FE8
   saves and legacy expansion saves written before this slice (the old
   pad was never written, so it reads back as whatever was already in
   that SRAM byte range -- typically `0xFF` from the last wipe, but this
   state does not require it to be).
4. **`SAVE_COMPAT_METADATA_CORRUPT`** -- metadata magic present, but its
   own checksum does not match.
5. **`SAVE_COMPAT_NEWER_UNSUPPORTED`** -- valid metadata whose
   `formatVersion` is greater than this build's `SAVE_FORMAT_VERSION_CURRENT`.
6. **`SAVE_COMPAT_MIGRATABLE_OLDER`** -- valid metadata whose
   `formatVersion` is less than current.
7. **`SAVE_COMPAT_SAVE_CONFIG_INCOMPATIBLE`** -- metadata is at the
   current `formatVersion`, but `compatEpoch` does not match
   `FE8_EXPANSION_SAVE_COMPAT_EPOCH`.
8. **`SAVE_COMPAT_CURRENT`** -- everything above passed.

Two independent implementations share this exact precedence and must be
kept in lockstep if it ever changes:

* **C** (shipped): `ClassifySaveCompatRaw()`/`ClassifySramSaveCompat()` in
  `src/bmsave-lib.c`, declared in `include/save_format.h`.
* **Python** (host tooling): `classify_save_compat_raw()`/
  `classify_image()` in `scripts/modernize/save_format_tool.py`, a
  byte-exact mirror documented as such in its own module docstring.

`ClassifySramSaveCompat()` additionally treats `IsSramWorking() == false`
(SRAM hardware not confirmed working) as `SAVE_COMPAT_HEADER_CORRUPT`
rather than `SAVE_COMPAT_EMPTY`, so a hardware fault can never be mistaken
for a genuinely blank cart and trigger an automatic wipe.

## Destructive boot-path fix

`EraseSramDataIfInvalid()` (`src/bmsave-lib.c`, called from `AgbMain()` in
`src/main.c`) previously wiped **all 32 KiB** of SRAM (via
`InitGlobalSaveInfodata()` -> `WipeSram()`) whenever
`ReadGlobalSaveInfo(NULL)` returned false -- true for corrupt, vanilla,
newer, older, and save-config-incompatible data alike, not just genuinely
blank SRAM. It now gates the wipe on `ClassifySramSaveCompat() ==
SAVE_COMPAT_EMPTY` instead, so every other state is left byte-for-byte
untouched for later UI/tooling (slice 2 and the host-side CLI below). The
bonus/rank/sound-room/link-arena per-block erase calls and
`LoadAndVerfySuspendSave()` immediately below it are unchanged -- those
are already bounded, per-block operations, not full-SRAM wipes, and are
out of this slice's scope.

`InitGlobalSaveInfodata()` (the genuinely-blank-SRAM initializer) now also
builds and writes a current `ExpansionSaveMeta` record
(`BuildCurrentExpansionSaveMeta()`) immediately after writing the global
header, so a freshly-initialized save is classified `SAVE_COMPAT_CURRENT`
from that point on.

### Requirement 11 scan: coupled boot/save-init call sites

The same "invalid means wipe" pattern was found in three call sites
outside `EraseSramDataIfInvalid()` itself:

* `src/sio_event.c`'s `EraseSaveData` -- an explicit, user-facing "erase
  save" feature. Wiping *is* the intended behavior here, so this one is
  deliberately left as-is (it is not a bug).
* `src/classchg-sel.c`'s `Check3rdTraineeEnabled` -- **fixed**.
* `src/bmsave-bwl.c`'s `SavePlayThroughData` -- **fixed**.

Both fixed call sites previously did:

```c
if (!ReadGlobalSaveInfo(&info)) {
    InitGlobalSaveInfodata();
    ReadGlobalSaveInfo(&info);
}
```

which silently equated "the header struct failed to read" with "SRAM is
blank" -- exactly the same bug `EraseSramDataIfInvalid()` had, just
reachable from class-change and chapter-completion instead of boot. Both
now call the shared `EnsureGlobalSaveInfoLoaded()` helper
(`src/bmsave-lib.c`, declared in `include/bmsave.h`), which only
initializes when the raw classifier proves SRAM is genuinely
`SAVE_COMPAT_EMPTY`, and otherwise returns `false` without touching or
reinterpreting SRAM -- `Check3rdTraineeEnabled` then returns `false`
(trainee locked) and `SavePlayThroughData` returns without saving,
instead of wiping or writing progress on top of unrecognized data.
`InitGlobalSaveInfodata()` itself is no longer called directly from
either file. See `scripts/modernize/tests/test_save_format_call_site_safety.py`
for the regression coverage (source-scan proof that the unsafe pattern
is gone, plus a byte-exact behavioral mirror of the guard across all 8
classifier states).

## Host-side CLI: `scripts/modernize/save_format_tool.py`


Python-stdlib-only (no new dependencies), following this repository's
existing `scripts/modernize/*.py` conventions.

```
save_format_tool.py [--repo-root PATH] inspect  <path>
save_format_tool.py [--repo-root PATH] validate <path> [--expect STATE ...]
save_format_tool.py [--repo-root PATH] migrate  <source> <dest> [--force]
```

* **`inspect`** prints the classification plus every diagnostic field.
  It never fails on classification itself, only on I/O (wrong size,
  unreadable file).
* **`validate`** exits non-zero if the classification is not one of the
  accepted `--expect` states (repeatable; default: `SAVE_COMPAT_CURRENT`).
* **`migrate <source> <dest> [--force]`** is strictly out-of-place and
  host-side only:
  1. Refuses `source == destination` (compared via resolved real paths,
     not string equality -- this catches symlink and relative-path
     aliases) before touching either file. A hard link to the source is
     a *distinct* path that resolving alone cannot recognize as the same
     file (there is no symlink to follow), so it is caught separately by
     device+inode identity (`os.path.samefile`) and refused with the same
     exit code `6` -- in both cases regardless of `--force`, since
     `--force` only governs overwriting an unrelated existing
     destination, never migrating a source onto an alias of itself.
  2. Refuses an already-existing `dest` unless `--force` is given. This
     early check is a cheap, human-friendly precondition (it avoids doing
     a full read/classify/rebuild just to report an obviously-already-
     existing destination) -- it is **not** the actual enforcement; see
     step 7.
  3. Reads the full 0x8000-byte source image into an in-memory
     `bytearray` -- the source file is **never** opened for writing.
  4. Classifies the source. Only `SAVE_COMPAT_VALID_LEGACY_OR_VANILLA`
     (the "v0" state: no metadata record at all) and already-`SAVE_COMPAT_CURRENT`
     (a no-op re-migration) are migratable in this slice -- every other
     classification is a precondition failure; nothing is written
     anywhere and the source is left untouched.
  5. Builds a current `ExpansionSaveMeta` record (mirroring
     `BuildCurrentExpansionSaveMeta()`; diagnostic fields use documented
     placeholders since a host tool has no live build context -- they
     never affect compatibility) and stamps it into the in-memory copy.
  6. Re-classifies the in-memory result. It must come back
     `SAVE_COMPAT_CURRENT`, or the migration is aborted with **nothing**
     written to `dest`.
  7. Publishes atomically: the full image is written to a temporary file
     created in `dest`'s own directory (so the final publish step is a
     same-filesystem operation), flushed and `fsync`'d, then re-read and
     re-verified byte-for-byte and by classification. Publication itself
     is then **OS-enforced, not merely precondition-checked**: without
     `--force`, the temp file is published via `os.link(temp, dest)`, an
     atomic fail-if-exists operation, so even a destination created by a
     concurrent writer strictly *after* the step-2 precheck still causes
     the publish itself to fail (exit code `7`) rather than clobbering
     it; with `--force`, the temp file is published via
     `os.replace(temp, dest)`, which unconditionally and atomically
     overwrites any existing destination. Either way, the temporary file
     is always removed afterward (whether publish succeeded, lost the
     no-force race, or failed outright), `dest` is never opened for
     writing directly, and any I/O failure -- including
     `tempfile.mkstemp()` itself failing -- is handled deterministically
     rather than propagating an uncaught exception. The published
     destination is re-read and re-verified once more after a successful
     publish.

### Exit codes

| Code | Meaning |
| --- | --- |
| `0` | success |
| `1` | I/O error (file not found, unreadable, unwritable, wrong size, temp-file creation/write/publish failure, ...) |
| `2` | argparse usage error (reserved by Python's `argparse`; this tool never raises it directly) |
| `3` | `validate`: classification not in the accepted `--expect` set |
| `4` | `migrate`: source classification is not migratable |
| `5` | `migrate`: post-migration re-verification did not come back `SAVE_COMPAT_CURRENT` |
| `6` | `migrate`: source and destination are the same file (refused) -- either by resolved canonical path (symlink/relative-path alias) or by device+inode identity (hard-link alias); enforced regardless of `--force` |
| `7` | `migrate`: destination already exists (refused; pass `--force` to overwrite). Raised either by the early precondition check (step 2 above) or by the OS-enforced `os.link()` publish itself losing a race against a concurrently created destination -- either way the destination is left completely untouched. |

## Limitations

* **No automatic in-console structural migration.** This slice's
  `migrate` command is host-side only, and only performs the trivial
  v0 -> v1 transition (stamping a metadata record onto a save that never
  had one). A real in-console migration of an *older current-format*
  save (`SAVE_COMPAT_MIGRATABLE_OLDER`, once a future format version 2+
  exists) would need to reinterpret and rewrite live save-block structs
  in place, and **no safe scratch SRAM block exists today** to do that
  atomically -- a partial in-place rewrite that is interrupted (power
  loss, reset) could corrupt the save beyond what any classifier can
  detect. Building that requires a future SRAM transaction/storage
  redesign, explicitly out of scope for this slice.
* **Save-menu incompatible-data UI is slice 2**, not this slice; this
  slice only makes the underlying classification safe and non-destructive
  to build on.

## Known infrastructure gaps

* This sandbox has no `arm-none-eabi-gcc` (the modern cross-compiler) and
  no root/network access to install `gcc-arm-none-eabi`. The modern
  debug/release build/link checks (`make expansion-modern-elf ...`) could
  not be executed here; this is a pre-existing gap already reflected in
  this repository's test suite (11 modern-toolchain-dependent tests were
  already failing/skipping before this slice for the same reason -- e.g.
  `test_modern_rom_boot.py`'s AAPCS-past-ABI-check tests). The vendored
  **legacy `agbcc`** toolchain (`tools/agbcc/`) *is* available and was
  used for all real-compiler evidence in this slice: `make fireemblem8.gba`
  builds and links cleanly with every change here (see the slice's PR/
  commit message for the captured build log), and
  `scripts/modernize/tests/test_save_format_layout.py`'s
  `test_layout_with_legacy_agbcc` compiles real
  `_Static_assert`-equivalent (C89 negative-array-size) layout probes
  against the real project headers with the real `agbcc` binary.
* `test_save_format_layout.py`'s `test_layout_in_all_modern_cells` mirrors
  `test_abi_layout.py`'s exact pattern (real `arm-none-eabi-gcc`,
  `_Static_assert`/`offsetof`, compiled across all four debug/release x
  AAPCS/APCS-GNU cells) and will run for real in any environment with that
  toolchain installed or `MODERN_CC`/`MODERN_TOOLCHAIN_ROOT` set; it
  currently skips here for the reason above, honestly reported rather than
  silently omitted.
* The classifier's precedence logic cannot be executed as real embedded
  C in this sandbox (no GBA emulator, and the real include chain is not
  portable to a host-native compiler without heavy stubbing). Its
  structural correctness (struct offsets/sizes/checksum domain) is proven
  by the layout probes above; its actual decision logic is proven by the
  thoroughly-unit-tested Python mirror in
  `scripts/modernize/save_format_tool.py`, which is explicitly documented
  (both here and in its own module docstring) as a mirror that must be
  kept in sync with the C implementation, not a substitute for testing the
  shipped code directly.

## Tests

* `scripts/modernize/tests/test_expansion_config.py` -- `EXPANSION_SAVE_COMPAT_EPOCH`
  parsing/validation/generation, extending the existing issue #8 suite.
* `scripts/modernize/tests/test_save_format_layout.py` -- struct-offset/size
  static assertions (legacy `agbcc`, always runs; modern cross-toolchain,
  skips gracefully if absent).
* `scripts/modernize/tests/test_save_format_tool.py` -- `Checksum16` mirror,
  blank-region detection, all 8 classifier states and their precedence,
  diagnostic-field independence, CLI `inspect`/`validate`/`migrate` exit
  codes, migration source preservation on failure, and successful
  out-of-place v0 -> v1 migration.

Run the whole suite:

```sh
cd scripts/modernize
python3 -m unittest discover -s tests -p "test_*.py"
```
