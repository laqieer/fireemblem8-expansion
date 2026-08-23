# Localization and locale-persistence feature cases

These procedures cover the shipped expansion and full-game localization
framework from [issue #18](https://github.com/laqieer/fireemblem8-expansion/issues/18),
backfilled by [issue #56](https://github.com/laqieer/fireemblem8-expansion/issues/56).
They use only documented modern AAPCS source profiles and existing semantic
probes, fixtures, and libmGBA scenarios. They do not require the optional
all-features artifact from issue #49.

`qps-ploc` is a deterministic test transform, not a translation. Functional
selection and rendering evidence is automated; linguistic quality and
comparison of rendered CJK artwork to authorized regional artwork remain
separate human judgments.

## TC-LOCALIZATION-001: Default English and single-locale auto-select

- **Feature / originating issue:** `expansion-locale-selection` /
  [#18](https://github.com/laqieer/fireemblem8-expansion/issues/18) and
  [#56](https://github.com/laqieer/fireemblem8-expansion/issues/56).
- **Supported configuration or artifact:** modern AAPCS debug and release
  default profile, `EXPANSION_ENABLED_LOCALES=en`, 16 MiB.
- **Prerequisites and clean starting state:** start at the repository root
  with libmGBA available. The mapped scenarios create a blank SRAM image and
  the reachable `UNSET` preference fixture under the ignored build root.

### Actions

1. Run
   `make expansion-modern-localization-runtime-debug-check MODERN_CONFIG=debug MODERN_ABI=aapcs`.
2. Run
   `make expansion-modern-localization-runtime-release-check MODERN_CONFIG=release MODERN_ABI=aapcs`.
3. From a clean default ROM, boot past the health/safety sequence and open
   Configuration; attempt Right on the only Language value.

### Expected result

The blank-SRAM and `UNSET` routes never show the first-start selector. The
semantic probe records English as the only enabled locale, no visible prompt,
and no settings submenu; Right is a no-op for the single language row.

### Negative control

This is the default English-only control: no real locale payload is selected
or linked. A multi-locale selector must not appear merely because SRAM is
blank, and a single-locale row must not expose a redundant submenu.

### Interactions and save compatibility

The case is independent of optional gameplay and presentation flags. It uses
the existing preference subrecord only; no save layout, migration, epoch, or
config-default change is made.

### Automation

- `python3 -m unittest tools.gba-playtest.tests.test_locale_tester_cases -v`
  — `tools/gba-playtest/tests/test_locale_tester_cases.py`.
- `make expansion-modern-localization-runtime-debug-check MODERN_CONFIG=debug MODERN_ABI=aapcs`
  — `tools/gba-playtest/scenarios/locale-blank-sram-no-selector-default-modern-debug.json`.
- `make expansion-modern-localization-runtime-release-check MODERN_CONFIG=release MODERN_ABI=aapcs`
  — `tools/gba-playtest/scenarios/locale-auto-select-single-locale-modern-release.json`.

### Cleanup and limitations

Use `make clean_fast` only to remove build artifacts. Delete or regenerate
the ignored scenario SRAM before a manual rerun; no committed save or
savestate is used.

## TC-LOCALIZATION-002: Selector, inline configuration, More, and pseudo locale

- **Feature / originating issue:** `expansion-locale-selection` /
  [#18](https://github.com/laqieer/fireemblem8-expansion/issues/18) and
  [#56](https://github.com/laqieer/fireemblem8-expansion/issues/56).
- **Supported configuration or artifact:** `en,qps-ploc` modern debug/release
  16 MiB profile, CJK `en,ja,zh-Hans` 32 MiB profile, and European
  `en,fr,de,es,it` 32 MiB profile.
- **Prerequisites and clean starting state:** use blank SRAM for first start,
  or the generated `valid_explicit_en.sav` fixture to enter Configuration
  without a first-start prompt.

### Actions

1. Run
   `make expansion-modern-localization-runtime-multi-check MODERN_CONFIG=debug MODERN_ABI=aapcs`
   and repeat it with `MODERN_CONFIG=release`.
2. In the two-locale profile, choose **Pseudo (Test)** at first start, then
   navigate Prep Map -> Options -> Configuration -> Language and use
   Right, Left, Right.
3. In an all-language build, verify that the Config row shows its inline
   values first, open **More** only for values beyond the inline slots, and
   exit with Back without choosing a new row.

### Expected result

Multi-locale blank SRAM shows a blocking selector. A real choice updates the
selected/current stable locale ID, cache generation, and stored preference.
The two-locale Config route changes inline and never opens settings; the More
route lists only non-inline values, and Back leaves the selected locale and
preferences unchanged. Pseudo is always labelled **Pseudo (Test)**.

### Negative control

The one-locale profile from `TC-LOCALIZATION-001` remains selector-free and
has no More route. `qps-ploc` is never treated as a production language or a
hand-authored translation.

### Interactions and save compatibility

The selected locale uses the existing preference record and invalidates both
expansion and full-game caches only after an actual change. There are no
feature conflicts and no save migration.

### Automation

- `python3 -m unittest tools.gba-playtest.tests.test_expansion_language_menu.ExpansionLanguageMenuDecisionHostTests tools.gba-playtest.tests.test_locale_tester_cases -v`
  — `tools/gba-playtest/tests/test_expansion_language_menu.py`.
- `make expansion-modern-localization-runtime-multi-check MODERN_CONFIG=debug MODERN_ABI=aapcs`
  — `tools/gba-playtest/scenarios/locale-selector-multi-switch-qps-modern-debug.json`.
- `make expansion-modern-localization-runtime-multi-check MODERN_CONFIG=debug MODERN_ABI=aapcs`
  — `tools/gba-playtest/scenarios/locale-settings-real-navigation-multi-modern-debug.json`.

### Cleanup and limitations

Regenerate the named fixture or use clean SRAM before a different
first-start selection. The test verifies state, persistence, and supported
menu routes; it does not evaluate translation quality.

## TC-LOCALIZATION-003: Production expansion and full-game locale catalogs

- **Feature / originating issue:** `full-game-localization` /
  [#18](https://github.com/laqieer/fireemblem8-expansion/issues/18) and
  [#56](https://github.com/laqieer/fireemblem8-expansion/issues/56).
- **Supported configuration or artifact:** named 32 MiB CJK, European, and
  all-locale modern AAPCS profiles.
- **Prerequisites and clean starting state:** repository root, host compiler,
  and libmGBA for the runtime scenarios. Start each selector run with blank
  SRAM.

### Actions

1. Run `make game-localization-check` and `make game-localization-test`.
2. Run
   `make expansion-modern-localization-runtime-cjk-check MODERN_CONFIG=debug MODERN_ABI=aapcs`
   and
   `make expansion-modern-localization-runtime-eu-check MODERN_CONFIG=debug MODERN_ABI=aapcs`.
3. In the named profile, select English, Japanese, Simplified Chinese, French,
   German, Spanish, and Italian in turn; inspect one expansion UI surface and
   one ordinary full-game message route for the selected profile.

### Expected result

Every real locale has a populated expansion catalog and all 3,414 full-game
entries with zero unresolved or production fallback rows. The CJK scenarios
commit JA (ID 1) and ZH (ID 2); the European selector commits French (ID 3)
from five rows. English remains the explicit fallback locale.

### Negative control

The 16 MiB English-only ROM deliberately contains no real-locale game
payload. A disabled or absent locale is unavailable rather than silently
selecting an unintended catalog.

### Interactions and save compatibility

The expansion catalog and full-game catalog are separate inputs selected by
the same profile. Locale selection uses the existing preference record; no
message ID, locale ID, save layout, or compatibility epoch changes.

### Automation

- `python3 -m unittest scripts.localization.tests.test_catalog scripts.localization.tests.test_eu_localization tools.gba-playtest.tests.test_locale_tester_cases -v`
  — `scripts/localization/tests/test_eu_localization.py`.
- `make expansion-modern-localization-runtime-cjk-check MODERN_CONFIG=debug MODERN_ABI=aapcs`
  — `tools/gba-playtest/scenarios/locale-cjk-first-start-ja-modern-debug.json`.
- `make expansion-modern-localization-runtime-eu-check MODERN_CONFIG=debug MODERN_ABI=aapcs`
  — `tools/gba-playtest/scenarios/locale-eu-first-start-fr-modern-debug.json`.

### Cleanup and limitations

Use a separate blank SRAM image for each selected locale. The deterministic
checks prove catalog selection and coverage, not linguistic editorial review.

## TC-LOCALIZATION-004: Preference repair, no-wipe behavior, and soft-reset persistence

- **Feature / originating issue:** `locale-preference-persistence` /
  [#18](https://github.com/laqieer/fireemblem8-expansion/issues/18) and
  [#56](https://github.com/laqieer/fireemblem8-expansion/issues/56).
- **Supported configuration or artifact:** default and `en,qps-ploc` modern
  debug/release profiles with generated `UNSET`, `CORRUPT`,
  `UNKNOWN_LOCALE`, and `DISABLED_LOCALE` SRAM fixtures.
- **Prerequisites and clean starting state:** use only the fixture generated
  by the mapped target; do not reuse a manually edited or committed save.

### Actions

1. Run
   `python3 -m unittest scripts.modernize.tests.test_expansion_user_prefs_native tools.gba-playtest.tests.test_locale_tester_cases -v`.
2. Run the multi-locale runtime check in debug and release, then run
   `make expansion-modern-localization-runtime-prefs-check MODERN_CONFIG=debug MODERN_ABI=aapcs`.
3. For each repair fixture, let the selector appear, move Down then Up,
   confirm English, and issue `A+B+SELECT+START` on the same SRAM image.

### Expected result

Each invalid/unset preference reports its own state and requires repair in a
multi-locale selector. Confirming English writes a valid explicit record even
though English was the fallback, leaves every non-excluded SRAM byte
unchanged, and suppresses the selector after the real soft reset.

### Negative control

A valid explicit selection suppresses the first-start prompt. The
single-locale no-wipe scenarios legitimately collapse repair to auto-select,
so they are retained as a separate control and not substituted for the
multi-locale prompt/choose-default matrix.

### Interactions and save compatibility

The only allowed mutation is the existing 12-byte preference record; two
documented vanilla boot-bookkeeping ranges are excluded from the whole-SRAM
hash. Normal save/load and suspend/resume use their established regression
gates, with no format or epoch migration.

### Automation

- `python3 -m unittest scripts.modernize.tests.test_expansion_user_prefs_native tools.gba-playtest.tests.test_locale_tester_cases -v`
  — `scripts/modernize/tests/test_expansion_user_prefs_native.py`.
- `make expansion-modern-localization-runtime-multi-check MODERN_CONFIG=release MODERN_ABI=aapcs`
  — `tools/gba-playtest/scenarios/locale-repair-corrupt-multi-modern-release.json`.
- `make expansion-modern-localization-runtime-prefs-check MODERN_CONFIG=debug MODERN_ABI=aapcs`
  — `tools/gba-playtest/scenarios/locale-prefs-corrupt-no-wipe-modern-debug.json`.

### Cleanup and limitations

Fixtures are generated under ignored build paths and may be removed with
`make clean_fast`. Do not commit fixture bytes, save files, or savestates.

## TC-LOCALIZATION-005: UTF-8 controls, bounded consumers, and cache switching

- **Feature / originating issue:** `localized-text-input-ui` /
  [#18](https://github.com/laqieer/fireemblem8-expansion/issues/18) and
  [#56](https://github.com/laqieer/fireemblem8-expansion/issues/56).
- **Supported configuration or artifact:** CJK-enabled modern 32 MiB host and
  ROM profiles; English-only and archival builds are negative controls.
- **Prerequisites and clean starting state:** repository root with a host C
  compiler and the repository preprocessor tool available or buildable.

### Actions

1. Run
   `python3 scripts/texttools/tests/test_text_renderer_native.py` and
   `python3 scripts/texttools/tests/test_text_consumers_native.py`.
2. Run
   `python3 -m unittest scripts.localization.tests.test_resolver_native.ResolverNativeTests -v`.
3. Select a second locale through a documented selector or Config route after
   resolving both an expansion UI message and a full-game message.

### Expected result

The real UTF-8 renderer and text consumers accept valid controls and
substitutions at bounded capacity, reject malformed/truncated input with an
explicit marker, and preserve whole scalar boundaries for layout. A real
locale switch invalidates expansion and full-game caches together.

### Negative control

English-only and archival builds keep their established legacy text paths.
Unknown-size modern buffer calls fail visibly instead of performing an
unbounded write.

### Interactions and save compatibility

This is runtime scratch/cache state only. It does not persist controls,
buffers, pointers, or locale text in saves and it does not change profile
identity beyond the already selected locale profile.

### Automation

- `python3 scripts/texttools/tests/test_text_renderer_native.py`
  — `scripts/texttools/tests/test_text_renderer_native.py`.
- `python3 scripts/texttools/tests/test_text_consumers_native.py`
  — `scripts/texttools/tests/test_text_consumers_native.py`.
- `python3 -m unittest scripts.localization.tests.test_resolver_native.ResolverNativeTests -v`
  — `scripts/localization/tests/test_resolver_native.py`.

### Cleanup and limitations

The host tests remove their generated scratch output. The deterministic suite
does not replace a linguistic review of translated text.

## TC-LOCALIZATION-006: Locale-specific tactician entry remains byte-bounded

- **Feature / originating issue:** `localized-text-input-ui` /
  [#18](https://github.com/laqieer/fireemblem8-expansion/issues/18) and
  [#56](https://github.com/laqieer/fireemblem8-expansion/issues/56).
- **Supported configuration or artifact:** modern JA or ZH 32 MiB profile
  plus the normal ASCII entry page.
- **Prerequisites and clean starting state:** start from a player-name field
  whose prior value is known; no generated fixture or savestate is required.

### Actions

1. Run
   `python3 -m unittest scripts.localization.tests.test_sio_localization scripts.texttools.tests.test_text_consumers_native.TextConsumerNativeTests.test_reviewed_consumer_functions -v`.
2. Select Japanese or Simplified Chinese, visit its tactician pages and then
   the ASCII page, and enter a name of at most ten encoded bytes plus NUL.
3. Attempt an oversized string and one that would split a UTF-8 scalar.

### Expected result

The locale-specific grids and ASCII page are available through the selected
locale route. A valid name occupies at most ten bytes plus its terminator.
Oversize or split-scalar input is rejected atomically and preserves the
previous name.

### Negative control

English, pseudo, and archival builds retain their established grids. The
persistent player-name field never expands and rejected input never leaves a
partial prefix.

### Interactions and save compatibility

This case uses the existing player-name storage field and serialization. It
adds no save field, migration, format version, or compatibility epoch.

### Automation

- `python3 -m unittest scripts.localization.tests.test_sio_localization scripts.texttools.tests.test_text_consumers_native.TextConsumerNativeTests.test_reviewed_consumer_functions -v`
  — `scripts/localization/tests/test_sio_localization.py`.
- `python3 -m unittest tools.gba-playtest.tests.test_locale_tester_cases -v`
  — `tools/gba-playtest/tests/test_locale_tester_cases.py`.

### Cleanup and limitations

Restore or erase only the test player's name before a manual repeat. The
framework does not add a campaign-specific tactician test chapter.

## TC-LOCALIZATION-007: Localized static graphics and CJK menu regressions

- **Feature / originating issue:** `localized-text-input-ui` /
  [#18](https://github.com/laqieer/fireemblem8-expansion/issues/18) and
  [#56](https://github.com/laqieer/fireemblem8-expansion/issues/56).
- **Supported configuration or artifact:** CJK and all-locale 32 MiB modern
  profiles; all-locale release is the Mode Select profile.
- **Prerequisites and clean starting state:** blank SRAM and libmGBA for the
  runtime scenario; do not create a save slot before reaching Mode Select.

### Actions

1. Run `make localized-ui-graphics-check`.
2. Run
   `make expansion-modern-localization-runtime-cjk-check MODERN_CONFIG=debug MODERN_ABI=aapcs`.
3. Select JA or ZH, reach Mode Select before choosing difficulty, then reach
   the Save Menu after the documented persistence route.

### Expected result

The selected CJK locale takes the registered title/menu/chapter and Mode
Select resource route. The zh-Hans persistence fingerprint records the
corrected Save Menu OAM layout while selected locale, persistence, and
selector-suppression probes remain semantic and stable.

### Negative control

English and European locales retain the original FE8U resources. Missing CJK
resource lookup follows the documented English/default fallback; the
pre-fix zh-Hans OAM framebuffer hash remains a rejected regression value.

### Interactions and save compatibility

Static resource selection follows the active locale and changes no preference
semantics, save byte, save format, or configuration identity. It conflicts
with no optional module.

### Automation

- `python3 -m unittest scripts.localization.tests.test_localized_ui_graphics tools.gba-playtest.tests.test_locale_tester_cases -v`
  — `scripts/localization/tests/test_localized_ui_graphics.py`.
- `make localized-ui-graphics-check`
  — `graphics/localized_ui/manifest.json`.
- `make expansion-modern-localization-runtime-cjk-check MODERN_CONFIG=debug MODERN_ABI=aapcs`
  — `tools/gba-playtest/scenarios/locale-cjk-softreset-persistence-modern-debug.json`.

### Cleanup and limitations

Remove only test SRAM/build output before another locale observation. The
remaining manual-only criterion is visual comparison of the rendered glyph
artwork with authorized regional artwork; semantic asset selection, layout,
and persistence are automated.

## TC-LOCALIZATION-008: Validated localization profiles and resource budgets

- **Feature / originating issue:** `localization-profile-validation` /
  [#18](https://github.com/laqieer/fireemblem8-expansion/issues/18) and
  [#56](https://github.com/laqieer/fireemblem8-expansion/issues/56).
- **Supported configuration or artifact:** clean source checkout; default,
  pseudo, CJK, European, and all-locale named modern AAPCS profiles.
- **Prerequisites and clean starting state:** repository root, ARM toolchain,
  host compiler, and libmGBA for linked runtime targets. Do not hand-edit
  generated build output.

### Actions

1. Run `make localization-validate`, `make localization-generate`,
   `make localization-check`, and `make localization-test`.
2. Run `make game-localization-validate`, `make game-localization-generate`,
   `make game-localization-check`, and `make game-localization-test`.
3. Run
   `make expansion-modern-localization-profile-headroom-check MODERN_CONFIG=release MODERN_ABI=aapcs`
   and
   `make expansion-modern-localization-profile-all MODERN_CONFIG=release MODERN_ABI=aapcs`.
4. Retain the pre-compilation errors from a real locale at 16 MiB, a default
   locale omitted from the enabled list, and a pseudo flag/list mismatch.

### Expected result

Valid profiles generate deterministic, isolated catalog outputs and link with
positive ROM, EWRAM, and IWRAM headroom. The all-locale profile preserves the
same stable ID order and profile identity metadata as the individual profiles.

### Negative control

Invalid size/default/pseudo combinations fail before compilation. Build-local
catalogs cannot be reused across profile roots and no budget threshold,
fingerprint, generated source, or runtime baseline is refreshed.

### Interactions and save compatibility

Locale profile choices are diagnosable through existing configuration metadata
but are not save-compatibility keys. No generated source belongs in commits,
and the archival lane is not built or changed by this case.

### Automation

- `python3 -m unittest scripts.localization.tests.test_catalog scripts.localization.tests.test_generate scripts.localization.tests.test_eu_localization tools.gba-playtest.tests.test_locale_tester_cases -v`
  — `scripts/localization/tests/test_catalog.py`.
- `make expansion-modern-localization-profile-headroom-check MODERN_CONFIG=release MODERN_ABI=aapcs`
  — `scripts/linker_report/localization_budget.py`.
- `make expansion-modern-localization-profile-all MODERN_CONFIG=release MODERN_ABI=aapcs`
  — `modern.mk`.

### Cleanup and limitations

Use `make clean_fast` for ignored artifacts. The final live provenance gate
requires separately authorized local ROM inputs and is not a substitute for,
or required by, these shipped tester procedures.
