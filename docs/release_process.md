# Release process

Release rehearsal is a read-only technical validation of an exact candidate.
Its commands return ordinary success or failure from concrete checks; they
never create tags, releases, uploads, comments, or protected-environment
changes.

## Candidate authority

The exact candidate Git tree is the only authority for source membership,
Git modes, and gitlinks. `scripts/release_rehearsal/candidate_tree.py` reads
that tree directly. The source archive includes exactly its non-gitlink
members, and `source_guard.py` rejects unsafe paths, files, links, and
archives before output is written.

`.gitmodules` and the candidate-tree gitlink are the complete submodule
integrity contract. `submodule_binding.py` requires a one-to-one path and
HTTPS URL declaration for each gitlink. The gitlink commit is read from the
candidate tree when a rebuild needs it; no duplicate pin is committed.

No legal-review metadata, path-to-fact mapping, content ledger, generation
basis, source allowlist, export exclusion, or external-attestation mechanism
participates in local technical checks.

## Integrity boundaries retained

The removed provenance layer was redundant with Git history. These independent
boundaries remain:

- the exact candidate commit supplied to CI and release commands;
- configuration and save-compatibility fingerprints;
- external source, dependency, action, and extracted-content integrity;
- source/artifact safety guards;
- format CRCs and checksums;
- deterministic double-built source archive SHA-256 values; and
- release-time artifact hashes and embedded identity checks when an artifact
  is produced.

The modern ROM is not compared against a whole-source, object, or ROM identity
hash. `asmdiff.sh` remains an explicit archival investigation tool.

## Commands

`make release-check` validates the technical candidate report and exits
successfully only when its concrete checks pass.

`make release-rehearse` performs the deterministic archive rehearsal and a
rebuild attempt when the locally initialized submodule is clean, bound to its
candidate-tree gitlink, and locally readable. A failed rebuild remains a
technical blocker; a rebuild that is not requested is reported separately and
does not recreate the removed legal-policy block.

`make release-candidate-tree-check` validates the exact target-tree
path/mode/gitlink set. It is a local technical command; no dedicated
release-check workflow is required.

## Consolidated static-contract evidence

Issue #106 resolves the 22 release/provenance/archive candidates from the
pre-#29 audit without reinstating the retired provenance ledger, action-pin
catalog, or standalone release workflow. The test names below are audit
history, not a public interface or a new source-text assertion surface.

| Audited candidates | Count | Current requirement owner and retained evidence | Mutation fixture |
| --- | ---: | --- | --- |
| `CheckAllowlistCompletenessTests.test_exact_match_is_clean`; `CheckAllowlistCompletenessNonGitTests.test_exact_match_is_clean` | 2 | `source_guard.py` exact candidate-member checks, exercised by `test_source_guard.py` tree, archive-member, and Git-tracked-member cases | A bare directory entry or unlisted sibling is rejected; a clean exact member set passes. |
| `CheckGitlinkPinsTests` (both cases); `AssignRootTests.test_unassigned_path_is_actionable`; `GenerateExactEntriesTests.test_unassigned_path_is_actionable`; `CheckBlobIdentityTests` (both cases); `RepositoryStateTests` (both cases) | 8 | `candidate_tree.py`, `git_source.py`, `gitmodules.py`, and `submodule_binding.py`, exercised by `test_candidate_release.py`, `test_git_source.py`, and `test_gitmodules.py` | Comment and blank-line-only `.gitmodules` formatting preserves the parsed binding; a candidate gitlink with no matching declaration fails closed. |
| `GoodWorkflowTests.test_no_violations`; `FullMatrixStructuralCommandContractTests.test_real_full_matrix_contract_is_clean`; `VariableCommandAssemblyTests` (three cases); `CommandSubstitutionAndTrackedAssignmentTests` (three cases); `BacktickCommandSubstitutionTests.test_real_workflow_remains_clean`; `MultiVariableReadTrackingTests.test_real_workflow_remains_clean`; `ProcessSubstitutionTests.test_real_workflow_remains_clean`; `PermissionsSemanticEscapeDecodingTests.test_unescaped_legitimate_workflow_remains_clean` | 12 | The consolidated Build workflow contracts in `tests/workflows/test_build_ci_checkout.py` and `tests/workflows/test_build_ci_topology.py`; #108 owns that cross-workflow surface | A comment-only workflow change remains valid; a merge-ref fallback, missing worker, or lost summary dependency fails. |

The historical candidates are therefore fully resolved as 2 allowlist,
8 candidate-tree/provenance, and 12 workflow-contract cases. The current
release path derives paths, modes, and gitlinks directly from the selected
tree at verification time. It deliberately does not compare a whole source
tree, object set, or ROM to a committed identity hash.

### TC-TEST-QUALITY-001: Preserve release-contract meaning while consolidating evidence

- **Feature / originating issue:** release-contract evidence consolidation /
  [issue #106](https://github.com/laqieer/fireemblem8-expansion/issues/106).
- **Supported configuration or artifact:** a clean Git checkout with Python,
  GNU Make, and the repository's tracked submodule declaration. No ROM,
  legal base, archive upload, or publishing credential is required.
- **Prerequisites and clean starting state:** start at the repository root on
  the candidate being checked. Do not create or copy a ROM, BPS file, or
  historical provenance manifest.

### Actions

1. Exercise the exact candidate, source-guard, and submodule-binding
   contracts:

   ```bash
   python3 -m unittest \
     scripts.release_rehearsal.tests.test_candidate_release \
     scripts.release_rehearsal.tests.test_gitmodules \
     scripts.release_rehearsal.tests.test_source_guard -v
   ```

2. Exercise the consolidated Build workflow owner:

   ```bash
   python3 -m unittest \
     tests.workflows.test_build_ci_checkout \
     tests.workflows.test_build_ci_topology -v
   ```

3. Run one exact-candidate archival rehearsal:

   ```bash
   make release-rehearse RELEASE_TARGET_SHA=<exact-40-character-candidate-sha>
   ```

### Expected result

The semantic-preserving `.gitmodules` formatting fixture remains clean. A
candidate/declaration mismatch, an unlisted source member, a changed
event-derived checkout, or a missing Build-summary dependency fails with an
actionable error. The rehearsal derives its archive membership from the
requested candidate and reports deterministic archive evidence without
creating a release or publishing an artifact.

### Negative control, interactions, and automation

The mismatch and workflow-corruption fixtures are the required negative
controls; their passing counterparts prevent a success-shaped failure check.
This case depends on #29's Git-tree authority and #100's meaningful-evidence
policy. #108 owns the cross-workflow contract named above; this release case
does not duplicate that implementation. There are no save-format,
generated-data, localization, modern/archival build-lane, or ROM/RAM budget
changes. All deterministic criteria are covered by the listed host selectors;
the source archive rehearsal is read-only and is the only artifact check.

## No publishing side effects

This repository has no publisher. Local archive commands only run technical
checks. Any future publishing tooling is outside this process and must not be
inferred from its output.
