# Grado native visual inputs

These are unfinished optional project sources for
[#325](https://github.com/laqieer/fireemblem8-expansion/issues/325).
The default asset manifest is unchanged. This package is not a selectable
game, an accepted save profile or whole-game visual qualification.

The CH00 ordered MAR/JSON and CH02 `tmx-safe-v1` input preserve the original
16x12 creator tile layouts without importing external tileset images or
modifying their design masters. They use the existing manifest and conversion
modules, not a second map parser or asset registry.
The binary MAR and its JSON live under `graphics/map/layout/grado`, the
existing permitted source-asset root; the project manifest binds them there.
The tracked-artifact guard remains unchanged and is not waived by a manifest.

The real Fort family `3c003d3e` selects native asset slots 60/61/62:
`ObjectType5`, `MapPalette7`, `TileConfiguration5`. One creator metatile
occupies four u16 configuration entries. Consequently safe TMX uses
`4 * creator_tile + 1`, MAR uses `32 * creator_tile`, and the existing
`mar_to_map.py` produces `4 * creator_tile`. The native terrain reader shifts
that value right2; copying creator tile numbers directly would select the
wrong configuration/terrain.

Each converted map contains 386 bytes (two dimension bytes plus 192 u16
entries), within the existing 2,048-byte map buffer. This is generated-format
evidence, not proof of correct units, routes, collision, graphics or effects.
CH02 retains the existing `Ch2Map` generated consumer identity. CH00's
`PrologueMap` slot still needs its project consumer selection; selecting this
manifest alone does not replace the stock CH00 incbin or tileset settings.
Neither map should be treated as loaded gameplay content yet.

## TC-GRADO-NATIVE-VISUALS-001

**Issue and scope:** #325, native-map source subcase of `TEST-ASSET-MAP`.
The original portrait/visual-source/decreasecolor contracts remain separate
open work in that issue.

**Prerequisites:** repository root, Python3 and the existing asset sources.
No ROM, alternate project, save or external art installation is required for
this source-only case. Use ordinary generated output under
`build/.../generated/assets`; do not hand-edit it.

1. Run
   `python3 -B -m unittest scripts.assets.tests.test_grado_visuals -v`.
   Authors also stage their inputs and run
   `python3 -B scripts/artifact_guard.py --index`; candidate CI checks the
   immutable committed revision.
2. The test generates the selected manifest through the public asset command
   and converts both MAR inputs with the original `scripts/mar_to_map.py`.
   Inspect native dimensions 16x12, total 386 bytes, four-entry alignment and
   the independently worked source samples, including CH00 altar/stair and CH02
   road/door/dais positions.
3. Generate default then Grado again in that same owned output directory
   without cleaning. The actual default CH02 map is 15x15 with initial native
   value 520; selected Grado is 16x12 with initial native value 2604.
   No stale selected geometry may leak into the default or next selection.

**Controls:** absent source input fails explicitly. Omitting the creator
four-entry conversion must fail the native-value/alignment checks rather
than being accepted from dimensions alone. The unselected/default command
uses the original framework assets. Existing adapter malformed XML,
ownership, resource and generated-output controls remain unchanged.

**Interactions and compatibility:** existing asset manifest, chapter asset
table ownership, safe TMX and ordered MAR/JSON only. No new framework flag,
save bytes/epoch, persistent RAM or default C selection. Future selected
code/chapter settings must agree with these exact owners and the Fort tuple.
Conflicting ownership is rejected by the existing validator.

**Cleanup:** the test removes only its owned temporary build directory.
Master sources and default tracked assets are never rewritten.

### Native map/configuration subcase

Run
`env -u GBA_PLAYTEST_HOST_ONLY python3 -B -m unittest tools.gba-playtest.tests.test_grado_visuals_native -v`
with the supported ARM toolchain and libmGBA. Explicit host-only CI skips this
module; a skip is not native evidence. It reuses the existing module-ROM
startup, fixed IWRAM layout, tool selection and semantic capture route.
The existing `tools/gbagfx/gbagfx` compressor is also required; if missing,
build that existing helper with `make -C tools/gbagfx`.
The existing debug ARM-enabled `expansion-modern-linker-check` invocation
also executes it in candidate and master Build CI, with the host-only switch
removed. A parsed Make execution-contract test preserves that binding.

The owned fixture supplies only native chapter/table inputs. Original
`GetChapterMapPointer`, `UnpackChapterMap`, `InitBaseTilesBmMap`,
`GetTrueTerrainAt` and BIOS copy/decode execute against the actual generated
maps and assembled Fort configuration. Both maps must bind their declared
table slots, decode 16x12 and report the worked native values and real
wall/floor/throne/pillar/stair/road/door terrain. The CH00 `(12,1)` sample is
`TERRAIN_STAIRS`, not the adjacent northern door. Unexpected SRAM/extra-map/
audio paths trap with an explicit failing phase, never success stubs.

The unscaled-data control reaches the same consumers and completion but
reports 426/499/623/719/452 and terrain 1/0/0/1/0 for CH00, not the correct
configuration/terrain; CH02 remains correct. The test captures actual ROM/
EWRAM/IWRAM sizes and retains module resource limits. Repeat both positive
and unscaled inputs as owned type0 and actual gbagfx-produced GBA LZ77
carriers for maps and Fort configuration. The original decode/BIOS consumers
must produce the same native records from both formats.

The source-owner subcase also compiles the original
`src/data/const_data_chapter_maps.c` through its public native Make target,
preprocessor and real `INCBIN_U8` sources, including only that owner's
canonical generated dependency fragment. The fixture's existing `-R`
preprocessor remap selects the owned CH00 carrier and declares it as an
input; CH02 uses the manifest's existing generated path. Native readers
consume the original `PrologueMap`, `Ch2Map` and `TileConfiguration5`
symbols directly. The same correct/unscaled semantics must hold. Without
the fixture remap, the actual original CH00 owner remains 15x10, not the
selected 16x12 derivative.

This proves actual C/incbin producer integration, not activation of a
supported project: chapter/table inputs and the remap entrypoint remain
owned by the isolated module. The manifest alone still does not select
CH00 or change its native tileset settings.

The display subcase uses the original `ObjectType5` graphics and
`MapPalette7` palette, native decompression, `RenderBmMapOnBg2`, background
and LCD flushes, and real GBA video. Its explicit visible-fog/chapter inputs
remain fixture-owned. At frame 30, libmGBA captures the actual 240x160
framebuffer plus 20 visible-pixel probes for CH00 and 16 for CH02.
Expected colors are independently derived from the worked map indices and
the public 4bpp tile, flip and BGR555 formats, including libmGBA's bit
replication; they are not copied from the captured framebuffer or native
tilemap result.

Both positive maps must match those colors. The unscaled CH00 control must
match its different input-derived pixels and have a different framebuffer;
CH02 remains pixel/framebuffer-identical. These are native emulator pixels,
not a CPU-reconstructed screenshot or a refreshed golden hash. Supplementary
PNGs can be exported through libmGBA's existing screenshot API. They depict
instrumented, isolated native modules, not a selected supported game or
manual artistic acceptance. A game profile, production movement/EVT coordinates,
portrait/static-art consumers and complete visual qualification remain open.

The mobility subcase links the existing generated `movecost` synthetic
object for CommonT2, Civilian and Fly normal profiles. It runs the original
terrain lookup, movement-cost setup and handwritten ARM flood code, loaded
by `StoreRoutinesToIRAM`; the actual dispatch pointer must match that
relocation inside IWRAM. Aligned padded map rows are fixture-owned input,
not a replacement flood routine or exposure of the private map initializer.

For each chapter, inspect all 192 cells for the three real profiles plus
an enemy-collision control. Starts are CH00 `(2,4)` and CH02 `(7,4)`; the
owned enemy `0x81` blocks CH00 `(4,3)` or CH02 `(7,5)` against actor index
`1`. Independent weighted-path expectations use only converted map bytes,
the original Fort terrain lookup and typed movement-cost JSON, never
the native movement result. The ordinary reachable blocker becomes
unreachable with collision; removing the actor collision input must fail.
Unscaled CH00 changes its native costs while every CH02 matrix stays equal.
Existing map records, pixel expectations and frame/resource bounds remain.

**Retained route warning:** in the current terrain state CH02 `(7,10)` is
unreachable from `(7,4)` for the ordinary profile, independently confirmed
by source/native cost matrices. Using adjacent `(7,5)` for an owned
collision control does not fix that source route or qualify actual EVT
coordinates. Real project classes/units, events, door changes and playable
routes remain outside this instrumented module.

**Not yet qualified:** selected-game map/palette/movement/coordinate
consumers and screenshots, required early portrait packages,
title/BG/CG carriers and normal/crossfade limits. In particular old ASCII
deployment/cortege points can land on real walls/doors in the accepted master;
EVT must establish actual coordinates and route safety after preflight.
These warnings are retained, not converted into approval or a generic human
review gate. Remaining consumer/effect evidence and precise material artistic
criteria still block issue completion.
