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

The initial registry is intentionally in **foundation** mode. It validates
the catalog's own case but does **not** claim that every current shipped
feature is covered. The feature-family backfills own that work:

| Issue | Backfill family |
| --- | --- |
| [#57](https://github.com/laqieer/fireemblem8-expansion/issues/57) | Core framework and authoring |
| [#55](https://github.com/laqieer/fireemblem8-expansion/issues/55) | Optional gameplay |
| [#58](https://github.com/laqieer/fireemblem8-expansion/issues/58) | Presentation, audio, and utility |
| [#56](https://github.com/laqieer/fireemblem8-expansion/issues/56) | Localization and locale persistence |

Those issues add their reference-document links and records. The final
backfill changes `coverage.mode` to `complete`, removes
`deferred_issues`, and supplies the explicit current shipped-feature index.
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

The foundation procedure is [`TC-CATALOG-001`](foundation.md#tc-catalog-001-tester-case-catalog-foundation).
The optional audio module procedure is
[`TC-AUDIO-HQMIX-001`](audio.md#tc-audio-hqmix-001-hq-pcm-mixer-produces-bounded-stereo-output).
