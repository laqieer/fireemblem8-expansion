# Issue #101 documentation and workflow-policy evidence

**Status: local candidate evidence only.** This report records the scoped
documentation/workflow-policy migration; it does not change issue state,
publish a branch, or claim CI evidence.

## Frozen contract

- **Classification:** confirmed test-evidence maintenance. The public build
  commands and Markdown table are stable documentation contracts, while
  historical count wording is not.
- **Scope:** replace only the issue #99 documentation/workflow-policy audit
  candidates for #101. #102 through #107 are explicit non-goals.
- **Dependencies/conflicts:** #100 supplies `TC-TEST-QUALITY-001`'s semantic
  evidence policy; #29 already removed the Full Matrix workflow and its
  duplicate guard. There are no runtime, configuration, generated-data,
  localization, save, ROM/RAM, or archival-lane changes.
- **Rollback:** revert this one commit to restore the prior test groups.

| Audit candidate | Decision and owner | Retained evidence / negative fixture |
| --- | --- | --- |
| Quickstart stale object-count loop | Merge: dynamic build-count documentation | Parsed `make print-MODERN_*_OBJECTS` commands must exist in the live Make database; historical count forms fail structurally. |
| Framework-support stale object-count/ABI loop | Merge: linked-target documentation | Parsed Build targets table requires `MODERN_ABI=aapcs` for every linked target; changing the ELF row to `apcs-gnu` fails. |
| Framework-support no-stale scan | Merge: linked-target documentation | The same parsed table contract replaces the duplicate whole-document phrase scan. |
| Issue #5 stale-status loop | Keep: issue-status stale-phrase guard | Its external issue-status/gate-history requirement and adversarial phrases are independent of the build-document contract. |
| Issue #5 framework-support no-stale scan | Merge: linked-target documentation | The parsed table check owns the current framework-support build contract. |
| Numeric fenced object-count fixture | Merge: object-count structural checker | A fenced resolved `MODERN_*_OBJECTS` value fails. |
| Spelled fenced object-count fixture | Merge: object-count structural checker | A fenced spelled source/object count fails in the same fixture. |
| Full Matrix structural guard | Retired upstream by #29 | The workflow and both duplicate release-evidence guards no longer exist; this candidate is out of the current tree. |

## Tester-facing and local evidence

`TC-TEST-QUALITY-001` exercises the policy requirement. The baseline seven
active candidate selectors passed in 0.502 seconds (0.76 seconds wall);
the four retained/replacement selectors passed in 0.408 seconds (0.55 seconds
wall). The semantic contract accepts table-row reordering, rejects a linked
`apcs-gnu` mutation and malformed table header, and the combined fenced
fixture rejects both numeric and spelled count claims.

## Delivery boundary

This candidate is based on `origin/master` commit
`58767aa3e693bfb2f3cc82ed8135280c00e65017` with no stack parent or dependent
issue. It requires only focused documentation/workflow checks; no Build,
Full Matrix, ROM, or remote validation is part of this local delivery.

## Review-size preflight

Against the immediate `origin/master` base: `docs/documentation-inventory.md`,
this report, `scripts/check_docs.py`, and `test_check_docs.py`; 293 additions,
203 deletions, 496 total changed lines. This is one independent #101 contract.
