# Custom battle spell-effect runtime (issue #77)

Issue #77 provides the **runtime foundation only** for an optional,
project-selected custom battle spell effect. It is a typed, bounded extension
to the existing `C05` -> `StartSpellAnimation()` route. It is not an item
catalog, manifest adapter, FEditor/CSA parser, package format, external patch,
or source of project spell assets. Issue #78 owns the sole future
`custom-spell-effect` asset adapter and its generated bindings.

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

Enabled identity also binds runtime ABI `1`, the synthetic descriptor
inventory SHA-256, and the published resource-envelope SHA-256. The disabled
metadata records ABI `0` and SHA-256-of-empty inventory/resource values, while
the disabled fingerprint intentionally remains the pre-feature default.

```sh
./configure --enable-custom-spell-effects
make

# One-off equivalent
make expansion-modern-rom EXPANSION_CUSTOM_SPELL_EFFECTS=1
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
silently replaced by the synthetic reference fallback.
The foundation's compiled synthetic descriptor proves the ABI without a
manifest record or external asset. `CustomSpellEffect_Lookup()` accepts only
the closed custom range. `StartSpellAnimation()` checks that range before
indexing the vanilla LUT; every other animation ID keeps its prior
`gEkrSpellAnimLut[]` and `SpellAssoc` ownership unchanged.

Missing or invalid custom descriptors, a reentrant custom owner, an occupied
spell semaphore, invalid presentation metadata, or `WITH_BACKGROUNDS` select
the descriptor's validated vanilla fallback before custom allocation. A
missing descriptor uses the foundation's known-valid reference fallback; an
invalid descriptor may use its declared fallback only when that LUT entry is
itself valid. No partial custom write occurs. The `OFF` presentation policy
takes the same clean fallback path; only `DEFAULT`, `REDUCED`, and `SOLO` may
acquire the custom spell lanes.

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

The synthetic foundation reserves and validates this envelope but deliberately
does not ship a package asset upload. #78 may fill the descriptor's generated
frame/resource references only through #60's `KIND_REGISTRY`; it must not add
a second router, Proc ABI, item/effect table, raw patch hook, or manual
generated output.

## Tester cases and rollback

`TC-CUSTOM-SPELL-061-001` covers enabled debug/release typed lookup, frame
timing, ordered boundary SFX, one hit, final-display latch, termination,
cleanup, and a subsequent vanilla LUT path.
`TC-CUSTOM-SPELL-061-002` covers default-disabled builds and confirms no
custom object symbols or dispatch are reachable. `TC-CUSTOM-SPELL-061-004`
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
  MODERN_ABI=aapcs MODERN_BUILD_ROOT=build/issue77-debug-enabled \
  FE8_EXPANSION_CUSTOM_SPELL_TEST=1 EXPANSION_CUSTOM_SPELL_EFFECTS=1
make expansion-modern-custom-spell-check MODERN_CONFIG=debug \
  MODERN_ABI=aapcs MODERN_BUILD_ROOT=build/issue77-debug-disabled \
  FE8_EXPANSION_CUSTOM_SPELL_TEST=1 EXPANSION_CUSTOM_SPELL_EFFECTS=0
make expansion-modern-custom-spell-check MODERN_CONFIG=release \
  MODERN_ABI=aapcs MODERN_BUILD_ROOT=build/issue77-release-enabled \
  FE8_EXPANSION_CUSTOM_SPELL_TEST=1 EXPANSION_CUSTOM_SPELL_EFFECTS=1
make expansion-modern-custom-spell-check MODERN_CONFIG=release \
  MODERN_ABI=aapcs MODERN_BUILD_ROOT=build/issue77-release-disabled \
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

Dependencies are #60's sole future manifest seam, the existing spell
association/`C05` path, spell-FX helpers, Proc scheduler, presentation policy,
configuration identity, linker budgets, and libmGBA harness. Conflicts are
external FEditor/CSA/custom-magic patches, direct LUT or `SpellAssoc`
replacement, duplicate item/effect ownership, manual spell-lane writes, and
unsupported presentation envelopes. There are no starter-mechanics,
casual-mode, AoE, localization-selection, or BGM-policy conflicts.

Rollback removes this default-off module before a dependent #78 adapter. No
item renumbering, asset migration, save conversion, or epoch change is
required; all runtime failures already use a vanilla fallback.

## TC-CUSTOM-SPELL-061-001: Custom spell dispatch completes one bounded effect

- **Profile:** run the enabled debug and release isolated-ROM commands above.
- **Starting state:** use distinct empty `build/issue77-*-enabled` roots. No
  save, chapter, event script, package, or external asset is loaded.
- **Actions:** run the host suite, then both enabled
  `expansion-modern-custom-spell-check` commands.
- **Expected result:** custom ID `0x80` traverses the public
  `StartSpellAnimation()` ABI, starts once, uploads all six visual resources
  once per frame (two complete sets in the synthetic descriptor), applies one
  ordered synthetic sound and one hit, keeps the final child through its last
  display tick, creates/deletes one child, cleans once, and ends with no owner,
  semaphore, spell-cast Proc, or active spell state. Vanilla ID `24` still
  traverses the LUT and never increments custom dispatch.
- **Interactions/save:** #78 is the only package adapter dependent. No starter,
  casual, AoE, localization, BGM, save-field, item-encoding, or migration
  dependency exists.
- **Automation:** `tools/gba-playtest/tests/test_custom_spell_effect.py` and
  `tools/gba-playtest/run_custom_spell_effect_checks.py`; there is no material
  manual-only criterion.

## TC-CUSTOM-SPELL-061-002: Default-off custom spell profile preserves vanilla dispatch

- **Profile:** run the disabled debug and release isolated-ROM commands above,
  plus `python3 -m unittest
  tools.gba-playtest.tests.test_custom_spell_effect.CustomSpellArmTests -v`.
- **Starting state:** use distinct empty `build/issue77-*-disabled` roots.
- **Expected result:** vanilla LUT ID `24` dispatches once, custom dispatch is
  zero, and the disabled ELF/ARM object contains no public
  `CustomSpellEffect_*` runtime symbol. The archival lane fixes both feature
  and harness macros at zero and has no custom package support.
- **Negative control:** the enabled roots contain the public ABI and dispatch
  `0x80`.
- **Save/cleanup:** the default identity and save epoch remain unchanged.
  Remove only the named build roots if cleanup is needed. No manual criterion.

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
  `expansion-modern-savefmt-check` for both enabled configurations.
- **Expected result:** enabled/disabled fingerprints differ, their
  `EXPANSION_SAVE_COMPAT_EPOCH` remains identical, existing game/suspend checks
  pass, and the isolated lifecycle proof finishes with no transient Proc or
  state to cross a save boundary.
- **Negative control:** default-disabled objects contain no custom runtime.
  There is no save migration or manual criterion.
