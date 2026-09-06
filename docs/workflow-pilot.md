# Workflow-efficiency pilot baseline

Issue [#176](https://github.com/laqieer/fireemblem8-expansion/issues/176)
is an accepted **framework capability: workflow measurement and decision
contract**. It freezes the pre-pilot evidence and supplies one fail-closed
reporter before any dependent issue changes delivery behavior. It does not
select CI, order review, alter a merge gate, or create a mutable delivery
ledger.

## Frozen boundary and authoritative sources

The immutable baseline fixture is
[`scripts/workflow_pilot/tests/fixtures/baseline.json`](../scripts/workflow_pilot/tests/fixtures/baseline.json).
Its inclusive UTC window is:

```text
start = 2026-08-20T00:00:00Z
end   = 2026-08-30T11:17:08Z
```

The end is the timestamp of Discussion #174's original baseline measurement,
not the later issue creation or this implementation commit. A timestamp equal
to either boundary is inside the window. The fixture is expected input, not a
generated report or a substitute GitHub ledger.

Artifact lifecycle evidence has a separate explicit `lifecycle_as_of` of
`2026-08-30T12:23:00Z`. This later boundary covers the authoritative
checkpoint/proof events and current disposition records without moving or
reinterpreting the historical measurement window.

The frozen source semantics are:

- Pull requests are the 64 PR identities whose authoritative `merged_at` is
  inside the inclusive window.
- Issue identities are the closing-issue relationships returned by GitHub for
  those PRs. An authoritative empty relationship is reported as excluded from
  issue-to-merge; a referenced issue with no fixture record is an error.
- Review identities are submitted
  `copilot-pull-request-reviewer[bot]` reviews on those PRs. Inline Copilot
  comments are review findings. GitHub exposes each thread's current
  `isResolved`/`resolvedBy` state, but neither the review-thread GraphQL object
  nor the PR timeline supplies a historical resolution timestamp. The
  fixture therefore preserves finding identities and current resolution state
  but contains no manufactured `resolved_at` values.
- Historical resolution timing is accepted only from complete GitHub
  `pull_request_review_thread` webhook delivery history. Each normalized event
  retains the GitHub delivery ID/GUID, delivery timestamp, repository, PR,
  review, finding, thread, actor, and `resolved`/`unresolved` action. The
  baseline has no such historical capture, so its source is explicitly
  unavailable rather than interpreted as an empty event history.
- The workflow cohort is the latest 1,000 Actions runs created at or before
  the end timestamp, ordered by `(created_at, run_id)` descending. Workflow
  run `33307027945` was still active at the measurement instant; its later
  successful completion is deliberately not back-propagated into the
  fixture.
- Commit identities, timestamps, parent edges, messages, PR head SHAs, and
  merge SHAs come from Git and GitHub at exact base
  `b8e7f9125e11d322ca37b5288b141bbd52902b61`.
  Commit messages are decoded as UTF-8 directly from `git cat-file` commit
  object bytes. Canonicalization removes exactly one conventional terminal LF
  from the raw message and no other byte; authored additional blank lines,
  spaces, leading whitespace, and multiline bodies must match the fixture
  exactly.
- PR #150 is the declared spotlight for review, Build, base-change, and
  close/reopen measurements. Safety outcomes aggregate across every referenced
  PR instead of inheriting that spotlight filter. Its Build association derives
  from the authoritative PR head branch and Actions `head_branch`, not a
  manually copied run list.

The frozen GitHub response fields were normalized from the Pulls, closing
issues, review/review-comment, PR timeline, and Actions Runs APIs. The reporter
performs no network access. A future live capture is not equivalent to this
fixture because mutable GitHub state now includes the formerly active run's
completion. If an exact timestamp, identity, relationship, status, conclusion,
or page needed by a formula is absent, the reporter exits nonzero instead of
guessing.

## Public seam

Run the stdlib-only reporter from the repository root:

```bash
python3 -m scripts.workflow_pilot.reporter \
  --repository-root . \
  --fixture scripts/workflow_pilot/tests/fixtures/baseline.json \
  --decisions .github/workflow-pilot-decisions.json \
  --expected scripts/workflow_pilot/tests/fixtures/baseline_expected.json
```

Successful output is canonical ASCII JSON: recursively sorted keys, compact
separators, and one trailing newline. Running over identical immutable inputs
is byte-identical. `baseline_expected.json` contains only immutable expected
values, not another authored current-state report. Its `identities.seal` uses
the `workflow-pilot-cohort-relationships-v2` domain for a SHA-256 of the
normalized cohort: snapshot fields; PR, issue, review, workflow-run, finding,
event, delivery, artifact, and dependency records; commit identities; and
their metric-relevant relationships. Identity
sets and set-like relationship fields are sorted before hashing. It is an
input-format/cohort checksum: it detects identity, timestamp, PR/SHA
association, and relationship substitution that preserves aggregate metrics,
but does not hash source files, blobs, objects, ROMs, or the repository tree.

## Completed-worktree cleanup

Issue [#208](https://github.com/laqieer/fireemblem8-expansion/issues/208)
adds a conservative framework capability through the existing workflow-pilot
package, not a publication broker, background service, or persistent ledger.
It uses Python's standard library, Git, and authenticated `gh` read operations.
The **delivery coordinator** owns actual historical cleanup, only after the
task's PR is merged, all relevant exact-master CI is green, and
`make remote-completion-check` passes. Implementation agents validate locally
and immediately return each commit for owner-context push; neither cleanup
nor pending review/CI delays publication or WIP visibility (issue #207).

Run from a retained source/coordinator checkout. List **every** assigned or
active workspace, even if its agent is between shell commands or its PR has
just merged. Keep those assignments fixed throughout apply:

```bash
# No targets means inventory all registrations; this never removes anything.
python3 -m scripts.workflow_pilot.worktree_cleanup --repository-root . \
  --preserve /absolute/coordinator-worktree \
  --preserve /absolute/active-agent-worktree

# Repeat --target for individually selected paths from the plan.
python3 -m scripts.workflow_pilot.worktree_cleanup --repository-root . \
  --preserve /absolute/coordinator-worktree \
  --preserve /absolute/active-agent-worktree \
  --target /absolute/completed-worktree --apply
```

`--apply` requires both explicit targets and a preserved-workspace inventory.
There is no plan-import or caller-provided `eligible` authority. All paths are
exact Git-registered roots; a similarly named directory is not enough.
The helper keeps the invoking/source/main/master worktrees, broad
home/repository/session roots and ancestors, other registered worktree
ancestors, explicit preserved paths, and active process working directories.
It checks exact Git common-directory and metadata-backlink ownership, branch
and HEAD, ordinary and hidden-index changes, all untracked files, private
worktree refs, incomplete Git operations, and upstream divergence.
Detached, unassociated/reused branches, missing registrations, nested Git
repositories/submodules (including bare or separated Git metadata without a
`.git` child), mounts, special files, and unknown ignored local data remain
held, not guessed disposable.

Every private reflog's old **and** new object identities, every private
pseudoref, and **every index resolve-undo object** are inspected, including
all `FETCH_HEAD` entries, not only its first mergeable line. A clean index
can still contain `REUC` records for all three stages of an earlier conflict.
The helper compares those binary records with `git ls-files --resolve-undo`
and proves that every object is durably reachable from shared refs which
worktree removal leaves intact. Shared commit ancestry can preserve a blob
or tree, not just a commit; the current HEAD's ancestry and the main
checkout's reflog do not substitute for this evidence. Changes to resolve-undo
paths, modes, stages, or objects also invalidate an earlier plan.

Private metadata is classified as a family, not by a blacklist of a few
filenames. `config.worktree` is retained even when empty or the extension is
disabled. Index backups, split-index bases, lock files, private hooks/excludes,
rerere records, other edit buffers and unrecognized private files/directories
remain held. A `COMMIT_EDITMSG` is disposable only when its bytes exactly
match the committed HEAD message. The ordinary registration files, inspected
refs/reflogs, and a validated index are the only other accepted metadata.
Empty private `refs/` directory trees, including the `heads/` and `tags/`
containers initialized by newer Git versions, are reconstructible. Every
entry is inspected within a bound; any file, symlink, or special entry keeps
the worktree, even when a shared ref already retains its object.
The index audit supports DIRC versions 2–4, verifies the format checksum and
bounds, and accepts only resolve-undo plus reconstructible tree, untracked,
fsmonitor and entry-offset caches. Sparse/split indexes and unfamiliar
extensions are retained, including optional extensions that Git itself
silently ignores. Missing, malformed, symlinked, or over-bound metadata and
private-only recovery objects retain the workspace. Do not erase configuration
or recovery records, rewrite the index, or weaken a hold to qualify a tree.

Only known generated ignored output is disposable: `build/`, `.dep/`,
`.deps/`, bytecode caches, standard root build products, the eight existing
Make-built host tools, and source-backed compiler/graphics intermediates.
The graphics formats include `.4bpp.fk` and
`.feimg[1-4].bin`/`.fetsa[1-4].bin` (with `.fk`/`.lz` derivatives) only when
their corresponding tracked image source exists. Unknown stems, other numeric
variants, and arbitrary ignored binary/compression files are not allowlisted.
Bitmap `.4bpp`/`.8bpp` output requires a tracked PNG; `.gbapal` requires a
tracked PNG or JASC `.pal`. A `.4bpp.h` needs its tracked bitmap or PNG
producer. Committed raw `.agbpal` data does not establish those conversions,
and `.8bpp.h` has no supported producer. These source-format requirements also
apply to recognized `.fk`/`.lz` derivatives; direct LZ output from a tracked
input remains supported.
Do not keep original user work in generated
directories. Ignored saves, baseroms, local configuration, editor state, or
other unrecognized files require preservation. Symlinks are not followed by
the size scan; unknown ignored data is not silently treated as build output.

Remote proof uses `origin`'s exact GitHub repository identity and the newest
PR for that branch, with its exact local/head SHA, same-repository head,
merged state, and `master` base. The local HEAD must be an ancestor of the
merge, the merge an ancestor of the proof, and the proof an ancestor of
GitHub's live master ref. This deliberately retains squash/rebase histories
whose ancestry does not establish that local work was delivered. Missing
local objects require a coordinator-owned fetch, not implicit mutation by
the planner. Deleted remote branches are acceptable only when the exact
merged PR/head and master ancestry still prove the work was pushed.

All Git commands disable optional locks, replacement objects, hooks,
fsmonitor/untracked-cache updates, and lazy fetching where supported.
Git 2.43 does **not** honor `GIT_NO_LAZY_FETCH`, so the helper does not rely
on that variable alone: before every other Git invocation it reads effective
configuration, including includes and per-worktree settings, and rejects any
partial-clone/promisor setting. This conservative hold applies even to a
false promisor setting or newer Git. Use a fully materialized, non-promisor
repository for cleanup authority; the helper never fetches missing objects or
rewrites configuration to make a target eligible.

By default the proof SHA is the PR's merge commit. Use `--proof-sha FULL_SHA`
to select a known later master descendant, for example after a CI fix-forward.
CLI input accepts uppercase hex by normalizing that argument alone. Git/GitHub
identities and programmatic proof inputs must remain canonical lowercase full
SHAs. The proof must pass the same checks; the option is not a success override.
A valid historical proof is not invalidated by an unrelated newer failure.
The mandatory Build follows the existing Make completion gate: exact proof
commit, `master`, automatic `push`, `build.yml`, completed/success.
Candidate or manual Build alone never suffices. The latest observed master
workflow for each workflow identity, the latest automatic Build, all
associated exact-commit checks, and latest external check/status contexts must
also pass. Reruns use current attempts, including an older run rerun more
recently; a newer failed/pending attempt cannot hide behind an older success.
Skipped Actions jobs are nonexecuted, not substitutes for a successful
workflow; external checks require success.

GitHub pages have bounded cardinality, stable totals where supplied, unique
record IDs, and validated repository/commit identities. Missing, malformed,
over-bound, stale, or changing evidence retains the target. The API cache is
only an in-memory optimization for one pass. Apply clears it before each
target and compares fresh Git/PR/CI proof with the plan, then repeats local
identity/status/lock/process/private-recovery checks and the nested-Git/size
scan immediately before normal
`git worktree remove`. There is no force, unlock, branch deletion, global
prune, or recursive filesystem deletion fallback.

JSON reports `eligible`, `retained`, or `removed`, the evidence, and the first
precise retention blocker. `allocated_bytes` measures observed allocated
blocks before removal, deduplicating hardlinks within that tree. It is null
when earlier checks retain a tree without scanning it. It is **not** exact
physical space freed: shared/reflink blocks and open files defeat that claim.
Dry-run exits zero after reporting holds; apply exits one if any explicit
target remains, or two for invalid arguments or unavailable root authority.
Record this output in the coordinator's existing completion evidence, outside
the removal targets; do not commit a mutable worktree list.
Command failures include their exit code even when the command emits no
stderr, so a silent ancestry-test failure remains diagnosable.
Linux mount records and Git metadata backlinks are read as bytes; paths use
the filesystem codec with surrogate escapes rather than requiring UTF-8.
Mount escapes are decoded before path comparisons for both the workspace
and its private Git directory. Unrelated non-UTF-8 mount names cannot crash
planning or conceal a related mount. Malformed records produce an explicit
hold, not an empty inventory fallback. JSON uses ASCII escapes, so
`os.fsencode(json.loads(report)["results"][i]["path"])` restores the original
path bytes even on a strict text-output stream.

Supported live use is Linux with visible same-owner process CWDs and a
coordinator-maintained complete preserved-path list. Other users' assignments
must also be included explicitly; this is not an OS-wide process lock or an
atomic GitHub/filesystem transaction. An uninspectable same-owner process
blocks removal. Do not reassign targets while cleanup is running. API limits,
unavailable history, ambiguous identities, and retained user data are precise
operational holds, never reasons to broaden deletion.
The real-worktree tests explicitly skip hosts without Linux `/proc`; they
scope process inventory to test-owned PIDs, including a real child with its
CWD in the fixture, and exercise unreadable-CWD retention. Simulated mount
records are ordinary fixture files, not privileged mounts. The live helper
still checks all visible processes and fails closed when required visibility
is unavailable.

There are no game-feature dependencies or conflicts, feature flags, CI
topology changes, ROM/RAM/save/config/generated-data/localization changes, or
modern/debug/release/archival build impacts. Workflow dependencies are the
existing completion gate and sole coordinator; conflicts are premature or
forceful cleanup and incomplete active-workspace ownership. Rollback is
reverting this helper and guidance; eligible Git work remains reconstructible
upstream, and any additional recovery objects remain anchored by shared refs.
The automated and human procedure is
[TC-WORKFLOW-WORKTREE-CLEANUP-001](test-cases/workflow-governance.md#tc-workflow-worktree-cleanup-001-remove-only-proven-completed-worktrees).

## Build event classification and candidate evidence

Issue [#177](https://github.com/laqieer/fireemblem8-expansion/issues/177)
adds a no-checkout `event-identity` validator, parsed fail-closed
`event-router`, and mode-specific classifier check ahead of the four expensive
workers. For pull requests with complete identity,
the router checks out
the exact current `pull_request.base.sha` with the pinned checkout action, no
credentials, no submodules, and depth one. A missing PR base uses only the
repository's trusted default-branch ref to execute the failure/bootstrap
classifier; it never substitutes the event's merge `github.sha`. Pushes use
their separate event `after` SHA. The job verifies exact immutable authority
when that base/push identity exists before invoking the closed
`/usr/bin/python3 -I` launcher's `classify-event` mode. A branch whose trusted
base predates the classifier takes an explicit bootstrap full-build path, so
introducing or reverting the seam cannot silently suppress evidence.
The classifier bootstrap may use the trusted default branch when PR base
identity is missing or unusable; worker checkouts never use a merge/default
fallback.

Check contexts are mode-separated. `event-identity` and `event-router` are
common setup only. Every normalized full or metadata run must contain exactly
one successful identity context and one successful router setup context;
missing, failed, skipped, renamed, duplicate, or
unknown setup contexts are invalid. Full candidate runs
expose `event-classifier`, `host-tests`, `build`,
`extended-host-tests`, `legacy`, and `summary`. The running `summary` context
is the sole candidate attestation; it succeeds only after the same full run's
classifier and all four workers succeed. Metadata-only runs expose the running
`metadata-classifier` plus the same canonical worker checks `host-tests`,
`build`, `extended-host-tests`, `legacy`, `patch-release`, and `summary`.
Metadata-only mode requires runner-backed `success` for `host-tests`/`build`
because those jobs run only the trusted continuity adapters, which
independently revalidate the raw edited pull-request event and exact
body/title-only `changes` payload from the runner-owned file-backed
`GITHUB_EVENT_PATH` before succeeding, and exact `skipped` for
`extended-host-tests`/`legacy`. Those adapters accept only a same-owner
regular event file up to 1 MiB, read at most one additional EOF byte, and do
not env-copy large body/title/changes JSON. Repository branch protection
therefore keeps the live canonical `host-tests`, `build`, and `summary`
contexts unchanged. The canonical metadata `summary` is branch-protection
continuity only: it succeeds only after a trusted no-checkout Actions API
proof enumerates the complete exact paginated result set with stable
`total_count`, single-page `Link` omission, exact non-final `next`/`last`
relations, and no final `next`, plus exact per-page cardinality, rejects
redirects before any second authenticated request, validates stable
`workflow_id` plus positive `run_number`/`run_attempt`, classifies exact prior
runs newest-first by `run_number`, requires the in-progress current run to
appear exactly once with matching sequence and exact PR/base/head identity,
skips only conclusively metadata-shaped runs, and confirms the newest
conclusively full Build CI run for the same
repository, PR number, authoritative base SHA, and immutable head SHA
completed successfully. A newer failed, cancelled, in-progress, or malformed
full run blocks older successes.
`candidate_evidence.evaluate_candidate_runs()` still derives eligibility only
from a matching full run, so a metadata-only run remains ineligible by itself
even when the adapters and canonical `summary` succeed.

[`scripts/workflow_pilot/candidate_evidence.py`](../scripts/workflow_pilot/candidate_evidence.py)
derives mode only from the running classifier/summary names and evaluates the latest
exact-head/exact-base full run as one unit. A metadata-only run is never
candidate evidence. A failed full run followed by green metadata remains
ineligible; a prior successful full run remains eligible because the later
metadata continuity run advances only the required canonical `summary`
context after proving that prior full run. Both modes require the same
successful common identity and router setup before their running attestations
are admissible.
A canonical successful `event-identity` context is mandatory in both modes.
A canonical successful `event-router` context is mandatory in both modes.

The classifier reads the bounded `GITHUB_EVENT_PATH` JSON file with duplicate
key and non-finite `NaN`/`Infinity` rejection. The metadata continuity
adapters independently read the same runner-owned file path with no-follow,
same-owner regular-file checks, and a 1 MiB plus EOF bound before parsing.
JSON floats are converted
through `Decimal` to finite binary64: positive/negative exponent overflow and
nonzero values that underflow to zero are rejected, including huge exponents;
normal finite values, representable subnormals, and signed zero remain valid.
The parsed tree receives a recursive finite-number check before
classification, so an unused overflowing field cannot accompany an otherwise
metadata-only event. An `edited` event suppresses `host-tests`, `build`,
`extended-host-tests`, and `legacy` only when:

- the event has a complete pull-request base and exact head identity;
- the event head/base equal the direct event identities used by every
  expensive worker condition;
- the nonempty `changes` key set is exactly `body`, `title`, or both; and
- each changed metadata field has exactly the documented GitHub
  `{"from": ...}` shape, schema-valid previous/current values, and a real
  value transition. A title's previous/current values are nonempty strings;
  a body may transition between null and string; same-value, missing-current,
  nested, malformed, or extra-key claims are not metadata-only.

A base edit uses the production `changes.base.ref.from` and
`changes.base.sha.from` records. Previous and current refs are nonempty,
previous/current SHAs are full identities, and both transitions must differ.
The current `pull_request.base` ref/SHA remains classifier checkout authority;
the previous base identifies the transition only and is never checked out.
Missing, ref-only, SHA-only, same, extra, or spoofed records remain full
fail-closed edits rather than metadata suppression.

Base refs are bounded to 1024 UTF-8 bytes and must satisfy full
`git check-ref-format refs/heads/<base.ref>` semantics; `--branch` shorthand
is never used, and lone `@` is rejected explicitly. The Python classifier
enforces the equivalent grammar without executing a subprocess. The trusted
pre-classifier bootstrap passes the quoted full ref to
`/usr/bin/git check-ref-format` and never checks out that ref.
Empty/whitespace, control or
DEL, space, `~`, `^`, `:`, `?`, `*`, `[`, backslash, `..`, `@{`,
leading/trailing/repeated slash, leading-dot or `.lock` components, and a
trailing dot are invalid. Git-valid slash, dash, and dot forms remain valid.
An invalid base ref is incomplete base identity: a validated head runs all
four workers at that exact head and summary fails; an invalid head runs none.

Base-only edits, mixed edits, unknown fields, incomplete change records,
unknown actions, `opened`, `synchronize`, `reopened`, and `master` pushes with
complete identity select the complete required graph. A classifier
parser/runtime failure (including malformed, duplicate-key, or non-finite
JSON) on a PR with a validated authoritative PR head also runs all four
workers at that exact `pull_request.head.sha` under canonical worker names; it
never uses merge `github.sha`. Summary verifies that every fallback worker
succeeded, then summary still fails to
surface the classifier defect. On a `master` push, classifier failure with a
validated authoritative `github.sha` similarly runs all four workers and the
master-only publisher at that exact push SHA, audits success, then fails
summary. A classifier failure with no validated PR/push fallback SHA or another
unsupported result starts no worker/publisher and fails summary. Missing
identity or stale outputs from a successful classifier cannot select a
fallback worker. Each normal worker runs only when the classifier has a
valid full-build decision and an exact event head. Complete identity or an
explicit valid-head `full_fallback` decision is also required. On a
metadata-only edit, `summary` succeeds only
when classification succeeded, the classified head still equals the event
head, the classified base still equals the event base, suppression is exactly
false, `host-tests`/`build` succeed through the no-checkout continuity
adapters, `extended-host-tests`/`legacy`/`patch-release` are exactly
`skipped`, and a trusted no-checkout Actions API query classifies exact prior
runs newest-first so only the newest conclusively full run for the same
repository, PR number, authoritative base SHA, and immutable head SHA can
authorize continuity. That query first proves pagination completeness with
stable counts, exact page sizes, valid next/last links, stable `workflow_id`,
ordered `run_number` values, and one exact current-run observation, then
rejects redirects before any second authenticated request. Older full
successes never override a newer failed,
cancelled, in-progress, or malformed full run. That canonical metadata
`summary` preserves live required-check continuity only; it is never full-build
evidence by itself. On a full event, normal workers check out the classifier's
exact nonempty head output. Any
missing, empty, malformed, or event-mismatched base ref/SHA with a valid exact
PR head sets `head_valid=true`, `identity_valid=false`, and
`full_fallback=true`. All four workers run at that exact head, then normal
`summary` audits them and fails because full base identity is unavailable or
incoherent. A syntactically valid direct base SHA remains in
`expected_base` for diagnostics even when another base component is invalid;
it never becomes checkout authority. A missing, malformed, stale, or spoofed
head sets no full fallback, runs no worker, and fails. Failure
fallback workers check out only the trusted event setup's validated PR/push
SHA. Both paths retain revision verification, commands, and environments.
Summary now joins the publisher as well: PR and metadata paths require it to
be skipped, while master-push paths require success.
Default-branch ref validation is deferred until a missing/unusable PR base
actually needs classifier bootstrap. Missing or malformed default-branch data
does not abort an independently valid PR-head or push fallback. If classifier
authority is unavailable, router checkout/classification never runs, router
and classifier fail, exact fallback workers plus any guarded push publisher
run, and summary remains failed.

Trusted event setup accepts an event identity only as an exact lowercase
40-hex SHA. A PR additionally requires its numeric event number and exact
`refs/pull/<number>/merge` ref; a push requires event `push`,
`refs/heads/master`, and equal event `after`/`github.sha`. Successful full and
metadata classifications, normal workers, and summary must all bind their
classified head to that same kind and SHA.
Metadata-only classification is accepted only for a coherently bound
`pull_request`; metadata-shaped router output on push or another event fails
the classifier and takes the validated full fallback path. Missing, uppercase,
short, nonhex,
ref-name, ref-number-mismatched, malformed, or cross-event identities select
no worker and cannot produce a successful summary. Classifier-failure workers
also consume only that validated output. Workers consume only that validated
SHA. The
publisher consumes the same validated push SHA, verifies
`/usr/bin/git rev-parse HEAD` immediately after checkout, and stages the
three-file producer from that exact validated after commit without whole-file
source hash pins. Before private download, the exact after tree builds as a
dedicated unprivileged UID inside mount, PID, and network namespaces with no
network, capabilities, secrets, `BASH_ENV`, or `GITHUB_ENV`. Private mount
propagation, recursively read-only host root/system/tool paths, private
`/tmp`/`run`/`proc`/`dev`, and masked D-Bus/container/service sockets leave only
exact candidate source/home/tmp/handoff mounts writable. Every descendant stays
in one exact cgroup v2. The trusted host stops the exact process group and
cgroup, verifies `cgroup.procs` is empty, proves no builder-UID process remains,
and removes the
owned cgroup, then admits only the expected regular, nonsymlink, single-link
32 MiB target and bounded metadata handoff; device, escaped, or unexpected
outputs fail. It removes the builder user, tree, wheelhouse, and candidate
checkout. No complete target ROM enters an Actions artifact, cache, release,
or log.
Before `/sys` is masked, the exact owned cgroup is bound read-only below a
root-only `0700` `/mnt/supervisor`; the candidate cannot read, write, execute,
or traverse that parent. The exact cgroup child there remains read-only. The
wrapper reads that supervisor view after `/sys` is masked and permits handoff
only when its own PID is the sole member. Host-side kill/removal still uses the
actual cgroup path.
Unavailable mount/cgroup features fail closed, and cleanup sends no UID-wide
signal.
Before candidate code starts, a trusted child launcher closes inherited file descriptors
above 2, redirects stdin/stdout/stderr permanently to private `/dev/null`, and
passes no GitHub workflow command-file paths.
Candidate output is never replayed, logged, or uploaded; the trusted host emits
only fixed status text with a numeric exit classification. Arbitrary output
volume cannot change an otherwise successful build. All other writable roots
and regular files retain tmpfs/ulimit bounds; no output sink exists.
The minimal `BASEROM_URL` step then creates an unpredictable, mode-restricted
private path and exposes only that path through trusted output. The immediately
following step runs the staged producer with absolute isolated Python, an
empty runtime CWD/environment, and no repository import path. No candidate
command runs while the base exists. Traps delete the base on success or
failure, cleanup is verified before upload, and later steps see only the patch
artifact.
After private cleanup, a final adjacent step revalidates exactly regular,
single-link BPS/manifest/README outputs immediately before upload.
All repository/candidate-controlled commands finish before private download.
Cleanup is verified before upload.
No whole-file source hash pins are used.
Before the base exists, the fresh hosted publisher proves that no
candidate-written `GITHUB_ENV`, `BASH_ENV`, background process, checkout, or
executable state can survive the builder teardown.
Build workflow preserves the live branch-protection contract directly: metadata
body/title edits run the distinct `metadata-classifier` attestation plus
canonical `host-tests`/`build` continuity adapters and a canonical continuity
`summary` that do not checkout or execute candidate code, while
`extended-host-tests` and `legacy` remain platform-skipped. The adapters
independently reject missing, base-retarget, unknown, empty, duplicate, or
unchanged raw `changes` payloads. The canonical metadata `summary` succeeds
only after a trusted no-checkout Actions API proof confirms one prior
successful complete full Build CI run for the same repository, PR number,
authoritative base SHA, and immutable head SHA. The live required Build
contexts therefore stay the canonical `host-tests`, `build`, and `summary`
names, while metadata-only runs still remain ineligible candidate evidence by
themselves even when those continuity attestations succeed.
The current Build workflow has no explicit final-dispatch trigger; if that
supported surface is introduced later, `workflow_dispatch` classifies as full
and the trigger/topology contracts must be updated together.

A live metadata exercise must use a disposable validation-only PR whose base
is the exact candidate branch containing the classifier and whose head is a
direct, nonempty, non-merge descendant carrying one deterministic tracked
probe. Editing an implementation PR whose base predates the classifier
correctly selects `classifier-bootstrap` and the full graph; it does not test
metadata suppression. The indexed
[`TC-WORKFLOW-BODY-EDIT-001`](test-cases/workflow-governance.md#planned-live-title-only-exercise-after-push)
procedure freezes creation, opened/full evidence, title-edit and title-restore
metadata evidence, evaluator input, and complete PR/branch/worktree cleanup.
Its bounded direct shell helper paginates and excludes all prior IDs, validates
created/event/branch/head fields before each exact-ID watcher, and installs
idempotent compare-and-swap cleanup before remote mutation. The evaluator
scans all raw REST jobs before normalization: metadata workers are admissible
only when `host-tests`/`build` report runner-backed `success` from the trusted
continuity adapters and `extended-host-tests`/`legacy` report `skipped` with
no assigned runner, including the documented platform-only `started_at`
timestamp quirk for the skipped jobs.

The pull-request body/template remains the stable frozen scope, non-goals,
classification, dependency, acceptance, tester procedure, and compatibility
contract. It must contain neither evolving evidence fields nor the canonical
marker. Evolving commands/results, tester actual results, candidate SHA, Build
run, Copilot/security review, unresolved-thread, completion, budget, and
review-size evidence belongs in exactly one canonical PR evidence comment
carrying this standalone marker:

<!-- workflow-pilot-candidate-evidence -->

Update that comment in place. A missing marker, duplicate markers in one or
more comments, a non-standalone marker, or a marker/body evidence field in the
PR body violates the contract. Do not append those facts to the body, title,
decision record, fixture, or another tracked/current-state ledger. Comment
edits emit no `pull_request: edited` event, while Git, GitHub, and Actions
remain authoritative for every value.

The issue #176 reporter's duplicate unchanged-SHA formula needs no new state:
capture the post-pilot Actions cohort with the same inclusive-window and
identity rules, group Build runs by exact `head_sha`, and compare its
`sum(group size - 1)` with the frozen pre-pilot value of 51. The canonical
comment may link the derived before/after report; it does not become reporter
input or an editable metric source.

`--repository-root` is required and must resolve to the exact checked-out Git
top level. Before any metric or report section is constructed, the reporter
requires its `origin` to identify the fixture repository, loads every listed
commit from that repository's real object database, and compares the exact
object type, parent list, committer timestamp, and message. It then verifies
the base commit, each PR's exact candidate-to-merge range, merge-head binding,
frozen-base membership, and the PR-history association of review, run, event,
safety, and override references. A fixture-authored SHA, parent, timestamp, or
message cannot replace Git authority. The fixture, decision, and expected
paths must resolve to the committed baseline inputs, including
`.github/workflow-pilot-decisions.json` in that tree. Build CI passes
`"$GITHUB_WORKSPACE"` explicitly, runs both the same stdlib suite and the
baseline/expected invocation in its required `host-tests` job, and the parsed
workflow topology regression requires both pilot commands exactly. Appended
shell operators, wrappers, substitutions, or changed redirections cannot turn
either command into advisory evidence.

Every authority subprocess uses resolved `/usr/bin/git` with
`--no-replace-objects` and explicit `-C <validated-root>`. Its environment is
constructed from a closed minimum rather than inheriting ambient `GIT_*`
settings: system/global and command-count config injection are disabled,
replacement objects and prompting are disabled, and offline reporter reads
set `GIT_NO_LAZY_FETCH=1`. Git directory, work-tree, object/alternate,
ceiling, namespace, replacement-ref-base, and injected config settings cannot
redirect the checkout. Nonempty repository graft files, replacement refs, and
object-alternate files fail closed. Ancestry is traversed from the already
validated raw commit-object parent lists rather than a replace/graft/shallow-
sensitive rendered revision walk. The supported upstream-port source-root
entrypoint uses the same trusted/minimal Git boundary when resolving its target
checkout before launching repository-relative gates.

The reporter remains offline and fails closed when any fixture commit object
is absent. The development object database used to freeze the fixture already
supplies this authority. A clean Actions
exact-candidate checkout can omit older force-pushed commits, so the
`host-tests` job runs one CI-only helper before reporter tests. The helper
reads only the committed strict baseline fixture, derives its unique commit
IDs and minimal maximal-tip set, and requires fixed `origin` to expose exactly
the corresponding lightweight
`refs/tags/workflow-pilot-baseline/<full-sha>` namespace. The current fixture
derives 12 anchors. Missing, moved, extra, duplicate, malformed, or
incompletely covering refs fail; missing objects are fetched only through the
named refs in bounded no-tags/blob-filtered batches without local ref or
FETCH_HEAD movement. It verifies every identity as a commit and re-derives
coverage from raw parents. From the strict fixture and current decisions it then
derives only override-introduction and first-reviewed commits, resolves their
exact `.github/workflow-pilot-decisions.json` blob IDs from hydrated trees,
and fetches those blobs explicitly without hydrating unrelated blobs. Both
bounded phases recheck that `HEAD`, refs, and FETCH_HEAD are unchanged and
that `HEAD` still equals `EXPECTED_BUILD_SHA`. This hydration is environment
setup, not a 29th local semantic gate; local
`scripts.upstream_port verify` remains network-independent.
The deterministic read-only owner handoff is:

```bash
/usr/bin/python3 -I scripts/workflow_pilot/isolated_launcher.py anchor-refs \
  --repository-root . \
  --fixture scripts/workflow_pilot/tests/fixtures/baseline.json \
  --decisions .github/workflow-pilot-decisions.json
```

These remote refs are operational Git reachability, not committed provenance,
a SHA ledger, an anchor commit, or an object snapshot. The command only prints
derived mappings; it never creates or pushes refs.

Checkout, exact-revision verification, hydration, host dependency setup, and
the three preceding host suites are one exact ordered pre-pilot sequence with
reviewed actions, commands, and fields. Hydration plus both reporter gates use
`/usr/bin/python3 -I scripts/workflow_pilot/isolated_launcher.py`. Python
therefore completes isolated startup before the launcher inserts only its
resolved source root and dispatches its closed modes; it exposes no arbitrary
module, command, or evaluation mode. The
hydration helper uses `/usr/bin/git`. Protected step environments set
`BASH_ENV`, `ENV`, `PYTHONPATH`, and known Git redirection controls to reviewed
safe values and pin `PATH=/usr/bin:/bin`; the isolated launcher removes every
ambient `GIT_*` name before dispatch. Runner environment files,
repository/user `sitecustomize.py`, shell startup hooks, and ambient Git
controls therefore cannot replace either executable or authority root.
At job scope, router and mode-classifier have separate closed setup mappings.
Every combined worker has a closed direct mapping: classifier dependency and
fail-closed condition,
`runs-on: ubuntu-latest`, its reviewed environment, and `steps`. The
comprehensive `build` worker has `timeout-minutes: 90`; `host-tests`,
`extended-host-tests`, and `legacy` remain 60 minutes, while all setup jobs and
summary remain 5. Host, modern, extended-host, and legacy therefore reject
containers, services, matrices/strategies, job permissions/defaults,
other dependencies, conditions/advisory mode, deployment environments, concurrency,
reusable-job `uses`/secrets, custom shell context, unknown fields, duplicate
keys, reordered keys, and complex key aliases. Patch publication and summary
retain separate closed contracts, including coherent push identity,
worker/publisher audit, and dynamic full/metadata summary name.

The 90-minute build ceiling covers observed shared-runner compile variance
without changing any Build content. The coordinator's
`timeout 90m gh run watch <run-id> --interval 30 --exit-status` can expire near
that ceiling; after one exact status query, it re-arms one watcher once only if
the run remains nonterminal.

Before the command succeeds, it creates a bounded mutable artifact sandbox
under the checkout's ignored `build/test-artifacts/` directory and copies only
the three declared pilot artifacts, the expected values, and the focused
reporter-test support files. The sandbox is not Git authority: every sandbox
check receives the original immutable checked-out object database as a
separate root and repeats the complete repository-authority phase. For each
allowlisted artifact it removes the sandbox copy, runs its declared reporter
consumer and/or focused consistency check and requires failure, restores the
copy, and requires both checks to pass. The command identifiers and file paths
come from a closed allowlist in the reporter; fixture text cannot supply
executable commands. Every proof child is exactly `/usr/bin/python3 -I` plus
the copied reviewed launcher's `lifecycle-check` mode, explicit sandbox and
authority roots, and one allowlisted check ID. Only after isolated startup does
the launcher insert the sandbox root. There is no `-c`, `-m`, `-E`, arbitrary
mode, or user/repository `sitecustomize.py` path before control. An empty or
fabricated `git init` cannot validate the
baseline. The sandbox is removed automatically and the checked out worktree is
never modified.

Each PR's `commit_shas` is its authoritative candidate-history set. It contains
the final candidate-to-merge range plus superseded candidates observed by a
review, same-PR workflow run, or typed candidate event. Every review must name
one of those candidates, must not predate that commit, and remains bounded by
the PR lifetime; sharing an ancestor with the candidate branch is not
sufficient.

The fixture carries derivable Git/GitHub/Actions facts. The single versioned
decision record,
[`.github/workflow-pilot-decisions.json`](../.github/workflow-pilot-decisions.json),
contains only the decisions those systems cannot supply:

| Record field | Contract |
| --- | --- |
| `risk_boundaries` | Closed enum; `none` must stand alone. |
| `threshold.triggers` | Closed enum for risk, changed-line, changed-file, and major-boundary triggers. |
| `threshold.override_history` | Ordered override decisions containing only `enabled` and a nonempty reason. The cited introduction SHA must be an actual candidate ancestor of the first-reviewed commit. The reporter reads `.github/workflow-pilot-decisions.json` directly from both immutable Git trees and requires the exact PR, schema, index, entry, and digest to match the current record. |
| `gate_mode` | Exactly `concurrent` or `review-first`. |
| `stack` | Root depth is zero with no parent. Every child requires the immediate parent's decision, authoritative base/branch agreement, and depth exactly `parent.depth + 1`; cycles and depths above three fail. A genuine root -> depth-one -> depth-two -> depth-three chain requires a nonempty exception reason only on its depth-three member. |
| `pilot` | Inclusion boolean and a closed pilot disposition. |
| artifact admission/history | Owner, executable consumer, unique decision, consistency check, bounded cost, deletion criterion, expiry, and disposition history. |

Candidate/event SHAs, override-introduction timestamps, ancestry, branches,
diff size, review/run status and conclusions, and current PR state are
forbidden from the corresponding decision schema. Artifact audit history
retains only its non-derivable disposition decision and decision time. Unknown
keys cannot override a derived fact. Duplicate JSON keys, missing spotlight
decisions, unknown enum values, and an override inserted, backdated, reordered,
or changed after its authoritative introduction all fail. A newly authored
fixture event plus an old candidate SHA is insufficient when that commit tree
lacks the exact decision entry; Git remains the only stored commit-content
authority, and the decision record stores no copied tree, blob, or commit hash.
Every schema version, identity, count, duration input, attempt, index, depth,
and cost uses exact-integer validation before bounds or equality checks; JSON
booleans are accepted only by declared boolean fields.

## Reproducible formulas

All durations use exact UTC seconds. Subject durations are converted to decimal
hours and rounded half-up to one decimal. The frozen baseline uses the upper
middle observation for an even-sized cohort; this explicitly preserves the
already-published 9.4-hour value instead of silently changing its statistical
convention.

| Metric | Formula and inclusion |
| --- | --- |
| Issue-to-merge | For each in-window merged PR with closing issues: `merged_at - min(linked issue created_at)`. Report the eligible count and authoritative empty-link exclusions. |
| First-push-to-clean-review | For each declared subject: the first GitHub-visible candidate boundary is the earlier of PR `created_at` and its earliest retained Actions head event. Subtract it from the first Copilot review with no findings only when complete GitHub review-thread webhook history proves every cumulative prior thread's latest action strictly before that review is `resolved`. `unresolved` transitions remain cumulative evidence, and a delivery at or after the review cannot make that review clean retroactively. Without complete source coverage or a proven clean boundary, emit `status: unavailable`, a nonempty reason, `pilot_ready: false`, and `median_hours: null`; pilot comparison or promotion must not consume a numeric value. Local commit dates and current thread state are never substituted for historical delivery evidence. |
| Review rounds | Count unique submitted Copilot review IDs for the subject PR. |
| Valid findings | Count captured inline Copilot finding identities independently of historical timing availability; report current resolved/unresolved counts, `findings / ((additions + deletions) / 1000)`, and `findings / review rounds`. A zero denominator is reported as unavailable, not infinity. Current unresolved conversations must still be zero for real delivery. |
| Build totals | Validate status, conclusion, start, completion, and repository-backed head-SHA availability for every authoritative workflow run before selecting the declared latest-1,000 cohort. Build and non-Build `created_at` cannot precede the real commit time. Completed intervals cannot end before they start; in-progress runs require a start; queued/in-progress runs require null conclusion/completion; timestamps cannot exceed the snapshot. Then count runs whose workflow name is `Build CI` and exhaustively partition terminal `success`, `failure`, `cancelled`, `neutral`, `skipped`, and `action_required` conclusions plus active queued/in-progress runs. Unknown conclusions fail instead of disappearing from the partition. |
| Build minutes | Sum `completed_at - started_at`; clamp an in-progress run at the inclusive end, count a queued run as zero even if `started_at` is populated, then floor the aggregate seconds divided by 60. |
| Duplicate unchanged-SHA Builds | Group sampled Builds by exact `head_sha`; sum `group size - 1` for groups larger than one. Attempts remain separate runs. |
| PR #150 Build totals | Select sampled Builds whose `head_branch` equals PR #150's authoritative head branch; older matching runs outside the declared latest-1,000 cohort are excluded. Apply the same exhaustive status and minute formulas. |
| Base changes and close/reopen | Count authoritative base-change events. Close/reopen cycles are `min(close events, reopen events)`, so the final merge closure is not a fake cycle. |
| Conflicts | Count typed authoritative conflict events for every PR inside the window. No event means zero; an unknown event name is not treated as zero. |
| Superseded candidates | For a PR head branch, count distinct authoritative workflow `head_sha` values minus one. Repeated runs on one SHA are duplicates, not supersessions. |
| Escaped defects, broken master, security findings, manual rejects | Count their typed in-window events across every PR, independently of spotlight metrics. Each identity binds a full SHA to an authoritative commit and that PR's candidate/merge history. |
| Reverts | Parse exactly one case-sensitive `This reverts commit <40 lowercase hex>.` trailer as the final bytes of the canonical raw-authority message, after only the single conventional terminal LF normalization. The standard `git revert` subject, blank line, and final trailer pass. Changed casing, short/uppercase SHA, leading/trailing trailer text, extra trailing blank/text, or multiple trailers fail. Require both commits in the real object database and frozen base history, require the target to be an ancestor of the revert, and require the revert's authoritative committer timestamp to be strictly later than the target's. |
| Pilot coordination and metadata maintenance | Sum only typed in-window pilot minutes. Report both beside saved Build and review minutes; net saved minutes are savings minus both overhead classes. |

Every PR-scoped event must occur at or after PR creation and no later than the
fixture lifecycle boundary. Base, review-savings, conflict, manual-reject,
override, supersession, and ordinary coordination events require an open PR;
close/reopen transitions must alternate. Candidate SHA events cannot predate
the referenced commit. Broken-master and escaped-defect events are post-merge
events and must identify an available commit on the merge-to-frozen-base
history. A security finding uses candidate history before merge and
merge/default-branch history at or after merge. Only those post-merge families
may follow final PR closure.

The 9.4-hour published baseline is specifically **PR-open-to-merge**, because
that is what the original measurement could reproduce for all 64 PRs. It is
not relabeled as issue-to-merge. The reporter additionally calculates
issue-to-merge over the 57 PRs with authoritative closing-issue relationships
and reports the seven exclusions.

## Work classification

Classifications are derived and may coexist:

| Class | Rule |
| --- | --- |
| Cancelled work | PR is closed without a merge; a cancelled Build is separately a terminal run conclusion. |
| Still-running work | An open PR retains its PR-derived `work_state`. Independently, any queued or in-progress run associated by authoritative PR branch/ref adds the `still-running` flag, so it coexists with merged, cancelled, stacked, superseded, or other classifications. |
| Superseded | More than one exact candidate SHA is observed for the PR branch; old-SHA runs remain cost evidence. |
| Stacked | Authoritative base differs from the default branch and the decision's immediate parent/base relation agrees. Depth three requires the documented exception. |
| Generated-only | Every changed path is under a controlled generated prefix/suffix; a mixed diff is not generated-only. |
| Bulk-deletion | At least 1,000 changed lines and deletions are at least 80 percent of additions plus deletions. |
| Reverted | A later fixture commit has the exact Git revert relation to the PR merge SHA. Both delivery and revert remain visible. |

No class removes a run, review, finding, or cost from the identity history.

## Artifact lifecycle

Every pilot artifact is admitted only when all six decisions exist: one owner,
one executable consumer, one unique decision, one executable consistency
check, a bounded estimated maintenance cost, and a deletion criterion.
Authoritative dependency edges bind consumers/checkers to the artifact in both
directions: every listed dependency must target its owner, and every
`consumes`/`checks` edge must be claimed exactly once by that target artifact.
Semantic duplicate edges, ambiguous claims, orphan derives/review dependencies,
duplicate artifacts, duplicate unique decisions, expired retained artifacts,
and unknown edge types fail.

Review invalidation is derived from `review_depends_on` edges and later
`dependency_changed` events. Unknown events or edges require contract review
and are rejected rather than ignored. Every checkpoint and pre-graduation
event, plus every dependency-change event that occurs, requires exactly one
strictly later non-destructive deletion proof:

- if removal preserves semantics, restoration must pass and the current
  disposition must be `Delete`;
- if removal loses a named invariant, the proof must name that reason,
  restoration must pass, and `Delete` is forbidden.

Every proof in the required history is checked independently, so a failed
earlier restoration cannot be hidden by a later successful proof. All proofs
for one artifact must simultaneously agree with the current disposition:
`Delete` requires every semantic removal result to pass; every retained
disposition requires every result to fail for one exact shared named reason.
Each proof must also have a unique identity, the correct artifact and trigger
kind, a strictly later timestamp, successful restoration, and a disposition
that follows it. No latest/any/all aggregation can mask a mixed record. For the
committed baseline's three retained artifacts, every recorded proof must state
the allowlisted issue #176 semantic failure and successful restoration, and
the reporter reruns that removal/restoration behavior in a plain temporary
artifact sandbox beneath the checkout's ignored `build/test-artifacts/`
directory. The original checkout and its object database remain the separate,
immutable Git authority. The sandbox is bounded to allowlisted files, removed
automatically, and never commits or modifies the source artifacts. A stale
restored artifact, fabricated reason/result, or fixture-authored command fails
before a success-shaped report is emitted.

Disposition values are exactly `Delete`, `Derive`, `Consolidate`, and
`Graduate`. History is append-only and strictly chronological; current
disposition is the last derived value, not a second mutable field. It must be
strictly later than every justifying proof and no event or disposition may
follow `lifecycle_as_of`. Expiry is evaluated at that lifecycle boundary,
rather than the older metric-capture timestamp. The committed baseline
contract, fixture, and reporter currently derive to `Graduate` because
removing any one loses a dependency required by issues #177 through #181.

## Frozen expected results

| Baseline fact | Expected result |
| --- | ---: |
| In-window merged PRs | 64 |
| Frozen PR-open-to-merge median | 9.4 hours |
| Linked-issue cohort / issue-to-merge median | 57 PRs / 44.7 hours |
| PR #150 first-visible-candidate-to-clean-review | Unavailable: historical review-thread events were not collected |
| Latest sampled workflow runs | 1,000 |
| Sampled Build runs | 326 |
| Build success / failure / cancelled / active | 98 / 61 / 166 / 1 |
| Build neutral / skipped / action-required | 0 / 0 / 0 |
| Sampled accumulated Build minutes | 9,939 |
| Duplicate unchanged-SHA Builds | 51 |
| PR #150 Copilot review rounds | 34 |
| PR #150 inline findings / current unresolved | 101 / 0 |
| PR #150 Build runs | 79 |
| PR #150 accumulated Build minutes | 2,210 |
| PR #150 base changes / close-reopen cycles / superseded candidates | 31 / 16 / 45 |

These values are immutable fixture expectations only. The reporter derives
them from identities and timestamps; they are not editable fields in the
decision record.

The expected file has one closed, versioned path set: every frozen scalar
snapshot/computed result is required, and collection-shaped classifications,
reverts, and artifact results participate in the domain-separated computed
result seal. Missing, extra, renamed, malformed, or duplicate expected paths
fail before any value comparison. The independent v2 identity/relationship
seal remains required as the fixture-input boundary. A third,
domain-separated non-derivable decision seal covers every validated PR
governance field, artifact admission/disposition field, override/review
boundary, lifecycle proof, authoritative artifact source, and dependency
association without copying Git objects or adding a source/ROM identity gate.

Review-size evidence is deliberately not stored as mutable current-head
numbers in this document. At each candidate, the coordinator derives the
changed-file list, per-file numstat, and shortstat from
`git diff <immediate-base>...HEAD` and publishes that exact-head preflight in
the canonical evidence comment. Updating that comment never edits the stable
PR body and never emits a Build-triggering pull-request event.

The unavailable clean-review result is the actual historical boundary, not a
zero, success, or estimate. It makes that metric ineligible for pilot
comparison/promotion while leaving the 34 review rounds, 101 finding
identities, current zero-unresolved state, and every other frozen baseline
metric useful. Future pilot collection must register a repository webhook,
capture GitHub's `pull_request_review_thread` `resolved` and `unresolved`
deliveries as they occur, and normalize their delivery API identities and
timestamps into a complete coverage interval before the reporter can emit a
numeric clean-review duration.

## Relationships, impact, and rollback

| Relationship | Contract |
| --- | --- |
| Dependencies | None |
| Dependents | Issue #177 (event classification), plus issues #178, #179, #180, and #181 |
| Conflicts | None with runtime, configuration, save, generated game data, localization, or archival behavior |

Modern debug impact: none. Modern release impact: none. Archival impact: none.
There is no feature flag and no ROM, RAM, save-format, generated-data,
localization, or runtime/gameplay effect.

Rollback is a normal revert of the dedicated issue #176 commit. Because no
delivery behavior or final gate changes here, existing CI, review, merge,
runtime, save, localization, generated-data, and archival behavior remains in
place throughout rollback.

## Sealed exact-tree execution capsules

Issue [#204](https://github.com/laqieer/fireemblem8-expansion/issues/204) adds an
accepted **framework capability: immutable Python execution capsules** in
[`sealed_capsule.py`](../scripts/workflow_pilot/sealed_capsule.py). This is the
independent foundation for issue #179 / PR #189, not a new sibling-family
policy. Its production consumer is the existing isolated launcher's
`classify-event` mode. The event decisions, output keys, Build graph, required
contexts, and baseline metrics are unchanged.

### Authority and threat boundary

The trusted initial launcher, system `/usr/bin/python3`, `/usr/bin/git`,
platform standard library, and Linux kernel are the bootstrap authority.
Start that launcher in the trusted checkout selected by the existing workflow;
do not execute a candidate-owned launcher or obtain signing credentials in
candidate code. The capsule is not a sandbox for arbitrary candidate Python,
nor does it defend against root or a compromised interpreter/kernel.

`prepare()` takes **independently trusted exact commit IDs**, program paths
and data/module declarations. It verifies raw Git commit, tree and blob
objects by their Git identities, traverses exact tree entries, and derives
the complete static trusted import closure, including package initializers
and proven absent namespace initializers. Dynamic trusted imports must be
declared in `CapsuleSpec.modules`; undeclared imports cannot fall back.
Static standard-library package imports also preload their named submodules:
`from xml.etree import ElementTree` needs no dynamic declaration. Preparation
inspects builtin/frozen specs and explicit platform-library package paths,
not ambient `sys.path`, `sys.modules` parents or meta-path importers. Ordinary
exports such as `collections.Counter` are not mistaken for submodules. The
isolated worker completes these trusted imports before closing its importer;
there is no post-validation pathname fallback.
Before program code runs, `sys.modules` retains only declared standard-library
names and their package parents; execution adds modules loaded from the sealed
base closure. Runtime/bootstrap modules and undeclared preload dependencies are
removed from that table; the runtime keeps its own private references.
Ordinary imports, cached `__import__` calls and `importlib.import_module()`
check the same declaration even on cache hits. Direct cache lookup cannot
recover an ambient module, and inserting an undeclared cache alias does not
authorize an import. Declared package exports and module aliases remain usable.
Programs/modules come only from the `base` tree. Other declared trees are
inert data, not executable candidate imports. Missing data and symlink data
are represented explicitly: `read()` returns `None` for absence and literal
link bytes for a symlink; it never follows a link.

The canonical, bounded in-memory bundle includes the exact spec, complete
proof-object closure, and each artifact's tree slot, canonical relative path,
mode, blob ID, byte size, SHA-256 and role. Missing, extra, duplicated,
wrong-mode, wrong-blob and wrong-role entries reject. These are ephemeral
execution artifacts, not committed source snapshots or a source/ROM identity
ledger. Git remains the source/history authority.

Runtime, selected program, canonical request and artifact bundle are separate
anonymous `memfd` descriptors. Write, grow, shrink and seal seals are applied
before execution; each owned descriptor retains its inode/size identity.
Mutable, aliased, replaced, reused and unexpected inherited descriptors
reject. The actual argv is `/usr/bin/python3 -I -S -c <protected startup and
closed bootstrap> <interpreter descriptor/identity> <capsule descriptor
identities>`. The kernel executes an anonymous, fully sealed **execute-only**
copy of the fixed root-owned system Python, bounded to 32 MiB. Its
`/proc/self/fd/N` executable reference resolves the already-owned interpreter
inode, not a repository or temporary script. The bootstrap still reads all
capsule runtime/program bytes with `pread`, never a pathname or
`/proc/self/fd/N` script; modules/data come from the artifact descriptor.
Changing or restoring any former pathname cannot change those bytes.

Ordinary Linux exec resets dumpability to 1: a Python or native bootstrap that
calls `PR_SET_DUMPABLE` afterward leaves a same-UID access interval. This
launcher instead requires the interpreter image to be unreadable under the
launching credentials and an existing `fs.suid_dumpable` policy of `0`
(disabled) or `2` (root-only). Both exclude same-UID FD/ptrace access at exec,
**before the dynamic loader or Python initializes**; `1` is unsafe and rejected.
Startup verifies the kernel-selected state, disables root-only dumping when
needed, and verifies state `0` **before inspecting or reading capsule
descriptors**. It never repairs an exposed ordinary exec.
During root-only initialization, the new address space contains only the
trusted platform/closed bootstrap and non-secret launch metadata, not capsule
source/input bytes or the parent's signing key. Capsule bytes are not mapped
or read until dumping is disabled. Parent/fork state remains non-dumpable.
No sysctl change, setuid helper, privilege, executable disk copy or ordinary-exec
fallback is used.

The protected image is retained by each guardian, validated against its
executing inode and inherited source identity, and reused by every nested
launch. It is closed with the other non-protocol descriptors before worker
entry. Image seals, execute-only mode, effective unreadability, source identity
and kernel policy are rechecked before launch; failure means no admitted
execution. Preparation/constructor failures and normal teardown close the
owned image without closing an unrelated descriptor reused at its integer.

After platform-stdlib preload, the child has an empty `sys.path`, a closed
descriptor-backed importer and no bytecode/file-loader fallback. Filesystem
reads/writes, filesystem module specs, undeclared imports, subprocess/fork,
signals, native-library loading and network attempts reject. A caught denial
is latched and cannot be converted into a successful receipt. This is an
execution-authority boundary for trusted programs, not a claim that Python
audit hooks can confine arbitrary malicious Python or native code. A
worker-only seccomp-BPF filter uses a **closed capability allowlist**, with
explicit x86-64 syscall mappings and process termination for every
unlisted, future or alternate-ABI call. It is not a growing pathname blacklist.
Prospective AArch64 mappings remain available for parsed filter tests only;
neither platform admission nor the native installer enables that runtime.
The allowed capabilities are private descriptor I/O/duplication/readiness,
anonymous memfds/pipes, interpreter memory allocation/synchronization,
self/runtime observations, clocks/entropy, signal handling and exit.
Only protocol pipes survive FD admission; no filesystem descriptor or
pathname-acquisition capability is available afterward. `fstat` can inspect
those private descriptors, not reopen a path. Restricted `fcntl` commands
support descriptor flags/duplication, pipe-size inspection and memfd seals,
not asynchronous signal ownership, filesystem commands or leases.
`prlimit64` can only inspect the worker's own limits (`pid=0`,
`new_limit=NULL`); it cannot inspect another process or change a limit.

Thus pathname acquisition, metadata, mutation and probes are all denied:
access, stat/statfs, readlink, directory enumeration, chdir/chroot,
ownership/mode changes, extended attributes, timestamps, link/rename/delete,
mount/handle/quota/watch interfaces and exec. Network acquisition,
process/thread creation, group/session changes, signaling and ptrace/prctl
are also absent. Unknown `os.*` audit events, as well as native-library,
resource, subprocess and socket audit events, latch a denial rather than
silently gaining authority. In particular, neither unaudited `os.access()`
nor caught `os.chroot()`/`os.chown()` errors may turn live filesystem
existence into a signable verdict. Test declared artifact existence with
`context.entry(tree, path)["mode"] is not None`; this answers from the sealed
Git entry even if the working pathname is created, deleted or restored.
The guardian itself retains the capabilities to supervise and launch declared
nested capsules.

### Public API and production integration

```python
from scripts.workflow_pilot.sealed_capsule import CapsuleSpec, prepare, sign_receipt

spec = CapsuleSpec(
    trees={"base": trusted_base_sha, "origin": finding_origin_sha, "head": candidate_sha},
    programs={"checker": "checks/checker.py", "assertion": "checks/assertion.py"},
    modules=("checks.dynamic_helper",),
    data={slot: ("inputs/state.json",) for slot in ("base", "origin", "head")},
)
with prepare(repository_root, spec) as prepared:
    result = prepared.execute("checker", request, timeout=30)
    # Obtain/use the parent-only key after preparation; never pass it to a child.
    signed = sign_receipt(result, trusted_key)
```

Paths in this example are downstream-owned declarations, not additional
files required by this repository. Each trusted program defines
`capsule_main(request, context)` and returns a JSON value. `context.read(tree,
path)` supplies immutable bytes; `context.entry(tree, path)` supplies verified
metadata; `context.load_module(name)` imports only the declared base closure.
`context.invoke(program_id, request)` asks the guardian to execute another
declared program with the same sealed mechanism and returns
`{"value": ..., "receipt": ...}`. No program receives a repository root or
materialized-root fallback. Programs needing filesystem-based probes must
separate that non-authoritative production/probe work from the trusted
assertion and provide explicitly declared inert inputs; do not disable the
loader boundary to retain old pathname helpers.

`ExecutionResult.value` and `.receipt` return fresh parsed values.
`sign_receipt()` accepts only an admitted execution result, and never shares
the key with a child. `verify_receipt(signed, key, expected_result)` requires
that exact invocation, not just matching semantic output. The receipt binds
the actual argv, fresh invocation nonce, program/runtime/artifact/request/
payload/output digests, exact loaded artifact metadata, stdout digest, empty
diagnostics and zero exit status. Caller-owned #179 finding/round/disposition
bindings remain in the request and semantic result; callers still validate
them. Durable cross-invocation replay scope/publication remains the existing
receipt consumer's responsibility, not a second persistence subsystem.
Receipt construction and `.receipt` parsing share `MAX_RECEIPT_BYTES`;
signing and verification enforce that same inner bound and the separate
`MAX_SIGNED_RECEIPT_BYTES` wrapper bound. An admitted result cannot defer
a size failure until its receipt is first used.

The existing production `classify-event` mode bootstraps the runtime directly
from hash-verified exact-HEAD Git object bytes, with no repository entry in
`sys.path`. Its CLI transport also comes from the sealed classifier artifact.
It captures the runner event once through a bounded no-follow same-owner
regular-file descriptor, seals the canonical request, executes the actual
classifier, and writes the existing GitHub output protocol only on success.
The direct Python predicate remains usable for diagnostic/host tests; it is
not an authenticated capsule receipt.

### Resource, failure and portability contract

Bundles are at most 16 MiB (aggregate decoded Git bytes are bounded while
collecting); programs/modules at most 2 MiB; requests at most 4 MiB; output at
most 1 MiB; each diagnostic stream at most 64 KiB. There are at most 1,024
artifacts, 8,192 distinct Git proof objects, eight tree slots, 32 programs and
four nested invocation levels. The proof-object count is enforced before
fetching an additional object, not only when parsing a serialized bundle;
cached objects remain usable at the limit.
Each construction or independent bundle validation keeps its own parsed-tree
cache keyed by immutable Git OID. Only a fully identity- and structure-validated
tree enters the cache; shared-prefix lookups parse each distinct tree once,
not once per artifact. Replaced object bytes are revalidated, malformed suffix
entries still reject, and no cache crosses an independent validation boundary.
The existing proof-object count and aggregate byte limits apply unchanged.
Canonical receipts have a separate **1 MiB + 4 KiB** ceiling: the additional
4 KiB bounds parent-added argv/bootstrap and digest metadata, which is not
part of the worker output. Signed receipts allow exactly 93 further bytes
for the canonical outer object and 64-character HMAC. Both encodings include
one trailing newline. These are receipt-specific allowances, not an increase
to program output, collection, requests or nested-message limits.
Construction, parsing, signing and verification reject over-limit records.
A near-limit loaded-artifact list can therefore produce a usable top-level
receipt even when the added metadata crosses 1 MiB; a nested value-plus-receipt
that exceeds its existing 1 MiB transport still rejects.
Each invocation accepts a positive timeout up to 120 seconds. A worker also
has a 512 MiB address-space ceiling and a CPU ceiling. On the supported x86-64
runtime, hard `RLIMIT_NOFILE=64` bounds new descriptor allocation, and hard
`RLIMIT_FSIZE=1 MiB` bounds kernel-backed file growth, including writable
memfds. Pipes/memfds can exhaust only the worker's descriptor allowance;
writes cannot extend a memfd past its file-size allowance. Fixed inherited
protocol descriptors remain usable at the limit, so declared nested
invocations still run through the separately supervised guardian.

A separate guardian owns the worker group, retains the leader until group
cleanup to avoid PID reuse, and reaps descendants. A private liveness pipe
detects parent exit, signals and interruption, including during nested
execution/reply handling. The worker has a parent-death kill signal as an
additional guardian-crash control. Timeout, crash, partial/oversized output,
malformed messages, nonempty diagnostics and interrupted execution produce
no admitted result or signature. Owned descriptors close on every path,
without closing an unrelated FD reused at the same integer.

The child owner is active **before** liveness-pipe creation and launch, and
the launcher binds the new process to that owner before returning its handle.
Cleanup covers every `BaseException` from launch handoff, stdin closure,
deadline/`fileno()` setup, selector entry, collection and result validation,
not just failures inside `select()`. Python signal handlers are deferred
while process registration or cleanup is critical, including signals received
by another thread and dispatched on the main thread. Original caller handlers
and masks are restored; protected Python children restore their original mask
before running trusted code. Probe and Git-read processes also have an owner
through their launch-to-collection handoff.
The isolated launcher's pre-runtime Git bootstrap has the same ownership
boundary implemented using only the already trusted launcher and stdlib:
it cannot import a repository lifecycle helper before proving those bytes.
Constructor-return SIGINT, output-descriptor/deadline setup, collection and
nonzero-exit failures all reap their owned child and close its pipe streams.
A process-group `ProcessLookupError` after exit does not skip the wait.
Other cleanup errors are chained behind the original failure, never substituted
for a timeout/output-limit error. Already reaped/unowned PIDs are not signaled
or waited again; a still-owned live child remains terminable by its reserved
PID if the group is unavailable.

Cancellation closes the owned liveness writer, gives the guardian its bounded
cleanup interval, then terminates and reaps only its still-owned child if
necessary. `waitid(WNOWAIT)` preserves the PID until group teardown; already
reaped or unowned PIDs are never signaled or reaped again. Worker registration,
group establishment and reap-state updates are protected too; a worker
interrupted before group establishment can still be terminated by its owned
PID. Liveness and supervisor pipes retain inode identities so cleanup cannot
close an unrelated FD reusing an old integer.

Supported execution is currently **Linux x86-64 with 64-bit Python 3.10+ only**,
for both the trusted preparation interpreter and fixed, root-owned
`/usr/bin/python3` execution interpreter. Both must provide
`sys.stdlib_module_names`, the required `os` descriptor/process APIs and
constants, `fcntl` seals and `resource` limits. It requires sealed memfd,
mounted/readable `/proc/self/fd` and `/proc/self/exe`, fork, process groups,
`waitid(WNOWAIT)`, POSIX signal-handler deferral, and the
non-dumpable/subreaper/parent-death prctl facilities
plus unprivileged seccomp-BPF. Executable memfds, enforceable execute-only
permissions and a readable `/proc/sys/fs/suid_dumpable` already set to `0` or `2`
are required. A caller whose credentials bypass image read permissions is
unsupported. Admission changes no system policy or privileges. Neither execution nor the real
parent-death regression requires pidfds. The regression observes saved
process generations and reaps adopted children; cleanup signals only
generation-matching children whose PIDs remain reserved until reaped.
AArch64 and other unvalidated ABIs fail closed before Git collection,
descriptor creation or process launch, and cannot install a native filter.
Parsed AArch64 mapping tests are not end-to-end runtime evidence; support
must remain disabled until real AArch64 execution has been validated.
Git SHA-1 repositories are supported; SHA-256
repositories are explicitly unsupported. Missing facilities raise `CapsuleUnavailable`
with disposition `sealed-capsule-unavailable`; the production launcher exits
nonzero and does **not** retry through a pathname, ordinary importer,
temporary directory or post-execution rehash.
Preparation checks the caller's Python version/capabilities, runtime ABI and
descriptor enumeration before collecting Git objects or creating capsule
resources. One fixed `-I -S` probe executes the protected system image before
any capsule descriptors or Git collection, with no candidate code or ambient
environment, a five-second deadline and a 4 KiB limit per output stream.
It inherits only its trusted interpreter image, not capsule authority.
An old or capability-incomplete interpreter, failed probe, unavailable protected
exec facility or malformed report raises `CapsuleUnavailable` before capsule
resources, workers and programs are launched. There is no `sys.executable` or PATH fallback.
Admission is per capsule, not a global cache: executable identity and metadata
are checked after probing and before reuse, including nested execution.
A changed system interpreter requires fresh preparation. Sealed descriptors
do not spawn probes, and guardians reuse their inherited protected-image
admission for nested calls. An unavailable procfs during guardian admission
retains the same explicit disposition.

The reporter-suite host regression retains the completed Build
`33995240775`/job `101384581761` failure: the zero-only policy gate rejected
capsule admission in both unit cases and fresh isolated-launcher subprocesses.
That log did not record the actual policy bytes. The corrected admission
distinguishes safe root-only mode `2` from unsafe mode `1`; rejection diagnostics
now include the bounded observed value. Policy/transition fixtures cover `2`
without modifying the host, while real kernel-entry tests exercise the host's
actual mode without skipping protection checks. Native mode coverage must be
reported separately from those fixtures.
Runtime-instrumentation tests restore the disposable fixture's committed Git
authority as well as its working file. Otherwise a subsequent isolated
classifier correctly loads the still-instrumented `HEAD`, even though the
pathname looks restored. A real-classifier regression rejects that file-only
cleanup control; fixture commits remain confined to owned test repositories.

### PR #189 adoption and completion boundary

PR [#189](https://github.com/laqieer/fireemblem8-expansion/pull/189) remains a
dependent integration, not part of this master-root foundation:

| Existing #189 seam | Required adoption |
| --- | --- |
| `trusted_review_gate.run_base_pinned_checker` | Prepare independently trusted base/origin/head closure, execute the declared checker, validate its result and sign only the admitted receipt |
| `review_base_checker.execute_registry` | Replace both behavior and member subprocess launches with `context.invoke`; remove old basename/absolute-path argv receipts |
| `review_assertions.read_text`, JSON/blob/syntax helpers | Read bytes/metadata by tree slot through `context`, never materialized roots |
| Trusted module loaders and `checker_cli_runtime` | Use the closed module loader / nested capsule invocation, not `sys.path`, file specs or normal imports of repository paths |
| Remote-round and local-remediation loops | Reuse this mechanism with independently validated per-round/finding/head request bindings |

All five #179 families, all dispositions, remote/current versus historical
identity, and the third-round hold remain #179's unchanged policy. Their
end-to-end integration tests and adoption in **all** listed paths must pass
on the later PR189 candidate before #179/PR189 can complete. Foundation tests
do not claim those absent-on-master consumers already passed. There must be
no compatibility branch that reinstates validate-then-reopen execution.

Dependencies: the existing isolated launcher, exact Git object store and
standard-library host test runner. Dependents: #179/PR189. Conflicts: PR189's
old launch/loader interfaces; none with gameplay or feature profiles.
Modern debug/release, archival, save/config identity, generated game data,
localization, ROM/RAM and Build topology are unchanged. No new package,
feature flag or human approval gate is introduced.

The indexed procedure is
[`TC-WORKFLOW-SEALED-ASSERTION-CAPSULE-001`](test-cases/workflow-governance.md#tc-workflow-sealed-assertion-capsule-001-execute-exact-tree-sealed-capsules).
Rollback is a normal revert of the dedicated #204 change. Keep PR189 blocked;
do not roll it back to pathname authority.
