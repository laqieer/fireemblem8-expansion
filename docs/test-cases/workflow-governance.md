# Workflow-governance cases

These source-only procedures cover the repository's agent delivery policy.
They exercise documented orchestration contracts without dispatching a
workflow, using credentials, or changing ROM behavior.

## TC-WORKFLOW-CI-WAIT-001: Keep CI waiting centralized and trusted pushes owner-scoped

- **Feature / originating issue:** `workflow-governance` /
  [issue #93](https://github.com/laqieer/fireemblem8-expansion/issues/93).
- **Supported configuration or artifact:** clean source checkout with
  Python 3; no GitHub token, active pull request, workflow run, ROM, or
  emulator is required.
- **Prerequisites and clean starting state:** start at the repository root and
  leave the mirrored development-workflow policy files unchanged.

### Actions

1. Inspect the trusted-push and CI-waiting sections in
   `.github/skills/development-workflow/SKILL.md`.
2. Run
   `python3 -m unittest scripts.docs_check_tests.test_development_workflow_skill -v`.
3. Confirm the focused suite exercises its required-policy, forbidden-policy,
   and deliberately unbounded watcher negative fixtures.

### Expected result

The mirrored policy requires implementation subagents to validate and commit
locally, then return for a trusted owner-context push. Dispatch records the
exact candidate SHA/run and returns; exactly one bounded direct watcher owns
each active run; Copilot review monitoring runs concurrently with Build;
reasoning inspection starts only after a terminal result; superseded runs are
cancelled; and post-merge monitoring remains nonblocking. The former Full
Matrix gate is absent because the combined Build owns the complete candidate
gate.

### Negative control

Removing a required dispatch-and-return, single-watcher, concurrent-review,
terminal-only inspection, stale-run cancellation, trusted-push, or combined
Build rule makes the focused suite fail. The suite also rejects stale
implementation-agent push ownership, duplicate/unbounded watcher examples,
privileged `pull_request_target`, weakened approvals, and any restored Full
Matrix wording.

### Interactions and save compatibility

The policy depends on GitHub CLI and the existing shell runtime when used for
real delivery. It conflicts with duplicate polling agents, repeated wakeups,
stale candidate evidence, implementation-agent pushes, and unbounded watcher
loops. The source-only case changes no save, generated data, localization,
ROM/RAM, debug/release, or archival behavior and needs no feature gate.

### Automation

`python3 -m unittest scripts.docs_check_tests.test_development_workflow_skill -v`
validates the mirrored policy, bounded watcher example, trusted-push
ownership, concurrent monitoring, stale cancellation, and combined-Build
replacement of the retired Full Matrix.

### Cleanup and limitations

No cleanup is required. The test validates repository policy text and its
fail-closed fixtures; it does not dispatch or wait for a live GitHub workflow
and does not grant push credentials.

## TC-WORKFLOW-MANUAL-HANDOFF-001: Surface actionable manual testing and resume automatically

- **Feature / originating issue:** `workflow-governance` /
  [issue #169](https://github.com/laqieer/fireemblem8-expansion/issues/169).
- **Supported configuration or artifact:** clean source checkout with Python
  3; no GitHub token, active manual hold, ROM, or emulator is required to
  validate the protocol.
- **Prerequisites and clean starting state:** start at the repository root with
  [the canonical JSON contract](../../.github/manual-testing-handoff.json),
  development-workflow skill, contributor guide, this case, and registry
  unchanged.

### Actions

1. Parse `.github/manual-testing-handoff.json` and validate every required key,
   value, enum, boolean, target, comment field, and the separately identified
   positive/control artifact roles with deterministic emulator screenshot or
   synchronized emulator A/V evidence. Validate the comment's exact
   `@laqieer` mention, stable case ID, full Git SHA, artifact paths and SHA-256
   values, nonempty text, numbered steps, and true merge/closure holds.
2. Exercise the positive issue-only, one-PR, and multiple-open-PR queue shapes,
   using an independent GitHub-linked PR relationship map. Exercise malformed
   item kind/URL/state, label, assignee, per-item comment, relationship, and
   stale-state controls before filtering closed relationships.
3. Exercise completion cleanup with closed and superseded labeled PR history,
   separate label/assignee cleanup sets, retained independent ownership, and a
   typed result/evidence comment bound to the original case ID and commit.
   Accept only concrete same-repository comment, review, run, artifact, or
   commit-pinned blob links, plus GitHub user attachments. Verify each open PR's
   current head still matches the tested activation commit.
4. Exercise rejected evidence while the label, temporary assignee, merge and
   closure holds, and actionable state all remain active.
5. Run
   `python3 -m unittest scripts.docs_check_tests.test_development_workflow_skill -v`.
6. Open the documented queue:
   [`repo:laqieer/fireemblem8-expansion is:open assignee:laqieer label:"waiting-for-manual-testing"`](https://github.com/laqieer/fireemblem8-expansion/issues?q=repo%3Alaqieer%2Ffireemblem8-expansion+is%3Aopen+assignee%3Alaqieer+label%3A%22waiting-for-manual-testing%22).
   When it is empty, do not schedule notifications or comments.

### Expected result

The focused suite accepts the canonical JSON and its supported queue shapes.
Human guidance links to that file without duplicating machine behavior.

### Lifecycle summary

- **Eligibility:** Require a material visual, audio, or UX criterion. Require
  automation to be unreliable for that criterion.
- **Activation:** Apply `waiting-for-manual-testing` to the originating issue
  and each open implementation PR. Assign `laqieer` to those targets. Ping
  `@laqieer` in each comment.
- **Hold:** Block merge for the manual criterion. Block issue closure for the
  manual criterion.
- **Completion:** After accepted evidence, remove
  `waiting-for-manual-testing` from the originating issue and every labeled
  implementation PR. Remove the temporary `laqieer` assignment unless
  independently owned. Resume exact-candidate gates and merge automatically.
  A rejected result retains both holds and remains actionable.

### Negative control

Every leaf mutation in the structured contract fails, including a missing
artifact role/path/hash/emulator/determinism/synchronization/inspection field,
instrumented artifacts, missing or optional materiality, missing or misplaced
mention, invalid or empty numbered steps, false or mistyped comment holds,
missing per-item comments, permissive activation, wrong identifiers or targets,
disabled holds, incomplete historical cleanup, missing or mismatched completion
evidence, malformed optional booleans, duplicate artifact identities, bare or
unrelated evidence pages, malformed PR origins, stale open-PR heads, ownership
exceptions without a reason, rejected cleanup/resumption, premature
rejected-state cleanup, empty-queue notifications, and invalid independently
discovered issue/PR relationships. Reversing any lifecycle summary action or
removing this case's own subsection also fails locally.

### Interactions and save compatibility

The protocol depends on GitHub issues, pull requests, relationships, labels,
assignments, and comments when a real hold is active. Live queue contents
remain release-time evidence rather than tracked state. Cleanup history includes
every labeled PR after closure or supersession; independent ownership may retain
an assignee but never the handoff label. It changes no save, generated data,
localization, ROM/RAM, debug/release, or archival behavior.

### Automation

`python3 -m unittest scripts.docs_check_tests.test_development_workflow_skill -v`
parses the JSON contract and exercises its schema, every semantic leaf,
supported queue shapes, and fail-closed controls.

### Cleanup and limitations

This source-only case changes no remote item and cannot make the subjective
judgment itself. Live queue state and evidence remain in the relevant PR and
issue rather than this repository.
## TC-WORKFLOW-STACKED-CI-001: Run exact Build CI on a genuine stacked PR base

- **Feature / originating issue:** `workflow-governance` /
  [issue #171](https://github.com/laqieer/fireemblem8-expansion/issues/171).
- **Supported configuration or artifact:** clean source checkout with Python
  3 and the committed combined Build workflow; no GitHub token, live pull
  request, workflow dispatch, ROM, or emulator is required.
- **Prerequisites and clean starting state:** start at the repository root
  with `.github/workflows/build.yml` and the stacked-PR guidance unchanged.

### Actions

1. Run
   `python3 -m unittest discover -s tests/workflows -p "test_*.py" -v`.
2. Run
   `python3 -m unittest scripts.docs_check_tests.test_development_workflow_skill -v`.
3. Inspect the synthetic `opened`, child-head `synchronize`, `reopened`, and
   base-change `edited` pull-request fixtures, plus inline and block
   `branches` and `branches-ignore` mutations.

### Expected result

The synthetic non-master-base pull request selects the existing `host-tests`,
`build`, `extended-host-tests`, `legacy`, and fail-closed `summary` jobs. Every
candidate worker still checks out and verifies `pull_request.head.sha`. The
publisher is absent from pull-request execution, while a push to `master`
selects it and a push to any other branch selects no workflow jobs.

The child remains based on its immediate parent while that parent is open, and
exact-head Build CI and Copilot review run against that genuine base. After the
parent head changes, its update is merged into the child with a normal merge
commit; the changed child head emits `synchronize`, the child-only diff is
verified again, and fresh exact-head gates replace evidence from the older
parent tree. A parent-only push does not emit an event for the child PR. After the
parent merges, the child is retargeted once to `master`, its child-only diff is
verified, and the resulting `pull_request` `edited` event starts fresh
exact-head Build CI even when the child head SHA is unchanged. The workflow
also runs for `opened`, `synchronize`, and `reopened`, but not `closed`,
`labeled`, or other unrelated activity types. An `edited` event alone is not
sufficient evidence: Build remains bound to `pull_request.head.sha`, and the
base/tree evidence, child-only diff, and fresh gate results must all be
verified.

### Negative control

Adding inline or block `branches` or `branches-ignore` filters under
`pull_request`, removing `edited` or another required activity type, enabling
`closed`/`labeled` activity, removing either trigger, allowing non-master
pushes, exposing the patch publisher to pull requests, weakening exact-head
checkout verification, accepting an old child run after its parent head
changes, or documenting a temporary base flip solely to trigger CI makes the
focused suites fail.

### Interactions and save compatibility

This source-only contract depends on the existing combined Build jobs, exact
checkout binding, trusted owner-context pushes, and one watcher per exact run.
It conflicts with temporary base retargeting, stale evidence after a base/tree
or parent-head change, unsynchronized child branches, duplicate workflows,
duplicate matrices, weakened permissions, and PR publication. It changes no save,
generated data, localization, ROM/RAM,
debug/release, or archival behavior and needs no feature gate.

### Automation

`python3 -m unittest discover -s tests/workflows -p "test_*.py" -v` parses the
workflow, evaluates synthetic PR actions and push metadata, rejects inline and
block PR branch filters, proves a parent-only push cannot refresh the child,
checks the required child-head `synchronize` and later base-change `edited`
events, verifies exact-head checkout binding, and preserves the fail-closed
summary and publisher boundary.

`python3 -m unittest scripts.docs_check_tests.test_development_workflow_skill -v`
validates the genuine-stack workflow and rejects guidance that relies on a
temporary base flip solely to trigger CI.

### Cleanup and limitations

No cleanup is required. The case evaluates committed workflow and governance
contracts without dispatching GitHub Actions; it does not prove live service
availability or grant credentials.

## TC-WORKFLOW-PILOT-BASELINE-001: Freeze reproducible pilot baseline and decisions

- **Feature / originating issue:** `workflow-governance` /
  [issue #176](https://github.com/laqieer/fireemblem8-expansion/issues/176).
- **Supported configuration or artifact:** clean source checkout with Python
  3, Git, the immutable issue #176 fixture, and the versioned decision record;
  no GitHub token, live workflow, ROM, emulator, or game build is required.
- **Prerequisites and clean starting state:** start at the repository root with
  `.github/workflow-pilot-decisions.json`,
  `scripts/workflow_pilot/tests/fixtures/baseline.json`, and
  `scripts/workflow_pilot/reporter.py` unchanged. Remove any prior
  `build/test-artifacts/workflow-pilot` directory.

### Actions

1. Run
   `python3 -m unittest discover -s scripts/workflow_pilot/tests -p 'test_*.py' -v`.
2. Create `build/test-artifacts/workflow-pilot`, then run the documented
   reporter twice with `--repository-root .` over the committed fixture,
   decision record, and expected values, writing `first.json` and
   `second.json` in that directory.
3. Compare `first.json` and `second.json` byte for byte. Parse either file and
   verify the frozen window, identity arrays, 64-PR/9.4-hour delivery baseline,
   326 exhaustively partitioned Build outcomes, one active run, 51
   duplicate-SHA Builds, and PR #150's review/Build/base-change/close-reopen
   metrics. Confirm first-push-to-clean-review is `unavailable`, its reason is
   `historical-review-thread-events-not-collected`, `pilot_ready` is false,
   and `median_hours` is null while 34 reviews, 101 findings, and zero current
   unresolved findings remain reportable.
4. Inspect the focused suite's positive classification fixture for inclusive
   boundaries, cancellation, supersession, unchanged-SHA duplication, stack
   ancestry, generated-only work, bulk deletion, reverts, still-running work,
   cross-PR SHA-bound safety outcomes, and pilot overhead.
5. Inspect the temporary-Git-repository controls: an exact pre-review override
   tree passes; a missing decision file, missing/changed entry, digest
   mismatch, non-candidate SHA, post-review commit, backdating, reordering, and
   recomputed current-record mutation each fail.
6. Inspect the resolution controls: cumulative unresolved identities,
   resolution before/after review, absent source timing, direct synthetic
   `resolved_at`, and an untrusted source kind. Confirm only a complete
   identity-bound GitHub webhook history can produce numeric clean-review
   evidence.
7. Inspect the executable lifecycle controls. Confirm the reporter creates a
   bounded plain artifact sandbox beneath the checkout's ignored
   `build/test-artifacts/` directory, while retaining the original checkout
   and object database as separate immutable Git authority. Confirm it removes
   each allowlisted decision/fixture/reporter artifact copy in turn, runs the
   declared production reporter consumer and/or focused reporter consistency
   test, observes the named semantic failure, restores the copy, and observes
   success. Confirm automatic bounded cleanup, unchanged source artifact
   bytes, and fail-closed stale, fabricated, or fixture-command substitutions.
8. Inspect the CI authority-hydration fixture: an exact-ref shallow checkout
   in a new object-clean temporary repository initially lacks a test-created,
   force-pushed unreachable commit, and the prior all-head refspec still cannot
   recover it with lazy fetching disabled. The strict exact-commit seam
   restores it without changing `HEAD`, refs, or `FETCH_HEAD`. Separately,
   confirm production extraction returns exactly the unique commit identities
   in the committed full baseline and that setup remains absent from the 28
   local gates.
9. Mutate every combined worker with container/services/strategy/permissions/
   defaults/needs/if/advisory/environment/concurrency/uses/secrets/shell
   execution context, including spaced, quoted, escaped, tagged, explicit,
   flow, duplicate, reordered, and wrong-value forms.
10. Place a real `sitecustomize.py` that exits successfully before ordinary
    Python commands. Confirm normal startup is bypassed while all three
    `/usr/bin/python3 -I` launcher modes execute, and arbitrary modes,
    arguments, roots, or launcher/`-I` changes fail.
11. Feed the workflow mirror parser a long repeated
    `a\n        ` environment adversary and require bounded completion with
    the same accepted/rejected structural results.
12. Run `python3 scripts/check_docs.py --check`.

### Expected result

Both reporter outputs are byte-identical canonical JSON and match
`baseline_expected.json`. The active run remains active at the frozen
measurement boundary even though GitHub later completed it. Current PR and
artifact states are derived from authoritative facts and disposition history;
threshold override decisions contain no editable introduction timestamp, SHA,
diff, run conclusion, or copied current-state field. Every override is read
from the cited candidate commit and first-reviewed commit trees. Every review
SHA is an exact member of its PR's candidate-history set and available by the
review timestamp. Before these calculations, the repository origin, base,
every commit object, exact parents, timestamps, messages, PR candidate/merge
ranges, and referenced history are validated against the real object database.
Commit messages are decoded as UTF-8 from the raw commit object, with exactly
one conventional terminal LF removed; additional trailing blank lines and all
other authored whitespace remain exact authority. Every numeric schema field
requires a JSON integer that is not a boolean before any range or version
comparison, while declared boolean fields remain valid.
Every Git subprocess uses resolved `/usr/bin/git`, explicit
`--no-replace-objects -C <validated-root>`, and a constructed minimal
environment with no inherited Git redirection or configuration controls.
Repository grafts, replacement refs, and object-alternate files fail closed;
hydration alone permits the fixed bounded `origin` fetch.
Commit hydration keeps `blob:none`, then strict fixture/decision relationships
derive only override-introduction and first-review decision-record blob IDs.
Those exact blobs are fetched separately while unrelated blobs, HEAD, refs,
and FETCH_HEAD remain unchanged.
The fixture parent graph also derives a minimal maximal-tip set and exact
lightweight remote anchor names. Only a complete exact fixed-origin namespace
may hydrate commits; local refs and FETCH_HEAD remain unchanged, and remote
GC/repack retains otherwise force-pushed history. The read-only print mode
emits the same mappings twice without remote mutation.
The expected cohort seal binds normalized identities plus review, event,
timestamp, PR/SHA, and other metric relationships.
Every Build and non-Build workflow run is also rejected when its creation
precedes the repository-validated commit time of its head SHA.
The independent decision seal binds all validated PR decisions, artifact
admission/disposition semantics, authoritative artifact sources, lifecycle
proofs, review boundaries/events, and dependency associations.
PR #150's clean-review timing remains explicitly unavailable because no
authoritative historical thread events were collected; no numeric result is
emitted or eligible for pilot comparison/promotion.

The positive controls report Build/review savings beside coordination and
metadata-maintenance overhead. Every authoritative workflow run has a coherent
status/timestamp interval before Build selection. Stack depths are proven from
the complete parent-decision chain, including a genuine depth-three exception.
Checkpoint, dependency-change, and pre-graduation removal proofs preserve
necessary artifacts after a named semantic failure; every historical
restoration passes, and a deletion-ready artifact passes only with `Delete`.
Every proof kind independently has the correct identity, artifact, trigger,
strictly later time, shared named reason, semantic outcome, restoration, and
current-disposition causality; one invalid record cannot be masked by valid
siblings or event ordering.
The committed artifacts additionally pass actual sandboxed removal/failure
and restoration/success execution through the closed command allowlist while
the plain `build/test-artifacts/` sandbox continues to use the immutable
original checkout and object database as separate Git authority.
Each child uses only `/usr/bin/python3 -I` and the copied closed launcher with
explicit sandbox/authority/check arguments; repository and user-site
`sitecustomize.py` exit hooks cannot run first, and root/mode/check/extra
argument mutations reject.

### Negative control

The suite rejects a boundary record outside the snapshot, terminal/active
status contradiction, cancelled-run misclassification, missing Actions page,
missing issue/commit/review/decision, derived-fact override, post-review
override insertion/backdating/reordering/current-record mutation, a cited tree
without the exact override entry, cumulative unresolved findings, post-review
resolution, direct synthetic resolution timestamps, untrusted/incomplete
resolution history, wrong-PR or missing event SHA, fabricated repository
identity/commit/parent/timestamp/message, base-only/merge-only/unrelated/future
review commits, incoherent candidate/merge or review history, any
PR/issue/review/run/finding/commit identity or metric-relationship
substitution, boolean substitution for any integer/version/count field,
commit-message trailing newline/space/blank-line/body/leading-whitespace
mutation, an omitted identity seal, PR events before creation, invalid
open/closed phases, future event or workflow-run SHAs,
earlier/equal/unrelated/fabricated reverts,
non-final/multiple/mixed-case/short/uppercase or text-padded revert trailers,
inconsistent stack parent/base/depth, missing parent decisions, stack cycles,
false depth-two or depth-three claims, an older spotlight run outside the
cohort, impossible non-Build intervals, incoherent
queued/in-progress/completed timestamps, unreported accepted conclusion,
unknown risk/event/edge/disposition, unclaimed or ambiguously owned dependency
edge, orphan/duplicate/expired artifact, deletion-ready non-Delete artifact,
non-causal or missing deletion proof, premature disposition, stale lifecycle
expiry, any failed historical restoration, an empty/fabricated Git authority,
a stale or fabricated executable proof, ambient Git directory/work-tree/object/
alternate/config/replace redirection, local graft/replace/alternate authority,
mixed proof semantic/restoration/reason/identity/time/kind/disposition state,
and any unallowlisted fixture command.
Build topology mutations also reject pilot commands hidden by `|| true`, `; true`,
`&& true`, wrappers, substitutions, or changed redirections.

### Interactions and save compatibility

Dependencies are none. Dependents are issues #177, #178, #179, #180, and
#181. Conflicts are none with CI selection, review ordering, merge gates,
runtime/gameplay, configuration, save, generated game data, localization, or
archival output. Modern debug, modern release, and archival impact are all
none; there is no feature flag and no ROM/RAM or save-format impact.

### Automation

`python3 -m unittest discover -s scripts/workflow_pilot/tests -p 'test_*.py' -v`
parses the decision and fixture schemas, reproduces every frozen calculation,
compares canonical bytes, and exercises all positive/adversarial controls
above. Build CI runs the same command plus the baseline reporter with
`--repository-root "$GITHUB_WORKSPACE"` in its existing required `host-tests`
job. Before those reporter commands, CI hydrates the fixture's exact commit
authority and proves exact `HEAD` and refs are unchanged. The parsed workflow
topology suite fails if hydration, pre-pilot step order/content, scrubbed
protected-step environments, ownership, or checked-out-root binding is removed
or weakened. It also requires each combined worker's exact direct job mapping
and values; no container or alternate execution context can replace the
reviewed Ubuntu host.
The protected Python steps use the closed isolated launcher, so repository or
user site customization cannot run before control. The workflow mirror parser
uses deterministic line/indent parsing rather than an ambiguous multiline
regular expression. Cross-checkout verification parses target workflow data
without importing target code and requires complete source/target job and step
sequences: counts/order, unique names, setup-versus-gate roles, immutable
actions, run argv, `env`/`with` mappings, direct fields, and root execution
before dry-run or execution. Unnamed non-checkout steps, duplicate setup names,
complex keys, and extra jobs fail closed. Workflow execution context is
exactly name/triggers/read-only permissions/jobs with no workflow env,
defaults, or concurrency. Every combined job is exactly Ubuntu, 60 minutes,
its allowlisted env, and steps; self-hosted/container/service/strategy/default
shell or any other execution field fails before dry-run.
Patch publication and summary are also complete semantic structures:
master-only publication condition, pinned actions, scoped secret/env and six
publisher steps; then `always()`, exact ordered needs/result env, five-minute
context, and one fail-closed summary step. Neither is locally executed, but any
runner/condition/needs/permission/env/step/command/action/alternate-context
drift rejects before dry-run.

`python3 scripts/check_docs.py --check` validates this complete procedure,
registry ownership, links, and automation evidence.

### Cleanup and limitations

Remove `build/test-artifacts/workflow-pilot`. No remote state was read or
changed by the procedure. The frozen fixture can reproduce only the explicitly
captured source semantics. GitHub's historical thread-resolution timing was
not captured for this baseline, so the reporter preserves counts/current
state while making the timing metric and its pilot readiness unavailable.
Future numeric evidence requires complete GitHub
`pull_request_review_thread` webhook delivery capture. No manual-only
criterion applies.

Rollback is a normal revert of issue #176's dedicated commit; no workflow or
game behavior needs a compensating change.

## TC-WORKFLOW-REVIEW-FAMILY-001: Expand valid findings across complete sibling families

- **Feature / originating issue:** `workflow-governance` /
  [issue #179](https://github.com/laqieer/fireemblem8-expansion/issues/179).
- **Supported configuration or artifact:** clean source checkout with Python
  3, Git, the issue #176 workflow-pilot reporter, candidate-pinned offline
  transform fixtures, and a mocked live GraphQL adapter; no GitHub token, live
  PR, workflow, ROM, or emulator is required.
- **Prerequisites and clean starting state:** start at the repository root
  with `scripts/workflow_pilot/review_family.py`,
  `scripts/workflow_pilot/review_base_checker.py`,
  `scripts/workflow_pilot/isolated_review_gate.py`,
  `review_family_complete.json`, `review_family_complete_evidence.json`,
  their default counterparts, and `review_family_github_adapter.json`
  unchanged. The fixture candidate
  `a8768e4f467c36f8bec60ee823d7d1735d3fcd45` must exist locally.

### Actions

1. Run
   `python3 -m unittest scripts.workflow_pilot.tests.test_review_family -v`.
2. Run the module twice with explicit `--repository-root`,
   `--expected-candidate`, `--contract`, and offline `--evidence` over a
   detached local checkout whose actual `HEAD` is the fixture candidate.
   Compare its canonical JSON bytes and confirm it cannot authorize push or
   merge.
3. Mock the production `gh api graphql` adapter with the committed bounded
   response inside the isolated gate. Confirm the report core itself can never
   authorize delivery and exports no trust-capability class/token.
4. Create a two-commit disposable repository under `build/test-artifacts/`.
   Extract the checker from its immutable base tree, run it with
   `/usr/bin/python3 -I` in the read-only sandbox, and inspect the receipt's
   checker blob/argv, base/candidate trees, full diff, actual finding IDs,
   report digest, pre/post cleanliness, and pass result. Remove one changed
   file or finding from the independent report and require failure.
5. Sign canonical independent-report bytes with the test-only external key.
   Verify fresh scope, key epoch/purpose, expiry, nonce, and atomic replay
   consumption. Reject stale/2020/future/overlong, wrong-scope/key, mutated,
   noncanonical, unavailable-replay, and reused receipts. Confirm production
   stores no key.
6. Inspect the complete evidence snapshot's immutable PR, actor, review,
   finding, action, full-SHA, tree/blob, and timestamp identities. Confirm the
   complete contract's one accepted synthetic finding in each
   action, lifecycle, wire, generated, and resource family. Confirm all exact
   siblings and manifest-backed result IDs appear in the global and
   first-round handoffs.
7. Mutate actual/live head, origin-bound candidate/tree/source, collector
   completeness/pagination, review/finding/action timestamps, actor case/bot
   aliases, ownership, permission/action order, frozen behavior rows, result
   and execution-receipt association, family/member/result/outcome, and every
   numeric limit. Reuse one review node as a finding node and require global
   collision failure. Prove an old `a876...` result cannot support the later
   `1a6...` candidate.
8. Supply 55 finding IDs against `max_findings_per_review: 10`; exceed the
   reviewed-file, duration, sibling-per-finding, and sibling-per-handoff
   maxima; and use boolean, negative, zero, or above-cap limits. Require
   failure.
9. Add six consecutive change-request rounds with dispositions for rounds 3
   and 6. Confirm both holds lift independently, then remove, reuse, reorder,
   future-date, or change the SHA of each event and require failure. Confirm a
   current third round without disposition remains a push-blocking hold.
   Insert a candidate advance between a held review and its disposition and
   require failure. Change the disposition actor to an outsider or pre-review
   owner and require failure.
10. Set a remote review to `CHANGES_REQUESTED` with zero inline comments, then
   add a body finding under `COMMENTED`; neither may classify clean. Change
   head/review/thread state between the gate's first and second live collection
   and require failure.
11. Inspect the default fixture's current zero-finding remote Copilot review.
   Remove it and then make it stale; confirm merge remains blocked while the
   remote-review-required gate remains true.

### Expected result

The complete offline fixture validates transformation semantics against a real
Git head/tree/object database but cannot authorize delivery. No in-process
object can set trust. Only the `/usr/bin/python3 -I` live gate with fresh
single-use externally authenticated report bytes, two matching live snapshots,
and a passing immutable-base checker promotes structural eligibility. The
exact five-row inventory maps all result identities to that base-pinned check.

### Negative control

The suite rejects an in-process/offline trust claim; stale live head,
repository, source, reviewer, outcome, action, order, unresolved state, or
timestamp; incomplete API pages; invalid/noncanonical/stale/reused/wrong-scope
receipt HMAC, key epoch/purpose, nonce, replay authority, result/check/SHA;
nonexistent or unrelated evidence; one omitted family sibling; unknown
family/member/result/action/state; cross-domain node collisions; actor case or
bot-suffix aliases; duplicate/overlapping ownership; duplicate/reversed/
interleaved actions; exceeded limits; semantic result reassociation; an old
candidate result; missing changed-file/finding review coverage; writable/dirty
checker execution; `CHANGES_REQUESTED` or body findings classified clean;
changed second live state; premature push; and outsider/self disposition.

### Interactions and save compatibility

The contract depends on issue #176's hardened Git authority, strict JSON/full-
SHA patterns, risk/threshold enums, canonical representation, lifecycle
discipline, and metric namespace. Issue #181 depends on this contract. It
conflicts with self-asserted or stale evidence,
overlapping review agents, incomplete sibling/result coverage, exceeded
bounds, and narrow patching through an unresolved architecture hold. Remote
Copilot review, candidate Build, and post-merge Build remain mandatory. There
is no game, runtime, save, generated game-data, localization, ROM/RAM, modern
debug/release, or archival impact.

### Automation

`python3 -m unittest scripts.workflow_pilot.tests.test_review_base_checker
scripts.workflow_pilot.tests.test_review_family
scripts.workflow_pilot.tests.test_github_review -v` executes the independent
base checker, structural evaluator, isolated live collector, immutable receipt,
replay, full-diff/finding coverage, state revalidation, semantic association,
global-node, bound, no-push, multi-hold, action-order, owner, and metric
reproducers.

`python3 -m unittest scripts.docs_check_tests.test_development_workflow_skill -v`
checks that the production review/merge policy retains the bounded read-only
owner, complete sibling families, third-round hold, and mandatory remote
review.

`python3 scripts/check_docs.py --check` parses this procedure and its registry
ownership and automation paths.

### Cleanup and limitations

The focused suites automatically remove ignored detached/synthetic authority
worktrees, checker sandboxes, and replay files. The collector uses only
read-only `gh api graphql`; no mode changes remote state. Offline fixtures and
HMAC bytes without current-time/key/replay plus fresh live authority never grant
delivery. No manual-only criterion applies.

Rollback is a normal revert of issue #179. The issue #176 baseline values and
existing Build, Copilot review, merge, and post-merge gates remain unchanged.
