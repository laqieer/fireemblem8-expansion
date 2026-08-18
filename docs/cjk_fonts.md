# Deterministic CJK font assets

This directory contract supplies the final Japanese and Simplified Chinese
system/talk glyph bitmaps used by the modern UTF-8 renderer. Final assets are
FEHRR-first: the original FE8J (`glyph/fe8j`) and FE8CN (`glyph/fe8cn`) glyph
bitmaps and width records win whenever they cover a scalar. Only missing
glyphs retain the immutable FEBuilderGBA/Noto result. Bare `make` does not
invoke FEHRR, FEBuilderGBA, .NET, a GUI, the network, or a system font lookup.

## Inventory contract

Run:

```bash
python3 -m scripts.fonttools.cjk generate-inventory
python3 -m scripts.fonttools.cjk check
```

If catalog/source provenance changes but the regenerated CJK corpora remain
byte-identical to the committed FEBuilder oracle, refresh only the compact
asset inventory hash with:

```bash
python3 -m scripts.fonttools.cjk refresh-provenance
```

This command first verifies the current manifest, corpora, and immutable
generation report. It does not permit changed glyph rows or raster payloads
to inherit stale FEBuilder evidence.

The inventory reads the committed normalized sources:

- `texts/locales/ja/indexed.txt`;
- materialized providers in `texts/locales/ja/raw.json`;
- `texts/locales/zh-Hans/indexed.txt`;
- unique records in `texts/locales/zh-Hans/raw.json`;
- canonical 329-string full-game authored catalogs under
  `texts/locales/authored/`, whose manifest pins the source queue and every
  reviewed shard;
- active `texts/expansion` catalog strings, resolved through the English
  catalog only for defensive sparse-profile behavior; the committed JA/ZH
  catalogs contain all 61 active keys and contribute zero fallback strings.

Bracketed game controls (named or `[CTRL:HHHH]`) and `{N}` expansion
placeholders are not glyphs.
Visible text is normalized with NFC only. ASCII remains in the existing
runtime fonts. The source contains one non-ASCII spacing scalar, U+3000
IDEOGRAPHIC SPACE; FEBuilder schema v1 rejects whitespace, so U+3000 is
reported separately and the runtime gives it an explicit advance rather than
performing a bitmap lookup. Verified FE8J indexed/raw providers preserve the
original SJIS `0x8140` token and its 6-pixel advance; authored Unicode U+3000
remains a distinct 16-pixel ideographic space.

Current deterministic counts:

| Locale | Source non-ASCII | System glyphs | Talk glyphs |
| --- | ---: | ---: | --- |
| `ja` | 1,786 | 1,174 | 1,731 |
| `zh-Hans` | 2,442 | 1,459 | 2,382 |
| union | 3,256 | 3,255 | U+3000 is the one spacing scalar |

The sorted corpora are in `fonts/cjk/corpora/`; human-readable scalar maps are
in `fonts/cjk/maps/`; counts, input hashes, output hashes, token rules, and
contributions are in `fonts/cjk/inventory.json`.

The same source corpora feed the full-game line-break contract. Before catalog
compression, `python3 -m scripts.localization.game_catalog check-width`
measures localized payloads with these committed per-style width tables and
inserts only safe runtime `[NL]` controls in generated output. It never adds a
glyph, so a successful width check also proves every rendered scalar was in
the atlas corpus; `make -f cjk_fonts.mk cjk-fonts-check cjk-fonts-test`
remains the independent atlas/provenance gate.

## FEHRR source-priority import

The committed source lock `fonts/cjk/fehrr-sources.json` records the exact
clean FEHRR commit, every selected original-game PNG hash, its packed 2bpp
hash, and its source-game width. It covers the current corpora as follows:

| Runtime asset | FEHRR source | Original glyphs | FEBuilder fallback |
| --- | --- | ---: | ---: |
| `ja.system` | `fe8j` item, then text | 1,098 same-style + 73 cross-style + 3 supplemental | 0 |
| `ja.talk` | `fe8j` text, then item | 1,729 same-style + 2 cross-style | 0 |
| `zh-Hans.system` | `fe8cn` item, then text | 1,317 same-style + 106 cross-style + 36 supplemental | 0 |
| `zh-Hans.talk` | `fe8cn` text, then item | 2,354 same-style + 27 supplemental | 1 (`％`, absent from configured tiers) |

The corpora are derived from checked runtime usage, not copied wholesale:
system has 1,174 JA / 1,459 ZH glyphs; talk has 1,731 JA / 2,382 ZH glyphs.
Descriptions are deliberately in both styles. The lock records every runtime
string's system/talk/both classification and rejects an unclassified string or
accidental corpus re-union. FEHRR priority is same-game same-style, then
same-game cross-style, then the pinned FEHRR punctuation/common-language/
missing-glyph tiers, then the verified full-union FEBuilder baseline. A selected
same-game PNG that is completely blank is treated as unavailable and follows
the same-game cross-style fallback path; this keeps blank source art from
entering the runtime font. Thus a fallback count is never described as an FEBuilder
export omission or as proof that an original-game glyph is absent. The one
remaining fallback is U+FF05 FULLWIDTH PERCENT in the Simplified-Chinese talk
font because it is absent from all configured style tiers. FE8J has one
duplicate text declaration for U+30FB; the first declaration (7 px) remains
the selected source-map width, while its blank text PNG uses the visible item
glyph and the ignored 1 px declaration is recorded in the lock.

To refresh source-priority assets, use a clean checkout with the canonical
`https://github.com/laqieer/FEHRR.git` `origin`:

```bash
make -f cjk_fonts.mk cjk-fonts-import-fehrr FEHRR_ROOT=../FEHRR
```

After a runtime usage/classification change, regenerate the split assets with:

```bash
make -f cjk_fonts.mk cjk-fonts-split-runtime FEHRR_ROOT=../FEHRR
```

The command reads only `glyph/fe8j` and `glyph/fe8cn`, converts their indexed
16×16 PNG palette indices directly to the runtime 2bpp format, updates the
four aggregate glyph and width files, and rewrites the source lock and compact
manifest. It rejects a dirty/wrong repository, malformed maps or PNGs, source
coverage changes without a fresh fallback baseline, and non-deterministic
output. Do not hand-edit the lock or aggregate outputs.

When intentionally updating to an FEHRR revision whose coverage changes,
first regenerate the FEBuilder baseline through the maintainer sequence below,
then apply this import. This guarantees newly missing scalars return to the
FEBuilderGBA output rather than an obsolete FEHRR glyph.

## Licensed FEBuilder fallback inputs

The repository vendors unmodified, region-appropriate Noto CJK 2.004 Regular
SubsetOTF files from upstream commit
`f8d157532fbfaeda587e826d4cd5b21a49186f7c`:

| Locale | File | Bytes | SHA-256 |
| --- | --- | ---: | --- |
| `ja` | `NotoSansJP-Regular.otf` | 4,533,028 | `dff723ba59d57d136764a04b9b2d03205544f7cd785a711442d6d2d085ac5073` |
| `zh-Hans` | `NotoSansSC-Regular.otf` | 8,331,336 | `faa6c9df652116dde789d351359f3d7e5d2285a2b2a1f04a2d7244df706d5ea9` |

Both are licensed under OFL-1.1. The exact upstream license is vendored as
`fonts/cjk/licenses/Noto-CJK-OFL-1.1.txt` with SHA-256
`6a73f9541c2de74158c0e7cf6b0a58ef774f5a780bf191f2d7ec9cc53efe2bf2`.
See `fonts/cjk/font-sources.json` and
`fonts/cjk/THIRD_PARTY_NOTICES.md`.

The generator/source-font quality follow-up is tracked upstream at
[FEBuilderGBA #2092](https://github.com/laqieer/FEBuilderGBA/issues/2092).

Normal work is offline. If a vendored input must be restored, the explicit
network bootstrap uses immutable URLs and refuses a hash mismatch:

```bash
python3 -m scripts.fonttools.cjk bootstrap-fonts
```

## FEBuilderGBA maintainer gate

`fonts/cjk/febuilder-manifest.json` is strict schema v1 with four
`main-16x16` jobs:

- `ja-system`: item style, 12 px;
- `ja-talk`: text style, 11 px;
- `zh-hans-system`: item style, 12 px;
- `zh-hans-talk`: text style, 11 px.

Each job uses the complete locale bitmap corpus and a deterministic dense
temporary `moji` mapping. Runtime lookup does not use that mapping; it uses the
generated Unicode tables described below. This is the fallback baseline, not
the final priority order.

Build FEBuilderGBA CLI issue #2034 from a trusted checkout, then set
`FEBUILDER_CLI`. For a framework-dependent build this may contain both the
`dotnet` executable and CLI DLL:

```bash
make -f cjk_fonts.mk cjk-fonts-febuilder-all \
  FEBUILDER_CLI="/path/to/dotnet /path/to/FEBuilderGBA.CLI.dll"
```

The explicit maintainer sequence is:

1. dry-run provenance and capacity planning;
2. generate the manifest jobs and their derived corpus row count;
3. validate with the immutable external generation report;
4. non-rasterizing byte-for-byte roundtrip;
5. record the generation report and gate evidence after all commands exit zero;
6. create a deterministic temporary archive under `build/tmp/cjk-fonts/`,
   import it twice to prove deterministic compact output;
7. apply the clean, pinned FEHRR source-priority import; and
8. check the hybrid assets.

The committed run used FEBuilderGBA commit
`c1700532b27c579511585ca63e2d63222b9ea646` and .NET SDK 10.0.302.
Machine-readable evidence is in
`fonts/cjk/reports/febuilder-gates.json`; the full immutable oracle is
`fonts/cjk/reports/febuilder-generation-report.json`. FEBuilder package
directories and archives are temporary maintainer artifacts under
`build/tmp/cjk-fonts/`; neither loose PNG package trees nor ZIP archives are
committed. Delete that ignored directory at any time and rerun the command
above to reproduce the gate.

`cjk-fonts-febuilder-all` performs steps 1–8 in order and therefore requires
both `FEBUILDER_CLI` and a clean `FEHRR_ROOT` checkout. Running
`cjk-fonts-import` by itself creates only the temporary FEBuilder baseline; do
not commit that intermediate state.

## Sprint 3 aggregate asset contract

For each `locale.style`, `graphics/fonts/cjk/` contains:

- `*.codepoints.u32le`: sorted unique little-endian `uint32` Unicode scalars;
- `*.widths.u8`: one unsigned width byte per scalar, range 1 through 16;
- `*.glyphs.2bpp`: one 64-byte 16x16 bitmap per scalar, row-major, four
  low-bit-first 2bpp pixels per byte.

Binary-search the codepoint table. The matching index selects one width byte
and one fixed-stride bitmap. `graphics/fonts/cjk/manifest.json` pins every
asset hash and all package/report provenance.

The four locale/style payloads total **594,090 bytes**, or **594,096 bytes**
when every blob is independently aligned to four bytes. This budget includes
the bitmap, width, and Unicode index data, but not future linker/table
wrappers.

## Verification

Normal stdlib-only verification:

```bash
python3 -m scripts.fonttools.cjk check
python3 -m unittest discover -s scripts/fonttools/cjk/tests -p 'test_*.py' -v
```

These checks regenerate the inventory, derive expected job/row counts from the
manifest and corpora, verify the recorded FEBuilder gates and report hashes,
require nonzero 64-byte glyphs, validate widths and exact catalog/game scalar
coverage, compare every aggregate hash, and reject ZIP files or ZIP magic in
the committed font domain. They also verify every FEHRR-selected packed glyph
and original width against the pinned source lock, and verify every scalar
absent from FEHRR is byte-for-byte the recorded FEBuilder fallback. They do
not invoke FEHRR, FEBuilderGBA, or download anything. The explicit maintainer
gate above performs the temporary package generation, validation, roundtrip,
and source-priority import.
