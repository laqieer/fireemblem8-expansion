# Formatted portrait packages

Issue #63 adds the `formatted-portrait-package` kind to the one versioned
asset manifest. It is a build-time adapter, not a runtime PNG loader, editor
integration, feature flag, ID namespace, or replacement for the typed
resolver in [`portrait_resolver.md`](portrait_resolver.md).

## Package contract

Each manifest record declares exactly one tracked package directory with:

* one indexed, non-interlaced 128x112 PNG sheet with exactly 16 palette
  entries, index 0 transparent, and no partial transparency;
* one `metadata.json` with version `1`, the stable existing FaceData ID and
  C symbol, blink kind, mouth/eye anchors, the complete fixed-frame layout,
  and an explicit `generated` or `existing-components` alias decision; and
* optionally, a same-name JASC-PAL (`JASC-PAL`, `0100`, 16 RGB rows) whose
  colors exactly match the PNG palette.

The tracked-artifact guard permits these source inputs only at
`assets/portraits/<package>/<package>.png` and the optional matching `.pal`
sidecar. It rejects alternate basenames, nested paths, other palette formats,
and all other PNG/palette locations under `assets/`; this is a package-source
exception, not a general generated-artifact or image allowance.

The fixed sheet grid is deliberately not inferred:

| Component | Rectangle `(x, y, width, height)` |
| --- | --- |
| Main face | `(0, 0, 80, 72)` |
| Minimug | `(80, 0, 32, 32)` |
| Open/closed eye frames | `(0, 72, 32, 16)`, `(32, 72, 32, 16)` |
| Closed/open mouth frames | `(64, 72, 32, 16)`, `(96, 72, 32, 16)` |

Every component rectangle must remain a multiple of 8 pixels in both axes.
Generated 4bpp data is emitted in GBA 8x8 tile order with the left pixel in
each byte's low nibble; it is never serialized in image scanline order.

The adapter rejects missing/multiple sheets, non-indexed/interlaced/wrong-size
PNG data, palette violations, JASC disagreement, unsafe or untracked paths,
incomplete metadata, invalid anchors/frames, duplicate IDs, duplicate
symbols, duplicate/cross-kind table ownership, and incomplete live FaceData
capacity. Package aliases are explicit: `generated` emits 4bpp components and
deterministic GBA LZ77 streams; `existing-components` is only a migration
choice that names the existing component symbols exactly.

`assets/portrait_registry.json` is the typed canonical source for the
contiguous 172-entry legacy FaceData table. `src/portrait_data.c` contains no
hand-maintained initializers; it includes the generated table. Character and
class generated-data validation reads this live manifest registration rather
than parsing a stale C table. The `EIRIKA_FORMATTED_PORTRAIT` package proves
the migration seam: it aliases the already-shipped Eirika components, so the
default resolver result, minimug, palette, blink state, IDs, and ROM behavior
are unchanged.

## Build and generated-output policy

```sh
make assets-validate
make assets-generate
make assets-check
make assets-test
make assets-clean
```

Generation writes only ignored `build/generated/assets/`, including the
FaceData include, component declarations, deterministic component products,
and the ordinary Make dependencies for the existing portrait data objects.
`assets-check` rejects missing, stale, and orphan output; `assets-clean`
removes only that generated subtree. Do not edit those outputs. Bare `make`
supports the modern AAPCS release lane; the generated C remains
source-compatible with the archival lane, but no byte-identity claim is made.

External tools such as FEBuilderGBA may create, preview, or cross-check a
sheet, but they are optional authoring tools: no .NET, GUI, external converter,
or third-party code is a normal build dependency. Structural validation does
not establish provenance, permission, or redistribution clearance; every
manifest record still requires origin, license/permission, modifications, and
tool metadata. `assets-validate` verifies those required, typed fields now.
The canonical release allowlist and archive provenance are generated from the
exact candidate commit during the ordinary commit/review release workflow; a
dirty worktree cannot truthfully claim that HEAD-only release evidence, so
this adapter neither synthesizes it nor blocks unrelated downstream work.

## Tester procedure: `TC-PORTRAIT-PACKAGE-063`

`expansion-modern-portrait-package-runtime-check` is a dedicated internal
test artifact, not a feature profile or configuration option. It alone
defines `FE8_PORTRAIT_PACKAGE_RUNTIME_TEST=1` and the private
`gPortraitPackageRuntimeProbe` instrumentation used to assert the Eirika
minimug, face, mouth, and eye path. Supported debug, release, and maximal
item-expansion builds omit that symbol and state entirely; production
debug-tools behavior and EWRAM budgets therefore remain unchanged.

Use a clean default modern checkout with no feature flags and no save reset.
Run the five asset commands above, build modern debug and release, then use
the normal Eirika face and minimug UI paths. The expected positive result is
that `GetPortraitData(2)` and the #35 resolver both select the same Eirika
FaceData ID and component pointers as the legacy default, including the
documented eye/mouth anchors and blink kind. The negative control is any
unchanged non-package portrait with the empty resolver-rule registry: its ID
and presentation remain unchanged. Host checks cover deterministic package
geometry, palette, IDs, generated bytes, dependencies, drift, cleanup, and
registry capacity; the in-emulator face/minimug exercise is the visual
confirmation.

`tools/gba-playtest/tests/test_portrait_package_runtime.py` automates the
deterministic portion: a clean New Game dialogue route validates the loaded
Eirika `FaceData` pointers and live palette/VRAM/OAM activity in debug and
release. The existing debug-only Unit Inspect seam separately invokes
`PutFaceChibi(2, ...)`, then the canonical face mouth and eye controls; its
bounded probe fields verify Eirika minimug VRAM/palette data plus mouth state
frame output and eye-control state `2`. Its release sibling proves all of those
debug-only probe fields remain zero.

This package adapter changes no save layout, configuration identity,
localization IDs/catalogs, or persistent resource allocation. It uses the
existing FaceData table, face VRAM/palette path, and resolver. Revert the
manifest record, canonical registry migration, generated include surface, and
documentation together to return to the prior hand-owned table without a save
migration.
