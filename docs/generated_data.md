# Generated-data platform (Issue #5 Chapter 2 slice -- Batch A + B + C; global `items`/`classes` Batch 1; `characters` Batch 2a)

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

**Batch C** (this update) closes that gap: a `chapterbundle` table
(`src/data/ch2_bundle.json`) is a **metadata-only, cross-table view**
tying every Batch A/B Chapter 2 table together into one bundle, plus the
`chapter_settings.json`/`gChapterDataAssetTable` wiring that actually
selects Chapter 2 at runtime:

* Cross-checks `chapter.id`/`chapterSettingsIndex`/`internalName`/
  `mapEventDataId` against `include/constants/chapters.h`,
  `src/data/chapter_settings.json`, and `src/data/data_8B363C.c`'s
  `gChapterDataAssetTable`, proving `mapEventDataId` really does resolve
  to the `eventlists` table's own `Ch2Events` manifest symbol.
* Proves every `units`/`shops`/`traps`/`eventscripts` record this bundle
  declares is reachable -- from the `eventlists` manifest/list-entry
  macro arguments, or from an explicitly-cited hand-written reference
  (`externalReferences`, verified by literal whole-word search, never by
  expanding event-script bytecode) -- so there is no orphan Ch2-owned
  record.
* Cross-checks Chapter 2's required `SupportData` owners (every
  non-enemy unit character) both exist and reciprocate.
* Declares the `CHARACTER_*`/`CLASS_*`/`ITEM_*` IDs Chapter 2's own unit/
  shop/support data actually uses as a **reference-only** dependency set
  -- cross-checked against the live enum headers and against exactly
  what Chapter 2's data references (both directions: undeclared and
  unused), but explicitly **not** claimed as authored by this slice.
* Proves the whole multi-table dependency graph (every registered
  table's `dependency_tables()` edges, not just this one table's own) is
  acyclic and has a deterministic topological order.
* Ships a committed whole-bundle inventory/report
  (`reports/generated_data_bundle_inventory.md`) with per-table record
  counts/digests, the external-reference table, the reference-only
  dependency sets, the full dependency-graph digest/topo-order, and one
  overall bundle digest.
* `generated-data-check` is now wired into
  `.github/workflows/build.yml`, once per job, before the modern debug/
  release linker checks -- see "Make targets" below.

See "## chapterbundle schema (Batch C whole-bundle manifest)" below for
the full write-up, and "Remaining Issue #5 scope" at the end of this
document for what's still open.

**Issue #5 Batch 1** (this update) adds the first **global, non-chapter-
scoped** table: `items` (`src/data/items.json`), authoring all 206
vanilla `gItemData[]` records (`ITEM_NONE`..`ITEM_UNK_CD`) field-for-field
against `struct ItemData` (`include/bmitem.h`). Unlike every Batch A/B/C
table, `items` is not part of the Chapter 2 vertical slice or the
`chapterbundle` manifest -- it is scoped to schema/generate/round-trip
only, per Issue #5 Batch 1's explicit "no link swap" boundary. See "##
`items` schema (Issue #5 Batch 1: full global item table)" below for the
full write-up.

**Issue #5 Batch 1, continued** (this update) adds the second global
table: `classes` (`src/data/classes.json`), authoring all 127 vanilla
`gClassData[]` records (`CLASS_EPHRAIM_LORD`..`CLASS_PUPIL_T1`)
field-for-field against `struct ClassData` (`include/bmunit.h`). Like
`items`, `classes` is scoped to schema/generate/round-trip only -- no
link swap, no Chapter 2/`chapterbundle` participation. See "##
`classes` schema (Issue #5 Batch 1: full global class table)" below for
the full write-up.

**Issue #5 Batch 2a** (this update) adds the **schema/dependency-
validation foundation only** for the third global table: `characters`
(`gCharacterData[]`/`struct CharacterData`, `include/bmunit.h`). Unlike
`items`/`classes`, this batch does **not** author any real, complete
`src/data/characters.json` source, nor does it add `generate.py`/
`parser.py`/`inventory.py` -- there is no C89 emission, no round-trip
against `src/data_characters.c` (which remains the sole hand-written,
linked source, untouched), and no committed inventory report. It is
also **not** added to `generated_data.mk`'s `GENERATED_DATA_TABLES` or
CI. What Batch 2a *does* provide: `CharactersTableSchema`, registered in
`registry.py` so `python3 -m scripts.generated_data validate --table
characters --source <path>` works end-to-end today, modeling
`gCharacterData[]`'s unique 256-slot dual symbolic
(`CHARACTER_*`)/raw-integer (unnamed generic-template) designator model,
every authorable `CharacterData` field/range/reference, the
`supportData` cross-table reference into `supports`, and a
`dependency_tables()` cross-check against `classes` -- see "## `characters`
schema (Issue #5 Batch 2a: schema/dependency-validation foundation)"
below for the full write-up. Batch 2b/2c (full field-for-field
transcription of all 256 vanilla records, `generate`/round-trip against
`src/data_characters.c`, and any eventual link-order migration) remain
open -- see "Remaining Issue #5 scope" at the end of this document.

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
| Structured source (items, global, Issue #5 Batch 1) | `src/data/items.json` | Yes | No |
| Hand-written canonical C (existing) | `src/data_items.c` | Yes | **Yes** -- unchanged, still canonical |
| Generated C89 (bulky) | `build/generated/data/data_items.c` | **No** (gitignored) | No |
| Committed inventory/summary | `reports/generated_data_items_inventory.md` | Yes (small) | N/A |

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
  items/
    schema.py           ItemRecord (full struct ItemData -- 19 authored
                        fields + derived .number), load_records(),
                        validate() -- the first global (non-chapter-scoped)
                        table, covering all 206 vanilla ITEM_* records
                        with full contiguous 0..205 coverage validation
    generate.py         C89 emission of gItemData[] matching the hand
                        file's own default-omission convention field for
                        field (only .weaponType/.iconId always emitted)
    parser.py           round-trip parser for src/data_items.c + comparer
                        (a bespoke offset-preserving entry splitter, since
                        gItemData[] is one 206-entry array needing
                        per-entry line numbers for diagnostics)
    inventory.py        builds the committed inventory/summary report
                        (weapon-type histogram, IA_* attribute usage,
                        pStatBonuses/pEffectiveness pointer-symbol usage)
  classes/
    schema.py           ClassRecord (full struct ClassData -- ~20 authored
                        fields/sub-objects + derived .number), load_records(),
                        validate() -- the second global (non-chapter-scoped)
                        table, covering all 127 vanilla CLASS_* records
                        with full contiguous 1..127 coverage validation
                        (excluding CLASS_NONE/the CLASS_OBSTACLE alias)
    generate.py         C89 emission of gClassData[] matching the hand
                        file's own default-omission convention field for
                        field, including the literal (void *)0x00000000
                        null-pointer cast for immobile classes' movCostTable
    parser.py           round-trip parser for src/data_classes.c + comparer
                        (reuses the same offset-preserving entry splitter
                        pattern as items/parser.py, since gClassData[] is
                        one 127-entry array needing per-entry line numbers)
    inventory.py        builds the committed inventory/summary report
                        (CA_* attribute usage, baseRanks weapon-type
                        histogram, battleAnim symbol usage)
  chapterbundle/
    schema.py           ChapterBundleRecord (metadata-only whole-bundle
                        view), load_records(), validate() -- cross-checks
                        chapter_settings.json/gChapterDataAssetTable wiring,
                        reachability/orphans, support owners, and
                        character/class/item dependency sets across all 6
                        other dependency_tables(); full_dependency_graph()
                        merges every registered schema's own dependency
                        edges to prove the whole system's table DAG is
                        acyclic (no generate.py/parser.py -- nothing to
                        generate or round-trip for a metadata-only,
                        cross-table bundle)
    inventory.py        builds the committed whole-bundle inventory/
                        summary report (per-table counts/digests,
                        external references, dependency sets, full
                        dependency-graph digest/topo-order, one bundle
                        digest)
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
`supports`, `units`, `shops`, `traps`, `eventscripts`, `eventlists`, and
`chapterbundle` (all v1); the CLI resolves `--table NAME` through this
registry instead of hardcoding imports, so `cli.py` has zero per-table
branches -- adding a table means adding a package + one line in
`registry.py`, nothing else.

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
  for `NAME = <value>,` enum entries across the *whole file*, filtered
  only by name prefix (used against `include/types.h` to read
  `UNIT_SUPPORT_MAX_COUNT` so the fixed-capacity check can't silently go
  stale if the struct's array size changes, and similarly for other
  single-enum headers). It is **not** used for `CHARACTER_*` references
  anywhere in this framework: `include/constants/characters.h` contains a
  second, separate `event_autoload_pid_idx` enum sharing the same
  `CHARACTER_` textual prefix, so a whole-file prefix scan would wrongly
  admit its event-engine slot sentinels as if they were valid
  `CharacterData` designators. Every table that reads `CHARACTER_*`
  symbols instead delegates to `character_refs.read_character_
  designators()` (see "`characters` schema" below), which scopes strictly
  to the primary designator enum block.
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

## `chapterbundle` schema (Batch C whole-bundle manifest)

Source: `src/data/ch2_bundle.json`. Like `eventscripts`, this table is
**metadata-only**: there is no single hand-written C file that *is* "the
bundle" to round-trip against, so `default_hand_source`/
`default_output_name` are both `None` (`generate`/`check` skip C output,
same as `eventscripts`). The bundle is a cross-table *view* over Batch
A/B's own tables, each of which is still independently round-tripped by
its own schema.

```json
{
  "$schema": "fe8.chapterbundle.v1",
  "chapter": {
    "id": "CHAPTER_L_2", "name": "Ch2", "chapterSettingsIndex": 2,
    "internalName": "L02", "mapEventDataId": 13
  },
  "manifest": { "table": "eventlists", "symbol": "Ch2Events" },
  "tables": {
    "units": { "source": "src/data/ch2_units.json", "symbols": ["..."] },
    "shops": { "source": "src/data/ch2_shops.json", "symbols": ["..."] },
    "traps": { "source": "src/data/ch2_traps.json", "symbols": ["..."] },
    "eventscripts": { "source": "src/data/ch2_eventscripts.json", "symbols": ["..."] },
    "eventlists": { "source": "src/data/ch2_eventlists.json", "symbols": ["..."] }
  },
  "supportOwners": { "source": "src/data/supports.json", "required": ["CHARACTER_EIRIKA", "..."] },
  "externalReferences": [
    { "table": "units", "symbol": "UnitDef_Ch2Enemy_0", "file": "src/events/ch2-eventscript.h" }
  ],
  "dependencies": { "characters": ["..."], "classes": ["..."], "items": ["..."] }
}
```

* **`chapter`** is the row this bundle claims to be: `id` must resolve
  in `include/constants/chapters.h`'s `chapter_idx` enum, and its enum
  value must equal `chapterSettingsIndex`; that index's
  `chapter_settings.json` row's `internalName`/`mapEventDataId` must
  match exactly.
* **`manifest`** is which table/symbol `mapEventDataId` resolves to via
  `gChapterDataAssetTable` (parsed from `src/data/data_8B363C.c` with
  `cparse.find_c_array_blocks`/`split_top_level_entries`, the same
  brace-depth-aware helpers `eventlists/parser.py` uses) -- and is
  cross-checked to equal the `eventlists` dependency table's own
  `manifest.symbol`, so the bundle and the `eventlists` table can never
  silently disagree about which array *is* `Ch2Events`.
* **`tables`** covers exactly the 5 registered per-symbol Chapter 2
  tables (`units`/`shops`/`traps`/`eventscripts`/`eventlists` --
  `supports` is chapter-agnostic and is referenced only via
  `supportOwners`, never listed here). Each entry's `symbols` must
  exactly match that table's real records: any declared symbol that
  isn't a real record is an error (a bogus/renamed reference); any real
  record missing from `symbols` is also an error ("stale bundle
  manifest" -- content-level drift distinct from the CLI's own
  committed-inventory drift check).
* **Reachability / orphan check**: every declared `units`/`shops`/
  `traps`/`eventscripts` symbol must be reachable from the `eventlists`
  manifest -- unit/trap fields and shop/event-script macro arguments
  (reusing `eventlists/schema.py`'s own `MACRO_SPECS`) -- or explicitly
  declared in `externalReferences`. An `externalReferences` entry is
  only accepted if its cited symbol literally appears (whole-word) in
  its cited file -- proving the citation is genuine without parsing or
  reimplementing event-script bytecode. (There is no equivalent "orphan
  eventlist": an `eventlists` table's own list/tutorial/manifest symbols
  *are* the manifest, so they're trivially always reachable.) The real
  Ch2 bundle needs external references for the 6 unit groups and 3
  world-map event scripts only reachable from hand-written event-script/
  world-map wiring (`src/events/ch2-eventscript.h`,
  `src/events/lordsplit-eventscript.h`, `src/events/ch2-wm.h`) rather
  than the typed `eventlists` macros.
* **`supportOwners.required`** must equal the *computed* set of every
  Ch2 unit character with a symbolic `charIndex` and
  `allegiance != FACTION_ID_RED` (i.e. every ally/NPC unit, not the red
  faction's boss characters) -- each must have its own `SupportData`
  record in the `supports` dependency table, and every partner that
  record lists must itself have a `SupportData` record (reciprocal
  support required).
* **`dependencies`** is the `characters`/`classes`/`items` this bundle's
  own `units`/`shops`/`supportOwners` data references -- scoped
  deliberately narrowly (not the transitive closure of support
  reciprocal partners or `eventlists` macro-argument characters, which
  are already validated by their own tables) -- cross-checked for
  uniqueness, existence in the live `characters.h`/`classes.h`/
  `items.h` headers, and an exact two-way match against what's actually
  used (undeclared-but-used and declared-but-unused are both errors).
  This is explicitly **reference-only**: it does not claim these
  characters/classes/items are themselves authored by this slice.
* Finally, `full_dependency_graph()` merges *every* registered table
  schema's `dependency_tables()` edges (not just `chapterbundle`'s own)
  into one combined `DependencyGraph` and calls `topo_order()`, proving
  the whole multi-table system -- not just this one table's direct
  dependencies -- is acyclic and deterministic.

Cross-table validation loads `units`/`shops`/`traps`/`eventscripts`/
`eventlists`/`supports` via `dependency_tables()` (same mechanism
`eventlists` uses); the chapter-settings/asset-table/chapters-enum
cross-check instead reads those pre-existing, read-only repo assets
directly (`read_chapter_settings_row()`/`read_asset_table_entries()`),
since they're consumed, not owned, by this schema.

### Committed inventory (`chapterbundle/inventory.py`)

`reports/generated_data_bundle_inventory.md` is the committed,
CI-checked whole-bundle report: per-table symbol counts and a stable
digest of each table's declared symbol list, the `externalReferences`
table (symbol/table/cited file), the `dependencies` sets marked
reference-only, the `full_dependency_graph()` digest and deterministic
topological order, and one overall bundle digest combining all of the
above -- so any drift in any of it (a symbol added/removed anywhere in
the bundle's view, a dependency added/removed, the table graph's shape
changing) fails `generated-data-check`.

## `items` schema (Issue #5 Batch 1: full global item table)

Source: `src/data/items.json` (schema `fe8.items.v1`), one object per
`ITEM_*` record under `"items"`, mirroring `struct ItemData`
(`include/bmitem.h`) field for field: `nameTextId`/`descTextId`/
`useDescTextId`, `weaponType` (`ITYPE_*`), `attributes` (a validated
`IA_*` bitmask list), nullable `statBonuses`/`effectiveness` (C-symbol
references into `ItemBonus_*`/`ItemEffectiveness_*` via
`CSymbolRefField`), `maxUses`/`might`/`hit`/`weight`/`crit`, `range`
(`{min, max}` nibbles, packed into the struct's single `.encodedRange`
byte), `costPerUse`, `requiredWexp` (a `WPN_EXP_*` symbolic threshold --
this is the struct's misleadingly-named `.weaponRank` field; see
`GetItemRequiredExp` in `src/bmitem.c`), `iconId`, `useEffect` (a bare
`u8`; the codebase defines no `USE_EFFECT_*` enum for it), `weaponEffect`
(`WPN_EFFECT_*`), and `weaponExp`. `.number` is **never authored** -- it
always equals the record's own `[ITEM_X]` designator symbol in every one
of the 206 vanilla records, so the generator derives it directly from
each record's own `item` key.

Unlike every Batch A/B/C table, `items` is **global, not chapter-scoped**:
it is not part of `chapterbundle` and covers the entire vanilla `ITEM_*`
enum (`ITEM_NONE`..`ITEM_UNK_CD`, 206 contiguous entries, indices 0..205)
in one flat, index-designated array, rather than a per-chapter group.

Validations enforced (`items/schema.py: validate()`): unique item
entries; **full contiguous `ITEM_*` coverage** (every enum value from 0
to the live max, no gaps or strays -- the one full-coverage check none of
the other tables need, since they only reference a subset of their
enums); `weaponType`/`requiredWexp`/`weaponEffect` are defined enum
constants; `attributes` is a validated, duplicate-free `IA_*` bitmask
list (scoped to the `IA_NONE = 0, ...` block, deliberately excluding the
`// Helpers` combo constants `IA_REQUIRES_WEXP`/`IA_LOCK_ANY`, which no
vanilla record references directly); nullable `statBonuses`/
`effectiveness` via `CSymbolRefField` against `include/bmitem.h`/
`include/variables.h` respectively; all numeric fields' widths/ranges
(`u8`/`u16`, and the `range.min`/`range.max` nibble bounds of `[0, 15]`);
`nameTextId`/`descTextId`/`useDescTextId` bounded by the live
`MSG_COUNT` (read via the new generic `extract_define_constant()`
helper, since `MSG_COUNT` is a `#define`, not an enum entry); `iconId`
bounded by a count of `u8 item_icon_*[] = INCBIN_U8(".../*.4bpp")`
declarations in `src/data/data_item_icon.c` -- the strongest
deterministic *source-level* bound available, since the real built
item-icon asset table only exists after `gbagfx` decompresses these
tiles during the build.

The generator (`items/generate.py`) matches the hand file's own
default-omission convention field for field: only `.weaponType` and
`.iconId` are unconditionally emitted (206/206 hand records, including an
explicit `.iconId = 0x0` for `ITEM_NONE`); every other field is omitted
when it equals its default (confirmed there are zero explicit-zero/
`NONE`/`null` occurrences for any conditional field across the entire
206-record hand file). Multi-flag `.attributes` combos are always emitted
in ascending bit-position order, matching every one of the 25 distinct
combos observed in the hand file (verified with zero violations),
regardless of the JSON's own authoring order.

### Round-trip checker (`items/parser.py`)

`gItemData[]` is one large, single, 206-entry designated-initializer
array (not many small per-symbol arrays like the other tables), so the
round-trip parser uses a bespoke, offset-preserving top-level-entry
splitter (`_split_entries_with_offsets`) to keep each `[ITEM_X] = { ... }`
entry's own absolute character offset -- needed for accurate
`file:line:column` diagnostics per entry, since a single offset for the
whole array would be far too coarse. Every field is resolved to the same
symbol-blind canonical tuple (`ItemRecord.as_tuple()`): every enum-valued
field -- `weaponType` (`ITYPE_*`) included, alongside `attributes`/
`requiredWexp`/`weaponEffect` -- reduces to its live integer value, so
JSON authoring order or which of two equal-valued symbolic constants the
hand file happens to use never cause a false mismatch -- only the actual
encoded bytes matter.
`python3 -m unittest scripts.generated_data.tests.test_items_roundtrip`
proves all 206/206 records match `src/data_items.c` exactly with zero
diagnostics.

### Committed inventory (`items/inventory.py`)

`reports/generated_data_items_inventory.md` is the committed, CI-checked
report: total record count and index range, a weapon-type histogram, an
`IA_*` attribute-usage histogram, `pStatBonuses`/`pEffectiveness`
pointer-symbol usage tables, and the table's own dependency-graph digest.

## `classes` schema (Issue #5 Batch 1: full global class table)

Source: `src/data/classes.json` (schema `fe8.classes.v1`), one object per
`CLASS_*` record under `"classes"`, mirroring `struct ClassData`
(`include/bmunit.h`) field for field: `nameTextId`/`descTextId`,
`promotion` (a self-referential `CLASS_*` symbol, default `CLASS_NONE`
meaning "does not promote"), `smsId`, `slowWalking` (authored as a JSON
boolean -- only 2 valid states, `UNIT_WALKSPEED_FAST`/`_SLOW`),
`defaultPortraitId`, `sortOrder`, nested `base`/`max`/`growth`/
`promotionGain` stat sub-objects (mirroring `items`' `range: {min, max}`
pattern), `classRelativePower`, `attributes` (a validated `CA_*` bitmask
list), `baseRanks` (a sparse `{ITYPE_*: WPN_EXP_*}` map, keyed by weapon
type), nullable `battleAnim` (a `CSymbolRefField` reference into
`AnimConf_*`, `include/ekrbattle.h`), a required 3-entry `movCostTable`
(`[normal, rain, snow]`, each either a `TerrainTable_MovCost_*`
`CSymbolRefField` reference or JSON `null` for the 9/127 fully-immobile
vanilla records that use a literal `(void *)0x00000000` null-pointer cast
instead of a named table -- gorgon-egg forms, spent/empty ballistae,
`CLASS_UNK77`), required `terrainAvoid`/`terrainDefense`/
`terrainResistance` (`CSymbolRefField` references into
`TerrainTable_{Avo,Def,Res}_*`), and nullable `reservedTerrainTable`
(models the struct's unresearched `._pU50` escape-hatch field, a
`CSymbolRefField` reference into `Unk_TerrainTable_*`). `.number` is
**never authored** -- it always equals the record's own `[CLASS_X - 1]`
designator symbol in every one of the 127 vanilla records, so the
generator derives it directly from each record's own `class` key.

Like `items`, `classes` is **global, not chapter-scoped**: it is not part
of `chapterbundle` and covers the entire vanilla `CLASS_*` enum
(`CLASS_EPHRAIM_LORD`..`CLASS_PUPIL_T1`, 127 contiguous entries, indices
1..127) in one flat, index-designated array. `CLASS_NONE` (index 0, the
sentinel "no class" value) and the `CLASS_OBSTACLE` alias
(`#define`-like `CLASS_OBSTACLE = CLASS_EPHRAIM_LORD`, not its own enum
value) are both excluded from the required-coverage set.

Validations enforced (`classes/schema.py: validate()`): unique class
entries; **full contiguous `CLASS_*` coverage** (1..127, excluding
`CLASS_NONE`/`CLASS_OBSTACLE`); `promotion` is a defined `CLASS_*`
constant; `attributes` is a validated, duplicate-free `CA_*` bitmask list
(scoped to the `CA_NONE = 0, ...` block, deliberately excluding the
`// Helpers` combo constants `CA_REFRESHER`/`CA_FLYER`/
`CA_TRIANGLEATTACK_ANY`, which no vanilla record references directly);
`baseRanks` entries are validated `ITYPE_*` -> `WPN_EXP_*` pairs, bounded
to the struct's own live `baseRanks[8]` capacity (read from
`include/bmunit.h`, not hardcoded); all numeric fields' widths/ranges at
their **actual C type** bounds (`s8` stats validated against the full
`[-128, 127]` range, not just the narrower all-non-negative range every
vanilla value happens to use; `promotionGain`/`sortOrder` against `u8`'s
`[0, 255]`); `nameTextId`/`descTextId` bounded by the live `MSG_COUNT`;
`defaultPortraitId` bounded by the live count of numbered entries in
`src/portrait_data.c`'s `portrait_data[]` (172 entries, 0..171) and
`smsId` bounded by the live count of numbered entries in
`src/unit_icon_wait_data.c`'s `unit_icon_wait_table[]` (107 entries,
0..106) -- both the strongest deterministic *source-level* bounds
available, and, for `smsId`, provably **stronger** than the runtime
`GetInfo()` macro's raw 7-bit mask (`(id) & ((1<<7)-1)`, i.e. 0..127):
indices 107..127 would read past the real table's end, so validating
against the table's actual length (not the mask width) is the correct
bound; the exact `movCostTable` triplet capacity (read from
`include/bmunit.h`, not hardcoded); and every C-symbol-reference field
(`battleAnim`, non-null `movCostTable` entries, the three terrain
lookups, `reservedTerrainTable`) via `CSymbolRefField` against
`include/ekrbattle.h`/`include/variables.h` -- no new header declarations
were needed since every required symbol already has a public `extern`.

The generator (`classes/generate.py`) matches the hand file's own
default-omission convention field for field (confirmed per-field
presence statistics across all 127 hand records): `smsId` and all
base/max/growth/promotionGain stats, `pMovCostTable`, and the three
terrain lookups are unconditionally emitted; every other field
(`nameTextId`/`descTextId`, `promotion`, `slowWalking`,
`defaultPortraitId`, `sortOrder`, `classRelativePower`, `attributes`,
`baseRanks`, `battleAnim`, `reservedTerrainTable`) is omitted when it
equals its default. Multi-flag `.attributes` combos and `.baseRanks`
entries are always emitted in ascending bit-value/weapon-type-index
order, matching the hand file's own convention regardless of the JSON's
own authoring order. Null `movCostTable` entries emit the literal
`(void *)0x00000000` cast back verbatim, byte-for-byte matching the hand
file's own convention for immobile classes; `reservedTerrainTable` is the
only pointer field emitted with an explicit `&` prefix (its C type is
`const void*`, not `const s8*`, so a bare array-decay wouldn't have the
right type -- exactly mirroring `items`' `pStatBonuses` (`&`-prefixed)
vs. `pEffectiveness` (bare) asymmetry).

### Round-trip checker (`classes/parser.py`)

Like `items/parser.py`, `gClassData[]` is one large, single, 127-entry
designated-initializer array, so the round-trip parser reuses the same
offset-preserving top-level-entry splitter for accurate `file:line:column`
diagnostics per `[CLASS_X - 1] = { ... }` entry. Every field is resolved
to the same symbol-blind canonical tuple (`ClassRecord.as_tuple()`):
`promotion`/`attributes`/`baseRanks` reduce to their live integer
value(s); `battleAnim`/`movCostTable` entries/terrain lookups/
`reservedTerrainTable` are compared as bare token strings (no further
numeric encoding exists to resolve) -- and the literal
`(void *)0x00000000` null-pointer cast is recognized as the same "no
table" sentinel as JSON `null`, not a mismatched symbol.
`python3 -m unittest scripts.generated_data.tests.test_classes_roundtrip`
proves all 127/127 records match `src/data_classes.c` exactly with zero
diagnostics.

### Committed inventory (`classes/inventory.py`)

`reports/generated_data_classes_inventory.md` is the committed,
CI-checked report: total record count and index range, promotion/
slow-walking/no-battle-anim/reserved-terrain-table counts, a `CA_*`
attribute-usage histogram, a `baseRanks` weapon-type histogram,
`battleAnim` symbol-usage table, and the table's own dependency-graph
digest.

## `characters` schema (Issue #5 Batch 2a: schema/dependency-validation foundation)

**Scope note: schema/validation only.** This section documents what
`characters/schema.py` provides *today* -- there is no
`generate.py`/`parser.py`/`inventory.py`, no committed
`src/data/characters.json`, and no entry in `generated_data.mk`'s
`GENERATED_DATA_TABLES`/CI. `src/data_characters.c` remains the sole
hand-written, linked source for `gCharacterData[]`, untouched. What
*is* available today: `python3 -m scripts.generated_data validate
--table characters --source <path>` (schema `fe8.characters.v1`,
registered in `registry.py`), including `--dep-source
classes=PATH`/`--dep-source supports=PATH` overrides for the two
cross-table checks described below.

`gCharacterData[]` has a model unlike every other table this platform
covers: a fixed **256-slot** array (confirmed against
`src/data_characters.c`, which has exactly 256 top-level designated
initializers) addressed by a 1-based **designator** (`[designator - 1]`,
mirroring the hand file's own `[CHARACTER_X - 1]`/`[0x1B - 1]`
indexing), where each slot is *either*:

* **symbolic** (JSON key `"character"`, a named `CHARACTER_*` constant
  from `include/constants/characters.h`, excluding the `CHARACTER_NONE`
  sentinel -- its implied array index would be `-1`, never a valid
  entry) -- roughly 96 of the 256 designators have one; or
* **raw/generic-template** (JSON key `"characterId"`, a bare integer
  designator) for the remaining unnamed enemy/filler templates the hand
  file indexes with a raw hex literal (e.g. `[0x1B - 1]`).

Exactly one of the two keys is required per record (`load_records()`
raises immediately, like every other table's required-key checks, if a
record has both or neither). Designator `256` (`0x100`) is the single
**unreachable padding slot**: no real `u8` character byte can ever equal
256, so it can never be reached by `GetCharacterData()` at runtime, but
it is still a real, authored 256th array entry -- only `characterId`
(never `character`, since no `CHARACTER_*` constant is or could be
`256`) can designate it. `.number` is **never authored**: it is always
derived as `designator & 0xFF` -- for designators `1..255` that's the
designator itself; for the padding slot the `u8` truncation naturally
yields `0`, exactly matching the hand file's own explicit
`.number = 0` for that one entry with no special case needed.

`character` is deliberately **not** resolved with the generic,
whole-file `extract_enum_constants(header, name_prefix="CHARACTER_")`
scan every other reference field in this schema uses -- it delegates to
`scripts/generated_data/character_refs.py`'s `read_character_designators()`,
a dedicated reader scoped strictly to the *primary*, anonymous designator
enum (the block that opens with `CHARACTER_NONE = 0x00` and closes with
`CHARACTER_SNAG = 0xFF`). `include/constants/characters.h` also declares
a wholly separate, *named* sibling enum immediately after that block,
`event_autoload_pid_idx` (`CHARACTER_EVT_LEADER`/`ACTIVE`/`SLOTB`/`SLOT2`
= `0`/`-1`/`-2`/`-3`) -- event-engine "active unit slot" indices that
happen to share the same `CHARACTER_` textual prefix but are never valid
`CharacterData` designators (two of them aren't even representable as
one, being negative). A prefix-only scan cannot tell the two enums apart
and would wrongly accept all four as designators; `read_character_
designators()` instead matches only the primary block's own text
(bounded by its own `CHARACTER_NONE =`/closing `};`), so the sibling
enum's members -- or any other same-prefix collision -- are excluded
structurally rather than by name, and are reported as an ordinary
`undefined character reference` diagnostic if authored.

`character_refs.py` is a **shared, cross-table module** (living directly
under `scripts/generated_data/`, not nested in any single table's own
package) rather than a `characters`-schema-specific helper: every table
that references `CHARACTER_*` symbols -- `characters` (the record's own
designator), `units` (`charIndex`/`leaderCharIndex`), `supports` (`owner`/
`characters` partners), `eventlists` (`CHAR()` macro's `pid1`/`pid2`
arguments), and `chapterbundle` (`dependencies.characters`) -- delegates
to the same `read_character_designators()` to read `CHARACTERS_HEADER`,
so the same primary-block/sibling-enum scoping fix applies everywhere a
`CHARACTER_*` reference is validated, not just in `characters` itself.
The reader intentionally **includes** `CHARACTER_NONE` in its returned
mapping (it's a genuine primary-block member); each table's own
`validate()` decides independently whether `CHARACTER_NONE` is a valid
value for its own specific field -- only `characters`' own
record-designator field rejects it explicitly (a record can't be *about*
"no character"), while `units`/`supports`/`eventlists`/`chapterbundle`
accept it exactly as they did before this fix (none of their real
committed data uses it today, so no new nullability semantics were
introduced by consolidating the reader).

Unlike `classes`' coverage check (always on, range derived from the
live header's max value), the full dense `1..256` coverage domain here
is a **fixed module constant** (`DESIGNATOR_MIN`/`DESIGNATOR_MAX`, not
derived from `characters.h`, since raw/generic-template records fill in
every unnamed designator) and coverage enforcement itself is an
opt-in, top-level JSON boolean, `"fullCoverage"` (default `false`):
Batch 2a never commits a real, complete 256-record source, so every
fixture validated so far leaves it `false` and is checked purely for
uniqueness/reference/range correctness, not completeness. A future
batch that authors the real, complete source would set it `true`.

Every other `CharacterData` field is modeled and validated: `nameTextId`/
`descTextId` against the live `MSG_COUNT`; `defaultClass` both as a live
`CLASS_*` reference *and* -- when the `classes` dependency table is
loaded (`--dep-source classes=PATH`, or the CLI's own `dependency_tables()`
wiring) -- present among that table's own loaded `ClassData` records (two
distinct failure modes: undefined constant vs. valid-but-unauthored
class); `portrait` against the live count of numbered entries in
`src/portrait_data.c`'s `portrait_data[]` (same live table `classes`
already validates `defaultPortraitId` against); `miniPortrait` against a
**stronger-than-`u8`** bound proven live from `src/face.c`'s
`sGenericChibiImgLut[]` array literal (traced through
`GetUnitMiniPortraitId()`/`GetGenericChibiImg()` in `src/bmunit.c`/
`src/face.c`: `miniPortrait` indexes that array directly, so its entry
count -- not the raw `u8` width -- is the real bound); `affinity` as an
optional `UNIT_AFFIN_*` reference (read with a bespoke sequential-value
reader, `read_unit_affinities()`, since that enum only assigns its first
member explicitly and lets the rest auto-increment -- the generic
`extract_enum_constants()` helper cannot parse that); signed `base` stats
at the full `s8` range and unsigned `growth` stats at `u8`; `baseRanks`
(a sparse `{ITYPE_*: WPN_EXP_*}` map, bounded to the struct's own live
`baseRanks[8]` capacity, exactly like `classes`); `attributes` as a
validated `CA_*` bitmask list (the same enum `classes` reads, shared at
runtime via `UNIT_CATTRIBUTES()`); `visitGroup`/`baseLevel` at plain
`u8` (no stronger invariant found for either); and the reserved
`_u23`/`_u24`/`_u25`/`_u27` struct fields, which are **rejected outright
if authored at all** (a new pattern for this platform -- every other
table silently ignores unrecognized JSON keys, but these four are never
authorable, not merely defaulted).

`supportData` (optional, most records and *all* raw/generic-template
records have none) is modeled as a genuine cross-table reference, not
just a header-string check: when the `supports` dependency table is
loaded, a non-null `supportData` value must match some loaded
`SupportData` record's own `symbol`, *and* that record's `owner` must
equal this record's own `character` (reciprocal, like `chapterbundle`'s
support-owner check) -- raw/generic-template records referencing
`supportData` at all is rejected unconditionally (no proven
generic-template record has one). Both cross-table checks
(`defaultClass`-presence and `supportData`) degrade gracefully to
header-only/skipped when the corresponding dependency table isn't
loaded, via `dependency_tables() = ("classes", "supports")`.

### Fixtures and tests

`scripts/generated_data/tests/fixtures/characters/` holds fixture
sources using the real repository headers/enums throughout (`CHARACTER_
EIRIKA`/`CLASS_MYRMIDON`/etc.) so they validate identically whether run
directly (`characters_schema.validate()`) or via the CLI (which has no
header-override mechanism for a table's own headers, only for
`--dep-source`-loaded dependency tables) -- plus a small set of `mini_*`
header/source fixtures used only to unit-test the individual live-source
reader functions (`read_msg_count`, `read_portrait_count`,
`read_mini_portrait_capacity`, `read_character_data_array_capacity`,
`read_character_attributes`, `read_unit_affinities`) against small,
exact expected numbers. `python3 -m unittest
scripts.generated_data.tests.test_characters_schema` covers the record
model, structural/duplicate/coverage/reference/range/reserved-field/
support-data/class-dependency checks (including the
`CHARACTER_EVT_LEADER`/`ACTIVE`/`SLOTB`/`SLOT2` sentinel-rejection
regression and a synthetic sibling-enum-collision regression wired
through `characters_schema.validate()`'s own `characters_header=`
override parameter), and the header readers in isolation; `CliCharactersTests`
in `test_cli_new_tables.py` covers the same end to end through the CLI,
including `--dep-source` overrides and the sentinel-rejection
regression.

`scripts/generated_data/character_refs.py`'s own `read_character_
designators()` -- being shared across five tables -- has its own
dedicated test module, `test_character_refs.py`, independent of any
single table's fixtures: one set of tests runs it against the real
`characters.h` (confirming `CHARACTER_NONE`/`CHARACTER_EIRIKA`/
`CHARACTER_SNAG` are present and all four `CHARACTER_EVT_*` sentinels
are absent), and a second set runs it against a minimal synthetic
fixture, `scripts/generated_data/tests/fixtures/character_refs/
mini_characters_sibling_enum.h`, that reproduces the real header's
primary-enum/named-sibling-enum, same-prefix collision shape in
miniature (down to a negative-valued sibling member), proving the
scoping mechanism itself -- anchored on the primary block's own opening/
closing braces -- independent of the real header's current contents.
Each of the four other affected tables (`units`, `supports`,
`chapterbundle`, plus `eventlists` as an additional table found to share
the same bug class) additionally carries its own `char_evt_sentinels.json`
fixture (proving its own real-header call site rejects all four EVT_*
sentinels) and its own synthetic-sibling-enum-collision regression test
wired through that table's own `characters_header=` `validate()`
parameter override -- so the fix is proven at the wiring level in each
table, not only inside the shared reader.

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

# chapterbundle (metadata-only, like eventscripts) cross-validates against
# units/shops/traps/eventscripts/eventlists/supports; without --dep-source
# each falls back to its own real, committed default_source, so validating
# the real bundle needs no extra flags at all:
python3 -m scripts.generated_data validate --table chapterbundle
python3 -m scripts.generated_data generate  --table chapterbundle
python3 -m scripts.generated_data check     --table chapterbundle
```

Common flags: `--source PATH` (JSON source, default per-table, e.g.
`src/data/supports.json`/`src/data/ch2_units.json`/
`src/data/ch2_shops.json`/`src/data/ch2_traps.json`/
`src/data/ch2_eventscripts.json`/`src/data/ch2_eventlists.json`/
`src/data/ch2_bundle.json`), `--hand-source PATH` (round-trip comparison
target, default per-table, e.g. `src/data_supports.c`/
`src/events_udefs.c`/`src/events_shoplist.c`/`src/events_trapdata.c`/
`src/events/ch2-eventinfo.h`; `eventscripts`/`chapterbundle` have none --
both are metadata-only), `--no-roundtrip` (skip it), `--out-dir DIR`
(generated C output directory, default `build/generated/data`),
`--inventory PATH` (committed inventory/summary path, default per-table
under `reports/`), `--dep-source NAME=PATH` (repeatable; overrides one
declared table dependency's JSON source -- only meaningful for schemas
that declare `dependency_tables()`, currently `eventlists` and
`chapterbundle`).

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

Still never wired into `all` (this work never links generated C or
replaces any hand-written `src/` file), but as of Batch C,
`generated-data-check` **is** wired into
`.github/workflows/build.yml`, once per job, as an actionable pre-flight
gate ahead of the modern debug/release linker checks -- a stale/
inconsistent generated-data source now fails CI fast, with
`file:line:column` diagnostics, instead of only being caught (or masked)
by a much slower linker failure. `GENERATED_DATA_TABLES` now lists all 9
registered tables (Issue #5 Batch 1 added `items`, then `classes`), so
the loop-based targets below cover `supports units shops traps items
classes eventscripts eventlists chapterbundle`. Neither `items` nor
`classes` is added to `GENERATED_DATA_CH2_TABLES` -- both are global
tables, not part of the Chapter 2 bundle, so `generated-data-ch2-check`
is unaffected:

```bash
make generated-data-validate      # validate only, all tables
make generated-data-generate      # validate + write build/ output + inventory, all tables
make generated-data-check         # CI-suitable: fails on drift, from a clean tree, all tables (wired into CI)
make generated-data-test          # python3 -m unittest discover -s scripts/generated_data/tests

# Batch C convenience aliases:
make generated-data-ch2-check     # check just the tables the Ch2 bundle composes + the bundle itself
make generated-data-bundle-validate  # validate just the chapterbundle table (fast iteration)
make generated-data-bundle-check     # check just the chapterbundle table (fast iteration)
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

`test_chapterbundle_schema.py` covers, for Batch C's `chapterbundle`
table (reusing the "EL" fixture family already established by
`tests/fixtures/eventlists/`, plus a small `deps_supports.json` and tiny
fixture `chapters.h`/`chapter_settings.json`/`data_8B363C.c` stand-ins):
mismatched chapter index/internalName/mapEventDataId, a `mapEventDataId`
resolving to the wrong manifest symbol, an unknown chapter id, a required
support owner missing from `supportOwners.required`, a required support
owner with no `SupportData` record of its own, a required support
owner's un-reciprocated partner, an orphan unit/shop/trap/event-script
record (declared but unreachable and uncited), undeclared/duplicate
character/class/item dependencies, a stale bundle manifest (both a real
record missing from `tables.*.symbols` and a declared symbol that isn't a
real record), the full multi-table dependency graph's acyclic/
deterministic topological order plus a synthetic injected-cycle
detection test, and an end-to-end test loading all 6 real dependency
tables + the real `chapter_settings.json` and validating the exact
committed `src/data/ch2_bundle.json` with zero diagnostics.
`test_cli_new_tables.py`'s `CliChapterBundleTests` covers the same
table's `validate`/`generate`/`check` CLI subcommands (including the
metadata-only C-output skip message and committed-inventory drift
detection) against both a fixture copy and the real committed bundle.

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

Additionally covers, for Issue #5 Batch 1 (`test_items_schema.py`,
`test_items_generate.py`, `test_items_roundtrip.py`, the two new generic
`validators.py` helper tests -- `ExtractDefineConstantTests`,
`ResolveBitmaskFlagsTests` -- and the `CliItemsTests` cases in
`test_cli_new_tables.py`):

* `items`: duplicate item entries, a gap in `ITEM_*` coverage (validated
  against small fixture headers -- `mini_items.h`/`mini_bmitem.h`/
  `mini_variables.h`/`mini_msg.h`/`mini_item_icon.c` -- so the mandatory
  full-coverage check can be exercised in isolation, without every test
  also having to author all 206 real records), undefined item/weapon-
  type/weapon-exp-threshold/weapon-effect references, undefined and
  duplicate `IA_*` attribute flags, out-of-range text IDs/range nibbles/
  icon IDs/`useEffect`, undeclared `statBonuses`/`effectiveness` C-symbol
  references, header-derived constant reads (the scoped `IA_*` block
  excluding the `// Helpers` combos, live `MSG_COUNT`, live item-icon
  count), deterministic C89 generation (default-field omission, ascending
  attribute-flag ordering regardless of JSON order), fixture-based
  round-trip match/mismatch/missing/extra-item detection, and the full
  **206/206** hand-table round trip against the real `src/data_items.c`
  with zero diagnostics. CLI tests cover `validate`/`generate`/`check`
  against both an invalid fixture and the real committed
  `src/data/items.json` (drift-free). The generated
  `build/generated/data/data_items.c` was also compiled end-to-end
  through the real `cpp | iconv | agbcc` pipeline (mirroring the
  Makefile's `$(C_OBJECTS)` recipe) and assembled with `arm-none-eabi-as`
  with zero errors/warnings, confirming it is valid, compilable C89 --
  it is still never linked in place of `src/data_items.c` (out of Batch 1
  scope).

Additionally covers, for Issue #5 Batch 1's `classes` table
(`test_classes_schema.py`, `test_classes_generate.py`,
`test_classes_roundtrip.py`, and the `CliClassesTests` cases in
`test_cli_new_tables.py`):

* `classes`: duplicate class entries, a gap in `CLASS_*` coverage
  (validated against small fixture headers -- `mini_classes.h`/
  `mini_bmunit.h`/`mini_bmitem.h`/`mini_ekrbattle.h`/`mini_variables.h`/
  `mini_msg.h`/`mini_portrait_data.c`/`mini_sms_data.c` -- so the
  mandatory full-coverage check can be exercised in isolation), undefined
  class/promotion/weapon-type/weapon-exp-threshold references, undefined
  and duplicate `CA_*` attribute flags, out-of-range text IDs/SMS IDs/
  portrait IDs/base-stat (`s8`) and promotion-gain (`u8`) values,
  undeclared `battleAnim`/`movCostTable`/terrain-lookup/
  `reservedTerrainTable` C-symbol references, an incorrect
  `movCostTable` entry count, header-derived constant reads (the scoped
  `CA_*` block excluding the `// Helpers` combos, live `MSG_COUNT`, live
  portrait/SMS counts, live `baseRanks`/`pMovCostTable` array
  capacities), deterministic C89 generation (default-field omission,
  ascending attribute-flag/base-rank ordering regardless of JSON order,
  the literal `(void *)0x00000000` null-pointer cast for immobile
  classes' `movCostTable` entries), fixture-based round-trip match/
  mismatch/missing/extra-class detection, and the full **127/127**
  hand-table round trip against the real `src/data_classes.c` with zero
  diagnostics. CLI tests cover `validate`/`generate`/`check` against both
  an invalid fixture and the real committed `src/data/classes.json`
  (drift-free). The generated `build/generated/data/data_classes.c` was
  also compiled end-to-end through the real `cpp | iconv | agbcc`
  pipeline and assembled with `arm-none-eabi-as` with zero errors,
  confirming it is valid, compilable C89 -- it is still never linked in
  place of `src/data_classes.c` (out of Batch 1 scope).

All fixtures and scratch directories live under
`scripts/generated_data/tests/` (never `/tmp`).

## Remaining Issue #5 scope (explicitly not done here)

Batch A + Batch B + Batch C together are scoped to the Chapter 2
vertical slice's pure-data tables, its event-list/manifest composition
layer, and now (Batch C) proving that whole slice is internally coherent
as one bundle against the chapter-settings row that actually selects it
at runtime -- but this still does **not** close Issue #5. Batch C closes
the gap Batch A/B left open -- `src/data/ch2_bundle.json` (`chapterbundle`
table, this doc's `## chapterbundle schema` section) now proves the
`chapter_settings.json`/`gChapterDataAssetTable` wiring, every
`units`/`shops`/`traps`/`eventscripts` record's reachability, required
support-owner existence/reciprocity, and the referenced character/class/
item dependency sets are all internally consistent as a single bundle,
CI-gated via `generated-data-check` (now wired into
`.github/workflows/build.yml`). Still open, and **explicitly out of
scope** for this Batch C update:

* **Global character authoring/generation/linking.** `items` and
  `classes` (Issue #5 Batch 1) are global tables with their own schema/
  generate/round-trip pipelines. `characters` (Issue #5 **Batch 2a**,
  this doc's `## characters schema` section) now has an analogous
  schema/dependency-validation foundation -- the 256-slot symbolic/raw
  designator model, full field/range/reference validation, and
  cross-table checks against `classes`/`supports` -- but still only as
  a schema, reachable via explicit `validate --table characters
  --source ...`; there is no committed `src/data/characters.json`, no
  `generate.py`/`parser.py`/`inventory.py`, and it is not wired into
  `GENERATED_DATA_TABLES`/CI. `chapterbundle` still only *references*
  `CHARACTER_*`/`CLASS_*` IDs (as a reference-only dependency set,
  cross-checked for existence against the live enum headers) -- it does
  not fold `characters`/`classes` into its own cross-table reachability
  checks. Remaining: **Batch 2b** (transcribe all 256 vanilla records
  into a real, complete `src/data/characters.json` with `fullCoverage`
  enabled, plus `generate.py`/`parser.py`/round-trip proof) and
  **Batch 2c** (link the generated table in place of
  `src/data_characters.c`, and any resulting `chapterbundle`
  cross-table wiring) remain open.
* **Linking the `items`/`classes` tables themselves.** Batch 1 is
  schema/generate/round-trip only, per its own explicit scope --
  `build/generated/data/data_items.c` and `build/generated/data/
  data_classes.c` both compile (and, for `classes`, assemble) cleanly
  through the real agbcc pipeline but are never linked in place of
  `src/data_items.c`/`src/data_classes.c`; that link-order migration
  (the `ldscript.txt` `src/x.o(.text)`-before-`asm/x.o(.text)` swap
  described in `CONTRIBUTING.md`, plus folding `items`/`classes` into
  `chapterbundle`-style cross-table reachability checks, if ever
  appropriate for a global table) remains **explicit Batch 2 scope**,
  not started here.
* **Mechanics** (combat/growth/AI/etc. formulas and their own data
  tables) are entirely untouched by Batches A/B/C or Issue #5 Batch 1.
* **Additional chapters.** This whole platform -- schemas, the
  `chapterbundle` composition pattern, the CLI, the Make targets, CI
  wiring -- covers Chapter 2 only (`items`/`classes` are the exceptions,
  being global by nature); every other chapter's equivalent tables/bundle
  remain to be modeled from scratch.
* **Actually linking any generated table** in place of its hand-written
  counterpart (requires the `ldscript.txt` link-order migration described
  in `CONTRIBUTING.md`, and is out of scope until a table is fully
  proven) -- Batch C does not link or generate any C output either (it's
  metadata-only, like `eventscripts`), and neither does Issue #5 Batch 1's
  `items`/`classes` tables.
* **Migrating this pattern to other repository data domains** beyond the
  Chapter 2 slice and the `items`/`classes` global tables this Issue has
  scoped so far.
