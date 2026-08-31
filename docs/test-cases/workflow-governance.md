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

## TC-WORKFLOW-AGENT-HANDOFF-001: Validate bounded exact-SHA agent handoffs

- **Feature / originating issue:** `workflow-governance` /
  [issue #178](https://github.com/laqieer/fireemblem8-expansion/issues/178).
- **Supported configuration or artifact:** clean source checkout with
  Python 3 and Git; no GitHub token, live PR/run, ROM, emulator, or game build
  is required.
- **Prerequisites and clean starting state:** start at the exact repository
  root with the issue #176 reporter and the agent-handoff module unchanged.
  Remove prior `build/test-artifacts/agent-handoff-*` test directories.

### Actions

1. Run
   `python3 -m unittest scripts.workflow_pilot.tests.test_agent_handoff -v`.
2. Inspect the temporary real-Git success fixture. Confirm its result is the
   clean worktree `HEAD`, has exactly the assigned direct parent and expected
   branch, changes only allowlisted paths within the numeric line budget, ends
   in the exact Copilot trailer, supplies every acceptance/check/budget
   evidence ID, and uses zero ROM/RAM plus one admitted protocol change.
   Confirm required checks use the closed non-shell contract and a sealed
   receipt bound to exact argv, check ID, parent/candidate, worktree,
   timestamps, exit code, and output digest.
   Set local `core.whitespace=-trailing-space`, hostile diff/textconv/alias
   config, ambient global/system/external-diff controls, and tracked diff
   attributes; trailing whitespace must still fail under the pinned argv.
   Confirm nonempty local `.git/info/attributes` rejects.
3. Exercise the repeated-parent/stale, wrong-parent, unrelated-branch, dirty,
   incomplete, missing-evidence, missing-trailer, out-of-scope, line-budget,
   ROM-budget, RAM-budget, protocol-budget, lifetime, and RSS fixtures.
4. Exercise reused-owner and duplicate-watcher fixtures. Add an
   implementation-owner push observation and confirm it rejects while the
   unchanged trusted owner push rule remains outside implementation ownership.
   Repeat with login case, `[bot]` suffix case, and immutable numeric-ID
   aliases. Seal two prior handoffs and reject owner reuse, a gap, fork,
   reorder, changed identity, or broken receipt seal. With separate local-bare
   owner and implementation roles, bootstrap the fixed `origin` ref, advance
   it honestly through expected-head/sequence CAS, and reject a stale CAS.
   Confirm a new document with `prior_handoffs: []`, a reset local ref, stale
   authority object, or missing upstream ref cannot forge genesis. Confirm a
   normal clone explicitly fetches the remote authority without changing
   local refs. Use a real bare-origin advance hook between the first remote
   observation, fetch, and second observation: one move retries from the new
   OID, repeated moves exhaust the fixed bound with `authority-moved`, and a
   move before final eligibility invalidates the observation token.
5. Reconcile a direct watcher timeout with an authoritative successful
   `github-actions-api` run. Then use an authoritative failed run plus a
   watcher process error and confirm delivery remains failed.
6. Exercise the SIGKILL/OOM fixture. Confirm kernel evidence names signal 9,
   the interrupted check remains incomplete, an allowlisted uncommitted file
   remains in the exact conflict-free worktree, no host process action occurs,
   and exactly one differently identified bounded replacement receives the
   same issue/parent/branch/worktree context. Confirm sent-only,
   sent/received, and sent/received/progressing prefixes report `in_progress`
   without trusted/delivery eligibility. Equal, predated, and multiple
   replacement assignments reject.
7. Exercise the local coordinator with autostop and stop-on-disconnect enabled
   but no availability plan. Confirm trusted push fails before unattended
   delivery relies on that coordinator. At a 2026-08-31 evaluation, reject a
   January expiry and an ineffective `disable_triggers` receipt; accept only
   same-evaluation runtime evidence showing both triggers disabled through the
   complete unattended interval.
8. Parse the typed delivery graph. Confirm a merged parent makes child
   implementation ready while its exact-master Build is in progress and
   remote completion is pending; an unmerged parent blocks the child; a
   healthy pending watcher never appears in todo dependencies; terminal
   master failure requires active fix-forward/revert without rewriting the
   prior pending result; `child-implement -> parent-remote` rejects and names
   `child-implement -> parent-merge`; and parent completion, closure, and
   remote completion retain their own post-merge-Build edges. Bind every
   handoff to one exact child relationship/task and bind the parent Build task
   to one authoritative run SHA/status/conclusion. Reject issue `999`, blocked
   or done status relabels, and missing/duplicate relationships/tasks.
9. Exercise reporter fixture schema version 2 with complete successful and
   stale source handoff bundles. Confirm document/Git/input/result/check
   revalidation, identity sealing, and accepted/rejected,
   stale-response, lifetime, RSS, coordination-turn, and recovery-minute
   metrics. Reject a hand-authored accepted row or any source/result/receipt
   seal drift. Confirm the frozen version 1 baseline still rejects the
   extension field and retains its exact expected schema.
10. Run
   `python3 -m unittest discover -s scripts/workflow_pilot/tests -p 'test_*.py' -v`,
   `python3 -m unittest scripts.docs_check_tests.test_development_workflow_skill -v`,
   and `python3 scripts/check_docs.py --check`.

### Expected result

Only the exact clean one-commit descendant reaches `trusted_push_eligible`.
Assignment sent, received, progressing, committed, and handed-off remain
distinct timestamped states. A committed result closes its implementation
owner. Later work requires a fresh handoff and owner. The normalized input
Git, and result seals are deterministic. Required checks execute only a closed
structured contract; passed labels do not replace its exact receipt or rerun.
The actual safe pass fixture succeeds, while the safe trailing-whitespace
failure plus literal `false`, stale/wrong-command/SHA/worktree/timestamp
receipts reject. Hostile local/global/system/env Git configuration cannot
change that outcome.

The watcher timeout records process failure while preserving authoritative CI
success. The true failed run records authoritative failure and cannot become
delivery-eligible. The OOM fixture preserves exact worktree evidence, records
one incomplete check and recovery cost, creates exactly one replacement
assignment, and performs no destructive process action. The hibernated local
coordinator is unavailable without a time-bounded disable/takeover plan.
The plan is accepted only with current runtime evidence covering the pending
unattended interval. Casefolded logins, bot aliases, and numeric IDs cannot
reuse closed owners or hide implementation-owner remote actions. Prior closed
handoffs remain a sealed contiguous chain rather than a mutable ledger, and
their exact expected head and sequence come from the independently read
fixed-`origin` authority commit chain. Local ref changes are irrelevant;
omitted history cannot reset a non-genesis lifecycle, and only owner CAS can
advance the remote head. A stable read requires equal remote OIDs around the
fetch; no stale parent survives movement or the final eligibility recheck.

The typed dependency graph reports `child-implement` ready as soon as
`parent-merge` is done, independently of a healthy running master watcher and
pending parent completion/closure/remote tasks. An unmerged parent blocks the
child. Terminal master failure activates `fix_forward_revert`, but does not
retroactively make the earlier pending watcher a valid reason to idle.
Completion, closure, and remote completion for the parent remain blocked by
its own post-merge Build.
Each handoff has exactly one issue/PR/candidate/status-matched relationship and
implementation task. All task statuses obey their dependencies. The parent
Build task's exact SHA, status, and conclusion come from its authoritative run;
active or failed state keeps parent delivery ineligible while leaving an
already merged child contract implementation-ready.

The issue #176 reporter consumes version 2 normalized handoff evidence and
reports failures, stale responses, maximum owner lifetime and RSS,
coordination turns, and recovery cost. Its frozen version 1 baseline, expected
values, seals, lifecycle, trusted-push, and centralized-watcher contracts are
unchanged. Version 2 accepts no hand-authored aggregate: it repeats complete
source worktree and receipt validation before computing those metrics.

### Negative control

Each stale/repeated SHA, non-direct or wrong parent, wrong branch/worktree,
dirty/conflicted tree, missing commit/trailer/evidence, failed or interrupted
required evidence, out-of-scope path, unquantified or over-limit lines,
ROM/RAM/protocol overage, lifetime/RSS excess, duplicate owner/coordinator/
watcher, stale run, implementation-owner remote action, missing/multiple OOM
replacement, reused replacement owner, lost preserved path, completed
interrupted test, host-process action, unavailable local coordinator, unknown
field/enum/rejection, duplicate JSON key, and incoherent reporter record fails
closed. Watcher process success cannot replace a nonterminal or failed
authoritative run, and a watcher timeout cannot turn authoritative success
into failure. A code/contract edge to parent remote completion, missing parent
merge edge, watcher-as-todo dependency, missing parent post-merge gate, or
terminal failure without active fix-forward/revert also rejects. Issue/task/
status relabeling, arbitrary commands, forged or stale check receipts,
hand-authored reporter rows, owner-history chain mutation, actor aliases, and
expired or ineffective availability evidence reject independently. A
missing/reset canonical history ref, hostile Git config or local attributes,
incomplete-prefix rejection, and equal/predated/multiple OOM replacement also
fail their dedicated controls. Mid-read movement retries, repeated movement
fails `authority-moved`, and movement after the read but before eligibility
invalidates the sealed observation.

### Interactions and save compatibility

Dependency: issue #176. Dependent: issue #181. Conflicts: none with existing
trusted owner pushes or one centralized direct watcher per exact run. This
source-only contract changes no modern debug/release ROM, runtime/gameplay,
save format, generated game data, localization, ROM/RAM content, or archival
lane. No feature flag, migration, or manual criterion applies.

### Automation

`python3 -m unittest scripts.workflow_pilot.tests.test_agent_handoff -v`
creates bounded temporary repositories below `build/test-artifacts/`, derives
real ancestry/branch/diff/worktree/trailer facts with the issue #176 clean Git
boundary, executes only the closed safe check runner, verifies receipts, and
uses a local bare remote plus separate owner/implementation repositories to
exercise deterministic bootstrap/advance plans, explicit fetch, local reset,
stale CAS, honest CAS, stable/moving read boundaries, bounded exhaustion, and
eligibility-token revalidation. It performs no GitHub mutation. The complete
`scripts/workflow_pilot/tests` discovery reruns the frozen issue #176 reporter
regressions. Parsed task/dependency/watcher fixtures compute readiness and
required edges rather than asserting policy wording. The workflow-governance
and docs checks validate this indexed procedure and preserve the trusted-push
and centralized-watcher policy.

### Cleanup and limitations

Temporary repositories are removed automatically. The tests model GitHub
responses, SIGKILL/OOM kernel evidence, and hibernation as inert fixtures; they
do not mutate GitHub, allocate destructive memory, signal a process, change
Dev Box settings, or kill any host process. No manual-only criterion applies.
