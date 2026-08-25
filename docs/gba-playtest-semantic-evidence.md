# Language/More semantic evidence

Issue [#159](https://github.com/laqieer/fireemblem8-expansion/issues/159)
is an independent framework test-evidence consolidation rooted at
`b20b060a90f9a96076e37c615a086d78e1e36118`. It changes no menu behavior,
feature default, locale ID, save schema, or archival contract. It owns one
five-locale More scenario and the following five audit-v1 rewrite IDs; all map
to
[`TC-TEST-QUALITY-001`](test-cases/foundation.md#tc-test-quality-001-meaningful-test-evidence-policy-rejects-semantic-mutations).

| Exact audit ID | Replacement and mutation control |
| --- | --- |
| `tools/gba-playtest/tests/test_expansion_language_menu.py::ExpansionLanguageMenuDecisionHostTests.test_decision_table_matches_every_real_input_combination` | The native driver executes the production startup/settings decision tables and menu geometry; success is its exit status, not a PASS string. Mutating a returned action, locale, reason, or geometry fails a real input. |
| `tools/gba-playtest/tests/test_expansion_language_menu.py::GameControlIntegrationStructureTests.test_title_idle_and_debug_hotkey_lifecycle_untouched` | The production startup initializer executes on host under two different vanilla-language/XMAP states, and the ROM scenario reaches Prep Configuration. Mutating initialization or coupling it to either legacy state changes the observed result. |
| `tools/gba-playtest/tests/test_expansion_language_menu.py::UiConfigLanguageEntryStructureTests.test_more_submenu_lists_only_locales_outside_inline_slots` | The decision driver, parsed generated locale budget, compiled Config edges, and live five-locale scenario prove the inline/More split. Mutating the third-inline transition, More row, Italian selection, close, persistence, or redraw fails. |
| `tools/gba-playtest/tests/test_expansion_language_menu.py::ExpansionLocaleVanillaIsolationTests.test_owned_runtime_files_have_no_vanilla_language_or_xmap_calls` | All four production objects reject vanilla-language relocations and compile under poisoned XMAP globals, fields, constants, IDs, and APIs; per-object adversarial injection proves the poison relocation is detected. The host initializer also varies and preserves executable XMAP state. |
| `tools/gba-playtest/tests/test_starter_features_scenarios.py::StarterHookScenarioSchemaTests.test_positive_asserts_hook_fired_and_negative_asserts_all_zero` | The centrally registered libmGBA class captures both real-combat ROMs: the starter profile records hook activity and the default ROM keeps all hook scalars zero. A stale fingerprint without live capture is insufficient. |

Semantics-preserving private renames, reordered helpers, and changed PASS text
stay green. Behavior mutations at the named public decisions, generated
registry, formatter, initializer, object boundary, scenario probes, or starter
hook fail without relying on comments or implementation spelling.

## Tester procedure

- **Profile and environment:** modern debug AAPCS, the 32 MiB
  `EXPANSION_ENABLED_LOCALES=en,fr,de,es,it` ROM, the starter-hooks ROM, the
  default ROM, host GCC/`nm`, and libmGBA. Start More from
  `valid_explicit_en.sav`.
- **Action:** run
  `make expansion-modern-localization-runtime-eu-check expansion-modern-starter-hook-check MODERN_CONFIG=debug MODERN_ABI=aapcs`.
  In Configuration, move Right through the three inline slots, open More,
  choose Italian with Down+A, and let the parent redraw.
- **Expected:** generated profile metadata parses to the five stable locale
  IDs; probes report five locales, More active, then More closed with current
  and selected locale `6`; the persisted preference byte is `6`.
- **Negative controls:** the default Config object has no language-menu
  references, host initialization is invariant across vanilla/XMAP states,
  and the default starter ROM completes the same combat with zero hook probes.
- **Interactions:** dependency is merged #100; there are no feature conflicts,
  save migration, configuration-identity change, or manual-only criterion.
  Cleanup is the scenario runner's isolated SRAM copy.

## Budget baseline provenance

The committed reports were stale relative to the exact base, so this issue
owns their directly derived refresh. This is not a no-budget-change claim.

| Owner | Old -> new | Provenance |
| --- | ---: | --- |
| Debug EWRAM | 260320 -> 260336 occupied (+16); 1824 -> 1808 free | PR #147 added the debug-only 6-byte `sSelectorState` after the previous report owner; linker section placement/alignment produces the measured +16 region delta. Release EWRAM remains 259056 occupied. |
| Source catalog | 112 -> 120 messages; index 3904 -> 4176 (+272); strings 9094 -> 10183 (+1089) | The eight compact locale-name records were already merged by `b8a31859` (en/qps-ploc), `6a2c6eb4` (ja/zh-Hans), and `cae24675` (fr/de/es/it). |
| Linked catalog | message-ID table 224 -> 240 (+16); eight string tables 3584 -> 3840 (+256) | Regeneration from those same parsed registry entries adds one `u16` ID and one pointer per locale for each of eight messages. |
| Scratch/glyph report | maximum slot 33 -> 63; headroom 63 -> 33; glyphs 362 -> 396 | Direct output of the current catalog generator; the fixed scratch budget remains 96 and has positive headroom. |

Rollback reverts this issue's tests, scenario, documentation, and refreshed
reports together; it does not migrate saves or change production behavior.
