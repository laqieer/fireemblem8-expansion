# Full-game locale source imports

`texts/locales/` contains deterministic, UTF-8 source imports for full-game
Japanese (`ja`) and Simplified Chinese (`zh-Hans`) localization. Verified
FE8U-target decisions feed the opt-in modern game catalog; raw-address values
remain import provenance only and never become runtime keys.

## Layout and provenance

- `source/fe8j/jp_texts.txt`, `source/fe8j/jp_textdefs.txt`,
  `source/fe8j/msg_map.tsv`, and `source/fe8cn/FE8CN.txt`: byte-exact,
  hash-pinned authorized input snapshots. These committed raw files are the
  independent regeneration source.
- `indexed_overrides.json` and its hash-pinned
  `indexed_audit_overrides.json` supplement: corrections applied only after
  control normalization. Entries pin either the exact normalized source text
  or its SHA-256, plus a full replacement or exact one-occurrence fragment
  replacements, FE8U target IDs, context, provenance audit, and reason. The
  importer rejects source/supplement/payload drift, stale fragments, or any
  change to control/newline/placeholder placement. Physical FE8CN payload
  lines normalize to canonical `[CTRL:0001]` runtime line controls; they are
  never emitted as literal UTF-8 `0x0A`. The raw FE8J/FE8CN snapshots remain
  byte-exact.
- `ja/indexed.txt`: 3,339 FE8J-layout messages (`0x0000` through `0x0D0A`).
- `ja/control_defs.txt`: FE8J source aliases mapped to canonical controls. It is
  an alias table, not normalized locale payload.
- `ja/raw.json`: 119 materialized FE8J raw-symbol providers keyed by FE8U
  target ID plus the verified evidence symbol. This includes the three
  inline goal-window labels and prevents a same-number FE8J indexed message
  from being substituted for a raw provider. The catalog pins and must exactly
  match the committed `source/fe8j/raw_symbols.json` symbol/value extraction;
  a revision name alone is not accepted as provenance.
- `zh-Hans/indexed.txt`: 3,339 FE8CN messages using the FE8J indexed layout.
- `zh-Hans/raw.json`: 152 raw-address occurrences deduplicated to 143 stable
  `fe8cn.raw.import-NNNN` IDs. IDs are assigned by pinned source import order,
  not ROM address. Address, source lines, and duplicate occurrences exist only
  under each record's `provenance` field.
- `mapping/fe8j_to_fe8u.candidates.json`: a sparse import of the supplied
  `msg_map.tsv`. It is explicitly `candidate`, `authoritative: false`, and
  unverified.
- `mapping/fe8u_structural_evidence.json`: hash-pinned evidence harvested from
  matching named FE8U/FE8J structures. Each slot records its subsystem,
  evidence kind, table/symbol/key, confidence, source paths, and rationale.
  A Japanese `literal` provider additionally names an existing tracked C
  source file, table symbol, `message_id=0xNNNN` key, and bounded initializer
  context SHA-256. Validation opens the source and requires the keyed entry's
  exact literal and fingerprint to match.
- `mapping/structural_completion_evidence.json`: separate, non-authoritative
  historical evidence for targets that were explicit English fallbacks before
  final promotion. It records bounded FE8J proposals, source-payload hashes,
  stable semantic slots, split/merge collisions, and residual targets.
- `mapping/fe8u_target_map.json`: the authoritative 3,414-row FE8U target
  decision ledger generated from committed evidence and reviewed authored
  shards. Every production row is indexed, raw, or authored.
- `mapping/fe8u_target_map.coverage.json`: deterministic source-kind and
  subsystem counts plus every fallback target ID and reason.
- `source/febuilder/translate_textid_FE8.txt`: byte-exact, hash-pinned
  FEBuilder FE8 translator map snapshot. Normal checks never read a sibling
  FEBuilderGBA checkout.
- `mapping/febuilder_alignment_evidence.json`: deterministic,
  non-authoritative FEBuilder target-candidate ledger. It preserves indexed
  versus pointer rows, literal substitutions, NOTFOUND rows, duplicate source
  keys, target collision groups, payload references, and structural
  comparisons without changing the release map.
- `mapping/combined_fallback_coverage.json`: generated final-owner handoff that
  recomputes the current (now empty) fallback subset against FEBuilder and
  structural history while preserving every conflict and collision ledger.
- `mapping/final_mapping_report.json`: deterministic promotion counts, input
  fingerprints, residual count, and the zero-fallback final-delivery policy.
- `mapping/audit_semantic_corrections.json`: target/call-site keyed authored
  corrections with English, incorrect-provider, and replacement payload
  SHA-256 pins; raw snapshots stay immutable.
- `mapping/ending_layout_metrics.json`: generated all-title/all-solo/all-paired
  ending metric report using the real 15-tile/26-tile `Text` allocations and
  committed runtime CJK widths.
- `runtime_latin_span_review.json`: the exact baseline review of every
  Latin-bearing target. Each scope/locale/target/span decision pins payload
  hashes, occurrence counts, approval or localization, factual reason, and
  source; it contains no regex or category-wide exemption.
- `mapping/runtime_english_leakage.json`: deterministic audit of all 3,414
  final JA payloads, all 3,414 final ZH payloads, and both 143-record
  materialized raw surfaces.
- `mapping/authored_translation_queue.json`: byte-identical historical source
  queue for the 259 reviewed targets, with canonical English,
  controls/placeholders, subsystem, reference sites, no-provider reason,
  suggested key, and grouping. The final report proves all rows fulfilled.
- `authored/manifest.json`: pins the source queue revision/SHA-256 and every
  reviewed shard SHA-256.
- `authored/shards/*.json`: normalized, locale-paired reviewed translations.
- `authored/catalog.{ja,zh-Hans}.json`: deterministic canonical runtime
  catalogs generated from the pinned shard union.
- `mapping/raw_surface_decisions.json`: the 29 audited records that were not
  part of the original 114 raw-to-game-ID mappings. Each has a concrete game
  message ID, semantic expansion key, explicit English fallback, or documented
  exclusion and call-site anchors.
- `mapping/raw_surface_closure.json`: deterministic 143-record closure
  manifest. It is rebuilt from the raw source, verified FE8U map, deferred
  decisions, expansion registry/catalogs, and live source anchors.
- `manifest.json`: pinned input SHA-256 hashes, artifact hashes, exact counts,
  locale IDs, codepoint counts, and maximum UTF-8 payload lengths.

FE8J source IDs are not FE8U target IDs. The FE8U target universe has 3,414
IDs, while the indexed FE8J layout has 3,339. Candidate rows must therefore
remain unresolved until semantic verification produces an authoritative
mapping.

## Canonical controls

Normalized payload has one accepted control spelling:

```text
[CTRL:HHHH]
```

`HHHH` is exactly four uppercase hexadecimal digits representing one u16
control unit. The importer converts FE8J `[$HHHH]`, FE8CN `[0xHHH]` /
`[0xHHHH]`, and pinned named aliases to that form. Unknown, malformed, mixed,
or bare marker-like tokens such as `[0001]` are rejected rather than retained
as text. `scripts.localization.game_locales` exports APIs to expand a
canonical token to its exact u16 value and little-endian bytes.

## Regeneration and check

Regenerate only when intentionally refreshing normalized outputs:

```bash
python3 -m scripts.localization.game_locales regenerate
```

The required source-of-truth gate regenerates every artifact and the manifest
from the committed raw snapshots in memory, then compares committed output
byte-for-byte:

```bash
python3 -m scripts.localization.game_locales check
```

This check does not trust artifact hashes recorded by the committed manifest,
so changing an artifact and its manifest entry together still fails. The four
raw snapshots must also match the independent SHA-256 pins in
`scripts/localization/game_locales/importer.py`.

The current override layer applies 48 Japanese and 85 Simplified Chinese
indexed corrections. `Healing` is the ordinary HP-restoring staff cue
(`癒やし` / `治疗`), while `Curing` is the status-removal cue
(`浄化` / `净化`); they are deliberately not collapsed to one generic
machine translation. Other corrected Sound Room/UI titles include
`NOW LOADING`, `NO DATA`, `Fire Emblem Theme`, `Prologue`, `Follow me!`,
`Indignation`, `Sadness time`, `Comical time`, `Work out a plot`,
`Game Over`, `Fly with the Breeze`, and `Epilogue`.

After changing an override, refresh every derived evidence/report layer:

```bash
python3 -m scripts.localization.game_locales regenerate
python3 -m scripts.localization.game_locales refresh-authored-provenance
python3 -m scripts.localization.game_locales build-authored-catalogs
python3 -m scripts.localization.game_locales build-febuilder-evidence
python3 -m scripts.localization.game_locales build-final-mapping
python3 -m scripts.localization.game_locales build-combined-coverage
python3 -m scripts.localization.game_locales build-raw-closure
python3 -m scripts.localization.game_catalog audit-leakage
make -f cjk_fonts.mk cjk-fonts-febuilder-all \
  FEBUILDER_CLI="/path/to/dotnet /path/to/FEBuilderGBA.CLI.dll"
```

`build-final-mapping` refreshes only normalized FE8J/payload-derived fields in
the structural evidence before revalidating the full evidence object. The
fulfilled authored queue remains byte-identical historical provenance; the
final report records both its historical pre-authored map hash and the current
hash when later payload-only evidence changes make them differ.

The explicit `import` command remains available for checking prospective
external replacements, but it accepts only inputs matching those pins.

## FEBuilder alignment evidence

FEBuilderGBA's translator consumes `translate_textid_FE8.txt` with semantics
that are broader than a two-column TSV:

- `U.IsComment` and `U.ClipComment` control whole-line and inline comments;
- `U.atoh` accepts the leading hexadecimal prefix rather than requiring the
  complete token to be hexadecimal;
- source key zero and rows with fewer than two tab-separated fields are
  skipped;
- a source key in the FE8 ROM pointer range is dereferenced by FEBuilder, so
  the importer preserves it as a pointer/raw key rather than treating it as an
  indexed message ID;
- a destination token containing `|` uses the literal suffix instead of
  decoding the numeric destination and is therefore not an alignment
  candidate;
- a missing/non-positive destination creates FEBuilder's NOTFOUND entry and is
  retained only as non-candidate evidence.

The pinned source profile is 3,339 sequential indexed rows, including 3,006
rows with a positive destination prefix, followed by 110 pointer rows. One
indexed row has no destination column. The pointer key `0x080D29BC` occurs
twice: once with destination `0x0001` and once as NOTFOUND. Because the current
normalized raw import has no payload at that address, neither occurrence is
invented as a payload reference.

For every usable target candidate, the importer verifies:

1. the FE8U target is inside the current 3,414-entry namespace;
2. indexed source IDs exist in both normalized FE8J and FE8CN payloads;
3. pointer keys resolve to a stable `fe8cn.raw.import-NNNN` address;
4. payload SHA-256 values and all input-file hashes are recorded;
5. the candidate identity is compared with the committed structural evidence.

Target marks are evidence classifications, not release states:

- `agrees-with-structural`: the exact indexed/raw source identity is already
  independently proven;
- `conflicts`: structural evidence has a comparable source type but a
  different identity;
- `unique-uncontested`: no comparable conflict and no unresolved differing
  payload collision;
- `collision-needs-context`: multiple FEBuilder rows offer different
  normalized FE8CN payloads and structural identity does not select one.

A target may carry both `conflicts` and `collision-needs-context`; `0x0647` is
the pinned example. Every target has `promotion_eligible: false`, and the
document-level promotion policy forbids automatic promotion of all candidates,
especially conflicts and collisions. The importer pins 12 structural conflict
targets and 17 unresolved differing-payload collision targets as drift gates.

Build or verify the committed ledger:

```bash
python3 -m scripts.localization.game_locales build-febuilder-evidence
python3 -m scripts.localization.game_locales check-febuilder-evidence
```

An intentional upstream refresh must use the explicit importer, which rejects
any source whose SHA-256 or profile differs from the pin:

```bash
python3 -m scripts.localization.game_locales import-febuilder-evidence \
  --source /path/to/FEBuilderGBA/config/data/translate_textid_FE8.txt
```

These commands never edit `fe8u_target_map.json`, coverage, raw closure, the
game catalog, or runtime sources.

## Structural mapping methodology

Mappings are promoted only by an independent semantic key shared by the FE8U
and FE8J references. Current evidence families are:

- character, class, and item row keys plus the corresponding name,
  description, and use-text fields;
- chapter `internalName` plus title/objective/goal fields;
- support `(character A, character B, rank)` slots;
- matching named event/world-map scripts plus text ordinal, including reviewed
  raw event opcodes;
- menu table symbol plus override ID/row, with direct regional strings keyed by
  stable `fe8cn.raw.import-NNNN` IDs;
- terrain enum index;
- battle/defeat table keys decoded from the named ROM structures.

The candidate seed is consulted only to record whether an independently proven
decision agrees with it. `interp`, `extrap`, shifted, or identity candidates
cannot create a release mapping. Split/merge cases remain explicit evidence
gaps; for example the two Chapter 14B scenes are not ordinal-mapped. The shared
Duessel/Knoll support key proves FE8U `0x0D49`-`0x0D4B` maps to FE8J
`0x0D08`-`0x0D0A`.

Maintainers may refresh the evidence from the authorized reference trees:

```bash
python3 -m scripts.localization.game_locales harvest-crosswalk \
  --fe8u-root /path/to/fireemblem8u \
  --fe8j-root /path/to/fireemblem8j
```

Normal validation does not require those trees. It rebuilds only from committed
evidence and compares the release artifacts byte-for-byte:

```bash
python3 -m scripts.localization.game_locales build-crosswalk
python3 -m scripts.localization.game_locales check-crosswalk
```

## Structural completion evidence

The completion harvester is deliberately a separate evidence domain. It reads
the current authoritative map only to select `not-yet-verified` targets and
hash-pins both that map and `fe8u_structural_evidence.json`; it never writes
either file. Proposed pairs require bounded FE8U/FE8J IDs, a non-empty indexed
Japanese payload, an authorized reference-map row or stronger keyed evidence,
and a stable semantic slot. Numeric interpolation, shifted ranges, and
proximity are not accepted evidence.

Source-site discovery is typed. It accepts `MSG_*` symbols, designated
message-ID fields, arguments at modeled message-consuming APIs, parsed event
message operands, and explicitly modeled message tables. Bare hexadecimal
literals are never sites, so palette, OAM, graphics, animation, and unrelated
numeric data cannot acquire message semantics from value equality.

High confidence requires parsed source and target structures with named keys,
actual slot context hashes, and matching message ordinals. Chapter 14B is
matched only inside the shared `Ch14B` model: the named FE8U event symbols are
paired with parsed scripts inside the named FE8J event table using their full
normalized message control-flow paths. There is no repository-global opcode
window matching. Trainee evidence parses each `PromoTrainee_TalkN.msgs` table
and its `StartCgText` consumer. Six slots in the currently pinned FE8J source
tree prove the mapped JP IDs; the other trainee reference-map pairs remain
reference confidence because their cited FE8J C tables do not contain those
IDs. Target `0x0C52` remains an explicit context collision between the
reference-map provider and the live preparation call-site provider rather than
choosing one arbitrarily.

The committed artifact preserves the original 1,794-target completion research
corpus even after final promotion: 1,381 unambiguous proposals and 413
structural residuals, including 20 originally context-required collisions. Of
the proposals, 27 have parsed high-confidence structure proof and 1,354 retain
reference confidence. Final-map rows record their recoverable original
fallback, so rebuilding this ledger remains deterministic after promotion.

Harvest from the authorized read-only trees and FEBuilder reference maps:

```bash
python3 -m scripts.localization.game_locales harvest-structural-completion \
  --fe8u-root /path/to/fireemblem8u \
  --fe8j-root /path/to/fireemblem8j \
  --reference-map /path/to/FEBuilderGBA/config/data/translate_textid_FE8.txt \
  --region-map /path/to/FEBuilderGBA/config/data/textid_FE8.txt
```

Normal checks need no external trees:

```bash
python3 -m scripts.localization.game_locales check-structural-completion
```

For a byte-for-byte rebuild check, add `--rebuild` and the four harvest input
paths to that command.

The committed FE8U target report contains 3,414 decisions, zero fallback, and
zero unresolved:

- 2,958 verified indexed mappings;
- 130 verified raw target mappings;
- 326 authored mappings: 259 fulfilled historical queue rows plus 67
  existing expansion-backed or target-specific semantic corrections.

Translation coverage is 3,414/3,414 (100%). Explicit English fallback and
unresolved coverage are both zero for Japanese and Simplified Chinese.

## Final mapping promotion and authored queue

`build-final-mapping` is the authoritative promotion pipeline. It is
idempotent: promoted rows retain their original fallback source and
verification, allowing the structural base to be reconstructed before every
run. Precedence is fixed:

1. preserve existing verified structural/raw/authored providers;
2. promote parsed structural high-confidence proof;
3. promote payload-valid, collision-free FEBuilder indexed/raw proof;
4. promote structural reference proof only with an independent table/call key,
   and apply the reviewed context decisions for every former collision;
5. reuse a mapped provider only when the normalized English bytes, including
   control tokens, are exact and all candidate JA/ZH payloads agree.
6. promote exactly the 259 historical queue rows from the canonical authored
   catalogs after queue hash, shard hash, target/key union, locale parity,
   control/newline/placeholder, and English-prose gates pass.

The pipeline additionally reuses the tracked legacy
`PROMO_OPTION_{1,2,3}_NAME` literals for the three FEBuilder pointer rows. It
does not infer by target number, offset, or proximity. The 12 FEBuilder
structural conflicts retain their pre-existing authoritative structural
providers; the 17 FEBuilder collision targets and four additional structural
collision targets have explicit context decisions rather than arbitrary
candidate selection.

```bash
python3 -m scripts.localization.game_locales build-final-mapping
python3 -m scripts.localization.game_locales check-final-mapping
```

`mapping/authored_translation_queue.json` retains exactly the original 259
targets byte-for-byte so every shard continues to pin the same source queue.
It is historical fulfilled input, not a residual runtime queue. Canonical
catalogs are generated and checked with:

```bash
python3 -m scripts.localization.game_locales refresh-authored-provenance
python3 -m scripts.localization.game_locales build-authored-catalogs
python3 -m scripts.localization.game_locales check-authored-catalogs
```

Final delivery proves that every historical target is mapped to its stable
authored key and that no fallback remains:

```bash
python3 -m scripts.localization.game_locales check-final-mapping \
  --require-no-fallback
```

## Combined fallback coverage handoff

`mapping/combined_fallback_coverage.json` is a generated, evidence-only
handoff for the final mapping owner. It hash-pins the authoritative map,
coverage report, structural crosswalk evidence, FEBuilder ledger, and
structural completion ledger. It cannot update the map and keeps every
conflict and context collision explicit.

After authored promotion the combined report has zero actionable fallback,
zero blocked target, and zero residual. It still retains the global exception
lists (12 FEBuilder conflicts, 17 FEBuilder collisions, and 20 structural
collision targets) so resolved history cannot silently disappear.

Build or verify the committed report:

```bash
python3 -m scripts.localization.game_locales build-combined-coverage
python3 -m scripts.localization.game_locales check-combined-coverage
```

## Raw-surface closure

The closure ledger accounts for all 143 unique raw imports:

- 137 records resolve through stable FE8U game message IDs;
- 6 records use semantic expansion keys: 2 distinct commands that share FE8U
  ID `0x0693`, 3 promotion-selector initializer providers, and 1 diagnostic
  build timestamp;
- the command keys are
  (`raw_surface.unit_action.summon` and
  `raw_surface.unit_action.call_monster`);
- the goal-window records use FE8J inline raw symbols `GoalString_UnitsLeft`,
  `GoalString_Turn`, and `GoalString_LastTurn`, bound to FE8U `0x01C1`-
  `0x01C3`; they never use unrelated same-number FE8J indexed messages;
- every record resolves to nonempty Japanese and Simplified Chinese payloads;
- 0 records use fallback or exclusion;
- 0 records remain unresolved.

Japanese raw providers comprise 20 literals verified directly against tracked
FE8J-derived C table entries and 119 materialized target+symbol records in
`ja/raw.json`, all checked against the committed raw-symbol source snapshot.
Simplified Chinese comes from the exact imported raw payload.
Modern promotion-selector initializers are empty because their draw callback
normally supplies a localized class name. If a bounded option target or its
name message is absent/invalid, the callback consumes the option's localized
semantic key as a fallback; legacy keeps the original Japanese initializer
bytes. The modern timestamp diagnostic resolves through the expansion locale
accessor, but every runtime locale duplicates this executable's current
`gBuildDateTime`. The imported FE8CN 2004 timestamp remains raw-source
provenance only; legacy keeps printing `gBuildDateTime`.

Build or check the machine report:

```bash
python3 -m scripts.localization.game_locales build-raw-closure
python3 -m scripts.localization.game_locales check-raw-closure
```

The check also verifies every recorded FE8U source path and every declared
anchor still exists in the declared order, every literal provider matches its
committed symbol/key/value/context,
every raw symbol has a matching target+symbol materialization, every semantic
expansion key is active and translated in `en`/`ja`/`zh-Hans`, and every
expansion provider names a surviving runtime consumer function whose body
contains the key-specific resolver anchors. Chinese expansion values equal
their imported raw payload except executable-identity records, whose
`en`/`ja`/`zh-Hans` values must all equal the referenced C string symbol. Its
strict gate is exactly 143 total, 143 verified game/expansion providers, 143
materialized JA payloads, 143 materialized ZH payloads, and zero fallback,
exclusions, or unresolved records.

Together, the compressed 3,414-row catalog and the 143-record raw-surface
closure have zero English fallback, zero exclusion, and zero unresolved
user-facing strings in both Japanese and Simplified Chinese.

## Final runtime Latin-span leakage gate

The leakage gate audits the materialized runtime payloads after mapping,
authored resolution, raw-provider resolution, and importer overrides. It
replaces bracketed controls with token boundaries, normalizes visible text
with NFKC, and tokenizes every contiguous ASCII Latin span even when it is
embedded in Japanese or Chinese. Exact/near-copy and Latin-only
classifications remain diagnostic signals, but every runtime span is gated.
An approval is accepted only for an exact scope + locale + target + span with
matching payload hash, occurrence count, category, factual reason, and source.
Localized decisions pin the corrected payload hash and require the reviewed
baseline span to be absent.

The committed report currently proves:

- 6,828/6,828 game-catalog payloads audited (3,414 per locale);
- 286/286 raw-surface payloads audited (143 per locale);
- all 125 Japanese and 139 Chinese mixed-script bypasses reviewed;
- 25 English spans localized through hash-pinned, control-preserving
  overrides, including the seven Japanese `MSG` placeholders;
- 162 current Japanese and 164 current Chinese Latin-bearing game payloads;
- 341 remaining exact approved spans across game and raw surfaces;
- 0 near-copy candidates;
- 0 unapproved span occurrences, 0 payload mismatches, and 0 stale decisions.

Generate or verify it with:

```bash
python3 -m scripts.localization.game_catalog audit-leakage
python3 -m scripts.localization.game_catalog check-leakage
```

## Mapping validation and coverage

```bash
python3 -m scripts.localization.game_locales validate-mapping \
  --mapping texts/locales/mapping/fe8u_target_map.json

python3 -m scripts.localization.game_locales coverage \
  --locale ja \
  --mapping texts/locales/mapping/fe8u_target_map.json
```

Coverage classifications are `indexed_source`, `raw_source`,
`authored_translation`, `explicit_english_fallback`, and `unresolved`.
The release report further groups them by structural subsystem. Candidate rows
remain unresolved when validating the candidate file itself; only a
schema-valid verified mapping backed by committed evidence contributes release
coverage.
