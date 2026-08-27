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
   commit-pinned blob links, plus GitHub user attachments.
4. Run
   `python3 -m unittest scripts.docs_check_tests.test_development_workflow_skill -v`.
5. Open the documented queue:
   [`repo:laqieer/fireemblem8-expansion is:open assignee:laqieer label:"waiting-for-manual-testing"`](https://github.com/laqieer/fireemblem8-expansion/issues?q=repo%3Alaqieer%2Ffireemblem8-expansion+is%3Aopen+assignee%3Alaqieer+label%3A%22waiting-for-manual-testing%22).
   When it is empty, do not schedule notifications or comments.

### Expected result

The focused suite accepts the canonical JSON and its supported queue shapes.
Human guidance links to that file without duplicating machine behavior.

### Lifecycle summary

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
instrumented artifacts, missing or misplaced mention, invalid or empty numbered
steps, false or mistyped comment holds, missing per-item comments, permissive
activation, wrong identifiers or targets, disabled holds, incomplete historical
cleanup, missing or mismatched completion evidence, malformed optional
booleans, duplicate artifact identities, bare or unrelated evidence pages,
malformed PR origins, rejected cleanup/resumption, empty-queue notifications,
and invalid independently discovered issue/PR relationships. Reversing any
lifecycle summary action or removing this case's own subsection also fails
locally.

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
