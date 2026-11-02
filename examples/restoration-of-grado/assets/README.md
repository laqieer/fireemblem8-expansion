# Grado native visual inputs

These are unfinished optional project sources for
[#325](https://github.com/laqieer/fireemblem8-expansion/issues/325).
The default asset manifest is unchanged. This package is not a selectable
game, an accepted save profile or whole-game visual qualification.

The CH00 ordered MAR/JSON and CH02 `tmx-safe-v1` input preserve the original
16x12 creator tile layouts without importing external tileset images or
modifying their design masters. They use the existing manifest and conversion
modules, not a second map parser or asset registry.

The real Fort family `3c003d3e` selects native asset slots60/61/62:
`ObjectType5`, `MapPalette7`, `TileConfiguration5`. One creator metatile
occupies four u16 configuration entries. Consequently safe TMX uses
`4 * creator_tile + 1`, MAR uses `32 * creator_tile`, and the existing
`mar_to_map.py` produces `4 * creator_tile`. The native terrain reader shifts
that value right2; copying creator tile numbers directly would select the
wrong configuration/terrain.

Each converted map contains386bytes (two dimension bytes plus192 u16
entries), within the existing2,048-byte map buffer. This is generated-format
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
2. The test generates the selected manifest through the public asset command
   and converts both MAR inputs with the original `scripts/mar_to_map.py`.
   Inspect native dimensions16x12, total386bytes, four-entry alignment and
   the independently worked source samples, including CH00 altar/stair and CH02
   road/door/dais positions.
3. Generate default then Grado again in that same owned output directory
   without cleaning. The actual default CH02 map is15x15 with initial native
   value520; selected Grado is16x12 with initial native value2604.
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

The owned fixture supplies only native chapter/table inputs. Original
`GetChapterMapPointer`, `UnpackChapterMap`, `InitBaseTilesBmMap`,
`GetTrueTerrainAt` and BIOS copy/decode execute against the actual generated
maps and assembled Fort configuration. Both maps must bind their declared
table slots, decode16x12 and report the worked native values and real
wall/floor/throne/pillar/stair/road/door terrain. The CH00 `(12,1)` sample is
`TERRAIN_STAIRS`, not the adjacent northern door. Unexpected SRAM/extra-map/
audio paths trap with an explicit failing phase, never success stubs.

The unscaled-data control reaches the same consumers and completion but
reports426/499/623/719/452 and terrain1/0/0/1/0 for CH00, not the correct
configuration/terrain; CH02 remains correct. The test captures actual ROM/
EWRAM/IWRAM sizes and retains module resource limits. These owned raw type0
inputs exercise real native decoding, not final compressed/game-profile,
movement, palette or visual qualification.

**Not yet qualified:** selected production map/palette/movement/coordinate
consumers, real target screenshots, required early portrait packages,
title/BG/CG carriers and normal/crossfade limits. In particular old ASCII
deployment/cortege points can land on real walls/doors in the accepted master;
EVT must establish actual coordinates and route safety after preflight.
These warnings are retained, not converted into approval or a generic human
review gate. Remaining consumer/effect evidence and precise material artistic
criteria still block issue completion.
