# Custom battle spell effects (issues #77 and #78)

Issue #77 provides the optional typed runtime extension to the existing
`C05` -> `StartSpellAnimation()` route. Issue #78 adds the sole
`custom-spell-effect` `KIND_REGISTRY` adapter and a committed synthetic
reference package. It is source generation, not an editor patch, ROM importer,
runtime parser, project spell catalog, or second item/effect registry.

## Configuration and compatibility

| Autoconf | Make | C macro | Default |
| --- | --- | --- | --- |
| `--enable-custom-spell-effects` | `EXPANSION_CUSTOM_SPELL_EFFECTS` | `FE8_EXPANSION_CUSTOM_SPELL_EFFECTS` | `0` |

The setting is strictly `0` or `1`; invalid values fail during configure,
modern configuration resolution, and C preprocessing. It is a permanent
project choice. It participates in the modern configuration fingerprint and
embedded build metadata, but it does not alter
`EXPANSION_SAVE_COMPAT_EPOCH`, any save layout, serialized item encoding, or
save migration. A battle Proc is transient and cannot survive an ordinary
save/suspend boundary.

Enabled identity also binds runtime ABI `1`, the selected manifest's generated
descriptor/asset inventory SHA-256, and resource-envelope SHA-256. The disabled
metadata records ABI `0` and SHA-256-of-empty inventory/resource values, while
the disabled fingerprint intentionally remains the pre-feature default.

```sh
./configure --enable-custom-spell-effects
make ASSET_MANIFEST=assets/manifests/custom-spell-reference.json

# One-off equivalent
make expansion-modern-rom EXPANSION_CUSTOM_SPELL_EFFECTS=1 \
  ASSET_MANIFEST=assets/manifests/custom-spell-reference.json
```

Only modern AAPCS debug and release builds support the runtime. The archival
lane always compiles with `FE8_EXPANSION_CUSTOM_SPELL_EFFECTS=0`; it has no
custom dispatch, descriptor, assets, or package support.

## Public runtime ABI

Include `custom_spell_effect.h`. Generated bindings must use a stable
`CUSTOM_SPELL_*` symbol and may only assign the private dense range
`0x80..0x8F`; authored manifests never use dense values.

```c
const struct CustomSpellEffect *CustomSpellEffect_Lookup(u8 animationId);
void CustomSpellEffect_Start(
    const struct CustomSpellEffect *effect, struct Anim *anim);
```

`struct CustomSpellEffect` contains a symbol, typed frame records, a valid
vanilla fallback animation, frame/hit timing, explicit resource metadata, and
one effect-wide `CustomSpellEffectOamScripts` orientation set. Every
`CustomSpellEffectFrame` points to a `CustomSpellEffectFrameAssets` record
containing that frame's BG/OBJ graphics, BG1 TSA pair, and BG/OBJ palettes.
The effect owns one generated `soundIds` table; each frame owns a contiguous
`soundStart`/`soundCount` range into it. All listed sounds play in source order
at that frame's start. Frame `flags` must be zero in runtime ABI v1.
The runtime accepts OBJ palette line 2 and BG palette line 1 only; a descriptor
that names another lane is invalid before it can reserve anything.
The fallback ID must be both inside the source-derived vanilla LUT bounds and
bound to a non-NULL LUT entry. A malformed fallback is rejected and is never
silently replaced by the known-valid Fire fallback.
The generated descriptor array is dense and ordered by stable manifest ID.
`CustomSpellEffect_Lookup()` accepts only generated entries in the closed
custom range. `StartSpellAnimation()` checks that range before indexing the
vanilla LUT; every other animation ID keeps its prior `gEkrSpellAnimLut[]`
and `SpellAssoc` ownership unchanged.

Missing or invalid custom descriptors, a reentrant custom owner, an occupied
spell semaphore, invalid presentation metadata, or `WITH_BACKGROUNDS` select
the descriptor's validated vanilla fallback before custom allocation. A
missing descriptor uses the foundation's known-valid reference fallback; an
invalid descriptor may use its declared fallback only when that LUT entry is
itself valid. No partial custom write occurs. The `OFF` presentation policy
takes the same clean fallback path; only `DEFAULT`, `REDUCED`, and `SOLO` may
acquire the custom spell lanes.

## `custom-spell-effect` manifest and package

The root [`assets/manifest.json`](../assets/manifest.json) contains no custom
spell record, so the default-off build remains catalog-free. The committed
reference profile is
[`assets/manifests/custom-spell-reference.json`](../assets/manifests/custom-spell-reference.json).
Feature `0` with a record is an error; feature `1` without a record is also an
error.

One record uses the exact kind-owned schema below:

```json
{
  "id": "CUSTOM_SPELL_REFERENCE",
  "kind": "custom-spell-effect",
  "sources": [
    "graphics/custom_spell/reference/spell.json",
    "graphics/custom_spell/reference/animation.txt",
    "graphics/custom_spell/reference/images/reference_obj_00.png",
    "graphics/custom_spell/reference/images/reference_bg_00.png"
  ],
  "dependsOn": [],
  "options": {
    "importFormat": "feditor-magic-v1",
    "runtimeAbi": 1,
    "compression": "lz77"
  },
  "ownership": {
    "seam": "spell-effect-dispatch",
    "item": "ITEM_ANIMA_FORBLAZE",
    "effectSymbol": "CUSTOM_SPELL_REFERENCE",
    "fallbackVanillaEffect": "SASSOC_EFX_Fire",
    "spellAssociationSource": "src/spellassoc-data.c"
  },
  "resources": {
    "frames": 2,
    "totalFrames": 4,
    "hitFrame": 2,
    "objBytes": 4096,
    "bgBytes": 1280,
    "bgTsaBytes": 1200,
    "objOamEntries": 2,
    "objPalettes": 1,
    "bgPalettes": 1,
    "soundEvents": 1,
    "romBytes": 262144
  }
}
```

`sources` lists `spell.json`, `animation.txt`, then every referenced image in
first-reference order. The package directory contains only those two files
and `images/`; image names are safe ASCII basenames. `ITEM_*` must resolve to
one existing anima/light/dark `IA_WEAPON | IA_MAGIC` record with no existing
`SpellAssoc` entry. The fallback symbol must resolve to a non-NULL vanilla
spell LUT entry. Duplicate item/effect ownership and more than 16 records fail.

`spell.json` owns only declared SFX:

```json
{
  "schemaVersion": 1,
  "soundTable": [
    { "id": "F1", "song": "SONG_F1" }
  ]
}
```

The object has exactly `schemaVersion` and `soundTable`. Each of at most eight
rows has exactly canonical uppercase hexadecimal `id` and a `SONG_*` symbol
whose value in `include/constants/songs.h` is identical. Duplicate, unused,
undeclared, zero, or mismatched IDs fail.

The exact `animation.txt` grammar is:

```text
file       := { blank | comment | marker | frame | sound } final-terminator
comment    := ws? ("#" | "@") text
marker     := ws? "///" text
frame      := obj bg wait
obj        := ws? "O" ws+ "p-" ws+ filename
bg         := ws? "B" ws+ "p-" ws+ filename
wait       := ws? decimal(1..255)
sound      := ws? "S" hex(1..4)
final-terminator := ws? "~~~"
```

There are 1..64 ordered frames and at most 255 total ticks. Sounds appear only
between complete frames, belong to the following frame boundary, and preserve
source order. One final `~~~` ends the file. Unknown tokens, `C...`, CSA
records, arbitrary C/Event Assembler, missing or reordered triples, unsafe
paths, and extra/mid-stream terminators fail with file/line diagnostics.

Each OBJ PNG is exact indexed 4bpp 480x160; each BG PNG is exact indexed 4bpp
240x64. Both have 1..16 colors, transparent index 0, opaque nonzero entries,
one IDAT, no ancillary/critical extras, and no runtime decoder.

### Deterministic conversion

- OBJ splits into 240x160 front/back planes. The generator adds an invisible
  transparent tile column, packs front then back into one zeroed 32x4-tile
  seat, and greedily tries `8x4, 4x4, 4x2, 2x4, 2x2, 4x1, 1x4, 2x1, 1x2,
  1x1` rectangles. Exact blocks are reused before first-free row-major
  allocation. The full seat is always `0x1000` bytes.
- Canonical OAM uses `x=tileX*8-0xAC`, `y=tileY*8-0x58`; the opposite
  orientation uses `x=-width-canonicalX` plus horizontal flip. Right/left
  origins are `(0xAC,0x58)` and `(0x44,0x58)`. Front entries precede back
  entries. More than 16 entries or a full seat fails.
- BG pre-scales vertically to 240x160 using nearest
  `round(y*64/160)` (rounded row 64 becomes transparent), deduplicates 30x20
  tiles with zero tile 0, and rejects more than 256 unique tiles. Every frame
  pads to the package-wide maximum tile count; each generated TSA is exactly
  600 `u16` entries/1200 bytes. Runtime supplies character base, palette line,
  distance selection, and left-side horizontal flip.
- Palettes pad to 16 BGR555 colors. Every frame's OBJ/BG/TSA is deterministically
  LZ77-compressed. Generated C includes, descriptors, `SpellAssoc` entries,
  canonical assets, inventory, provenance/digests, and Make dependencies live
  only under ignored `build/generated/assets/custom_spell/`.

Run:

```sh
make assets-validate assets-generate assets-check assets-test \
  EXPANSION_CUSTOM_SPELL_EFFECTS=1 \
  ASSET_MANIFEST=assets/manifests/custom-spell-reference.json
```

## Resource and lifecycle contract

The one owner Proc is in `PROC_TREE_3`. Before allocation it validates every
frame record and all six visual pointers, plus all four effect-wide OAM script
pointers. It then reserves one spell semaphore, uploads frame 0's complete
visual set, and creates one bounded child animation. At each later frame
boundary it uploads that frame's complete visual set exactly once without
recreating the child; generated OAM scripts encode all frame/duration changes.
It plays every generated boundary sound in order, applies one hit at
`hitFrame`, and holds the final child display for one additional update tick
after the last frame boundary so its last OAM state reaches `AnimUpdateAll`
before cleanup. It then releases all ownership through its normal and
forced-end callback. Cleanup clears BG1 and its position, restores color/window
state, ends spell-cast registration, deletes the one child animation, and
releases the reservation. The enabled debug profile exports
`gCustomSpellEffectDebugProbe` from the existing debugtools probe section.
Release retains the same validation and cleanup without that diagnostic
object.

| Resource | Bound |
| --- | --- |
| OBJ spell lane | exact padded upload size for every frame, at most `0x1000` bytes |
| BG spell lane | exact padded upload size for every frame, at most `0x2000` bytes |
| BG1 TSA | exactly 30x20 / 1200 bytes |
| Palette lanes | OBJ line 2 and BG line 1 |
| OAM | generated effect-wide scripts; at most 16 records in any frame |
| Sound | one contiguous generated table; at most 8 ordered boundary events |
| Runtime | one Proc, one hit, no concurrent custom effect |
| Compressed module payload | at most `0x40000` bytes |

The adapter fills this envelope only through #60's `KIND_REGISTRY`; it does
not add a second router, Proc ABI, item/effect table, raw patch hook, or
hand-edited generated output.

## Tester cases and rollback

`TC-CUSTOM-SPELL-061-001` covers enabled debug/release typed lookup, frame
timing, ordered boundary SFX, one hit, final-display latch, termination,
cleanup, and a subsequent vanilla LUT path.
`TC-CUSTOM-SPELL-061-002` covers default-disabled builds and confirms no
custom object symbols or dispatch are reachable. `TC-CUSTOM-SPELL-061-003`
covers strict package parsing, conversion, binding, provenance, capacity, and
generated-output drift. `TC-CUSTOM-SPELL-061-004`
covers `WITH_BACKGROUNDS`, reentrancy, and resource-conflict fallback.
`TC-CUSTOM-SPELL-061-005` covers save/suspend layout and epoch stability.
Focused host/config/ARM object checks enforce the descriptor bounds, closed
dispatch range, enabled/disabled linkage boundary, and identity decision.
The real-ROM gate uses a separate `FE8_EXPANSION_CUSTOM_SPELL_TEST=1` build
that is never part of a production configuration identity. That test ROM
bypasses `StartGame`, creates four bounded isolated `Anim` fixtures after
normal hardware/Proc/sound initialization, and calls the public
`StartSpellAnimation()` ABI. It does not launch a chapter, alter an event
script, inject a custom ID into a scripted battle, or rely on framebuffer
timing. `tools/gba-playtest/run_custom_spell_effect_checks.py` resolves its
test-only scalar probe from the exact ELF and drives the ROM through libmGBA.

Run each supported configuration and both feature states with separate build
roots so no production object cache is reused:

```sh
make expansion-modern-custom-spell-check MODERN_CONFIG=debug \
  MODERN_ABI=aapcs MODERN_BUILD_ROOT=build/issue78-debug-enabled \
  FE8_EXPANSION_CUSTOM_SPELL_TEST=1 EXPANSION_CUSTOM_SPELL_EFFECTS=1 \
  ASSET_MANIFEST=assets/manifests/custom-spell-reference.json
make expansion-modern-custom-spell-check MODERN_CONFIG=debug \
  MODERN_ABI=aapcs MODERN_BUILD_ROOT=build/issue78-debug-disabled \
  FE8_EXPANSION_CUSTOM_SPELL_TEST=1 EXPANSION_CUSTOM_SPELL_EFFECTS=0
make expansion-modern-custom-spell-check MODERN_CONFIG=release \
  MODERN_ABI=aapcs MODERN_BUILD_ROOT=build/issue78-release-enabled \
  FE8_EXPANSION_CUSTOM_SPELL_TEST=1 EXPANSION_CUSTOM_SPELL_EFFECTS=1 \
  ASSET_MANIFEST=assets/manifests/custom-spell-reference.json
make expansion-modern-custom-spell-check MODERN_CONFIG=release \
  MODERN_ABI=aapcs MODERN_BUILD_ROOT=build/issue78-release-disabled \
  FE8_EXPANSION_CUSTOM_SPELL_TEST=1 EXPANSION_CUSTOM_SPELL_EFFECTS=0
```

Enabled runs prove the registered custom route, an unchanged vanilla LUT
route, missing and invalid descriptors, reentrancy, post-acquisition resource
failure cleanup, `WITH_BACKGROUNDS`, foreign semaphore preservation, forced
Proc end, child cleanup, and zero final custom/spell ownership. Disabled runs
prove the vanilla route and assert that the linked ELF has no public custom
dispatcher/runtime symbols. The host lifecycle driver separately executes the
same production runtime with stubbed spell helpers, so the test-only hooks are
observers and fault selectors rather than a replacement implementation.
Every isolated ROM also injects one Anim allocation failure and one harness
Proc allocation failure before the ordinary scenarios, records both in the
scalar probe, clears every partial Anim/global allocation, and only then
continues. A real unexpected allocation failure records the same fail-closed
probe instead of dereferencing NULL.

Dependencies are #60's sole manifest seam, the existing spell
association/`C05` path, spell-FX helpers, Proc scheduler, presentation policy,
configuration identity, linker budgets, and libmGBA harness. Conflicts are
external FEditor/CSA/custom-magic patches, direct LUT or `SpellAssoc`
replacement, duplicate item/effect ownership, manual spell-lane writes, and
unsupported presentation envelopes. There are no starter-mechanics,
casual-mode, AoE, localization-selection, or BGM-policy conflicts.
Version 1 adds no user-facing text or locale catalog entry; diagnostics are
stable ASCII source keys. It adds no permanent EWRAM and uses only #77's
existing transient spell buffers.

Rollback reverts the #78 adapter/package layer before the #77 runtime layer.
No item renumbering, committed generated asset migration, save conversion, or
epoch change is required; all runtime failures already use a vanilla fallback.

## TC-CUSTOM-SPELL-061-001: Custom spell dispatch completes one bounded effect

- **Profile:** run the enabled debug and release isolated-ROM commands above.
- **Starting state:** use distinct empty `build/issue78-*-enabled` roots and
  the committed alternate reference manifest. No save, chapter, event script,
  editor, ROM input, or external patch is loaded.
- **Actions:** run the host suite, then both enabled
  `expansion-modern-custom-spell-check` commands.
- **Expected result:** custom ID `0x80` traverses the public
  `StartSpellAnimation()` ABI, starts once, uploads all six visual resources
  once per frame (two generated reference sets), applies one ordered generated
  sound and one hit, keeps the final child through its last
  display tick, creates/deletes one child, cleans once, and ends with no owner,
  semaphore, spell-cast Proc, or active spell state. Vanilla ID `24` still
  traverses the LUT and never increments custom dispatch.
- **Interactions/save:** #78 uses #77's only generated package seam. No starter,
  casual, AoE, localization, BGM, save-field, item-encoding, or migration
  dependency exists.
- **Automation:** `tools/gba-playtest/tests/test_custom_spell_effect.py` and
  `tools/gba-playtest/run_custom_spell_effect_checks.py`; there is no material
  manual-only criterion.

## TC-CUSTOM-SPELL-061-002: Default-off custom spell profile preserves vanilla dispatch

- **Profile:** run the disabled debug and release isolated-ROM commands above,
  plus `python3 -m unittest
  tools.gba-playtest.tests.test_custom_spell_effect.CustomSpellArmTests -v`.
- **Starting state:** use distinct empty `build/issue78-*-disabled` roots.
- **Expected result:** vanilla LUT ID `24` dispatches once, custom dispatch is
  zero, and the disabled ELF/ARM object contains no public
  `CustomSpellEffect_*` runtime symbol. The archival lane fixes both feature
  and harness macros at zero and has no custom package support.
- **Negative control:** the enabled roots contain the public ABI and dispatch
  `0x80`.
- **Save/cleanup:** the default identity and save epoch remain unchanged.
  Remove only the named build roots if cleanup is needed. No manual criterion.

## TC-CUSTOM-SPELL-061-003: Strict package conversion

- **Profile:** selected alternate reference manifest with feature `1`.
- **Starting state:** clean `build/generated/assets`; no editor, ROM, external
  patch, or generated source-tree file.
- **Actions:** run `python3 -m unittest
  scripts.assets.tests.test_custom_spell -v`, then the enabled
  `assets-validate assets-generate assets-check assets-test` command above.
- **Expected result:** the reference emits one dense `0x80` descriptor,
  `ITEM_ANIMA_FORBLAZE` binding, two frame visual sets, one declared SFX,
  deterministic inventory/resource digests, and current generated outputs.
- **Negative control:** schema/option/token/C/CSA/path/PNG/item/fallback/
  ownership/capacity/missing/stale/orphan variants fail before compilation
  with a file/line or exact reason.
- **Save/cleanup:** no save effect; `make assets-clean` removes only ignored
  products. No material manual criterion.

## TC-CUSTOM-SPELL-061-004: Custom spell conflicts fall back without leaking resources

- **Profile:** either enabled isolated debug or release ROM.
- **Actions:** run the enabled check. One deterministic run selects missing
  and invalid descriptors, starts a second dispatch while the first owns the
  resource, injects one post-acquisition load failure, selects
  `WITH_BACKGROUNDS`, holds a foreign semaphore, and force-ends one live owner.
- **Expected result:** missing/invalid descriptors use vanilla fallback `22`;
  reentrancy starts only one owner; the injected load failure and forced end
  each clean once; `WITH_BACKGROUNDS` writes no custom resource; a foreign
  semaphore stays `1`; every subcase ends with zero custom ownership.
- **Negative control:** the DEFAULT-policy subcase in the same ROM acquires and
  completes normally. Test fault selection exists only behind
  `FE8_EXPANSION_CUSTOM_SPELL_TEST=1`. No manual criterion.

## TC-CUSTOM-SPELL-061-005: Custom spell runtime remains save-neutral

- **Profile:** ordinary modern AAPCS debug/release production builds with the
  feature enabled, compared with the default-disabled profile.
- **Actions:** run the identity unit test and
  `expansion-modern-savefmt-check` for both enabled configurations with the
  alternate reference manifest.
- **Expected result:** enabled/disabled fingerprints differ, their
  `EXPANSION_SAVE_COMPAT_EPOCH` remains identical, existing game/suspend checks
  pass, and the isolated lifecycle proof finishes with no transient Proc or
  state to cross a save boundary.
- **Negative control:** default-disabled objects contain no custom runtime.
  There is no save migration or manual criterion.
