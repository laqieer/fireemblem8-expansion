# Audio feature cases

## TC-AUDIO-HQMIX-001: HQ PCM mixer produces bounded stereo output

- **Feature / originating issue:** `hq-pcm-mixer` / issue #83.
- **Supported configuration or artifact:** source-built modern AAPCS debug or
  release automation profile with `EXPANSION_HQ_MIXER=1`, compared with its
  isolated `EXPANSION_HQ_MIXER=0` automation control; manual listening uses
  the separate normal-game release ROM pair documented below.
- **Prerequisites and clean starting state:** start at the repository root
  with the supported ARM toolchain, libmGBA, and project build tools. The
  command creates separate ignored build roots; no save, savestate, patch, or
  mutable upstream checkout is required.

### Actions

1. Run `make expansion-modern-hq-mixer-check MODERN_CONFIG=debug MODERN_ABI=aapcs`.
2. Run `make expansion-modern-hq-mixer-check MODERN_CONFIG=release MODERN_ABI=aapcs`.
3. Build the non-instrumented listening pair with
   `make expansion-modern-hq-mixer-listening-roms MODERN_CONFIG=release MODERN_ABI=aapcs`.
4. For the configuration negative, retain the failure from
   `make -n legacy EXPANSION_HQ_MIXER=1`.
5. Clean-boot, without a save or savestate, the enabled listening ROM at
   `build/expansion-modern-hq-mixer-listening/enabled/release/aapcs/fireemblem8.gba`.
   Confirm initial silence, press `A` once at the first prompt, and listen
   through the opening BGM.
6. Repeat the same input and emulator-volume sequence with
   `build/expansion-modern-hq-mixer-listening/stock-control/release/aapcs/fireemblem8.gba`.

The ROMs under `build/expansion-modern-hq-mixer*/` used by the automated
runtime gate define `FE8_HQ_MIXER_TEST_FIXTURE` and start an automation-only
startup song. They are not valid manual controls; in particular, the old
disabled recording's early song is fixture behavior rather than normal-game
startup.

### Expected result

Both enabled profiles link and boot with exactly one HQ `SoundMainRAM`
implementation. The mixer copies its bounded ROM code to IWRAM, uses the
aligned 16-bit intermediate buffer, leaves the DMA3 path disabled, retains
the `0x1000` user-stack floor, omits reverb feedback, and completes the
scripted battle's ordinary HBlank/DMA activity. libmGBA captures non-silent,
bounded, distinct left and right PCM buffers throughout the declared late
window, live owned MP2K channels at every late checkpoint, surviving BGM
player state, valid PCM interrupt counters, a matching pre-execution
ROM/IWRAM copy checksum, and no invalid interrupt-buffer observation. The
final enabled checkpoint must remain non-silent. This validates fixed
paired-channel output; it does not claim a mono/stereo toggle. The
deterministic multi-voice host fixture reports lower final-quantization RMS
error than the stock per-voice model.

The normal-game listening pair both starts silent and begins the same opening
BGM after the same `A` input. It must not reproduce the automation fixture's
early song, orphan-channel noise, or subsequent permanent mute.

### Negative control

The disabled ROM has no HQ mixer symbol, high-resolution buffer, or runtime
probe; it uses the stock MP2K mixer while still producing bounded non-silent
stereo PCM through the same scripted-battle progression. An enabled archival request
fails before compilation rather than silently selecting either mixer.

### Interactions and save compatibility

The module depends on the existing MP2K ABI, modern AAPCS linker, IWRAM
budget, and libmGBA test backend. Its initial DMA-disabled profile has no
dependency or conflict with other optional modules. Real localized-game
profiles conflict with the fixed HQ IWRAM reservation and fail early; `en` and
`en,qps-ploc` remain supported. It changes only the diagnostic configuration
fingerprint: no save byte, layout, migration, or compatibility epoch changes,
and saves remain interchangeable.

### Automation

- `python3 -m unittest tools.gba-playtest.tests.test_hq_mixer -v` validates
  strict configuration, Autoconf persistence, archival rejection, compiled
  mixer selection, IWRAM symbol bounds, and the deterministic RMS fixture.
- `make expansion-modern-hq-mixer-check MODERN_CONFIG=debug MODERN_ABI=aapcs`
  and the matching release command run exact-ELF/map checks plus real
  libmGBA player-liveness, channel-ownership, sustained late-window
  PCM/interrupt-buffer, and scripted-battle-HBlank evidence.
- `make expansion-modern-hq-mixer-listening-roms MODERN_CONFIG=release
  MODERN_ABI=aapcs` creates the isolated non-instrumented pair used only for
  the manual listening judgment.

The manual-only criterion is audible clipping, click-at-loop-boundary,
periodic dropout, unexpected noise, permanent mute, or a perceptible
left/right reversal during the normal opening BGM. Those judgments require a
listener and are not replaced by screenshots, capture hashes, or source text.

### Cleanup and limitations

Use `make clean_fast` to remove build artifacts if desired. Closing the
emulator resets the case; no in-game cleanup exists. DMA-on, mono,
direct-sound reverb, runtime switching, altered sample rates/voice limits,
compressed samples, Camelot synth support, and the archival decompilation lane
are intentionally unsupported.
