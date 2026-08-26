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
  the development-workflow skill, contributor guide, this case, and registry
  unchanged. The repository label `waiting-for-manual-testing` has description
  `Blocked until @laqieer records a specific manual tester result`.

### Actions

1. Confirm the proposed criterion is one precise, material visual, audio, or UX
   judgment that semantic automation cannot reliably decide. Reject vague
   review requests and deterministic behavior the agent can verify.
2. Before any handoff, render real non-instrumented positive and
   negative/control artifacts from the exact candidate commit, drive the same
   deterministic input route, and inspect deterministic screenshots for static
   UI or a short synchronized emulator A/V clip for time-dependent or
   audiovisual behavior. Keep semantic assertions as the primary evidence.
3. For an actionable criterion, verify the documented lifecycle requires the
   agent to apply the label to the originating issue and each open
   implementation PR, assign both items to `laqieer`, comment on each item, and
   explicitly ping `@laqieer`.
4. Verify each handoff comment includes the tester-case ID, exact commit,
   exact artifact path or link, artifact hash, environment, clean starting
   state, numbered steps, expected result, negative/control artifact, one
   precise judgment, and an explicit statement that merge and issue closure
   are blocked.
5. Verify an accepted result requires the lifecycle to post the actual result
   and evidence link, remove the label from the issue and PR, remove the
   temporary assignment unless another ownership reason remains, and
   automatically resume exact-candidate gates and merge.
6. Run
   `python3 -m unittest scripts.docs_check_tests.test_development_workflow_skill -v`.
7. Open the documented queue:
   [`repo:laqieer/fireemblem8-expansion is:open assignee:laqieer label:"waiting-for-manual-testing"`](https://github.com/laqieer/fireemblem8-expansion/issues?q=repo%3Alaqieer%2Ffireemblem8-expansion+is%3Aopen+assignee%3Alaqieer+label%3A%22waiting-for-manual-testing%22).
   When it is empty, do not schedule notifications or comments.

### Expected result

The focused suite parses the mirrored handoff sections and registry entry.
Only an actionable non-agent-verifiable visual, audio, or UX judgment enters
the queue. The issue and open PR become discoverable and assigned with a
complete artifact-specific request; merge and closure remain blocked until the
one judgment is accepted; cleanup removes temporary tracking; and delivery
resumes automatically.

### Negative control

Mutations that remove or change the exact label, explicit `@laqieer` ping,
issue-and-PR assignment, clickable query URL, pre-handoff screenshot/A/V
inspection, merge-and-closure hold, or accepted-result cleanup lifecycle must
fail the focused suite. Phrase-preserving polarity reversals such as `do not
apply` or `do not remove` must also fail. This narrowly scoped exact-source
coverage is required because label names, mentions, assignees, and query URLs
are an externally consumed GitHub workflow protocol; the remaining assertions
are parsed as labeled document-contract fields with affirmative activation,
hold, completion, cleanup, and resume semantics.

### Current queue audit

Repository evidence audited on 2026-08-26 requires no current label
application:

- issue #83 has a recorded accepted result for the replacement
  non-instrumented listening artifacts;
- issue #168 is agent-verifiable static UI and should use deterministic
  screenshots plus semantic assertions rather than a manual hold; and
- issues #90, #91, and #92 and their open implementation PRs have no current
  manual criterion.

The expected current queue is therefore empty. Do not label, assign, ping, or
schedule empty-queue reminders for these items.

### Interactions and save compatibility

The protocol depends on GitHub issues, pull requests, labels, assignments, and
comments when a real hold is active. It conflicts with blanket human review,
manual gates for deterministic behavior, unlabeled requests, and leaving stale
labels or assignments after acceptance. It changes no save, generated data,
localization, ROM/RAM, debug/release, or archival behavior.

### Automation

`python3 -m unittest scripts.docs_check_tests.test_development_workflow_skill -v`
parses the mirrored labeled policy, validates the indexed case, and exercises
fail-closed mutations of every externally consumed lifecycle boundary.

### Cleanup and limitations

This source-only case changes no remote item. A real accepted handoff must
remove the label from the issue and PR and remove the temporary `laqieer`
assignment unless another ownership reason remains. The test cannot make the
subjective judgment itself; it proves that only such a judgment can enter the
manual queue and that its state cannot remain stale afterward.
