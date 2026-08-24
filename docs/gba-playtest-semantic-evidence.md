# GBA playtest semantic evidence

Issue [#105](https://github.com/laqieer/fireemblem8-expansion/issues/105)
replaces source-spelling and source-order checks in the GBA playtest domain
with observable contracts. It changes no game mechanic, feature default,
save format, generated-data schema, locale ID, or ROM/RAM budget.

The audited set is the 108 `rewrite` records in audit v1 whose domain is
`gba-playtest-runtime`. The following table is the complete record map. A
module's listed tests retain its named tester cases and fixtures; only raw
implementation spelling is removed as evidence.

| Audited records | Replacement evidence | Positive control | Negative control |
| --- | --- | --- | --- |
| `test_debugtools_registry.py` (53) | Native drivers execute the public registry, launcher, actions, diagnostics, and extended-tools APIs; debugtools scenarios capture counters and state probes. | `debugtools-*-modern-debug.json` | Matching `*-modern-release.json` probes remain zero. |
| `test_debugtools_sram_fixture.py` (1) | Deterministic fixture writer and classifier operate on generated SRAM bytes. | Current-format image classifies current. | Invalid or stale image is rejected. |
| `test_expansion_aoe.py` (4) | Native enabled/disabled API drivers, ARM object/section checks, and enabled relocations from every item/AI production caller to the public dispatch API. | Every production route retains `ExpansionAoE_DispatchItem`; the reference action records its probe. | Disabled reference object has no reference probe/effect callback. |
| `test_expansion_danger_overlay.py` (7) | Enabled/default compiled objects plus danger-overlay probe scenarios. | Enabled overlay checkpoint observes the menu action. | Default checkpoint remains all zero. |
| `test_expansion_language_menu.py` (4) | Native decision driver, parsed scenario schema, generated catalog headers, and modern/default Config-object relocations. | The >4-locale table reaches More and every public selection decision is retained by the modern object. | Default Config object has no language-menu relocation; owned locale objects have no vanilla-language/XMAP relocation. |
| `test_expansion_mechanics.py` (5) | Native registry/sample drivers and enabled/default battle object references. | Hook driver applies the registered effect. | Default object has no mechanics reference; enabled battle object has no starter-content reference. |
| `test_expansion_starter_content.py` (18) | Generated content output, ARM profile compilation, a tiny public `ItemExpansionProbe` ABI consumer, item-name object data, and starter hook scenarios. | Starter profile executes real combat and increments hook probes. | Default profile performs the same combat with zero hook probes. |
| `test_host_only_mode.py` (2) | Parsed-AST discovery of all test modules plus poisoned artifact access for registered live classes. | Category-A checks run in host-only mode. | Every discovered Category-B class skips before artifact access and must be registered. |
| `test_prep_positive_scenario.py` (1) | Parsed input timeline and semantic prep probes. | Prep hotkey produces the documented observation. | Release/default probe remains inert. |
| `test_probe_bindings.py` (1) | Fake `nm` executables plus a generated `make -n` starter binding recipe exercise explicit/configured forwarding and failure resolution. | Explicit/configured tools resolve a symbol and the generated recipe forwards `MODERN_NM`. | Missing tool reports `ProbeBindingError`. |
| `test_save_compat_gate_safety.py` (6) | Compiled undefined-symbol boundaries, a tiny public probe-header consumer, and save-compat scenarios assert SRAM classification, confirmation, and erase outcomes. | Current and explicit-confirmation flows complete. | Forbidden slot APIs and external normal-save-menu starts have no relocation; Back/corrupt paths preserve saves and never erase. |
| `test_save_load_scenario.py` (1) | Parsed reset input and live SRAM/play-state transition. | New game, reset, and reload restore the discriminants. | Pre-write SRAM differs from the created save. |
| `test_savesuspend_resume_scenario.py` (2) | Parsed ordinary UI inputs and runtime SRAM/play-state checkpoints. | Manual suspend resumes the intended state. | No raw memory poke or bypass fixture is accepted. |
| `test_starter_features_scenarios.py` (1) | Parsed behavior fingerprints and live starter/default ROM probes. | Starter hook fires during real combat. | Same combat on the default ROM keeps every hook scalar zero. |
| `test_worldmap_proc_iter_null_guard.py` (2) | Optimized ARM objects inspect every world-map iterator call's immediate null branch. | Every `Proc_FindNext` result is compared and branched on before dereference. | Restoring any null-dereference mutation removes that branch. |

## Tester-facing evidence

`TC-TEST-QUALITY-001` is the governing tester case. The related game-facing
procedures remain in their existing scenario tests; no case IDs were removed
or renumbered. Each runtime scenario is executed against the named modern
profile that supplies its ELF probe bindings. A scenario baseline is never
refreshed for this refactor: a mismatch requires a behavioral root-cause
investigation.

The supported modern profiles exercised by this evidence are default
debug/release and the starter debug/release profile where a positive feature
case needs it. The archival lane is not part of this issue. Save compatibility
is unchanged: all save assertions continue to use the existing deterministic
fixtures and current-format classification.

## Rollback

Revert this issue's test/doc commit to restore the former checks. A rollback
does not change a ROM, save, generated artifact, configuration identity, or
fingerprint.
