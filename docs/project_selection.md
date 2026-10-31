# Explicit project inputs and build profiles

Project selection is a reusable build capability. It does not install a game,
copy the engine, synchronize an external repository, or qualify its content.
An unconfigured build remains `default/default`: modern GCC/AAPCS, English,
16 MiB, and the existing framework output paths.

## Select a reviewed project

A bundled project supplies `examples/NAME/project.mk`. Names and profiles
match `[A-Za-z0-9][A-Za-z0-9_-]*`. Run from the framework root:

```sh
make FE8_PROJECT=NAME FE8_PROJECT_PROFILE=primary
./configure --with-project=NAME --with-project-profile=primary
make
```

These illustrative commands require an existing declaration; an unknown name
is an error, not a fallback. Omitting the profile chooses that project's
declared default. `make FE8_PROJECT=default` selects the framework again,
including from a configured build directory. A persisted profile applies only
to its original project. Explicit one-off Make settings still take precedence,
but cannot contradict a profile's required settings. Other explicitly
configured feature options remain configured when the project changes.

Nondefault projects support modern `debug` and `release` with `aapcs`.
Archival targets, archival-only objects, mixed modern/archival requests and
`apcs-gnu` are rejected. Shared assembly/MIDI prerequisites remain available
to modern link preparation; they are not classified as an archival game build.
The default project's explicit archival lane is unchanged.

## Declaration

For example, a project that replaces its character data and event-code module
can declare:

```make
FE8_PROJECT_PROFILES := primary small
FE8_PROJECT_DEFAULT_PROFILE := primary
FE8_PROJECT_REQUIRED_SETTINGS := MODERN_ROM_SIZE EXPANSION_ROM_TITLE
FE8_PROJECT_primary_MODERN_ROM_SIZE := 32M
FE8_PROJECT_primary_EXPANSION_ROM_TITLE := MYPROJECT
FE8_PROJECT_small_MODERN_ROM_SIZE := 16M
FE8_PROJECT_small_EXPANSION_ROM_TITLE := MYPROJECT

FE8_PROJECT_REPLACE := characters code
FE8_PROJECT_INHERIT := $(filter-out $(FE8_PROJECT_REPLACE),$(FE8_PROJECT_MODULES))
FE8_PROJECT_SOURCE_characters := examples/NAME/characters.json
FE8_PROJECT_CODE_REPLACEMENTS := src/eventscr.c=examples/NAME/events.c
```

The paths in this example must be supplied by the project. Declarations are
ordinary reviewed Make code, not an untrusted plugin or an operating-system
sandbox. Paths are framework-root-relative Make words, without whitespace or
`=`. C replacements and additional C sources belong under the selected
project directory.

Every module in `FE8_PROJECT_MODULES` has exactly one provider: `inherit` or
`replace`. The filter expression above explicitly inherits everything not
replaced. Unknown, omitted, duplicate or overlapping providers fail. A
replacement must exist; a source supplied for an inherited module fails.
There is no filename-shadowing search path.

The supported required settings are `MODERN_ROM_SIZE`, `EXPANSION_ROM_TITLE`,
`EXPANSION_ENABLED_LOCALES`, `EXPANSION_DEFAULT_LOCALE` and
`EXPANSION_SAVE_COMPAT_EPOCH`. Each listed setting needs a
`FE8_PROJECT_PROFILE_SETTING` value for the selected profile. Locale order and
numeric epoch spelling are normalized before comparison. Unknown settings and
contradictory overrides fail. Unspecified settings use the existing framework
configuration and its existing dependency/conflict checks.
The same normalized requirements apply to standalone data, UI, asset and
game-catalog producers. Clean-only requests do not need a valid content
configuration.

## Input providers

| Module | Replacement declaration and consumer |
| --- | --- |
| Any registered generated-data table | `FE8_PROJECT_SOURCE_TABLE := PATH`; the existing schema, dependency-table validation, generator and logical object slot consume it |
| `code` | `FE8_PROJECT_CODE_REPLACEMENTS := ORIGINAL.c=PROJECT.c ...`; optional `FE8_PROJECT_C_SOURCES := examples/NAME/extra.c ...` adds ordinary C modules |
| `game-text` | `FE8_PROJECT_TEXT_MAIN` and `FE8_PROJECT_TEXT_DEFS`; existing text processing generates a private message C file and `constants/msg.h` |
| `ui-text` | `FE8_PROJECT_UI_REGISTRY` and `FE8_PROJECT_UI_CATALOGS := en=PATH ja=PATH ...`; existing UI generation, presentation data and full-game audit consume the selected catalogs |
| `fonts` | `FE8_PROJECT_FONT_REPLACEMENTS := ORIGINAL.c=PROJECT.c ...`; supported original slots are `src/fontgrp-data.c` and `src/data/localized_font_data.c` |
| `assets` | `FE8_PROJECT_SOURCE_assets := PATH`; the existing asset manifest and adapters own the derivatives and existing engine consumers |

Generated-data table names come from the existing schema registry. Replacement
tables still satisfy its types, ranges, references, capacities and compilation
contracts. An intentional replacement is not compared with vanilla hand C.
Inherited tables retain that comparison. Inventories, manifests and ACTIVE
ID-space outputs for a selected project are build-local; generation does not
rewrite the framework's committed reports. A complete replacement `items`
source supplies its own expanded records instead of implicitly appending the
framework's starter overlay.
When replacing a table used by a chapter bundle, select matching bundle
bindings too. Contradictory embedded paths are rejected rather than silently
validating stock records; directory-backed objective/strategy providers must
contain their declared owner members. Unselected per-bundle sources remain
authoritative.
The optional `TableSchema.validate_source_bindings(records, diagnostics,
inputs)` hook runs for the selected table and its loaded dependency tables
before content validation. It does not replace embedded owners with registry
defaults or enable the test-only supplied-dependency route. Numeric item
loading needs no message header; symbolic IDs load their selected header
once per source-file load without a shared cache.

C replacements keep the original logical object path and linker position.
Generated whole-table C and `src/msg_data.c` are selected through their data
and text providers, not through the C-replacement list. Existing partial-table
guards still apply to event-code replacements. Additional or replacement
modules must provide the intended symbols exactly once; normal compilation
and linking remain authoritative.

The selected gameplay header is used both by C compilation and by item,
character and class validation. Item symbols resolve against that same
header, including during loading. UI presentation IDs resolve against the
selected UI registry, not a separate stock catalog.
Selected text production follows the actual parser's transitive includes,
including non-`.txt` files. Its content-stable writer preserves C/header
modification times on an unchanged run; no second include scanner is used.
Objective bounds and their dependency discovery use the effective selected
asset manifest and its TMX sources rather than stock map dimensions.
The existing generated-data and ACTIVE CLI interfaces accept
`--asset-manifest PATH`; `SchemaInputs.asset_manifest` keeps that binding
invocation-local and does not change cached default schemas.

### Full-game localization inputs

GNU Make dry-run namespace inspection does not require the ARM compiler:
`-n` avoids remaking compiler-generated header prescans or reading an
unproduced packed-animation script. Real builds still perform both steps,
including their existing `NODEP=1` bootstrap behavior.

The existing full-game CLI remains the consumer. Its file options can be
selected independently as `game-OPTION` modules:

`mapping`, `width-registry`, `fixed-width-aliases`, `eu-mapping`, `ja-indexed`,
`ja-raw`, `zh-indexed`, `zh-raw`, `latin-span-review`,
`unicode-script-review`, and `raw-closure`.

For example:

```make
FE8_PROJECT_SOURCE_game-mapping := examples/NAME/mapping.json
FE8_PROJECT_SOURCE_game-authored-ja := examples/NAME/catalog.ja.json
```

Both names must also appear in `FE8_PROJECT_REPLACE`. Authored catalog modules
are `game-authored-LOCALE` for `ja`, `zh-Hans`, `fr`, `de`, `es` and `it`;
European indexed modules are `game-indexed-LOCALE` for `fr`, `de`, `es` and
`it`. Other sources remain explicitly inherited. This also supports a project
that retains the campaign but replaces a translation.

The engine/tools/schema root never moves to the mapping or example directory.
The `game-text` provider supplies the English source and generated target
header to localized generation. The `ui-text` provider also supplies its
actual catalog paths to the leakage audit. Selected file paths do not waive
any existing validation. Generalizing semantic message-set qualification is
the separate contract in [issue #314](https://github.com/laqieer/fireemblem8-expansion/issues/314);
these input bindings are not a non-stock message-set or game qualification.
An EU override does not disable inherited CJK shard/catalog validation.
Explicit CJK replacements are independent: each retained JA/ZH catalog still
receives its existing deterministic-merge check.

## Outputs, identity and compatibility

With the default outer root, selected outputs live beneath:

```text
build/projects/PROJECT/PROFILE/CONFIG/ABI/
```

This includes data, text/header, UI/game catalogs, asset generation/discovery,
battle-animation objects/sidecars, objects, metadata, ELF, map and ROM outputs.
The selected battle-animation object retains its pinned ROM address and
depends on the actual selected script and existing compile-settings stamp,
so changing asset selections cannot reuse another selection's object.
Its `banim/project_data_banim.o` basename distinguishes packed data from the
unrelated floating `src/data/banim/data_banim.o`. Packing retains the existing
common lock for shared stock intermediates as well as the private output lock.
The producer creates its own output parent. The existing base/pin audit checks
both default and selected selectors, including a mismatch in either selector.
A custom `MODERN_BUILD_ROOT=build/custom` changes the outer root to
`build/custom/projects/PROJECT/PROFILE/CONFIG/ABI/`; it does not remove any
selection dimension. Do not override derived output-directory variables.
Custom project rules must likewise use `FE8_PROJECT_OUTPUT_DIR` for
project-sensitive generated output. `expansion-modern-clean` removes only the
selected project/profile root. Switching projects does not require cleaning.
Recursive runtime/budget profiles use the same path calculation for producer
and readback. Their selected auxiliary roots stay under
`MODERN_BUILD_ROOT/profiles/VARIANT/` with project/profile/config/ABI-qualified
artifacts, instead of escaping to sibling directories or shared default trees.
Compiler/binutils/library input directories remain supported inputs; derived
modern/asset output directories are not override surfaces.

Default leakage checks retain the full committed JA/ZH audit locale set even
when catalog generation requests a subset. Selected leakage checks run the
existing fail-closed audit once and write their current build-local report;
they do not compare a generated report with itself or rewrite the committed
framework report.

Nondefault project/profile selection participates in the existing diagnostic
configuration fingerprint and metadata JSON. The default JSON shape and
fingerprint inputs omit those extra fields and remain unchanged. The selector
adds no runtime branch, persistent field or save envelope. Runtime ROM/RAM
cost comes from the chosen content; existing typed and linker budgets remain
required.

The public resolver carries project/profile as invocation context. The core
`ExpansionIdentity` positional constructor remains unchanged; only selected
metadata and fingerprint inputs include that context.

The current review-family `project-selection` action, lifecycle, wire, generated and resource slices
exercise the real declaration, identity and metadata CLI against the parsed
schema-owner, setting and provider domain. They cover default/selected entry,
default preservation, fresh profile resets and failed/recovered publication.
Wire checks consume actual serialized Make arguments, all registered schema
contexts, malformed source selectors and replayed independent invocations.
The item loader reads a real selected message header twice after its contents
change, detecting stale or ignored bindings rather than checking only an
attribute.
Generated checks consume the actual Make-serialized providers and private
four-dimensional namespace for all sixteen schemas. They exercise selected
owner rejection, complete generation and no-op publication, all fourteen
generated-C ARM object symbol layouts against equivalent inherited inputs,
both metadata-only downstream consumers, and the existing build-local healing
policy without modifying committed inventories. Actual ACTIVE headers are
produced for item compilation; no decoder, compiler or source stub is used.
Their execution-only closure contains production Python plus the declared
configuration/font slots; generated roles additionally stage the parsed
event/schema/header/asset owners and three real capacity-source files, not
candidate tests or an indiscriminate engine snapshot.
Resource roles compile the existing embedded metadata translation unit with
actual Make-resolved flags for default and selected debug/release AAPCS
contexts. The normal ROM metadata verifier consumes the real record bytes;
sized public symbols and allocated sections retain the fixed readonly
record with no mutable-memory contribution. Compiler-resolved binutils,
not an extra backend, extracts the object bytes. These bounded object
observations are not full linked-ROM, overall ROM/RAM or supported-profile
runtime qualification and do not authorize the whole selector handoff or
replace any final gate.

Project names and fingerprints are not save-compatibility authorities.
Projects must explicitly select an appropriate compatibility epoch when their
save meanings differ; selection does not invent a migration or implicitly
import another game's saves. See [configuration identity](config_identity.md)
and [save compatibility](save_format.md).

Dependencies are the existing Make/Autoconf, modern compiler, generator,
localization and asset interfaces. Conflicts are unsupported lanes/ABIs,
profile contradictions and inconsistent providers. There is no new runtime
feature flag or generic human-review requirement. A regression is fixed
forward or reverted through the normal candidate/master gates.

## TC-PROJECT-SELECTION-001

**Issue:** [#313](https://github.com/laqieer/fireemblem8-expansion/issues/313).
**Profiles:** unconfigured `default/default`, temporary primary/alternate
projects, modern debug/release/AAPCS, English/en-fr source generation and
inherited JA/ZH with an explicit EU input.

**Prerequisites and reset:** use a checkout with the modern quickstart tools,
Python 3, GNU Make, host `cc`, ARM GCC/binutils and Git. Preserve existing
work. Do not run `make clean`, edit a stock message header, or supply a ROM.
The tests create uniquely named temporary projects under `examples/`, use an
alternate temporary Git index for asset fixtures, and remove their own
project outputs and fixtures. They do not modify the contributor's index.

1. From the framework root, run:

   ```sh
   python3 -m unittest scripts.modernize.tests.test_expansion_config.ProjectSelectionIdentityTests -v
   python3 -m unittest scripts.generated_data.tests.test_cli scripts.generated_data.tests.test_idspace_active.ActiveContractModelTests -v
   python3 -m unittest scripts.modernize.tests.test_expansion_config.ProjectSelectionRomTests -v
   ```

2. Observe the direct and persistent default/project/default sequence without
   cleaning: parsed Eirika base HP is **0, 23, 0**, and the default generated
   table returns byte-identically. The configured alternate profile selects
   16 MiB; changing back to default does not retain its profile name.
   The primary profile selects 32 MiB and its own title.

3. Observe real output consumption: ARM-compiled and relocatably linked code
   occupies its original logical slot; selected font bytes reach its ARM data
   object; a new gameplay ID is generated and consumed by compilation and
   item validation; selected UI presentation resolves through a linked native
   catalog descriptor; selected French text decodes through the actual native
   codec; the selected TMX derivative starts with metatile word **8**, not
   the default **4160**.
   The separate inherited-content debug/32 MiB ROM probe links and verifies
   real emulator boot checkpoints. Its linker map places the private
   battle-animation object at `0x08C02000`, not a shared default object.

4. Observe negative controls: unknown projects/profiles, missing or competing
   providers, inherited replacement inputs, contradictory profiles, invalid
   item message symbols and archival/mixed/non-AAPCS requests fail. Invalid
   item regeneration preserves the previous generated output. Normalized
   equivalent locale/epoch overrides succeed.
   Standalone data validation and manifest generation build and refresh the
   selected header. Numeric-only item loading needs no message-header file.
   Nested bundle/objective/strategy validation rejects conflicting providers
   and accepts coherent bindings.
   Editing only a transitive `story.inc` grows the actual selected message
   count from **2 to 3**; a later no-op preserves both C/header bytes and
   modification times. Unsupported required settings reject in the declaration
   frontend, and all four standalone producer families reject contradictory
   effective locales.

5. Confirm selected data/UI/game/asset outputs retain all four namespace
   dimensions with a custom outer root, committed framework source/report
   content is unchanged, and selected ACTIVE counts do not leak into a later
   default invocation. Keep the tests' actual results with candidate evidence.
   Recursive runtime/budget consumers must name the same private namespace as
   their producers; derived directory overrides reject while tool inputs remain
   allowed. Resizing the selected map from **15x15 to 1x1** rejects out-of-map
   objective areas and preserves previous generated C. Corrupting an inherited
   JA or ZH catalog rejects even with another locale replaced; a stale unused
   default JA catalog does not block its valid explicit replacement.
   A fresh selected leakage check creates its own zero-unapproved report
   without changing the stock report. Every supported packed-animation selector
   rejects a mismatched Make base; the real ROM map separately keeps unrelated
   animation data floating and packed data pinned.

The first command covers actual configure/Make/generated/compiled/native
behavior; the second covers generated-data validation, report isolation and
invocation-local counts; the third covers one selected ROM/link/boot path.
Missing ARM tools make the ARM-specific probes unavailable, not passed.
There is no subjective manual criterion in this build
case. It does not establish a playable Grado campaign, full translated corpus,
glyph qualification, physical ROM/RAM fit for that game, or M0A/M0B/release
acceptance.
