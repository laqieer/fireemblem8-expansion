# Asset manifest framework

Issue #60 provides one versioned, source-owned asset-manifest framework for
future adapters. It is infrastructure, not an editor importer or a runtime
asset registry. The current version proves one Chapter 2 TMX map layout
without changing the selected map, save format, configuration identity,
localization, or runtime behavior.

The authoritative coverage and ownership audit remains
[`community_asset_coverage.md`](community_asset_coverage.md). Read that
catalog before proposing a kind: a new kind must attach to the catalogued
runtime/table/linker seam instead of creating a parallel list.

## Public manifest API

The committed root is [`../assets/manifest.json`](../assets/manifest.json).
Its version-1 shape is:

```json
{
  "schemaVersion": 1,
  "assets": [
    {
      "id": "STABLE_SYMBOLIC_ID",
      "kind": "tiled-tmx-map-layout",
      "sources": ["assets/tmx/ChapterMap.tmx"],
      "dependsOn": [],
      "options": {
        "format": "tmx-safe-v1",
        "compression": "lz77",
        "layer": "Main",
        "tilesetId": "fe8-metatiles-16px-4096"
      },
      "ownership": {
        "seam": "chapter-data-asset-table",
        "tableSource": "src/data/data_8B363C.c",
        "chapterSettings": "src/data/chapter_settings.json",
        "chapterSettingsIndex": 2,
        "mainLayerId": 11,
        "symbol": "Ch2Map",
        "consumer": "GetChapterMapPointer"
      },
      "resources": {
        "mapWidth": 15,
        "mapHeight": 15,
        "mapBufferBytes": 2048
      },
      "provenance": {
        "origin": "source origin",
        "license": "license or permission statement",
        "modifications": "description",
        "tools": ["optional-tool version"]
      }
    }
  ]
}
```

`id` is a stable `UPPER_SNAKE_CASE` identifier for diagnostics and
dependencies; it is **not** another runtime ID namespace. `sources` must be
existing, tracked, repository-relative files outside `build/`. Absolute,
escaping, generated, missing, untracked, symlinked, backslash-separated, or
non-NFC paths are rejected. Case-folded/NFC-equivalent source paths collide
and are rejected so the manifest cannot differ between host filesystems.
`dependsOn` can only name another manifest ID; duplicate edges and cycles are
rejected.

The manifest has one static extension seam:
`scripts/assets/manifest.py` registers a kind in `KIND_REGISTRY`. Manifest
data can never select code, run a command, or dynamically load an adapter.
Unknown schema versions, kinds, fields, and kind options fail closed.

Every record carries structured provenance. It records claimed origin,
license/permission, modifications, and optional tool/version data, but
validation, conversion, or a successful build never grants redistribution
clearance. External tools such as FEBuilderGBA, Event Assembler, Tiled, and
.NET/GUI tooling are not normal build dependencies.

## Current proof and runtime seam

`CH2_MAIN_MAP` owns the dependency relationship for the tracked
[`assets/tmx/Ch2Map.tmx`](../assets/tmx/Ch2Map.tmx) source. Its
`tiled-tmx-map-layout` adapter verifies the safe TMX contract documented in
[`tmx_map_layouts.md`](tmx_map_layouts.md), as well as all of the following
before rendering:

1. The parsed TMX dimensions and canonical payload match declared resources.
2. The Chapter 2 settings row selects `mainLayerId` 11.
3. `gChapterDataAssetTable[11]` is the existing `Ch2Map` symbol.
4. The serialized dimensions fit the existing 2048-byte `gBmMapBuffer` contract:
   a 2-byte header plus 2 bytes for each `u16` tile.

The generated dependency fragment attaches the TMX source to the existing
table object and makes the existing `const_data_chapter_maps.o` depend on its
ignored generated `.mar`/JSON -> `.bin` -> `.lz` chain. Every declared source
is also a prerequisite of the generated include, so editing a TMX regenerates
that chain before Make resolves object prerequisites. The generated `.bin`
explicitly depends on both `.mar` and JSON because `mar_to_map.py` reads the
JSON dimensions for its header; its `.lz` and INCBIN consumer therefore rebuild
when dimensions change even if the flattened tile bytes do not. It does not
emit C, assembly, a linker list, or an asset lookup table. The existing
`Ch2Map` symbol now INCbins that generated LZ stream from the fixed
`build/generated/assets/` root. `ASSET_OUTPUT_DIR` therefore fails fast when
the selected manifest declares any literal-INCBIN consumer (`formatted-portrait-package`,
`tiled-tmx-map-layout`, or `battle-animation-package`). A manifest without
those kinds retains the supported subdirectory override. The pre-existing `gChapterDataAssetTable`,
`GetChapterMapPointer`, and `InitChapterMap`/`UnpackChapterMap` path remain
the runtime consumers.

## Commands and generated-output policy

```sh
make assets-validate  # strict schema, source, ownership, capacity, provenance checks
make assets-generate  # write only build/generated/assets/
make assets-check     # fail on missing, stale, or orphan generated output
make assets-test      # positive and adversarial stdlib host tests
make assets-clean     # remove only build/generated/assets/
```

`assets-generate` uses write-if-changed output for deterministic incremental
builds. `assets-check` never writes outputs; it compares the expected
fragment, inventory, and adapter-owned canonical outputs and fails on stale,
missing, or orphan files. It recognizes only the TMX adapter's `.bin` and
`.bin.lz` transient build products in addition to those canonical outputs.
The normal Make include regenerates the dependency fragment before graph
resolution when the source manifest, framework, or any declared source
changes, then exposes ordinary source prerequisites for the actual owning
object. Outputs are confined to the fixed
ignored `build/generated/assets/`, are disposable, and must never be
hand-edited or committed.

The framework does not replace `generated_data.mk`; typed game data remains
owned by the generated-data platform. The TMX adapter deliberately reuses the
ordinary map conversion path (`.mar` -> build-local `.bin` -> build-local
`.lz`) after generating its canonical private inputs.

### Captured-source discovery

Source-archive and isolated report consumers can reuse the existing Python
validation/rendering seam without starting Git inside their execution context:

```python
records = manifest.load_discovery(
    manifest_path, tracked_sources=frozenset(captured_regular_git_paths))
content = manifest.render_discovery_makefile(records)
```

For a declared private output, the compatible adapter combines those same
validators and renderer with the ordinary logical build-root rule:

```python
relative_output, content = manifest.render_discovery_artifact(
    manifest_path, "build/generated/asset-discovery/selected.mk",
    tracked_sources=frozenset(captured_regular_git_paths))
```

The logical input and returned path are repository-relative and must identify
a file below the build root, regardless of the caller's working directory.
Absolute logical inputs, malformed paths, the root directory itself, and
escaping paths reject. This adapter uses the shared output validator's
repository-relative mode; ordinary CLI filesystem outputs retain their
existing absolute-path and current-working-directory semantics.
The isolated executor must separately enforce the declared private output,
source-ancestor/symlink restrictions, capture and publication. The adapter
does not write into the source tree or redirect a guest path.

The caller must obtain that set from its authoritative Git capture, not from
the asset manifest's claims. This is an input to the existing membership
check, not a skip-validation flag: every source still needs a valid path,
existing nonsymlink file, required kind/ownership fields, and membership in
the captured set. Malformed sets and missing members reject. Filesystem
admission and actual source-consumption checks remain the isolated caller's
responsibility; supplying a name grants no filesystem access.

Omitting `tracked_sources` retains ordinary Git verification. No CLI option
exposes this input. Normal generation still performs its existing output-root,
symlink and atomic-write checks; a private-output caller writes the actual
renderer result through its own declared output boundary. Importing the module
anchors the build root to the real source root without probing a mutable output
tree.

The optional `custom-spell-effect` kind is documented in
[`custom_spell_effects.md`](custom_spell_effects.md). It is the only kind whose
presence is tied to `EXPANSION_CUSTOM_SPELL_EFFECTS`: a selected manifest
record with the flag off fails, and the flag on requires at least one record.
It emits build-local descriptors, per-frame canonical assets, the existing
`gSpellAssocData` entry, dependency/provenance inventory, and configuration
digests without adding a runtime parser or parallel item/effect table. The
default root manifest remains custom-spell-free; the committed source-build
proof is `assets/manifests/custom-spell-reference.json`.

## Adding a kind

1. First add or update the asset family's row and ownership/capacity evidence
   in `community_asset_coverage.md`.
2. Add one static kind implementation to `KIND_REGISTRY`. It must define an
   exact kind-owned `options`, `ownership`, and `resources` schema; validate
   sources/dependencies/ownership/provenance; render deterministic
   dependencies through the existing seam; and reject unsupported input.
   Ownership keys are global across all static kinds, so adapters that claim
   the same existing slot conflict even when their kinds differ.
3. Add positive and adversarial fixtures for paths, IDs, ownership collisions,
   resource limits, and generated drift/orphans.
4. Prove the real runtime consumer with the relevant modern debug and release
   scenario. A converter or editor preview alone is not integration evidence.

Do not make per-adapter hand table/linker edits outside the manifest's
rendered ownership path. Do not add a feature flag for this foundation:
default behavior is unchanged, so a disabled branch would only create dead
build logic.

## Compatibility, tester case, and rollback

**TC-ASSET-MANIFEST-060** uses a clean default checkout. Run
`make assets-generate`, `make assets-check`, and the modern debug/release
boot checks. The positive result is the real Chapter 2 map sources attached
to the real chapter asset-table object, with the existing Chapter 2 map still
selected at runtime. The control is the normal default build: no runtime
table/data or feature configuration changed. Host negative controls cover
unknown schema/kind/options, duplicate ID/ownership, invalid source path,
dangling dependency, malformed provenance, map capacity/selector conflict,
and missing/stale/orphan outputs.
For captured-source consumers, compare default and captured discovery rendering
on the same real manifest, then remove a required captured source, supply
malformed membership data, and introduce an escaping source path. The
`test_captured_discovery_*` cases in
`scripts/assets/tests/test_manifest.py` automate those positive/adversarial
checks without replacing normal CLI verification or output safety.
The `test_discovery_artifact_*` cases additionally reject absolute,
malformed/escaping logical outputs, require complete captured membership, and
compare actual rendered Make-include behavior while verifying input metadata
is unchanged. Repeat the same logical output from the repository root, a
nested working directory, and outside the repository: both its normalized
path and rendered content must remain identical.
The immutable probe's copied file timestamps may differ from an ambient
checkout: equivalence compares ordinary rendering and adapted rendering on
the same captured inputs, not unrelated wall-clock/materialization times.

**TC-ASSET-TMX-064-POSITIVE** freezes the generated Chapter 2 15x15 payload
and its map-load wiring. **TC-ASSET-TMX-064-DEFAULT** proves that the normal
default map-loader remains the only runtime behavior. **TC-ASSET-TMX-064-
NEGATIVE** exercises unsafe XML, unsupported layers/tilesets/GIDs, capacity,
ownership, source-path, stale, and orphan failures. No save reset or migration
is required. The adapter has no localization payload, configuration flag,
runtime XML dependency, or new ROM/RAM allocation beyond the existing map's
unchanged 452-byte uncompressed payload. Bare `make` remains the supported
modern AAPCS path; `make legacy` sees the ordinary generated map prerequisite
but makes no byte-identity claim. Revert the TMX record/source and generated
dependency edge together to return to a `.mar` source with no save,
configuration, or runtime migration.
