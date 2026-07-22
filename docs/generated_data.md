# Generated-data platform (Issue #5 Slice 0 canary)

## Status

**This is a canary, not a completed migration.** It proves out the
deterministic schema/validation/generation/build/drift plumbing on the
smallest complete gameplay table (`SupportData`), end to end, without
replacing or linking any hand-written table yet. `src/data_supports.c`
remains the canonical, linked source of truth for `gSupportData`-family
symbols. Issue #5 itself is **not closed** by this slice.

The next step (not part of this slice) is the required Chapter 2
all-entity vertical slice: applying this same platform to every entity
table referenced by a single chapter (units, AI, event scripts, etc.),
which will exercise multi-table dependencies and at least one real
consumer of the C-symbol escape hatch described below.

## Source vs. generated vs. committed-public artifacts

| Artifact | Path | Committed? | Linked into the ROM? |
|---|---|---|---|
| Structured source (hand-edited) | `src/data/supports.json` | Yes | No (not compiled directly) |
| Hand-written canonical C (existing) | `src/data_supports.c` | Yes | **Yes** -- unchanged, still canonical |
| Generated C89 (bulky) | `build/generated/data/data_supports.c` | **No** (gitignored, `build/`) | No -- not referenced by `ldscript.txt`/Makefile |
| Committed inventory/summary | `reports/generated_data_supports_inventory.md` | Yes (small) | N/A |

Because the generated C is not linked, this slice cannot introduce
`multiple definition` link errors and cannot silently change gameplay
behavior -- the only committed, gameplay-visible source of `SupportData`
is still `src/data_supports.c`. The JSON source and the generated output
are validated to be *semantically identical* to it (see "Round-trip
checker" below) so that a future slice can switch the link order with
confidence.

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
  registry.py         registers every table schema (import this to dispatch)
  cli.py              validate / generate / check subcommands
  supports/
    schema.py          SupportRecord, load_records(), validate()
    generate.py         C89 emission for SupportData
    parser.py           round-trip parser for src/data_supports.c + comparer
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
`registry.py` is the single place every table registers itself (currently
just `supports` v1); the CLI resolves `--table supports` through this
registry instead of hardcoding imports, so adding a table later (the
Chapter 2 slice) doesn't touch dispatch logic.

`schema.DependencyGraph` records which headers/tables a schema depends on
(`supports` depends on `constants/characters.h` and
`UNIT_SUPPORT_MAX_COUNT` from `types.h`). It provides a deterministic
topological order (Kahn's algorithm with alphabetical tie-breaking, for
future multi-table generation ordering) and a stable sha256 digest that
is embedded in the committed inventory report -- so a change in a table's
dependencies is visible as a one-line diff, not silent.

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

## The C-symbol escape hatch (`escape_hatch.py`)

Not used by `SupportData` (no callback fields), but implemented and
tested here as a generic, reusable mechanism for later tables that do
need one (e.g. AI script hooks, event callbacks in the Chapter 2 slice):

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
`tests/fixtures/escape_hatch/demo_header.h` for the fixture-driven proof
(valid function reference, valid extern-object reference, malformed
identifier, undeclared symbol).

## CLI

```bash
python3 -m scripts.generated_data validate --table supports
python3 -m scripts.generated_data generate  --table supports
python3 -m scripts.generated_data check     --table supports
```

Common flags: `--source PATH` (JSON source, default
`src/data/supports.json`), `--hand-source PATH` (round-trip comparison
target, default `src/data_supports.c`), `--no-roundtrip` (skip it),
`--out-dir DIR` (generated C output directory, default
`build/generated/data`), `--inventory PATH` (committed inventory/summary
path, default `reports/generated_data_supports_inventory.md`).

* `validate` -- load + validate; prints **every** diagnostic (not just the
  first), each with `file:line:column`.
* `generate` -- validate, then write-if-changed the generated C89 and the
  committed inventory. Fails (writing nothing) if validation or the
  round-trip check fails.
* `check` -- the CI-suitable gate. Self-heals the ephemeral `build/`
  output (write-if-changed, since it's gitignored and there is nothing
  committed to have drifted against), but only *compares* the committed
  inventory report -- it never rewrites it. Fails with a `DRIFT:` message
  if the committed inventory is stale relative to the current JSON
  source, in addition to failing on any validation/round-trip error.

## Make targets (`generated_data.mk`)

Standalone, not wired into `all` or `.github/workflows/build.yml` (which
is scoped to target-ROM linker/boot checks per repository policy) --
deliberately isolated so this canary can't destabilize the existing CI
gate:

```bash
make generated-data-validate   # validate only
make generated-data-generate   # validate + write build/ output + inventory
make generated-data-check      # CI-suitable: fails on drift, from a clean tree
make generated-data-test       # python3 -m unittest discover -s scripts/generated_data/tests
```

## Testing

```bash
python3 -m unittest discover -s scripts/generated_data/tests -v
```

Covers: valid generation, deterministic/repeatable generation,
write-if-changed semantics, duplicate owner/partner/symbol detection,
missing character references, self-references, out-of-range values,
parallel-array/count mismatches, fixed-capacity overflow, reciprocal
mismatch and reciprocal-missing detection, `file:line:column` source
locations, the escape-hatch mechanism (valid/malformed/undeclared
symbols), the full 33-record hand-table round trip (plus injected
mismatch detection), and CLI-level drift detection (including against
the real committed `src/data/supports.json` and inventory report). All
fixtures and scratch directories live under `scripts/generated_data/tests/`
(never `/tmp`).

## Remaining Issue #5 scope (explicitly not done here)

This slice is scoped to a single small table and does not close Issue #5.
Still open, for later slices:

* Migrating additional gameplay tables beyond `SupportData`.
* The Chapter 2 all-entity vertical slice (units, AI, event scripts, map
  data for one full chapter) -- the next concrete milestone, which will
  exercise multi-table dependency ordering and at least one real
  escape-hatch consumer.
* Actually linking any generated table in place of its hand-written
  counterpart (requires the `ldscript.txt` link-order migration described
  in `CONTRIBUTING.md`, and is out of scope until a table is fully
  proven).
* Wiring `generated-data-check` into `.github/workflows/build.yml` (left
  as a standalone target for now, per repository policy of keeping that
  workflow scoped to target-ROM linker/boot checks).
