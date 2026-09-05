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

### Safe pull-request metadata ordering

**Architecture disposition after the third consecutive finding round:
accepted in place.** One standards-based typed HTTP header/media policy and
one exact job-status/timing state model cover the findings without widening
issue #199 or introducing another subsystem; no split is required.

Finalize the stable PR title/body before pushing the candidate, push the
candidate, then freeze the title/body while its exact-head full Build is queued
or in progress. Put every evolving command, result, SHA, run, review, preflight,
budget, and completion update in the canonical evidence comment instead.

Use the isolated metadata helper for title/body changes after a candidate
exists. Every invocation binds the repository, PR number, current head SHA,
current base SHA, active Build workflow, complete exact-head run pages, and
complete job pages. Every response includes an exact HTTP status and canonical
Link-header proof; redirects, duplicate/malformed/looping relations, page/body
contradictions, stale identity, incomplete pagination, unknown or mixed run
shapes, or a noncanonical workflow fail closed:

HTTP response headers accept only visible ASCII plus the explicitly parsed
SP separator. Bare CR/LF, NUL, tab, vertical tab, form feed, DEL, non-ASCII,
folded/obs-fold lines, mixed line endings, and controls embedded in
Content-Type, Link, Location, or any other value fail before interpretation.
CRLF and LF header blocks are accepted only when internally consistent.
Singleton headers, including Content-Type and security/identity fields, cannot
repeat. The explicitly RFC-combinable `Vary`, `Cache-Control`, and `Link`
fields may repeat and are SP/comma-normalized before typed interpretation;
unsupported repeated fields fail closed. Multiple Link fields feed the same
canonical relation parser, so duplicate relations and loops still fail.
Content-Type requires case-insensitive exact `application/json`, followed only
by syntactically valid, uniquely named optional parameters; prefix forms such
as `application/jsonp` are rejected. Parameter names are tokens and split from
their values at the first unquoted equals. Token values are accepted; the
closed quoted subset may contain literal equals and semicolons but no
backslash/escape or embedded quote. Extra unquoted equals, empty names/values,
duplicate names, trailing junk, unterminated quotes, and noncanonical
whitespace are rejected.

The current PR's fully validated base-repository payload binds both
`owner/repository` and its positive numeric repository ID. Pagination accepts
only GitHub's canonical `/repos/<owner>/<repository>/...` and
`/repositories/<numeric-id>/...` Link path families when they resolve to that
same repository, endpoint suffix, query, and page. Other numeric IDs,
owner/repository drift, percent-encoded or ambiguous paths, and cross-form
loops fail closed. The workflow payload must also bind its ID, node, name,
path, active state, timestamps, API URL, HTML URL, and badge URL to that exact
repository.

Every job is bound to the requested run and attempt before its result can
classify a run. Job IDs are unique, and each job's run ID/URL, optional exposed
attempt/event, head SHA, head branch, workflow name, node, job API URL, HTML
URL, check-run URL, runner identity, and completion state must agree with the
parent exact-candidate run and repository. Mixed run/attempt/repository/head
pages and identityless jobs fail closed.

Completed runs are refreshed through their exact run API identity before job
classification and retain the exact full/metadata job-set requirement.
Queued and in-progress runs may expose no jobs or only a partial graph.
After repository/workflow/event/head/base/run/attempt validation, an active run
is harmless only when a successful `metadata-classifier` and the observed
known-job subset prove metadata-only mode. An active `event-classifier`, an
unknown/partial graph, zero jobs, or any other unproven active shape is a
blocking active candidate and makes the default edit defer rather than error
or mutate. Materialization changes between the two pre-PATCH snapshots also
defer.

Each exact-head run has one typed PR-binding state after its repository,
workflow, event, and head identity is validated: `explicit-same`,
`explicit-other`, or `unbound`. Missing, null, or empty `pull_requests` is
unbound. An active unbound run blocks because GitHub may not have materialized
its PR binding yet. A terminal unbound run cannot authorize full-Build or
metadata-continuity evidence. One explicit binding to another PR/base is
ignored only after its complete run and job authority validates. Multiple
bindings, a binding head that contradicts the run head, or malformed binding
content fails closed.

Run and job times use a manual canonical
`YYYY-MM-DDTHH:MM:SSZ` GitHub-RFC3339 parser; timezone offsets, fractional or
24:00 times, malformed dates, and missing required fields are rejected.
Runner-backed completed jobs require
`run-created <= job-created <= job-started <= job-completed <= run-updated`
after the exact terminal refresh.
Queued jobs have null start/completion, and in-progress jobs have a valid start
but null completion. An active run's `updated_at` is not a live job-completion
upper bound; active jobs retain strict intrinsic chronology, while the upper
bound is applied only after terminal run refresh. Canonical unassigned skipped jobs preserve GitHub's live
timestamp quirk: created and started must be equal, while completion may equal
them or precede them by exactly one second. Positive skipped duration,
including one or 28 seconds, is rejected. These unassigned skipped jobs never
satisfy a runner-backed success requirement. No wider chronology exception is
accepted.

```bash
repo=laqieer/fireemblem8-expansion
pr=123
head_sha="$(gh api "repos/$repo/pulls/$pr" --jq .head.sha)"
base_sha="$(gh api "repos/$repo/pulls/$pr" --jq .base.sha)"

/usr/bin/python3 -I scripts/workflow_pilot/isolated_launcher.py pr-metadata edit \
  --repository "$repo" --pr "$pr" \
  --head-sha "$head_sha" --base-sha "$base_sha" \
  --body-file /path/to/stable-pr-body.md
```

An initially active same-head/same-base full or unproven Build takes one
complete run/job snapshot and immediately returns `deferred` without changing
metadata. A mutation-eligible default edit takes two complete exact-candidate
run/job snapshots. It returns `deferred` when the second snapshot differs from
the first or no longer proves the same successful full Build.
Its structured guidance points to the canonical comment route:

```bash
/usr/bin/python3 -I scripts/workflow_pilot/isolated_launcher.py \
  pr-metadata evidence-comment \
  --repository "$repo" --pr "$pr" \
  --head-sha "$head_sha" --base-sha "$base_sha" \
  --comment-file /path/to/canonical-evidence.md
```

An urgent frozen-contract correction may use `--essential-reason` with
non-whitespace text. The helper revalidates current head/base immediately
before the title/body PATCH, refreshes complete run authority, never cancels
the still-valid full Build, and returns the exact reconciliation command. A
successful edit requires the PATCH response itself to attest the exact
repository, PR, head, base, requested title, and requested body; a stale
read-after-write GET is not accepted as mutation evidence. Record the essential
reason in the canonical evidence comment. No tracked or local pending-state
ledger is created: GitHub's run number, attempt, event, workflow, PR, head,
base, mode, status, conclusion, and job identities derive whether
reconciliation remains pending. After a successful PATCH, the helper creates
one dedicated append-only owner-authored issue comment containing an immutable
edit receipt with a versioned schema and unique nonce. It binds repository
ID/name, PR, head/base, the complete post-PATCH title/body digest, SHA-256
digests of the exact requested fields, the authoritative PATCH-response PR
`updated_at`, workflow ID/path, and the highest completely observed pre-PATCH
run ID/number/creation-time watermark. The command returns only the validated
receipt comment ID and URL; the GitHub comment is authoritative and no mutable
local ledger or caller-authored receipt file exists.

```json
{
  "base_sha": "<40-lowercase-hex>",
  "edit_updated_at": "YYYY-MM-DDTHH:MM:SSZ",
  "head_sha": "<40-lowercase-hex>",
  "metadata_sha256": "<sha256>",
  "nonce": "<64-lowercase-hex>",
  "pr_number": 123,
  "repository": "owner/name",
  "repository_id": 123,
  "requested_fields": {"body": "<sha256>"},
  "schema_version": 1,
  "watermark": {
    "created_at": "YYYY-MM-DDTHH:MM:SSZ",
    "run_id": 123,
    "run_number": 45
  },
  "workflow": {"id": 678, "path": ".github/workflows/build.yml"}
}
```

Every issue comment receives exact comment ID/body and API/HTML
repository/PR/issue validation. Ordinary unmarked contributor, bot, and
deleted-author comments remain permitted and cannot disable the helper. The
one marked canonical evidence comment must be the repository owner's
owner-associated `User` comment with the exact owner numeric user ID from the
validated PR repository payload and exactly one standalone marker.
Marked missing/deleted authors, bots, non-owner associations, cross-repository
IDs, duplicate or embedded markers, and a PATCH response that does not attest
the same author/comment plus the requested body are rejected.
Structured results reserve `run_id` exclusively for an Actions workflow run
and `comment_id` exclusively for the canonical issue comment. Both fields are
optional and serialized in every result, but they are mutually exclusive;
`comment-updated` requires only `comment_id`.
The isolated CLI writes exactly one canonical JSON line for a decision: exit
status `0` means successful, no-op, or already complete; `3` means deferred or
refused; and `2` reports invalid arguments, unreadable files, or malformed API
authority without decision JSON on stdout.
The receipt is not a secret, signature, or authentication token.
Receipt-comment-bound `pr-metadata reconcile` refetches the comment, parses its
closed canonical schema,
requires exact repository/PR/comment URLs and repository-owner numeric
ID/login/`User`/`OWNER` authority, rejects edited or duplicate-nonce receipts,
requires the selected comment to be the unique latest valid receipt for the
PR, revalidates complete current title/body plus requested-field digests, and
requires the watermark run to remain present with the same identity. A later
direct metadata edit, including edit-and-revert to the same content, invalidates
the receipt through the complete metadata digest or post-receipt run
chronology. Receipt comments are never edited or deleted by reconciliation and
their `issue_comment` creation does not trigger Build CI.
The separately marked canonical evidence comment may continue to be updated;
it never becomes a receipt and does not supersede the latest receipt marker.

After the newest exact full Build succeeds, run:

```bash
/usr/bin/python3 -I scripts/workflow_pilot/isolated_launcher.py \
  pr-metadata reconcile \
  --repository "$repo" --pr "$pr" \
  --head-sha "$head_sha" --base-sha "$base_sha" \
  --receipt-comment-id <receipt-comment-id>
```

Reconciliation revalidates head/base and all exact run authority twice, then
reruns only the newest `completed`/`failure` lightweight metadata continuity
run that is strictly newer than both the successful full Build and the
receipt's pre-PATCH run-number watermark, and whose creation time is not
earlier than the authoritative edit timestamp and strictly earlier than the
receipt comment creation timestamp. If the run is not visible yet, or if
same-second ordering against the receipt comment cannot be proven,
reconciliation defers; an older successful metadata run never completes a
newer edit. A rerun attempt of a watermarked run is not a new edit event. The
eligible run's identity/router/classifier and adapter jobs must be canonical
successes, expensive jobs and the publisher must be canonical skips, and
summary must be the canonical failure. Cancelled, timed-out, action-required,
startup-failure, skipped, neutral, stale, active, unknown, unbound, or malformed
runs are never rerun. It dispatches no full Build, edits or deletes no receipt
comment, and exposes no cancellation operation. An already-active eligible
metadata run is deferred, and an already-successful eligible one is complete.

Every successful title/body update returns this reconciliation command. The
second run snapshot closes the deterministic pre-PATCH run-state race; only an
external same-SHA full rerun that starts after that final authority query is an
irreducible API race. The authoritative PATCH response detects identity or
result drift at mutation time, and the returned reconciliation command handles
the remaining external race without weakening continuity.
Only the existing coordinator may cancel full runs whose old head SHA was
actually superseded; a title/body change never makes a same-SHA full run stale.
The helper therefore never cancels or replaces a same-SHA full Build.

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
