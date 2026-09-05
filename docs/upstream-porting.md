# Canonical upstream porting (Issue #12)

This document describes the read-only-by-default tooling under
`scripts/upstream_port/` that helps a human maintainer track drift against
the canonical upstream decomp repository, classify unreviewed commits, and
explicitly select, review, and manually apply upstream patches.

**Nothing in this tool automatically cherry-picks, applies, merges, commits,
branches, or pushes anything. It never fetches unless you explicitly ask it
to. It never executes upstream code.** Every mutating action is a distinct,
explicit subcommand documented below.

## Canonical upstream

- Canonical URL (pinned, hardcoded): `https://github.com/laqieer/fireemblem8u.git`
- Reusable remote name (default, matches existing maintainer clones): `decomp`
- The tool refuses to fetch through any remote whose configured URL does not
  match the pinned canonical URL exactly.

## State/manifest

Persistent, committed state lives in `config/upstream-port-state.json`
(schema-versioned JSON, sorted keys, stable formatting so diffs are
reviewable). It records:

- `canonical_upstream_url`, `remote_name`
- `last_scanned` — `{ref, sha}` of the last human-reviewed scan boundary
- `last_ported` — `{ref, sha}` of the last fully-accounted-for integration
  boundary (every commit up to this SHA is `ported`/`skipped`/`superseded`)
- `commits` — map of full 40-hex SHA → `{status, author_name, author_email,
  subject, rationale, validation_evidence, updated_at}`

Status values: `pending`, `ported`, `skipped`, `superseded`, `conflict`.

**Strict commit-record schema (enforced at `load_state` time, before any
dependent command produces output, scans git, or writes a file):** every
`commits[sha]` record must have *exactly* the seven fields above — no
missing, no extra (a record must not, for instance, carry its own redundant
`sha` field; the map key already is the SHA) — all seven typed as strings,
`status` one of the five legal values, and `author_name`/`author_email`/
`subject`/`updated_at` non-empty (author_email must look like an email;
`updated_at` must match `YYYY-MM-DDTHH:MM:SSZ`). `ported`/`skipped`/
`superseded`/`conflict` additionally require non-empty `rationale` and
`validation_evidence`; `pending` still requires every field to be present
and correctly typed, but `rationale`/`validation_evidence` may be empty
strings. A malformed record anywhere in `commits` fails the whole
`load_state` call — see `state._validate_commit_record` — so a forged or
hand-edited "evidence" gap is caught before it can be mistaken for a real
review decision.

Only the explicit `update-state` subcommand ever writes this file. `scan`,
`drift`, and `report` are read-only and never touch it.

## Workflow

### 0. (Optional, explicit) Fetch the canonical remote

```sh
python3 -m scripts.upstream_port fetch --remote decomp
```

Refuses to run unless `git remote get-url decomp` equals the pinned
canonical URL. Only updates remote-tracking refs/objects — never touches
local branches, the working tree, or history. If you already have a fresh
local `decomp/*` ref (e.g. from a prior maintainer fetch), you can skip this
and go straight to `scan`.

### 1. Review: scan for unreviewed commits (read-only)

```sh
python3 -m scripts.upstream_port scan --ref decomp/master --format text
python3 -m scripts.upstream_port scan --ref decomp/master --format json \
  --output build/upstream-port/scan.json
```

Lists every commit strictly after `last_ported.sha` up to the caller-selected
local ref (`decomp/master`, `decomp/remove_tools`, a raw SHA, etc.), with:
original full SHA, author identity, subject, changed paths, a path
classification (`code`/`data`/`symbol`/`docs`/`tools`/`build`/`linker`/
`config`/`other`), and risk flags (`modern-build-divergence-risk`,
`linker-conflict-risk`, `symbol-table-conflict-risk`,
`known-fork-divergence-hotspot`) for commits touching known fork/build
hotspots (`Makefile`, `modern.mk`, `ldscript.txt`, `scripts/shiftcheck/*`,
etc.). Output is deterministically ordered (oldest-first, topological) —
never dependent on wall-clock time. A merge commit in range is classified
using the deterministic, sorted **union** of changed paths across all of
its parents (never a silently empty path list, which is what plain `git
diff-tree` would give you for a merge commit by default).

`scan`/`drift` print to **stdout by default**. `--output PATH` is only ever
honored if `PATH` passes the same write-safety contract described in
Step 3 below (repo-contained, no symlink anywhere on the path, confirmed
gitignored) — a tracked file (e.g. `README.md`), a path outside the repo
(e.g. `/tmp/scan.json`), or a symlinked path is rejected with a clear error
*before anything is opened*, and nothing is mutated.

If `last_ported` is not an ancestor of the selected ref (histories
diverged — e.g. you selected a side-topic branch that was never rebased
onto the tip you last ported from), `scan` refuses to guess and tells you to
run `drift` first.

### 2. Check for drift / stale state (read-only)

```sh
python3 -m scripts.upstream_port drift --ref decomp/master --format json
```

Reports whether the selected ref moved since the last recorded scan, whether
the state's recorded SHAs are still reachable/consistent in this clone, and
how many commits remain unreviewed. Exit codes: `0` clean, `2` drift found
(ref moved and/or unreviewed commits exist), `3` integrity problem (a
recorded SHA is unreachable, or histories have diverged) — always read-only,
suitable for CI (see the drift-scan workflow below). Same `--output`
write-safety contract as `scan` above.

### 3. Select commits and generate a review report + patches

```sh
python3 -m scripts.upstream_port report \
  --ref decomp/master \
  --sha <full-sha-1> --sha <full-sha-2> \
  --out-dir build/upstream-port/my-batch
```

- Only the **explicitly listed** SHAs get anything generated — nothing is
  auto-selected.
- Each SHA must be a full 40-hex commit SHA that already exists locally and
  is reachable from the selected ref or from any `refs/remotes/<remote>/*`
  ref; anything else is rejected with a clear error.
- **A merge commit SHA is always rejected outright** — before any output
  directory is created or any file is written. Plain `git format-patch -1
  --stdout <merge-sha>` does not honestly patch a merge commit at all: it
  silently walks past it and formats a *different*, non-merge ancestor
  commit instead (or produces nothing), which would be a dangerously
  misleading result to hand a reviewer. There is no single deterministic,
  safely hand-appliable patch this tool can produce for a merge that also
  preserves provenance, so it refuses rather than fabricate one. Select the
  merge's individual non-merge constituent commits instead, or review it
  manually (`git show <merge-sha>`, `git log --graph`) outside this tool.
  A mixed selection containing even one merge SHA is rejected wholesale —
  no partial output is ever written for the valid SHAs in the same batch.
- Output (`report.json`, `report.md`, and one `NNNN-<shortsha>.patch` per
  commit) is written only under the gitignored `build/upstream-port/` root
  (or another directory you point at), and only after that directory passes
  the full write-safety contract: it must resolve inside the repository
  root, contain no symlink anywhere on its path (an ignored directory that
  is itself a symlink — whether pointing outside the repo or to another
  location inside it — is rejected, not silently followed), and be
  confirmed ignored via `git check-ignore`. All checks run, and must pass,
  before anything is created or written.
- Patches are produced by `git format-patch --stdout` reading local objects
  only — never applied, cherry-picked, or merged — and preserve the original
  author name/email/date/subject and commit SHA in standard patch headers.

### 4. Manually review and apply

Read `report.md`, inspect each `.patch` file, and manually apply the ones you
accept (e.g. `git apply <patch>` or hand-editing) **outside this tool**. This
tool does not do this step for you.

### 5. Explicitly record your review decisions

```sh
python3 -m scripts.upstream_port update-state mark \
  --sha <full-sha> --status ported \
  --rationale "why this was ported" \
  --evidence "how you validated it (tests run, diff reviewed, etc.)"
```

Legal statuses: `pending`, `ported`, `skipped`, `superseded`, `conflict`.
`ported`/`skipped`/`superseded`/`conflict` all require non-empty `rationale`
and `evidence`. Illegal transitions (e.g. leaving a `superseded` commit
without `--force`) are rejected.

### 6. Verify the manually-applied batch

```sh
python3 -m scripts.upstream_port verify
python3 -m scripts.upstream_port verify --dry-run   # list the gate commands without running them
```

**⚠️ This builds and checks the CURRENT TRUSTED WORKTREE (your repo, after
you manually applied whatever you accepted) — it never builds, checks out,
or executes the upstream ref/tree.** It orchestrates all 30 current-master
mirrored verifier gates in fail-fast order. `.github/workflows/build.yml`
carries the same 30 commands with argv/order preserved across its combined
host, modern, extended-host, and archival jobs, plus the deliberately
standalone issues #7/#17 documentation-governance workflow gate described
below. A no-checkout event identity validator, event router, and mode-specific
classifier check precede the four combined workers in CI; the four combined
workers run in parallel after that decision, and `summary` is their
fail-closed join. Metadata-only PR edits do not invoke local `verify` or those
workers. Local `verify` runs the same 30 gates in its
documented order and therefore does not reproduce CI wall-clock parallelism.
Every mirrored command uses repository-relative argv, so all 30 subprocesses
run at one resolved target repository root. Launch the source-tree module from
this source repository root. Implicit selection targets that source checkout;
`--repo <target-root>` may select another checkout while the module still
launches from its source root. Nested/external module discovery is not an
installed or supported interface. Local verification ignores ambient
`GITHUB_WORKSPACE` and expands the mirrored workspace argument to the selected
target; the committed CI workflow passes its runner workspace directly. That
selected root is both the subprocess working directory and the pilot
baseline's `--repository-root`; there is no per-step working-directory
override. Before either a dry run or execution, the source tool parses the
target checkout's Build workflow as bounded UTF-8 data without importing or
executing target Python. The event identity validator, event router,
mode-specific classifier, and four reviewed worker jobs must have
the exact same complete ordered step sequences as the source: step count,
unique required names, setup-versus-gate role, action and immutable SHA, run
argv, `env`/`with` mappings, direct fields, and no working-directory override.
The complete job-name order must also match, so extra jobs fail. All three
setup jobs are parsed and never become a 29th local gate. The 28 gate
commands are then checked against source `gates()`. An unnamed non-checkout
step, duplicate setup/name, complex key form, or older, newer, missing, added,
removed, reordered, or changed target step fails closed instead of running
source-defined evidence against a different checkout contract.
The same structure closes execution context before step comparison:
workflow-level keys are exactly reviewed `name`, triggers, read-only
permissions, and jobs, with workflow `env`, `defaults`, and `concurrency`
absent. The identity validator, router, and mode-classifier contain only their
reviewed names, runner, timeout, outputs, environment,
dependencies/conditions, and steps. Each combined job contains only its
identity/classifier dependencies and fail-closed condition, `runs-on: ubuntu-latest`,
its exact allowlisted environment, and `steps`. The comprehensive `build` job
has `timeout-minutes: 90`; `host-tests`, `extended-host-tests`, and `legacy`
remain 60 minutes, while identity/router/classifier and summary remain 5.
Classifier authority uses direct PR-base or push identities, with a
trusted-default-branch failure bootstrap only when PR base identity is absent
or unusable;
neither classifier nor worker can substitute the pull-request merge
`github.sha`. Every successful PR classification requires the identity
validator's numeric event number, exact `refs/pull/<number>/merge` ref, and
lowercase 40-hex SHA to match both the classifier and direct PR head; successful
push classification requires the corresponding validated push kind/SHA.
Workers accept either a complete current classifier head/base pair or the
explicit fail-closed state for a valid exact PR head with an
incomplete/malformed/incoherent base, and check out only that exact validated
head during normal classification. The latter state audits all four workers
and then fails summary; a valid base SHA may remain diagnostic data but cannot
authorize a checkout.
Base refs are bounded to 1024 UTF-8 bytes. Python applies grammar equivalent to
full `git check-ref-format refs/heads/<base.ref>` without a subprocess; the
trusted bootstrap invokes `/usr/bin/git check-ref-format` with the quoted full
ref, no `--branch` shorthand, explicit lone-`@` rejection, and no ref checkout.
Invalid refs retain a validated head only for the incomplete-base worker path
whose summary fails.
After classifier failure only, trusted event setup permits fallback solely for
an exact lowercase 40-hex SHA. PR fallback additionally requires its numeric
event number and exact `refs/pull/<number>/merge` event ref; push fallback requires
`refs/heads/master` and equal event `after`/`github.sha`. Workers consume only
that validated output, and the publisher consumes only the validated push
output. Missing, uppercase, short, nonhex, ref-name, mismatched, or cross-event
identities run no fallback worker or publisher. Summary audits worker and
publisher conclusions and always fails the classifier-failure path even when
every validated fallback job succeeds.
Default-branch validation occurs only when PR classifier bootstrap actually
needs it. Missing/malformed default-branch data cannot discard a separately
validated PR-head or push fallback. With no classifier authority, router
checkout/classification are suppressed behind an explicit guard, the router
and classifier fail, exact fallback workers and any guarded push publisher run,
and summary stays fail-closed.
Containers, services, strategies/matrices, permissions, defaults, dependency
or condition substitutions, deployment environment, concurrency, reusable-job
`uses`/secrets, custom shell context, unknown fields, and complex, duplicate,
or reordered keys fail before dry-run.
The complete nine-job structure preserves the six closed issue #176 jobs and
adds only the closed, non-mirrored identity/router/mode-classifier setup jobs.
`patch-release` must retain its validated master-push-only condition,
Ubuntu/60-minute context, exact commit env, nine ordered fresh-job publisher
steps, pinned checkout/upload actions, exact-after producer without whole-file
source hash pins, dedicated-UID private-mount/PID/network isolation, recursively
read-only host paths, private runtime mounts, masked service sockets, offline
hash-locked dependencies, exact cgroup-v2/process teardown, and an exact
two-file regular/single-link handoff. Complete target ROM bytes never enter an
Actions artifact, cache, release, or log. All candidate work finishes and its
cgroup/user/tree/process state is removed before the unpredictable
mode-restricted private download; immediate absolute isolated patch creation,
guaranteed base cleanup, cleanup verification, post-cleanup BPS/manifest/README
revalidation, and the exact patch-only upload mapping remain mandatory.
Candidate stdin/stdout/stderr must permanently target private `/dev/null`, and
an isolated trusted child launcher must close inherited descriptors above 2
before executing `setpriv` with the candidate script held in Bash `-c` argv.
GitHub workflow command-file paths
stay absent, output is never replayed, arbitrary output volume cannot alter a
successful exit, no output sink exists, and only fixed trusted status text plus
a numeric exit classification reaches the workflow log. Other writable roots
and regular files retain tmpfs/ulimit bounds.
Before `/sys` masking, the exact owned cgroup must be bound read-only under
root-only mode-`0700` `/mnt/supervisor`. The candidate must not read, write,
execute, or traverse that parent or inherit an FD; the exact cgroup child must
remain read-only, and the wrapper must use that surviving view to require
itself as the sole post-build member while host kill/removal retains the actual
cgroup path.
The larger `build` ceiling covers observed shared-runner compile variance
without removing or weakening a gate. The delivery coordinator still uses
`timeout 90m gh run watch <run-id> --interval 30 --exit-status`; because that
watch may expire near the job ceiling, it queries the exact run status once and
re-arms one watcher once only when the run is still nonterminal.
`summary` must retain
`always()`, its reliably evaluated dynamic summary name, the classifier plus exact ordered
worker/publisher needs, Ubuntu/five-minute context, exact classifier/result
env, metadata-skip and full-run validation, and missing/stale identity
failure, plus its single fail-closed command. Runner, condition, needs,
permission, env, step, command, action, container/default, or unknown-field
drift in either job fails before local dry-run even though neither job becomes
one of the 30 locally executed gates.
Candidate-evidence normalization independently requires one canonical
successful `event-identity` job context in both full and metadata runs;
missing, failed, skipped, renamed, duplicate, or unknown setup contexts reject
the run before eligibility evaluation.

CI additionally hydrates commit authority before the workflow-pilot tests with
the strict fixture-derived helper. It derives the minimal maximal commit tips,
requires fixed `origin` to have the exact lightweight
`refs/tags/workflow-pilot-baseline/<full-sha>` mappings, and fetches missing
history only through those named refs in bounded no-tags/blob-filtered/no-local-
ref batches. It verifies raw-parent coverage and then derives the exact historical
`.github/workflow-pilot-decisions.json` blobs needed by override introduction
and first-review commits from the strict fixture and current decisions. Only
those blob object IDs are fetched without the commit-level blob filter; other
blobs remain omitted. The helper rechecks exact `EXPECTED_BUILD_SHA`, the
complete ref set, and FETCH_HEAD after both phases. This covers force-pushed
candidates that an all-head fetch cannot recover. It is CI setup, not one of
the 30 local gates; normal local clones are never hydrated by `verify`, which
remains deliberately network-independent and fails if authority is incomplete.
The read-only `isolated_launcher.py anchor-refs` command documented in
[`workflow-pilot.md`](workflow-pilot.md) prints the mappings for the owner
orchestrator; it never creates or pushes them.

Before a non-dry-run local `verify`, install both the supported modern
toolchain and the explicit archival `make legacy` prerequisites. The
archival setup is intentionally opt-in (`./scripts/quickstart.sh --legacy`
or the equivalent instructions in
[`docs/archival-decomp.md`](archival-decomp.md)); `verify` has no safe
subset switch and fails closed if the legacy toolchain is absent. Use
`verify --dry-run` to inspect the complete 30-gate sequence without those
local prerequisites.

1. `GBA_PLAYTEST_HOST_ONLY=1 python3 -m unittest discover -s tools/gba-playtest/tests -v`
   (issue #13 host-only suite; the inline environment assignment applies only
   to this child process, so later runtime gates retain live-ROM coverage)
2. `python3 -m unittest discover -s tests/upstream_port -v`
   (pure-stdlib upstream-port tests, including the workflow mirror contract;
   rerun it for the current test count rather than trusting a written count)
3. `python3 -m unittest discover -s tests/workflows -p "test_*.py" -v`
   (pure-stdlib consolidated Build CI topology and checkout contracts)
4. `/usr/bin/python3 -I scripts/workflow_pilot/isolated_launcher.py reporter-tests`
5. `/usr/bin/python3 -I scripts/workflow_pilot/isolated_launcher.py baseline --repository-root "$GITHUB_WORKSPACE" --fixture scripts/workflow_pilot/tests/fixtures/baseline.json --decisions .github/workflow-pilot-decisions.json --expected scripts/workflow_pilot/tests/fixtures/baseline_expected.json > /dev/null`
6. `/usr/bin/python3 -I scripts/validation_ownership/isolated_launcher.py tests`
   (issue #206 confined Make/generated-source security and aggregate-budget
   suite)
7. `MAKEFLAGS= MFLAGS= MAKEOVERRIDES= GNUMAKEFLAGS= make validation-ownership-check`
   (issue #206 public sole-goal probe foundation)
8. `python3 -m unittest discover -s scripts/localization/tests -p "test_*.py"`
   (issue #18 host-only localization schema/catalog/pseudo/generation/resolver
   coverage)
9. `make game-localization-test`
10. `python3 -m scripts.localization.game_locales check`
11. `python3 -m scripts.localization.game_locales check-crosswalk`
12. `python3 -m scripts.localization.game_locales check-raw-closure`
13. `python3 -m unittest discover -s scripts/artifact_guard_tests -p 'test_*.py' -v`
14. `python3 scripts/artifact_guard.py --revision HEAD`
15. `make codeql-alerts-test CODEQL_REQUIRE_FANALYZER=1`
16. `python3 -m unittest discover -s scripts/modernize/tests -p test_build_default_lane.py -v`
17. `python3 -m unittest discover -s scripts/modernize/tests -p test_quickstart.py -v`
18. `make generated-data-test`
19. `make generated-data-check`
20. `make expansion-modern-linker-check MODERN_CONFIG=debug MODERN_ABI=aapcs`
21. `make expansion-modern-linker-check MODERN_CONFIG=release MODERN_ABI=aapcs`
22. `FE8_ITEM_ID_CAP=0xCE FE8_EXPANSION_ITEMTEST=1 make expansion-modern-itemexpansion-check MODERN_CONFIG=debug MODERN_ABI=aapcs EXPANSION_STARTER_CONTENT=1 EXPANSION_MECHANICS_HOOKS=1 EXPANSION_MECHANICS_SAMPLE=1`
23. `FE8_ITEM_ID_CAP=0xCE FE8_EXPANSION_ITEMTEST=1 make expansion-modern-itemexpansion-check MODERN_CONFIG=release MODERN_ABI=aapcs EXPANSION_STARTER_CONTENT=1 EXPANSION_MECHANICS_HOOKS=1 EXPANSION_MECHANICS_SAMPLE=1`
24. `make expansion-modern-map-menu-presentation-check -j1`
    (builds the all-locales/all-features profile once, then verifies the
    localized Danger map-menu/help framebuffer and semantic overlay lifecycle)
25. `make -f cjk_fonts.mk cjk-fonts-check cjk-fonts-test`
26. `python3 -m unittest discover -s scripts/texttools/tests -p 'test_multilang_codec*.py' -v`
27. `python3 -m unittest discover -s scripts/modernize/tests -p 'test_expansion_config.py' -v`
28. `python3 -m unittest discover -s scripts/linker_report/tests -p 'test_*.py' -v`
29. `make legacy -j2`
30. `make -C mgfembp compare`

Gates 20-21 aggregate the complete modern debug/release ROM, linker, budget,
shift, save, starter-feature, and localization runtime matrices through
`expansion-modern-linker-check`. Gates 22-23 reuse the item-expansion runtime
probe at cap `0xCE`; the three issue #6 arguments make the same ROM also prove
the typed starter-content record and both registered mechanics. No additional
item-expansion ROM build or gate is added.

### Standalone workflow check

Immediately after the artifact guard, the `build` job runs the independent
issues #7/#17 documentation-governance gate:

```sh
python3 -m unittest discover -s scripts/docs_check_tests -v
python3 scripts/check_docs.py --check --check-examples
```

This gate is stdlib-only, zero-network, and zero-ROM, and runs before
dependency/tool installation. It is additional to all 30 mirrored verifier
gates and intentionally has no `verify.gates()` entry; it does not weaken,
reorder, or replace any mirrored command.

**There is no gate subset/selection flag, on the CLI or in the internal
`verify.run_gates` API.** `verify` (with or without `--dry-run`) always
runs/lists the *full*, fixed, ordered gate set above — never an
unknown-gate, partial, or zero-gate result. This is intentional: closure
evidence for a manually-applied port batch is only ever meaningful as a
full-gate outcome, so the ability to select a subset was removed rather
than merely restricted (an `--gate` flag would let a caller manufacture a
"verified" result that skipped some gates entirely). `verify --dry-run`
lists every gate command in the exact order above without running any of
them — never a filtered preview.

### 7. Advance the ported boundary

```sh
python3 -m scripts.upstream_port update-state advance-ported --ref decomp/master
```

Only succeeds if every commit between the current `last_ported.sha` and the
new one is already `ported`, `skipped`, or `superseded` — it refuses to
silently skip review of any commit in the batch. Also only moves forward
(new SHA must be a descendant of, or equal to, the current one).

**Ref-tip binding (both the explicit `--sha` and the implicit no-`--sha`
path are validated identically):**

- `record-scan --ref X [--sha Y]` — `Y` (if given) must be a full 40-hex
  SHA and must be **exactly equal** to `X`'s own resolved local tip, i.e.
  `resolve_commit_sha(X)` right now. An expansion-side commit, an
  unrelated/diverged commit, or a real-but-stale SHA that used to be `X`'s
  tip but no longer is are all rejected — `record-scan` only ever records a
  ref's *current* tip, never an arbitrary point on or off its history.
  Omitting `--sha` uses that same resolved tip directly (there is no
  looser implicit path).
- `advance-ported --ref X [--sha Y]` — the candidate (`Y`, or the resolved
  tip of `X` if omitted) must lie inside the ancestry corridor bounded by
  the **current** `last_ported.sha` on one end and `X`'s resolved tip on
  the other: a descendant-of-or-equal-to the current boundary, **and** an
  ancestor-of-or-equal-to the resolved ref tip. Both ends are checked —
  not just old-boundary ancestry, which is what let an expansion-side
  commit (itself a genuine descendant of the old boundary, since it shares
  that ancestor) slip through before this fix. A commit only reachable via
  a diverged/forked branch, or one that comes *after* the resolved ref tip,
  is rejected the same way. A legitimate **intermediate** upstream commit
  (not necessarily `X`'s exact tip) is still accepted as a valid partial
  batch boundary, provided every commit up to it is already accounted for.

All of the above validation happens — including missing/unreachable local
objects being reported as an actionable error — **before** `state.json` is
ever written, so a rejected call leaves the file byte-for-byte unchanged.

### Ref-binding evidence consolidation (Issue #107)

Issue #107 records the upstream-port portion of the #99 test-governance
audit. Its scope is limited to duplicate `record-scan` protocol evidence:
it does not change the upstream-port policy, ref semantics, workflow gates,
or the state-file schema.

All four audit candidates have the `merge` disposition. The
`RecordScanRefBindingTests` methods are the retained counterparts because
they sit with the direct explicit/implicit ref-binding matrix and now assert
both the resolved-tip value and rejected-call state preservation.

| Audit candidate | Merge result | Retained protocol evidence |
| --- | --- | --- |
| `test_ref_binding.py::RecordScanRefBindingTests.test_explicit_record_scan_matching_resolved_tip_ok` | Retained | Explicit current-tip scan resolves `decomp/master` to the supplied SHA and records that exact tip. |
| `test_state.py::BoundaryAdvanceTests.test_record_scan_forward_ok` | Removed as duplicate | `RecordScanRefBindingTests.test_explicit_record_scan_matching_resolved_tip_ok` |
| `test_ref_binding.py::RecordScanRefBindingTests.test_explicit_record_scan_backward_still_rejected` | Retained | A previously recorded boundary cannot move backward; the rejected call preserves the state snapshot. |
| `test_state.py::BoundaryAdvanceTests.test_record_scan_backward_rejected` | Removed as duplicate | `RecordScanRefBindingTests.test_explicit_record_scan_backward_still_rejected` |

The retained forward and backward controls call `state.record_scan` against
synthetic local Git repositories; they do not inspect tracked source text.
Consequently, a behavior-preserving refactor remains green, while a
directionality mutation that accepts the old boundary or a mutation that
records a non-resolved tip fails the retained semantic evidence. This
consolidation exercises the meaningful-test-evidence contract in
[`TC-TEST-QUALITY-001`](test-cases/foundation.md#tc-test-quality-001-meaningful-test-evidence-policy-rejects-semantic-mutations).

No raw tracked-source-text assertion was confirmed in this audit scope. The
parent #99 audit and issue #100 policy are dependencies; there are no feature,
profile, save, generated-data, localization, ROM/RAM, debug/release, or
archival-lane conflicts. Reverting this change restores only the duplicate
assertions and does not alter persisted upstream-port state.

Separately, `update-state record-scan --ref decomp/master` lets you
explicitly advance `last_scanned` once you've reviewed a scan's output (also
forward-only, and ref-tip-bound as described above).

## Path classification categories

`code`, `data`, `symbol`, `docs`, `tools`, `build`, `linker`, `config`,
`other` — see `scripts/upstream_port/classify.py` for the exact, ordered
pattern rules (first match wins, purely a function of the path string, no
git calls).

## Safety boundaries (what this tool will never do)

- Never fetches by default; `fetch` is the only network-touching subcommand
  and it validates the remote URL first.
- Never applies, cherry-picks, merges, commits, branches, or pushes.
- Never executes, imports, builds, or tests upstream source.
- Never writes `report`/`patch` output, or a `scan`/`drift --output` file,
  anywhere that fails the shared write-safety contract
  (`output_safety.validate_output_target`, used by every write path in this
  package): the resolved target must be contained inside the repository
  root, must not pass through a symlink anywhere on its path (regardless of
  whether that symlink points inside or outside the repo), and must be
  confirmed ignored via `git check-ignore`. A tracked file (e.g.
  `README.md`) is always rejected, unchanged, before anything is opened.
- Never generates a patch for a SHA that wasn't explicitly selected, and
  never generates one for a merge commit SHA even if explicitly selected —
  see Step 3 above for why.
- For a merge commit, `scan`/classification always report the
  deterministic, sorted **union** of changed paths across all of its
  parents — never a silently empty path list.
- Never mutates `config/upstream-port-state.json` except via `update-state`.
- `verify` never builds the upstream ref/tree — only the current worktree.

## Scheduled drift check (CI)

`.github/workflows/upstream-port-drift.yml` runs on a schedule and on
`workflow_dispatch`, with `permissions: contents: read` only, no secrets, and
`persist-credentials: false`. Each run:

1. Configures/verifies the `decomp` remote points at the pinned canonical URL
   (`https://github.com/laqieer/fireemblem8u.git`) — a local `.git/config`
   edit only, never a fetch/checkout by itself.
2. Explicitly, anonymously fetches that remote's objects/refs by calling the
   same `python3 -m scripts.upstream_port fetch --remote decomp` subcommand a
   maintainer runs locally (see Step 0 above), which re-verifies the pinned
   URL itself before running a plain `git fetch`. This **only** updates local
   remote-tracking refs/objects (e.g. `refs/remotes/decomp/master`) — it never
   checks out, builds, imports, or executes anything from the fetched tree.
3. Runs the read-only `drift` (and, best-effort, `scan`) subcommands against
   that freshly-fetched local ref — not against the recorded `last_ported`
   SHA itself — so it can genuinely detect new commits that have landed on
   the live canonical branch since the last recorded scan/port boundary.
4. Writes the textual drift/scan report to the job summary and uploads it as
   an artifact, whether or not drift was found, before deciding the job's
   pass/fail status.
5. Fails the job (as a visibility signal only, never a state change) when the
   `drift` subcommand's exit code is non-zero — real upstream drift found
   (`2`) or an integrity problem/tool error (`3`/other).

It never commits, branches, opens a PR, merges, cherry-picks, or pushes
anything, and it never calls `update-state` — detecting drift never
auto-updates `config/upstream-port-state.json`, the source tree, or `HEAD`.
`workflow_dispatch` takes no inputs, so there is no caller-controlled value
that could inject an alternate remote/ref/URL into any step.

## Tests

```sh
python3 -m unittest discover -s tests/upstream_port -p "test_*.py" -v
```

Uses deterministic, offline, synthetic Git repositories (fixed author
identities/dates via `GIT_AUTHOR_DATE`/`GIT_COMMITTER_DATE`, never
`datetime.now()`) built with plain local `git` subprocess calls — no
network access, and upstream "commits" in the fixtures are never executed,
only read.

## Tester-facing procedure

[`TC-CORE-008`](test-cases/core-framework.md#tc-core-008-upstream-scan-records-a-human-decision)
covers read-only scan/drift/report safety, the ignored-output boundary, and
the explicit human port-decision record that automation must never infer.
