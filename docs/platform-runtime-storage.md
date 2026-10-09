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
Destination ownership begins at descriptor acquisition, before stream wrapping.
A refused open leaves existing contents untouched; wrapping and later failures
close the acquired descriptor and remove the owned destination, preserving the
primary exception and explicit diagnostics if cleanup itself fails.
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
   Inject destination stream-wrapping failure after successful descriptor
   acquisition for both sealed and byte bodies, including interruption and
   denied unlink; require closed FDs and removal or explicit retained-state
   diagnostics.
   If removal itself is denied, require the explicitly reported remaining
   prefix rather than a success-shaped fallback.
6. Combine actual constructor read, backing-write and deadline failures with
   source-close and backing-close failures. Preserve the original exception
   identity and both cleanup diagnostics, and verify both real descriptors
   are closed. After otherwise successful capture, a source-close failure
   itself is primary and still retires the backing descriptor.
7. Supply controlled source-stat observations around the actual GNU Make
   source descriptor: each nonregular, empty/negative-size, non-root-owned,
   group/other-writable and special-mode predicate must refuse before backing
   allocation. Independently change every identity field at descriptor and
   pathname post-capture observations; each must refuse and retire both real
   descriptors. These are controlled observation inputs, not claimed physical
   mutations of trusted host files.
8. Compare actual slices at zero, workspace-minus-one, exact workspace,
   negative/tail/clamped/empty and explicit-step-one boundaries. Unsupported
   scalar/step keys and workspace-plus-one/full-body slices must refuse before
   reading. Short underlying reads must refuse incomplete results. Derived
   control exhaustion and elapsed-deadline inputs must refuse before reads
   and retain owned-body cleanup. Closed backing must also refuse.

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

## Finite review-subject binding

The existing `review_subjects.BINDINGS` entry for
`TC-OWNERSHIP-SEALED-PLATFORM-STORAGE-001/sealed-platform-storage` supplies
15 obligations: all five wire roles, four lifecycle roles, two resource roles
and four generated-owner roles. The accepted findings map individually to
`owners:probe-inventory`, `validators:source-admission`,
`stale-bindings:source-identity` and `enabled:bounded-slices`. The remaining
roles cover complete capture/materialization, sealed replay, FD handoff,
primary-error preservation, no budget reset, retirement, caller-owned byte
compatibility, generated Make selection and its full-only workflow consumer.

Select the reviewed binding at an explicit immutable tool revision using the
existing `ReviewTools.members` and `ReviewTools.run_obligations` APIs. The
existing `_stage` route materializes each origin/candidate from `GitTree` and
runs the closed probes in its credential-free child. It does not import the
candidate into the coordinator collector. Every obligation carries the shared
execution closure and actual Git blob identities: provider, provider tests,
budget/lifecycle/channel/authority imports and package roots, foundation Make
file, native test inventory sources, owner tests, workflow and the existing
finite workflow parser/condition functions. Other native suites are parsed as
inventory inputs, not imported or executed by this binding. Dynamic import
attempts cannot reach them through ordinary Python module lookup: staging moves
their exact source bytes to `build/platform-inventory/<original-path>.source`,
outside the importable module paths. The parsers consume those relocated bytes
while observations retain the original Git paths and object bindings. This is
physical staging separation, not a claim of hostile same-UID isolation.
A changed import
closure, identity field set or unrecognized mutation guard blocks; no arbitrary
caller test, evaluator, service, budget or execution permission is introduced.

Coverage findings are **not historical runtime defects**. For each admission,
post-capture identity and oversized-workspace predicate, the worker first runs
the actual source revision's complete provider suite unchanged. It then removes
only that production predicate through its finite AST model and reruns that
same original suite. The coverage contract requires 9, 16 and 3 independent
input-assertion failures respectively, without errors, skips or expected
failures. Admission observations cover nine early refusals; identity
observations independently change all eight fields at both FD and path checks.
The unchanged current suite additionally executes zero/N-1/N, negative,
clamped, empty and step-one slices, scalar/step and N+1/full-body refusals,
short reads, quotas, deadline and closed-body controls. A surviving mutation
is reported as a **coverage gap**, explicitly retaining that the unmutated
original suite passed. A failed baseline or unavailable execution never
qualifies as a successful coverage observation.

The initial missing owner inventory at
`deb861f4acfd8f26e54501a0362a8a94d148727a` is reproduced by parsed Make
selected-versus-expected module equality. This is a closed dedicated probe of
the original inventory and real generated command, not a claim to have run
the entire original workflow-test import closure. The coverage origin at
`20ce99cc816b85647569f0a6ba4600326870ddc8` has eight original provider tests;
actual original-suite runs survive all three semantic guard removals. The
current thirteen-test provider suite kills them with 9/16/3 input failures.
Those results establish missing regression coverage, not incorrect old
admission, identity or slice runtime behavior.

Historical #275 operation controls remain separate: the session-only
comparison adapts exact `3a32` materialization operations, `7c522` destination
ownership and `c8d6117` constructor operations to the extracted provider,
producing 22/12/3 expected failures without errors; the exact `deb861f4`
owner tests produce one selected-versus-expected failure. These are explicit
component adaptations, not full exact-origin-tree qualification. Neither
these controls nor this binding complete the held #275/#270/#180/#186 work.
Exact-head independent/remote review, security, complete candidate/master
Builds and every existing completion gate remain unchanged.

Focused binding regression:
`python3 -B -m unittest scripts.workflow_pilot.tests.test_platform_storage_review_subject -v`.

The binding regression module is included by the existing registered review-family
`test_*review*.py` discovery. Its parsed owner model rejects module-level `load_tests`
and module `__getattr__`/`__dir__` bindings and wildcard imports instead of executing
custom selection or enumeration hooks.
Actual module selection is compared with the parsed provider inventory, including
hooks introduced dynamically through the module namespace.
It exercises the real staged route, all finite roles, coverage-only origins,
owner omission, missing sibling/stale-tool refusal, malformed execution
closure, runtime regression and unavailable original-suite execution.

For the provider's original directly launched CLI review, retain native task
`call_fyfIDetIpYPYd3XEg7d6KtNs`, reviewer
`c7bfd6a2-7afc-44f0-8f4c-0be5f54d4eba`, observed original head
`ba125e3d90279c21eaaa9e81554b4821d0045dc5` and original report/findings.
Native start/completion chronology and nine observed candidate paths remain
session evidence, not reconstructed historical `begin`/`finish` lease fields. Total runtime
file count remains unknown; the original broader advertised tool set is not
relabeled as an enforced read-only allowlist. The byte-budget finding is
retained and explicitly rejected against the frozen caller-owned bytes
contract, not erased or converted into a clean original report. Later scoped
reviews, accepted remote findings and exact current-head gates remain separate.
These historical observations do not establish the mandatory independent-review
admission facts; retain that precise hold rather than reconstructing a lease.
