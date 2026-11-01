# Full-game localization catalog

The full-game catalog is an opt-in modern-build input for FE8U message IDs.
It is separate from the expansion-framework catalog documented in
`localization.md`.

The tester-facing production-catalog procedure is
[`TC-LOCALIZATION-003`](test-cases/localization.md#tc-localization-003-production-expansion-and-full-game-locale-catalogs).
The host-only
[`TC-LOCALIZATION-AUTHORED-SCOPE-001`](test-cases/localization.md#tc-localization-authored-scope-001-scope-deterministic-authored-validation)
procedure covers explicit JA/ZH scopes on the existing authored-catalog
builder/checker. Omitted scope still validates both locales; it does not
change the default writer, CLI or runtime profiles.

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

Modern ROM catalogs instead live under the selected
`MODERN_OUTPUT_DIR/game-localization/generated/`, including default-project
debug/release and ABI dimensions. Their producer depends on the existing
`selected-text-inputs.json` Make context and both selected font objects.
Changes to that context also refresh the font objects, so target-private
compiler flags cannot leave an older object paired with new planned inputs.
The source-known declaration header and real debug-font resource permit font
compilation before any measured catalog header is generated.

The CLI's optional `--compile-context PATH` selects that same native font
input for width, Talk and fixed-name checks. It uses actual compiler-emitted
layout and locale facts and the selected linker; missing objects, fonts,
styles or locales fail explicitly, without stock fallback. Private measurement
outputs are temporary, not declared inputs or serialized report identities.
Unreadable, non-UTF-8 or malformed JSON contexts report a path-qualified CLI
error without a traceback or replacement of previously generated output.
Omitting the option retains the standalone stock-font authoring path; that
path is not proof of a project's selected compiler/font context.

Every present message is strict UTF-8 plus canonical engine control bytes and
one trailing NUL. Each descriptor records both compressed byte length and
exact meaningful bit length; the standalone NUL is the final Huffman symbol at
that bit boundary. Generation rejects unknown controls, embedded NUL bytes,
unresolved mapping decisions, and codec round-trip mismatches. A bounded final
stream tokenizer also rejects truncated extended controls and FIDs, FIDs that
do not match the FE8U English target context, unmatched consecutive newlines,
and localized `BreakTalk` counts incompatible with the statically modeled
event `TEXTCONT` sequence. Continuation validation requires exactly one locale
entry for every active English target, even when an event does not reference
that target. An explicit absent entry still selects English fallback; a missing
or extra entry is not an implicit fallback.
When a selected locale entry is absent, the same final control and portrait
validation checks its effective English bytes without materializing a localized
descriptor. Reports include those effective payloads in `validated_payload_count`
and expose a nonzero `validated_fallback_payload_count` separately. Catalogs
without fallbacks retain their existing report shape and counts.

`build_game_catalog(event_root_path=...)`, `generate(event_root_path=...)`
and CLI `--event-root` select the same existing event source directory for
width classification and continuation validation; the default is `src/events`.
Numeric and active source/header message symbols are resolved before checking
the actual English allocation. Unknown symbols, missing roots and ordinary
retired IDs reject rather than disappear from coverage. C comments are not
event commands, and the world-map command's remaining operand is not a message ID.
Allocation headers contain stable object-like single-integer bindings.
Parentheses, integer suffixes, octal/hex spelling, comments and definition
ordering do not change ownership. Unsupported expressions, function-like
message macros, `#undef` and redefinition reject explicitly instead of
leaving an obsolete value active in the validator. `MSG_COUNT` retains its
hexadecimal metadata format, must be an actual active definition and must
match the active source. The generated-header dialect permits unconditional
literal bindings and an optional conventional `#ifndef` / empty `#define`
include guard. Other conditionals are explicitly unsupported, not evaluated
with guessed profile flags. Native `%:` directive spelling has the same
meaning as `#`; unmodeled directives such as external-header inclusion are
rejected rather than silently skipped. Native line splices are joined before comments
are masked, so a continued comment or split directive cannot expose a ghost
allocation/count; a split spelling of a valid definition remains valid.
Shared C-array/delimiter parsing preserves multiline and inline formatting.
Public engine macro aliases are read from the existing `EAstdlib.h`; implicit
`MSG_NNN` symbols from numeric source entries retain their validated bindings.
Both text subcommands preserve the signed dynamic-operand range outside
direct-ID coverage.
Non-stock fixture catalogs must supply their owned event directory instead
of borrowing unrelated stock events. This is not a competing event language
or a qualification of arbitrary runtime-indirect message IDs.

Source composition is shared with font inventory through
`compose_game_catalog`, which returns typed source bundles, not a render-ready
build/report. Font inventory can derive required glyphs before the atlas exists;
the strict builder still requires valid actual fonts, widths, controls and
continuations. There is no skip-validation mode.
Narrow character/class/item names use the existing 40/64/56px contract and
display-alias registry regardless of the total message count. The builder's
optional `schema_inputs` reuses invocation-local `SchemaInputs` for selected
typed data sources; CLI `--source-override TABLE=PATH` and selected Make
providers pass that same context. Retained default name IDs are intersected
with the actual active set; explicitly selected names must belong to it.
Absent localized names are measured from their effective English bytes, not
replaced with unrelated translated aliases. Only validated active aliases
are emitted. No new name resolver, ID registry or text language is introduced.
Removal flushes the current message and advances to subsequent commands.
The source model distinguishes the explicit dialogue-skip guard from ordinary
branches without skipping later ID validation. Correct traversal now covers
567 direct stock targets in 581 models; the prior 275/286 coverage stopped
prematurely at removal. All 6,828 final payloads retain the existing FID,
mouth-topology and line-width contracts.

Width checking and CJK inventory share the schema-2 renderer requirements,
not a single winning font classification. A message used by several native
consumers retains every renderer, font, source and measurement-only use.
Event modes 0/1 draw with Talk; mode 2 uses the 160px CG viewport; modes
3/4/5 draw with Talk in the 192px box limit. Box premeasurement can inherit
System glyphs before drawing selects Talk, so both fonts may be required.
Static Text using Talk glyphs still ignores NL2; Talk/Box/CG page controls
do not acquire their behavior from the selected font. Wait controls do not
reset a drawing cursor, unlike the native box-dimension helpers.
Re-selecting the same Talk speaker, or switching away and back before a
visible glyph, does not clear an open bubble. The Talk validator and width
wrapper share that cursor model; redundant selection cannot hide overflow.
Width-report records expose `contexts` and `requirements` instead of one
`context`/`usage` winner. Context counts can overlap across messages.
Custom schema-1 width registries must be updated to include the explicit
box and CG classification contexts; unsupported schemas reject explicitly.

`check-width --compile-context` also preprocesses the supplied selected C
inputs with their actual compiler arguments before accepting direct
message calls and designated message fields. It collects complete balanced
operands from the existing consumer/field registry, including the third
argument of `StartTalkMsg` and `StartTalkMsgExt`. Numeric constant operands
with parentheses, suffixes, arithmetic, character literals, `sizeof` and
casts through selected typedefs, plus named enum constants, are evaluated by
that ARM compiler in their original C scope, not interpreted with Python
arithmetic or filtered by a C-type name whitelist. Call IDs come from that
compiler's semantic `tree-original` output after actual argument conversion;
signed-int, typedef and comma-list declarations are not message calls.
Scalar/array initializer fields use their actual destination member type
and compile-time assertions after their declaration in the original C
scope, without rewriting the initializer. Integer `.msg` members are
checked; unrelated pointer, pointer-array and aggregate `.msg` members are
left intact. Nested scalar braces and trailing commas preserve their native
conversion. Complete direct designator lists such as `[0].nameTextId` do
not evade checks, and equality/member expressions are not initializers.
Evaluation
compiles the frozen preprocessed token stream, without replaying operands
under later macros or another include depth. The checked copy and compiler
dump are temporary; they add no final ROM data or runtime code. Dynamic
operands remain dynamic. Private conditional branches therefore select the
checked references; raw disabled branches are not live references.
The context loader is shared with font measurement. This is a first
source-reference slice, not complete dataflow, nested-member initializer
binding, event-helper, generated-owner or fresh full-project integration.

## Runtime and build gating

The focused selected-text native check is:

```bash
python3 -m unittest scripts.localization.game_catalog.tests.runtime.selected_text_native -v
```

It uses the supported ARM GCC/AAPCS toolchain and existing headless libmGBA
backend. The debug linker-check gate runs it explicitly; the host-only
localization discovery suite does not require an ARM toolchain. It freshly
validates a three-message English/JA fixture through the real width-check CLI,
generates it with the existing library writer, and compiles the unchanged whole
`asm/arm.s` and real message, item-name, locale and tokenizer consumers, and
checks actual target RAM records. Controls, English fallback, locale switching,
the retained `ItemData.nameTextId`, localized invalid IDs and a guarded
four-byte decode destination are asserted. English-only output uses the real
historical ARM decoder, not a stub. Temporary ROMs and objects are removed.
This module-native check is not a supported full-game ROM profile, rendered
page/glyph/portrait evidence, or production-content qualification; it does not
call the historical English-only decoder with invalid IDs.
Its deliberately absent JA entry is not a production catalog: the production
generation/leakage gate still rejects unmaterialized entries.
The same native suite also links the real font, Box and CG measurement
functions with deliberately distinct 3px System/7px Talk glyph advances.
For `AA[NL2]AA`, inherited System box measurement is 6px, Talk box
measurement is 14px, static Talk-font length is 28px and the dialogue
glyph-length helper returns 30px including its padding. Native CG content
length is 28px but its box-dimension helper returns 14px. A wait caret
adds eight pixels to the measured 14px segment, and the box frame's
native maximum is 192px. These are actual ARM RAM observations, not
rendered page, layout, portrait or full-game qualification.

### TC-PROJECT-TEXT-001: Validate the selected gameplay message set

- **Feature / issue:** selected gameplay text /
  [#314](https://github.com/laqieer/fireemblem8-expansion/issues/314).
  `TEST-TEXT-I18N-COVERAGE` maps to this case's host and native observations,
  not to a second catalog or resolver.
- **Profile / artifact:** Linux/CPython 3.12 source build. The native fixture
  uses modern ARM GCC/AAPCS and headless libmGBA, three active messages,
  English-only and English/JA masks. No original ROM is needed.
- **Prerequisites / state:** run from this source checkout with host `cc`,
  the supported ARM GCC/binutils and libmGBA available. Use the existing
  `MODERN_CC`, `MODERN_NM` and `MODERN_OBJCOPY` overrides when necessary.
  Fixtures and generated headers belong to temporary directories; do not
  edit a tracked header or a build-local generated output.

#### Actions

1. Run the actual source/header, event and publication checks:

   ```sh
   python3 -m unittest scripts.localization.game_catalog.tests.test_build_fixture.FixtureBuildTests scripts.localization.game_catalog.tests.test_control_streams.SelectedEventContinuationTests scripts.texttools.tests.test_textprocess_publication -v
   python3 -m unittest scripts.localization.game_catalog.tests.test_width_contract.WidthContractTests scripts.fonttools.cjk.tests.test_cjk_fonts.CjkFontTests.test_direct_talk_inventory_is_independent_of_directory_order scripts.fonttools.cjk.tests.test_cjk_fonts.CjkFontTests.test_inventory_uses_all_native_requirements_and_rejects_missing_payloads -v
   python3 -m unittest scripts.modernize.tests.test_expansion_config.ProjectSelectionIdentityTests.test_selected_compile_context_plans_all_c_slots_once_without_catalog_build scripts.modernize.tests.test_expansion_config.ProjectSelectionIdentityTests.test_selected_compile_context_excludes_late_dependency_file_remakes -v
   ```

2. Observe the owned three-message fixtures. An allocation/header symbol must
   retain its actual table position; missing, colliding, retired, reordered
   or gapped allocations reject before either public text output changes.
   The generated header's `MSG_COUNT` and `MSG_H` names cannot be authored
   message symbols; unrelated names such as `MSG_COUNTED` remain valid.
   Valid declarations with `[]` or an explicit bound both expose their event
   operands. Literal and selected-header popup/notify IDs are range-checked
   without becoming Talk continuation models. Signed dynamic event operands
   retain their slot semantics; zero text-show leaves the active dialogue
   unchanged. Native renderer requirements are unioned, not overwritten.
   NL2 resets Talk/Box/CG pages but not static Text, even with Talk glyphs;
   wait controls retain the drawing cursor. Effective fallback validation
   neither reflows nor materializes an absent locale descriptor.
   Direct native `StartTalkMsg`/`StartTalkMsgExt` references use the third
   argument (the message), not either coordinate; nested coordinates and
   comments cannot invent glyph requirements. Missing production payloads
   and live out-of-range event/name references reject explicitly.
   The native constant-reference fixture separately admits `(1u)`,
   `3u - 2u`, native `u16` casts/wraps, `sizeof(u16)` and character value 1;
   it rejects `(1u + 2u)`, `-1`, `(u16)-1`, `sizeof(u32)` and character
   `'3'` against three active messages. The same inputs cover the getter,
   both direct Talk APIs and all three retained ItemData message fields.
   Actual destination conversions also admit 65537 as ItemData ID 1, but
   reject it as an `int` API ID; 4294967297 narrows to ID 1 in both.
   Qualified casts, named enum constants and include-depth-selected `u8`/
   `u16` inversion controls retain their original C semantics.
   Braced character literals retain their complete expression spans:
   `{'3'}` is native ID 51 and rejects against three messages, while
   `{1 + '\0'}` and nested braced character value 1 remain valid.
   Named enum casts, compiler builtins, local attributed typedefs/initializers
   and nested message calls are checked in their actual scope.
   The Make context fixture creates a dependency file after the parent has
   parsed its include list. Its native child plan must still contain
   exactly the selected C objects, not a builtin `.d.o` remake. This plans
   real recipes without executing either compiler or preprocessor; late
   private flags and recorded data-preprocessor arguments remain intact.
3. Execute the real target consumers, explicitly outside the host-only lane:

   ```sh
   env -u GBA_PLAYTEST_HOST_ONLY python3 -m unittest scripts.localization.game_catalog.tests.runtime.selected_text_native -v
   ```

#### Expected result

The host fixtures reject invalid active IDs, unresolved/colliding header
bindings, missing authored keys, malformed controls, incompatible continuation
counts, bad portrait operands and width/substitution overflow. Failures remain
explicit; existing publication files stay unchanged.

The native JSON records report three messages and masks 1 and 3. The real
English ARM decoder and localized consumers return the fixture's exact bytes.
The separate module-native geometry record matches the System/Talk, static
Text, Box and CG measurements described above. Host font inventory retains
both inherited measurement and drawing resources and all known overlapping
native consumer requirements.
Message 0 is the first lookup after a fresh boot and returns `Base zero`, not
an empty cache buffer. The modern English cache has an explicit zero-initialized
validity byte, so this also works with the native fixture's NOLOAD EWRAM.
The archival cache path is unchanged.
The multi-page message retains control sequence `[1, 3, 2, 31]`; the absent JA
entry falls back to English without becoming a translated entry. The retained
`ItemData.nameTextId = 2` returns the correct item name through the real
item-name consumer, with its ABI unchanged. Locale switching returns the
appropriate payload. Localized invalid IDs return the explicit invalid marker;
bounded decode reports overflow without changing either destination guard.
The selected typed item source also requires a validated compact name. The
real native display-alias lookup returns that generated alias for the 56px
surface and refuses the 55px request, while canonical item lookup keeps the
full localized name. This tests the alias lookup interface with explicit
width inputs, not an entire rendered forecast screen.
Fixed-name measurement keeps the legacy `81 40` space at six pixels,
distinct from the native 16px Unicode U+3000 space. Its JSON display text
uses a Unicode visual space, but widths are measured from the original
tokens and canonical payload bytes are unchanged. The source fixture's
six `A` glyphs plus legacy space occupy 36px; seven plus legacy space
occupy 41px and require an alias on the 40px surface. The same six glyphs
with genuine U+3000 occupy 46px and also reject without an alias.

#### Negative control

English-only output is the real legacy-consumer control, not a host stub.
The three-entry set cannot accept static ID 3; reordering or omitting table
positions cannot publish a success-shaped header/table pair. A second 248px
line still rejects even when separated by NL2. Missing locale entries may
exercise the documented fallback fixture but do not qualify production
generation or leakage gates.
Before the cache fix, the real English-only ROM returns zero bytes for its
first message-0 lookup instead of the expected nine bytes; the localized ROM
still returns the expected payload. A compile failure is not this negative
control.

The selected-font tracers can be run separately:

```sh
python3 -m unittest scripts.modernize.tests.test_expansion_config.ProjectSelectionIdentityTests.test_selected_compiled_font_metrics_use_real_private_include_and_macro_context scripts.modernize.tests.test_expansion_config.ProjectSelectionIdentityTests.test_selected_cjk_metrics_use_compiled_counts_widths_and_enabled_locale scripts.modernize.tests.test_expansion_config.ProjectSelectionIdentityTests.test_selected_font_metrics_resolve_glyphs_across_both_native_font_slots scripts.modernize.tests.test_expansion_config.ProjectSelectionIdentityTests.test_default_english_resolver_compiles_with_source_only_declarations scripts.modernize.tests.test_expansion_config.ProjectSelectionIdentityTests.test_pseudo_locale_metrics_keep_the_native_latin_path_without_an_atlas -v
```

These compile real selected ARM font data, use the selected native linker for
cross-object glyph references, and pass the measured widths to the width,
Talk and fixed-name interfaces. Private System/Talk widths 9/11 accept 234px
and 231px examples but reject the next character. A selected Japanese
System width of 13 accepts a 39px name and rejects 52px on the 40px surface.
The real three-message CLI fixture retains 18 such glyphs at 234px without
reflow; 19 glyphs require one inserted NL under the existing CJK wrapping
policy. Repeating measurement in another private directory preserves the
same metric/input facts. The final builder passes one supplied metric input
to all three consumer families, rather than reloading stock widths.
The CLI reference fixture supplies four genuine scoped compiler inputs
(layout declarations, native font data, localized font data and a retained
consumer), not a whole default engine paired with only three messages.
It preserves that 13px CLI measurement/reflow contract while private CPP
flags alternately expose valid ID 1 or invalid ID 3 in a real message call
and each `ItemData` name/description/use-description field. The latter must
fail explicitly; changing only a raw disabled branch must not fail.
The separate EN/FR publication fixture uses a real locale-mask-9 context
and explicit English-preserve mappings. `generate` succeeds with live ID 1,
including on a cold output directory whose catalog C does not yet exist.
All seven build/validation/audit commands reject live ID 3 before replacing
any output, even when the changed English input would produce different
bytes. These commands share the native check at their existing build
boundary; `check-width` is not a special qualification-only path.
The catalog producer stages its genuine six outputs in an owned temporary
directory, checks the prospective synthetic catalog C and its headers,
and only then returns to the ordinary publication/audit path. A stale
invalid final C does not replace this prospective source; an invalid
prospective C still fails the native checker rather than being exempt.
The Make producer prepares every actual compiler input from the same
selected-source registry, including data preprocessing, reinstated tables,
presentation manifests, selected English and UI catalogs. Its own staged
catalog is excluded from that prerequisite graph, not from native checking.
The Make regression parses the real compiler-context JSON and GNU Make
database: every non-self compiler input must be a prerequisite, while the
self source must not form a cycle. It does not assert Make source spelling
or execute a fake compiler. The fixed stock
143-surface CJK leakage audit is unchanged; the small EN/FR fixture does
not qualify that audit or an entire game profile.
English source-only declarations and the pseudo locale's Latin-font path
need no measured catalog or locale atlas. These are bounded compiler and
consumer observations, not complete selected CPP/reference/multi-renderer
integration, rendered pages, full-game profiles or release qualification.

#### Interactions and save compatibility

Depends on the existing project input seam, text tools, selected event source,
font metrics, portrait map and locale consumers. No new dialogue language,
resolver, ID registry, flag, locale or save/epoch change. Modern debug runs the
native fixture explicitly; release and archival behavior retain their existing
gates. Default catalogs and all complete candidate/master checks remain
required independently.

#### Automation

The commands above map the case and `TEST-TEXT-I18N-COVERAGE` directly to
generated outputs, real host refusals and target RAM/control/retained-field
observations. The debug linker-check gate runs the native command. No
subjective manual criterion is asserted by this module-level case.

#### Cleanup and limitations

The suites remove only their owned temporary source, object, ELF and ROM
fixtures. Preserve their emitted records as evidence when needed. This is
module-native coverage, not a complete supported game profile, rendered
glyph/portrait judgment, full-game playback or Grado production qualification.
Do not invoke the historical English-only decoder with invalid IDs.

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
