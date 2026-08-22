# Safe TMX chapter map layouts

Issue #64 adds `tiled-tmx-map-layout`, a deterministic **compile-time** input
adapter. Tiled is optional authoring software, not a normal-build dependency:
the build uses only the repository's Python standard library and existing map
conversion/compression rules. The GBA ROM never parses XML, TMX, TSX, or a
Tiled project.

## Authoring contract: `tmx-safe-v1`

The source must be a tracked, repository-relative `.tmx` file and satisfy all
of these exact requirements:

| Area | Accepted form |
| --- | --- |
| XML | Starts with `<?xml version="1.0" encoding="UTF-8"?>`; UTF-8 XML 1.0 only, at most 512 KiB and 16 markup constructs. DTDs, entity syntax, comments, and `&` are rejected before XML expansion. |
| TMX producer | `version="1.10"` and `tiledversion="1.10.x"`. |
| Map | One finite `orientation="orthogonal"` map with `renderorder="right-down"`, `infinite="0"`, 1..255 width/height, and 16x16 metatiles. |
| Children | Exactly one inline tileset followed by exactly one layer. Groups, object layers, image layers, additional layers, properties, templates, and unknown XML are rejected. |
| Tileset | `firstgid="1"`, `name="fe8-metatiles-16px-4096"`, 16x16 tiles, `tilecount="4096"`, and `columns="64"`. It has no children: external TSX paths, images, tiles, transforms, and properties are unsupported. |
| Layer | Exactly `id="1" name="Main"` at the map dimensions, with one `<data encoding="csv">` child and no compression, chunks, or properties. |
| Cells | Exactly width*height ASCII-decimal CSV GIDs, with only ASCII space, tab, CR, or LF around tokens and at most 10 decimal digits per token. Every GID is 1..4096; `gid - 1` is the FE8 13-bit metatile ID. Zero, signed, overflowing, out-of-range IDs, and Tiled flip/rotation flag bits fail. |

The manifest record uses the exact options shown below. Its tileset identity is
an explicit mapping contract, not a graphic import: tile GID 1 maps to FE8
metatile 0 and GID 4096 maps to 4095. A tileset package that creates graphics,
palettes, or configuration is a separate future capability.

```json
"options": {
  "format": "tmx-safe-v1",
  "compression": "lz77",
  "layer": "Main",
  "tilesetId": "fe8-metatiles-16px-4096"
}
```

## Source workflow

1. Create a finite 16x16 orthogonal map under `assets/tmx/` using the table
   above. Do not add an image, external tileset, properties, secondary layer,
   objects, or transforms.
2. Add one `tiled-tmx-map-layout` record to `assets/manifest.json`, selecting
   an existing chapter's `mainLayerId`, table symbol, and resource capacity.
   The manifest is the only registration surface; do not add a table, linker
   list, or hand-maintained generated file.
3. Run `make assets-validate assets-generate assets-check assets-test`, then
   the supported modern build profile. Generated `.mar`, JSON, `.bin`, and
   `.lz` products live only below `build/generated/assets/tmx/`.
   `ASSET_OUTPUT_DIR` is intentionally fixed to `build/generated/assets`
   while this compile-time adapter has an INCBIN consumer, so a build cannot
   generate the stream in a location different from the C filename.

`CH2_MAIN_MAP` is the reference migration. Its generated `.mar` exactly
matches the prior Chapter 2 source payload; the existing converter then emits
the same 15x15 canonical map bytes selected by
`gChapterDataAssetTable[11]` and `GetChapterMapPointer`.

## Compatibility and boundaries

There is no feature flag, configuration identity change, save-format change,
generated-data schema change, localization ID/catalog change, or map-change
semantic change. The existing 2048-byte `gBmMapBuffer` capacity is validated
as a 2-byte header plus 2 bytes for every `u16` tile: 1023 cells (such as
31x33) fit exactly, while 32x32 requires 2050 bytes and fails before TMX
payload parsing. The reference map's dimensions and payload are unchanged.
Modern debug and release consume the same generated semantic map. The archival
lane can consume the ordinary generated prerequisite but is not an original-ROM
identity claim.

Map changes, objects, tileset graphics/configuration, tile animation, and
external editor packages are deliberately out of scope. Revert a map record,
its TMX source, and the generated dependency edge together to restore its
previous `.mar` source without a runtime or save migration.
