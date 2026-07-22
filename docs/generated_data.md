# Generated-data platform (Issue #5 Chapter 2 slice -- Batch A + B)

## Status

**Slice 0** proved out the deterministic schema/validation/generation/
build/drift plumbing on a single canary table (`SupportData`), end to
end, without replacing or linking any hand-written table.

**Batch A** generalized that framework from supports-only hardcoding to
per-table dispatch, and extended it with the Chapter 2 vertical slice's
pure-data tables:

* `units` -- `UnitDefinition` groups + their `REDA` position arrays for
  all 7 Chapter 2 unit groups.
* `shops` -- `ShopList_Event_Ch2Armory`.
* `traps` -- `TrapData_Event_Ch2`/`TrapData_Event_Ch2Hard`.
* `eventscripts` -- a **metadata-only** inventory of the 43
  `EventScr`/`EventListScr` symbols the Chapter 2 vertical slice depends
  on, and the first real production consumer of the `CSymbolRefField`
  escape hatch (see below).

**Batch B** (this update) adds the chapter *composition* layer on top of
Batch A's leaf tables:

* `eventlists` -- the 7 `EventListScr_Ch2_*` event-list arrays (turn/
  character/location/misc/select-unit/select-destination/unit-move),
  the 30-symbol `EventListScr_Ch2_Tutorial` pointer array, and the full
  `struct ChapterEventGroup Ch2Events` manifest (20 designated fields).
  Event-list entries are modeled **structurally** as macro calls (macro
  name + typed ordered arguments -- `TURN`, `CHAR`, `Village`, `Armory`,
  `DefeatAll`, `CauseGameOverIfLordDies`), never expanding/reimplementing
  the underlying event-script bytecode the macros generate; the
  generator emits the identical C macro-call text `ch2-eventinfo.h`
  hand-writes. `eventlists` cross-validates against Batch A's own
  `units`/`shops`/`traps`/`eventscripts` JSON records (not just "is this
  symbol declared somewhere in a header", which would trivially accept
  any chapter's symbol) via a new, generalized
  `TableSchema.dependency_tables()` mechanism (see below).

None of these tables replace or link any hand-written source yet.
`src/data_supports.c`, `src/events_udefs.c`, `src/events_shoplist.c`,
`src/events_trapdata.c`, `include/eventcall.h`, and
`src/events/ch2-eventinfo.h` all remain the canonical, linked/
authoritative sources. Issue #5 itself is **not closed** by Batch A/B.

Still open for **Batch C** (not part of this update): a whole-bundle
manifest tying every Chapter 2 table (`units`/`shops`/`traps`/
`eventscripts`/`eventlists`) together, and a chapter-settings
cross-check that the full vertical slice is internally consistent as a
whole. See "Remaining Issue #5 scope" at the end of this document.

## Source vs. generated vs. committed-public artifacts

| Artifact | Path | Committed? | Linked into the ROM? |
|---|---|---|---|
| Structured source (hand-edited) | `src/data/supports.json` | Yes | No (not compiled directly) |
| Hand-written canonical C (existing) | `src/data_supports.c` | Yes | **Yes** -- unchanged, still canonical |
| Generated C89 (bulky) | `build/generated/data/data_supports.c` | **No** (gitignored, `build/`) | No -- not referenced by `ldscript.txt`/Makefile |
| Committed inventory/summary | `reports/generated_data_supports_inventory.md` | Yes (small) | N/A |
| Structured source (units) | `src/data/ch2_units.json` | Yes | No |
| Hand-written canonical C (existing) | `src/events_udefs.c` | Yes | **Yes** -- unchanged, still canonical |
| Generated C89 (bulky) | `build/generated/data/data_ch2_units.c` | **No** (gitignored) | No |
| Committed inventory/summary | `reports/generated_data_units_inventory.md` | Yes (small) | N/A |
| Structured source (shops) | `src/data/ch2_shops.json` | Yes | No |
| Hand-written canonical C (existing) | `src/events_shoplist.c` | Yes | **Yes** -- unchanged, still canonical |
| Generated C89 (bulky) | `build/generated/data/data_ch2_shops.c` | **No** (gitignored) | No |
| Committed inventory/summary | `reports/generated_data_shops_inventory.md` | Yes (small) | N/A |
| Structured source (traps) | `src/data/ch2_traps.json` | Yes | No |
| Hand-written canonical C (existing) | `src/events_trapdata.c` | Yes | **Yes** -- unchanged, still canonical |
| Generated C89 (bulky) | `build/generated/data/data_ch2_traps.c` | **No** (gitignored) | No |
| Committed inventory/summary | `reports/generated_data_traps_inventory.md` | Yes (small) | N/A |
| Structured source (eventscripts, metadata-only) | `src/data/ch2_eventscripts.json` | Yes | No |
| Header declaring the referenced symbols (existing) | `include/eventcall.h` | Yes | **Yes** -- unchanged, still canonical |
| Generated C89 | *(none -- metadata-only table)* | N/A | N/A |
| Committed inventory/summary | `reports/generated_data_eventscripts_inventory.md` | Yes (small) | N/A |
| Structured source (eventlists) | `src/data/ch2_eventlists.json` | Yes | No |
| Hand-written canonical C (existing) | `src/events/ch2-eventinfo.h` | Yes | **Yes** -- unchanged, still canonical |
| Generated C89 (bulky) | `build/generated/data/data_ch2_eventlists.c` | **No** (gitignored) | No |
| Committed inventory/summary | `reports/generated_data_eventlists_inventory.md` | Yes (small) | N/A |

Because none of the generated C is linked, this work cannot introduce
`multiple definition` link errors and cannot silently change gameplay
behavior -- the only committed, gameplay-visible sources of these tables
are still the hand-written files above. Every JSON source and its
generated output is validated to be *semantically identical* to the
corresponding hand-written file (see "Round-trip checker" below), or (for
`eventscripts`) that every referenced symbol genuinely exists as declared,
so that a future slice can switch the link order with confidence.

## Package layout

```
scripts/generated_data/
  diagnostics.py     SourceLocation, GeneratedDataError, DiagnosticCollector
  json_loader.py      hand-rolled JSON parser preserving order + file:line:column
  schema.py           SchemaRegistry (name/version dispatch) + DependencyGraph
  validators.py       reusable checks: unique ids, references, ranges,
                       fixed capacities, enum-header symbol extraction
  escape_hatch.py     CSymbolRefField -- typed hand-written-C escape hatch
  cgen.py             C89 emission helpers + write_if_changed
  cparse.py           shared brace-depth-aware C block/initializer parsing
                       helpers reused by the units/traps round-trip parsers
  registry.py         registers every table schema (import this to dispatch)
  cli.py              validate / generate / check subcommands (fully
                       generic -- no per-table `if table == ...:` branches)
  supports/
    schema.py          SupportRecord, load_records(), validate()
    generate.py         C89 emission for SupportData
    parser.py           round-trip parser for src/data_supports.c + comparer
    inventory.py        builds the committed inventory/summary report
  units/
    schema.py           UnitDefinition/REDA records, load_records(), validate()
    generate.py         C89 emission (REDA arrays + UnitDefinition literals)
    parser.py           round-trip parser for src/events_udefs.c + comparer
                         (expands AI helper macros, resolves symbolic/raw
                         charIndex/classIndex)
    inventory.py        builds the committed inventory/summary report
  shops/
    schema.py           ShopRecord (ordered ITEM_* refs), load_records(), validate()
    generate.py         C89 emission (u16[] + auto-appended ITEM_NONE)
    parser.py           round-trip parser for src/events_shoplist.c + comparer
    inventory.py        builds the committed inventory/summary report
  traps/
    schema.py           TrapArray/TrapEntry (flat 6-field u8[] format),
                        load_records(), validate()
    generate.py         C89 emission (auto-appended bare TRAP_NONE terminator)
    parser.py           round-trip parser for src/events_trapdata.c + comparer
    inventory.py        builds the committed inventory/summary report
  eventscripts/
    schema.py           EventScriptRecord (metadata-only), load_records(),
                        validate() -- the first production CSymbolRefField
                        consumer
    inventory.py        builds the committed inventory/summary report
                        (no generate.py/parser.py -- nothing to generate or
                        round-trip for a metadata-only table)
  eventlists/
    schema.py           MacroArg/MacroCall/EventList/TutorialList/Manifest
                        records, load_records(), validate() -- the first
                        production dependency_tables() consumer
                        (units/shops/traps/eventscripts)
    generate.py         C89 emission of the same macro-call text
                        ch2-eventinfo.h hand-writes (no bytecode expansion)
    parser.py           round-trip parser for src/events/ch2-eventinfo.h +
                        comparer -- a paren-depth-aware macro-call
                        tokenizer (no top-level commas between entries,
                        unlike the other tables' array literals)
    inventory.py        builds the committed inventory/summary report
  tests/               stdlib unittest suite (see "Testing" below)
```

## Why a hand-rolled JSON parser?

Python's `json` module discards source positions once a value is
decoded. Diagnostics for duplicate/invalid records need to point at
`file:line:column`, so `json_loader.py` implements a small, dependency-free
recursive-descent parser that:

* preserves key/array insertion order (never re-sorted);
* records a `SourceLocation` for every object, array, key, and scalar;
* rejects duplicate object keys at parse time, citing both the first
  definition's location and the duplicate's.

It intentionally supports only the JSON subset this project's data files
use (objects, arrays, strings, numbers, `true`/`false`/`null`, and `//`
line comments) -- it is not a general-purpose JSON5 parser.

## Schema/version dispatch and dependency graph

`schema.SchemaRegistry` maps `(table_name, version)` to a `TableSchema`.
`registry.py` is the single place every table registers itself -- now
`supports`, `units`, `shops`, `traps`, `eventscripts`, and `eventlists`
(all v1); the CLI resolves `--table NAME` through this registry instead
of hardcoding imports, so `cli.py` has zero per-table branches -- adding
a table means adding a package + one line in `registry.py`, nothing else.

`schema.DependencyGraph` records which headers/tables a schema depends on
(`supports` depends on `constants/characters.h` and
`UNIT_SUPPORT_MAX_COUNT` from `types.h`). It provides a deterministic
topological order (Kahn's algorithm with alphabetical tie-breaking, for
future multi-table generation ordering) and a stable sha256 digest that
is embedded in the committed inventory report -- so a change in a table's
dependencies is visible as a one-line diff, not silent.

### `TableSchema.dependency_tables()` (Batch B: cross-table validation)

Batch A's tables validate purely against headers (enum constants, item
lists, `CSymbolRefField`). `eventlists` is the first table that needs to
validate against *other tables' own JSON records* -- e.g. "does this
`playerUnitsInNormal` symbol actually exist as a `units` group in this
chapter" is a stronger, more meaningful check than "is this symbol
declared anywhere in `include/eventcall.h`", which would trivially
accept any chapter's unit-group symbol.

A schema declares the *other registered table names* it needs loaded by
overriding `dependency_tables()` (default: `()`, i.e. no cross-table
dependency). `cli.py`'s `_load_dependency_records()` loads each declared
dependency deterministically, in declaration order, through the same
schema registry and each dependency's own `default_source` -- so
`eventlists` always cross-checks against the *current* `units`/`shops`/
`traps`/`eventscripts` JSON, not a stale snapshot. Each dependency's
source can be overridden independently with a repeatable
`--dep-source NAME=PATH` CLI flag, which is how this table's own test
fixtures validate in isolation against small fixture-only
`units`/`shops`/`traps`/`eventscripts` records instead of the real
Chapter 2 data (see `tests/fixtures/eventlists/deps_*.json`). When a
schema declares no dependencies, `validate(records, diagnostics)` is
still called with its original 2-argument signature -- Batch A's
5 tables are completely unaffected by this generalization.

## Validation helpers (`validators.py`)

Generic, reusable, and covered independently of any one table:

* `extract_enum_constants(header, name_prefix)` -- regex-scans a C header
  for `NAME = <value>,` enum entries (used against
  `include/constants/characters.h` for character references, and against
  `include/types.h` to read `UNIT_SUPPORT_MAX_COUNT` so the fixed-capacity
  check can't silently go stale if the struct's array size changes).
* `validate_unique` -- duplicate-key detection with the first
  definition's location in the message.
* `validate_reference` -- membership in an allowed symbol set.
* `validate_range` -- inclusive numeric range (used for `u8` fields: 0..255).
* `validate_fixed_capacity` -- a count against a fixed C array capacity.
* `validate_parallel_arrays` -- parallel-array length agreement (the
  `characters[]`/`supportExpBase[]`/`supportExpGrowth[]` triple).

## `SupportData` schema (the canary table)

Source: `src/data/supports.json`, one object per owner under `"records"`:

```json
{
  "owner": "CHARACTER_EIRIKA",
  "symbol": "SupportData_Eirika",
  "characters": ["CHARACTER_EPHRAIM", "CHARACTER_SETH", ...],
  "supportExpBase": [30, 25, ...],
  "supportExpGrowth": [4, 3, ...],
  "supportCount": 7
}
```

This mirrors `struct SupportData` in `include/bmreliance.h` field-for-field
(parallel arrays + count), so the round-trip checker needs no semantic
reinterpretation. All 33 vanilla records (every character with a
`SupportData_*` symbol in `src/data_supports.c`) are present, preserving
symbolic character IDs and support values exactly -- no invented content.

Validations enforced (`supports/schema.py: validate()`):

* unique owner IDs and unique generated symbol names across records;
* unique partners within a single owner's record;
* every owner/partner is a defined `CHARACTER_*` constant;
* `characters[]`/`supportExpBase[]`/`supportExpGrowth[]` agree in length,
  and `supportCount` matches that length;
* `supportCount` (and thus partner count) never exceeds
  `UNIT_SUPPORT_MAX_COUNT` (7), read live from `include/types.h`;
* `supportExpBase`/`supportExpGrowth` are in `u8` range (0..255);
* no record lists its own owner as a partner;
* **reciprocal support pairs**: if A lists B as a partner, B must have a
  record, must list A back, and both directions' `supportExpBase`/
  `supportExpGrowth` must match exactly. This is enforced unconditionally
  because every one of the 33 vanilla records satisfies it (verified by
  direct inspection of `src/data_supports.c` before writing this
  validator) -- vanilla Sacred Stones supports are always mutual.

Every diagnostic carries the JSON source's `file:line:column` and a
reference-path breadcrumb (e.g. `records[owner=CHARACTER_EIRIKA]
.characters[2]=CHARACTER_NOT_A_REAL_CHARACTER`).

## Round-trip checker (`supports/parser.py`)

`parse_hand_written(path)` is a small regex-based parser tailored
specifically to the designated-initializer shape used in
`src/data_supports.c` (not a general C parser). `compare_records()`
diffs the generated model (built from the JSON source) against every
parsed hand-written record -- **all 33**, not a sample -- field for field
(owner, characters, supportExpBase, supportExpGrowth, supportCount).
Formatting differences (whitespace, line breaks) are irrelevant since
comparison happens on parsed values, not text; any semantic difference
(missing record, extra record, or a differing value) is reported with the
hand file's line number.

## `units` schema (Chapter 2 `UnitDefinition`/`REDA`)

Source: `src/data/ch2_units.json`, one object per `UnitDefinition` group
under `"groups"`; each group has a `symbol` and an ordered `"units"` list.
Covers exactly the 7 vanilla Chapter 2 groups referenced from
`src/events_udefs.c`: `UnitDef_Event_Ch2Ally`, `UnitDef_Ch2Enemy_0`,
`UnitDef_LordSplitAlly`, `UnitDef_Ch2Ally`, `UnitDef_Ch2NPC`,
`UnitDef_Ch2Enemy_1`, `UnitDef_Ch2Enemy_2` (5/6/2/1/2/2/1 units
respectively).

Each unit record mirrors `struct UnitDefinition` (`include/bmunit.h`)
field-for-field: `charIndex`/`classIndex`/`leaderCharIndex` (accept either
a symbolic `CHARACTER_*`/`CLASS_*` constant or a raw integer -- generic
filler units use raw indices that don't have a named constant, exactly
as `src/events_udefs.c` does), `level`/`autolevel`, `allegiance`,
`xPos`/`yPos`, an ordered `items[]` (bounded by `UNIT_DEFINITION_ITEM_COUNT`,
read live from `include/bmunit.h`), `ai[]` (bounded by `UDEF_AIIDX_MAX`,
also read live), and an ordered `redas[]` list of `{x, y}` points
(each becomes a `REDA_<group>_<index>` sub-array; `redaCount` is never
stored in JSON -- it's always derived as `len(redas)`).

`ai[]` values are always the **resolved raw integers** in JSON -- the
round-trip parser (not the JSON source) expands the hand-written file's
AI helper macros (`DefaultAI`, `AttackInRangeAI`, etc., `#define`d in
`include/EA_Standard_Library/AI_Helpers.h`) into the same raw integers
for comparison, so the JSON source itself never needs macro-awareness.

Validations enforced (`units/schema.py: validate()`): unique group
symbols and unique per-unit generated identifiers; every symbolic
`charIndex`/`classIndex`/`leaderCharIndex`/item reference is a defined
constant; `level`/`xPos`/`yPos`/REDA point coordinates are in range;
`items[]`/`ai[]` never exceed their header-derived fixed capacities.

## `shops` schema (Chapter 2 armory)

Source: `src/data/ch2_shops.json`, one object per shop list under
`"shops"`: `symbol` and an ordered `items[]` of `ITEM_*` references.
Covers `ShopList_Event_Ch2Armory` -- a plain `u16[]` of item symbols (no
price pairing, unlike some other shop-list shapes in
`src/events_shoplist.c`), terminated by `ITEM_NONE`. The terminator is
never stored in JSON: it's auto-appended by the generator and stripped
before round-trip comparison; explicitly including `ITEM_NONE` in the
JSON `items[]` is a validation error (it would be ambiguous whether the
author meant an early terminator or a literal item reference).

Validations enforced (`shops/schema.py: validate()`): unique shop
symbols; every item is a defined `ITEM_*` constant; `items[]` is
non-empty; `ITEM_NONE` is rejected if present explicitly.

## `traps` schema (Chapter 2 trap arrays)

Source: `src/data/ch2_traps.json`, one object per trap array under
`"traps"`: `symbol` and an ordered `entries[]`. Covers
`TrapData_Event_Ch2`/`TrapData_Event_Ch2Hard` (both trivial -- empty
`entries[]`, matching the vanilla `{ TRAP_NONE }`-only data), but the
schema/generator/round-trip parser support the full flat 6-field-per-entry
`u8[]` format generally (`type, xPos, yPos, subtype, turnCounter, turn`,
per the inline comments in `src/events_trapdata.c`), proven against the
real, non-trivial `TrapData_Event_Ch7` (2 ballista entries) via this
table's test fixtures/round-trip tests.

The array is terminated by a **bare single `TRAP_NONE` token**, not a
zeroed 6-byte entry (confirmed via `TrapData_Event_Ch7`); like the
`items[]` terminator in `shops`, it's auto-appended/stripped, and
explicitly including `TRAP_NONE` as an entry's `type` is a validation
error. `subtype` accepts either a symbolic `ITEM_*` reference (for
`TRAP_BALLISTA`) or a raw `u8` integer (e.g. plain `0` for
`TRAP_GORGON_EGG`), exactly like `UnitDefinition.charIndex`.

Validations enforced (`traps/schema.py: validate()`): unique trap array
symbols; entries-per-array bounded by `TRAP_MAX_COUNT` (=64, read live
from `include/bmtrick.h`); `type` is a defined trap-type enum constant
(scoped precisely to the `TRAP_NONE = 0, ...` enum block, not the
unrelated `TRAP_MAX_COUNT`/`TRAP_EXTDATA_*` constants sharing the
`TRAP_` prefix); `xPosition`/`yPosition`/`subtype`(when raw)/
`turnCounter`/`turn` are in `u8` range.

## `eventscripts` schema (metadata-only; the escape-hatch consumer)

Source: `src/data/ch2_eventscripts.json`, one object per script under
`"scripts"`: `owner` (the `struct ChapterEventGroup` category from
`src/events/ch2-eventinfo.h`'s `Ch2Events` -- `turn_based`,
`character_based`, `location_based`, `misc_based`, `tutorial`,
`beginning_scene` -- or `world_map` for the 3 scripts wired from
`src/events/ch2-wm.h` instead), `symbol`, `kind`
(`event_list_scr`/`event_scr_wm`), and `header` (always
`include/eventcall.h` for this slice). All 43 Chapter 2 + Chapter 2
world-map `EventScr_Ch2_*`/`EventScrWM_Ch2_*` symbols required by the
vertical slice are present.

This table is **deliberately metadata-only**: it does not generate,
parse, or reinterpret any event-script/macro body, and it does not model
the higher-level `EventListScr_Ch2_*`/`Ch2Events` composition (those live
only in `.c`/implementation-only `.h` files, never `extern`-declared, so
they're out of `CSymbolRefField`'s header-declared scope by construction
-- that composition is exactly what the `eventlists` table below adds).
It only proves that each of the 43 leaf symbols this slice depends on is
genuinely declared where expected. `default_output_name`/`generate_c`
are left unset, so `cli.py generate` explicitly prints `skip: table
'eventscripts' is metadata-only; no C output generated` rather than
emitting a meaningless empty file.

Validations enforced (`eventscripts/schema.py: validate()`): unique
symbols; `owner`/`kind` are one of the valid enum values; every
`(symbol, header)` pair is checked with `CSymbolRefField` -- the header
file must exist, and the symbol must be found as an `extern`-declared
object in it. This is the first real production use of the escape hatch
(see next section), not just its own test fixtures.

## `eventlists` schema (Chapter 2 event-list composition + `Ch2Events`)

Source: `src/data/ch2_eventlists.json`, three top-level sections:

```json
{
  "$schema": "fe8.eventlists.v1",
  "lists": [
    {
      "field": "turnBasedEvents",
      "symbol": "EventListScr_Ch2_Turn",
      "entries": [
        { "macro": "TURN", "args": [0, "EventScr_Ch2_Turn1Player", 1, 0, "FACTION_ID_BLUE"] }
      ]
    }
  ],
  "tutorial": {
    "field": "tutorialEvents",
    "symbol": "EventListScr_Ch2_Tutorial",
    "entries": ["EventScr_Ch2Tutorial1", "...", "EventScr_Ch2Tutorial30"]
  },
  "manifest": {
    "symbol": "Ch2Events",
    "fields": { "turnBasedEvents": "EventListScr_Ch2_Turn", "...": "..." }
  }
}
```

* **`lists`** covers exactly the 7 `EventListScr_Ch2_*` arrays
  (`turnBasedEvents`/`characterBasedEvents`/`locationBasedEvents`/
  `miscBasedEvents`/`specialEventsWhenUnitSelected`/
  `specialEventsWhenDestSelected`/`specialEventsAfterUnitMoved`), each an
  ordered list of **macro calls**, not plain values -- every entry is
  `{"macro": NAME, "args": [...]}`, where each argument is a bare JSON
  int (C integer literal), a bare JSON string (C symbol/token), or a
  nested `{"macro": ..., "args": [...]}` object (currently only
  `EVFLAG_TMP(n)`, the free chapter-scoped temp-flag range documented in
  `include/constants/event-flags.h`, read live rather than hardcoded).
  This is what "typed ordered args" means in the Issue #5 WHAT section --
  every argument keeps its int/symbol/nested-call shape instead of
  collapsing to an opaque string, so `MacroArg`/`MacroCall.as_tuple()`
  can structurally compare generated vs. hand-parsed calls. Supported
  macros (`MACRO_SPECS` in `eventlists/schema.py`) are exactly the ones
  Chapter 2's hand source uses: `TURN`, `CHAR`, `Village`, `Armory`,
  `DefeatAll`, and the zero-arg, bare (no-parens) `CauseGameOverIfLordDies`
  -- no event-script bytecode is ever expanded or reimplemented; the
  generator emits the identical C macro-call text (see
  `include/EAstdlib.h`/`include/EA_Standard_Library/Main_Code_Helpers.h`
  for the macro definitions this structurally encodes). Each list also
  has a per-field macro allow-list (`ALLOWED_MACROS_BY_FIELD`) and a
  per-field required `event_scr` owner (`EVENT_SCR_OWNER_BY_FIELD`), so
  e.g. a `CHAR(...)` call can't sneak into `turnBasedEvents`, and a
  `TURN(...)` call's script argument must resolve to an `eventscripts`
  record whose `owner` is `"turn_based"`. The bare `END_MAIN` terminator
  is never stored in JSON -- like `ITEM_NONE`/`TRAP_NONE` in
  `shops`/`traps`, it's auto-appended at generation and explicitly
  including it is a validation error.
* **`tutorial`** is the `EventListScr_Ch2_Tutorial[]` pointer array --
  exactly 30 ordered, unique `EventScr_Ch2TutorialN` symbols (each must
  resolve in the `eventscripts` dependency table with `owner ==
  "tutorial"`), terminated by a `NULL` pointer that (like `END_MAIN`
  above) is auto-appended, never stored explicitly.
* **`manifest`** is the full `struct ChapterEventGroup Ch2Events`
  designated initializer -- all 20 fields from `include/chapterdata.h`
  are required (`MANIFEST_FIELD_SPECS`), each tagged with how it's
  resolved: the 7 list fields and `tutorialEvents` must be non-`NULL` and
  match the corresponding declared `lists`/`tutorial` symbol exactly;
  `traps`/`extraTrapsInHard` and the 6 encounter-choice unit fields are
  nullable and, when set, must resolve into the `traps`/`units`
  dependency tables; `beginningSceneEvents` must resolve in
  `eventscripts` with `owner == "beginning_scene"`; `endingSceneEvents`
  must resolve in `eventscripts` (owner unconstrained, since
  `EventScr_Ch2_EndingScene`'s real owner is `"misc_based"` -- it's also
  reused as `DefeatAll`'s argument in `miscBasedEvents`). `NULL` fields
  stay explicit (`null` in JSON, not simply omitted).

Cross-table validation (unit/shop/trap/event-script references) is
resolved against Batch A's own `units`/`shops`/`traps`/`eventscripts`
JSON records, loaded via the new `dependency_tables()` mechanism (see
above) -- not just header declarations, which would trivially accept any
chapter's symbol.

Validations enforced (`eventlists/schema.py: validate()`): exactly the 7
known list fields present, no duplicates/missing/extras; every list/
tutorial symbol pairwise unique; every macro allow-listed for its list;
macro arity and per-argument type (`flag`/`event_scr`/`character`/
`faction`/`shop_symbol`/`int`) checked against `MACRO_SPECS`; `EVFLAG_TMP`
range-checked against the header-derived free range and **unique
chapter-wide** (not just per-list, since temp flags are a shared
chapter-scoped array); duplicate `(x, y)` location coordinates rejected
within `locationBasedEvents`; explicit `END_MAIN`/`NULL` terminators
rejected; the tutorial list is exactly 30 entries, unique, each resolving
to an `owner == "tutorial"` eventscript; every `Ch2Events` designated
field present/known and resolved per its `MANIFEST_FIELD_SPECS` kind.

### Round-trip parsing (`eventlists/parser.py`)

Unlike every other table's array literals, the 7 `EventListScr_Ch2_*`
arrays have **no top-level commas between entries** -- each macro's
expansion already supplies its own trailing comma internally (see
`_EvtParams2`/`_EvtParams4` in `include/eventscript.h`), so
`cparse.split_top_level_entries` (comma-splitting) doesn't apply. Instead
`parser.py` implements a small paren-depth-aware macro-call tokenizer:
read an identifier, optionally followed by a balanced `(...)` argument
list (itself recursively parsed for nested calls like `EVFLAG_TMP(9)`),
repeated until the bare `END_MAIN` sentinel. The tutorial pointer array
and the `Ch2Events` struct *are* plain comma-separated/designated-
initializer literals, so those reuse `cparse.find_matching_brace`/
`extract_designated_fields` directly. `compare_records()` then
structurally diffs (via `MacroCall.as_tuple()`) every generated macro
call, the tutorial list, and every manifest field against
`src/events/ch2-eventinfo.h` -- **100%**, not a sample (Issue #5 Batch B
DONE criterion).

## The C-symbol escape hatch (`escape_hatch.py`)

Originally implemented and tested as a generic, reusable mechanism ahead
of a real consumer; the Chapter 2 slice's `eventscripts` table (above) is
now that first production consumer:

* A schema field can declare it references a symbol that must be
  **declared in a specific header** (`CSymbolRefField(header_path)`).
  The header acts as an allowlist by construction -- only symbols its
  author chose to declare (function declarations/definitions or `extern`
  object declarations) are acceptable.
* `validate(symbol, loc, reference_path)` rejects: malformed identifiers
  (not a valid C token), and symbols not found declared in the header
  (with the header path and the list of symbols that *are* declared, in
  the error message).
* `emit(symbol)` returns the symbol **unquoted** -- the generator writes
  it as a bare C token (e.g. a function-pointer field initializer), not a
  JSON string, so it compiles as a real reference.

See `tests/test_escape_hatch.py` and
`tests/fixtures/escape_hatch/demo_header.h` for the original
fixture-driven proof (valid function reference, valid extern-object
reference, malformed identifier, undeclared symbol), and
`eventscripts/schema.py` + `tests/test_eventscripts_schema.py` for the
production usage against the real `include/eventcall.h`.

## CLI

```bash
python3 -m scripts.generated_data validate --table supports
python3 -m scripts.generated_data generate  --table supports
python3 -m scripts.generated_data check     --table supports

# Same three subcommands work identically for every registered table --
# cli.py has no per-table dispatch logic:
python3 -m scripts.generated_data validate --table units
python3 -m scripts.generated_data generate  --table units
python3 -m scripts.generated_data check     --table units

python3 -m scripts.generated_data validate --table shops
python3 -m scripts.generated_data generate  --table shops
python3 -m scripts.generated_data check     --table shops

python3 -m scripts.generated_data validate --table traps
python3 -m scripts.generated_data generate  --table traps
python3 -m scripts.generated_data check     --table traps

# eventscripts is metadata-only: `generate`/`check` skip C output but
# still write/compare the committed inventory.
python3 -m scripts.generated_data validate --table eventscripts
python3 -m scripts.generated_data generate  --table eventscripts
python3 -m scripts.generated_data check     --table eventscripts

# eventlists cross-validates against units/shops/traps/eventscripts
# (via dependency_tables()); --dep-source NAME=PATH overrides one
# dependency's source, e.g. to validate a fixture in isolation:
python3 -m scripts.generated_data validate --table eventlists
python3 -m scripts.generated_data generate  --table eventlists
python3 -m scripts.generated_data check     --table eventlists
python3 -m scripts.generated_data validate --table eventlists \
  --source scripts/generated_data/tests/fixtures/eventlists/valid.json --no-roundtrip \
  --dep-source units=scripts/generated_data/tests/fixtures/eventlists/deps_units.json \
  --dep-source shops=scripts/generated_data/tests/fixtures/eventlists/deps_shops.json \
  --dep-source traps=scripts/generated_data/tests/fixtures/eventlists/deps_traps.json \
  --dep-source eventscripts=scripts/generated_data/tests/fixtures/eventlists/deps_eventscripts.json
```

Common flags: `--source PATH` (JSON source, default per-table, e.g.
`src/data/supports.json`/`src/data/ch2_units.json`/
`src/data/ch2_shops.json`/`src/data/ch2_traps.json`/
`src/data/ch2_eventscripts.json`/`src/data/ch2_eventlists.json`),
`--hand-source PATH` (round-trip comparison target, default per-table,
e.g. `src/data_supports.c`/`src/events_udefs.c`/`src/events_shoplist.c`/
`src/events_trapdata.c`/`src/events/ch2-eventinfo.h`; `eventscripts` has
none -- it's metadata-only), `--no-roundtrip` (skip it), `--out-dir DIR`
(generated C output directory, default `build/generated/data`),
`--inventory PATH` (committed inventory/summary path, default per-table
under `reports/`), `--dep-source NAME=PATH` (repeatable; overrides one
declared table dependency's JSON source -- only meaningful for schemas
that declare `dependency_tables()`, currently just `eventlists`).

* `validate` -- load + validate; prints **every** diagnostic (not just the
  first), each with `file:line:column`.
* `generate` -- validate, then write-if-changed the generated C89 (or
  print the metadata-only skip message) and the committed inventory.
  Fails (writing nothing) if validation or the round-trip check fails.
* `check` -- the CI-suitable gate. Self-heals the ephemeral `build/`
  output (write-if-changed, since it's gitignored and there is nothing
  committed to have drifted against), but only *compares* the committed
  inventory report -- it never rewrites it. Fails with a `DRIFT:` message
  if the committed inventory is stale relative to the current JSON
  source, in addition to failing on any validation/round-trip error.

## Make targets (`generated_data.mk`)

Standalone, not wired into `all` or `.github/workflows/build.yml` (which
is scoped to target-ROM linker/boot checks per repository policy) --
deliberately isolated so this work can't destabilize the existing CI
gate. `GENERATED_DATA_TABLES` now lists all 6 registered tables, so every
target below loops over `supports units shops traps eventscripts
eventlists`:

```bash
make generated-data-validate   # validate only, all tables
make generated-data-generate   # validate + write build/ output + inventory, all tables
make generated-data-check      # CI-suitable: fails on drift, from a clean tree, all tables
make generated-data-test       # python3 -m unittest discover -s scripts/generated_data/tests
```

## Testing

```bash
python3 -m unittest discover -s scripts/generated_data/tests -v
```

Covers, for `supports`: valid generation, deterministic/repeatable
generation, write-if-changed semantics, duplicate owner/partner/symbol
detection, missing character references, self-references, out-of-range
values, parallel-array/count mismatches, fixed-capacity overflow,
reciprocal mismatch and reciprocal-missing detection, `file:line:column`
source locations, the escape-hatch mechanism (valid/malformed/undeclared
symbols against its own test fixtures), the full 33-record hand-table
round trip (plus injected mismatch detection), and CLI-level drift
detection (including against the real committed
`src/data/supports.json` and inventory report).

Additionally covers, for the Batch A tables (`test_units_schema.py`,
`test_units_roundtrip.py`, `test_units_generate.py`,
`test_shops_schema.py`, `test_shops_roundtrip.py`,
`test_shops_generate.py`, `test_traps_schema.py`,
`test_traps_roundtrip.py`, `test_traps_generate.py`,
`test_eventscripts_schema.py`, and the new-table cases in
`test_cli_new_tables.py`):

* `units`: duplicate group symbols, missing character/class/item
  references, out-of-range level/position/REDA values, item-capacity
  overflow, wrong AI-array length, header-derived constant reads
  (`UNIT_DEFINITION_ITEM_COUNT`, `UDEF_AIIDX_MAX`), generic filler units'
  raw-int `charIndex`, deterministic generation, and the full 7-group
  hand-table round trip against `src/events_udefs.c` (plus fixture-based
  match/mismatch and injected-mismatch detection).
* `shops`: duplicate shop symbols, missing item references, empty
  `items[]`, explicit `ITEM_NONE` terminator rejection, deterministic
  generation, and the full hand-table round trip against
  `src/events_shoplist.c` (plus fixture-based match/mismatch detection).
* `traps`: duplicate trap array symbols, undefined trap-type references,
  explicit `TRAP_NONE` terminator rejection, out-of-range
  position/subtype values, capacity-override overflow detection,
  header-derived constant reads (`TRAP_MAX_COUNT`, the scoped trap-type
  enum), deterministic generation, the full hand-table round trip against
  the (trivial) Chapter 2 arrays in `src/events_trapdata.c`, and a general
  non-trivial round trip against the real `TrapData_Event_Ch7` (2 ballista
  entries) proving the full 6-field format works beyond Chapter 2's own
  trivial data.
* `eventscripts`: duplicate symbols, unknown owner/kind values, undeclared
  symbol detection, missing header detection, and the full 43-record
  validation against the real `include/eventcall.h`.
* CLI: `validate`/`generate`/`check` for all 4 Batch A new tables against
  both fixtures and the real committed Chapter 2 JSON sources, including
  the `eventscripts` metadata-only skip message and drift-free checks
  against all 4 tables' real inventories.

Additionally covers, for Batch B (`test_eventlists_schema.py`,
`test_eventlists_generate.py`, `test_eventlists_roundtrip.py`, and the
`CliEventListsTests` cases in `test_cli_new_tables.py`):

* `eventlists`: missing unit/shop/trap/event-script cross-table
  references (each resolved against fixture-only
  `units`/`shops`/`traps`/`eventscripts` dependency records via
  `--dep-source`, proving isolation from the real Chapter 2 data), wrong
  macro arity/argument type, duplicate chapter-wide `EVFLAG_TMP(n)` use,
  duplicate `locationBasedEvents` coordinates, a macro used in a list
  that doesn't allow it, explicit `END_MAIN`/`NULL` terminator rejection,
  a missing required list field, an `EVFLAG_TMP` value outside the
  header-documented free range, duplicate list symbols, tutorial
  wrong-count/duplicate/explicit-terminator/wrong-owner detection,
  manifest missing/extra/mismatched-reference field detection,
  deterministic C89 generation (including that every list still
  terminates in a bare, un-comma'd `END_MAIN` and the tutorial array
  renders as a `NULL`-terminated pointer array), and the full hand-table
  round trip against `src/events/ch2-eventinfo.h` -- **100%** of macro
  invocations/order/arguments, the tutorial list, and every `Ch2Events`
  designated field (plus fixture-based match/mismatch and
  injected-mismatch detection for missing lists, tutorial entries, and
  manifest fields). CLI tests additionally cover `validate`/`generate`/
  `check` against the real committed `src/data/ch2_eventlists.json`
  (drift-free) and the fixture-plus-`--dep-source` isolation path.

All fixtures and scratch directories live under
`scripts/generated_data/tests/` (never `/tmp`).

## Remaining Issue #5 scope (explicitly not done here)

Batch A + Batch B together are scoped to the Chapter 2 vertical slice's
pure-data tables plus its event-list/manifest composition layer, and do
**not** close Issue #5. Batch B closes the gap Batch A left open --
`EventListScr_Ch2_*`/`Ch2Events` are now modeled, generated, and
round-tripped (`eventlists` table, this doc's `## eventlists schema`
section) -- so `eventscripts`' 43 leaf symbols are now provably wired
into the right `ChapterEventGroup` slots via `Ch2Events`' designated
fields and the 7 list arrays' macro-call arguments. Still open, for
**Batch C only**:

* **Batch C (whole-bundle manifest + chapter cross-check)**: a manifest
  tying all 6 Chapter 2 tables (`units`, `shops`, `traps`,
  `eventscripts`, `eventlists`, and `supports` where relevant) together
  into one bundle-level artifact, plus a chapter-settings cross-check
  (e.g. that `ChapterData`/`ChapterEventGroup` wiring for Chapter 2 as a
  whole is internally consistent, that every unit/trap/shop placement
  referenced anywhere is reachable from the chapter's declared tables,
  etc.) -- not attempted by Batch A or Batch B, which validate each
  table (and, for `eventlists`, its direct cross-table references)
  independently rather than the slice as one bundle.
* Migrating additional gameplay tables/chapters beyond this Chapter 2
  slice.
* Actually linking any generated table in place of its hand-written
  counterpart (requires the `ldscript.txt` link-order migration described
  in `CONTRIBUTING.md`, and is out of scope until a table is fully
  proven).
* Wiring `generated-data-check` into `.github/workflows/build.yml` (left
  as a standalone target for now, per repository policy of keeping that
  workflow scoped to target-ROM linker/boot checks).
