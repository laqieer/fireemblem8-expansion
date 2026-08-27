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
3. Inspect the synthetic `opened`, `synchronize`, `reopened`, and base-change
   `edited` pull-request fixtures, plus inline and block `branches` and
   `branches-ignore` mutations.

### Expected result

The synthetic non-master-base pull request selects the existing `host-tests`,
`build`, `extended-host-tests`, `legacy`, and fail-closed `summary` jobs. Every
candidate worker still checks out and verifies `pull_request.head.sha`. The
publisher is absent from pull-request execution, while a push to `master`
selects it and a push to any other branch selects no workflow jobs.

The child remains based on its immediate parent while that parent is open, and
exact-head Build CI and Copilot review run against that genuine base. After the
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
checkout verification, or documenting a temporary base flip solely to trigger
CI makes the focused suites fail.

### Interactions and save compatibility

This source-only contract depends on the existing combined Build jobs, exact
checkout binding, trusted owner-context pushes, and one watcher per exact run.
It conflicts with temporary base retargeting, stale evidence after a base/tree
change, duplicate workflows, duplicate matrices, weakened permissions, and PR
publication. It changes no save, generated data, localization, ROM/RAM,
debug/release, or archival behavior and needs no feature gate.

### Automation

`python3 -m unittest discover -s tests/workflows -p "test_*.py" -v` parses the
workflow, evaluates synthetic PR actions and push metadata, rejects inline and
block PR branch filters, checks the base-change `edited` event with an
unchanged head SHA, verifies exact-head checkout binding, and preserves the
fail-closed summary and publisher boundary.

`python3 -m unittest scripts.docs_check_tests.test_development_workflow_skill -v`
validates the genuine-stack workflow and rejects guidance that relies on a
temporary base flip solely to trigger CI.

### Cleanup and limitations

No cleanup is required. The case evaluates committed workflow and governance
contracts without dispatching GitHub Actions; it does not prove live service
availability or grant credentials.
