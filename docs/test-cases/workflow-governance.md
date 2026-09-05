# Workflow-governance cases

These source-only procedures cover the repository's agent delivery policy.
They exercise documented orchestration contracts without dispatching a
workflow, using credentials, or changing ROM behavior.

## TC-WORKFLOW-IMMEDIATE-PUSH-001: Publish new commits immediately and expose WIP ownership

- **Feature / originating issue:** `workflow-governance` /
  [issue #207](https://github.com/laqieer/fireemblem8-expansion/issues/207).
- **Supported configuration or artifact:** clean source checkout with Python
  3 and the existing CLI workflow instructions; no token, live PR, ROM, or
  emulator is needed for the source-only protocol checks.
- **Prerequisites and clean starting state:** retain the committed publication
  protocol, mirrored contributor instructions, and case registry.

### Actions

1. Parse the labeled "Immediate publication and visible work" protocol in
   `.github/skills/development-workflow/SKILL.md`.
2. Run
   `python3 -m unittest scripts.docs_check_tests.test_development_workflow_skill.DevelopmentWorkflowSkillTests.test_immediate_publication_protocol -v`.
3. Exercise missing/duplicate/unknown fields, delayed publication, delegated
   implementation-agent push, omitted WIP ownership/scope/blockers, and weakened
   final-gate controls. Reorder fields and visibility members without changing
   their meanings and require acceptance.
4. For real delivery, create a task commit and immediately owner-push its exact
   SHA even while review, CI, or remaining work is pending. Open/update its
   dedicated PR and canonical evidence comment with branch/head, owner, scope,
   state, remaining work and blockers. Keep incomplete work draft and retain
   all merge/closure holds. Synthetic temporary-repository test commits are
   not task publication events.

### Expected result

Commit persistence is not delayed by validation/review/CI or batch timing.
Implementation agents immediately hand off the commit without pushing.
Contributors can identify the active owner and scope from the issue/PR instead
of starting duplicate work. Exact-head review/security/Build, semantic handoff,
terminal authority, and exact-master completion remain independent gates.
A failed publication is reported as a blocker, not as remotely saved work.

### Negative control

The pre-fix workflow retained completed commits locally during independent
review. Delayed/softened publication, missing visibility, implementation-owned
pushes, or treating persistence as acceptance fails the parsed protocol.

### Interactions and save compatibility

Depends on existing owner-context push, the single coordinator, and canonical
evidence comments. It conflicts with post-commit pre-review holds and deferred
batches; pending #178/#179/#181 integration must preserve persistence versus
acceptance. No game, ROM/RAM, save/config identity, localization, generated-data,
modern/archival, or feature-gate impact. Worktree cleanup belongs to #208.

### Automation

The focused existing workflow-governance unittest parses the named external
CLI instruction format, including mutation and order-independent controls.
This is instruction data consumed by the CLI, not a claim that matching source
text proves runtime behavior; a ROM/compile check cannot establish this
instruction contract. Real owner-context pushes and visible GitHub refs/PR
state supply operational evidence during delivery.

### Cleanup and limitations

The source-only checks create no remote state and need no cleanup. They do not
grant credentials or make remote publication atomic; preserve failed-push
evidence and all final quality gates. No manual-only criterion applies.

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

The synthetic non-master-base pull request selects the mandatory
`event-identity` setup before `event-router`, `event-classifier`, the existing
`host-tests`, `build`, `extended-host-tests`, `legacy`, and fail-closed
`summary` jobs.

- **Parsed full-PR job set:** {`event-identity`, `event-router`,
  `event-classifier`, `host-tests`, `build`, `extended-host-tests`, `legacy`,
  `summary`}.

Every candidate worker still checks out and verifies
`pull_request.head.sha`. The publisher is absent from pull-request execution,
while a push to `master` selects it and a push to any other branch selects no
workflow jobs.

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
`pull_request`, omitting either mandatory `event-identity` or `event-router`
setup context, removing `edited` or another required activity type, enabling
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

## TC-WORKFLOW-BODY-EDIT-001: Suppress metadata-only Build workers

- **Feature / originating issue:** `workflow-governance` /
  [issue #177](https://github.com/laqieer/fireemblem8-expansion/issues/177).
- **Supported configuration or artifact:** clean source checkout with Python
  3, the Build workflow, and the committed GitHub event fixture; no token,
  live pull request, workflow run, ROM, emulator, or game build is required.
- **Prerequisites and clean starting state:** start at the repository root
  with `.github/workflows/build.yml`,
  `scripts/workflow_pilot/event_classifier.py`, the isolated launcher, and
  `scripts/workflow_pilot/tests/fixtures/event_classification.json` plus the
  preserved `pre_fix_build.yml` parsed graph unchanged.
  The fixture declares that the current workflow has no explicit final
  dispatch surface.

### Actions

1. Run
   `python3 -m unittest scripts.workflow_pilot.tests.test_event_classifier -v`.
2. Run
   `python3 -m unittest scripts.workflow_pilot.tests.test_candidate_evidence -v`.
3. Run
   `python3 -m unittest discover -s tests/workflows -p "test_*.py" -v`.
4. Run
   `python3 -m unittest tests.upstream_port.test_verify -v`.
5. Inspect the parsed body-only, title-only, body-and-title, base-only,
   base-plus-body, unknown-field, incomplete-change, `opened`, `synchronize`,
   `reopened`, missing/empty/malformed/mismatched base components, missing-head,
   missing-both, stacked-base,
   merge-`github.sha`, and `master`-push cases. Confirm every fixture provides
   separate PR base/head and push identity, GitHub-shaped payload, exact
   classifier result, exact selected job set, exact head/base, and expected
   summary conclusion. Base-only, mixed, and stack-retarget fixtures carry the
   production `changes.base.ref.from` plus `changes.base.sha.from` transition.
6. Inspect the disposable-event replay. It writes each payload beneath ignored
   `build/test-artifacts/`, invokes the real `/usr/bin/python3 -I` launcher and
   output-file protocol, parses the resulting job outputs, and removes the
   sandbox without reading or mutating remote state.
7. Inspect a pull request's stable body contract and canonical evidence
   comment protocol in [`../workflow-pilot.md`](../workflow-pilot.md). The
   comment carries this standalone marker:

   <!-- workflow-pilot-candidate-evidence -->

   Evolving SHA/run/review/budget/preflight values are updated there in place
   rather than in the body, title, baseline fixture, decision record, or
   another mutable ledger.
8. Parse `.github/PULL_REQUEST_TEMPLATE.md`. Confirm it contains only frozen
   scope/non-goals, classification/relationships, acceptance criteria, tester
   procedure, and compatibility decisions. Replay one canonical comment,
   missing/duplicate/non-standalone marker comments, a body marker, and every
   prohibited evolving body field.
9. Replay the same body-only event through the preserved pre-fix Build graph.
   Compare these unordered parsed sets:
   - **Parsed preserved pre-fix body-only job set:** {`host-tests`, `build`,
     `extended-host-tests`, `legacy`, `summary`}.
   - **Parsed current metadata-only job/check set:** {`event-identity`,
     `event-router`, `metadata-classifier`, `host-tests`, `build`,
     `extended-host-tests`, `legacy`, `patch-release`, `summary`}.
   The pre-fix graph therefore starts all four expensive workers and summary;
   the current graph retains both mandatory setup contexts, preserves the live
   canonical `host-tests`/`build`/`summary` required contexts through trusted
   branch-protection continuity adapters plus the summary continuity proof,
   keeps canonical skipped `extended-host-tests`/`legacy` plus canonical
   skipped patch publication, and uses only the running metadata classifier
   attestation beyond those existing required names.

### Expected result

Body-only, title-only, and combined body/title edits emit
`event-identity`, `event-router`, `metadata-classifier`, the canonical
worker checks `host-tests`, `build`, `extended-host-tests`, and `legacy`,
plus `patch-release` and canonical `summary`. The trusted metadata-only path
starts runners for `host-tests` and `build`, but those two jobs execute only a
fixed no-checkout continuity attestation that validates exact event identity,
classifier, head, base, and the raw edited pull-request body/title-only
`changes` payload itself from the runner-owned file-backed `GITHUB_EVENT_PATH`;
that attestation accepts only a same-owner regular file up to 1 MiB, reads at
most one additional EOF byte, and never env-copies the body/title/changes
JSON. Missing, malformed, duplicate, base-retarget,
unknown, empty, or unchanged body/title changes reject both adapters. Every
existing
checkout/install/test/build step in those jobs is full/fallback-only and
remains skipped. `extended-host-tests` and `legacy` stay platform-skipped with
no runner. Live branch protection remains unchanged and therefore still
requires canonical `host-tests`, `build`, `summary`, and the independent
GitGuardian context. Metadata `summary` succeeds only after a trusted
no-checkout Actions API proof classifies exact prior runs newest-first, skips
only conclusively metadata runs, and confirms the newest conclusively full
Build CI run for the same repository, PR number, authoritative base SHA, and
immutable head SHA completed successfully; a newer failed, cancelled,
in-progress, or malformed full run blocks older successes. That proof first
requires complete paginated results with stable `total_count`, single-page
`Link` omission, exact non-final `next`/`last` relations, no final `next`,
exact per-page cardinality, stable `workflow_id`, ordered positive
`run_number`/`run_attempt` values, one exact current-run observation, and
rejects redirects before any second authenticated request.
Without that prior green full run, metadata-only edits still block merge.
Metadata runs remain
ineligible candidate evidence even when their continuity adapters and canonical
`summary` succeed. A later metadata continuity run advances the required
canonical `summary` context only after proving that newest prior full run,
while candidate eligibility remains bound to the newest prior complete full
run. Evaluated metadata labels, duplicates,
unknown names, or spoofed worker names reject instead of becoming candidate
evidence.
The recorded `gh pr checks --required` output after the title edit and restore
shows canonical `host-tests`, `build`, and `summary` passing together, and a
protected async merge attempt no longer fails with `Required status check
"summary" is expected.`.
The summary succeeds only when classifier status is `success`, the classified
SHA equals the event's validated exact `pull_request.head.sha`, event number
matches the exact `refs/pull/<number>/merge` ref, suppression is exactly false,
`host-tests`/`build` succeed through the trusted continuity adapters, and
`extended-host-tests`/`legacy`/`patch-release` are exactly `skipped`, and the
trusted Actions API proof classifies exact prior runs newest-first so only the
newest conclusively full run with the same repository, PR number,
authoritative base SHA, and immutable head SHA can authorize continuity.
That proof first requires complete paginated results with stable
`total_count`, single-page `Link` omission, exact non-final `next`/`last`
relations, no final `next`, exact per-page cardinality, stable `workflow_id`,
ordered positive `run_number`/`run_attempt` values, one exact current-run
observation, and rejects redirects before any second authenticated request.
Older full
successes never override a newer failed, cancelled, in-progress, or malformed
full run.

Base-only edits, mixed edits, unknown and incomplete change records, `opened`,
`synchronize`, and `reopened` select the classifier, all four expensive
workers, and summary at the exact PR head. A `master` push additionally selects
the existing patch publisher and runs the complete graph from its separate
push SHA. Malformed/duplicate/non-finite JSON or another classifier failure with a
validated authoritative PR head runs all four workers at that exact head under
their canonical worker names, then summary still fails to expose the classifier
defect. A classifier failure on a
master push with validated `github.sha` runs all four workers and the publisher
at that exact push SHA, then summary still fails. Any
missing, empty, malformed, or event-mismatched base ref/SHA with a valid exact
PR head runs all four workers at that head and fails normal summary; a
syntactically valid direct base SHA may remain diagnostic output but is never
checkout authority. Missing, malformed, stale, or spoofed PR head or missing
push SHA starts no combined worker/publisher and fails summary.
Missing/stale successful output cannot select a fallback ref.
An accepted base retarget requires valid, differing previous/current ref and
SHA pairs; ref-only, SHA-only, same, missing, extra, or spoofed transition
records remain full fail-closed edits and never metadata suppression.
Base refs are bounded to 1024 UTF-8 bytes and must satisfy full
`git check-ref-format refs/heads/<base.ref>` semantics; `--branch` shorthand
is not used, and lone `@` is rejected. Python applies the equivalent grammar
without a subprocess; the trusted bootstrap quotes the full ref to system Git
and never checks it out. Invalid base refs are incomplete identity: a valid
exact head runs all four workers and fails summary; an invalid head runs none.
The classifier executes from the verified current PR base SHA; a missing base
uses the trusted default branch only to report invalid identity, while a base
without the new classifier uses the explicit strict bootstrap. The current
workflow has no `workflow_dispatch`, so the fixture and topology test assert
that no final-dispatch job selection exists to preserve.
The classifier bootstrap may use the trusted default branch when PR base
identity is missing or unusable; worker checkouts never use a merge/default
fallback.
Trusted event setup accepts identity only as an exact lowercase 40-hex SHA. A
PR also requires its numeric event number and exact
`refs/pull/<number>/merge` ref; a push requires `refs/heads/master` and equal
event `after`/`github.sha`. Successful full/metadata classifications, workers,
and summary all bind to that same kind and SHA. Missing, uppercase, short,
nonhex, ref-name, ref-number-mismatched, malformed, or cross-event identities
run no worker and cannot produce a successful summary. Candidate normalization
requires exactly one successful identity context for both full and metadata
modes and exactly one successful router setup
context; missing, failed, skipped, renamed, duplicate, or unknown setup
contexts reject the run.
A canonical successful `event-identity` context is mandatory in both modes.
A canonical successful `event-router` context is mandatory in both modes.
Metadata-only mode is accepted only for a coherently
bound pull request. Push-shaped or cross-event metadata output fails the
classifier, runs the validated full fallback workers/publisher, and leaves
normal summary failed. Workers consume only that validated SHA. The publisher
uses the same validated push SHA, verifies `/usr/bin/git rev-parse HEAD`
immediately after checkout, and stages the three-file producer from that exact
validated after commit without whole-file source hash pins. Before private
download, the exact after tree builds as a dedicated unprivileged UID inside
mount, PID, and network namespaces with no network, capabilities, secrets,
`BASH_ENV`, or `GITHUB_ENV`. Private mount propagation, recursively read-only host
root/system/tool paths, private `/tmp`/`run`/`proc`/`dev`, and masked host
D-Bus/container/service sockets leave only exact candidate-owned mounts
writable. Every descendant stays in one exact cgroup v2. The trusted host stops
the exact process group and cgroup, verifies `cgroup.procs` is empty, proves no
builder-UID process remains, removes only the owned cgroup, then admits the expected regular,
nonsymlink, single-link 32 MiB target and bounded metadata handoff; device,
escaped, and unexpected outputs fail. It removes the builder user, tree,
wheelhouse, and candidate checkout. No complete target ROM enters an Actions
artifact, cache, release, or log. The minimal `BASEROM_URL` step then creates an
unpredictable mode-restricted path and exposes only its trusted output. The
next step uses absolute isolated Python from an empty runtime CWD/environment;
no candidate command runs while the base exists. Cleanup traps delete the base
on success/failure, cleanup is verified, and only the patch artifact reaches
upload.
Before `/sys` is masked, the exact owned cgroup is bound read-only below a
root-only `0700` `/mnt/supervisor`; the candidate cannot read, write, execute,
or traverse that parent. The exact cgroup child there remains read-only. The
wrapper reads that supervisor view after `/sys` is masked and permits handoff
only when its own PID is the sole member. Host-side kill/removal still uses the
actual cgroup path.
All repository/candidate-controlled commands finish before private download.
Cleanup is verified before upload.
After that cleanup, an adjacent final check revalidates exactly regular,
single-link BPS/manifest/README outputs immediately before upload.
Unavailable mount/cgroup features fail closed, and cleanup sends no UID-wide
signal.
Before candidate code starts, a trusted child launcher closes inherited file descriptors
above 2, redirects stdin/stdout/stderr permanently to private `/dev/null`, and
passes no GitHub workflow command-file paths.
Candidate output is never replayed, logged, or uploaded; the trusted host emits
only fixed status text with a numeric exit classification. Arbitrary output
volume cannot change an otherwise successful build. All other writable roots
and regular files retain tmpfs/ulimit bounds; no output sink exists.
No whole-file source hash pins are used.
Before the base exists, the fresh hosted publisher proves that no
candidate-written `GITHUB_ENV`, `BASH_ENV`, background process, checkout, or
executable state can survive the builder teardown.
Default-branch validation is deferred until classifier bootstrap is actually
needed. A missing or malformed default branch never invalidates an
independently valid PR-head or push fallback. With no classifier authority,
the router performs no checkout and fails safely, the classifier fails, exact
fallback workers plus any guarded push publisher run, and summary remains
fail-closed.

### Negative control

The preserved parsed pre-fix workflow fixture selects `host-tests`, `build`,
`extended-host-tests`, `legacy`, and `summary` for the same body-only fixture
despite its unchanged head SHA. The focused suites also reject metadata
topology that omits either mandatory `event-identity` or `event-router` setup,
suppression for same-value/spoofed/missing-current/extra-key/nested metadata
records, invalid title/body values, malformed `changes`, missing PR identity,
body/title metadata with whitespace, control, forbidden-character, dot,
slash, `.lock`, `@{`, lone-`@`, or oversized base refs,
valid-head events with malformed or mismatched base components that skip
workers or produce a successful summary,
duplicate JSON keys, `NaN`/positive or negative `Infinity`, positive/negative
exponent overflow, nonzero-to-zero underflow, huge exponents, an unused
overflow field on metadata-only input, oversized event
files, base or mixed edits, worker job-name overrides, historical
noncanonical worker names, merge-SHA fallback, malformed fallback identities,
successful classification with a cross-event/malformed/number-mismatched ref,
missing/failed/skipped/renamed/duplicate event-identity or event-router
evidence, push-shaped metadata router output,
an unverified/mutable
classifier checkout, classifier output drift, worker conditions that accept
invalid/stale identity, worker skipping after classifier exit 2 with a valid
PR/push head, cross-event fallback, worker/publisher execution after classifier
failure with no event SHA, summary success after any classifier failure, weakened
exact-head checkout, body/template evolving evidence or marker placement, and
source/target workflow mirror drift.

### Interactions and save compatibility

This confirmed workflow-efficiency fix depends on issue #176's immutable
baseline, closed workflow mirror, isolated launcher, exact-head checkout, and
summary authority. Issue #181 depends on this classification. It deliberately
changes only pull-request metadata-event selection and the evidence-comment
contract. It conflicts with manual labels, `pull_request_target`, head-authored
classifier execution, success-shaped defaults, mutable metric ledgers, and
suppression of genuine stacked-PR base edits. It has no feature flag and no
game/runtime, modern debug/release output, save, generated-data, localization,
ROM/RAM, or archival impact.

### Automation

`python3 -m unittest scripts.workflow_pilot.tests.test_event_classifier -v`
parses every fixture, replays the real isolated event-file/output protocol, and
exercises strict identity, semantic transition, non-finite JSON, malformed,
and unknown fail-closed controls.

`python3 -m unittest scripts.workflow_pilot.tests.test_candidate_evidence -v`
derives full versus metadata mode from running classifier/summary contexts and
proves a later green metadata run cannot replace a failed/missing candidate
full run; a later metadata continuity run advances the required canonical
`summary` context only after proving the newest prior successful full run,
while candidate eligibility remains bound to that prior full run.

`python3 -m unittest discover -s tests/workflows -p "test_*.py" -v` parses the
workflow and asserts exact trigger, job, head, worker-condition, summary, setup,
pin, and environment semantics, including the pre-fix negative selection.

`python3 -m unittest tests.upstream_port.test_verify -v` preserves the 28 local
gates while requiring complete nine-job source/target equivalence: the six
issue #176 jobs remain closed and the identity/router/classifier are closed
setup-only jobs, never 29th/30th/31st local gates.

`python3 -m unittest scripts.docs_check_tests.test_development_workflow_skill -v`
parses the frozen PR template/body and comment collection, requiring exactly
one standalone canonical marker while rejecting missing, duplicate, inline,
or body markers and every evolving body field.

### Cleanup and limitations

The disposable sandbox is removed automatically. No remote state is read or
changed. The local replay proves GitHub's documented event-file semantics and
the exact workflow graph contract, not live service availability. No
manual-only criterion applies. Rollback is a normal revert; the prior broad
`edited` behavior then resumes.

### Planned live title-only exercise after push

The owner performs this validation-only remote exercise only after the
candidate branch is pushed. The disposable PR is never merged and does not
implement an independent issue. Do not edit the implementation PR: while its
base predates `event_classifier.py`, base-authoritative routing correctly
reports `classifier-bootstrap` and runs the full graph, so it is not a valid
metadata-suppression probe until the classifier is merged into that base.

Run all commands below in one Bash session. The discovery helper snapshots all
prior run IDs, then makes at most 60 attempts five seconds apart. It accepts
exactly one unseen `Build CI` pull-request run created after the mutation with
the exact branch and head; timeout or ambiguity fails before `gh run watch`.

1. From the issue worktree, choose unused temporary names and create a direct
   child of the exact candidate branch with one deterministic tracked probe.
   Install cleanup before the first remote mutation:

   ```bash
   set -euo pipefail
   source_root="$PWD"
   repo="$(gh repo view --json nameWithOwner --jq .nameWithOwner)"
   head_owner="${repo%%/*}"
   candidate_branch="${candidate_branch:-agent/issue-177}"
   probe_branch="${probe_branch:-validation/issue-177-title-probe}"
   probe_worktree="$(dirname "$source_root")/issue-177-title-probe"
   probe_file=".github/workflow-probes/issue-177-title-only.json"
   evidence_dir="$source_root/build/test-artifacts/issue-177-live-probe"
   pr=""
   probe_head_sha=""
   original_title="TC-WORKFLOW-BODY-EDIT-001 validation"
   probe_title="$original_title [title-only metadata probe]"
   evidence_dir_created=false
   local_ownership_intent=false
   push_ownership_intent=false
   pr_ownership_intent=false

   list_build_run_ids() {
     gh api --method GET --paginate \
       "repos/{owner}/{repo}/actions/workflows/build.yml/runs" \
       -f event=pull_request -f branch="$probe_branch" -f per_page=100 \
       --jq '.workflow_runs[].id'
   }

   discover_build_run() {
     prior_ids="$1"
     created_after="$2"
     attempt=0
     while [ "$attempt" -lt 60 ]; do
       runs_json="$(gh api --method GET --paginate \
         "repos/{owner}/{repo}/actions/workflows/build.yml/runs" \
         -f event=pull_request -f branch="$probe_branch" -f per_page=100 \
         | jq -s '.')"
       if run_id="$(RUNS_JSON="$runs_json" PRIOR_IDS="$prior_ids" \
           EXPECTED_CREATED_AFTER="$created_after" \
           EXPECTED_BRANCH="$probe_branch" EXPECTED_HEAD="$head_sha" \
           python3 - <<'PY'
   import json
   import os

   prior = set(os.environ["PRIOR_IDS"].splitlines())
   records = [
       record
       for page in json.loads(os.environ["RUNS_JSON"])
       for record in page["workflow_runs"]
   ]
   matches = [
       record
       for record in records
       if str(record["id"]) not in prior
       and record["name"] == "Build CI"
       and record["event"] == "pull_request"
       and record["head_branch"] == os.environ["EXPECTED_BRANCH"]
       and record["head_sha"] == os.environ["EXPECTED_HEAD"]
       and record["created_at"] >= os.environ["EXPECTED_CREATED_AFTER"]
   ]
   if len(matches) != 1:
       raise SystemExit(1)
   print(matches[0]["id"])
   PY
       )"; then
         test -n "$run_id"
         printf '%s\n' "$run_id"
         return 0
       fi
       attempt=$((attempt + 1))
       sleep 5
     done
     echo "timed out waiting for one unseen exact Build CI run" >&2
     return 1
   }

   watch_build_run() {
     run_id="$1"
     case "$run_id" in
       ''|*[!0-9]*) echo "invalid Build run ID" >&2; return 1 ;;
     esac
     set +e
     timeout 90m gh run watch "$run_id" --interval 30 --exit-status
     watch_status="$?"
     set -e
     if [ "$watch_status" -ne 124 ]; then
       return "$watch_status"
     fi

     run_state="$(gh run view "$run_id" --json status,conclusion \
       --jq '[.status, (.conclusion // "")] | @tsv')"
     run_status="${run_state%%$'\t'*}"
     run_conclusion="${run_state#*$'\t'}"
     case "$run_status" in
       queued|in_progress|waiting)
         test -z "$run_conclusion"
         set +e
         timeout 90m gh run watch "$run_id" --interval 30 --exit-status
         watch_status="$?"
         set -e
         if [ "$watch_status" -eq 124 ]; then
           echo "second watcher timed out for exact Build run" >&2
           return 124
         fi
         return "$watch_status"
         ;;
       completed)
         if [ "$run_conclusion" = success ]; then
           return 0
         fi
         test -n "$run_conclusion"
         echo "exact Build run completed unsuccessfully" >&2
         return 1
         ;;
       *)
         echo "exact Build run has unsupported status" >&2
         return 1
         ;;
     esac
   }

   # Each exact run uses watch_build_run. A first watcher timeout (124)
   # triggers exactly one status/conclusion query for that run. Only queued,
   # in_progress, or waiting re-arms one final 90-minute watcher; a terminal
   # result is consumed immediately, any terminal failure is preserved, and a
   # second timeout fails.

   owned_probe_pr_numbers() {
     pulls_json="$(gh api --method GET --paginate \
       "repos/$repo/pulls" -f state=open -f head="$head_owner:$probe_branch" \
       -f base="$candidate_branch" -f per_page=100 | jq -s '.')"
     PULLS_JSON="$pulls_json" EXPECTED_OWNER="$head_owner" \
       EXPECTED_BRANCH="$probe_branch" EXPECTED_BASE="$candidate_branch" \
       EXPECTED_HEAD_SHA="$probe_head_sha" \
       EXPECTED_BASE_SHA="$candidate_sha" \
       python3 - <<'PY'
   import json
   import os

   records = [
       record
       for page in json.loads(os.environ["PULLS_JSON"])
       for record in page
   ]
   matches = [
       record
       for record in records
       if record["state"] == "open"
       and record["merged_at"] is None
       and record["head"]["user"]["login"] == os.environ["EXPECTED_OWNER"]
       and record["head"]["ref"] == os.environ["EXPECTED_BRANCH"]
       and record["head"]["sha"] == os.environ["EXPECTED_HEAD_SHA"]
       and record["base"]["ref"] == os.environ["EXPECTED_BASE"]
       and record["base"]["sha"] == os.environ["EXPECTED_BASE_SHA"]
   ]
   for record in matches:
       print(record["number"])
   PY
   }

   cleanup_probe() {
     cleanup_failed=0
     set +e
     if [ "${pr_ownership_intent:-false}" = true ]; then
       matching_prs="$(owned_probe_pr_numbers)"
       query_status=$?
       if [ "$query_status" -ne 0 ]; then
         echo "cannot discover exact validation PR during cleanup" >&2
         cleanup_failed=1
       elif [ -n "$matching_prs" ]; then
         if [ "$(printf '%s\n' "$matching_prs" | grep -c .)" -ne 1 ]; then
           echo "ambiguous exact validation PRs; preserving all" >&2
           cleanup_failed=1
         else
           cleanup_pr="$matching_prs"
           cleanup_pr_body="$(gh api "repos/$repo/pulls/$cleanup_pr" \
             --jq .body)"
           cleanup_pr_title="$(gh api "repos/$repo/pulls/$cleanup_pr" \
             --jq .title)"
           if [ "$cleanup_pr_body" != \
                "Validation-only disposable PR. Never merge." ] || \
              { [ "$cleanup_pr_title" != "$original_title" ] && \
                [ "$cleanup_pr_title" != "$probe_title" ]; }; then
             echo "validation PR contract changed; preserving it" >&2
             cleanup_failed=1
           else
             if [ "$cleanup_pr_title" != "$original_title" ]; then
               gh api --method PATCH "repos/$repo/pulls/$cleanup_pr" \
                 -f title="$original_title" > /dev/null 2>&1 \
                 || cleanup_failed=1
             fi
             gh pr close "$cleanup_pr" > /dev/null 2>&1 || cleanup_failed=1
             pr_state="$(gh api "repos/$repo/pulls/$cleanup_pr" \
               --jq '[.state, (.merged_at // "")] | @tsv')"
             test "$pr_state" = "$(printf 'closed\t')" || cleanup_failed=1
           fi
         fi
       elif [ -n "${pr:-}" ]; then
         existing_pr_state="$(gh api "repos/$repo/pulls/$pr" \
           --jq '[.state, (.merged_at // "")] | @tsv')"
         if [ "$existing_pr_state" != "$(printf 'closed\t')" ]; then
           echo "recorded validation PR changed or merged; preserving it" >&2
           cleanup_failed=1
         fi
       fi
     fi
     if [ "${push_ownership_intent:-false}" = true ]; then
       remote_probe_ref="$(git ls-remote --heads origin \
         "refs/heads/$probe_branch")" || cleanup_failed=1
       if [ -n "$remote_probe_ref" ]; then
         remote_sha="$(printf '%s\n' "$remote_probe_ref" | awk 'NR == 1 {print $1}')"
         remote_ref="$(printf '%s\n' "$remote_probe_ref" | awk 'NR == 1 {print $2}')"
         if [ "$(printf '%s\n' "$remote_probe_ref" | grep -c .)" -ne 1 ] || \
            [ "$remote_ref" != "refs/heads/$probe_branch" ] || \
            [ -z "$probe_head_sha" ] || [ "$remote_sha" != "$probe_head_sha" ]; then
           echo "remote probe ref changed; preserving it for inspection" >&2
           cleanup_failed=1
         else
           git push --force-with-lease="refs/heads/$probe_branch:$probe_head_sha" \
             origin ":refs/heads/$probe_branch" > /dev/null 2>&1 \
             || cleanup_failed=1
         fi
       fi
       remote_probe_ref="$(git ls-remote --heads origin \
         "refs/heads/$probe_branch")" || cleanup_failed=1
       if [ -n "$remote_probe_ref" ]; then
         cleanup_failed=1
       fi
     fi
     if [ "${local_ownership_intent:-false}" = true ]; then
       if [ -d "$probe_worktree" ]; then
         local_head="$(git -C "$probe_worktree" rev-parse HEAD 2>/dev/null)"
         local_ref="$(git -C "$probe_worktree" symbolic-ref -q HEAD 2>/dev/null)"
         local_root="$(git -C "$probe_worktree" rev-parse --show-toplevel \
           2>/dev/null)"
         local_dirty="$(git -C "$probe_worktree" status --porcelain \
           --untracked-files=all 2>/dev/null)"
         if [ -z "$probe_head_sha" ] || [ "$local_head" != "$probe_head_sha" ] || \
            [ "$local_ref" != "refs/heads/$probe_branch" ] || \
            [ "$local_root" != "$probe_worktree" ] || [ -n "$local_dirty" ]; then
           echo "local probe worktree changed or dirty; preserving it" >&2
           cleanup_failed=1
         else
           git -C "$source_root" worktree remove "$probe_worktree" \
             || cleanup_failed=1
         fi
       fi
       if git -C "$source_root" show-ref --verify --quiet \
            "refs/heads/$probe_branch"; then
         local_branch_sha="$(git -C "$source_root" rev-parse \
           "refs/heads/$probe_branch")"
         if [ -z "$probe_head_sha" ] || \
            [ "$local_branch_sha" != "$probe_head_sha" ] || \
            [ -d "$probe_worktree" ]; then
           echo "local probe branch changed or remains checked out; preserving it" >&2
           cleanup_failed=1
         else
           git -C "$source_root" update-ref -d \
             "refs/heads/$probe_branch" "$probe_head_sha" \
             || cleanup_failed=1
         fi
       fi
     fi
     if [ "${evidence_dir_created:-false}" = true ] && \
        [ "$evidence_dir" = \
          "$source_root/build/test-artifacts/issue-177-live-probe" ]; then
       rm -rf -- "$evidence_dir" || cleanup_failed=1
     elif [ "${evidence_dir_created:-false}" = true ]; then
       cleanup_failed=1
     fi
     return "$cleanup_failed"
   }

   finish_probe() {
     primary_status="$?"
     trap - EXIT INT TERM
     set +e
     cleanup_probe
     cleanup_status="$?"
     if [ "$cleanup_status" -ne 0 ]; then
       echo "live probe cleanup failed; inspect preserved exact resources" >&2
     fi
     if [ "$primary_status" -ne 0 ]; then
       exit "$primary_status"
     fi
     exit "$cleanup_status"
   }
   trap finish_probe EXIT
   trap 'exit 130' INT
   trap 'exit 143' TERM

   test ! -e "$probe_worktree"
   test ! -e "$evidence_dir"
   if git -C "$source_root" show-ref --verify --quiet \
        "refs/heads/$probe_branch"; then
     echo "local probe branch already exists" >&2
     exit 1
   fi
   remote_probe_ref="$(git ls-remote --heads origin \
     "refs/heads/$probe_branch")"
   if [ -n "$remote_probe_ref" ]; then
     echo "remote probe branch already exists" >&2
     exit 1
   fi
   mkdir -p "$evidence_dir"
   evidence_dir_created=true
   candidate_sha="$(git rev-parse "$candidate_branch^{commit}")"
   local_ownership_intent=true
   git worktree add -b "$probe_branch" "$probe_worktree" "$candidate_sha"
   cd "$probe_worktree"
   mkdir -p "$(dirname "$probe_file")"
   printf '{"candidate_sha":"%s","case":"TC-WORKFLOW-BODY-EDIT-001"}\n' \
     "$candidate_sha" > "$probe_file"
   git add "$probe_file"
   git diff --cached --quiet && { echo "probe change is empty" >&2; exit 1; }
   git commit -m "test(ci): add title-only validation probe" \
     -m "Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
   head_sha="$(git rev-parse HEAD)"
   probe_head_sha="$head_sha"
   test "$(git rev-parse "$head_sha^")" = "$candidate_sha"
   test "$(git diff-tree --no-commit-id --name-only -r "$head_sha")" = "$probe_file"
   opened_prior_ids="$(list_build_run_ids)"
   opened_created_after="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
   push_ownership_intent=true
   git push -u origin "$probe_branch"
   existing_prs="$(owned_probe_pr_numbers)"
   test -z "$existing_prs"
   pr_ownership_intent=true
   set +e
   pr_url="$(gh pr create --head "$probe_branch" --base "$candidate_branch" \
     --title "$original_title" \
     --body "Validation-only disposable PR. Never merge.")"
   create_status="$?"
   set -e
   matching_prs="$(owned_probe_pr_numbers)"
   test "$(printf '%s\n' "$matching_prs" | grep -c .)" -eq 1
   pr="$matching_prs"
   test "$(gh api "repos/$repo/pulls/$pr" --jq .title)" = "$original_title"
   test "$(gh api "repos/$repo/pulls/$pr" --jq .body)" = \
     "Validation-only disposable PR. Never merge."
   if [ "$create_status" -ne 0 ]; then
     echo "PR create response failed; recovered exact validation PR $pr" >&2
   fi
   base_ref="$(gh api "repos/{owner}/{repo}/pulls/$pr" --jq .base.ref)"
   base_sha="$(gh api "repos/{owner}/{repo}/pulls/$pr" --jq .base.sha)"
   test "$base_ref" = "$candidate_branch"
   test "$base_sha" = "$candidate_sha"
   ```

   Never use `git commit --allow-empty`, an empty commit, or a merge commit.
   The tracked probe is deterministic for the candidate SHA and the direct
   parent assertion proves the head is a strict nonempty descendant.
2. Discover, watch, and save the opened-event full run:

   ```bash
   opened_run_id="$(discover_build_run \
     "$opened_prior_ids" "$opened_created_after")"
   test "$(gh pr view "$pr" --json headRefOid --jq .headRefOid)" = "$head_sha"
   test "$(gh api "repos/{owner}/{repo}/pulls/$pr" --jq .base.sha)" = "$base_sha"
   watch_build_run "$opened_run_id"
   test "$(gh run view "$opened_run_id" --json event --jq .event)" = "pull_request"
   test "$(gh run view "$opened_run_id" --json headSha --jq .headSha)" = "$head_sha"
   gh run view "$opened_run_id" \
     --json event,headSha,conclusion,url > "$evidence_dir/opened.json"
   gh api --method GET --paginate \
     "repos/$repo/actions/runs/$opened_run_id/jobs" -f per_page=100 \
     | jq -s '.' > "$evidence_dir/opened-jobs.json"
   ```

   - **Parsed live opened-run job set:** {`event-identity`, `event-router`,
     `event-classifier`, `host-tests`, `build`, `extended-host-tests`, `legacy`,
     `patch-release`, `summary`}.
3. Snapshot prior IDs, apply the title-only mutation through the owner REST
   endpoint, then discover, watch, and save its distinct metadata run:

   ```bash
   title_prior_ids="$(list_build_run_ids)"
   title_created_after="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
   gh api --method PATCH "repos/{owner}/{repo}/pulls/$pr" \
     -f title="$probe_title" > /dev/null
   title_run_id="$(discover_build_run "$title_prior_ids" "$title_created_after")"
   test "$title_run_id" != "$opened_run_id"
   test "$(gh pr view "$pr" --json headRefOid --jq .headRefOid)" = "$head_sha"
   test "$(gh api "repos/{owner}/{repo}/pulls/$pr" --jq .base.sha)" = "$base_sha"
   watch_build_run "$title_run_id"
   test "$(gh run view "$title_run_id" --json event --jq .event)" = "pull_request"
   test "$(gh run view "$title_run_id" --json headSha --jq .headSha)" = "$head_sha"
   gh run view "$title_run_id" \
     --json event,headSha,conclusion,url > "$evidence_dir/title.json"
   gh api --method GET --paginate \
     "repos/$repo/actions/runs/$title_run_id/jobs" -f per_page=100 \
     | jq -s '.' > "$evidence_dir/title-jobs.json"
   gh pr checks "$pr" --required > "$evidence_dir/title-required-checks.txt"
   ```

   - **Parsed live title-edit job/check set:** {`event-identity`,
     `event-router`, `metadata-classifier`, `host-tests`, `build`,
     `extended-host-tests`, `legacy`, `patch-release`, `summary`}.

   Every raw REST job record is scanned before normalization. Duplicate API
   IDs, duplicate names/stable IDs, unknown jobs, a metadata `host-tests` or
   `build` record without a runner-backed `success` conclusion, or a metadata
   `extended-host-tests`/`legacy` record with a runner or non-`skipped`
   conclusion fail. GitHub may stamp `started_at` on a platform-skipped record;
   that timestamp is admissible only when `runner_name` is null and the
   conclusion is exactly `skipped`. Every metadata worker record is included
   with its stable ID and canonical worker name rather than hidden behind the
   running metadata classifier/summary names.
   `patch-release` is mandatory in every pull-request run and must have exact
   stable ID/name `patch-release`, conclusion `skipped`, and no runner.
   Missing, successful, failed, renamed, or duplicate publisher context
   rejects both full and metadata evidence.
4. Snapshot IDs before restoring the original title through the owner REST
   endpoint. Discover, watch, and save the distinct restore metadata run:

   ```bash
   restore_prior_ids="$(list_build_run_ids)"
   restore_created_after="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
   gh api --method PATCH "repos/{owner}/{repo}/pulls/$pr" \
     -f title="$original_title" > /dev/null
   restore_run_id="$(discover_build_run \
     "$restore_prior_ids" "$restore_created_after")"
   test "$restore_run_id" != "$title_run_id"
   test "$(gh pr view "$pr" --json headRefOid --jq .headRefOid)" = "$head_sha"
   test "$(gh api "repos/{owner}/{repo}/pulls/$pr" --jq .base.sha)" = "$base_sha"
   watch_build_run "$restore_run_id"
   test "$(gh run view "$restore_run_id" --json event --jq .event)" = "pull_request"
   test "$(gh run view "$restore_run_id" --json headSha --jq .headSha)" = "$head_sha"
   gh run view "$restore_run_id" \
     --json event,headSha,conclusion,url > "$evidence_dir/restore.json"
   gh api --method GET --paginate \
     "repos/$repo/actions/runs/$restore_run_id/jobs" -f per_page=100 \
     | jq -s '.' > "$evidence_dir/restore-jobs.json"
   gh pr checks "$pr" --required > "$evidence_dir/restore-required-checks.txt"
   ```

   - **Parsed live title-restore job/check set:** {`event-identity`,
     `event-router`, `metadata-classifier`, `host-tests`, `build`,
     `extended-host-tests`, `legacy`, `patch-release`, `summary`}.
5. Normalize all three real runs and execute the candidate evaluator's full,
   metadata-only, combined, failed-full, and missing-full assertions:

   ```bash
   python3 - "$head_sha" "$base_sha" \
     "$opened_run_id" "$evidence_dir/opened.json" "$evidence_dir/opened-jobs.json" \
     "$title_run_id" "$evidence_dir/title.json" "$evidence_dir/title-jobs.json" \
     "$restore_run_id" "$evidence_dir/restore.json" \
     "$evidence_dir/restore-jobs.json" <<'PY'
   import copy
   import json
   import sys

   from scripts.workflow_pilot import candidate_evidence

   head_sha, base_sha = sys.argv[1:3]
   run_specs = (
       ("full", int(sys.argv[3]), sys.argv[4], sys.argv[5]),
       ("metadata-only", int(sys.argv[6]), sys.argv[7], sys.argv[8]),
       ("metadata-only", int(sys.argv[9]), sys.argv[10], sys.argv[11]),
   )

   def normalize_run(mode, run_id, run_path, jobs_path):
       with open(run_path, encoding="utf-8") as source:
           raw = json.load(source)
       assert raw["event"] == "pull_request"
       assert raw["headSha"] == head_sha
       assert raw["conclusion"] == "success"
       with open(jobs_path, encoding="utf-8") as source:
           pages = json.load(source)
       raw_jobs = [
           job
           for page in pages
           for job in page["jobs"]
       ]
       stable_by_name = {
           "event-identity": "event-identity",
           "event-router": "event-router",
           "event-classifier": "event-classifier",
           "metadata-classifier": "event-classifier",
           "host-tests": "host-tests",
           "build": "build",
           "extended-host-tests": "extended-host-tests",
           "legacy": "legacy",
           "patch-release": "patch-release",
           "summary": "summary",
       }
       workers = {
           "host-tests",
           "build",
           "extended-host-tests",
           "legacy",
       }
       metadata_adapter_ids = {"host-tests", "build"}
       metadata_skipped_ids = {"extended-host-tests", "legacy"}
       required_names = (
           {
               "event-identity",
               "event-router",
               "event-classifier",
               "host-tests",
               "build",
               "extended-host-tests",
               "legacy",
               "patch-release",
               "summary",
           }
           if mode == "full"
           else {
               "event-identity",
               "event-router",
               "metadata-classifier",
               "host-tests",
               "build",
               "extended-host-tests",
               "legacy",
               "patch-release",
               "summary",
           }
       )
       seen_api_ids = set()
       seen_names = set()
       seen_stable_ids = set()
       contexts = []
       for job in raw_jobs:
           api_id = job["id"]
           name = job["name"]
           started_at = job["started_at"]
           assert isinstance(api_id, int) and api_id > 0
           assert api_id not in seen_api_ids
           assert isinstance(name, str) and name
           assert name not in seen_names
           assert name in stable_by_name
           job_id = stable_by_name[name]
           assert job_id not in seen_stable_ids
           seen_api_ids.add(api_id)
           seen_names.add(name)
           seen_stable_ids.add(job_id)
           assert name in required_names
           if mode == "metadata-only" and job_id in metadata_adapter_ids:
               assert job["conclusion"] == "success"
               assert isinstance(job["runner_name"], str) and job["runner_name"]
               assert isinstance(started_at, str)
           elif mode == "metadata-only" and job_id in metadata_skipped_ids:
               # GitHub may stamp started_at on a skipped job, but a real
               # runner is never assigned: both facts are required together.
               assert job["conclusion"] == "skipped"
               assert job["runner_name"] is None
               assert started_at is None or isinstance(started_at, str)
           elif job_id == "patch-release":
               assert job["conclusion"] == "skipped"
               assert job["runner_name"] is None
           else:
               assert name in required_names
               assert job["conclusion"] == "success"
           contexts.append(
               {
                   "conclusion": job["conclusion"],
                   "job_id": job_id,
                   "name": name,
               }
           )
       assert required_names <= seen_names
       return {
           "base_sha": base_sha,
           "contexts": contexts,
           "event": "pull_request",
           "head_sha": head_sha,
           "run_id": run_id,
       }

   opened, title, restore = [
       normalize_run(*spec)
       for spec in run_specs
   ]
   opened_result = candidate_evidence.evaluate_candidate_runs(
       [opened], head_sha=head_sha, base_sha=base_sha
   )
   assert opened_result.eligible and opened_result.run_id == opened["run_id"]
   title_result = candidate_evidence.evaluate_candidate_runs(
       [title], head_sha=head_sha, base_sha=base_sha
   )
   assert not title_result.eligible and title_result.mode == "metadata-only"
   full_title_result = candidate_evidence.evaluate_candidate_runs(
       [opened, title], head_sha=head_sha, base_sha=base_sha
   )
   assert full_title_result.eligible
   assert full_title_result.run_id == opened["run_id"]
   latest_full = candidate_evidence.latest_contexts([opened, title])
   assert latest_full["summary"] == (title["run_id"], "success")
   assert latest_full["host-tests"] == (title["run_id"], "success")
   assert latest_full["build"] == (title["run_id"], "success")
   assert "metadata-summary" not in latest_full
   for job_id in candidate_evidence.METADATA_SKIPPED_JOB_IDS:
       assert latest_full[job_id] == (title["run_id"], "skipped")
   failed_opened = copy.deepcopy(opened)
   next(
       context
       for context in failed_opened["contexts"]
       if context["job_id"] == "summary"
   )["conclusion"] = "failure"
   failed_result = candidate_evidence.evaluate_candidate_runs(
       [failed_opened, title], head_sha=head_sha, base_sha=base_sha
   )
   assert not failed_result.eligible
   assert candidate_evidence.latest_contexts([failed_opened, title])["summary"] == (
       title["run_id"],
       "success",
   )
   all_runs_result = candidate_evidence.evaluate_candidate_runs(
       [opened, title, restore], head_sha=head_sha, base_sha=base_sha
   )
   assert all_runs_result.eligible
   assert all_runs_result.run_id == opened["run_id"]
   restore_result = candidate_evidence.evaluate_candidate_runs(
       [restore], head_sha=head_sha, base_sha=base_sha
   )
   assert not restore_result.eligible and restore_result.mode == "metadata-only"
   failed_restore_result = candidate_evidence.evaluate_candidate_runs(
       [failed_opened, title, restore], head_sha=head_sha, base_sha=base_sha
   )
   assert not failed_restore_result.eligible
   PY
   ```

   The title-only and restore runs alone prove the missing-full negative; the
   copied failed summary proves the failed-full negative without inventing a
   success-shaped fallback.
6. Run exact idempotent cleanup explicitly. The EXIT trap performs the same
   cleanup automatically on any earlier failure:

   ```bash
   exit 0
   ```

   Cleanup restores the original title if necessary, closes without merging,
   deletes the exact remote ref only through a SHA compare-and-swap lease,
   removes the exact isolated worktree/local ref only when their head/ref match
   and the worktree is clean, and deletes only the guarded exact evidence
   directory. A mismatched remote SHA, PR identity, local head/ref/path, or
   dirty worktree is preserved and reported. The EXIT trap retains the primary
   failure status while surfacing cleanup failure.
   Architecture/review comments remain unmarked; only the canonical evolving
   evidence comment carries the one marker.

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
   defaults/advisory/environment/concurrency/uses/secrets/shell execution
   context, and mutate the exact classifier `needs`/`if` edge, including
   spaced, quoted, escaped, tagged, explicit, flow, duplicate, reordered, and
   wrong-value forms.
10. Place a real `sitecustomize.py` that exits successfully before ordinary
    Python commands. Confirm normal startup is bypassed while the three
    baseline `/usr/bin/python3 -I` launcher modes and the event-classifier mode
    execute, and arbitrary modes, arguments, roots, or launcher/`-I` changes
    fail.
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
topology suite fails if classification, hydration, pre-pilot step
order/content, scrubbed protected-step environments, ownership, or
checked-out-root binding is removed or weakened. It also requires the
router, mode-classifier, and each combined worker's exact direct job mapping and values; no
container or alternate execution context can replace the reviewed Ubuntu
host.
The no-checkout identity validator and protected Python steps use only closed
trusted setup, so repository or
user site customization cannot run before control. The workflow mirror parser
uses deterministic line/indent parsing rather than an ambiguous multiline
regular expression. Cross-checkout verification parses target workflow data
without importing target code and requires complete source/target job and step
sequences: counts/order, unique names, setup-versus-gate roles, immutable
actions, run argv, `env`/`with` mappings, direct fields, and root execution
before dry-run or execution. Unnamed non-checkout steps, duplicate setup names,
complex keys, and extra jobs fail closed. Workflow execution context is
exactly name/triggers/read-only permissions/jobs with no workflow env,
defaults, or concurrency. The identity validator is exactly Ubuntu, five minutes, its
outputs/environment, and one trusted shell step. The router is exactly Ubuntu,
five minutes, its outputs/environment, and three setup steps; the
mode-classifier is a separate five-minute one-step check. The comprehensive
`build` job has exact identity/classifier edges, Ubuntu, 90 minutes, its
allowlisted env, and steps; host, extended-host, legacy, and patch publication
remain 60 minutes, while identity/router/classifier and summary remain 5;
self-hosted/container/service/strategy/default shell or any other execution
field fails before dry-run.
Patch publication and summary are also complete semantic structures:
validated master-only publication condition, pinned actions, immediate exact
revision verification, exact-after producer, no target-ROM artifact transfer,
dedicated-UID private-mount/PID/network isolation, read-only host paths, masked
service sockets, offline dependencies, exact cgroup-v2/process teardown, exact
regular/single-link two-file handoff, candidate-state removal before download,
discarded non-replayed candidate output with fixed numeric status, unpredictable
private path, immediate isolated patch tool, verified cleanup, late
BPS/manifest/README revalidation, and nine fresh-job publisher steps;
then `always()`, identity/classifier plus exact ordered
worker/publisher needs/result env, dynamic full/metadata summary name, five-minute
context, and one fail-closed summary step.
Neither is locally executed, but any
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

## TC-WORKFLOW-OWNERSHIP-PROBE-SANDBOX-001: Confine and bound authentic probe execution

### Feature and configuration

Issue [#206](https://github.com/laqieer/fireemblem8-expansion/issues/206);
supported Linux x86-64 source checkout, GNU Make 4.3, Python 3, a static-capable
host C compiler, C++ compiler for native-tool controls, and working private
namespaces. See the [foundation contract](../ownership-probe-foundation.md).
No ROM, emulator, credentials, remote workflow or feature flag is required.
Start from a clean checkout; fixtures use only ignored `build/test-artifacts`.

### Actions

1. Run `make -f scripts/validation_ownership/foundation.mk ownership-probe-check`.
   Inspect the returned native `localization-check` dependency on
   `localization-generate`, the actual output-directory value and the real
   chapterbundle registry result containing `src/data/ch2_bundle.json`.
2. Run `make -f scripts/validation_ownership/foundation.mk ownership-probe-test`.
   Positive fixtures use GNU Make include/define/eval, finite domain values,
   patterns, target variables and order-only prerequisites. Their typed
   observations must describe actual targets, not candidate stdout.
3. The same suite compiles benign pre-fix `load`/native-SHELL payloads and
   demonstrates actual writes to an explicitly inherited test FD. The confined
   payloads must reject without a forged byte. File/include/eval, supervisor
   paths, device/proc FD paths, alternate SHELL/loaders, shell flags and
   stdout-shaped observations cannot forge channels. Canonical argv preserves
   argument boundaries and equivalent quote spellings.
4. Observe actual admitted open/mmap/stat/glob/directory inputs. Repeat with
   undeclared and dynamically constructed paths, caught exceptions, unused or
   falsely reported source declarations, and symlink/FIFO controls. Every
   mismatch must fail closed. Real C/C++ candidate tools compile and run only
   in channel-free capsules; changed ELF handles and channel/FD access reject.
   The alias controls create a relative symlink from a deeper cwd and relocate
   a cwd/dirfd ancestor before a `..` lookup. All symlink and rename variants
   must reject before dispatch; they cannot hide an undeclared attempt under
   a recorded `/work` path.
   An owned, namespace-only runtime alias fixture also checks relative and
   absolute trusted symlinks: declared source open/mmap still reports the real
   source, while direct, cwd and directory-FD undeclared accesses reject.
5. Change unrelated docs/source/modes/symlink targets in the disposable fixture:
   execution identity changes while the unrelated owner's semantic digest
   remains stable. Change a real owner input and require changed semantics and
   command bytes; old Git entry IDs must not cause stale output reuse.
6. Exhaust variant, process, pending-request, event, mapping, output, cache,
   scratch-write and file-creation limits. Two individually short real
   processes cannot reset the aggregate deadline. Malformed binary/text
   protocols, worker failure and SIGTERM interruption must reject and leave
   no live owned child, descriptor, mapping, cache or owned scratch tree.
   Creation controls include `O_TMPFILE`, `creat`, directory creation,
   hardlinks/`linkat`, `AT_EMPTY_PATH` and a one-creation limit across commands.
   Supported attempts consume quota before dispatch; symlink, relocation and
   special-file alternatives reject rather than escaping accounting.
7. Run the same suite's mapping controls. A read-only/`PROT_NONE` shared
   anonymous mapping upgraded writable and inherited by a child must reject,
   as must mutable file backing even through a read-only private mmap and
   duplicated/closed descriptors. The benign unconfined control demonstrates
   child-written pathname and `writev` vector bytes reaching real syscalls.
   Writable protection upgrades, remap clones/fixed/DONTUNMAP aliases and
   alternate memory APIs reject. Private COW fork, ordinary private resize,
   read-only source mmap and suspended-parent native spawn remain positive.
8. The bootstrap controls reproduce a nondumpable stopped child and a
   pathname ending at an unmapped page. The old memory reads fail with `EIO`.
   Post-drop observation must work without restoring credentials/capabilities,
   and NUL-terminated boundary strings must read correctly while malformed
   UTF-8 and overlong paths still reject. Every control reaps its own child.
9. Exercise the same suite's lifecycle controls: complete a worker that leaves
   an owned child, close its caller lifetime pipe, exhaust the shared deadline,
   overflow output, and interrupt it. The watchdog must reap its entire owned
   tree before returning. Modeled outer `PermissionError` cannot bypass that
   cleanup. Missing kernel support or a missing/closed lifetime pipe must reject
   before launch. These fixtures use same-UID processes, never real sudo; hold
   any claim of full sudo-route validation until separate exact-candidate
   evidence actually exercises that credential transition.
   The same-UID adapter validates the production launcher contract but runs the
   actual watchdog/payload without `unshare`, including on hosts that deny user
   namespaces. Budget/interruption controls require a payload-start marker and
   the intended error, not an early launcher failure. Real capsule namespace
   execution remains a separate production-path check.
10. Capture Make's parsed ELF interpreter and runtime closure, then run the real
    Make observation with only an Arch-shaped, non-multiarch `/usr/lib` library
    layout. Require the authentic prerequisite/value and read-only captured
    files, even after the fixture's runtime source map is cleared. Mutable or
    non-system aliases, malformed ELF headers and unresolved dependencies
    reject. This controlled layout is not a native Arch host validation claim.
11. Attempt `chmod('/work', 0)`, nested and directory-FD permission removal,
    restrictive directory creation and owner-masking umasks, even while catching
    errors in the candidate. Require the original policy rejection and complete
    owned scratch removal, not a cleanup `PermissionError`. Confirm that safe
    directory permissions, regular-file `fchmod`, and real C/C++ compiler output
    remain supported. Fixtures retain only their own directory FDs to safely
    restore permissions if a regression re-admits the old behavior.
12. Run the same suite's ordinary-Make context comparisons. Conditional
    prerequisites must match real `/usr/bin/make` for `SHELL`/`MAKE` values and
    origins, `.SHELLFLAGS` origin, and dry-run/always-make/job/print flags.
    Compare complete value/origin/flavor records in default, file-shell,
    requested-domain and POSIX contexts. Identical recipe and `$(shell)` argv
    must remain metadata-only and value-bearing respectively; recursive and
    Makefile-remake commands still require actual registered mappings.
13. Reproduce the benign `GNUMAKEFLAGS=--eval=INJECTED=yes` control in the owned
    fixture with ordinary Make; its graph changes. Passing that option channel
    through either probe assignment origin must reject before a Make launch.
    Private dispatch queries, observer-image reads and control-path reads must
    not become a candidate-controlled context or evidence channel.
14. Fail scratch construction after new parents exist: use a tracked leaf,
    overlong component, injected open/mkdir failures, an inaccessible owned
    directory for an unprivileged runner, and interruption. All new parents and
    FDs must be released, while an existing parent/sentinel survives.
    SIGTERM delivered during allocation must wait until ownership is recorded.
    A modeled cleanup failure must preserve the primary setup exception; the
    fixture teardown, not production code, removes that deliberately retained
    test residue.

### Expected result

The real consumer reports the native localization prerequisite and exactly the
declared chapterbundle source. Candidate programs cannot forge observation
channels or hide undeclared source access; unsupported aliases, shared mutable
memory and resource exhaustion reject before unsafe work proceeds. Immutable
source mmap, private COW fork and valid boundary pathnames remain supported.
Execution snapshots change independently of unrelated semantic owner identity,
and every failure clears owned processes, channels, mappings and caches.

### Negative control

The pre-fix native Make processes really forge the inherited test channel;
unconfined source functions really read/stat/enumerate undeclared fixture
paths; a per-process timeout really admits work beyond a single total budget.
These controls are restricted to disposable test inputs and are not a
production bypass switch. Normal positive behavior is tested alongside every
boundary; no failure is converted to successful evidence.

The original #206 guard additionally admitted relative symlink and relocated
cwd/dirfd aliases, shared mapping upgrades and private mappings of mutable
backing files; it reported zero creations for `O_TMPFILE` and omitted hardlink
entries. The frozen process regressions fail against that guard. Their benign
unconfined mapping controls use only owned buffers, fixture paths and reaped
child PIDs; no timed race against unrelated host data or process is required.

The prior sudo caller's unprivileged `killpg` raised `PermissionError` before
reaping; the lifecycle contract reproduces that failure without acquiring
privileges. The prior Make root attempted a nonexistent Debian multiarch libc
on a non-multiarch layout. A real confined `chmod('/work', 0)` followed by a
failing command formerly masked the original error with cleanup
`PermissionError` and left an inaccessible owned directory. The new regressions
preserve these negative controls while limiting all effects to owned fixtures.

Before the normal-context correction, benign ordinary Make selected `genuine`
while the probe selected `hidden` for seven independent flag/value/origin
conditions. `GNUMAKEFLAGS --eval` also injected an unrequested definition.
Partial setup rejected a tracked leaf but left newly created parents behind
because ownership had not reached the session. These are behavioral negative
controls, not source-spelling checks.

### Interactions and save compatibility

This host-only contract changes no save, migration, config identity, generated
game content, localization, modern/archival behavior or ROM/RAM. Other feature
interactions: none. The authority and existing generated-registry schema stay
shared; PR186/#180 must still perform their downstream graph adoption.

### Automation

`python3 -m unittest scripts.validation_ownership.tests.test_foundation -v`
executes the real process scenarios, native compile/link checks, parsed ELF and
binary/JSON protocols, exact source sets, semantic identity and cleanup.
The standalone Make check is a separate real consumer, not a test-name alias.
No subjective/manual-only criterion applies.

### Cleanup and limitations

Fixtures and session channels are removed automatically. Remove the empty
`build/test-artifacts/ownership-foundation-tests` parent if desired. No remote
state is read or changed.

The introducing root does not claim PR186's full graph, domain matrix, oracle,
lifecycle or absent-on-master `validation-ownership-check` target. Those remain
explicit downstream integration gates under #180. Unsupported native Make
ABIs/platforms fail rather than running a weaker probe. Roll back by reverting
this dedicated foundation; broader validation remains required.
