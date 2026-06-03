# Shiftability harness

Tools that test whether the ROM is **shiftable** — i.e. whether it could be edited,
recompiled, and run correctly without breaking pointer integrity. The build being
byte-identical (`make compare`) does **not** prove this: a pointer stored as a raw
absolute address (`(u8*)0x08A39148`) holds the correct value only because everything
sits at its original offset. It carries no relocation, so it breaks the moment the
layout moves. These tools find such hardcoded pointers.

Nothing here touches the matching build — every target is `.PHONY` and uses a
separate ELF / a generated ldscript, never `ldscript.txt`, `$(ROM)`, or `compare`.

## Layers (cheapest → strongest)

| Make target | Layer | What it does |
| --- | --- | --- |
| `make shiftcheck-build` | 0 | Audits hardcoded GBA addresses in the **build system** (Makefile, ldscripts). Cross-checks coupled constants — e.g. the banim link base `-b 0x8c02000` must equal the ldscript pin `0xC02000` — and fails on a mismatch. |
| `make shiftcheck-static` | 1 | Relinks with `ld --emit-relocs`, then flags every ROM-pointer-looking word that carries **no relocation**. Ranked by signal (see below). |
| `make shiftcheck-diff` | 2 | Builds the ROM **shifted** by two amounts and diffs: a real pointer's value tracks the shift; a hardcoded literal stays put. Independent of the reloc table; confirms Layer 1. |
| `make shiftcheck-run` | 3 | Runs the matching vs a shifted ROM headless under identical input and compares framebuffer/RAM at checkpoints. End-to-end proof. Needs mGBA python bindings (non-blocking if absent). |
| `make shiftcheck` | 0+1+2 | The static gate (no emulator). |

## Why ranking, not a flat list

Perfect static precision is impossible: incbin'd audio/graphics/animation data
contains byte runs that coincidentally look like unrelocated ROM pointers. The
shared classifier (`_classify.py`) buckets findings so the signal isn't drowned:

- **[A] HIGH** — a *coherent* pointer table (≥4 consecutive entries pointing into
  1–2 symbols) inside a **MIXED** object (one that also has real relocated pointers).
  This is the actionable worklist; the tools exit non-zero when it is non-empty.
- **[B] MIXED scattered** — unrelocated words in pointer-bearing objects that aren't
  a coherent table. Review / let Layers 2–3 adjudicate.
- **[C] BLOB** — objects with no relocations at all (pure incbin data); almost
  certainly coincidental.
- **[D] BLOB-INTERNAL** — a word inside the very symbol it points at (embedded blob
  data, e.g. a banim palette), not a typed pointer table.

Coincidental values in the cartridge header range (`< 0x08000100`) and `.text`
literal pools are filtered out; pinned-region (`≥ 0x08C00000`) words are bucketed
separately.

## Validation case (now fixed)

When first run, both Layers 1 and 2 independently isolated exactly one
high-confidence finding:

```
src/opinfo.o  (64 suspects in 1 table(s); targets: gUnkData_96(64))
  0x08A2F340 = 0x08A39148  -> gUnkData_96+0x1E48
  ...
```

`gOpinfo_1[]` in `src/opinfo.c` was a 64-entry table of raw `(u8*)0x08A3xxxx` casts
into the graphics blob `gUnkData_96`. Each was rewritten as a symbol reference,
`(u8*)gUnkData_96 + 0x1E48` (gUnkData_96 is `u16[]`, so the byte offset needs the
`(u8*)` cast). This is **byte-identical** in the matching build (so `make compare`
still passes) but is now a relocation, so it shifts correctly. After the fix the
HIGH bucket is empty:

```
[A] HIGH-CONFIDENCE ... : 0 in 0 object(s)
RESULT: no high-confidence shiftable-region suspects.
```

A separate probe also confirmed there are no hardcoded *jump* tables (runs of
unrelocated pointers at distinct symbol starts) elsewhere in the shiftable region —
`gOpinfo_1` was the only real hardcoded-pointer table in the typed data. The
remaining `[B]`/`[C]`/`[D]` words are coincidental incbin data, not pointers; the
definitive check on those is Layer 3 (runtime).

## Layer 3 runtime results

Backend: `mgba_oracle.c`, a small program linking `libmgba` (apt: `libmgba-dev`);
`run_dynamic.py` compiles it on demand. (The mGBA Python bindings aren't on PyPI for
this environment.) It is validated and non-vacuous:

- **Positive** — fixed ROM vs a shifted ROM (`+0x40000` and `+0x80000`): framebuffers
  are **identical at every checkpoint** through boot → title → intro → menus
  (frames 120–3000). The per-frame hashes vary (real changing screens), so the
  oracle is reading actual output, not a constant.
- **Oracle sanity** — base vs a heavily-corrupted ROM: correctly reports
  **DIVERGENCE**. So it does detect differences.
- **Coverage caveat** — reverting the fix and shifting did *not* diverge under
  generic input, because `gOpinfo_1` is only used on the deep in-game class-reel
  screen, which START/A spam doesn't reach in ~50 s. Layer 3 confirms the broadly
  exercised paths are shiftable; the **static layers** are what conclusively caught
  `opinfo` (the literal had no relocation; the fix gives it one). Reaching the
  class-reel screen would need a targeted input script / save state.

## Files

- `scan_build_addrs.py` — Layer 0.
- `emit_relocs_link.sh` — single source of truth for the production link line,
  parameterized (used with `-q` for Layer 1 and with a shifted ldscript for Layer 2).
- `scan_relocs.py` — Layer 1.
- `gen_shifted_ldscript.py`, `diff_shift.py` — Layer 2.
- `run_dynamic.py` — Layer 3.
- `_classify.py` — shared classifier (Layers 1 and 2 feed it different "relocated"
  oracles: the reloc table vs. tracks-the-shift).
- `allowlist.txt` — value ranges proven coincidental (keep tight; prefer fixing).
