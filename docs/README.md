# Documentation index

This is the authoritative entry point for this repository's documentation.
The project's default, supported path is a **modern `arm-none-eabi`
GCC/AAPCS release framework**; the original agbcc-based decompilation
workflow is preserved as an explicit, clearly separated **archival** lane.
See [`../README.md`](../README.md) for the top-level project overview.
The [project wiki](https://github.com/laqieer/fireemblem8-expansion/wiki)
provides a concise navigation portal; the versioned files indexed here remain
the authoritative technical documentation and are checked by CI.

## Start here

| If you want to... | Read |
| --- | --- |
| Build the project for the first time | [`quickstart.md`](quickstart.md) |
| Understand what's actually supported (hosts, toolchains, targets) | [`framework-support.md`](framework-support.md) |
| Get the architecture map before diving into source | [`architecture.md`](architecture.md) |
| Author game content (characters/classes/items/etc.) | [`generated_data_tutorial.md`](generated_data_tutorial.md) |
| Enable/extend starter content, mechanics, or Danger QoL | [`starter_features.md`](starter_features.md) |
| Add a bounded area-of-effect target/effect or item route | [`aoe.md`](aoe.md) |
| Drive or optionally delegate blue units through the existing AI, including bounded semantic runtime scenarios | [`autoplay.md`](autoplay.md) |
| Author typed chapter objectives and AI-group membership for bounded autoplay | [`generated_data_tutorial.md`](generated_data_tutorial.md), [`autoplay.md`](autoplay.md) |
| Author a bounded autoplay strategy assignment or downstream profile | [`generated_data_tutorial.md`](generated_data_tutorial.md), [`autoplay.md`](autoplay.md) |
| Author and enable strict custom battle spell-effect packages | [`custom_spell_effects.md`](custom_spell_effects.md) |
| Configure casual defeat restoration | [`starter_features.md`](starter_features.md#optional-casual-defeat-policy-issue-34) |
| Enable the optional high-resolution MP2K PCM mixer | [`audio.md`](audio.md) |
| Configure portrait/minimug rules | [`portrait_resolver.md`](portrait_resolver.md) |
| Run a tester-facing feature procedure | [`test-cases/README.md`](test-cases/README.md) |
| Audit a community asset format before proposing an adapter | [`community_asset_coverage.md`](community_asset_coverage.md) |
| Author a source-owned asset record or add an asset adapter | [`asset_manifest.md`](asset_manifest.md) |
| Author a strict community battle-animation text/PNG package | [`battle_animation_packages.md`](battle_animation_packages.md) |
| Author a strict formatted full-portrait package | [`portrait_packages.md`](portrait_packages.md) |
| Author a safe Tiled chapter map layout | [`tmx_map_layouts.md`](tmx_map_layouts.md) |
| Author expansion-localized UI text/locales | [`localization.md`](localization.md) |
| Build or verify the patch-only maximal profile artifact | [`patch_release.md`](patch_release.md) |
| Generate or test localized full-game message catalogs | [`game_localization_catalog.md`](game_localization_catalog.md) |
| Evaluate and deliver a feature request or bug fix with Copilot | [`development-workflow`](../.github/skills/development-workflow/SKILL.md) |
| Reproduce the workflow-efficiency baseline, event selection, or bounded exact-SHA handoffs | [`workflow-pilot.md`](workflow-pilot.md) |
| Contribute code/docs and know the review process | [`../CONTRIBUTING.md`](../CONTRIBUTING.md), [`project-governance.md`](project-governance.md) |
| Come from the old decomp-base/agbcc workflow | [`migration-from-decomp.md`](migration-from-decomp.md) |
| Do byte-for-byte decomp-matching work | [`archival-decomp.md`](archival-decomp.md) |
| Write a future version-to-version migration guide | [`release-migration-template.md`](release-migration-template.md) |

## Learning paths

**New contributor, modern framework (most people):**
1. [`quickstart.md`](quickstart.md) — install and boot-verify a build.
2. [`config_identity.md`](config_identity.md) — configure identity, debug/release, starter flags, and locales.
3. [`architecture.md`](architecture.md) — orient yourself.
4. [`generated_data_tutorial.md`](generated_data_tutorial.md) and
   [`localization.md`](localization.md) — author typed content and expansion UI text.
5. [`../CONTRIBUTING.md`](../CONTRIBUTING.md) — run host checks, both ROM gates, and record PR evidence.
6. [`debugtools.md`](debugtools.md) and
   [`../tools/gba-playtest/README.md`](../tools/gba-playtest/README.md) — debug bounded runtime behavior.

**Coming from the old decomp-first workflow:**
1. [`migration-from-decomp.md`](migration-from-decomp.md) — what changed and why.
2. [`archival-decomp.md`](archival-decomp.md) — if your goal is still byte-for-byte matching, this is unchanged and still supported *for that purpose*.

**Maintaining framework identity, saves, typed IDs, or debug tooling:**
1. [`config_identity.md`](config_identity.md)
2. [`save_format.md`](save_format.md)
3. [`id_space.md`](id_space.md) — typed IDs / DEFAULT vs ACTIVE contract (issue #10)
4. [`debugtools.md`](debugtools.md)

**Tracking or applying upstream decomp changes:**
1. [`upstream-porting.md`](upstream-porting.md)

## Public API index (source of truth by subsystem)

Concrete entry points a downstream contributor actually calls/includes/
extends -- each row links the reference doc, not aspirational; issue
numbers mark merged (closed) contracts only:

| Subsystem | Public entry points | Reference |
| --- | --- | --- |
| Generated data authoring | JSON sources under `src/data/*.json` + table schemas in `scripts/generated_data/*/schema.py`; `make generated-data-check`/`generated-data-active-heal-check` | [`generated_data.md`](generated_data.md), [`generated_data_tutorial.md`](generated_data_tutorial.md) |
| Typed BGM routing and continuation | Public context router, validated chapter/flag variants, dancer/staff selectors, and preserve/resume/restart policy | [`bgm_routing.md`](bgm_routing.md), [`config_identity.md`](config_identity.md) |
| Optional HQ PCM mixer (issue #83) | Default-off modern MP2K mixer selection, IWRAM budget, and target PCM procedure | [`audio.md`](audio.md) |
| Typed IDs / caps (issue #10) | `include/id_space.h` (DEFAULT), `build/generated/data/id_space_active.h` (ACTIVE), `FE8_ITEM_ID_CAP` | [`id_space.md`](id_space.md), [`../reports/id_space_audit.md`](../reports/id_space_audit.md) |
| Config / ROM identity | `struct ExpansionMetadata` (`include/expansion_metadata.h`), `EXPANSION_SAVE_COMPAT_EPOCH` | [`config_identity.md`](config_identity.md), [`save_format.md`](save_format.md) |
| Debug-tools extension (issues #11/#128) | Action-registration API (`include/expansion_debugtools.h`), `FE8_EXPANSION_DEBUGTOOLS_ENABLED`, isolated volatile save fixtures | [`debugtools.md`](debugtools.md), [`debug_save_fixtures.md`](debug_save_fixtures.md) |
| Release-safe mGBA logging (issue #68) | Bounded typed API (`include/expansion_log.h`), existing `FE8_EXPANSION_LOGGING_ENABLED` debug profile gate | [`debugtools.md`](debugtools.md#release-safe-mgba-logging-issue-68) |
| Starter features (issue #6) | Four default-off flags; `include/expansion_mechanics.h`; `include/expansion_starter_content.h`; danger-overlay menu | [`starter_features.md`](starter_features.md) |
| Typed area-of-effect actions (issue #42) | `include/expansion_aoe.h`; bounded target/effect API and shared item/action/AI route registry | [`aoe.md`](aoe.md) |
| Transient blue computer control (issue #85) | `include/expansion_autoplay.h`; `PLAYER`/`COMPUTER` control and pointer-free semantic telemetry | [`autoplay.md`](autoplay.md) |
| Bounded semantic run-until scenarios (issue #86) | `tools/gba-playtest` schema v2, fingerprint format v3, seven typed terminal reasons, and fixed-frame compatibility | [`autoplay.md`](autoplay.md), [`../tools/gba-playtest/README.md`](../tools/gba-playtest/README.md) |
| Typed chapter objectives and AI groups (issue #89) | `include/expansion_chapter_objectives.h`; generated `chapterobjectives` records, chapter-bundle ownership, and semantic telemetry | [`generated_data_tutorial.md`](generated_data_tutorial.md), [`autoplay.md`](autoplay.md) |
| Typed autoplay strategy profiles (issue #90) | `include/expansion_autoplay_strategies.h`; generated `autoplaystrategies` registry and chapter/group/unit assignments | [`generated_data_tutorial.md`](generated_data_tutorial.md), [`autoplay.md`](autoplay.md) |
| Optional one-phase Charge delegation (issue #87) | `include/expansion_autoplay.h`; default-off one-phase map command built on the #85 control/telemetry API | [`autoplay.md`](autoplay.md) |
| Accelerated-fidelity comparison (issue #88) | `tools/gba-playtest` schema v3, profile-contained game-speed/BANIM-off configuration, semantic differential traces, and fixed-frame reduction | [`autoplay.md`](autoplay.md), [`../tools/gba-playtest/README.md`](../tools/gba-playtest/README.md) |
| Custom battle spell effects (issues #77/#78) | `include/custom_spell_effect.h` plus the `custom-spell-effect` manifest kind; typed runtime and strict generated package binding | [`custom_spell_effects.md`](custom_spell_effects.md) |
| Portrait/minimug resolver (issue #35) | Typed character/class/chapter/flag registry with legacy fallback | [`portrait_resolver.md`](portrait_resolver.md) |
| Community asset coverage (issue #59) | Asset-family ownership/gap catalog; not an importer or runtime API | [`community_asset_coverage.md`](community_asset_coverage.md) |
| Asset manifest (issue #60) | Versioned source-owned asset records and generated existing-seam dependencies | [`asset_manifest.md`](asset_manifest.md) |
| Battle-animation packages (issue #62) | Strict text/PNG package adapter through the existing battle-animation and compressor seams | [`battle_animation_packages.md`](battle_animation_packages.md) |
| Formatted portrait packages (issue #63) | Strict sheet contract, generated FaceData registration, and tester procedure | [`portrait_packages.md`](portrait_packages.md) |
| Safe TMX chapter maps (issue #64) | Fail-closed Tiled 1.10 map-layout subset and source workflow | [`tmx_map_layouts.md`](tmx_map_layouts.md) |
| Localization (issue #18) | `ExpansionLocaleId`/`ExpansionMsgId`, `texts/expansion/`, prefs + selector/settings APIs | [`localization.md`](localization.md), [`save_format.md`](save_format.md) |
| Runtime test harness (issue #13) | JSON scenario format + fingerprints, `GBA_PLAYTEST_HOST_ONLY` | [`../tools/gba-playtest/README.md`](../tools/gba-playtest/README.md) |
| Upstream-port review tooling | `python3 -m scripts.upstream_port {scan,drift,verify,update-state}` | [`upstream-porting.md`](upstream-porting.md) |
| Proc/runtime core | `include/proc.h` (`struct Proc`, `struct ProcCmd[]`) | [`architecture.md`](architecture.md) |

Not a public API today: issue #9's future versioned-release/downstream-
upgrade tooling. The repository currently has no release automation, tags/
changelog contract, versioned artifact pipeline, or downstream updater; the
migration template is scaffolding only.

## Documentation governance

This document is the human-oriented narrative index. The **machine-checked,
exact-coverage registry** of every Markdown file in this repository (owner,
status, scope) is [`documentation-inventory.md`](documentation-inventory.md);
external URLs used across all docs are classified in
[`external-link-registry.md`](external-link-registry.md). Both are enforced
by [`scripts/check_docs.py`](../scripts/check_docs.py) (stdlib-only,
zero-network) in CI -- see its own `--help` and
[`scripts/docs_check_tests/`](../scripts/docs_check_tests/) for the checker
and its test suite. Candidate closure-mapping evidence for the documentation
governance work itself lives in
[`reports/issue7_documentation_foundation.md`](../reports/issue7_documentation_foundation.md)
and
[`reports/issue17_documentation_audit.md`](../reports/issue17_documentation_audit.md)
-- neither claims a GitHub issue is closed; see
[`docs/issue-resolution-policy.md`](issue-resolution-policy.md#issue-closure-evidence).

## Full document list and status

| Document | Status | Scope |
| --- | --- | --- |
| [`quickstart.md`](quickstart.md) | Current | One-command setup, modern default + archival `--legacy` path |
| [`framework-support.md`](framework-support.md) | Current | Supported hosts/toolchains/targets/outputs |
| [`architecture.md`](architecture.md) | Current | Concise architecture map + later integration slots |
| [`project-governance.md`](project-governance.md) | Current | Contribution/security/copyright/credits/compatibility policy |
| [`migration-from-decomp.md`](migration-from-decomp.md) | Current | Decomp-base/agbcc → modern framework bridge |
| [`release-migration-template.md`](release-migration-template.md) | Template | Fill in for a real future version migration |
| [`archival-decomp.md`](archival-decomp.md) | Current, archival scope | Unsupported-for-releases decomp-matching workflow |
| [`workflow-pilot.md`](workflow-pilot.md) | Current | Frozen workflow-efficiency baseline, event/evidence protocol, artifact lifecycle, and bounded exact-SHA handoffs (issues #176/#177/#178) |
| [`ownership-probe-foundation.md`](ownership-probe-foundation.md) | Current | Confined native Make/registry execution, aggregate budgets, source admission and downstream graph seam (issue #206) |
| [`config_identity.md`](config_identity.md) | Current | Config surface + ROM identity fingerprint (issue #8) |
| [`save_format.md`](save_format.md) | Current | Save format + compatibility gate (issue #2) |
| [`id_space.md`](id_space.md) | Current | Typed-ID DEFAULT vs ACTIVE contract, cap switching (issue #10) |
| [`debugtools.md`](debugtools.md) | Current | Debug-tools subsystem, merged (issue #11); see its "Remaining #11 scope" for the few narrow non-goals |
| [`generated_data.md`](generated_data.md) | Current, reference | Full generated-data design reference (issue #5) |
| [`generated_data_tutorial.md`](generated_data_tutorial.md) | Current, tutorial | Contributor-facing generated-data walkthrough |
| [`starter_features.md`](starter_features.md) | Current | Four opt-in flags, typed mechanics/content API, QoL and matrices (issue #6) |
| [`aoe.md`](aoe.md) | Current | Typed bounded AoE targeting/effects, shared item seam, and default-off reference (issue #42) |
| [`autoplay.md`](autoplay.md) | Current | Transient blue control/telemetry, bounded semantic outcomes, optional Charge delegation, and accelerated-fidelity comparison (issues #85/#86/#87/#88) |
| [`custom_spell_effects.md`](custom_spell_effects.md) | Current | Default-off typed custom spell runtime, strict FEditor subset adapter, package schema, resources, and tester cases (issues #77/#78) |
| [`portrait_resolver.md`](portrait_resolver.md) | Current | Typed data-driven portrait/minimug resolver and validation contract (issue #35) |
| [`community_asset_coverage.md`](community_asset_coverage.md) | Current | Authoritative community asset family ownership, build/runtime seam, and gap catalog (issue #59) |
| [`asset_manifest.md`](asset_manifest.md) | Current | Versioned asset manifest, generated existing-seam dependencies, and adapter contract (issue #60) |
| [`battle_animation_packages.md`](battle_animation_packages.md) | Current | Strict versioned community battle-animation text/PNG package adapter (issue #62) |
| [`portrait_packages.md`](portrait_packages.md) | Current | Strict formatted portrait package, generated FaceData registration, and tester procedure (issue #63) |
| [`tmx_map_layouts.md`](tmx_map_layouts.md) | Current | Fail-closed TMX map-layout adapter and Tiled authoring boundary (issue #64) |
| [`localization.md`](localization.md) | Current | Stable locale/message IDs, authoring, prefs/UI, budgets and matrices (issue #18) |
| [`patch_release.md`](patch_release.md) | Current | Trusted BPS-only maximal-profile artifact and local verification contract (issue #49) |
| [`ui_presentation_registry.md`](ui_presentation_registry.md) | Current | Typed battle-animation, chapter/screen manifest, and utility-preference registries (issues #41/#43/#44) |
| [`test-cases/README.md`](test-cases/README.md) | Current | Indexed tester-facing case catalog, template, and backfill lifecycle (issue #54) |
| [`test-cases/template.md`](test-cases/template.md) | Template | Reusable tester-facing procedure template (issue #54) |
| [`test-cases/foundation.md`](test-cases/foundation.md) | Current | Catalog/checker foundation procedure (issue #54) |
| [`test-cases/core-framework.md`](test-cases/core-framework.md) | Current | Core framework and authoring procedures, profiles, controls, and automation (issue #57) |
| [`test-cases/optional-gameplay.md`](test-cases/optional-gameplay.md) | Current | Optional gameplay procedures, profiles, controls, and automation (issue #55) |
| [`test-cases/presentation-audio-utility.md`](test-cases/presentation-audio-utility.md) | Current | Presentation, audio, and utility procedures, profiles, controls, and automation (issue #58) |
| [`test-cases/asset-authoring.md`](test-cases/asset-authoring.md) | Current | Source-asset adapter procedures, controls, runtime evidence, and cleanup (issue #62) |
| [`test-cases/workflow-governance.md`](test-cases/workflow-governance.md) | Current | Trusted/immediate-push, WIP visibility, CI-wait, manual-handoff, stacked-CI, workflow-pilot, metadata-edit, and safe completed-worktree cleanup procedures (issues #93/#169/#171/#176/#177/#207/#208) |
| [`test-cases/debugtools.md`](test-cases/debugtools.md) | Current | Cursor-unit editor and bounded music-preview procedures, teardown negatives, and save-neutral evidence (issues #125 and #126) |
| [`test-cases/localization.md`](test-cases/localization.md) | Current | Localization and locale-persistence procedures, semantic runtime mappings, and negative controls (issue #56) |
| [`test-cases/patch-release.md`](test-cases/patch-release.md) | Current | Trusted BPS artifact validation/application and malformed-input procedures (issue #49) |
| [`test-cases/autoplay.md`](test-cases/autoplay.md) | Current | Controller smoke, bounded semantic termination, one-phase Charge delegation, and accelerated-fidelity procedures (issues #85/#86/#87/#88) |
| [`test-cases/codeql-alerts.md`](test-cases/codeql-alerts.md) | Current | Link Arena and confirmed CodeQL alert regression procedures (issue #84) |
| [`game_localization_catalog.md`](game_localization_catalog.md) | Current | Full-game FE8U-indexed CJK catalog generation, runtime bounds, and synthetic link gate |
| [`documentation-inventory.md`](documentation-inventory.md) | Current | Exact recognized-Markdown inventory |
| [`external-link-registry.md`](external-link-registry.md) | Current | Offline URL ownership/status coverage |
| [`upstream-porting.md`](upstream-porting.md) | Current | Canonical upstream drift tooling (issue #12) |
| [`issue-resolution-policy.md`](issue-resolution-policy.md) | Current, general policy | Issue evidence / review / legal-boundary governance; the development workflow skill overrides conflicts |
| [`dump_extraction_plan.md`](dump_extraction_plan.md) | Archival | Raw-blob-to-source extraction workflow |
| [`lz_suffix_diagnostic.md`](lz_suffix_diagnostic.md) | Archival | Hidden-asset diagnostic technique |
| [`tsa_audit.md`](tsa_audit.md) | Archival, point-in-time | Tilemap data audit snapshot |
| [`banim_asset_extraction.md`](banim_asset_extraction.md) | Archival | Battle-animation asset extraction |
| [`Banim_AnimScr_Decompilation_Report.md`](Banim_AnimScr_Decompilation_Report.md) | Archival | Battle-animation script decompilation report |
| [`Banim_TSA_Preservation_Report.md`](Banim_TSA_Preservation_Report.md) | Archival | Battle-animation TSA preservation report |

"Current" means actively maintained and expected to reflect `master`.
"Archival" means it documents a point-in-time or archival-lane-only
workflow and is not re-verified against the modern framework. "Template"
means it is intentionally unfilled scaffolding.

## Merged contracts and future release slot

Issues **#6**, **#10**, **#11**, **#13**, and **#18** have implementation
merged into the current source tree; this statement does not assert or change
their GitHub issue state. Their supported surfaces and explicit non-goals are
summarized in [`architecture.md`](architecture.md#public-extension-boundaries)
and [`framework-support.md`](framework-support.md#merged-framework-contracts).

Issue **#9** remains future work. Only the current issue-resolution policy and
an explicitly unfilled migration template exist; do not infer release
automation or a current release process from either.
