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
## Bounded exact-SHA implementation handoffs

Issue [#178](https://github.com/laqieer/fireemblem8-expansion/issues/178)
extends the issue #176 lifecycle and sealing seam with a bounded implementation
handoff. It is an accepted **framework capability: bounded delivery
coordination**. It changes who may own an implementation step and how the
result is admitted to trusted push; it does not change trusted-push ownership,
the final candidate/master gates, game code, or any build profile.

Validate a temporary immutable handoff document against its one allowed real
worktree:
```bash
python3 -m scripts.workflow_pilot.agent_handoff \
  --fixture build/test-artifacts/agent-handoff.json \
  --worktree /absolute/path/to/the/exact/worktree
```
The document is per-assignment input and evidence, not a live ledger or a
manually synchronized Git snapshot. The coordinator may preserve it with
delivery evidence, while Git remains authoritative for the checkout root,
origin, branch, `HEAD`, direct parent, commit message/trailer, changed paths,
numeric line count, and clean/conflict state. GitHub Actions response fixtures
remain authoritative for run status and conclusion. Only non-derivable
assignment decisions are authored: scope, acceptance/evidence names, budget
caps, owner bounds, prohibited actions, and watcher ownership. Availability,
remote observations, runtime metrics, resources, and interruption snapshots
come from one coordinator-sealed receipt, not document claims.
Duplicate JSON keys, unknown fields/enums, booleans in integer fields, and
unquantified binary line changes fail before a success-shaped result.
### Assignment and result contract

Each handoff carries:
- issue and optional eventual PR identity;
- unique handoff and implementation-owner identities with mandatory numeric
  GitHub database IDs;
- exact assigned parent SHA, branch, absolute worktree, and repository-relative
  file/directory allowlist;
- finding IDs, itemized acceptance criteria, required focused-check commands,
  and the evidence IDs that discharge each item;
- maximum changed lines, ROM bytes, RAM bytes, and protocol changes;
- maximum owner lifetime and peak RSS;
- the exact prohibited implementation-owner actions: push, remote-ref
  creation, PR creation/update, comment, review request, CI dispatch, and
  merge; and
- timestamped lifecycle, evidence, optional commit result, or evidenced
  interruption/recovery link.

The top-level coordinator receipt is asymmetrically signed by an isolated
external coordinator service. Its private key never enters the implementation
namespace; protected authority history pins only public RSA verification
material and the service isolation attestation. The signature covers the
complete document: assignment/scope, delivery graph, handoffs and successor
edges, interruptions, metrics, PR/ruleset observations, remote source events,
runs, watchers, availability, process policy, and resource receipts. Every
signature field is canonical standard-padded base64 text, and RSA verification
rejects any same-width representative greater than or equal to the public
modulus before exponentiation. The same canonical base64 rule also applies to
sealed interruption-snapshot `content_base64` bytes anywhere they are replayed
through coordinator telemetry, prior handoff history, or reporter records.

`--coordinator-installation` (or
`WORKFLOW_PILOT_COORDINATOR_INSTALLATION`) points outside the candidate
worktree to public configuration and the external bootstrap validator. Its
`installation.json` contains repository IDs, authorized coordinator users,
explicit separately typed non-user bypasses, frozen delivery/base branches,
expected ruleset identities, and public signer material only. Same-UID
HMAC secrets, candidate-authored digests, and permission modes do not establish
trust. Authority reads reject any fetched authority chain whose signer
identity/public material digest, ruleset ID, full bypass actor set, or frozen
delivery repository/branch identity drift from that verified installation
before any fetched publication signature or ruleset response can self-attest
the substitute history. In `bare-remote-config` mode, the installation must
also reject both force pushes and deletions for the protected refs before
bootstrap or trust begins. Missing external asymmetric attestation fails
closed.

`assignment_sent`, `assignment_received`, `progressing`, `committed`, and
`handed_off` are distinct, unique, strictly ordered states. A result is
accepted only when `HEAD` is one new commit whose sole direct parent is the
assigned SHA, the current branch/worktree match the assignment, the worktree
is clean and conflict-free, every changed path and budget is allowed, the
required Copilot trailer is the final unique trailer, and all named checks and
acceptance evidence passed. One root exists. A committed handoff can have one
fresh-owner `review_successor` assigned from its exact result after closure; an
interrupted handoff can have one `oom_replacement`. Successors form a causal,
nonoverlapping linear chain with at most one active successor.

Each required check names a closed contract rather than shell text. The
current `git-diff-check` contract derives exact isolated-Python argv for the
assigned parent's immutable raw-diff-checker blob, worktree, assigned parent,
and candidate. It never executes the candidate worktree copy. When the parent
predates the checker, `external-bootstrap` uses only the validator from the
external coordinator installation. Bootstrap check evidence can validate the
introducing PR but can never produce `trusted_push_eligible`; after merge,
future candidates pin the exact base blob.

Structured execution uses no shell and returns a receipt containing
check/receipt IDs, argv, checker trust mode/object, parent/candidate SHAs,
worktree identity, start/completion times, exit code, output digest, and
receipt seal. Validation re-executes that exact
allowlisted command and compares its result. Unknown contracts or a caller
`command` field fail schema validation; passed prose/evidence cannot rescue
literal `false`, a nonzero safe check, or a
stale/wrong-command/SHA/worktree receipt. The checker's exact Git argv pins
`core.whitespace=blank-at-eol,blank-at-eof,space-before-tab`, attributes/color/
quoting/diff policy, `--no-ext-diff`, `--no-textconv`, and `--text` under the
minimal Git environment. It parses raw added lines and candidate EOF bytes
itself, so root/nested/macro/negative tracked `whitespace` attributes cannot
override the policy; benign unrelated attributes remain valid. A nonempty
local `.git/info/attributes` rejects. Repository-local
`core.whitespace=-trailing-space`, diff drivers/textconv, aliases, hostile
global/system files, or ambient `GIT_EXTERNAL_DIFF` therefore cannot change
pass/fail or receipt output.

The validator mechanically rejects repeated-parent/stale results, result/HEAD
drift, missing or non-direct commits, wrong parent/worktree/branch, unrelated
paths, dirty or conflicted state, missing trailer, missing/failed/incomplete
evidence, line/ROM/RAM/protocol excess, lifetime/RSS excess, reused owners,
and prohibited remote actions by an implementation owner. The implementation
owner never pushes, opens or updates a PR, comments, creates a remote ref,
requests review, dispatches CI, or merges; those remain coordinator actions.

Every owner, coordinator, and remote actor requires an immutable numeric
GitHub ID resolved by the collector. Missing IDs, one login mapped to multiple
IDs, or one ID mapped to multiple logins reject. Each later document supplies
prior closed handoffs as a domain-separated chain covering sequence, previous
receipt, replacement edge, numeric owner, lifecycle, issue, optional PR,
candidate, closure, interruption snapshot, input, Git, and result. Exactly one
root handoff exists for an issue authority. Closed commits admit one
`review_successor`; interruptions admit one `oom_replacement`. Two roots,
overlap, wrong predecessor/result, branching successors, owner reuse across PR
binding, gaps, replay, or tampering reject.

The document cannot choose its history base. One stable protected branch at
`refs/heads/workflow-pilot/authority/issue-<n>` exists from the first no-PR
handoff. PR creation appends the exact externally signed GitHub PR API
observation: repository ID/full name, PR number, `OPEN`/unmerged state,
base/head branches and OIDs, head repository, creation/observation times,
coordinator user ID, and authority/anchor state. Compare it to independent
delivery/base values frozen in protected genesis, the root assignment
branch/result, and the pre-observation publication request. Keep
`delivery_expectation.immediate_base_oid` as frozen provenance, bind the live
current base OID separately, and require the frozen base to be an ancestor of
both the live current base and the candidate head. Never validate it against
copied observation fields. Invented, closed, merged, stale, wrong-repo,
wrong-branch, stale-current-base, non-descendant/rewritten-base, or wrong-OID
binding rejects.

Every authority commit directly parents the remotely observed head and
advances its sequence. The independently protected branch
`refs/heads/workflow-pilot/authority-anchor/issue-<n>` advances in lockstep
and records the exact authority object and sequence. The signed live GitHub
ruleset response must prove active enforcement, exact inclusion of both
branches with no excludes, update/non-fast-forward/deletion restrictions, and
exactly authorized bypass actors. A coordinator uses the 2026 REST user shape:
`actor_type: User`, identical frozen `actor_id`/`database_id`, and
`bypass_mode: always`. `RepositoryRole` numbers cannot represent users.
Non-user types require explicit separately typed frozen authorization and
default to rejection. An unrelated ruleset ID or unexpected bypass rejects.

Publication is one normal `git push --atomic` containing both direct-parent
commits. The coordinator first preflights remote atomic capability. A split
push, a stale competing coordinator plan, or a server without atomic support
rejects without moving either protected head; recovery requires a new plan
from the common observed pair.

Each fixed three-attempt read queries both remote OIDs, fetches their exact
objects without writing `FETCH_HEAD` or local refs, and queries both again.
The complete authority and anchor ancestries must reach matching genesis. One
move retries from the new pair; repeated movement fails `authority-moved`.
The dual-ref observation is checked again immediately before eligibility.
Rollback or authority A-to-B-to-A replay cannot match the independent
monotonic anchor. Historical reads also recompute the stored PR binding digest and the exact
canonical digest of each sealed history receipt. Every protected handoff
commit also carries one bounded private `history_carrier` containing the
exact signed handoff document, the canonical validation result, and the
selected handoff ID; readback re-verifies the original coordinator, PR, and
authority signatures, replays `make_history_receipt()` from that carrier,
and requires the signed publication attestation's `history_carrier_digest`
and `history_receipt_digest` to match the full carried carrier and receipt
bytes rather than a projected event subset. Returned authority snapshots keep
`history_carrier: null` so later handoff documents do not recursively embed
older carriers. Historical reads also require the signed publication
attestation's `binding_expectation` to match the stored frozen delivery plus
stored live current-base fields, and re-check that the frozen base remains an
ancestor of the stored live base OID. The stored `pr_binding.head_oid`, its
digest, and `publication_attestation.binding_expectation.head_oid` must all
match the immediately prior sealed handoff candidate carried by the current
`handoff_sequence`/`head_seal`. Derive the stored observation's
`authority_object_id` from the bind commit's canonical `previous_object_id`.
Derive the stored observation's `anchor_object_id` from the exact prior
canonical anchor record, never from copied observation fields. Swapped signed
observations/publications, stale signed observation replays, out-of-band
branch advances, copied anchors/seals/sequences, and rewritten bases fail
even when each record is individually valid.

Bootstrap, advance, and PR binding plans are deterministic and read-only:
```bash
python3 -m scripts.workflow_pilot.agent_handoff \
  --authority-operation bootstrap \
  --worktree . --repository laqieer/fireemblem8-expansion \
  --issue 178 --publication-attestation <signed-json> \
  --coordinator-installation <external-path>

python3 -m scripts.workflow_pilot.agent_handoff \
  --authority-operation advance \
  --fixture <handoff-document-json> --handoff-id <closed-handoff-id> \
  --worktree . --repository laqieer/fireemblem8-expansion \
  --expected-object-id <remote-head> --expected-sequence <n> \
  --publication-attestation <signed-json> \
  --coordinator-installation <external-path>

python3 -m scripts.workflow_pilot.agent_handoff \
  --authority-operation bind \
  --worktree . --repository laqieer/fireemblem8-expansion \
  --issue 178 --pull-request <actual-pr> \
  --expected-object-id <remote-head> --expected-sequence <n> \
  --pull-request-observation <signed-github-response-json> \
  --publication-attestation <signed-json> \
  --coordinator-installation <external-path>
```
The output names normalized authority and anchor records, both expected remote
objects, the single-use operation nonce, authorized numeric actors, and one
atomic push template. It never emits separate or force-capable commands,
creates an object, updates a ref, or pushes. Each advance/bind requires current
objects, sequence, externally signed publication inputs, and a fresh atomic
preflight.
Normal clones fetch the authority explicitly during validation. Implementation
owners remain prohibited from every bootstrap/advance/push action.

This remote authority follows the issue #176 admission/lifecycle pattern:
owner `delivery-coordinator`; executable consumer
`scripts.workflow_pilot.agent_handoff`; unique decision
`canonical-closed-handoff-head`; consistency check
`test_agent_handoff`; bounded authority/anchor commits per close; deletion only
after issues #178/#181 and every consumer retires the ownership invariant.
The branches retain minimal canonical ancestry and do not duplicate the
mutable delivery ledger.
### Typed delivery dependencies

The handoff's `delivery_graph` separates authored relationship semantics from
normalized task and watcher facts:
- `code_contract` is the only parent/child implementation relationship. Its
  required edge is `child-implement -> parent-merge`.
- `delivery_gate` links the parent's own completion, closure, and remote
  completion to `parent-post-merge-build`.
- task phases and states are normalized Git/GitHub/orchestration facts.
  Watchers are separate records and cannot appear on either side of a todo
  dependency.

The evaluator derives pending-task readiness only from typed task edges. When
the parent merge is `done`, the target tree contains the required contract, so
child implementation is ready even while the healthy exact-master Build is
`in_progress` and parent remote completion is pending. When parent merge is
pending, the child is blocked by `parent-merge`. A graph that instead records
`child-implement -> parent-remote` rejects with both the missing required
merge edge and wrong code-contract edge, and reports the exact edge that must
replace it.

Every source handoff must have exactly one relationship and one implementation
task with matching handoff ID, issue, PR, candidate SHA, and lifecycle-derived
status. Missing/duplicate/relabelled relationships or tasks reject. Dependency
validation applies to every task status, not only pending tasks: `done` or
`in_progress` cannot have a non-done prerequisite, and blocked tasks carry a
closed dependency, workflow-failure, or owner-interruption reason which must
match their role and handoff.

An in-progress master watcher is reported as `orthogonal_to_todos: true` and
does not alter the ready set. A dependency that names its watcher ID rejects.
If the authoritative run later completes with failure, the post-merge Build
task becomes blocked and `fix_forward_revert` must be in progress. That
terminal transition does not change the fact that dependency-ready work
should have proceeded during the earlier healthy pending interval. Regardless
of child readiness, the parent completion, closure, and remote-completion
tasks retain direct `delivery_gate` edges to the parent's post-merge Build.
That Build task names an exact target SHA and binds to one
`github-actions-api` run with the same SHA/status/conclusion plus one direct
watcher. Active and failed runs keep parent delivery eligibility false and
prevent those parent tasks from becoming done. A successful terminal run is
the only Build state that opens that parent gate.
### Coordinator, watcher, and recovery contract

One document admits exactly one coordinator. Each included exact run has one
direct-shell watcher owned by that coordinator, bound to both run ID and head
SHA. Duplicate watchers reject. A timeout or watcher process error is not
treated as a workflow conclusion: the validator reconciles it with one
authoritative `github-actions-api` observation. An authoritative successful
run remains successful after a watcher timeout; an authoritative failure
remains failed even when the watcher itself errored; an active run remains
incomplete. The fixture path performs no GitHub mutation.

Remote-action authority never comes from a caller-authored list. The
coordinator collector seals the repository ID, actor resolutions, interval,
and exact timeline, Actions-run, ref, and audit-log source responses. Its
normalized action list must equal the complete union of source events, so an
omitted push, comment, review request, or CI dispatch rejects. If any source
is unavailable or incomplete, each implementation process needs a sealed
credentialless, network-denied launcher interval covering its entire
lifecycle. Every transport Git subprocess that reads or preflights protected
authority (`ls-remote`, object fetches, and atomic push dry-runs) runs inside
the same closed Git environment with an explicit wall-clock timeout; timeout
kills the whole process group and returns a typed handoff error instead of
retrying unboundedly or orphaning transport helpers. The external service owns
a monotonic consume sequence/anchor and
spent-nonce store. One atomic operation terminates that process, collects every
source through the consume instant, decides, marks the nonce spent, and returns
the signed decision. The same nonce's second call rejects before authority
publication. Local validation cannot make a preissued receipt freshly
eligible; the live receipt must satisfy the same narrow freshness window as
live PR/publication observations, and receipt repository IDs/full names must
match the installation manifest and frozen delivery expectation. Any after-sign
event or document/run/watcher mutation invalidates it. Before trusted-push
eligibility is granted, the validator also rechecks the protected authority
refs and reconciles the bound delivery/base branch refs against the live
remote so a post-snapshot push or base rewrite cannot race past eligibility.

An interrupted owner has the exact `assignment_sent` ->
`assignment_received` -> `progressing` -> `interrupted` sequence. SIGKILL/OOM
recovery requires signal 9, nonempty kernel evidence, named interrupted checks
whose evidence is explicitly incomplete, and content-bearing protected
authority evidence containing each preserved file's bytes, path, Git mode,
SHA-256, exact status, and the original scope, criteria, checks, and budgets.
A later completed replacement may validate after the old worktree is clean
only by restoring every file byte/mode or recording an externally attested
explicit resolution mapping. Committing an unrelated file does not recover
the interrupted work. Exactly one different owner receives the same context.
Tests do not exhaust memory, signal a process, or kill any host process.
The replacement may validly report only `assignment_sent`, then optionally
`assignment_received` and `progressing`; these exact prefixes produce
`in_progress` without an incomplete-lifecycle error and can never become
trusted-push or delivery eligible. Its `assignment_sent` timestamp must be
strictly later than the interruption. Equal, predated, missing, or multiple
replacement assignments reject.

Availability is another coordinator-sealed observation, not a literal mode
claim. It must be issued near validation time, be observed before assignment,
cover the complete unattended interval through the lifecycle cutoff, and show
both autostop and stop-on-disconnect disabled. Future, stale, late, expired,
ineffective, or unsealed observations reject.
### Reporter extension and compatibility

The unchanged frozen issue #176 baseline remains strict fixture/report schema
version 1 and rejects a handoff field. Version 2 is an additive operational
fixture schema that requires normalized `implementation_handoffs` records from
the validator. The public handoff document format itself stays at protocol
version 8 / `schema_version: 2`: the shipped JSON Schema now closes
every live protocol section — `history_authority`, `prior_handoffs`,
`delivery_graph`, handoff arrays/objects, top-level run/watcher receipts, and
the full coordinator receipt surface — with standards-compliant field
definitions that match the runtime parser's nested objects, enums, nullability,
numeric bounds, and `additionalProperties: false` behavior. Every schema
timestamp now follows the same exact runtime grammar: RFC 3339 UTC with a
terminal `Z` and at most six fractional digits. Offset forms such as `+00:00`
and over-precise fractions remain invalid because the runtime does not accept
them.

Each normalized record contains the complete source document, source handoff
identities, and matching input/Git/check/coordinator/result seals. The
reporter separates live eligibility from historical verification. The external
service finalizes once and signs the complete canonical source-plus-result
payload, including outcomes, reporter summary, RSS, and all verified metrics.
The unkeyed result hash is integrity-only. Offline or relocated historical
verification checks the finalize signature, document/result seals, structural
row facts, and bundle-global semantics without consulting the old worktree.
When a caller supplies the original repository root for live revalidation, the
same verifier additionally proves current authority/anchor ancestry plus the
live Git-derived handoff facts. Neither mode restores current trusted-push
eligibility from a stale receipt, and structurally stale rows cannot self-mark
`accepted` or claim `trusted_push_eligible`. A hand-authored aggregate still
rejects.

Runtime lifetime telemetry remains owner-supplied evidence, not a rounding
hint. The validator rejects any non-whole-second owner lifetime delta before
integer conversion, even when the timestamps use one-to-six RFC 3339
fractional digits. Exact whole-second deltas remain valid.

The reporter preserves handoff-local outcomes from sealed results, but its
normalized aggregation treats an otherwise accepted handoff inside a
trusted-ineligible bundle as `bundle_rejected` instead of counting it as
accepted. The aggregate output therefore reports
accepted/bundle-rejected/rejected/interrupted/in-progress counts plus the
union of rejection codes, stale responses, maximum owner lifetime/RSS,
coordination turns, and recovery minutes exclusively from verified results.
Bundle/global rejection codes derived from sealed runs, watchers, and remote
coverage stay present even when the affected handoff already has its own local
rejection outcome, so aggregate failure reporting never loses duplicate
watcher, watcher-owner-mismatch, remote-coverage, or authoritative-run defects
and never double-counts the same handoff as both rejected and
bundle-rejected.
`verify_reporter_record()` re-derives the signed per-handoff rows, summary,
and bundle-global rejection semantics from the sealed document/result payload
instead of trusting mutable row or summary fields. Line usage remains Git-derived. Protocol changes
come from the parsed, monotonically versioned
`scripts/workflow_pilot/agent_handoff.schema.json`. ROM/RAM derives zero only
for exact proven host-only path prefixes. Linker scripts, Makefiles, assets,
configuration, fonts, text, generated data, and every unclassified tracked
input require a closed build/map/resource receipt whose dependency inputs
exactly equal the conservative Git-derived set. RSS, lifetime, turns, and
recovery come from coordinator runtime telemetry. Claim-only tampering fails
its seal. This adds no mutable decision record and does not alter version 1
expected paths, values, or seals.
Run a version 2 operational fixture without the version 1-only `--expected`
argument:
```bash
python3 -m scripts.workflow_pilot.reporter \
  --repository-root . \
  --fixture build/test-artifacts/workflow-pilot-operational.json \
  --decisions .github/workflow-pilot-decisions.json
```
Conversely, omitting `--expected` from a version 1 baseline run fails, so the
new mode cannot bypass the frozen baseline comparison or executable lifecycle
proof.

Dependency: issue #176. Dependent: issue #181. Conflicts: none. Modern debug,
modern release, save, generated data, localization, ROM, RAM, gameplay, and
archival impact are all none; no feature flag or migration applies. Rollback
is a normal revert of issue #178 while the issue #176 baseline and existing
trusted-push/centralized-watcher rules remain in force.
