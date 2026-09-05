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
- Review identities are submitted Copilot reviews on those PRs. The immutable
  issue #176 baseline preserves the historical REST author tuple
  `type: Bot`, `node_id: BOT_kgDOCnlnWA`, `id: 175728472`, and
  `login: copilot-pull-request-reviewer[bot]`; the trusted live review gate
  separately authenticates the same actor from GraphQL only as exact
  `__typename: Bot`, `id: BOT_kgDOCnlnWA`, `login: copilot-pull-request-reviewer`.
  Inline Copilot comments are review findings, and each authoritative
  historical finding record preserves the same exact REST actor tuple rather
  than a login-only string or implied bot family. GitHub exposes each thread's current
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
The historical REST actor migration raised the frozen baseline fixture to
schema v2 and refreshed only `identities.seal` plus the decision
record-derived `decisions.seal`; the frozen semantic metric values, formulas,
and availability reasons remain unchanged.

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
In the trusted live gate, both the exact base decision record and the current
candidate PR record must exist for the same PR, and the candidate's
decision/trigger fields must remain identical to the authoritative base state.
The candidate path is read only after exact candidate/base Git-tree mode
validation confirms a regular blob and a no-follow regular-file read still
matches the candidate tree blob; symlink, parent-symlink, escape, replacement,
and tree/worktree mismatch all fail before trust. The live gate pins the
repository root directory and walks each relative parent with a dirfd/openat
no-follow directory open before reading the leaf.
Every schema version, identity, count, duration input, attempt, index, depth,
cost, and authoritative REST actor database ID uses exact-integer validation
before bounds or equality checks; JSON booleans are accepted only by declared
boolean fields.

## Sibling-family review convergence

Issue [#179](https://github.com/laqieer/fireemblem8-expansion/issues/179) extends the issue #176 seam with an accepted **framework capability: review-convergence contract**. It does not change the frozen baseline fixture, expected values, decision seal, or reporter formulas. Candidate code emits only strict, inert JSON for structural diagnostics:

```bash
/usr/bin/python3 -m scripts.workflow_pilot.review_family \
  --repository-root <exact-candidate-checkout> \
  --expected-candidate <full-head-sha> \
  --contract <candidate-contract.json> \
  --evidence <untrusted-evidence.json>
```

Production authority is deliberately absent from the candidate package. `github_review.py` and `isolated_review_gate.py` do not exist, and the candidate receives no GitHub token, HMAC key, replay store, merge credential, or push credential. A trusted coordinator invokes `scripts/workflow_pilot/trusted_review_gate.py` only from the exact clean PR base checkout:

```bash
/usr/bin/python3 -I \
  <trusted-base>/scripts/workflow_pilot/trusted_review_gate.py \
  --trusted-root <trusted-base> \
  --candidate-root <untrusted-candidate-checkout> \
  --expected-base <current-live-pr-base-oid> \
  --expected-candidate <authoritative-pr-head-oid> \
  --contract <candidate-contract.json> \
  --pre-review-state <new-or-preserved>
```

`new` additionally supplies `--review-receipt <independent-pre-review-receipt.json>`. `preserved` rejects that argument and loads the accepted bytes only from the trusted replay store. When the exact base decision file has no record for the current PR, the trusted coordinator must preregister one immutable GitHub PR comment with the standalone prefix `workflow-review-family-decision:v1 ` before the first remote review. Its canonical closed JSON binds the exact repository ID/name, PR number, base SHA, original first-reviewed head, preregistered initial remote head, and the full normalized decision entry. Later descendant remote heads may reuse that immutable preregistration only while current PR commit history and Git ancestry preserve that preregistered head as a non-rewritten ancestor. That payload `candidate_sha` is the actual initial remote review head, never a later unpushed local descendant, and the exact local candidate tree must still carry a matching decision-file entry before trust. The comment author must equal the current trusted authenticated GraphQL actor exactly, the top-level `pullRequest.comments` selection must carry both `createdAt` and `updatedAt`, those timestamps must be exact RFC 3339 UTC strings with byte-identical values, and the candidate's decision-file entry must still match that preregistered decision exactly. Candidate-only decision records remain inadmissible.

Before importing a package initializer or reading credentials, the launcher requires empty porcelain-v2 status including tracked, index, and untracked state; exact base `HEAD`; regular-file modes; and worktree blob equality with the exact base tree. It parses the local import graph before execution and object-binds `workflow_pilot/__init__.py`, `trusted_review_gate.py`, `reporter.py`, `review_family.py`, `review_base_checker.py`, and every transitive local import. Bytecode writes are disabled, preloaded local modules fail, and status is rechecked after import. Only then is the trusted base added to `sys.path`; the candidate and its parent/child paths are rejected. There is no environment-string external-installation bypass.

The credentialed collector queries authoritative `baseRefOid`, `mergeable`, and `headRefOid`. `headRefOid` must equal the candidate or expected remote head exactly, while `baseRefOid` must equal the coordinator's current live base tip exactly rather than the frozen merge base. The frozen merge base remains the contract base and must still be the exact `git merge-base` of the current candidate history against that live base tip; an ancestor base substitute, base rewrite, conflict, or candidate/authority shared-contract path change still fails closed. Polymorphic GitHub `Actor` selections obtain identity with `... on Node { id }`; the query is validated against GitHub's live schema. `Commit.pushedDate` is nullable metadata. It is never used to reconstruct normal ref advances or to attest complete head history.

The committed fixtures and unit tests are synthetic and version-independent: they use temporary PR identities and temporary exact Git histories rather than pinning a live implementation PR or a self-referential candidate SHA. `introduction` mode models any PR whose exact base lacks the trusted checker or trusted decision consumer. That mode always returns `merge_allowed: false` and `trusted_push_allowed: false`, cannot self-attest, and requires the external delivery coordinator to validate live evidence outside candidate authority. After merge, later PRs use `base-pinned` mode with the exact actual base that contains both trusted files.

### Independent and remote chronology

The independent pre-review is signed once before remote review. Its HMAC envelope binds repository, PR, exact base, `original_pre_review_head` A, issued/expiry times, nonce, key ID, epoch, purpose, and immutable payload. Its findings use the `LOCAL-` namespace and carry family and creation time inside that receipt. The current candidate is a separate identity and may advance to B, C, and later remediation heads without changing or re-signing A's receipt. The first remote review must occur while the original receipt is valid; later gate evaluation may occur after its expiry because it verifies immutable historical chronology rather than pretending the receipt was issued for the current head. Replaying its nonce as another pre-review still fails. The first trusted evaluation uses `new` and atomically stores the exact receipt under its repository/PR/base/A scope. Later B/C evaluations use `preserved`; they require byte equality with that trusted stored receipt and do not consume or re-sign it. A second `new` receipt, including a different nonce over the same scope, is rejected.

For a held-head pre-push decision, the authoritative current remote head A and the proposed clean local descendant B are distinct identities. GitHub must still report A at evaluation time; local Git independently proves B, B must descend from A, and a base-owned local remediation receipt for the exact A→B finding set is required before `trusted_push_allowed` can become true. For the first and second change-request rounds that exact receipt is sufficient; after a third consecutive change-request hold, the authenticated disposition must also authorize exactly the A→B transition. After push, normal exact-remote-head validation still requires GitHub's `headRefOid` to equal B.

Later GitHub review IDs and inline finding node IDs are collected and validated separately after they exist. They never replace, backdate, or re-sign local findings. `CHANGES_REQUESTED`, a nonempty finding body, inline findings, unresolved threads, incomplete pagination, or a stale review SHA cannot classify clean. A body is clean only when its first top-level line is the documented exact `### 🟢 Approval recommended` marker, or its entire body is the legacy exact `No issues found.` marker. Exact `### 🟡 Changes recommended` and `### 🔵 Needs a closer look` markers, unknown/empty bodies, nested/spoofed markers, or conflicting later top-level markers are non-clean. Authoritative Copilot authentication never uses suffix-normalized login families: each trusted review body and inline finding must bind the exact GraphQL Bot tuple above, or the exact REST tuple above only when a fixture explicitly declares REST actor shape. Case/suffix normalization remains limited to non-authoritative display and alias checks. PR, actor, local review/action/finding, remote review/finding/thread, force-push, and disposition identities still share one case-normalized uniqueness check. Selection authenticates that exact Bot actor before any review body, inline finding, or thread becomes authoritative. Human reviews, other bots, and lookalike bots are ignored entirely for trusted review-family evidence even if their bodies use a recognized marker or carry inline comments. Only review threads rooted in accepted authoritative Copilot finding IDs participate in family evidence. Human or other-bot threads and replies remain visible on GitHub but neither satisfy nor poison the exact Copilot thread coverage requirement; a matched Copilot thread must still preserve the same root finding ID, review ID, actor, and chronology. When the exact base cannot derive remote finding families, the trusted coordinator must publish immutable PR comments with the standalone prefix `workflow-review-family-classification:v1 `. Each canonical closed JSON comment binds the exact repository ID/name, PR number, base SHA, original first-reviewed head, authoritative remote review node ID and head, plus the accepted finding-to-family mapping for that review. Candidate sweeps must match that trusted family mapping exactly; any mismatch is a `family-authority-drift` hold, and downstream assertion binding continues to use the trusted classified family rather than a candidate rewrite. Every accepted finding sweep must still include at least one `affected-fixed` sibling. Unrelated PR comments, including deleted-user `author: null` comments, are ignored before actor parsing. Deleted-user `author: null` reviews are ignored before body/comment parsing, and threads with `author: null` never satisfy authoritative coverage; only prefixed authority/disposition comments require an authenticated author and fail closed on null or malformed actors. The same immutable top-level-comment contract applies to `workflow-review-family-disposition:v2`: exact trusted coordinator actor, canonical closed JSON body, strict RFC 3339 UTC `createdAt`/`updatedAt`, and byte-identical timestamps so chronology binds to the unedited comment creation time.

### Base-owned executable evidence

Candidates supply only closed assertion IDs. They cannot supply result IDs, paths, inputs, outputs, command names, or success records. The exact base registry maps every behavior row/class and family/member/disposition to one allowlisted implementation and derives inputs from exact Git/GitHub evidence. Positive/default/runtime checks execute distinct row-specific probes. Adversarial checks construct a row-specific invalid input, execute it, and require an observed rejection.

High-risk/large pre-review requirement is derived from authoritative decision data, currently the reviewed `.github/workflow-pilot-decisions.json` entry for the exact PR. Candidate `trigger.risk_boundaries` and `trigger.threshold_triggers` are evidence only: they must match that authoritative decision record exactly. Missing, duplicate, stale-head, wrong-base, or mismatched trigger decisions fail closed.

The registry and executable live in exact-base `scripts/workflow_pilot/review_assertions.py`; the checker invokes its base blob with fixed `/usr/bin/python3 -I review_assertions.py --stdin` argv. The trusted launcher materializes a closed allowlist of real production artifacts from the exact finding-origin and remediation Git trees in read-only roots: the reviewed decision record, workflow-governance docs/registry, docs check tests, event-classifier/candidate-evidence contracts, and the trusted review-family Python modules themselves. There is no standalone witness JSON: candidate-authored sidecar files cannot self-attest a member outcome. Candidate registry/program additions or edits are never executed. Each sibling member first derives its exact-base authority dependency closure from the real local import graph of the exact-base modules it executes. The closure is computed from trusted base AST/import resolution, includes package `__init__.py` files, shared modules such as `reporter.py`, and constant-path local scripts loaded dynamically by exact-base code, and fails closed on missing or ambiguous local imports. If any blob in that exact-base closure differs in either the origin or remediation tree, the member returns an explicit `authority-dependency-changed` hold requiring a fresh base and external review; it does not probe or execute candidate-owned authority code. When the closure is unchanged, the base-owned program executes only exact-base validators/consumers over the real production artifacts. The child receives a closed environment without GitHub/HMAC credentials, proxy/PYTHONPATH injection, network-capable candidate programs, or inherited startup hooks. Namespace-package parent initializers are authority too: every imported local module contributes its full parent chain (for example `scripts/__init__.py`, `scripts/docs_check_tests/__init__.py`, and `tests/__init__.py`) as exact presence/absence state even when the base tree lacks the file. Adding, removing, or mode-changing one of those parent initializers therefore produces the same hold before execution rather than silently changing future import semantics.

Each `affected-fixed` assertion executes the same base-owned member predicate against the materialized origin artifact and remediation artifact: origin may fail only because that root's real production code/data violates the predicate, and remediation may pass only because that root fixes it. `action/items`, `lifecycle/entries`, and `wire/stale-bindings` therefore parse their exact materialized production sources instead of manufacturing failure from round or SHA mismatch. Each `verified-unaffected` assertion runs only for members with a registered unaffected invariant and requires passing equivalent member-specific semantic output. One arbitrary unchanged file cannot certify unrelated members. `not-applicable` is accepted only for the explicit `resource/disabled/feature-disabled-by-contract` predicate, which must execute and establish false. Swapping a member, family, disposition, assertion ID, or reason fails before result creation. Action members execute the reviewed `review_base_checker.py` public script context with the same isolated `review_base_checker.py --input checker-input.json` argv/home/path shape production uses; package-import-only success is inadmissible. Wire producers consume a captured credentialed `live-gh-api` payload through `collect_live_evidence_bytes`, compare it with the offline packaging path, and require both modes to preserve the same shared wire schema before the current `review_family.py` consumer accepts them. Generated-output members never execute candidate-edited `candidate_evidence.py` or `event_classifier.py`: unchanged base blobs are validated with authoritative current PR/base/head inputs, and changed blobs produce the same explicit authority hold. The generated-consumer closure also follows the exact-base workflow-topology imports, so canonical `metadata_adapter_contract.py` and `summary_continuity_contract.py` changes trigger that hold before execution. One finding may legitimately reference several affected member assertions; each must independently observe its own origin failure and remediation pass. Before any sibling result is created, the checker rebinds the candidate request to the exact authoritative source collection for that round: local pre-review findings for round 1 and the immediately preceding remote review's finding set for later rounds. The finding ID must exist exactly once in that collection, match the asserted family/member registry entry, and carry the authoritative origin/head commit-tree identities for that remediation step.

For every remote review round, including each remediation head B/C, the trusted gate derives exact base-to-head status/blob coverage, executes the round's behavior assertions and the previous round's finding remediations, and issues a distinct HMAC execution receipt. It binds review round, exact head, checker blob/argv, assertion-program path/base blob/fixed argv, original A receipt digest/head, origin/head tree OIDs, every member input blob, GitHub finding IDs, program exit/status, canonical stdout and semantic-output digests, and chronological execution time. The result ID is derived from head, round, assertion, and the authoritative finding/family/member/origin/head binding; it cannot replay across rounds. The final clean remote review must be on the current exact head, and every earlier round/head receipt must still be present and valid.

The Git-derived diff is a closed status-aware record set. Added files bind head mode/blob and base absence; deleted files bind base mode/blob and head absence; modified files bind both blobs at one unchanged mode/path; renames bind old-base/new-head identities plus opposite-path absence; copies bind the retained old head blob and new destination. Unknown statuses, traversal, non-blob or surprising modes, malformed similarity, and status/field contradictions fail. Review coverage must equal those records, so deletion of `isolated_review_gate.py` is reviewable without pretending a head blob exists. The checker independently re-derives the authoritative base-owned checker blob, assertion-program blob, finding-origin tree, remediation-head tree, and every materialized subject blob from trusted Git objects before execution. Reusing one checkout for differing origin/head claims, swapping roots, leaving extra or dirty files in a materialized root, inventing a blob/tree OID, or using a symlink/path escape fails before the child assertion program runs.

The five families remain exact: action (`actions`, `items`, `targets`); generated (`owners`, `outputs`, `consumers`, `drift-checks`); lifecycle (`entries`, `preservation`, `resets`, `terminals`); resource (`enabled`, `disabled`); and wire (`producers`, `consumers`, `validators`, `replay`, `stale-bindings`). Missing, duplicate, extra, or unknown siblings fail.

### Held-head progression

Rounds one and two emit bounded sibling-family handoffs. Each third consecutive change-request round creates an independent architecture hold bound to that review's exact head. A different current head is ineligible, including an ordinary fast-forward, unless one authenticated disposition names the held round, held head, and authorized next exact head. Its actor must be disjoint from the implementer/PR author, pre-reviewer, remote reviewers, and every finding author; repository ownership does not override an overlap. The disposition must follow the held review and precede the next review, and can be consumed once only. Rounds 3 and 6 therefore hold and lift independently without inferring ref movement from commit timestamps.

The trusted gate recollects head, base, reviews, bodies, inline findings, threads, force-push events, actors, and dispositions immediately before its decision. The integrated A-finding → B-finding → C progression retains A's receipt, binds separate round/head assertions, and continues through independent round-3 and round-6 dispositions. An unrelated live-base fast-forward beyond the frozen merge base does not invalidate the candidate by itself; the gate separately records that tip, rechecks mergeability and merge-base ancestry, and refreshes only on real conflicts or candidate/authority shared-contract drift. Only matching snapshots, one replay-consumed original receipt, all chronological execution receipts, no unresolved architectural hold/thread, no authority-dependency hold, and a clean remote Copilot review on the current exact head can authorize merge or trusted push. The importable core and every offline fixture always deny those authorities.

### Metric and lifecycle integration

The review-family report names, but does not duplicate or overwrite, the existing issue #176 metrics:

| Review-family measurement | #176 reporter path |
| --- | --- |
| Review rounds | `reviews.rounds` |
| Accepted findings | `reviews.valid_findings` |
| Findings per KLOC | `reviews.valid_findings_per_kloc` |
| Time to clean review | `delivery.first_push_to_clean_review.*` |
| Coordination overhead | `efficiency.pilot_coordination_minutes` and `efficiency.metadata_maintenance_minutes` |

The frozen baseline keeps the same semantic metrics and outcomes: 34 rounds, 101 findings, 5.054 findings/KLOC, unavailable historical time-to-clean evidence, and zero captured pilot and metadata overhead. The authenticated review and manual-handoff contract intentionally updates the baseline representation to schema v2 and refreshes the derived `identities.seal` and `decisions.seal`; the semantic metric values, formulas, and availability reasons remain unchanged. Future #179 pilot events use those existing formulas and outcomes; this module adds no alternate counter or current-state field.

The contract depends on #176 and is required by #181. It conflicts with dirty or candidate-held trusted code/credentials, duplicate review agents, mutating pre-review permissions, incomplete or unsupported outcome evidence, stale base/head/blob/status evidence, spoofed clean markers, backdated receipts, inferred push histories, overlapping disposition actors, and advancement through an unresolved held head. It has no game/runtime, save, generated game-data, localization, ROM, RAM, modern debug/release, or archival impact and needs no feature flag. Rollback is a normal revert of issue #179.

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
