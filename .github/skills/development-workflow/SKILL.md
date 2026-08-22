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

Every non-root PR must record:

- `Depends on #...` links for its immediate issue and PR dependency;
- its immediate base branch and position in the stack;
- known dependent issues and PRs; and
- the umbrella issue or discussion, when one exists.

Keep those links and the umbrella checklist current as the stack changes.
Every issue-specific layer must remain buildable and testable against its
immediate base. Review and merge the stack bottom-up; never merge a child while
its required parent is open. After a parent merges, run
`gh pr edit <child-pr> --base master`, confirm that
`git diff master...<child-branch>` contains only the child issue's scope, and
rerun exact-candidate remote checks whenever the candidate commit or tree
changes.

Apply candidate-commit Build CI, Full Matrix, post-merge Build verification,
issue evidence and closure, and the remote completion gate independently to
every issue PR. Complete the umbrella initiative only after every accepted
issue has been independently merged, verified on `master`, and closed.

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

During iteration, run the smallest focused host checks and the one relevant
modern ROM profile. Before delivery, expand validation to the exact acceptance
surface described in [`CONTRIBUTING.md`](../../../CONTRIBUTING.md).

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

Tests must prove behavior, a parsed structural contract, generated output,
compile/link properties, or runtime state. Do not add a test whose only
evidence is that arbitrary strings, comments, helper names, line numbers,
ordering, or implementation spelling exist or do not exist in a Git-tracked
text file. Git already preserves that text, and retaining the expected phrase
does not prove the implementation works.

A source-text assertion is permitted only when the exact syntax, spelling, or
absence is itself a documented public format, security boundary, generated
file contract, ABI/layout constraint, or externally consumed protocol. The
test must name that contract and explain why function-level, parsed, compiled,
or runtime evidence cannot replace it.

Prefer evidence in this order:

1. call the real function with positive and adversarial inputs;
2. parse the real JSON, YAML, Make database, AST, binary, or schema rather
   than grepping its serialization;
3. compile/link and inspect typed symbols, sections, resources, or generated
   output;
4. run deterministic target-ROM/libmGBA behavior; and
5. use a narrowly justified source-text assertion only for the static
   contracts above.

When removing or rewriting a brittle test, preserve its accepted requirement
with stronger evidence or explicitly prove it duplicated another gate and had
no independent contract. Where feasible, demonstrate that a behavior change
which preserves the old phrase fails the replacement test, while a
semantics-preserving spelling or ordering refactor remains green.

### Issue #29 identity boundary

Do not add or restore a whole-source/object/ROM SHA-256 identity gate.
`legacy-identity-check`, `scripts/archival_identity.py`, and its manifest were
removed intentionally. Git records source history, the modern expansion ROM is
not required to match the original binary, and `asmdiff.sh` remains available
for explicit archival investigations.

Build CI for the **exact candidate commit** means only that the successful run
must correspond to the commit being delivered, not an earlier revision. It is
not a ROM or source-tree SHA-256 equality requirement. Configuration
fingerprints, dependency integrity hashes, and release-archive determinism may
remain when they serve their separate correctness contracts.

## Phase 6: PR, AI review, and merge

Use one dedicated pull request for exactly one independent issue by default.
Push directly to `master` only when the user explicitly requests it and
repository permissions allow it; direct delivery still requires the same local
validation, Full Matrix run for the pushed commit, pushed-commit Build CI, and
remote completion gate.

The PR must record:

- frozen scope and non-goals;
- feature classification or bug-triage result and root cause;
- dependencies, dependents, conflicts, and supported combinations;
- immediate base, stack position, parent dependency, and known dependents;
- review-size changed files and additions/deletions, or the narrow
  indivisible-single-issue exception and alternative evidence;
- tester-facing case IDs exercised, definition/catalog links, exact profile or
  artifact, environment, positive and negative actual results, interactions
  and save expectations, and automation mapping or precise manual-only reason;
- every command actually run and its result;
- runtime scenario, environment, command, and result when behavior changes;
- save, generated-data, debug, release, and archival compatibility impact;
- any baseline or fingerprint change and why the oracle itself changed.

Triage every AI review finding. Fix valid findings, answer questions, and close
false positives with a reasoned explanation. Do not implement review comments
blindly.

No human code review or approval is required. A CODEOWNERS request is advisory
unless an external GitHub ruleset enforces it; this workflow does not add such
a gate.

### CI waiting and agent lifetime

Reasoning subagents must not remain alive merely to wait for a remote workflow.
The subagent that pushes or dispatches a workflow records the exact candidate
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
checks for the replacement SHA. Never repeatedly wake the same subagent merely
to poll CI, and never accept a stale run because its watcher completed.

Post-merge `master` Build CI monitoring is always nonblocking. Start its
bounded direct shell watcher in attached asynchronous mode so process
completion produces a notification, leave the post-merge verification work
item in progress, and immediately continue scheduling every dependency-ready
task that does not depend on that Build result. Do not stop orchestration or
send a waiting-only response merely because the master watcher is active.
Only issue closure, remote completion, and other true dependents wait for the
post-merge result.

When the asynchronous watcher completes, verify that the run belongs to the
exact merged `master` SHA and inspect every required job. Resume the dependent
completion chain on success. On failure, interrupt ordinary delivery work as
needed to fix forward or revert immediately; never let background monitoring
hide a broken default branch.

Before merge:

1. Confirm required Build CI succeeds for the exact candidate commit.
2. Dispatch and pass `full-matrix.yml` for that same candidate branch and
   commit.
3. Resolve every review thread with code or an explanation.
4. Confirm every objective acceptance criterion, positive scenario, negative
   control, compatibility check, tester-facing case, and documentation
   requirement is complete.
5. Confirm no material risk or validation gap remains unresolved.

Merge the PR autonomously when all five conditions hold. Do not wait for human
review or approval. Respect branch protection and never bypass a required
GitHub control.

For a stacked PR, satisfy those conditions against its immediate base, merge
only after its parent, then retarget and revalidate it as described above.

Leave the PR open only when:

- a material acceptance criterion is subjective or otherwise cannot be
  validated reliably by the agent;
- a required external permission or repository control blocks merging; or
- an objective check is still failing or missing.

In every hold case, name the exact blocker or missing validation. Do not leave
a generically worded request for review.

## Phase 7: post-merge completion

For every issue-specific PR:

1. Verify Build CI on the resulting `master` commit.
2. If `master` fails, immediately fix forward or revert; do not report the
   feature as delivered.
3. Add the final evidence and commit/PR/CI links to the originating issue.
   Include installed investigation tools, versions, purpose, and any
   pre-existing IDA/Ghidra/GDB resources used.
4. Close the feature or bug issue only when every required tester-facing case
   is present and every material manual criterion is verified. This
   development workflow overrides conflicting generic language that reserves
   closure for human review.
5. For tracked changes, run `make remote-completion-check` only after the
   intended commit is pushed and its Build CI succeeds.

The task is complete only when the implementation and documentation are
persistent upstream, required CI is green, the remote completion gate passes,
and no current-request work item remains open.

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
