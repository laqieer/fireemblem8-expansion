# Sealed platform-runtime storage

`scripts.validation_ownership.runtime_image` provides complete immutable
platform bodies independently of execution or source authority. This is the
storage prerequisite of #275; native compiler admission, generated-source
custody and original end-to-end qualification are not supplied by this API.

Two intended uses are large Python/platform images and GCC frontend images.
The latter can exceed the source-file allowance. Consumers must first issue
the exact trusted path and purpose through their existing discovery mechanism;
storage does not make an arbitrary library, compiler, source or metadata path
trusted. No host mount, native execution grant, runtime registry or persistent
protocol version is added.

## API and ownership

Construct `RuntimeImage(Path, ProbeBudget)` from a canonical trusted regular
file. Capture rejects nonregular, non-root-owned, group/other-writable or
special-mode backing, and requires stable complete source identity. The body
is copied into an owned memfd with WRITE/GROW/SHRINK/SEAL seals. Complete
backing, digest and seal checks govern reads and materialization.

`len(image)`, bounded byte slices and `image_digest(image)` expose the complete
captured body. `materialize_image(destination, image)` streams it through
bounded workspace; the same helper supports an existing byte body.
That compatibility byte-body route does not own a `ProbeBudget`: its caller
remains responsible for the byte body's storage, destination and deadline
accounting. It is not a substitute for budget-owning `RuntimeImage` capture.
`image.close()` retires its descriptor. `close_images(cache)` closes owned
image values and clears the cache using the existing all-actions cleanup.
The consumer owns the image and must close it on success, failure and
interruption. This is not hostile-same-UID isolation.

`RuntimeImage` capture charges snapshot storage; its digest, comparison,
streaming workspace and context metadata use the existing budget categories. Every numerical
production limit remains unchanged. Source and generated-file limits still
apply to their own consumers; they are not platform-image quotas. A platform
body larger than its source-file allowance succeeds only while its real
snapshot and aggregate allowances suffice. Exhaustion is explicit, terminal
where the caller's existing public boundary requires it, and never refunded
or reset.

Materialization completes short writes and rejects invalid/no progress.
The destination must be the caller's private owned path, with no untrusted
leaf symlink or concurrent replacement; this helper is not a pathname security
or publication service.
After opening a destination, failure removes that owned path. A pre-open
quota/open refusal leaves an existing destination untouched. This is not
atomic replacement or preservation of old contents after a successful open.
Denied removal is explicitly reported with the actual remaining prefix.
Primary read/write/deadline/backing failures remain primary even when close
or unlink also fails; cleanup diagnostics retain every secondary failure.

## TC-OWNERSHIP-SEALED-PLATFORM-STORAGE-001

Linked issue: #279. Supported profile: Linux host source tooling with Python3,
`/proc`, memfd/seal support, GNU Make, Python and the standard host GCC.
Start from a clean source checkout; no ROM or game save is required.

1. Run
   `python3 -B -m unittest scripts.validation_ownership.tests.test_platform_image -v`.
   The same module is wired into the existing `ownership-probe-test` owner.
2. Capture the actual GNU Make and Python bodies. Compare full bytes and
   digests, inspect real kernel seals, and attempt real WRITE/GROW/SHRINK
   mutations. Every mutation must fail while the complete body remains valid.
3. Discover the actual host cc1 through the trusted host driver's ordinary
   `-print-prog-name=cc1` query. Capture and materialize its complete body with
   the source-file allowance below the actual extent, never above its default.
   Exact bytes/digest/seals and snapshot charges must agree. Set the snapshot
   allowance one byte below that real extent; capture must refuse and close
   the actual source descriptor without inventing a smaller successful body.
4. Exercise the provider's actual capture/fdopen handoff, closed/corrupted
   backing, read/write interruption and short/invalid-progress controls.
   Require complete successful output, or explicit refusal with removal of
   the opened destination and retirement of owned FDs.
5. Exercise pre-open quota/open refusal, failed replacement, combined
   operation-plus-close/unlink errors and close failure after otherwise
   successful output. Compare original exception identity, all cleanup
   diagnostics, actual destination contents/state and real FD closure.
   If removal itself is denied, require the explicitly reported remaining
   prefix rather than a success-shaped fallback.
6. Combine actual constructor read, backing-write and deadline failures with
   source-close and backing-close failures. Preserve the original exception
   identity and both cleanup diagnostics, and verify both real descriptors
   are closed. After otherwise successful capture, a source-close failure
   itself is primary and still retires the backing descriptor.

All assertions are deterministic host automation. Each case owns and cleans
its temporary paths and descriptors; reset between cases through the test's
ordinary cleanup. The historical exact pre-fix materialization and combined
teardown operations frozen under #275 remain the component negative controls.
They are not evidence of native Make lineage or original qualification.

Dependencies: existing `ProbeBudget` and lifecycle cleanup from the merged
native foundation (#274). Dependents: #275, #270, #180 and #186. The provider
has no independent profile conflicts; session/cache/root/alias/permission and
archive integrations remain the dependent owner's responsibility.
Default execution routes and modern debug/release/archival behavior are
unchanged. ROM, RAM, save/migration, generated game data and localization
impact: none. Rollback is an ordinary revert before dependent integration;
required dependents must not merge before this API is delivered.
