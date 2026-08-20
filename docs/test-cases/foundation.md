# Catalog foundation cases

## TC-CATALOG-001: Tester-case catalog foundation

- **Feature / originating issue:** `tester-case-catalog` /
  [issue #54](https://github.com/laqieer/fireemblem8-expansion/issues/54).
- **Supported configuration or artifact:** clean source checkout with Python
  3; no ROM, emulator, artifact, optional feature profile, or save state.
- **Prerequisites and clean starting state:** run from the repository root
  with `docs/test-cases/` and `scripts/check_docs.py` present.

### Actions

1. Read the [catalog index](README.md), [case template](template.md), and
   [`registry.json`](registry.json).
2. Run `python3 -m unittest scripts.docs_check_tests.test_check_docs -v`.
3. Run `python3 scripts/check_docs.py --check`.

### Expected result

The registry exposes distinct feature and case records, this procedure is
addressable through its stable ID, and both commands accept the valid
foundation catalog.

### Negative control

The focused fixtures reject malformed or duplicate IDs, unknown feature/case
ownership, missing document/anchor/reference links, empty or success-shaped
required fields, unnamed automation evidence, an excluded/retired feature
without a reason, and a missing expected feature in future `complete` mode.

### Interactions and save compatibility

This depends on issue #53's tester-facing-case contract. It stays in
explicit foundation/backfill-pending mode until #55 through #58 supply their
feature-family entries. It has no runtime feature interaction and changes no
save bytes, migration, or compatibility epoch.

### Automation

`python3 -m unittest scripts.docs_check_tests.test_check_docs -v` exercises
the schema's positive and negative fixtures. `python3 scripts/check_docs.py
--check` validates the repository registry with the existing documentation
checker. No manual-only criterion applies.

### Cleanup and limitations

No cleanup is required. This case validates the generic catalog/checker seam;
the final fail-closed all-current-feature assertion is intentionally deferred
until the #55 through #58 backfills land.
