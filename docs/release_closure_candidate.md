# Historical issue #9 closure record

This file preserves only the historical role of issue #9: it introduced
read-only release rehearsal, deterministic source archives, source/artifact
guards, immutable action pins, and exact candidate binding.

The former human-provenance, legal-review, source-allowlist,
export-exclusion, generation-basis, and external-attestation layer was removed
by issue #29. Those historical controls are not current release requirements.
Current behavior is defined by [release_process.md](release_process.md):
candidate paths, modes, and gitlinks come directly from the exact Git tree,
submodule URL binding comes from `.gitmodules`, and technical eligibility never
publishes anything.

The retained workflow guard rejects `defaults.run.shell` and required-step
shell overrides even when actionlint accepts them: `true {0}`, `bash -n {0}`,
`cmd`, and `pwsh` are not accepted execution contracts.
