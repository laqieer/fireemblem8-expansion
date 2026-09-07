---
name: development-workflow
description: Triage and deliver feature requests and bug fixes for fireemblem8-expansion. Use when asked to review an idea, accept or reject a feature, investigate or fix a bug or regression, add a justified build gate, validate behavior, create or review a PR, merge it, or close its issue.
---

# Development workflow

Use this skill for incoming feature requests, bug reports, and regressions that
may change the supported expansion framework. Do not use it for routine
dependency maintenance or archival decomp matching unless that work is part of
an accepted feature or bug fix.

The governing principle is:

> Keep the core generic, make optional behavior explicit, prove both enabled
> and disabled paths, ship documentation with the change, merge autonomously
> when objective evidence is complete, and leave work open only for a
> precisely named criterion the agent cannot validate reliably.

Follow every phase below. Do not skip directly from implementation to delivery.
For feature requests and bug fixes, this skill implements Discussion #30 and
is authoritative over conflicting generic review or closure guidance elsewhere
in the repository. Do not add a human code-review or approval gate.

Before adding a prerequisite, gate or service in this repository, identify the
original accepted requirement or concrete risk, accepted threat model, smallest
existing mechanism, and why a simpler solution is insufficient. Review findings
do not automatically expand requirements: fix accepted-contract bugs, simplify
architecture-created problems, and separate optional hardening with honest
claims. At the existing third-round/8K reconsideration point, compare a concrete
simpler design and remove unnecessary machinery or split complete independent
contracts when needed—not merely write another note. Measure the original
end-to-end outcomes and total delivery cost; preserve real safety requirements
and every final gate.

## Phase 1: establish state

1. Read the complete issue or discussion, including comments and linked PRs.
2. Inspect the current worktree, branch, upstream, relevant implementation,
   tests, documentation, and recent history. Do not assume the request still
   matches the current tree.
3. Verify an ARM debugger is available as `arm-none-eabi-gdb` or
   `gdb-multiarch`. Run `./scripts/quickstart.sh` or install the platform
   package if neither command works.
4. Search for an existing public seam, registry, schema, flag, scenario, or
   validation target before designing another one.
5. Identify dependencies and conflicts between the request, already accepted
   requirements, existing feature profiles, save/config identities, generated
   data, IDs, memory budgets, and shared runtime seams. Keep this relationship
   map active throughout implementation.
6. For a multi-issue objective, build a dependency graph before editing. Treat
   each independent issue as its own root delivery unit based on `master`; use
   a stack only when a child issue genuinely requires another issue's unmerged
   code or contract.
7. For tracked changes, create per-issue dependent work items for
   implementation, validation, commit, push, candidate-commit CI, merge,
   post-merge verification, issue closure, and the remote completion gate.
8. Resolve ordinary ambiguity from repository evidence. Stop at `Needs design`
   only when a material contract cannot be chosen safely.

## Investigation resources and tools

Use the smallest authoritative resource set that can answer the question:

1. Start with this repository's source, documentation, tests, generated-data
   sources, Git history, and runtime scenarios.
2. Use sibling decompilation sources such as `../fireemblem8u` and
   `../fireemblem8j` when upstream/original behavior or symbol context matters.
3. Use `../FEBuilderGBA`, including its initialized Git submodules, and
   relevant Nightmare modules for editor/data-format behavior.
4. Use legally obtained clean ROMs under `../GBA-FE-ROMS` only for local
   investigation. Never copy, commit, publish, or embed those ROMs in this
   repository or its evidence comments.

Use tools according to the evidence needed:

- ARM GDB (`arm-none-eabi-gdb` or `gdb-multiarch`) for register, stack, symbol,
  memory, and control-flow debugging.
- mGBA's GDB server plus `make expansion-modern-gdb-smoke` to prove the
  cross-compiled debug ELF can be controlled through a real emulator target.
- IDA Pro/IDALib CLI or MCP as the preferred primary disassembler/decompiler
  when available; project experience finds it more stable.
- Ghidra/PyGhidra CLI or MCP as a cross-check, fallback, or batch-analysis
  path.
- `arm-none-eabi` binutils and repository scripts for symbol, relocation,
  section, and disassembly checks.

Install another local tool when investigation shows a concrete benefit and its
source is trusted. Record every tool installed for the task, its version, and
why it was needed in the final issue/discussion comment. Never record
credentials, copy restricted ROM content, or send repository/ROM data to an
unapproved third party.

## Phase 2: triage and classify the request

### Bug triage

For a bug report or regression, triage the bug before designing or editing:

1. State the expected and actual behavior, affected configurations, and
   severity.
2. Reproduce the failure with the smallest deterministic test or runtime
   scenario available. If direct reproduction is impossible, establish the
   failure from concrete code, logs, history, or invariant evidence and state
   that limitation.
3. Use ARM GDB when register, stack, symbol, memory, or control-flow state is
   needed to establish the failure or root cause. Prefer debugger evidence over
   speculative source edits.
4. Preserve the failing evidence. Do not refresh a fingerprint, weaken an
   assertion, or add a success-shaped fallback.
5. Trace the root cause and every coupled surface that must change together.
6. Classify the report as **Confirmed**, **Needs evidence**, **By design/not a
   bug**, **Duplicate**, or **Already fixed**.

Proceed with implementation only for a confirmed bug. A bug fix does not need
to pass the feature universality test below; it must restore an intended
contract, fix the root cause, and add a regression test that fails for the
original defect. It must also retain the deterministic reproduction and freeze
at least one tester-facing regression case whose pre-fix behavior is the
negative control.

### Feature classification

For a feature request, return exactly one of these classifications before
implementation:

1. **Framework capability**: reusable infrastructure with a stable,
   typed or data-driven extension surface. Accept.
2. **Optional reusable module or reference implementation**: broadly useful
   behavior that projects may choose differently. Accept with a validated,
   usually default-off profile.
3. **Project-specific content or ruleset**: hard-coded characters, chapters,
   skills, assets, balance values, or an opinionated game design. Decline it
   from the framework core and identify the generic hook or schema through
   which a downstream project can implement it.
4. **Needs design**: potentially reusable, but its acceptance criteria,
   compatibility impact, integration seam, or maintenance cost is not yet
   concrete.

A core proposal must satisfy all of these checks:

- It supports at least two plausible project uses without hard-coded game
  content.
- It reuses or introduces one narrow public seam instead of duplicating a
  router, registry, or data source.
- Its dependencies, dependents, conflicts, and supported feature/profile
  combinations can be stated and validated.
- Its default-disabled or default-configured build preserves intended
  behavior.
- Its ROM, RAM, save, generated-data, debug/release, configuration, and
  archival-lane impact can be stated explicitly.
- It has a maintainable automated validation strategy.

Examples:

- A plain-text fallback for chapter-title rendering is a framework capability.
- A gameplay skill catalog and project-specific effects are not framework
  core. A generic typed trigger/effect registry may be.
- A 1RN versus 2RN rule is a reusable project choice, but it should plug into a
  generic hit-calculation or mechanics seam instead of adding another direct
  conditional throughout battle code.

When declining a request from core, explain the boundary and the reusable seam
that would make downstream implementation possible. Do not dismiss the use
case without that guidance.

Do not accept a feature request without at least one proposed tester-facing
case. If the reusable behavior is plausible but no complete case can yet be
frozen, classify it as `Needs design` rather than inventing acceptance
evidence during implementation.

## Phase 3: freeze the design contract

Record these items in the issue or implementation notes before editing:

- itemized scope and explicit non-goals;
- observable acceptance criteria;
- classification and rationale;
- dependency/dependent/conflict matrix, including explicit `none` entries;
- public API, data, configuration, and integration contracts;
- affected modern debug/release profiles and any archival-lane impact;
- save-format or migration impact;
- generated-data and localization impact;
- ROM/RAM/resource-budget impact;
- tester-facing case IDs and their positive and negative evidence;
- focused host checks, target-ROM checks, runtime scenarios, and negative
  controls;
- rollback or revert plan for a post-merge regression.

Prefer typed APIs, generated data, symbolic IDs, and existing registries. Do not
introduce a second subsystem when the current public seam can be extended.
When implementation reveals a new dependency or conflict, update the frozen
contract and every affected validator/test/document before continuing.

## Tester-facing case contract

Before accepting a feature request or implementing a confirmed bug fix, freeze
at least one tester-facing case. Stable case IDs are never silently renumbered
or reused; use a readable form such as `TC-SAVE-001`. The canonical indexed
catalog and coverage guard are tracked by
[issue #54](https://github.com/laqieer/fireemblem8-expansion/issues/54).
Until that catalog lands, keep each proposed ID and its complete definition in
the originating issue and relevant feature or bug documentation. Do not create
a competing registry.

Each tester-facing case must state:

- its stable case ID and linked feature or bug issue;
- the supported configuration/profile or downloadable artifact, while
  remaining runnable from a documented source build;
- prerequisites, clean starting state, and reset or cleanup requirements;
- exact actions or inputs;
- the observable expected result;
- the default/disabled or pre-fix negative control, or why one is not
  applicable;
- dependencies, conflicts, feature interactions, and save-compatibility
  expectations;
- mapping to deterministic host/ROM automation, or the precise reason a
  criterion remains manual; and
- known limitations and unsupported configurations.

A bug report and fix require deterministic reproduction plus at least one
regression case; the regression case does not replace the triage evidence. A
feature request without at least one proposed tester-facing case cannot be
accepted for implementation.

During implementation, ship or update the tester-facing procedure with the
behavior and its feature/bug documentation. Do not defer it to post-merge
cleanup. Preserve the human steps even when automation covers every assertion;
a tester must not have to reverse-engineer a unit test or scenario file.

Automate every deterministic assertion in the case. Manual-only evidence is
reserved for a precisely named visual, audio, or UX judgment that cannot be
asserted reliably. Record the exact manual criterion and environment. If it is
material and remains unverified, hold merge and closure on that criterion
rather than requesting generic review.

## Actionable manual-testing handoff

The canonical machine-readable protocol is
[`manual-testing-handoff.json`](../../manual-testing-handoff.json). Follow its
eligibility, preview, activation, hold, completion, and queue fields exactly;
it is authoritative over prose summaries. This is not a human review gate:
deterministic criteria remain the agent's responsibility, and manual handoff
is limited to the visual, audio, or UX judgments admitted by the contract.
The contract requires identified non-instrumented positive and control
artifacts plus deterministic emulator screenshots or synchronized emulator A/V
evidence before activation. GitHub-linked open PR discovery determines the
active targets. Completion cleans the label from every labeled item and removes
the temporary assignment unless independent ownership remains, including for a
PR later closed or superseded. Each handoff comment uses the contract's exact
`@laqieer` mention, at least one numbered step, and true merge/closure holds;
every actionable issue and linked open PR carries its own typed comment with
stable case ID, full Git SHA, artifact paths and SHA-256 values, and nonempty
environment, state, expectation, and judgment fields.
Before cleanup and automatic resumption, every labeled item also receives a
typed completion comment whose nonblank actual result and GitHub evidence link
match the original handoff case ID and commit. Only an accepted outcome permits
cleanup and resumption; a rejected outcome retains both holds and remains
actionable.
Evidence links identify a same-repository issue/PR comment, review comment,
workflow run or artifact, commit-pinned blob, or GitHub user attachment; bare
issue and PR pages are not completion evidence. Positive and control artifact
path-plus-SHA-256 identities must differ.
Before accepted cleanup or resumption, every open implementation PR's current
GitHub head SHA must equal its activation-comment commit; a changed head needs
a fresh handoff and evidence. Retaining `laqieer` requires a nonblank independent
ownership reason. Rejected evidence keeps the label, assignee, both holds, and
actionable state.

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

Use the documented queue:
  [`repo:laqieer/fireemblem8-expansion is:open assignee:laqieer label:"waiting-for-manual-testing"`](https://github.com/laqieer/fireemblem8-expansion/issues?q=repo%3Alaqieer%2Ffireemblem8-expansion+is%3Aopen+assignee%3Alaqieer+label%3A%22waiting-for-manual-testing%22).
The contract permits issue-only handoff when GitHub relationship discovery
finds no open implementation PR, requires every linked open implementation PR
otherwise, and sends no notification when the queue is empty.

The indexed source-only regression for this protocol is
[`TC-WORKFLOW-MANUAL-HANDOFF-001`](../../../docs/test-cases/workflow-governance.md#tc-workflow-manual-handoff-001-surface-actionable-manual-testing-and-resume-automatically).

## Issue and pull-request boundaries

Every independent issue must have one dedicated pull request. A pull request
must not implement or close several independent issues, even when they share
files, a discussion, a milestone, or a planned release. Track a multi-issue
initiative in an umbrella issue or discussion with a current checklist and
dependency links; do not use an umbrella implementation PR for its independent
issues.

Keep one issue's implementation, tests, documentation, generated outputs,
migrations, and provenance updates together. Do not split an issue
mechanically by file type or line count when that would leave an incomplete or
unbuildable layer. If one request contains separately deliverable contracts,
create explicit dependent sub-issues before implementation, freeze each
sub-issue's contract, and give each sub-issue its own PR.

### Independent roots and genuine stacks

Base every independent issue branch directly on `master`. Use a stack only
when one issue genuinely depends on another issue's unmerged code or contract,
not merely because the issues touch shared files or belong to the same
initiative.

Validate and merge independent PRs in parallel. Do not serialize them by age,
issue number, shared initiative, or an unrelated PR's post-merge CI. Another
independent merge advancing `master` does not by itself invalidate a candidate
head's evidence. Refresh and rerun gates only when the candidate tree changes,
GitHub reports a merge conflict, a declared shared contract changes, or the
new base exposes a concrete interaction. Post-merge Build CI then verifies the
combined default branch and triggers immediate fix-forward or revert on
failure.

Every non-root PR must record:

- `Depends on #...` links for its immediate issue and PR dependency;
- its immediate base branch and position in the stack;
- known dependent issues and PRs; and
- the umbrella issue or discussion, when one exists.

Keep those links and the umbrella checklist current as the stack changes.
Every issue-specific layer must remain buildable and testable against its
immediate base. Keep a child based on its immediate parent while that parent is
open, and run exact-head Build CI and Copilot review against that genuine base.
Whenever the parent head changes while both PRs remain open, merge the updated
parent branch into the child with a normal merge commit, verify the child-only
diff again, and rerun the child's exact-head gates. A parent-only push does not
emit a child `pull_request` event; the child merge changes its head and emits
the required `synchronize` event instead. Never accept the child's earlier
green run against an older parent head.
Never temporarily retarget a child to `master`, close and reopen it, or
otherwise misrepresent the stack solely to trigger CI.
Review and merge the stack bottom-up; never merge a child while its required
parent is open. After a parent merges, retarget the child once by running
`gh pr edit <child-pr> --base master`, confirm that
`git diff master...<child-branch>` contains only the child issue's scope, and
require the resulting `pull_request` `edited` event to start fresh exact-head
Build CI against `master`; rerun Copilot review because the candidate
base/tree evidence changed. The `edited` event alone is not delivery evidence:
the Build must still bind to `pull_request.head.sha`, the child-only diff must
be verified, and every fresh gate must succeed.

Apply candidate-commit Build CI plus Copilot review, then post-merge
consolidated Build verification, issue evidence and closure, and the remote
completion gate independently to every issue PR. Complete the umbrella
initiative only after every accepted issue has been independently merged,
verified on `master`, and closed.

### Review-size preflight

Before requesting review, record the immediate base, changed-file list, and
numeric additions, deletions, and total changed lines from:

```bash
git diff --name-only <base>...HEAD
git diff --numstat <base>...HEAD
git diff --shortstat <base>...HEAD
```

Treat GitHub Copilot review's 20,000-line limit as a hard ceiling, not a
target, and replan before an issue PR knowingly reaches it. Split independent
contracts into independent issues; when one issue has separately deliverable
contracts, create explicit dependent sub-issues. Never reduce the reported
size by separating required tests, generated evidence, migrations,
documentation, or provenance from their implementation.

The only exception is a genuinely indivisible single-issue change. The PR
must document why no complete smaller contract exists, the changed files and
diff size, and the alternative automated and per-area review evidence used
because hosted review cannot cover the complete diff. This exception must
never combine independent issues or add a human approval gate. The normal
workflow requires only `git` and `gh`; do not require Graphite, Git Town, or
another stacking service.

## Change gate policy

Do not add a compile-time flag automatically for every change.

Bug fixes do not need a feature gate by default. Fix the root cause directly
and pin it with a regression test. Add a temporary or permanent gate only when
a documented special concern makes both paths intentional, such as a risky
format migration, incomplete platform coverage, a staged compatibility
transition, or a project-selectable behavior discovered during triage. If the
behavior is a permanent project choice rather than restoration of an intended
contract, reclassify it as an optional feature.

Use a gate for optional gameplay or UI behavior, a supported project profile,
or a staged feature whose enabled and disabled forms are both intentional. Do
not add a gate for a routine bug fix, refactor, documentation change,
build-tool change, or foundational API when the disabled branch would only
create dead code. Never use a gate to hide a failing fix or avoid resolving the
root cause.

Every accepted gate must provide:

- a GNU Autoconf option, Make variable, and C macro with one documented
  default;
- strict `0` or `1` validation and fail-fast dependency/conflict checks;
- configuration-identity participation when behavior or ROM identity changes;
- an explicit save-compatibility and migration decision;
- an enabled positive test and a disabled negative control;
- supported named profiles and dependency-boundary tests rather than an
  unmaintainable power-set matrix;
- a lifecycle decision: permanent project choice, temporary incubation flag,
  or future graduation/removal candidate.

A compile-time flag is not an emergency runtime kill switch. Disabling it
requires rebuilding the ROM, and it reduces risk only while both paths remain
tested. Follow the established contracts in
[`docs/starter_features.md`](../../../docs/starter_features.md) and
[`docs/config_identity.md`](../../../docs/config_identity.md).

## Phase 4: implement

1. Treat the modern `arm-none-eabi` GCC/AAPCS framework as the supported
   default path.
2. Make complete, surgical changes through existing extension points.
3. Preserve the default behavior unless the accepted contract explicitly
   changes it.
4. Keep disabled optional code absent or semantically inert, and prove that
   property with a negative control.
5. For a bug fix, add a regression test that demonstrates the original failure
   and the corrected behavior.
6. Preserve every declared dependency and conflict in configuration validators,
   compile-time guards, runtime checks, or tests as appropriate.
7. Update user-facing, contributor, configuration, and public-API
   documentation in the same change. Documentation is part of implementation,
   not post-merge cleanup. Final docs must name all dependencies and conflicts
   with other features, or state explicitly that none exist.
8. Ship or update every required tester-facing case with the behavior and map
   its deterministic assertions to host or ROM automation.
9. Do not hand-edit build-local generated output.

Follow the repository conventions in
[`CONTRIBUTING.md`](../../../CONTRIBUTING.md) and
[`copilot-instructions.md`](../../copilot-instructions.md).

## Phase 5: validate

Local validation is change-focused by default. Run only the smallest tests
that directly cover the changed behavior and the one necessary compile or
runtime scenario. Do not run broad catalog validation, full repository test
suites, all-locale/all-feature profiles, broad archival builds, or every
supported profile locally unless the changed surface directly owns that gate
or focused evidence cannot answer the acceptance criterion. Combined Build CI
is the comprehensive final integration gate. Stop after focused checks pass,
commit the candidate, and hand it off.

For a bug fix, first reproduce the original symptom or preserve equivalent
structural failure evidence, then prove the symptom is gone and the new
regression test fails if the fix is removed.

When debugging runtime code with GDB, do not stop at a host-side version or
architecture probe. Run `make expansion-modern-gdb-smoke` and require a real
remote connection, register read, symbolic `AgbMain` breakpoint, continue, and
backtrace through mGBA before claiming the debugging chain works.

For boot, save, UI, or gameplay behavior:

- prefer deterministic headless libmGBA scenarios;
- assert semantic state, counters, save bytes, or bounded transitions rather
  than timing or pointer accidents;
- include enabled behavior and a disabled/default negative control;
- preserve a failing fingerprint and root-cause it instead of refreshing an
  oracle merely to make the test green;
- use screenshots or recordings as supplementary visual evidence, never as
  the sole proof of correctness.

Automate every deterministic acceptance criterion. If a material result is
inherently subjective or cannot be asserted reliably, such as a visual, audio,
or UX judgment, leave the work open and state the exact unresolved criterion
and all evidence already collected. Do not convert that hold into a blanket
human-review requirement. Validation evidence must name the tester-facing case
IDs exercised, exact profile or artifact, environment, positive and negative
actual results, and the mapped automation result or precise manual-only reason.

### Meaningful test evidence

- **Evidence standard:** required
  - **behavior:** required
  - **parsed structural contract:** required
  - **generated output:** required
  - **compile/link properties:** required
  - **runtime state:** required
- **Prohibited evidence:** prohibited
  - **sole-evidence rule:** prohibited
  - **arbitrary strings:** prohibited
  - **comments:** prohibited
  - **helper names:** prohibited
  - **line numbers:** prohibited
  - **ordering:** prohibited
  - **implementation spelling:** prohibited
  - **Git-text rationale:** required. git-tracks=source,review,history;
    raw-tracked-text=not-behavior-evidence
- **Static-contract exception:** conditional
  - **source-text assertion:** permitted-only
  - **exact syntax/spelling/absence:** required
  - **documented public format:** one-of
  - **security boundary:** one-of
  - **generated-file contract:** one-of
  - **ABI/layout constraint:** one-of
  - **externally consumed protocol:** one-of
  - **named contract:** required
  - **irreplaceable evidence explanation:** required
- **Evidence preference:** ordered
  - **real function positive/adversarial inputs:** first
  - **parsed JSON/YAML/Make/AST/binary/schema:** second
  - **compile/link typed symbols/sections/resources/generated output:** third
  - **deterministic target-ROM/libmGBA behavior:** fourth
  - **narrowly justified source-text assertion:** last
- **Replacement and mutation controls:** required
  - **accepted requirement:** preserve
  - **stronger evidence:** required-or-duplicate
  - **duplicate gate:** no-independent-contract
  - **phrase-preserving behavior change:** fails
  - **semantics-preserving spelling/order refactor:** green

### Issue #29 identity boundary

Do not add or restore a whole-source/object/ROM SHA-256 identity gate, or
committed source/blob/object/commit snapshots that duplicate Git's immutable
candidate tree. Derive release paths, modes, and gitlinks from the exact
target tree at verification time.
`legacy-identity-check`, `scripts/archival_identity.py`, and its manifest were
removed intentionally. Git records source history, the modern expansion ROM is
not required to match the original binary, and `asmdiff.sh` remains available
for explicit archival investigations.

Build CI for the **exact candidate commit** means only that the successful run
must correspond to the commit being delivered, not an earlier revision. It is
not a ROM or source-tree SHA-256 equality requirement. Configuration
fingerprints, dependency integrity hashes, extracted-content integrity,
behavioral framebuffer/SRAM hashes, format CRCs/checksums, and release-time
manifest/archive hashes may remain when they serve their separate correctness
contracts. Human provenance metadata may identify exact paths and facts, but
must not carry content hashes or duplicated gitlink pins.

## Phase 6: PR, AI review, and merge

Use one dedicated pull request for exactly one independent issue by default.
Push directly to `master` only when the user explicitly requests it and
repository permissions allow it; direct delivery still requires the same local
validation, pushed-commit consolidated Build CI, and remote completion gate.

Keep the PR body stable. It records:

- frozen scope and non-goals;
- feature classification or bug-triage result and root cause;
- dependencies, dependents, conflicts, and supported combinations;
- immediate base, stack position, parent dependency, and known dependents;
- tester-facing case definitions and procedures;
- save, generated-data, debug, release, archival, baseline, and fingerprint
  compatibility decisions.

Put evolving evidence in exactly one canonical PR comment, update that comment
in place, and never copy its marker into the PR body or template. Follow
[`docs/workflow-pilot.md`](../../../docs/workflow-pilot.md#build-event-classification-and-candidate-evidence).
That comment records:

- review-size changed files and additions/deletions, or the narrow
  indivisible-single-issue exception and alternative evidence;
- tester-facing case IDs exercised, definition/catalog links, exact profile or
  artifact, environment, positive and negative actual results, interactions
  and save expectations, and automation mapping or precise manual-only reason;
- every command actually run and its result;
- runtime scenario, environment, command, and result when behavior changes;
- exact candidate SHA, workflow/review identities, and completion state;
- any actual baseline or fingerprint change and why the oracle itself changed.

Finalize the stable title/body before pushing the candidate, then freeze both
while the exact-head full Build is queued or in progress. Continue updating the
canonical evidence comment because comment edits do not emit
`pull_request: edited`. For a later title/body correction, use the repository's
isolated `pr-metadata edit` helper with exact current head/base identity. Its
default path defers an active-full-build race without metadata mutation or run
cancellation. An essential frozen-contract correction requires a nonempty
reason; record that reason in the canonical evidence comment, leave the
same-SHA full Build active, preserve the helper's returned intent and
confirmation comment IDs, and run confirmation-comment-bound
`pr-metadata reconcile` after that exact full Build succeeds. The two
append-only owner-authored comments and metadata-specific GitHub version are
authoritative rather than caller data or a mutable local ledger.
Reconciliation refetches the requested confirmation and its referenced intent,
validates that exact pair against current metadata/version and GitHub run/event
identity, and reruns only the failed lightweight metadata
continuity run. It never edits/deletes either transaction comment or
dispatches/cancels a full Build. See
[`docs/workflow-pilot.md`](../../../docs/workflow-pilot.md#safe-pull-request-metadata-ordering).

Triage every AI review finding. Fix valid findings, answer questions, and close
false positives with a reasoned explanation. Do not implement review comments
blindly.

No human code review or approval is required. A CODEOWNERS request is advisory
unless an external GitHub ruleset enforces it; this workflow does not add such
a gate.

### Sibling-family review convergence

Before the first remote review of a high-risk or large change, the coordinator
uses one fresh bounded read-only reviewer through the existing task/tool
interfaces. Keep implementer/reviewer/coordinator identities distinct and
reject overlapping reviewer ownership. Only reading the exact candidate,
reading supplied evidence and returning the report are permitted; deny edit,
push, comment, request-review, CI dispatch, merge and arbitrary commands at the
tool dispatch boundary.

Use the [review-family API](../../../docs/workflow-pilot.md#sibling-family-review-convergence)
to bind accepted findings to existing cases, actual production predicates and
reviewed finite source models. Complete all applicable action/item/target,
lifecycle, wire/replay/stale-binding, generated-owner/output/consumer/drift and
enabled/disabled resource obligations. Evidence comes from actual task,
Git/GitHub and test-tool observations. Do not accept candidate programs, pass
records or trusted-status flags, or relabel a whole-suite pass as member/ROM
evidence. Unknown coverage blocks honestly; a new binding can be reviewed and
selected at an exact tool revision in the same feature PR, without an
unrelated base-first installation.

First/second change requests produce bounded complete family handoffs. A
third creates a sticky architecture/decomposition hold bound to the held
round/head. New heads and later clean reviews do not release it. Require the
coordinator's bound redesign/decompose/retain-with-evidence disposition before
resuming narrow work or eligibility. Preserve the publication protocol below:
already-created commits are immediately owner-pushed on their assigned branch
as ineligible WIP, never hidden on an invented side branch or held locally.

GitHub review identities and content are observations, not natural-language
approval signals. The coordinator triages complete content, including
suppressed findings; COMMENTED and zero new inline comments do not mean clean.
Keep exact-head Copilot/security/Build and exact-master completion unchanged.
Read-only tool roles/minimal environments are operational controls, not
same-UID OS isolation. No broker, generic receipt/capability platform, new
agent backend or protected installation is a prerequisite.

The indexed case is
[`TC-WORKFLOW-REVIEW-FAMILY-001`](../../../docs/test-cases/workflow-governance.md#tc-workflow-review-family-001-expand-valid-findings-across-complete-sibling-families).

### Bounded exact-SHA implementation handoffs

Use the [version-3 contract](../../../docs/workflow-pilot.md#bounded-exact-sha-implementation-handoffs)
and retain `TC-WORKFLOW-AGENT-HANDOFF-001`. One coordinator owns the assignment
and a bounded, locked, atomically updated session-local state document. The
implementation-submitted result cannot replace Git, focused-check, process or
GitHub observations, or alter scope, budgets or permitted actions.

Observe dispatch, explicit receipt, progress, commit and result delivery
separately. Native CLI event IDs correlate the first three and delivery; Git
establishes the commit. A tool's transport success and displayed exit text are
not OS process results. Run focused checks through reviewed coordinator tools
or the existing approved test/CI route, never by importing a candidate-selected
module into a credential-bearing collector.

Record exact authorized upstream merge inputs before assignment; verify the
task's first-parent commits and apply their trailers without demanding task
trailers from imported upstream history. Keep incremental handoff budgets
separate from the full PR's review-size gate. Close the implementation owner
after its committed handoff; a review successor needs a fresh runtime/session.
Use existing runtime deadlines and process controls for bounded lifetime.
Opaque handles and unavailable RSS or OOM authority remain unknown, not proven.

One coordinator owns one direct watcher per repository/run/attempt. A watcher
failure triggers an exact GitHub run query; preserve real failure and incomplete
states and re-arm only after the previous watcher has terminated. No reasoning
agent waits for CI. Preserve/lock and reuse the original interrupted worktree
before one bounded replacement; never reset/delete it or build a second recovery
copy engine. Check unattended availability or document a covering availability
plan, without treating that decision as a permanent uptime guarantee.

These are operational observations, not authenticated records. Existing
platform role/tool permissions enforce the implementation owner's remote-action
prohibition; do not claim protection against arbitrary hostile same-UID
processes. There is no handoff broker, signer, privileged publication API or
new delivery graph. The coordinator still performs normal owner-context pushes,
and immediate publication plus every final security/review/Build/merge/closure
gate below remain mandatory.

### Immediate publication and visible work

Every new task commit, including a WIP or checkpoint commit, is a durability
boundary: the coordinator immediately pushes that exact commit under the
repository owner's context. Do not retain commits locally for independent or
hosted review, additional validation, CI results, or a later batch.
Implementation owners return a new commit immediately; they still do not push.
Prefer focused validation before committing. If work or validation remains
after a commit exists, publish first and describe the pending or failed state.

Open or update the dedicated PR promptly, using a draft for incomplete work.
Keep its issue, branch, exact head, assigned owner, active scope, remaining work,
and precise blockers visible in the single canonical evidence comment so other
contributors and agents do not duplicate the work. Keep the PR body as the
stable contract. Required independent review runs before commit when possible
or concurrently after immediate publication; it cannot become a post-commit
persistence hold. Publishing WIP is not semantic handoff acceptance, terminal
authority publication, merge eligibility, or task completion.

The labeled fields below are the canonical CLI commit-publication and
WIP-visibility protocol. Field and list order are immaterial; the symbolic
values are fixed. Surrounding prose explains this protocol, not an override.

- **Trigger:** new-task-commit
- **Publisher:** repository-owner-coordinator
- **Implementation owner:** immediate-handoff-no-push
- **Pre-push waits:** none
- **WIP visibility:** issue, pull-request, branch, commit, owner, scope, state, remaining-work, blockers
- **Authority:** persistence-not-acceptance
- **Final gates:** unchanged
- **Push failure:** explicit-blocker-no-success-claim

### Trusted push ownership

Implementation subagents validate and commit locally but do not push. The
orchestrator pushes the exact commit under repository-owner context so Build
does not become `action_required`. If an already-pushed run for that same SHA
is `action_required`, the orchestrator reruns it with `gh run rerun <run-id>`
under owner context. Never create empty commits, weaken Actions approvals, or
use privileged `pull_request_target` just to bypass approval.

### CI waiting and agent lifetime

For a fleet with multiple active pull requests, designate one delivery
coordinator. It owns the run/PR ledger, starts or records exactly one direct
shell watcher per active run, receives terminal watcher notifications, triages
CI and review failures, routes local-only fixes to one owner, performs each
final merge gate and autonomous merge, and initiates the post-merge conflict
sweep. The coordinator must not poll, sleep, or keep a reasoning turn alive
solely to wait. Other agents must not duplicate watchers, fix ownership, or
merge decisions; they return validated local commits to the coordinator for
the trusted owner-context push.

Every delegated reasoning agent must be launched in background mode. Never use
a synchronous subagent invocation that blocks the main orchestrator. After
launching background work, continue every independent dependency-ready task
immediately. If the result is a true dependency and no independent work
remains, end the turn and rely on the automatic completion notification
instead of waiting synchronously or polling. Keep simple work that needs only
two to five direct tool calls in the main orchestrator rather than delegating
it.

Reasoning subagents must not remain alive merely to wait for a remote workflow.
The orchestrator that pushes or dispatches a workflow records the exact candidate
SHA and run ID, updates the orchestration state, and returns immediately. It
must not poll until completion, sleep through rate-limit backoff, or repeatedly
report that the workflow is still pending.

The orchestrator owns exactly one direct shell watcher for each active run,
rather than assigning that wait to a reasoning subagent:

```bash
timeout 90m gh run watch <run-id> --interval 30 --exit-status
```

Run the watcher through the shell runtime so its process-completion
notification resumes orchestration without model polling. If the bounded
watcher times out while the workflow is legitimately still running, query its
status once, then re-arm one direct watcher; never create duplicate watchers.
Only after the workflow reaches a terminal state may a short-lived reasoning
agent inspect logs, triage findings, or update evidence.

If a candidate SHA changes, cancel superseded candidate runs with
`gh run cancel <run-id>` when safe, discard their evidence, and dispatch new
checks for the replacement SHA. A title/body edit does not supersede the
candidate and must never cancel a same-SHA full Build. Never repeatedly wake
the same subagent merely to poll CI, and never accept a stale run because its
watcher completed.

After each merge, immediately inspect every open PR. Merge current `master`
only into PRs with real conflicts or shared-contract changes; refresh
independent conflicts concurrently and rerun only conflict-affected checks
plus replacement Build/review. Never pause or cancel unaffected PR CI because
of priority or unrelated `master` movement; cancel superseded CI only when its
candidate actually changes.

After each PR opens or updates, concurrently monitor exact-head Build CI,
Copilot comments/threads, and mergeability; triage review findings
immediately. Refresh real conflicts with a normal `master` merge. Monitor master-branch CI after every merge.
That means the exact-master combined Build CI and an open-PR conflict rescan.
Fix forward or revert a broken `master`;
unrelated PRs do not wait on healthy master runs.

All exact-head Build CI, Copilot-review, and post-merge master-branch CI
monitoring uses attached asynchronous shell watchers and is nonblocking.
Continue unrelated dependency-ready work while those watchers run; never
occupy a reasoning agent or stop with a waiting-only response. Cancel only a
superseded candidate run after that candidate actually changes. A broken
master Build requires an immediate fix-forward or revert and blocks that
issue's closure and remote completion, but not unrelated independent PRs.

The indexed source-only regression for trusted-push ownership and centralized
CI waiting is
[`TC-WORKFLOW-CI-WAIT-001`](../../../docs/test-cases/workflow-governance.md#tc-workflow-ci-wait-001-keep-ci-waiting-centralized-and-trusted-pushes-owner-scoped).

Before merge:

1. Confirm required Build CI succeeds for the exact candidate commit.
2. Request Copilot review concurrently with Build CI, and resolve each finding
   with code or an explanation. Candidate Build CI includes the complete
   combined host, modern, extended-host, archival, and summary gate.
3. Confirm every objective acceptance criterion, positive scenario, negative
   control, compatibility check, tester-facing case, and documentation
   requirement is complete.
4. Confirm no material risk or validation gap remains unresolved.

Merge the PR autonomously when all four conditions hold. Do not wait for human
review or approval. Respect branch protection and never bypass a required
GitHub control.

For a stacked PR, satisfy those candidate Build/review conditions against its
immediate parent base without temporary base retargeting, synchronize every
parent-head update into the child, merge only after its parent, then retarget
once to `master`, verify the child-only diff, require the fresh `edited`-event
Build, and rerun review as described above.

Leave the PR open only when:

- a material acceptance criterion is subjective or otherwise cannot be
  validated reliably by the agent;
- a required external permission or repository control blocks merging; or
- an objective check is still failing or missing.

In every hold case, name the exact blocker or missing validation. Do not leave
a generically worded request for review.

## Phase 7: post-merge completion

For every issue-specific PR:

1. Verify the one combined Build CI run on the resulting `master` commit;
   only the technically used patch publisher is master-only.
2. If the consolidated post-merge Build fails, immediately fix forward or revert; do not
   report the feature as delivered. The failure blocks the affected issue's
   closure and remote completion, but not unrelated independent PRs.
3. Add the final evidence and commit/PR/CI links to the originating issue.
   Include installed investigation tools, versions, purpose, and any
   pre-existing IDA/Ghidra/GDB resources used.
4. Close the feature or bug issue only when every required tester-facing case
   is present and every material manual criterion is verified. This
   development workflow overrides conflicting generic language that reserves
   closure for human review.
5. For tracked changes, run `make remote-completion-check` only after the
   intended `master` commit has one successful consolidated Build CI result.
6. After the PR is merged and all relevant exact-master CI is green, the
   delivery coordinator performs the completed-worktree cleanup below. Do not
   discard a workspace still needed to fix failed or incomplete master CI.

The task is complete only when the implementation and documentation are
persistent upstream, the combined Build check for the exact pushed `master`
commit is green, the remote completion gate passes, and no current-request
work item remains open.

### Completed-worktree cleanup

Use the existing workflow tooling's
[read-only planner and explicit apply command](../../../docs/workflow-pilot.md#completed-worktree-cleanup).
The coordinator supplies every assigned/active workspace through `--preserve`,
including an agent between commands, and keeps assignments unchanged until
apply returns. Run from a retained source/coordinator workspace, never from a
target. Implementation agents do not remove real historical worktrees.

Run the planner after completion rather than losing track of historical
leftovers. Apply only to explicit registered targets after fresh Git ownership,
clean tracked/untracked state, pushed/merged PR head, and post-merge automatic
master Build plus relevant exact-proof-commit CI checks. The existing Make
remote-completion definition remains authoritative; candidate CI or merge
alone never qualifies. A historical successful master proof containing the
merge remains useful even if unrelated newer work has failing CI.

Retain active, current/master, locked, dirty/untracked, unpushed/unique, foreign,
ambiguous, and missing/failed/pending-evidence paths. Retain nested/bare Git
repositories, private reflog/pseudoref/index resolve-undo objects not durably
reachable from shared refs, private configuration and unclassified
recovery/index metadata, and partial/promisor repositories whose object
lookups could fetch during a dry-run. Preserve filesystem byte paths in
mount/backlink checks and reports. Do not erase configuration/recovery records
or rewrite indexes to bypass these holds.
Never force removal,
unlock user worktrees, delete branches, globally prune registrations, or
recursively remove repository/home/session roots. Normal `git worktree remove`
is the only removal operation. Publish removed paths, their pre-removal
allocated sizes (not purported exact physical bytes freed), and precise
retained reasons in the existing completion evidence. Do not create a committed
mutable worktree ledger.

Cleanup is separate from immediate publication: return each newly created
commit immediately for the coordinator's owner-context push, including WIP.
Never retain a commit locally pending review, CI, batching, or cleanup.
The indexed behavioral regression is
[`TC-WORKFLOW-WORKTREE-CLEANUP-001`](../../../docs/test-cases/workflow-governance.md#tc-workflow-worktree-cleanup-001-remove-only-proven-completed-worktrees).

## Required final report

Report:

- the feature classification or bug-triage outcome and short rationale;
- dependencies, conflicts, and their enforced validation;
- the implemented behavior and configuration surface;
- focused and runtime evidence;
- installed or used investigation tools and versions;
- commit, PR or direct-push, and CI links;
- immediate base, stack position, and review-size preflight result;
- tester-facing case IDs, exact profile/artifact and environment, actual
  positive/negative results, and automation or manual evidence;
- whether the PR was merged autonomously or left open for one precisely named
  non-agent-verifiable criterion;
- the remote completion result.

Do not claim delivery from screenshots, AI review, or CI alone. Conversely, do
not stop for review or approval after all objective evidence is complete.
