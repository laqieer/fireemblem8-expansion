# UI presentation registries

Issues #41, #43, and #44 share bounded runtime registries rather than adding
one build flag or a second settings screen per visual tweak.

## Battle animation policies

`include/banim_presentation.h` exposes the typed
`BanimPresentationPolicy` surface. The default and existing game-option
values preserve the existing presentation; `BANIM_PRESENTATION_POLICY_REDUCED`
is the deterministic reference policy. Runtime-enforced fields are:

- `backgroundMode`: permits the existing terrain battle background seam;
- `damageNumberStyle`: standard shows hit/damage/crit numbers, reduced shows
  damage only, and off hides all three;
- `hitEffectStyle`: standard runs the existing quake/no-damage/miss effects,
  reduced keeps only the bounded HP-bar effect, and off suppresses these
  effects;
- `hitEffectPalette`: standard uses the existing unit flashing palette, while
  off suppresses that palette flash without adding assets;
- `effectSpeed` and `timingMinFrames`/`timingMaxFrames`: divide existing
  hit-effect durations and clamp them to the declared inclusive frame range.

`paletteSlots`, `oamEntries`, and `vramBytes` are metadata-only declarations;
they validate against 16 palette slots, 128 OAM entries, and `0x8000` VRAM
bytes, but do not reserve resources or change rendering. Unsupported
extension bytes must remain zero. `BanimPresentationPolicy_ValidateAll`
enforces these bounds, effect-speed value 8, and 1--255 timing frames.

The existing Animation row remains the user-facing setting. Its
`PlaySt_OptionBits.animationType` continues to be saved by the normal game
save. The expansion preference record may additionally select a policy
without changing that row, its input ownership, or the config-menu capacity;
the registry callback is dispatched from the existing row handler.

## Chapter and screen manifests

`src/data/ui_presentation.json` is validated by the `ui_presentation` schema
and generates the manifest C object during a modern build. A context provides
its kind, chapter ID, localized title key, fallback text, optional asset ID,
and VRAM/palette/OAM requirements. Optional missing assets resolve through
the localized/static fallback; a required resource without an asset ID is a
validation error. The manifest is capped at 32 records; schema validation
rejects the 33rd record before emitting its bounded `u8` count. Production
catalog validation checks the localized title widths.

Static chapter-title graphics remain the default renderer. Projects can use
`ExpansionUiPresentation_ResolveTitle` at an existing title or screen seam
when they opt into a manifest context.

Fallback text is emitted as a UTF-8 C string with deterministic escaping for
quotes, backslashes, controls, and non-ASCII bytes. Embedded NUL is rejected
because it cannot be represented by a C string.

## Unified utility preferences

`gExpansionUiPreferenceRegistry` is the single descriptor/dispatch surface
for reusable field and utility preferences. It currently registers battle
presentation and the existing Threat Range behavior. Threat Range remains
compile-time optional (`FE8_EXPANSION_DANGER_OVERLAY_MENU`) and is unavailable
when that profile is disabled. It reuses the existing map-menu command and
does not add a second settings screen or a new preference-row localization
ID.

Selections use the existing 12-byte `ExpansionUserPrefs` record. Reserved
bytes carry a bounded policy ID, utility bits, and a selection schema byte;
the record checksum and locale persistence remain unchanged. Older records
default safely, unknown values are rejected, and sound-room/casual save
records are not reused.

The save-compatibility SRAM fingerprints for the empty/migrated preference
scenarios intentionally changed: the existing single-locale auto-selection
write now records the policy/utility schema byte and recomputes the same
record checksum. No save offset or unrelated save block changed.

Validation:

```bash
make generated-data-check
python3 -m scripts.localization check --out-dir build/generated/localization
python3 -m unittest discover -s scripts/generated_data/tests -v
python3 -m unittest discover -s scripts/modernize/tests -v
```

## Tester-facing cases

[`TC-BANIM-001`, `TC-BANIM-002`, `TC-MANIFEST-001`, and
`TC-UTILITY-001`](test-cases/presentation-audio-utility.md#tc-banim-001-apply-standard-reduced-and-off-battle-presentation)
cover policy behavior/bounds, generated manifest fallback and rejection,
saved preference normalization, and the default-off Threat Range profile.
