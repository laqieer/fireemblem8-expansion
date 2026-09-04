# Contributing to Fire Emblem 8 Expansion

This project's default, supported contribution path is the **modern
`arm-none-eabi` GCC/AAPCS framework**. The original agbcc-based
decompilation workflow is preserved as an explicit **archival** lane — see
the "Archival/decomp contributions" section at the end of this document
and [`docs/archival-decomp.md`](docs/archival-decomp.md) for its full
guide.

For architecture context before you dive in, see
[`docs/architecture.md`](docs/architecture.md) and the
[full documentation index](docs/README.md).

For an incoming feature request or bug fix, Copilot CLI contributors should
invoke the project-scoped `/development-workflow` skill in
[`SKILL.md`](.github/skills/development-workflow/SKILL.md). It owns feature
classification, bug reproduction/root-cause triage, selective change gates,
dependency/conflict tracking, local IDA/Ghidra/GDB and reference-resource
selection, automated runtime evidence, autonomous merge when objective
evidence is complete, and the precise hold boundary for criteria the agent
cannot validate. For features and bug fixes, that skill is authoritative over
conflicting generic review or closure guidance; it requires no human review
or approval.

## 1. Preparation

1. Register an account on [GitHub](https://github.com/) if you don't have one.
2. Fork and clone the repository, then fetch submodules:
   ```bash
   git submodule update --init --recursive
   ```
3. Run the quickstart to get a working modern build:
   ```bash
   ./scripts/quickstart.sh
   ```
   See [`docs/quickstart.md`](docs/quickstart.md) for flags and
   troubleshooting, and [`docs/framework-support.md`](docs/framework-support.md)
   for supported hosts/toolchains.
4. Review `config.mk` and [`docs/config_identity.md`](docs/config_identity.md).
   Defaults are English-only and all issue #6 starter flags are off. Use
   `./configure --help` for persistent, validated feature/profile choices;
   direct `make VAR=value` overrides remain available for one-off builds.

## 2. Choose your change type

| Change type | Where | Primary commands |
| --- | --- | --- |
| **Content authoring** (characters, classes, items, supports, Chapter 2 slice) | `src/data/*.json` | `make generated-data-validate`, `make generated-data-generate`, `make generated-data-test` — see [`docs/generated_data_tutorial.md`](docs/generated_data_tutorial.md) |
| **Starter content/mechanics/QoL** | `src/data/items_expansion.json`, typed callbacks under `src/`/`include/` | See the dependency-safe profiles and matrices in [`docs/starter_features.md`](docs/starter_features.md) |
| **Localization** | `texts/expansion/registry.json`, `texts/expansion/catalog.<locale>.json` | `make localization-validate`, `make localization-generate`, `make localization-test` — see [`docs/localization.md`](docs/localization.md) |
| **C/runtime code** (modern framework) | `src/`, `include/` | `make expansion-modern-toolchain-check`, `make expansion-modern-cohort` (or `-all`), `make expansion-modern-elf`, `make expansion-modern-rom`, `make expansion-modern-boot-check` — see [`docs/quickstart.md`](docs/quickstart.md) |
| **Docs** | `README.md`, `CONTRIBUTING.md`, `docs/*.md` | Verify every relative link resolves and every referenced command actually exists |
| **Upstream-port tracking** | `config/upstream-port-state.json` (via CLI only) | `python3 -m scripts.upstream_port scan/drift/report/update-state/verify` — see [`docs/upstream-porting.md`](docs/upstream-porting.md) |
| **Archival/decomp matching** | `asm/`, `src/` (agbcc-matched) | `make legacy` — see [`docs/archival-decomp.md`](docs/archival-decomp.md) |

## 3. Fast checks (no ROM, run these first)

```bash
python3 scripts/artifact_guard.py --revision HEAD
make generated-data-validate
python3 -m unittest discover -s scripts/artifact_guard_tests -p 'test_*.py'
python3 -m unittest discover -s scripts/modernize/tests -v          # modern build/config/save-format host tests
GBA_PLAYTEST_HOST_ONLY=1 python3 -m unittest discover -s tools/gba-playtest/tests -v
python3 -m unittest discover -s scripts/localization/tests -p 'test_*.py' -v
python3 -m unittest discover -s scripts/docs_check_tests -v
python3 scripts/check_docs.py --check --check-examples
python3 -m scripts.upstream_port scan                               # only if your change touches upstream-port tracking
```

`scripts/modernize/tests` and `tools/gba-playtest/tests` assume
`./build_tools.sh` and `git submodule update --init --recursive` have
already been run (quickstart does both); a small number of their host
tests are environment-dependent (missing built tool binaries, or a
libmGBA backend without `pkg-config` metadata) and fail actionably rather
than silently in an incomplete environment — see each test's own
diagnostic.

## 4. Full validation policy

During iteration, run only the focused fast checks and the one relevant ROM
profile for the code you changed. A pull-request candidate is gated by the
required Build CI and Copilot review running concurrently. When those
candidate gates are clean, merge directly; the merge automatically starts the
expanded Build CI on `master`.

Both PR and master Build runs execute `host-tests`, `build`,
`extended-host-tests`, `legacy`, and the seconds-only fail-closed `summary` in
parallel. Master additionally runs the technically used `patch-release`
publisher. Artifact, documentation, generated-data, localization, crosswalk,
raw-closure, and modern debug/release runtime evidence run only in their
existing Build owners; no duplicate Matrix path or manual dispatch exists. The
expected combined-gate wall clock is approximately 35–40 minutes because the
jobs run in parallel; that operational range is not a duration assertion.
Repository branch protection or rulesets should require this workflow's
canonical `host-tests`, `build`, and `summary` contexts, while leaving
independent security/review contexts unchanged. Metadata-only PR edits keep
those existing required names green by running only the trusted no-checkout
continuity attestation in `host-tests`/`build`; `extended-host-tests` and
`legacy` stay platform-skipped, the required `summary` context advances to the
later metadata continuity run, and candidate eligibility still remains bound
to the newest prior complete full Build CI run.

If post-merge Build fails, fix forward or revert the affected `master` change.
That failure blocks the affected issue's closure and
`make remote-completion-check`, but does not block unrelated independent PRs.

The fixed upstream-port verifier still lists the current-master Build CI
commands with `python3 -m scripts.upstream_port verify --dry-run --jobs 2`.
If your change can affect boot, save, or gameplay behavior, also capture
`tools/gba-playtest` scenario evidence (scenario, environment, command,
result) — see [`docs/issue-resolution-policy.md`](docs/issue-resolution-policy.md#issue-closure-evidence).


## 5. Keep pull requests issue-sized

Every independent issue gets one dedicated branch and PR. Do not implement or
close several independent issues in one PR because they share files, a
discussion, a milestone, or a release. Use an umbrella issue or discussion to
track a multi-issue initiative, its dependency graph, and its current
checklist; the umbrella is not an implementation PR.

Base independent issue branches directly on `master`. Stack a child issue only
when it requires a parent's unmerged code or contract. A non-root PR must say
`Depends on #...`, identify its immediate base branch and stack position, and
list known dependents. Keep each layer buildable and testable against that
base. While the parent is open, keep the child based on that immediate parent
and run exact-head Build CI and Copilot review there. Whenever the parent head
changes, merge the updated parent into the child with a normal merge commit,
verify the child-only diff again, and rerun the child gates. A parent-only push
does not emit a child `pull_request` event; changing the child head emits the
required `synchronize` event. Never temporarily retarget a child to `master`,
close and reopen it, or otherwise misrepresent the stack solely to trigger CI.
Merge bottom-up and never merge a child before its required parent. After the
parent merges, retarget the child once to `master`, confirm its diff contains
only the child issue, and require the resulting `pull_request` `edited` event
to start fresh exact-head Build CI; rerun Copilot review because the candidate
base/tree evidence changed. An `edited` event by itself is not sufficient
evidence: Build must still bind to `pull_request.head.sha`, the child-only diff
must be verified, and all fresh gates must pass.

Keep an issue's implementation, tests, documentation, generated outputs,
migrations, and provenance evidence in the same PR. Do not split by file type
or line count if that leaves an incomplete layer. When one request contains
separately deliverable contracts, create explicit dependent sub-issues first;
then each sub-issue receives its own branch, frozen contract, PR, validation,
merge, post-merge Build verification, issue closure, and
`make remote-completion-check`.

### Worked umbrella and stack example

Suppose discussion `#100` tracks three accepted issues:

| Issue | Branch and PR base | Relationship | Merge order |
| --- | --- | --- | --- |
| `#101` documentation search | `feat/101-doc-search`, base `master` | Independent root | Any time after its own gates pass |
| `#102` typed registry | `feat/102-registry`, base `master` | Independent root | Before `#103` |
| `#103` registry-backed selector | `feat/103-selector`, base `feat/102-registry` | `Depends on #102` and its PR | After `#102` |

Discussion `#100` keeps links and completion state for all three PRs, but has
no umbrella implementation PR. PRs `#101` and `#102` are independent even if
they touch a shared guide. PR `#103` is a genuine child because it cannot
build against `master` until `#102` lands. Keep PR `#103` based on
`feat/102-registry` while its parent is open and run its exact-head Build CI
and Copilot review on that genuine base; do not flip it to `master` merely to
trigger CI. If `feat/102-registry` receives another commit first, merge that
updated parent into `feat/103-selector`; the resulting child `synchronize`
event reruns exact-head Build CI and review against the new parent tree. After
merging `#102` with the repository's merge-commit policy, retarget once by running
`gh pr edit <child-pr-number> --base master`, inspect
`git diff master...feat/103-selector`, require the resulting `pull_request`
`edited` event to start fresh exact-head Build CI, and rerun Copilot review
because the candidate base/tree evidence changed. The workflow run remains
bound to the unchanged child `pull_request.head.sha`; the edited event does not
replace diff verification or successful gates. After merge, let the automatic master Build rerun
the same consolidated evidence. Complete discussion `#100` only after all
three issues are independently merged, verified on `master`, and closed.

### Review-size preflight

Before requesting review, record the immediate base, changed files, additions,
deletions, and total changed lines:

```bash
base_ref=master # or the genuine parent branch for a stacked child
git diff --name-only "$base_ref"...HEAD
git diff --numstat "$base_ref"...HEAD
git diff --shortstat "$base_ref"...HEAD
```

GitHub Copilot review's 20,000-line limit is a hard ceiling, not a target.
Replan before knowingly reaching it. Independent contracts become independent
issues; separately deliverable parts of one issue require explicit dependent
sub-issues. Required tests, generated evidence, migrations, documentation, and
provenance stay with their implementation.

An exception is allowed only for one genuinely indivisible issue. Record why
no smaller complete contract exists, the full changed-file and diff-size
result, and the alternative automated and per-area review evidence. The
exception cannot combine independent issues or require human approval. The
normal lifecycle uses `git` and `gh`; Graphite, Git Town, and other stacking
services are optional and never required.

## 6. File a feature request or bug report

Use the structured forms in [`.github/ISSUE_TEMPLATE/`](.github/ISSUE_TEMPLATE/):

- **Feature request** requires a detailed capability description, request
  reason, reusable use cases, dependency/conflict review, configuration and
  compatibility impact, acceptance criteria, explicit non-goals, and at least
  one proposed tester-facing case.
- **Bug report** requires the exact commit, detailed build configuration and
  environment, deterministic reproduction steps, expected versus actual
  behavior, a proposed post-fix regression case, and game screenshots.
  Preserve failing logs/fingerprints and add GDB/IDA/Ghidra evidence when
  used. The regression case complements rather than replaces deterministic
  reproduction.

Blank issues are disabled for ordinary contributors; use the linked
Discussions page for questions or early design exploration.

## 7. Write tester-facing cases

Every accepted feature request and confirmed bug fix freezes at least one
stable tester-facing case before implementation. Case IDs are never silently
renumbered or reused; use a readable form such as `TC-SAVE-001`. The canonical
indexed catalog and coverage guard are tracked by
[issue #54](https://github.com/laqieer/fireemblem8-expansion/issues/54).
Until it lands, keep the proposed ID and complete case in the originating
issue and relevant feature or bug documentation rather than creating another
registry.

Each case states:

- linked issue and stable case ID;
- exact supported configuration/profile or optional downloadable artifact;
- prerequisites, clean starting state, and reset/cleanup requirements;
- numbered actions or inputs and the observable expected result;
- default/disabled or pre-fix negative control, or why it is not applicable;
- dependencies, conflicts, feature interactions, and save expectations;
- the deterministic host/ROM test, command, or scenario that covers each
  assertion, or a precise manual-only reason; and
- known limitations and unsupported configurations.

Tester instructions and automated evidence are complementary. Keep the human
procedure even when a unit test or `tools/gba-playtest` scenario covers every
assertion; testers should not need to reverse-engineer automation. Conversely,
automate every deterministic result instead of treating the written procedure
as proof. Manual-only judgment is legitimate only for a precisely named
visual, audio, or UX result that cannot be asserted reliably. If that
criterion is material and unverified, leave the PR and issue open on that
exact hold rather than requesting generic review.

Cases must remain runnable from documented source profiles. A downloadable or
combined artifact may be named when available, but cannot be the only setup.
For PR evidence, record the case IDs exercised, definition/catalog links,
exact profile or artifact, environment, positive and negative actual results,
interactions/save expectations, and the automation result or precise
manual-only reason.

### Actionable manual-testing handoff

The canonical machine-readable protocol is
[`.github/manual-testing-handoff.json`](.github/manual-testing-handoff.json).
Follow its eligibility, artifact-preview, activation, hold, completion, and
queue fields exactly. It is not a human review gate, and deterministic criteria
must remain automated. Both identified non-instrumented artifact roles require
deterministic emulator screenshots or synchronized emulator A/V evidence.
GitHub-linked open PR discovery determines activation targets; completion
cleans every labeled item, including closed or superseded PRs, while preserving
an assignee only when independent ownership remains. Every handoff comment
includes the exact `@laqieer` mention, a nonempty numbered step list, and true
merge/closure holds. Each actionable issue and linked open PR has its own
typed comment payload with stable case ID, full Git SHA, artifact paths and
SHA-256 values, and nonempty environment, state, expectation, and judgment.
Cleanup and automatic resumption require a per-item completion comment whose
actual result and GitHub evidence link match the original case ID and commit.
Only an accepted outcome permits cleanup; a rejected outcome retains both holds
and remains actionable.
Evidence must link to a same-repository issue/PR comment, review comment,
workflow run or artifact, commit-pinned blob, or GitHub attachment—not a bare
issue or PR page. Positive and control path-plus-SHA-256 identities must differ.
Accepted cleanup also requires every open PR's current GitHub head to match its
activation commit; a changed head needs fresh evidence. Retained assignment
requires a nonblank ownership reason. Rejected evidence retains the label,
assignee, holds, and actionable state.

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

The actionable queue is
  [`repo:laqieer/fireemblem8-expansion is:open assignee:laqieer label:"waiting-for-manual-testing"`](https://github.com/laqieer/fireemblem8-expansion/issues?q=repo%3Alaqieer%2Ffireemblem8-expansion+is%3Aopen+assignee%3Alaqieer+label%3A%22waiting-for-manual-testing%22).
An originating issue may appear alone only when independent GitHub relationship
discovery finds no open implementation PR; otherwise every discovered open PR
follows the same contract.
An empty queue produces no scheduled notification or comment.

The complete source-only lifecycle is
[`TC-WORKFLOW-MANUAL-HANDOFF-001`](docs/test-cases/workflow-governance.md#tc-workflow-manual-handoff-001-surface-actionable-manual-testing-and-resume-automatically).

## 8. Debug before filing a regression

Use [`docs/debugtools.md`](docs/debugtools.md) for the release-safe debug
surface and [`tools/gba-playtest/README.md`](tools/gba-playtest/README.md)
for deterministic scenario/fingerprint diagnosis. Do not refresh a reviewed
fingerprint merely to make a mismatch disappear; preserve the failing output,
root-cause it, and document any justified oracle change. For live source-level
debugging, run `make expansion-modern-gdb-smoke` to prove ARM GDB can control
the modern debug ROM through mGBA's remote server before relying on debugger
evidence.

## 9. PR provenance and delivery

This repository's general Wave 0 governance baseline describes what a PR/issue
must record before closure:
[`docs/issue-resolution-policy.md`](docs/issue-resolution-policy.md). For
feature requests and bug fixes, `/development-workflow` is authoritative
wherever generic review or closure guidance conflicts with it. In short:

- Issue closure is an evidence-based decision recorded in the PR/issue thread
  (frozen scope, every command run and its result, runtime/playtest evidence
  when relevant), not a machine-readable schema. For features and bug fixes,
  the project skill permits autonomous merge and closure when that evidence
  is complete and requires no human review or approval.
- Use [`.github/PULL_REQUEST_TEMPLATE.md`](.github/PULL_REQUEST_TEMPLATE.md)'s
  frozen contract shape. Keep evolving commands/results, tester observations,
  review-size measurements, candidate SHA, workflow/review identities, and
  completion state in exactly one canonical comment updated in place, as
  defined by
  [`docs/workflow-pilot.md`](docs/workflow-pilot.md#build-event-classification-and-candidate-evidence).
  Never copy that comment's marker into the PR body or template.
- Finalize the stable PR title/body before pushing a candidate. After the push,
  freeze title/body while the exact-head full Build is active and update only
  the canonical evidence comment. Use the repository's isolated
  `pr-metadata edit` helper for a later title/body correction; its default path
  defers rather than racing an active full Build. An essential correction
  requires a nonempty reason and a later `pr-metadata reconcile` invocation
  after that same-SHA full Build succeeds. The helper never cancels or
  dispatches a full Build; see
  [`docs/workflow-pilot.md`](docs/workflow-pilot.md#safe-pull-request-metadata-ordering).
- `reports/baseline/`, `tools/gba-playtest/fingerprints/`, and
  `scripts/shiftcheck/tas/fingerprint.lua` are reviewed oracles. Keep only the
  frozen baseline/fingerprint plan in the PR body. If the candidate actually
  changes an oracle, put the exact change, rationale, and independent
  verification in the one canonical marked comment above.
- `python3 scripts/artifact_guard.py --revision HEAD` rejects tracked
  ROM/ELF/save/savestate/patch/generated-compressed-asset files; it is a
  structural check, **not** a legal/copyright clearance — see
  [`docs/project-governance.md`](docs/project-governance.md#copyright-and-provenance).

**Working on your first Pull Request?** Learn how from this *free* series:
[How to Contribute to an Open Source Project on GitHub](https://egghead.io/series/how-to-contribute-to-an-open-source-project-on-github).

## Archival/decomp contributions

If your change is byte-for-byte decomp-matching work against the original
ROM (not the supported modern framework), use the archival agbcc lane:

```bash
make legacy -j$(nproc)
```

The full decompiling tutorial, rules, setup steps, and related
asset-extraction references live in
[`docs/archival-decomp.md`](docs/archival-decomp.md) — that document is
explicitly marked unsupported for expansion releases; do not treat it as
guidance for the default framework path.
