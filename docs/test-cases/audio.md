# Audio feature cases

## TC-AUDIO-HQMIX-001: HQ PCM mixer produces bounded stereo output

- **Feature / originating issue:** `hq-pcm-mixer` / issue #83.
- **Supported configuration or artifact:** source-built modern AAPCS debug or
  release profile with `EXPANSION_HQ_MIXER=1`, compared with the isolated
  `EXPANSION_HQ_MIXER=0` control built by the same command.
- **Prerequisites and clean starting state:** start at the repository root
  with the supported ARM toolchain, libmGBA, and project build tools. The
  command creates separate ignored build roots; no save, savestate, patch, or
  mutable upstream checkout is required.

### Actions

1. Run `make expansion-modern-hq-mixer-check MODERN_CONFIG=debug MODERN_ABI=aapcs`.
2. Run `make expansion-modern-hq-mixer-check MODERN_CONFIG=release MODERN_ABI=aapcs`.
3. For the configuration negative, retain the failure from
   `make -n legacy EXPANSION_HQ_MIXER=1`.
4. Listen to the fixed scripted-battle segment captured by the enabled and
   disabled runs at the same emulator volume.

### Expected result

Both enabled profiles link and boot with exactly one HQ `SoundMainRAM`
implementation. The mixer copies its bounded ROM code to IWRAM, uses the
aligned 16-bit intermediate buffer, leaves the DMA3 path disabled, retains
the `0x1000` user-stack floor, and completes the scripted battle's ordinary HBlank/DMA
activity. libmGBA captures non-silent, bounded left and right PCM buffers,
valid PCM interrupt counters, a matching pre-execution ROM/IWRAM copy
checksum, and no invalid interrupt-buffer observation. The deterministic
multi-voice host fixture reports lower final-quantization RMS error than the
stock per-voice model.

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
  libmGBA PCM/interrupt-buffer/scripted-battle-HBlank evidence.

The manual-only criterion is audible clipping, click-at-loop-boundary,
periodic dropout, or a perceptible left/right reversal in the fixed segment.
Those judgments require a listener and are not replaced by screenshots,
capture hashes, or source text.

### Cleanup and limitations

Use `make clean_fast` to remove build artifacts if desired. Closing the
emulator resets the case; no in-game cleanup exists. DMA-on, mono, runtime
switching, altered sample rates/voice limits, compressed samples, Camelot
synth support, and the archival decompilation lane are intentionally
unsupported.
