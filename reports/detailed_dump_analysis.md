# Detailed Analysis of Remaining `dump/` Binary Files

This document tracks remaining `dump/*.bin` files after the source-form extraction pass documented in `docs/dump_extraction_plan.md`. After the pass, only **3** files remain — all deferred for documented reasons.

## Remaining files

| Symbol / Label | Size (Bytes) | Assembly File (.s) | Dump File | Deferral Reason |
| --- | --- | --- | --- | --- |
| `Img_DemonLightSprites_087A5BA4` | 760 | `data/banim-ekrdragonfx.s` | `dump/banim-ekrdragonfx_7A5BA4.bin` | LZ-compressed (header 0x10), gbagfx decompresses to 4096 raw bytes but its recompression (756 bytes, prefix-matches the original only at the 4-byte LZ header) is a different valid LZ encoding of the same content — `make compare` would diverge. The original used a different LZ77 implementation. Needs a custom compressor or .lz commit to preserve byte-identity. |
| `Img_DemonLightSprites_087A5E9C` | 848 | `data/banim-ekrdragonfx.s` | `dump/banim-ekrdragonfx_7A5E9C.bin` | Same as above (848 vs 848 bytes; only 4-byte header common prefix). |
| `gUnknown_08fe6sio_B1A368` | 21452 | `data/data_fe6sio.s` | `dump/data_fe6sio_B1A368.bin` | ARM Thumb code blob — needs full function decomp into `src/fe6sio_*.c`, or assembly disassembly into `asm/fe6sio.s` with proper symbol references. Out of scope for the source-form extraction pass; tracked as a separate decomp task. |

## What changed

Pre-extraction: 205 dump files, ~920 KB.

Post-extraction: 3 dump files, ~23 KB. The remaining content represents <2.5% of the original dump volume and is explicitly justified per the rationales above.

## Discoveries

The LZ-suffix diagnostic (documented in `docs/lz_suffix_diagnostic.md`) revealed several anonymous dump chunks that were actually **multiple concatenated assets** the original mining missed. Notable examples:

| Original dump chunk | Was actually |
| --- | --- |
| `const_data_banimekrdk_0DFA2C.bin` (5944 B) | 3 LZ images + 1 palette (4 sub-assets, commit `ca414705`) |
| `banim-efxlvupfx_5BB2FC.bin` (3724 B) | Multi-palette table (116 palettes + 12 trailing bytes, commit `ca4732a0`) |
| `banim-efxlvupfx_5BF114.bin` (1524 B) | LZ image + 8-palette block + LZ irregular chunk (commit `3cc436d2`) |
| `data_A2EEF0_A36284.bin` (180 B) | 148-byte LZ + 32-byte hidden palette (commit `5840b2c0`) |
| `data_A21658_A2E5EC.bin` (772 B) | 676-byte LZ + 96-byte hidden palette block (commit `7dd2b53d`) |

These had previously been treated as opaque single chunks; the diagnostic exposed their true structure.

## Audit

After this pass:
- `ls dump/ | wc -l` → 3
- `grep -c '\.incbin "dump/' data/*.s` → 3 (across data_fe6sio.s and banim-ekrdragonfx.s)
- `make clean_fast && make -j$(nproc) compare` → `fireemblem8.gba: OK`
- `bash scripts/calcrom.sh` → "bytes of data in src" climbed from ~534 KB to ~550 KB
