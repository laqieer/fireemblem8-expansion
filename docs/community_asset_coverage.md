# Community asset build coverage catalog

This is the authoritative coverage and ownership audit for the asset-authoring
roadmap in issue #59 and Discussion #47. It describes the current tree; it
does not add an asset registry, converter, runtime module, or a claim that an
external editor can integrate an asset with this project.

Use this catalog before proposing an asset adapter. The adapter must preserve
the listed owning seam or replace it through the single generated ownership
mechanism planned by issue #60. It must not add another hand-maintained table,
linker list, or ID namespace.

## Status and boundary vocabulary

| Status | Meaning |
| --- | --- |
| **Supported** | A project-owned committed source, deterministic conversion/build path, and runtime consumer already exist. Adding a distinct asset can still require manual registration. |
| **Partially supported** | The low-level source/conversion/runtime pieces exist, but no one source-owned package/manifest owns every registration or capacity check. |
| **Manual-only** | Runtime consumes the asset family, but authoring requires direct source/table/linker edits with no project-owned community-format adapter. |
| **Runtime capability missing** | A requested community asset needs a new engine/data capability, not merely conversion. |
| **Out of scope** | The named input is an external tool format or workflow and is intentionally not a normal-build dependency. |

An authoring file is a compile-time input, never a runtime format. In
particular, neither a TMX map, a formatted portrait sheet, an Event Assembler
event file, nor an editor battle-animation package may be passed to the ROM at
runtime. Successful preview, import, or export in FEBuilderGBA, Event
Assembler, Tiled, or another editor does not establish **Supported** status.
Support requires a committed project-owned source package, deterministic
validation/conversion, the complete registration/linker wiring, and a runtime
consumption case.

## Index

| Family | Current status | Current owning seam | Next owner for the gap |
| --- | --- | --- | --- |
| Chapter map layouts | Supported | Chapter asset-table indices and map data | #64 TMX safe subset |
| Map changes | Manual-only | Chapter event/map-change data | Future `map-change schema/adapter` after #60 |
| Tilesets and map configuration | Partially supported | Chapter asset table plus graphics rules | Future `tileset package` after #60 |
| Tile animation | Manual-only | Chapter asset table and map-animation data | Future `tile-animation schema/adapter` after #60 |
| Portraits, minimugs, and cards | Manual-only | `portrait_data[]` and portrait symbols | #63 after #60 |
| Battle animations | Manual-only | `banim_data[]`, class definitions, and banim linker list | #62 after #60 |
| Spell and magic animations | Runtime capability missing | Per-effect battle/magic code and data | #61 after #60; it remains Needs design |
| Backgrounds and TSA/tilemaps | Partially supported | Per-screen data and graphics rules | Future `background/TSA package` after #60 |
| Icons and map sprites | Partially supported | Per-consumer source/data plus graphics rules | Future `icon/map-sprite package` after #60 |
| Palettes | Partially supported | Per-consumer symbols and build rules | #60 ownership metadata, then family adapters |
| Music and SFX | Partially supported | `gSongTable`, M4A, and song sources | Future `audio package` after #60 |
| Fonts | Partially supported | Font data/link sections and locale font pipeline | Future `font package` after #60 |
| Localized UI graphics | Partially supported | Locale generator, generated registry, and locale config | Future `localized UI package` after #60 |

The future names in this table are deliberately narrow gaps, not accepted
implementation issues. They must be split and contracted before work begins.

## Common source, conversion, and build rules

The project already owns useful low-level conversion primitives:

| Concern | Current contract | Evidence |
| --- | --- | --- |
| Indexed graphics | Committed indexed PNG can produce build-local `.4bpp`; family-specific rules constrain tile counts where the consumer requires one. | `graphics_file_rules.mk` |
| Palette conversion | Plain `.pal` produces a build-local `.gbapal`; `.agbpal` remains a binary canonical source only when its encoding/data shape requires it. | `docs/banim_asset_extraction.md`, `graphics_file_rules.mk` |
| Tilemaps/TSA | Headerless `.map.bin` and headered bottom-to-top `.tsa.bin` are distinct runtime formats. The extension and consumer, not byte shape, decide which is correct. | `docs/tsa_audit.md`, `docs/dump_extraction_plan.md` |
| Image/TSA pair generation | FETSA rules derive per-screen image and TSA outputs from PNG with explicit per-asset options. | `graphics_file_rules.mk` |
| LZ compression | Committed assets are canonical/decompressed inputs; `.lz` is build-local. When a legacy stream must reproduce exactly, a per-target `LZ_FLAGS := -mindist N` records the needed setting. | `docs/lz_suffix_diagnostic.md`, `Makefile` |
| Typed game data | Generated data has deterministic validate/generate/check/test patterns, but it is not an asset-package registry and must not be duplicated. | `docs/generated_data.md`, `generated_data.mk` |

No primitive above registers a new asset with a runtime table. A family is not
fully integrated until its build product is wired through its row below.

## Family coverage matrix

Unless a row explicitly says otherwise, its evidence location is the matching
repository source or documentation named in [Audit evidence and maintenance](#audit-evidence-and-maintenance),
and its provenance boundary is the one in
[Provenance, licensing, and external-tool boundary](#provenance-licensing-and-external-tool-boundary).
Those common requirements apply to every family below; each row's validation
and lane cell records the family-specific coverage in addition to them.

### Chapter map layouts

| Field | Current coverage |
| --- | --- |
| Community input | `tmx-safe-v1`: the documented finite, orthogonal, one-layer Tiled TMX subset in `docs/tmx_map_layouts.md`; Tiled itself remains optional. |
| Canonical source/build product | A manifest-owned TMX generates ignored `.mar`/JSON inputs, then `MARTOMAP` (`scripts/mar_to_map.py`) emits the existing `.bin` payload and normal rules compress it. |
| Runtime consumer/seam | `GetChapterMapPointer` and map loading consume `struct ROMChapterData.map.mainLayerId` through `gChapterDataAssetTable`; `src/bmmap.c` loads the selected resources. |
| IDs and limits | Chapter map fields and asset-table indexes are `u8`; the adapter validates manifest ID/ownership, the 1..255 dimensions, 4096-GID mapping, and the 2048-byte map-buffer capacity. |
| Validation and lane | Deterministic manifest/adapter checks cover syntax, ownership, source safety, drift, and adversarial TMX. Modern release/debug are the supported integration lanes; archival matching is not an authoring contract. |
| Status/gap | **Supported** for `tmx-safe-v1` only. Map changes, tileset packages, objects, transforms, external tilesets, and other editor exports remain separate unsupported contracts. |

### Map changes

| Field | Current coverage |
| --- | --- |
| Community input | None. An editor map-change export is not a source-owned input contract. |
| Canonical source/build product | Existing change data is hand-owned chapter/map event data and binary/typed resources selected by chapter data. |
| Runtime consumer/seam | `struct ROMChapterData.map.changeLayerId` resolves through `gChapterDataAssetTable`; event/map code consumes map-change records. |
| IDs and limits | The change-layer selector is `u8`; map-change rectangle, tile count, event ordering, and map bounds have no common validator. |
| Validation and lane | Existing source compiles in its linked lane; no focused map-change package validation exists. |
| Status/gap | **Manual-only**. A future `map-change schema/adapter` must follow #60 and define event semantics, bounds, ordering, and chapter wiring. |

### Tilesets and map configuration

| Field | Current coverage |
| --- | --- |
| Community input | Indexed PNG and palette inputs are usable by current low-level graphics rules; no tileset package format is accepted. |
| Canonical source/build product | Map object PNGs produce `.4bpp`; the rules record family-specific capacities such as `ObjectType1.4bpp` through `ObjectType10.4bpp` at up to 1024 tiles. Palette/configuration data remains per-resource source. |
| Runtime consumer/seam | `src/bmmap.c` resolves `obj1Id`, `obj2Id`, `paletteId`, and `tileConfigId` through `gChapterDataAssetTable`. |
| IDs and limits | The selectors are `u8`; the PNG rules are the only current mechanical tile-count checks. Tile configuration format, palette-slot allocation, and combined VRAM budget are not owned by a shared validator. |
| Validation and lane | Graphics rules are build dependencies; runtime selection is shared by modern and archival code. |
| Status/gap | **Partially supported**. #60 must represent ownership/limits; a future `tileset package` owns user-facing packaging and complete registration. |

### Tile animation

| Field | Current coverage |
| --- | --- |
| Community input | None. |
| Canonical source/build product | Existing frame/palette animation data is hand-authored data. |
| Runtime consumer/seam | `objAnimId` and `paletteAnimId` resolve through `gChapterDataAssetTable`; `src/bmio.c` is a current consumer. |
| IDs and limits | Both selectors are `u8`; frame cadence, palette range, source tile capacity, and DMA/VRAM budget lack a common declarative validator. |
| Validation and lane | Normal compile/link coverage only; no package validator or isolated runtime scenario exists. |
| Status/gap | **Manual-only**. A future `tile-animation schema/adapter` after #60 must define frame format, shared palette rules, resource budgets, and map binding. |

### Portraits, minimugs, and cards

| Field | Current coverage |
| --- | --- |
| Community input | No formatted portrait-sheet importer is owned. Community 128x112 portrait packages may be useful reference inputs but are not accepted project source today. |
| Canonical source/build product | Existing portrait graphics/palettes and declared symbols are consumed by the hand-written `portrait_data[]` table. |
| Runtime consumer/seam | `GetPortraitData` in `src/face.c` indexes `portrait_data[]`; `struct FaceData` selects tiles, minimug/chibi, palette, mouth, card, and blink metadata. The typed resolver only chooses existing IDs; it does not import graphics. |
| IDs and limits | `docs/portrait_resolver.md` validates full portrait IDs `1..0xAC` and generic minimug IDs `0x7F00..0x7F07`. `struct FaceData` plus the existing OBJ palette/tile loading path are the real resource contract; no generic package validator checks sheet geometry, card/minimug presence, or complete symbol wiring. |
| Validation and lane | Resolver validation covers references, not asset conversion or registration. Existing assets are linked normally; no generated portrait table exists. |
| Status/gap | **Manual-only**. #63 must generate every required symbol and `FaceData`/ID wiring through #60's single ownership mechanism. |

### Battle animations

| Field | Current coverage |
| --- | --- |
| Community input | No project-owned FEditor/FEBuilder text/PNG or binary package adapter exists. |
| Canonical source/build product | Existing component sources include indexed animation PNGs, palettes, OAM binaries, modes, and motion assembly. Build rules derive graphics/compression products; `scripts/arm_compressing_linker.py` owns selected compressed script wrapping. |
| Runtime consumer/seam | `struct BattleAnim banim_data[]` in `src/banim_data.c` provides mode, script, OAM, and palette pointers. Class/unit animation definitions select entries; `linker_script_banim.txt` determines compression/link order. |
| IDs and limits | `struct BattleAnim.abbr` is 12 bytes. The effective resource contract includes table index, class references, script/OAM shape, palette slots, VRAM/OAM use, and linker ordering; no one validator currently checks all of them as a package. |
| Validation and lane | Existing image rules and linker build the current data in both lanes. `docs/banim_asset_extraction.md` is extraction history, not a current package authoring API. |
| Status/gap | **Manual-only**. #62 owns complete package conversion, table/class/linker generation, capacity checks, and one runtime consumption case after #60. |

### Spell and magic animations

| Field | Current coverage |
| --- | --- |
| Community input | None. Existing effect code/data is not a generic custom-spell import format. |
| Canonical source/build product | Per-effect graphics, palettes, TSA, scripts, and C procedures are maintained alongside `src/banim-efxmagic-*.c` and other battle-effect code. |
| Runtime consumer/seam | Battle effect dispatch, procedures, and graphics/palette registration calls own effect semantics; assets alone cannot create a new effect. |
| IDs and limits | Effect commands, battle procedure ABI, VRAM, OAM, BG layers, palette slots, DMA timing, ROM/RAM, and interaction with battle animation state are runtime constraints. They have no generic declarative schema. |
| Validation and lane | Existing effects compile/link; there is no source-owned custom-spell package validator or bounded module profile. |
| Status/gap | **Runtime capability missing**. #61 must first freeze the optional runtime ABI, resources, save/config decision, positive case, and disabled control; it must not be folded into a converter. |

### Backgrounds and TSA/tilemaps

| Field | Current coverage |
| --- | --- |
| Community input | Indexed PNG is accepted by per-asset FETSA rules; committed `.tsa.bin`/`.map.bin` assets are source forms for their documented consumers. No general background package is accepted. |
| Canonical source/build product | `graphics_file_rules.mk` derives image/TSA pairs such as `.feimg*.bin` and `.fetsa*.bin`; regular graphics and compressed forms follow their per-consumer rules. |
| Runtime consumer/seam | Per-screen/battle/map animation data points at image, palette, and TSA resources. Headered TSA uses `TmApplyTsa`/`CallARM_FillTileRect`; raw tilemaps are separate consumers. |
| IDs and limits | Headered TSA has width/height bytes and bottom-to-top entries; raw maps are `u16` top-to-bottom entries. Tile, palette, BG size, and VRAM constraints are consumer-specific; the historical TSA audit is evidence, not an up-to-date general capacity validator. |
| Validation and lane | Current conversion is deterministic when a rule exists. Preview tooling is review-only and does not establish runtime registration. |
| Status/gap | **Partially supported**. A future `background/TSA package` after #60 must retain the header distinction and declare consumer, dimensions, tile/palette budget, compression, and registry seam. |

### Icons and map sprites

| Field | Current coverage |
| --- | --- |
| Community input | Indexed PNG is accepted where a corresponding graphics rule exists; no generic icon or map-sprite package exists. |
| Canonical source/build product | `graphics/unit_icon/` and map graphics are converted to `.4bpp`; move-icon rules contain explicit tile limits, including a 256-tile Dancer sheet. |
| Runtime consumer/seam | Per-consumer icon arrays, map-unit graphics configuration, and UI/map code own registration rather than a shared registry. |
| IDs and limits | Rule-specific tile limits exist, but icon IDs, palette allocation, OBJ tile ranges, animation frame timing, and per-screen VRAM/OAM budgets are distributed across consumers. |
| Validation and lane | Build rules validate conversions that they own; no cross-consumer package validator exists. |
| Status/gap | **Partially supported**. A future `icon/map-sprite package` after #60 must split icon, moving-unit, and map-object contracts where their runtime resources differ. |

### Palettes

| Field | Current coverage |
| --- | --- |
| Community input | `.pal` is a current project source form; special `.agbpal` data is retained where plain conversion is not lossless or the resource contains an explicitly audited non-palette payload. |
| Canonical source/build product | Build rules derive `.gbapal`; a palette can be shared, cycled, or selected independently from image data. |
| Runtime consumer/seam | Each consuming graphics/effect table or direct registration call owns its palette pointer and target slot. |
| IDs and limits | A 4bpp palette unit is 16 colors/32 bytes. Palette-slot ownership, cycling groups, high-bit representation, and hidden trailing data require consumer-specific evidence; they are not validated by filename or adjacency. |
| Validation and lane | `docs/banim_asset_extraction.md` documents the current audit rules. Build conversion does not prove a palette is bound to every intended consumer. |
| Status/gap | **Partially supported**. #60 must carry palette ownership/provenance metadata; family adapters must validate slot/cycling/binding rules instead of treating palette PNG embedding as universal. |

### Music and SFX

| Field | Current coverage |
| --- | --- |
| Community input | MIDI is accepted for existing song rules; direct-sound sources use the existing audio toolchain. No generic community audio package/metadata contract exists. |
| Canonical source/build product | `sound/songs/midi/*.mid` becomes assembly through `mid2agb`; `songs.mk` records per-song voicegroup, reverb, priority, and volume options. |
| Runtime consumer/seam | `sound/song_table.s` owns `gSongTable`; M4A consumers read `gSongTable` and `gMPlayTable`, with additional context tables such as world-map BGM routing. |
| IDs and limits | Song table order/index is the current ID contract. Voicegroup, sequence/player allocation, priority, reverb, sample memory, and simultaneous-channel budget are not exposed through a single package validator. |
| Validation and lane | Existing conversion/linking works for registered entries. A MIDI that converts successfully is not registered until song-table and routing ownership are complete. |
| Status/gap | **Partially supported**. A future `audio package` after #60 must generate/validate the sequence and table/routing ownership and declare sound-memory/player limits. |

### Fonts

| Field | Current coverage |
| --- | --- |
| Community input | The CJK locale pipeline has explicit pinned source/provenance and deterministic generated aggregate assets; it is not a general arbitrary font importer. Legacy font data is separately owned source/data. |
| Canonical source/build product | CJK aggregate codepoints, widths, and 2bpp glyph payloads are committed canonical assets and are `INCBIN`ed into locale sections by `src/data/localized_font_data.c`. |
| Runtime consumer/seam | `localized_font` uses locale/style tables and the generated payloads. The locale mask controls whether the corresponding data is linked. |
| IDs and limits | CJK glyphs are 16x16, 64-byte 2bpp bitmaps with widths in `1..16`; codepoint/width/bitmap cardinalities are statically asserted. `docs/cjk_fonts.md` records the current generated payload budget and provenance checks. |
| Validation and lane | `cjk-fonts-check` and its tests verify the project pipeline without invoking external tools. The specialized FEBuilderGBA maintainer gate is optional and does not make editor export a normal source contract. |
| Status/gap | **Partially supported**. A future `font package` after #60 must decide accepted source fonts, license/provenance, rasterization determinism, glyph coverage, section budgets, and locale interaction. |

### Localized UI graphics

| Field | Current coverage |
| --- | --- |
| Community input | No general locale UI graphics package is accepted. The existing locale sources and localization generator are project-specific pipeline inputs. |
| Canonical source/build product | Localized graphics are generated/converted under `graphics/localized_ui/`; `src/data/localized_eu_ui_graphics.c` `INCBIN`s the selected build products and records the runtime override registry. |
| Runtime consumer/seam | Locale-specific UI/effect consumers receive overridden graphics through the generated registry; locale configuration controls whether the data is linked. |
| IDs and limits | The locale mask and per-resource runtime registration are the current control surface. Asset dimensions, palette slots, compression, text-fit, and per-locale ROM budget are resource-specific rather than a generic package schema. |
| Validation and lane | Localization and font checks validate their existing pipelines. A translated/exported UI image is not registered merely because an editor can produce it. |
| Status/gap | **Partially supported**. A future `localized UI package` after #60 must integrate locale IDs, resource replacement ownership, text-fit/resource budgets, provenance, and locale-enabled/disabled cases. |

## Dependency and conflict matrix

| Concern | Required relationship | Conflict or prohibited shortcut |
| --- | --- | --- |
| Common ownership | Every adapter depends on #60 and uses its one versioned manifest/registry. | Per-adapter hand lists, a second ID namespace, or generated output edited by hand. |
| TMX layouts | #64 depends on #60 and this layout row. | Claiming all TMX layers, transforms, external tilesets, encodings, or editor exports are supported before its safe subset validates them. |
| Portrait packages | #63 depends on #60 and the `FaceData` row. | Generating only PNG conversion while leaving IDs, symbols, cards/minimugs, or table entries manual. |
| Battle-animation packages | #62 depends on #60 and the banim table/class/linker row. | Registering images without the modes/script/OAM/palette/class/linker contract. |
| Custom spells | #61 depends on #60 but remains a separate runtime module. | Treating a magic package as a converter-only change or hiding a runtime extension in the generic asset framework. |
| Generated data | Existing generated-data sources can provide typed references. | Replacing generated-data or duplicating its validation with an unrelated asset registry. |
| Localization/fonts | Locale/asset adapters must preserve locale masks, renderer data, and source provenance. | Making FEBuilderGBA, a GUI, network access, an installed font, or a commercial asset a normal build dependency. |
| Save/configuration | This audit changes neither. Future runtime modules must state their own compatibility decision. | Inferring a save/config change from a converter-only change, or silently changing configuration identity. |

There are no conflicts with the existing starter-feature flags, casual defeat
policy, or AoE module: this catalog is documentation only. There are no
dependents outside the roadmap listed above.

## Provenance, licensing, and external-tool boundary

Source ownership is a required field for every future asset record: identify
the source author/origin, license or permission, modifications, and any
third-party tool/version used to create an intermediate. A structural
validator, a clean build, an editor preview, a ROM import/export, or a
byte-for-byte round trip does **not** grant redistribution permission.

Use local, versioned project sources and deterministic project-owned tools in
normal builds. FEBuilderGBA, Event Assembler, Tiled, and similar tools may be
used as optional reference, migration, preview, or import/export tools. Their
native formats, generated event files, and editor-specific metadata remain
external unless a future adapter accepts a documented subset and has its
project-owned validation, conversion, registration, and runtime evidence.

Do not copy external tool code, a clean ROM, editor output containing
unlicensed game data, or community assets into this repository merely to make
an adapter test pass. Existing historical extraction documents explain how
already-owned project assets were represented; they do not license new input.

## Tester-facing audit procedure

### TC-ASSET-AUDIT-059 — verify an asset family is catalogued before adapter work

| Field | Contract |
| --- | --- |
| Purpose and issue | Confirm that a proposed asset family has a current, evidence-backed ownership row before it can become a #60 adapter or an asset-specific follow-up. Origin: #59. |
| Profile/artifact | Clean source checkout on the default documentation-only profile. No ROM, save, external editor, or proprietary asset is required. |
| Prerequisites | Read this catalog and the current source path cited by the candidate row. Start with no unreviewed generated output; do not use a GUI export as evidence. |
| Actions | 1. Locate the family in the index. 2. Read its canonical source/build, runtime seam, IDs/limits, validation, and status/gap cells. 3. Inspect the cited source/doc evidence. 4. Run `python3 scripts/check_docs.py --check` from the repository root. |
| Expected result | The family has exactly one row with a current ownership seam, stated resource-limit evidence or an explicit missing validator, modern/archival boundary, provenance boundary, and a precise owning issue or named future gap. The documentation check succeeds. |
| Negative control | A successful editor preview/export, image conversion, or compiled binary without the row's registration seam is **not** evidence of integration and must retain Manual-only, Partially supported, or Runtime capability missing status. |
| Interactions/save | Depends on #59 and informs #60 through #64. It changes no save data, configuration, ROM, RAM, or runtime behavior. |
| Automation | `scripts/check_docs.py --check` deterministically checks the new document's inventory entry and links. Completeness of a future adapter's semantic resource contract is a maintainer audit against this procedure until #60 provides its manifest validator. |
| Reset/limitations | No reset beyond discarding uncommitted documentation edits. This case does not prove a ROM consumes an asset; each follow-up adapter must add its own positive runtime case and relevant disabled/default control. |

## Handoff contract for issue #60

Issue #60 is the next implementation layer, not an implicit acceptance of all
editor formats. Its manifest/adapter design must:

1. use stable symbolic IDs and one statically registered asset-kind
   interface;
2. map each record to the existing owning table/linker/runtime seam named in
   this catalog;
3. validate source paths, declared dependencies, ownership conflicts,
   capacities, and provenance metadata before generating only ignored
   build-local output;
4. preserve Make dependencies from every committed source input to every
   generated build product;
5. fail closed for unknown kinds/options and unsupported editor constructs;
6. leave new runtime capabilities, especially spells, to independently
   contracted modules; and
7. keep normal builds independent of FEBuilderGBA, Event Assembler, Tiled,
   .NET, a GUI, network access, and archival agbcc tooling.

The initial proof asset in #60 must exercise a real row and its real
registration seam. It may not claim general format support, use an editor
export as a canonical source, or leave table/linker registration outside the
generated ownership contract.

## Audit evidence and maintenance

Primary evidence is the current repository: `Makefile`,
`graphics_file_rules.mk`, `songs.mk`, `linker_script_banim.txt`,
`src/data/data_8B363C.c`, `include/chapterdata.h`, `src/portrait_data.c`,
`src/banim_data.c`, `include/banim_data.h`, `sound/song_table.s`,
`src/data/localized_font_data.c`, and
`src/data/localized_eu_ui_graphics.c`. Supporting current or historical
documentation is named in the relevant rows.

The audit intentionally treats historical extraction/TSA reports as evidence
for format distinctions, not as proof that their point-in-time counts or
workflows are current. Refresh a row whenever a follow-up changes its
canonical source, registration seam, capacity validator, provenance contract,
or status. Update its tester-facing case in the same change.
