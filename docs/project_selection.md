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
Archival targets, direct archival objects, mixed modern/archival requests and
`apcs-gnu` are rejected. The default project's explicit archival lane is
unchanged.

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

### Full-game localization inputs

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

## Outputs, identity and compatibility

With the default outer root, selected outputs live beneath:

```text
build/projects/PROJECT/PROFILE/CONFIG/ABI/
```

This includes data, text/header, UI/game catalogs, asset generation/discovery,
objects, metadata, ELF, map and ROM outputs. A custom
`MODERN_BUILD_ROOT=build/custom` changes the outer root to
`build/custom/projects/PROJECT/PROFILE/CONFIG/ABI/`; it does not remove any
selection dimension. Do not override derived output-directory variables.
Custom project rules must likewise use `FE8_PROJECT_OUTPUT_DIR` for
project-sensitive generated output. `expansion-modern-clean` removes only the
selected project/profile root. Switching projects does not require cleaning.

Nondefault project/profile selection participates in the existing diagnostic
configuration fingerprint and metadata JSON. The default JSON shape and
fingerprint inputs omit those extra fields and remain unchanged. The selector
adds no runtime branch, persistent field or save envelope. Runtime ROM/RAM
cost comes from the chosen content; existing typed and linker budgets remain
required.

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
projects, modern debug/release/AAPCS, English and en/fr source-generation probes.

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

4. Observe negative controls: unknown projects/profiles, missing or competing
   providers, inherited replacement inputs, contradictory profiles, invalid
   item message symbols and archival/mixed/non-AAPCS requests fail. Invalid
   item regeneration preserves the previous generated output. Normalized
   equivalent locale/epoch overrides succeed.

5. Confirm selected data/UI/game/asset outputs retain all four namespace
   dimensions with a custom outer root, committed framework source/report
   content is unchanged, and selected ACTIVE counts do not leak into a later
   default invocation. Keep the tests' actual results with candidate evidence.

The first command covers actual configure/Make/generated/compiled/native
behavior; the second covers generated-data validation, report isolation and
invocation-local counts. Missing ARM tools make the ARM-specific probes
unavailable, not passed. There is no subjective manual criterion in this build
case. It does not establish a playable Grado campaign, full translated corpus,
glyph qualification, physical ROM/RAM fit for that game, or M0A/M0B/release
acceptance.
