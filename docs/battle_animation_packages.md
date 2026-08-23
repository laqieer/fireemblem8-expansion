# Community battle-animation packages

Issue #62 adds `battle-animation-package`, a static version-1 adapter in the
sole asset-manifest `KIND_REGISTRY`. It imports reviewable text and indexed
PNG package metadata into the existing `BattleAnimDef`, `banim_data[]`, class
data, `linker_script_banim.txt`, and compressor ownership path. It does not
add a runtime registry, hand-owned second banim table, custom spell format,
engine command, feature flag, save change, localization payload, or
configuration identity change.

## Package and manifest

The manifest record has `kind: "battle-animation-package"` and
`options.format: "community-text-png-v1"`. Its tracked, repository-relative
sources are ordered as `package.json`, `script.txt`, then indexed frame PNGs.
The package schema string is `fe8.community-banim.v1`; all object keys are
closed, paths use NFC/POSIX separators and cannot be absolute, generated,
symlinked, escaping, or case-fold-colliding.

`package.json` has exactly these top-level keys:

```json
{
  "schemaVersion": "fe8.community-banim.v1",
  "id": "UPPER_SNAKE_CASE_MANIFEST_ID",
  "abbreviation": "package_animation_abbreviation",
  "animConf": "AnimConf_N",
  "class": "CLASS_SYMBOL",
  "weaponType": "ITYPE_DARK",
  "frames": [{"id": "lower_case_frame_id", "path": "graphics/banim/frame.png"}],
  "paletteVariants": ["default"],
  "resources": {
    "maxFrames": 1,
    "maxSheetTiles": 1024,
    "maxOamPerFrame": 32,
    "maxPaletteColors": 16
  }
}
```

V1 has exactly one palette variant: `paletteVariants` must be precisely
`["default"]`. The package may not name pre-existing motion, OAM, palette, mode, or linker
artifacts. The adapter derives all of those build-local products from the
declared PNG frames and text script, then registers them through the existing
compressing-linker object, additive `banim_data[]` entry, and generated
`BattleAnimDef`. The selected class must currently point to the declared
`AnimConf_*`; duplicate `AnimConf_*` ownership, IDs, source paths, or package
IDs fail before generation. `abbreviation` is at most 11 ASCII identifier
characters so it remains terminated in `struct BattleAnim.abbr[12]`.
`weaponType` must be present in the selected class's generated-data
`baseRanks`; the generated `BattleAnimDef` uses it as the resolver fallback.

## Text grammar v1

The file is UTF-8 with LF line endings. Blank lines and `#` comments are
ignored. Its first non-comment line is exactly `BANIM 1`. Every remaining
line is one of:

```text
mode normal|critical|ranged|dodge|standing
frame <decimal 1..255> <lower_case_frame_id> [left|right|both]
wait <decimal 1..255>
command start_attack_1|start_attack_2|hit_normal|hit_critical_1
command prepare_hp_deplete|wait_hp_deplete|start_dodge|end_dodge
command range_attack|shake_screen_heavily|shake_screen_slightly
sound sword_swing_short|sword_slash_air|step_heavy
loop <positive decimal count>
end
```

Every listed mode occurs exactly once, has a timed frame or wait, and ends
with `end`; expanded duration is capped at 65535 ticks. `loop` repeats only
the immediately preceding frame or wait group inside the current mode.
Commands map one-to-one to the named existing `banim_code_*` macros.

`left` and `right` create equal-offset but distinct left/right OAM payloads:
the selected side receives the frame while the opposite side receives a
padded terminator frame. `both` emits the frame in both payloads. The three
`sound` names are the complete v1 sound subset. They map one-to-one
to `banim_code_sound_sword_swing_short`,
`banim_code_sound_sword_slash_air`, and `banim_code_sound_step_heavy`.
No numeric opcode or sound ID, FEditor binary serialization, macro, include,
alias, path, transform, custom spell command, unknown command/sound,
malformed mode, or unterminated frame group is accepted.

## Images, palettes, and budgets

Each frame is a single-IDAT, non-interlaced PNG with indexed color type 3,
4-bit depth, standard compression/filter, a positive 32x32-block-aligned
geometry, one 1..16-color `PLTE`, and zero or one `tRNS` chunk. If `tRNS`
exists only palette index 0 may have zero alpha and every other declared
entry must be fully opaque (`255`). RGB/RGBA, interlaced,
over-color, 8x8-but-not-32px, cropped, scaled, rotated, or multi-IDAT PNGs
fail. Every frame must carry byte-identical `PLTE` and `tRNS` chunks, since
v1 emits exactly one runtime palette. The generator
converts the indexed pixels into 4bpp tile payloads, converts PLTE values to
the existing 15-bit palette layout, deterministically composes each frame into
left/right 32x32 OAM records, and emits mode offsets plus relocatable motion
assembly. Identical frame payloads share one emitted sheet: OBJ-VRAM budget
uses that deduplicated emitted-sheet set, while ROM budget separately covers
every emitted runtime product. It validates the derived 1024-sheet-tile,
32-OAM-entry, 16-palette color, 128-total-OAM, 32KiB OBJ-VRAM, and declared ROM bounds before
publishing.

Generation writes only ignored `build/generated/assets/banim/` products:
4bpp frames, palette payloads, left/right OAM, mode offsets, relocatable
motion assembly, `banim_data_entries.inc`, runtime symbol declarations,
`banim_defs.inc`, `banim_defs.h`, and a combined compressor-linker script.
The existing C source includes only generated entry/definition/symbol seams;
no public header includes generated content. The generated linker script
extends the committed base list and is consumed by the existing #67-locked
`banim/data_banim.o` publication path; it is not a second runtime linker.
`assets-generate` is write-if-changed/atomic; `assets-check` rejects missing,
stale, and orphan products.

## Workflow and tester case

1. Add only project-owned, licensed package metadata/art to a manifest record
   with complete origin, license, modification, and tool provenance.
2. Run `make assets-validate assets-generate assets-check assets-test`.
3. Build the supported modern debug and release configurations. Run
   `make expansion-modern-banim-package-runtime-check` for the dedicated
   libmGBA proof build. It removes only its named
   `build/banim-package-runtime/` output before building with
   `FE8_BANIM_PACKAGE_RUNTIME_TEST=1`; normal debug and release builds never
   define that macro.
4. `make legacy` remains an archival compiler/linker boundary, not a package
   byte-identity promise.
5. Do not manually edit `src/banim_data.c`, class tables, declarations, or
   `linker_script_banim.txt` for a package. Extend the adapter instead.

**TC-BANIM-PACKAGE-062** uses `LORM_SP1_PROOF` in a clean checkout. The
positive host automation validates its v1 text/PNG/package/class/linker
binding and asserts generated 4bpp/palette/OAM/mode/motion/linker payloads;
the default control retains `CLASS_EPHRAIM_LORD -> AnimConf_0` and its
original battle entry. Host negatives cover unsupported commands and sounds,
missing mode structure,
nonconforming PNGs, invalid resource declarations, duplicate ownership,
unsafe source paths, stale/orphan output, and atomic replacement.

`make expansion-modern-banim-package-runtime-check` exercises the dedicated
test-only `FE8_BANIM_PACKAGE_RUNTIME_TEST` seam. Its clean isolated ROM enters
Chapter 4's real `FIGHT` through the engine's scripted-animation lifecycle.
The test-only build makes that route scripted once so the shared combat input
can retain its ordinary event-skip timing; this macro is absent from every
normal build. A checkpoint immediately before `FIGHT` is the default negative
control: every test-only probe remains zero, so no normal boot/setup path can
select the alias. The real scripted route then selects `LORM_SP1_PROOF` exactly
once after the ordinary resolver has chosen its default mapping, records entry
only when the battle-animation lifecycle starts, and records completion only
from `EkrMainEndExec`. It asserts that selected alias index, five modes,
normal/total timing, generated script sound opcode, decompressed OAM and
palette payloads, the actual
`CLASS_MOGALL` class ID (`0x5F`), the unchanged
`CLASS_MOGALL -> AnimConf_90` default mapping, and one selection,
entry, generated-data consumption, and completion. The macro is absent from ordinary debug/release builds, so it
cannot create a production mapping or router. The generated alias remains
additive and the ordinary resolver remains unmodified.

For a local visual check, enter an Ephraim Lord lance battle in a modern debug
ROM and compare the standing, attack, critical, ranged, and dodge presentation
to the default build; no palette or frame layout may change. Screenshots are
supplementary only.

Rollback removes the manifest/package record and disposable generated
products together. No save migration, default behavior, or external editor
dependency is involved.
