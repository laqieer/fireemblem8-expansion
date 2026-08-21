# Asset manifest framework

Issue #60 provides one versioned, source-owned asset-manifest framework for
future adapters. It is infrastructure, not an editor importer or a runtime
asset registry. The current version proves one existing Chapter 2 map layout
without changing the selected map, ROM data, save format, configuration
identity, localization, or runtime behavior.

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
      "kind": "chapter-map-layout",
      "sources": ["committed/source.mar", "committed/metadata.json"],
      "dependsOn": [],
      "options": {"format": "mar", "compression": "lz77"},
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
`graphics/map/layout/Ch2Map.mar` and `Ch2Map.json` pair. Its
`chapter-map-layout` adapter verifies all of the following before rendering:

1. The metadata ID and dimensions match the declared symbol/resource data.
2. The Chapter 2 settings row selects `mainLayerId` 11.
3. `gChapterDataAssetTable[11]` is the existing `Ch2Map` symbol.
4. Dimensions fit the existing 2048-byte `gBmMapBuffer` contract.

The generated dependency fragment attaches both sources to the existing
`src/data/data_8B363C.o` object and the active modern equivalent
`$(MODERN_OUTPUT_DIR)/src/data/data_8B363C.o`. It does not emit C, assembly,
a linker list, or an asset lookup table. The pre-existing
`GetChapterMapPointer` and `InitChapterMap`/`UnpackChapterMap` path remain
the runtime consumers. This deliberately leaves #64 free to add a safe TMX
adapter through the same seam, while #62/#63 can add their own catalogue
seams and #61 remains a separate runtime design.

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
fragment/inventory and fails on stale, missing, or orphan files. The normal
Make include regenerates the dependency fragment before graph resolution when
the source manifest/framework changes, then exposes ordinary source
prerequisites for the actual owning object. Outputs are confined to ignored
`build/generated/assets/`, are disposable, and must never be hand-edited or
committed.

The framework does not replace `generated_data.mk`; typed game data remains
owned by the generated-data platform. It also does not change the ordinary
map conversion path (`.mar` -> build-local `.bin` -> build-local `.lz`).

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

No save reset or migration is required. The framework has no localization
payload, ROM/RAM allocation, or archival byte-identity effect. Bare `make`
remains the supported modern AAPCS path; `make legacy` remains an archival
lane and sees only an ordinary source prerequisite. Revert the manifest
include, framework, proof record, and docs together to restore the prior
dependency graph; no source asset, save, configuration, or runtime migration
needs reversal.
