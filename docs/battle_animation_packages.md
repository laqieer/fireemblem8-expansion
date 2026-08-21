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
  "abbreviation": "existing_banim_abbreviation",
  "animConf": "AnimConf_N",
  "class": "CLASS_SYMBOL",
  "runtime": {
    "modes": "banim/name_modes.bin",
    "motion": "banim/name_motion.o",
    "oamLeft": "banim/name_oam_l.bin",
    "oamRight": "banim/name_oam_r.bin",
    "palette": "graphics/banim/name.agbpal",
    "linkerInputs": ["existing linker_script_banim.txt entry"]
  },
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

The adapter checks that the runtime symbols name one existing `banim_data[]`
entry, every linker input is already owned by `linker_script_banim.txt`, and
the selected class currently points to the declared `AnimConf_*`. This
registers an additive `BattleAnimDef` hook and table alias without altering
the default class mapping. Duplicate `AnimConf_*` ownership, IDs, source
paths, or package IDs fail before generation.

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
loop <positive decimal count>
end
```

Every listed mode occurs exactly once, has a timed frame or wait, and ends
with `end`; expanded duration is capped at 65535 ticks. `loop` repeats only
the immediately preceding frame or wait group inside the current mode.
Commands map one-to-one to the named existing `banim_code_*` macros.

No numeric opcode, FEditor binary serialization, macro, include, alias,
path, transform, custom spell command, unknown command, malformed mode, or
unterminated frame group is accepted. Sound commands are intentionally not in
v1 because their command-to-sound semantics need a separately versioned
runtime policy; they fail rather than being guessed or dropped.

## Images, palettes, and budgets

Each frame is a single-IDAT, non-interlaced PNG with indexed color type 3,
4-bit depth, standard compression/filter, a positive 8x8-tile-aligned
geometry, one 1..16-color `PLTE`, and zero or one `tRNS` chunk. If `tRNS`
exists it must have exactly one transparent color. RGB/RGBA, interlaced,
over-color, cropped, scaled, rotated, or multi-IDAT PNGs fail. Frame payload
deduplication and OAM composition remain in the established compiler/linker
formats; v1 validates the declared 1024-sheet-tile, 32-OAM-entry, 16-palette
color, 128-total-OAM, 32KiB OBJ-VRAM, 256KiB ROM declaration bounds before
publishing.

Generation writes only ignored `build/generated/assets/banim/` products:
`banim_data_entries.inc`, `banim_defs.inc`, `banim_defs.h`, and
`linker_inputs.mk`. The existing C source includes the generated entry and
typed declaration; `linker_inputs.mk` is a dependency report, not a second
linker list. `assets-generate` is write-if-changed/atomic; `assets-check`
rejects missing, stale, and orphan products. The existing #67 publication
lock still exclusively serializes `banim/data_banim.o`.

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
binding and asserts deterministic generated declarations; the default control
retains `CLASS_EPHRAIM_LORD -> AnimConf_0` and its original battle entry.
Host negatives cover unsupported commands, missing mode structure,
nonconforming PNGs, invalid resource declarations, duplicate ownership,
unsafe source paths, stale/orphan output, and atomic replacement.

`make expansion-modern-banim-package-runtime-check` exercises the dedicated
test-only `FE8_BANIM_PACKAGE_RUNTIME_TEST` seam. Its clean isolated ROM enters
Chapter 4's real scripted battle, selects `LORM_SP1_PROOF` exactly once in the
test-only battle state through the generated alias, and asserts that selected
alias index, five modes,
normal/total timing, five existing runtime resources, the unchanged
`CLASS_EPHRAIM_LORD -> AnimConf_0 -> 0` default mapping, and clean battle
completion. The macro is absent from ordinary debug/release builds, so it
cannot create a production mapping or router. The generated alias remains
additive and the ordinary resolver remains unmodified.

For a local visual check, enter an Ephraim Lord lance battle in a modern debug
ROM and compare the standing, attack, critical, ranged, and dodge presentation
to the default build; no palette or frame layout may change. Screenshots are
supplementary only.

Rollback removes the manifest/package record and disposable generated
products together. No save migration, default behavior, or external editor
dependency is involved.
