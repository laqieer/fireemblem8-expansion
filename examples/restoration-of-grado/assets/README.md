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
CH00 or change its native tileset settings. A game profile, movement,
palette/rendering and visual qualification remain open.

**Not yet qualified:** selected production map/palette/movement/coordinate
consumers, real target screenshots, required early portrait packages,
title/BG/CG carriers and normal/crossfade limits. In particular old ASCII
deployment/cortege points can land on real walls/doors in the accepted master;
EVT must establish actual coordinates and route safety after preflight.
These warnings are retained, not converted into approval or a generic human
review gate. Remaining consumer/effect evidence and precise material artistic
criteria still block issue completion.
