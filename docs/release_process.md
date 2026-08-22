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

## No publishing side effects

This repository has no publisher. Local archive commands only run technical
checks. Any future publishing tooling is outside this process and must not be
inferred from its output.
