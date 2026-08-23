# Optional gameplay feature cases

These procedures cover the shipped, default-off gameplay modules from issues
[#6](https://github.com/laqieer/fireemblem8-expansion/issues/6),
[#34](https://github.com/laqieer/fireemblem8-expansion/issues/34), and
[#42](https://github.com/laqieer/fireemblem8-expansion/issues/42). They use
the supported modern AAPCS source builds only. A case that uses a host driver
does so because the framework deliberately ships no campaign-specific event,
unit, spell, or test chapter to expose that public seam.

## TC-GAMEPLAY-001: Mechanics registry applies ordered, bounded hooks

- **Feature / originating issue:** `mechanics-hook-registry` /
  [#6](https://github.com/laqieer/fireemblem8-expansion/issues/6).
- **Supported configuration or artifact:** modern AAPCS release starter profile:
  `EXPANSION_MECHANICS_HOOKS=1`, `EXPANSION_MECHANICS_SAMPLE=1`; the mapped
  gate also compares it with the flags-off default ROM.
- **Prerequisites and clean starting state:** start at the repository root.
  Remove any generated `config.autotools.mk` overrides or use only the
  command-line profile below.

### Actions

1. Run
   `python3 -m unittest discover -s tools/gba-playtest/tests -p "test_expansion_mechanics.py" -v`.
2. Run `make expansion-modern-starter-hook-check MODERN_CONFIG=release MODERN_ABI=aapcs`.
3. For the dependency negative, run
   `make -n expansion-modern-rom EXPANSION_MECHANICS_HOOKS=0 EXPANSION_MECHANICS_SAMPLE=1`
   and retain the nonzero configuration error.

### Expected result

The host test registers callbacks in append order, reports the distinct
duplicate/length/capacity/reentrancy errors without changing the table, accepts
exactly eight entries, and refuses the ninth. The release gate records one
successful built-in registration, two applies, and a bounded `+1` defence delta in the real
Prologue bout. The invalid sample-without-hooks profile fails before compiling.

### Negative control

The same host suite compiles the flags-off implementation: every public API is
disabled or inert and its diagnostic probe stays all-zero. The release gate
replays the same bout on the default ROM with all hook counters zero and the
same HP transition, so no default battle-stat behavior changes.

### Interactions and save compatibility

The sample and starter content both require this registry; the danger overlay,
casual mode, and AoE reference do not. Registration order is the only hook
ordering contract. This flag changes the diagnostic config fingerprint only:
it adds no save field, layout change, or compatibility-epoch change.

### Automation

- `python3 -m unittest discover -s tools/gba-playtest/tests -p "test_expansion_mechanics.py" -v`
  — `tools/gba-playtest/tests/test_expansion_mechanics.py`.
- `make expansion-modern-starter-hook-check MODERN_CONFIG=release MODERN_ABI=aapcs`
  — `tools/gba-playtest/scenarios/starter-hook-clean-modern-release.json`.
- `python3 -m unittest scripts.modernize.tests.test_expansion_config -v`
  — `scripts/modernize/tests/test_expansion_config.py`.

### Cleanup and limitations

Use `make clean_fast` only if build artifacts must be removed. This is a
battle-stat seam, not a skill/content catalog; it creates no menu, save option,
or campaign mechanic beyond the selected callbacks.

## TC-GAMEPLAY-002: Full-HP Guard grants exactly one bounded defence

- **Feature / originating issue:** `full-hp-guard-sample` /
  [#6](https://github.com/laqieer/fireemblem8-expansion/issues/6).
- **Supported configuration or artifact:** modern AAPCS release starter profile:
  `EXPANSION_MECHANICS_HOOKS=1` and `EXPANSION_MECHANICS_SAMPLE=1`.
- **Prerequisites and clean starting state:** repository root; no save or
  savestate is used by the host check or clean-boot ROM scenario.

### Actions

1. Run
   `python3 -m unittest discover -s tools/gba-playtest/tests -p "test_expansion_mechanics.py" -v`.
   This single discovery run executes both
   `MechanicsRegistryHostTests.test_sample_exact_effect_and_clamp` for the
   enabled sample and
   `MechanicsRegistryHostTests.test_disabled_path_is_inert_and_probe_stays_zero`
   for the disabled path; retain both named assertions in its output.
2. Run `make expansion-modern-starter-hook-check MODERN_CONFIG=release MODERN_ABI=aapcs`.

### Expected result

At full HP the sample adds exactly `+1` to `battleDefense`; below full HP it
adds `+0`; its `99` defence cap prevents an unbounded result. The real Prologue scenario
observes the same `+1` delta during a computed battle, so forecast and combat
share the single `ComputeBattleUnitStats()` seam.

### Negative control

With hooks/sample disabled, the sample is not registered, no defence changes,
and every hook-probe field remains zero. `EXPANSION_MECHANICS_SAMPLE=1` without
hooks is a configuration and C preprocessor error, never an implicit enable.

### Interactions and save compatibility

Starter content may register a separate `+5` avoid callback through the same
registry; it does not alter this sample's defence semantics. No save state,
save layout, migration, or `EXPANSION_SAVE_COMPAT_EPOCH` changes.

### Automation

- `python3 -m unittest discover -s tools/gba-playtest/tests -p "test_expansion_mechanics.py" -v`
  — `tools/gba-playtest/tests/test_expansion_mechanics.py`.
- `make expansion-modern-starter-hook-check MODERN_CONFIG=release MODERN_ABI=aapcs`
  — `tools/gba-playtest/fingerprints/starter-hook-clean-modern-release.json`.

### Cleanup and limitations

The optional sample is intentionally content-free and has no player-facing
toggle. Resetting means rebuilding without the two flags; it is not a
replacement for a downstream ruleset's mechanics.

## TC-GAMEPLAY-003: Threat Range menu toggles and safely returns to the map

- **Feature / originating issue:** `danger-overlay-menu` /
  [#6](https://github.com/laqieer/fireemblem8-expansion/issues/6).
- **Supported configuration or artifact:** modern AAPCS debug or release
  starter QoL profile, `EXPANSION_DANGER_OVERLAY_MENU=1`; the gate compares
  the matching default-disabled ROM.
- **Prerequisites and clean starting state:** repository root with libmGBA
  available for the runtime gate. The scenario starts a new Normal game from a
  clean boot and reaches Prologue player phase without a save fixture.

### Actions

1. Run
   `python3 -m unittest discover -s tools/gba-playtest/tests -p "test_expansion_danger_overlay.py" -v`.
2. Run `make expansion-modern-starter-qol-check MODERN_CONFIG=debug MODERN_ABI=aapcs`.
3. On the enabled ROM, open the map menu at Prologue player phase, select
   **Threat Range**, press `B` to return, and repeat once; move the cursor
   after each return.

### Expected result

The enabled menu has exactly one additional **Threat Range** command. Each
selection displays exactly `39` nonzero danger-range tiles, sets the active
range display, and each `B` cancel clears it and returns control to the
interactive map. The scenario records menu/display and cancel counters
`0 -> 1 -> 2`, range-active `1 -> 0` twice, and successful cursor movement.

### Negative control

The default ROM has no added menu item; its compiled table remains vanilla and
the same clean route leaves every danger-overlay probe field zero. No saved
option bit or automatic fallback enables the command.

### Interactions and save compatibility

The overlay is independent of mechanics, starter content, casual mode, and
AoE. It reuses the vanilla danger-zone path rather than range math or a second
router. It changes only config identity; it writes no save bytes.

### Automation

- `python3 -m unittest discover -s tools/gba-playtest/tests -p "test_expansion_danger_overlay.py" -v`
  — `tools/gba-playtest/tests/test_expansion_danger_overlay.py`.
- `make expansion-modern-starter-qol-check MODERN_CONFIG=debug MODERN_ABI=aapcs`
  — `tools/gba-playtest/scenarios/starter-danger-overlay-modern-debug.json`.
- `make expansion-modern-starter-qol-check MODERN_CONFIG=release MODERN_ABI=aapcs`
  — `tools/gba-playtest/scenarios/starter-danger-overlay-modern-release.json`.

### Cleanup and limitations

Exit the map menu with `B`; no scenario save is created. The visual tile
overlay is automated through semantic range counters, not screenshots; no
additional threat UI, persisted preference, or AI behavior is shipped.

## TC-GAMEPLAY-004: Starter Sample Charm composes generated data and hooks

- **Feature / originating issue:** `starter-generated-content` /
  [#6](https://github.com/laqieer/fireemblem8-expansion/issues/6).
- **Supported configuration or artifact:** modern AAPCS debug content profile:
  `FE8_ITEM_ID_CAP=0xCE`, `EXPANSION_STARTER_CONTENT=1`, and
  `EXPANSION_MECHANICS_HOOKS=1`; the mapped item gate additionally enables the
  sample for its independent control.
- **Prerequisites and clean starting state:** repository root; generated
  content remains under ignored `build/generated/`, never in committed source.

### Actions

1. Run
   `FE8_ITEM_ID_CAP=0xCE EXPANSION_STARTER_CONTENT=1 make generated-data-content-text`,
   then run
   `FE8_ITEM_ID_CAP=0xCE FE8_EXPANSION_ITEMTEST=1 make VPATH=build/generated/data expansion-modern-itemexpansion-check MODERN_CONFIG=debug MODERN_ABI=aapcs EXPANSION_STARTER_CONTENT=1 EXPANSION_MECHANICS_HOOKS=1 EXPANSION_MECHANICS_SAMPLE=1`.
2. Run
   `python3 -m unittest discover -s tools/gba-playtest/tests -p "test_expansion_starter_content.py" -v`.
3. Run each dependency negative and retain its failure:
   `make -n expansion-modern-rom EXPANSION_STARTER_CONTENT=1` and
   `make -n expansion-modern-rom EXPANSION_STARTER_CONTENT=1 EXPANSION_MECHANICS_HOOKS=1 FE8_ITEM_ID_CAP=0xCD`.

### Expected result

The generated record is `ITEM_EXPANSION_CE`, named **Sample Charm**, with icon
`222`, `3` uses, and runtime item halfword `0x03CE`. The debug gate verifies
the inventory/event/UI/name path plus game-save and suspend round trips. Its
bearer gets exactly `+5` avoid, while a deployed control unit with no charm
gets `+0`; Full-HP Guard remains a separate `+1` defence effect.

### Negative control

The content-disabled starter artifact has only the sample registration; it
contains no content callback, name accessor, authored name, or expanded item
table. Missing hooks and a cap below `0xCE` both fail explicitly before a
success-shaped build; an expanded cap without content remains valid.

### Interactions and save compatibility

Content requires mechanics hooks and the active item cap; it does not require
the sample, danger overlay, casual mode, AoE, localization, or debug tools.
The item uses existing item fields and the existing game/suspend serializers:
there is no save layout or epoch change. The feature flag changes only the
diagnostic config fingerprint.

### Automation

- `python3 -m unittest discover -s tools/gba-playtest/tests -p "test_expansion_starter_content.py" -v`
  — `tools/gba-playtest/tests/test_expansion_starter_content.py`.
- `FE8_ITEM_ID_CAP=0xCE EXPANSION_STARTER_CONTENT=1 make generated-data-content-text`
  followed by
  `FE8_ITEM_ID_CAP=0xCE FE8_EXPANSION_ITEMTEST=1 make VPATH=build/generated/data expansion-modern-itemexpansion-check MODERN_CONFIG=debug MODERN_ABI=aapcs EXPANSION_STARTER_CONTENT=1 EXPANSION_MECHANICS_HOOKS=1 EXPANSION_MECHANICS_SAMPLE=1`
  — `tools/gba-playtest/run_item_expansion_checks.py`.
- `make expansion-modern-starter-hook-check MODERN_CONFIG=release MODERN_ABI=aapcs`
  — `tools/gba-playtest/scenarios/starter-hook-clean-modern-release.json` and
  `tools/gba-playtest/fingerprints/starter-hook-clean-modern-release.json`.

### Cleanup and limitations

Run `make clean_fast` if generated build output must be removed. The content
text header is build-local; the explicit generation and `VPATH` above make the
bare generated-header dependency resolvable from a clean checkout. The name is
shown through the typed production name path; description/help text remains
intentionally unset, and this single example is not a content pack.

## TC-GAMEPLAY-005: Casual defeat restores ordinary player defeats only

- **Feature / originating issue:** `casual-defeat-policy` /
  [#34](https://github.com/laqieer/fireemblem8-expansion/issues/34).
- **Supported configuration or artifact:** modern AAPCS profile
  `EXPANSION_CASUAL_MODE=1`, configured with
  `./configure --enable-casual-mode && make`; the default profile is
  `EXPANSION_CASUAL_MODE=0`.
- **Prerequisites and clean starting state:** repository root and a host C
  compiler. The executable host driver constructs only ordinary unit state; no
  campaign-specific death event, save file, or savestate is added.

### Actions

1. Run
   `python3 -m unittest discover -s tools/gba-playtest/tests -p "test_expansion_casual_mode.py" -v`.
2. Run `python3 -m unittest scripts.modernize.tests.test_issue34_35_resolvers.CasualModeContractTests -v`.
3. Run
   `python3 scripts/modernize/expansion_config.py resolve --config debug --abi aapcs --rom-size 16M --casual-mode 0`
   and repeat with `--casual-mode 1`; then run it with `--casual-mode 2` and
   retain the failure.

### Expected result

With casual mode enabled, ordinary blue-unit combat and arena defeats set the
existing marker; chapter-boundary restoration clears that marker and
`US_DEAD` while preserving unrelated state. Unknown defeat kinds, non-player
units, and explicitly unavailable units are not marked. The source contract
also proves scripted-death and hazard paths never call the marker seam, so
their direct `UnitKill` behavior remains permanent.

### Negative control

With the default flag, marking and restoration are no-ops: ordinary permadeath
and an already serialized marker remain unchanged. Both casual profiles
serialize the marker through normal game and suspend paths; a disabled profile
does not activate an existing marker.

### Interactions and save compatibility

Casual mode is independent of the starter, danger, and AoE flags. Alternate
restoration policies do not stack with it. It uses existing packed unit state
and suspend state; there is no new field, layout migration, or compatibility
epoch bump, although the flag changes the diagnostic fingerprint.

### Automation

- `python3 -m unittest discover -s tools/gba-playtest/tests -p "test_expansion_casual_mode.py" -v`
  — `tools/gba-playtest/tests/test_expansion_casual_mode.py`.
- `python3 -m unittest scripts.modernize.tests.test_issue34_35_resolvers.CasualModeContractTests -v`
  — `scripts/modernize/tests/test_issue34_35_resolvers.py`.
- `python3 scripts/modernize/expansion_config.py resolve --config debug --abi aapcs --rom-size 16M --casual-mode 1`
  — `scripts/modernize/expansion_config.py`.

### Cleanup and limitations

Delete generated Autoconf state with `make distclean` only if it was created
for this case. The public policy has no campaign UI or runtime switch; the
host driver is the supported deterministic surface because the framework does
not ship a project-specific scripted-death chapter.

## TC-GAMEPLAY-006: Typed AoE reference heals bounded allied targets

- **Feature / originating issue:** `aoe-reference-module` /
  [#42](https://github.com/laqieer/fireemblem8-expansion/issues/42).
- **Supported configuration or artifact:** modern AAPCS debug or release
  profile `EXPANSION_AOE_REFERENCE=1`, built by the isolated
  `expansion-modern-aoe-check` target; the same target builds a forced
  `EXPANSION_AOE_REFERENCE=0` negative ROM.
- **Prerequisites and clean starting state:** repository root with libmGBA for
  the live gate. The reference probe starts at blue phase, restores its
  temporary HP/range/target-list state, and adds no item, AI route, or save.

### Actions

1. Run
   `python3 -m unittest discover -s tools/gba-playtest/tests -p "test_expansion_aoe.py" -v`.
2. Run `make expansion-modern-aoe-check MODERN_CONFIG=debug MODERN_ABI=aapcs`.
3. Repeat step 2 with `MODERN_CONFIG=release`.

### Expected result

The core uses a validated radius-2 diamond and deterministic target order,
healing damaged allies by exactly `3` HP. The target-capacity path refuses
execution when more than `16` targets match. The enabled ROM probe heals and
then restores two temporary units; it awards no EXP, starts no animation or
event, registers no item, and is never selected by AI.

### Negative control

The forced disabled ROM has no reference probe symbol or heal callback and the
public reference API is inert. Invalid reference values fail configuration;
the typed core and empty item-route seam remain independently available.

### Interactions and save compatibility

AoE is independent of every starter and casual flag and intentionally does not
reuse the battle-stat registry. Selection/execution is synchronous atomic
rebuild; no target, callback, pointer, or proc state is serialized, so there
is no save layout or epoch change.

### Automation

- `python3 -m unittest discover -s tools/gba-playtest/tests -p "test_expansion_aoe.py" -v`
  — `tools/gba-playtest/tests/test_expansion_aoe.py`.
- `make expansion-modern-aoe-check MODERN_CONFIG=debug MODERN_ABI=aapcs`
  — `tools/gba-playtest/run_aoe_checks.py`.
- `make expansion-modern-aoe-check MODERN_CONFIG=release MODERN_ABI=aapcs`
  — `tools/gba-playtest/run_aoe_checks.py`.

### Cleanup and limitations

The gate owns isolated enabled/disabled build roots; remove them with
`make clean_fast` if necessary. This is a reference module, not a spell or
item pack, and downstream routes must supply their own authored items and UI.

## TC-GAMEPLAY-007: Optional gameplay profiles compose without changing saves

- **Feature / originating issue:** `optional-gameplay-combinations` /
  [#55](https://github.com/laqieer/fireemblem8-expansion/issues/55).
- **Supported configuration or artifact:** modern AAPCS debug 16 MiB combined
  source profile: mechanics hooks/sample, danger overlay, starter content at
  item cap `0xCE`, casual mode, and AoE reference all enabled.
- **Prerequisites and clean starting state:** repository root; use a clean
  configuration or explicit command-line values so a prior Autoconf profile
  cannot obscure a dependency failure.

### Actions

1. Run
   `python3 scripts/modernize/expansion_config.py resolve --config debug --abi aapcs --rom-size 16M --mechanics-hooks 1 --mechanics-sample 1 --danger-overlay-menu 1 --starter-content 1 --item-id-cap 0xCE --casual-mode 1 --aoe-reference 1`.
2. Run `python3 -m unittest scripts.modernize.tests.test_expansion_config scripts.modernize.tests.test_issue34_35_resolvers -v`.
3. Run the individual starter runtime and AoE gates from cases
   `TC-GAMEPLAY-003` and `TC-GAMEPLAY-006`; rerun either with its feature
   disabled to exercise its independent negative ROM.

### Expected result

The resolver accepts the combined profile and produces a distinct diagnostic
fingerprint. Starter content and its sample coexist through the one registry;
danger, casual, and AoE remain independently selectable. Each mapped gate
retains its own enabled behavior and disabled/default negative rather than
normalizing a flag or requiring an unrelated feature.

### Negative control

The all-default `make expansion-modern-rom MODERN_CONFIG=debug MODERN_ABI=aapcs`
profile leaves every optional gameplay feature off. A sample without hooks,
content without hooks, content below cap `0xCE`, or any non-binary flag fails
before compilation; no combination silently enables a dependency.

### Interactions and save compatibility

The only dependencies are sample -> hooks and content -> hooks plus item cap.
All other pairs are explicitly independent; there are no feature conflicts.
Every flag changes the config fingerprint but none changes save structure,
`ExpansionSaveMeta`, packed-unit layout, or `EXPANSION_SAVE_COMPAT_EPOCH`.

### Automation

- `python3 -m unittest scripts.modernize.tests.test_expansion_config scripts.modernize.tests.test_issue34_35_resolvers -v`
  — `scripts/modernize/tests/test_expansion_config.py`.
- `make expansion-modern-starter-runtime-check MODERN_CONFIG=debug MODERN_ABI=aapcs`
  — `tools/gba-playtest/tests/test_starter_features_scenarios.py`.
- `make expansion-modern-aoe-check MODERN_CONFIG=debug MODERN_ABI=aapcs`
  — `tools/gba-playtest/run_aoe_checks.py`.

### Cleanup and limitations

Use `make clean_fast` to remove profile artifacts; use `make distclean` only
to remove generated Autoconf state. This case proves supported composition,
not an all-feature release artifact or a new complete-coverage catalog mode.
