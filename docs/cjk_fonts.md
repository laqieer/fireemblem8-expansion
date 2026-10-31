# Deterministic CJK font assets

This directory contract supplies the final Japanese and Simplified Chinese
system/talk glyph bitmaps used by the modern UTF-8 renderer. Final assets are
FEHRR-first: the original FE8J (`glyph/fe8j`) and FE8CN (`glyph/fe8cn`) glyph
bitmaps and width records win whenever they cover a scalar. Only missing
glyphs retain the immutable FEBuilderGBA/Noto result. Bare `make` does not
invoke FEHRR, FEBuilderGBA, .NET, a GUI, the network, or a system font lookup.

## Resource index storage

The host tools accept the existing plain JSON representation and the version-1
`cjk-resource-index` envelope for `fonts/cjk/fehrr-sources.json` and
`fonts/cjk/runtime_usage.json`. Both default indexes are packed.
Generation preserves the representation already present. Both
formats decode to the same complete JSON object.
The shared `scripts.fonttools.cjk.resource_index` API provides `decode_index`,
`encode_index(value, packed=False)`, `load_index` and `encode_like_index`.

The envelope has exactly `kind`, `version`, `encoding`, `decoded_bytes` and
`payload` fields. `kind` is `cjk-resource-index`, integer `version` is `1`,
`encoding` is `xz-base64`, integer `decoded_bytes` is the canonical UTF-8 JSON
byte count, and `payload` is base64 of one XZ stream. Encoding uses sorted,
two-space-indented UTF-8 JSON with a final newline and the standard-library XZ
preset 6. Repeated encoding is deterministic. Stored and decoded indexes are
bounded to 4 MiB; XZ decoding has a 64 MiB decoder-memory bound. Unknown fields
or versions, duplicate keys, malformed data, incorrect size/checksum, truncated
or trailing streams and nested envelopes fail explicitly.

Logical schema, FEHRR source priority, immutable FEBuilder baseline and
glyph/width/bitmap checks are unchanged. Physical checksums bind the actual
stored representation, not a reconstruction of its decoded JSON. A format
conversion never creates font, content or provenance approval.

For an explicitly selected index in an owned checkout or fixture:

```bash
python3 -m scripts.fonttools.cjk --root OWNED_ROOT repack-index --index fehrr --format packed
python3 -m scripts.fonttools.cjk --root OWNED_ROOT repack-index --index runtime-usage --format packed
python3 -m scripts.fonttools.cjk --root OWNED_ROOT check
```

Use `--format plain` to reverse the conversion. The operation validates the
existing compact assets (and generated inventory for runtime usage), prepares
all bytes before writing, and updates only the selected index and its existing
inventory/asset-manifest checksum chain. It does not provide a multi-file
transaction: use an owned, otherwise idle root and inspect every resulting
file. Invalid input is rejected before writes. Do not hand-edit generated
indexes or checksums. Revert a migration through the normal Git delivery path.

Dependencies are the existing CJK tools and Python 3.12 standard library;
there is no new package, service or feature conflict. This host-only interface
changes no atlas, ROM/RAM, locale, configuration identity, save epoch, modern
debug/release or archival behavior. Default-index migrations are independent
dependent contracts [#349](https://github.com/laqieer/fireemblem8-expansion/issues/349)
and [#350](https://github.com/laqieer/fireemblem8-expansion/issues/350).

### TC-CJK-RESOURCE-STORAGE-001: bounded lossless resource indexes

Issue [#348](https://github.com/laqieer/fireemblem8-expansion/issues/348).
Start from a clean source checkout on Linux/CPython 3.12 with the committed CJK
inputs. No ROM, emulator, font-source checkout or manual judgment is needed.

1. Run `python3 -B -m unittest scripts.fonttools.cjk.tests.test_resource_index -v`.
   The test creates an owned temporary source/font fixture, converts both
   indexes through the actual CLI, and runs existing inventory/compact-asset
   consumers against plain and packed inputs.
2. Require complete decoded equality, repeated deterministic encoding,
   unchanged plain bytes and every glyph/width/bitmap byte, valid physical
   checksums, and exact original files after reverse conversion. The
   pre-interface FEHRR reader rejects the packed fixture. An explicit plain
   fixture remains the representation control even after default migrations;
   obtain pre-migration plain data from the exact parent Git blob, not by
   assuming the current checkout still stores that index in plain form.
3. Exercise malformed encoding, unknown fields/version, boolean size/version,
   wrong size, damaged/truncated/trailing streams and oversized expansion.
   Require explicit errors; a corrupt owned CLI input must leave every
   existing index/checksum file unchanged.

The automation is the codec module above plus actual package/inventory/CLI
consumers, not a raw-source assertion. It removes only its owned temporary
files and never converts stock inputs. All existing source/schema/provenance
checks remain required. This is storage evidence, not font-art, selected-game,
rendered-text or supported ROM-profile qualification.

#### Default FEHRR migration variant

Issue [#349](https://github.com/laqieer/fireemblem8-expansion/issues/349)
uses the same case after the storage interface lands. From the exact clean
parent, run `repack-index --index fehrr --format packed` and the existing
`python3 -m scripts.fonttools.cjk check`. Preserve original plain bytes in Git,
not in a second committed snapshot.

Compare the complete decoded before/after objects from the immutable parent
and candidate Git blobs using `decode_index`, including every source,
selection and glyph record. Compare all twelve bitmap/codepoint/width files
named by the four original manifest assets byte-for-byte; only the FEHRR index
and its physical checksum in the asset manifest change among generated files.
With `BASE` set to the exact parent revision, run from the source root:

```bash
python3 - "$BASE" <<'PY'
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from scripts.fonttools.cjk.resource_index import decode_index

def at(revision, path):
    return subprocess.check_output(["git", "show", f"{revision}:{path}"])

base = sys.argv[1]
index = "fonts/cjk/fehrr-sources.json"
manifest = "graphics/fonts/cjk/manifest.json"
stored = at("HEAD", index)
assert decode_index(at(base, index)) == decode_index(stored)
expected = json.loads(at(base, manifest))
for asset in expected["assets"].values():
    for kind in ("bitmap", "codepoints", "widths"):
        path = asset[kind]["path"]
        assert at(base, path) == at("HEAD", path)
expected["sources"]["fehrr_priority"]["sha256"] = hashlib.sha256(stored).hexdigest()
assert json.loads(at("HEAD", manifest)) == expected
usage = "fonts/cjk/runtime_usage.json"
assert at(base, usage) == at("HEAD", usage)
print("complete decoded index, all twelve atlas files and checksum-only metadata match")
PY
```

This compares immutable committed candidates; before committing, inspect the
generated working-tree files with the same decoder and run the focused suite.
Repeat the pack operation and require identical bytes, then run the focused
resource-index suite from the now-packed starting state. Its stale-checksum
and malformed-input controls must still reject. No default runtime-usage,
font, atlas, locale or game-data conversion is part of this variant.

Report the complete generated-data deletion/replacement in review-size
evidence. The index is indivisible: partial JSON conversion is invalid. The
generated-line review exception does not exempt any blob from the existing
4 MiB old-plus-new raw gate or replace exact candidate/master delivery gates.

#### Default runtime-usage migration variant

Issue [#350](https://github.com/laqieer/fireemblem8-expansion/issues/350)
uses the same indexed case after the interface and FEHRR conversion. From
the exact clean parent, run `repack-index --index runtime-usage --format packed`,
the focused resource-index suite and `python3 -m scripts.fonttools.cjk check`.
Both packed starting representations must retain exact round-trip restoration.
No classification, message, corpus, source-priority or atlas change is allowed.

The physical chain is usage bytes -> inventory output byte-count/hash ->
asset-manifest inventory checksum. The FEHRR index has no inventory-hash field
and remains byte-identical. With `BASE` set to the exact parent, verify the
complete committed conversion from the source root:

```bash
python3 - "$BASE" <<'PY'
import hashlib
import json
import subprocess
import sys
from scripts.fonttools.cjk.resource_index import ENVELOPE_KIND, decode_index

def at(revision, path):
    return subprocess.check_output(["git", "show", f"{revision}:{path}"])

if not sys.argv[1].strip():
    raise SystemExit("BASE must identify the exact plain parent commit")
base = subprocess.check_output(
    ["git", "rev-parse", "--verify", "--end-of-options", sys.argv[1] + "^{commit}"],
    text=True,
).strip()
head = subprocess.check_output(["git", "rev-parse", "HEAD^{commit}"], text=True).strip()
if base == head:
    raise SystemExit("BASE must differ from the packed candidate")
subprocess.run(["git", "merge-base", "--is-ancestor", base, head], check=True)
usage = "fonts/cjk/runtime_usage.json"
inventory = "fonts/cjk/inventory.json"
manifest = "graphics/fonts/cjk/manifest.json"
stored = at("HEAD", usage)
prior = at(base, usage)
if json.loads(prior).get("kind") == ENVELOPE_KIND:
    raise SystemExit("BASE must contain the plain runtime-usage index")
if json.loads(stored).get("kind") != ENVELOPE_KIND:
    raise SystemExit("HEAD must contain the packed runtime-usage index")
assert decode_index(prior) == decode_index(stored)
expected = json.loads(at(base, inventory))
expected["outputs"][usage] = {
    "byte_count": len(stored), "sha256": hashlib.sha256(stored).hexdigest(),
}
assert json.loads(at("HEAD", inventory)) == expected
expected = json.loads(at(base, manifest))
for asset in expected["assets"].values():
    for kind in ("bitmap", "codepoints", "widths"):
        path = asset[kind]["path"]
        assert at(base, path) == at("HEAD", path)
expected["sources"]["inventory"]["sha256"] = hashlib.sha256(at("HEAD", inventory)).hexdigest()
assert json.loads(at("HEAD", manifest)) == expected
fehrr = "fonts/cjk/fehrr-sources.json"
assert at(base, fehrr) == at("HEAD", fehrr)
print("all usage records/policies, twelve atlas files and checksum-only metadata match")
PY
```

Repeat conversion and require identical outputs. The actual generated-file
consumer must reject an owned fixture carrying the old usage output record,
without rewriting it, and accept it again after restoring current metadata.
Existing corrupt/truncated/trailing/bounded-input controls remain mandatory.
This is another indivisible generated-index conversion: report every deleted
line and alternative complete-data proof, retain the unchanged 4 MiB raw
boundary and all parent-first candidate/master completion gates. No manual
judgment, font art or production-game qualification is supplied.

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

Direct talk-consumer files are traversed in canonical path order. If multiple
files name the same message, the existing last-consumer explanation therefore
has a stable owner regardless of filesystem enumeration order; all unique
message IDs remain covered.

### TC-CJK-INVENTORY-ORDER-001: deterministic consumer inventory

Issue [#272](https://github.com/laqieer/fireemblem8-expansion/issues/272) fixes
host inventory reproducibility, with no font, glyph, locale text, ROM/RAM,
save, configuration or archival change. Start from a clean checkout with
Python 3. No optional profile, emulator, ROM or manual judgment is required.

1. Run `python3 -m unittest scripts.fonttools.cjk.tests.test_cjk_fonts.CjkFontTests.test_direct_talk_inventory_is_independent_of_directory_order`.
   The fixture creates real C source files with overlapping and unique
   message consumers, reverses their enumeration, and requires identical
   classification/reasons with every unique target retained. Removing the
   canonical traversal produces two different explanations for message 15.
2. Run `python3 -m unittest scripts.fonttools.cjk.tests.test_cjk_fonts.CjkFontTests.test_inventory_regeneration_is_byte_identical`.
   This generates every inventory output under both forward and reverse
   source enumeration, compares the output bytes between orders, and checks
   both against committed metadata. Run `make -f cjk_fonts.mk cjk-fonts-check`
   for the existing combined inventory/provenance boundary.
3. When intentionally refreshing consumer metadata, use
   `make -f cjk_fonts.mk cjk-fonts-refresh-provenance` and inspect the diff.
   Explanations and their dependent inventory checksums may change, but
   corpora, glyph counts, raster payloads and widths must not. The existing
   provenance-refresh regression rejects a changed font oracle.

The temporary fixture cleans its own files; generation only writes the
existing named inventory outputs. Dependencies are the existing CJK
inventory/provenance interfaces; no new dependency or feature conflict is
introduced. This procedure does not claim native Make evaluator qualification.

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

These are parsed/binary artifact contracts, not source-spelling checks:
renaming an implementation helper does not affect them, while a missing,
reordered, malformed, or width-incompatible glyph changes the generated
inventory or decoded asset and fails validation.
