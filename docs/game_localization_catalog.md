# Full-game localization catalog

The full-game catalog is an opt-in modern-build input for FE8U message IDs.
It is separate from the expansion-framework catalog documented in
`localization.md`.

## Generate and validate

```bash
make game-localization-validate
make game-localization-generate
make game-localization-check
make game-localization-test
make game-localization-budget
make game-localization-leakage-check
FE8J_BASEROM=/path/to/fe8j/baserom.gba make game-localization-final-check
```

These targets generate both CJK bundles by default. Every generated CJK
profile also emits exactly one shared modern English bundle covering all 3,414
FE8U message IDs. To inspect one build profile in isolation, set
`GAME_LOCALIZATION_ENABLED_LOCALES=ja` or
`GAME_LOCALIZATION_ENABLED_LOCALES=zh-Hans`. A single-locale profile emits no
nodes, compressed blob, entries, or catalog descriptor for the disabled CJK
locale; its fixed `gGameLocalizationCatalogs[]` slot is null, while the shared
English bundle remains present once.

European profiles are opt-in with:

```bash
make game-localization-eu-check
make expansion-modern-localization-profile-en-fr-de-es-it
```

The European generator reads `texts/locales/eu/indexed.{fr,de,es,it}.txt`
and the authoritative `texts/locales/mapping/fe8eu_to_fe8u.json` ledger.
Every European locale emits all 3,414 targets with zero English fallback.

The generator reads committed `texts/texts.txt` plus `texts/textdefs.txt` for
English, and the canonical `texts/locales/` sources plus verified
`texts/locales/mapping/fe8u_target_map.json` decisions for CJK. Authored rows
resolve through `texts/locales/authored/catalog.{ja,zh-Hans}.json` by default;
those catalogs are byte-identical deterministic merges of queue/hash-pinned
reviewed shards. The English
parser handles explicit `#` IDs, `##` macro IDs, relative includes, named
controls/FIDs, and source comments deterministically. It encodes literal text
as UTF-8 while preserving engine control payload bytes. Legacy printable
tokens are normalized during generation: `DashedLine` to `-`, `TAB` to UTF-8
U+3000, `LQuote`/`RQuote` to `"`, and `AccentedE` to `e`. An unknown high-byte
printable token is rejected rather than emitted as invalid UTF-8.

The CJK mapping never infers a positional match. The production map contains
no fallback or unresolved decision.
Evidence-backed regional raw mappings
may commit an authorized FE8J literal as the Japanese provider while retaining
the FE8CN import as the Chinese provider, but only when a tracked C source
table's symbol, message-ID key, exact literal, and bounded context hash all
verify. The remaining reviewed targets resolve through stable authored keys.

Provider metadata also defines the control-operand domain. Indexed and raw
regional providers are FE8J-domain streams; authored target translations are
already FE8U-domain streams. During final composition only FE8J-domain
`LoadFace` operands are remapped through the hash-pinned named portrait-table
crosswalk in
`texts/locales/mapping/fe8j_to_fe8u_portrait_operands.json`. This prevents
double-remapping authored controls and covers named characters, eye-closed
variants, shop/debug portraits, and reviewed target-specific portrait context.

Outputs are generated under `build/game-localization/generated/`:

- `localized_game_text_data.h`: target count and maximum decoded bytes;
- `game_localization_catalog.h`: catalog and entry descriptors;
- `game_localization_catalog.c`: Huffman nodes, blobs, metadata, and entries
  in `.locale_data`;
- `game_localization_report.json`: entry-level provenance and hashes;
- `game_localization_budget.json`: coverage, storage, shared-English, and
  profile-specific ROM estimates;
- `game_localization_latin_span_audit.json`: final materialized Latin/script/
  confusable/artifact audit with exact scope/locale/target approvals.

Every present message is strict UTF-8 plus canonical engine control bytes and
one trailing NUL. Each descriptor records both compressed byte length and
exact meaningful bit length; the standalone NUL is the final Huffman symbol at
that bit boundary. Generation rejects unknown controls, embedded NUL bytes,
unresolved mapping decisions, and codec round-trip mismatches. A bounded final
stream tokenizer also rejects truncated extended controls and FIDs, FIDs that
do not match the FE8U English target context, unmatched consecutive newlines,
and localized `BreakTalk` counts incompatible with the statically modeled
event `TEXTCONT` sequence.

## Runtime and build gating

English-only modern and archival builds do not generate or link this catalog.
They retain the historical 4 KiB `MsgBuffer`, English `gMsgTable`, and ARM
decoder path with zero modern English/CJK payload.
European and CJK profiles share the bounded UTF-8 decoder/cache path.

Production CJK assets are selected directly from the validated locale profile:

```bash
make expansion-modern-localization-profile-en-ja
make expansion-modern-localization-profile-en-zh-hans
make expansion-modern-localization-profile-en-ja-zh-hans
make expansion-modern-localization-profile-en-fr-de-es-it
make expansion-modern-localization-profile-all
```

Equivalent direct builds use `EXPANSION_ENABLED_LOCALES=en,ja`,
`en,zh-Hans`, or `en,ja,zh-Hans` with `MODERN_ROM_SIZE=32M`. The same
effective profile drives identity metadata/fingerprint/mask, the selected CJK
catalog bundle(s), and localized font compilation. There is no synthetic
identity or separate CJK mask. Every CJK profile emits one shared English
bundle and only its selected CJK bundle(s); adding `qps-ploc` changes the
framework profile but does not duplicate full-game English data.

CJK profiles use one explicit message-storage overlay. The historical helper
scratch fields keep their offsets inside the overlay; total capacity is at
least `0x1600` bytes and grows if the generated maximum (including NUL)
requires more. Decode overflow or corrupt input returns a visible marker and
an explicit `LocalizedGameTextStatus`. Message indexes are checked before
localized or English lookup. In every CJK-enabled build, English and qps-ploc
decode the modern English descriptor directly; absent/unpopulated Japanese or
Simplified Chinese entries select that same descriptor. No active CJK path
reads `gMsgTable`, guesses a 4 KiB compressed-input bound, or depends on
adjacent compressed arrays. Bounded InBuffer lookup remains
cache-independent, so it cannot invalidate or overwrite a pointer returned by
an earlier `GetStringFromIndex` call.

The exhaustive audit independently decodes all 3,414 English entries, checks
source equality, renderer-valid UTF-8/control structure, and exact NUL bit
boundaries. It separately guards `0xD4D`, `0xD4E`, `0xD4F`, `0xD50`, and
`0xD54`, and validates all 6,828 final Japanese/Chinese streams through the
same control, UTF-8, and codec contract. Production reports require
`ja.present=3414`,
`zh-Hans.present=3414`, `english_fallback=0`, and `unresolved=0`; the shared
3,414-entry English bundle remains linked for the actual English locale.
Generation also runs the leakage gate. The committed
`texts/locales/mapping/runtime_english_leakage.json` separately proves all
6,828 JA/ZH catalog payloads and all 286 JA/ZH raw-surface payloads were
audited with zero unapproved Latin spans or disallowed Unicode scripts.
Controls are replaced by boundaries
before NFKC tokenization, so Latin inside mixed Japanese/Chinese text cannot
bypass the gate. Only canonical `[CTRL:HHHH]` controls or exact named engine
tokens defined by `texts/textdefs.txt` are stripped. Literal, unknown, or
malformed bracket text such as `[MISSING TEXT]`, `[TODO]`, or `[CTRL:000G]`
remains visible and must pass the same Latin/script audit. Exact reviewed
decisions are committed in
`texts/locales/runtime_latin_span_review.json`; every remaining acronym/code
is approved only for its exact target, locale, span, payload hash, occurrence
count, reason, and source. Regex or category-wide whitelisting is not
accepted.
The companion exact-target
`texts/locales/runtime_unicode_script_review.json` enforces locale-specific
scripts: Japanese permits kana and Han, Simplified Chinese permits Han, and
both consume only exact-reviewed Latin/fullwidth spans. Bopomofo in either
locale and kana in `zh-Hans` require exact target approval. NFKC plus explicit
enclosed-alphanumeric and Greek/Cyrillic lookalike skeletons catch forms such
as `🅾🅺`, fullwidth English, and cross-script `OK`; there is no broad
whitelist. Format characters, replacement characters, C1 controls, box
drawing, private/surrogate/unassigned scalars, and detected mojibake require
correction and cannot be approved. The audit covers final game, raw-surface,
and fixed-width display-alias payloads.
Its raw-surface section names all six semantic expansion keys explicitly, in
addition to auditing all 143 providers for both locales, so a non-Latin
semantic payload cannot disappear from the evidence merely because it has no
review row.

The 143-record raw closure is a separate call-site audit:

```bash
python3 -m scripts.localization.game_locales check-raw-closure
```

See `docs/game_locale_sources.md` for its 137 game-ID and 6 semantic expansion
providers. The normal command also performs the offline commit/tree/path/blob,
exact-symbol, and exact-slot checks. All 143 raw records have Japanese and
Chinese payloads with zero fallback, exclusion, or unresolved record.

The advertised final-delivery target is a serialized Make dependency chain,
safe under `make -j`: authored-catalog freshness, deterministic final mapping
with both `--require-no-fallback` and `--require-live-origin`, offline raw
closure, the runtime leakage audit, and the committed CJK font check. It
requires a legally obtained FE8J ROM through `FE8J_BASEROM`; omitting it is a
hard failure rather than a fallback to committed provenance.

Modern CJK consumers use `TextUtf8_Next` for low controls,
`[LoadFace]` plus FID, extended controls and arguments, U+3000, strict UTF-8,
and malformed/truncated input. `StringInsertSpecialPrefixByCtrl` and
`StrInsertTact` preserve control tokens while performing bounded UTF-8
substitutions; production callers provide explicit capacities. English-only
and archival preprocessing retains the legacy walkers and layouts.
