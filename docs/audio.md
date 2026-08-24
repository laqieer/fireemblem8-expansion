# Optional HQ PCM mixer

Issue #83 adds a permanent, default-off high-resolution PCM mixer at the
existing MP2K `SoundMainRAM` / `m4aSoundInit` seam. It retains the normal
sequence, song, SFX, and volume APIs; only the direct-sound PCM mixing
implementation changes after a rebuild. The pinned HQ profile compiles
`ENABLE_REVERB=0`: reverb metadata and setters remain ABI-compatible, but the
downsampler omits reverb feedback, so MP2K's stored reverb setting is not
applied to HQ PCM output.

## Configuration

| Surface | Enabled value | Default |
| --- | --- | --- |
| GNU Autoconf | `./configure --enable-hq-mixer` | disabled |
| Make | `EXPANSION_HQ_MIXER=1` | `0` |
| C/assembly | `FE8_EXPANSION_HQ_MIXER=1` | `0` |

The value is strictly `0` or `1`. It is part of
`FE8_EXPANSION_CONFIG_FINGERPRINT`, but does not alter
`EXPANSION_SAVE_COMPAT_EPOCH`, save bytes, generated data, IDs, locale data,
or the MP2K public API. Use an isolated build root when comparing profiles:

```bash
make expansion-modern-hq-mixer-check MODERN_CONFIG=debug MODERN_ABI=aapcs
make expansion-modern-hq-mixer-check MODERN_CONFIG=release MODERN_ABI=aapcs
```

The enabled configuration is supported only by linked modern AAPCS debug and
release English or English-plus-pseudo-locale outputs. Real localized-game
profiles fail before compilation because their IWRAM transform scratch and the
HQ code/buffer cannot both preserve the mandatory user-stack floor. `make legacy EXPANSION_HQ_MIXER=1` and
`make fireemblem8.gba EXPANSION_HQ_MIXER=1` fail before compilation; the
default-off archival lane remains independent.

## Runtime and resource contract

The vendored mixer is pinned to upstream revision
`2b1e2ea36ad6c256718f219b7c55eb434c50477c`; its source carries the required
copyright and MIT permission notice. The build never fetches it.

The initial profile is non-Pokemon, always emits paired left/right
interleaved PCM samples, uses FE8's existing 13,379 Hz frame length, omits
reverb feedback, and is DMA-disabled. The upstream `ENABLE_STEREO` pseudo
switch was unused and is intentionally absent: this integration has no
mono/stereo runtime or build toggle. The 16-bit interleaved intermediate
buffer is linker-owned rather than address-coded. The exact fixed resources
are:

| Resource | Disabled | Enabled | Delta |
| --- | ---: | ---: | ---: |
| Mixer ROM object | stock `SoundMainRAM` (`0x3A4` bytes) | HQ object (`0xAC8` bytes) | `+0x724` bytes |
| IWRAM executable buffer | `0x400` bytes | `0xAC0` bytes | `+0x6C0` bytes |
| IWRAM intermediate buffer | absent | `0x380` bytes | `+0x380` bytes |
| IWRAM static end | `0x030067F0` | `0x03006E00` | `+0x610` bytes |
| Minimum user stack | `0x1000` bytes | `0x1000` bytes | unchanged |
| Relocated MP2K/presentation bookkeeping and probe | absent | EWRAM | `+0x418` bytes |

The enabled layout deliberately consumes the pre-existing static-growth
headroom but never crosses `__iwram_static_limit`; linker assertions and
`expansion-modern-budget-check` remain the authority for the complete ROM,
EWRAM, and IWRAM maps. The runtime gate emits the exact profile map values in
`build/expansion-modern/<config>/aapcs/hq-mixer-runtime/hq-mixer-runtime.json`.
The padded 16 MiB ROM file remains the configured size in both profiles; the
`+0x724` mixer-object delta above is the meaningful ROM-content delta.

The imported configuration disables its DMA3 fast path and mixer reverb pass.
This avoids claiming DMA ownership and preserves compatibility with existing
HBlank and other DMA-sensitive effects. DMA-on, mono, direct-sound reverb,
compressed samples, Camelot synth extensions, sample-rate/voice-count changes,
runtime switching, and archival decomp matching are unsupported.

## Validation and tester procedure

`expansion-modern-hq-mixer-check` builds separate enabled and disabled ROMs,
checks the exact selected mixer symbols, copy extent, aligned IWRAM buffer,
absent disabled symbols, linker stack floor, and the 16-bit buffer size. Its
libmGBA's fixed scripted-battle progression captures both PCM FIFO buffers and live interrupt
state from the linked ELF symbols, verifies bounded non-silent left/right
output with distinct captured channel values, checks the one-time
ROM-to-IWRAM copy checksum before self-modifying mixer execution, and runs the
ordinary scripted-battle HBlank/DMA progression on both profiles. This is
evidence for the fixed paired-channel output, not for a selectable stereo
mode.

The deterministic host fixture compares stock per-voice rounding against one
final high-resolution quantization and requires lower RMS error for the latter.
The full tester procedure, negative control, save expectations, and the one
manual listening criterion are in
[`TC-AUDIO-HQMIX-001`](test-cases/audio.md#tc-audio-hqmix-001-hq-pcm-mixer-produces-bounded-stereo-output).

To disable after enabling, rebuild with `EXPANSION_HQ_MIXER=0`. If a
post-merge regression cannot be resolved, revert the issue #83 change; no
save migration or cleanup is required.
