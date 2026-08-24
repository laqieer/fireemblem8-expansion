# Tester-facing case catalog

This directory is the authoritative, indexed catalog of stable
tester-facing procedures. It complements deterministic host and ROM
automation: a tester can follow the documented setup, actions, and
expected result without reverse-engineering a unit test or scenario, while
every deterministic assertion remains mapped to automation.

The machine-readable source is [`registry.json`](registry.json). It has
separate `features` and `cases` arrays:

- A feature identifies one shipped capability, its originating issue URLs,
  authoritative reference document, lifecycle status, and required case IDs.
- A case identifies one stable `TC-<AREA>-NNN` ID, its owning feature,
  originating issue URLs, Markdown procedure and anchor, supported source
  profiles/artifacts, required procedure data, and either named automation
  evidence or an explicit `manual_only_reason` for a visual, audio, or UX
  criterion that cannot be asserted deterministically.

Case areas start with an uppercase letter, while feature IDs start with a
lowercase letter. Their hyphen-separated segments must be non-empty and use
only the corresponding alphanumeric character set.
Stable IDs are never silently renumbered or reused. Add a case from
[`template.md`](template.md), then add the corresponding feature/case
records to `registry.json` in the same change. The existing
[`scripts/check_docs.py`](../../scripts/check_docs.py) checker validates the
registry, links, anchors, required fields, automation evidence, exclusions,
and coverage lifecycle.

## Coverage lifecycle

The registry is in **complete** mode. The #55 optional-gameplay, #56
localization, #57 core-framework, and #58 presentation/audio/utility backfills
all supply their current feature records, case links, and automation mappings.
`coverage.expected_feature_ids` enumerates every current feature, while
`deferred_issues` is empty.

Complete mode is a living shipped-feature inventory, not a snapshot of those
four backfills. Every subsequently shipped named contract must add its feature
ID, stable case, procedure, and automation mapping in the same change.

In complete mode the checker fails if an indexed feature is absent, is not
current, or lacks a required owned case. A retired or excluded record is not
coverage: it requires an explicit reason and cannot satisfy a complete-mode
current feature.

The localization and locale-persistence procedures are
[`TC-LOCALIZATION-001` through `TC-LOCALIZATION-008`](localization.md).
The volatile debug save-fixture procedure is
[`TC-DEBUGSAVE-001`](debug-save-fixtures.md#tc-debugsave-001-volatile-save-fixture-isolation-and-recovery).

## Running catalog checks

Run the focused schema fixtures and the repository checker from the root:

```bash
python3 -m unittest scripts.docs_check_tests.test_check_docs -v
python3 scripts/check_docs.py --check
```

The foundation procedures are
[`TC-CATALOG-001`](foundation.md#tc-catalog-001-tester-case-catalog-foundation)
and
[`TC-TEST-QUALITY-001`](foundation.md#tc-test-quality-001-meaningful-test-evidence-policy-rejects-semantic-mutations).
The core framework and authoring procedures are in
[`core-framework.md`](core-framework.md); optional gameplay procedures are in
[`optional-gameplay.md`](optional-gameplay.md); presentation, audio, and
utility procedures are in
[`presentation-audio-utility.md`](presentation-audio-utility.md); and
localization procedures are in [`localization.md`](localization.md). The
optional HQ PCM mixer procedure is
[`TC-AUDIO-HQMIX-001`](audio.md#tc-audio-hqmix-001-hq-pcm-mixer-produces-bounded-stereo-output).
Community asset-adapter procedures are in
[`asset-authoring.md`](asset-authoring.md), and repository delivery-policy
procedures are in [`workflow-governance.md`](workflow-governance.md).
Issue #84's security/correctness procedures are
[`TC-SIO-084`](codeql-alerts.md#tc-sio-084-bounded-link-arena-transfer) and
[`TC-CODEQL-084`](codeql-alerts.md#tc-codeql-084-remaining-confirmed-alert-boundaries).
The bounded debug chapter/skirmish procedure is
[`TC-DEBUGTOOLS-PROTOTYPE-001`](debugtools.md#tc-debugtools-prototype-001-bounded-chapter-and-skirmish-selector).
The cursor-selected unit debug editor procedure is
[`TC-DEBUGTOOLS-PROTOTYPE-003`](debugtools.md#tc-debugtools-prototype-003-cursor-selected-bounded-unit-inspectoreditor).
The bounded debugtools music-preview procedure is
[`TC-DEBUGTOOLS-PROTOTYPE-004`](debugtools.md#tc-debugtools-prototype-004-preview-bounded-music-and-restore-its-owner).
The typed State and Engine diagnostics procedure is
[`TC-DEBUGTOOLS-DIAGNOSTICS-001`](debugtools.md#tc-debugtools-diagnostics-001-typed-state-and-engine-diagnostics).
