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

Run the stdlib-only reporter from the source repository root:

```bash
python3 -m scripts.workflow_pilot.reporter \
  --repository-root . \
  --fixture scripts/workflow_pilot/tests/fixtures/baseline.json \
  --decisions .github/workflow-pilot-decisions.json \
  --expected scripts/workflow_pilot/tests/fixtures/baseline_expected.json \
  --cohort-decisions
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
`--cohort-decisions` explicitly projects the live decision collection onto the
validated historical PR cohort. The existing isolated `baseline` and lifecycle
baseline modes do this automatically; ordinary `build_report` remains strict.
Adding a future pilot/fixture decision neither adds it to the frozen cohort nor
changes baseline v1. Unknown fields, duplicate records and malformed decisions
still reject.

## Adaptive review-first candidate gates

[Issue #181](https://github.com/laqieer/fireemblem8-expansion/issues/181) is an
accepted framework capability that changes **when**, not **what**, is tested.
Dependencies #176/#177/#178/#179 supply the existing decision/metric record,
event and canonical-evidence identity, coordinator/watchers, and independent
review/family/hold authority. #180/#206 remain parallel; #196 extraction is not
a prerequisite. No gameplay, flag, save/config, locale, generated game-data,
ROM/RAM allocation or archival behavior changes.

### Decision and workflow

Keep each decision in `.github/workflow-pilot-decisions.json`, not another
policy file. `protocol`, `replay`, `transport`, `security`, `save`, `lifecycle`,
`abi` and `migration` select review-first. More than 2,000 changed lines is
also a review-first signal. Small low-risk records may select concurrent.
Overrides use #176's immutable pre-review introduction validation; named high
risk always wins. A timing override does not waive #179's independent local
review. A missing/unknown record, unavailable override provenance, or existing
`pilot.disposition: paused` uses the broader concurrent/full workflow and
retains a visible reason.

The trusted integration-base classifier reads the selected candidate's
committed record as data through GitHub, checks its actual Git blob bytes,
and binds the PR/head/merge base. It never imports candidate programs.
The diagnostic classifier can select this path with `--adaptive --repository
owner/repo`; the Build workflow enables it only when its trusted base contains
the implementation. A base predating deployment retains the full bootstrap.

There is still **one Build workflow and eight jobs**. A review-first PR event
runs identity/router/classifier plus tiny `host-tests`/`build` continuity
checks; `extended-host-tests` and `legacy` are platform-skipped. Its classifier
is `review-first-classifier`, not full evidence. The canonical `summary`
deliberately fails with a waiting-for-full explanation until the full run
replaces it: green preflight is never merge permission. Concurrent candidates
retain the full graph immediately. Exact-head security and Copilot are
requested/observed concurrently by the existing delivery coordinator, not a
new reviewer service.

After accepted clean review and security, the coordinator uses one input-free
`workflow_dispatch` on the actual candidate branch. All eight jobs run.
Dispatching early manually remains possible for a repository owner, but that
run is inadmissible; no impossible prevention guarantee is claimed.
The normal modern job still builds once and publishes its existing patch only
for the guarded automatic master push. Dispatches and PRs never publish.
Every resulting master revision still runs the complete graph and remote
completion remains mandatory.

### Coordinator API and evidence

`scripts.workflow_pilot.adaptive_gate` reuses #178's locked coordinator state
with an optional bounded `candidates` list. It does not launch agents or
watchers. The existing assignment/handoff and watcher schemas remain valid.

1. `fetch_candidate`, `fetch_decision` and `frozen_base` collect existing PR,
   decision and merge-base observations. In a short `locked_state` transaction,
   `begin_candidate` records the exact head/base/ref/decision and supersedes
   older candidates for that PR only.
2. Supply the coordinator's actual #179 `ReviewSession` and complete `Triage`
   records. `assess_observed` refreshes the real GitHub review facts, exact
   check runs, and Build runs, and calls #179's shared `review_state` predicate.
   Its `review_tools` uses the actual unique Git merge-base check. Prior
   accepted findings additionally need the existing family handoff inputs:
   `(request, members, observations, tool_revision)`.
3. Security requires the complete exact-head check-run set: `CodeQL` from
   GitHub Advanced Security app 57789 and `GitGuardian Security Checks` from
   app 46505, with their expected slugs, terminal success and complete
   pagination. A resolved false positive does not turn a failed check green:
   observe the successful exact check. A coordinator-accepted valid security
   or review finding permanently abandons that head.
4. The existing exact local handoff must be accepted and closed. The
   coordinator supplies `criteria_ready` only from the existing objective and
   manual audiovisual completion gates. Its default is false. No human review
   gate or audiovisual exception is introduced.
5. `dispatch_full(client, state_path, pr, assess_callback)` calls the callback
   to obtain `(record, assessment, observed_runs)`, refetches PR identity, and
   **persists the reservation before POST**. It sends only `{"ref": head_ref}`.
   Unknown delivery cannot be retried into a duplicate; it remains pending
   reconciliation. Bind the unique actual run after the recorded watermark,
   never a guessed run ID. Run ID and attempt are retained; a later mismatched
   attempt or run is not silently promoted. Preserve native fractional
   reservation times, but compare GitHub's second-granular creation time at
   provider precision alongside the strict run-number watermark and identity.
   A same-head/branch active run with unknown binding/mode remains a visible
   hold before both dispatch and merge, even beside an earlier full success.
6. When a run becomes visible, use the existing #178 `reserve_watcher` /
   `finish_watcher` / `reconcile_run` interfaces and exactly one attached
   bounded shell watcher per run/attempt. A reasoning agent never waits.
7. `cancel_abandoned` reloads the persisted state before any remote action,
   verifies actual repository/run/attempt/head/workflow identity, and refuses
   cancellation without prior abandonment or for unrelated work.
8. Render `evidence_comment(assessment, preserved_text)` and update the single
   existing owner-authored canonical comment through
   `pr_metadata.update_evidence_comment`. Preserve other local/runtime/manual
   evidence; do not edit the stable PR body or create another evidence ledger.

The assessment records decision identity/mode/reason, frozen and live bases,
state/missing evidence, review IDs/rounds/current findings/unresolved count,
actual security checks, and preflight/full run IDs and attempts.
`merge_eligible` requires the one bound complete full success plus all current
review/security/local/objective gates. Cancelled, abandoned, duplicate,
wrong-head/base/decision or stale-attempt evidence cannot qualify.

Live base tips and #179's **frozen unique merge base** are distinct. Unrelated
master movement does not itself supersede or cancel a candidate. A changed
head, base ref or unique merge base does. The workflow's candidate-binding step
and #177's existing metadata continuity lookback preserve this distinction for
both PR and input-free dispatch runs.
Automatic exact-head security checks can legitimately start before the
coordinator first registers a new head. Do not rewrite their timestamps.
A same-head base rebind instead requires the current eligible clean review
and security to follow that binding, because the SHA alone cannot distinguish
its prior base. Historical reviews stay in complete triage and unresolved/
abandonment checks; their old timestamps do not invalidate a newer clean review.

These APIs consume trusted coordinator observations, not authenticated JSON
receipts or arbitrary Python callers. They do not offer a broker, signer,
capability service, source ledger or reviewer fleet. Missing evidence is a
visible hold, not a pass label.

### Pilot measurement and rollback

Use #176's existing Build minutes, review rounds/time-to-clean, safety events
and coordination overhead, plus #178's captured handoff observations.
`pause_for_safety` updates the existing decision's paused disposition for
observed `security_finding`, `escaped_defect` or `broken_master` events.
Persist that decision normally; new candidates take the broader workflow.
Pause and ordinary revert change timing only, never final gates.

Disposable fixtures use `pilot.included: false` and `disposition: excluded`;
they are not merged pilot samples. Three weeks or **20 actual post-deployment
merged pilot PRs**, and measured efficiency improvement/non-inferiority, remain
future Discussion #174 promotion criteria. Unit tests, synthetic history and
the introducing PR cannot fabricate that checkpoint.

The indexed [TC-WORKFLOW-REVIEW-FIRST-001](test-cases/workflow-governance.md#tc-workflow-review-first-001-gate-expensive-builds-after-accepted-review)
contains local controls and the coordinator-only disposable-PR exercise,
including the introducing-feature bootstrap boundary.

## Sibling-family review convergence

[Issue #179](https://github.com/laqieer/fireemblem8-expansion/issues/179) adds a
bounded local review and executable sibling sweep, not another delivery or
hostile-Python platform. It depends on #176's risk/metric contracts, the
existing Git/GitHub/task/test interfaces, and #216's locked schema-test
environment. #181 is a dependent; #178 is independent. #204 is not a
prerequisite. The original
[tester case](test-cases/workflow-governance.md#tc-workflow-review-family-001-expand-valid-findings-across-complete-sibling-families)
remains the acceptance contract.

### Trust and ownership

The coordinator freezes repository/PR/base/head, accepted cases, findings and
risk classification. A high-risk or large candidate gets one fresh bounded
read-only review before its first remote review. The existing task runtime
owns task identity, owner, role, completion and observed tool actions. The
reviewer's response is findings data, not an authentication token. Keep its
owner distinct from implementation and coordination.
Share one `ReviewOwnership` index across the coordinator's sessions; it blocks
another active reviewer for the same repository/PR or the same candidate head,
regardless of scope, before another task can start.

`ReviewSession.begin` forwards the closed `code-review` role, exact head/scope,
allowed read/report actions and duration/file/finding bounds to the
coordinator's existing task adapter. It returns immediately. After the
existing task-completion notification, `finish` reads that task's actual
result. There is no polling, new agent backend or JSON-selected runtime.
The adapter's result exposes the actual `task`, `owner`, `role`, `head`,
`subjects`, `completed`, `read_only`, `actions`, `files`, `findings`, `started_at` and
`completed_at`; these are runtime metadata, not fields copied from the
reviewer's prose. Both `completed` and `read_only` must be Boolean `True`
for report admission; truthy strings/numbers or missing fields cannot admit
a completed review. Requested
duration (1–3,600 seconds) and files (1–200, default 200) are strict Python
integers. The lease retains its requested file bound; returned `files` must
be an integer from zero through that bound, never a Boolean, float or string.
Scope/actions must be collections of strings and findings a list/tuple of
typed records. Malformed or over-budget reports do not grant review evidence
or silently release ownership.
Every admitted completed report must include actual runtime observations for
both `read-candidate` and `emit-report`; `read-evidence` is optional.
An empty or partial subset of the allowed actions is not sufficient.
Bound read tools through the coordinator's `readers` map.
`read_action` rejects mutation/arbitrary-command operations before dispatch.
Ordinary test execution is a separate coordinator-owned test role, not a
second overlapping reviewer.

Lease retirement is distinct from report admission. `finish` reads actual
task status even after the deadline. A still-running or unknown task retains
ownership; a verified late terminal result closes the lease with
`outcome: timed-out`, releases ownership and rejects the report. The deadline
is checked after reading and again before admission, so completion observed
across the deadline cannot supply review evidence.

The explicit `session.abort(runtime)` operation uses the coordinator's
optional existing `runtime.stop(task)` capability when the task is still
running. It verifies the exact task/owner/role/head/scope, Boolean terminal
`completed` state and terminal timestamp chronology. A stop request or
acknowledgment alone never closes the lease: the operation reads the actual
task again. Stop/read failure, missing stop capability, unknown status or
malformed terminal metadata retains ownership until real terminal evidence
is available. An already completed failed report can be abandoned without
being accepted or stopping it again.

Both paths use the same retirement helper. `finished` means the lease is
closed, while `outcome` distinguishes an admitted `completed` review from
`timed-out` or `aborted` work. Timeout/abort retains no report or local finding
evidence and cannot satisfy independent pre-review admission. Completion
releases the existing index for a fresh session; no polling loop, new task
backend or cleanup service is added.

The coordinator and reviewed validator/test tools are trusted. Candidate
requests are data and cannot choose Python programs, imports, commands,
expected members, pass records or trusted-status flags. Test children receive
captured source and a minimal environment without supplied GitHub/SSH
credentials or coordinator configuration. These are operational controls,
**not OS isolation from malicious same-UID code**. No broker, HMAC, receipt
store, capsule, import-capability proof or protected installation is required.

### Public request and execution API

The independently checked
[`review_family.schema.json`](../scripts/workflow_pilot/review_family.schema.json)
defines the closed wire shape. `validate_request` also checks identity joins,
duplicates, bounds and subject membership. The CLI accepts only a non-symlink
regular request file, reads at most 1 MiB plus one EOF byte from its checked
descriptor, and rejects overage before JSON parsing. Malformed JSON, invalid
record types and nonregular inputs produce bounded nonzero diagnostics.
These file/runtime limits do not add fields to the version-1 JSON schema.
A request contains:

```json
{
  "schema_version": 1,
  "repository": "owner/repo",
  "pull_request": 1,
  "base_sha": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "candidate_sha": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
  "subjects": [
    {"case_id": "TC-GAMEPLAY-006", "subject": "aoe-item-dispatch"}
  ],
  "findings": []
}
```

An accepted finding additionally names `finding_id`, `case_id`, `subject`,
`family` and `reported_member`. Its actual review/task, origin and source
location come from the coordinator's observation, not candidate JSON.
The requested subjects/findings must equal the frozen accepted scope; omitting
an entire finding is not a way to avoid its sibling obligations.

The three responsibilities are:

| Implementation | Contract |
| --- | --- |
| `review_family.py` | Strict data, ownership, coverage and round reducer |
| `review_subjects.py` | Existing-case bindings, finite models and closed probes |
| `trusted_review_gate.py` | Exact Git/GitHub collection and approved test-process execution |

`ReviewTools` loads the two fixed validator modules from the coordinator's
explicit **reviewed tool revision**, compiling the exact captured Git bytes.
The existing fixed isolated launcher must itself run from a **separately
trusted checkout or installation**, using a trusted interpreter. Its trust is
established by the coordinator before invocation: a candidate launcher cannot
authenticate itself by checking `--tool-revision` after it has already started.
Do not run the candidate checkout's bootstrap or automatically copy it elsewhere
and call that trust.

For `review-family`, `--repository-root` identifies the candidate object-storage
worktree, not the launcher's own root. The launcher requires separate locations
and validates the exact candidate Git top level, commit type and captured
regular-blob bytes before executing any reviewed initializer or validator.
Other protected modes retain their original same-root checks. It loads the
gate's captured bytes, not a validate-then-reopen working-copy path.
An existing case binding/model may be
reviewed in the same feature PR and selected at its exact tool revision.
There is no required base-first installation or second canonical case catalog.
The request cannot select that revision or register its own probe.
This introducing PR supplies the fixed launcher mode too: the coordinator may
independently review and trust that launcher from this PR in another checkout.
This explicit introducing-PR boundary does not require a separate adapter PR,
broker, source ledger or generic bootstrap service.

`resolve_subject` joins an existing catalog case to the reviewed binding.
`expand_members` derives the finite source-backed obligations at the finding
origin and candidate. `run_obligations` executes their closed selectors and
returns actual observations. `ReviewTools.assess` obtains fresh GitHub facts,
executes the actual origin/candidate probes, revalidates source identities and
GitHub state, then calls `assess_handoff` with the coordinator's real review
session and triage. The session's `identity` is the frozen
`(repository, pull_request, base_sha)` tuple. The coordinator derives
`base_sha` as the unique merge base of the candidate and the observed live base.
Unrelated base fast-forwards preserve that identity; an arbitrary older
ancestor is not an acceptable replacement. The coordinator derives
`pre_review_required` from the existing #176 risk/threshold decision, not a
candidate option.

The coordinator supplies existing tool adapters through the in-process API;
they are never deserialized from a file. This API does not authenticate an
arbitrary Python caller. Its trust boundary is the existing coordinator/tool
role, not a new platform.
The existing ownership index enforces repository/PR and candidate-head
exclusion independently, even for disjoint scopes. A new head does not permit
a second active reviewer on the same PR; another PR does not permit duplicate
active review of the same candidate head. Only work with both a different
repository/PR identity and a different head remains independent. Completion
releases both exclusions for a subsequent bounded session.
`begin` requires `runtime.start` to return a nonblank string task identity,
preserved unchanged. Missing, blank or wrong-type handles unwind the attempted
reservation before any lease or local report can be created; a later valid
acquisition remains possible.
`advance` rejects while a lease is active, including after its deadline or
a stop acknowledgment without terminal evidence. The existing observed
completion/timeout/abort path must release it first. Subsequent head changes
preserve the old report's origin rather than rebinding it to the new head.

`ReviewSession.finish` snapshots validated runtime fields into an internal
frozen report before releasing ownership. Scope/actions are immutable sets,
findings are a tuple of frozen typed values, and timestamps and bounds are
copied values. Later runtime-record mutations cannot change the accepted
report or its pre-review chronology. This is a value snapshot, not a receipt,
signature, history service or protection against arbitrary malicious Python
mutation of the trusted coordinator. Before handoff, call
`session.triage_local(finding_id, accepted=decision, reason=reason)` for every
returned finding, with a Boolean decision. Accepted records enter the existing sibling sweep;
rejected records retain a nonblank coordinator reason. Omitted, duplicate,
wrong-task or contradictory dispositions cannot establish eligibility, even
after a later candidate passes its probes. This is coordinator-owned triage,
not authenticated provenance from a JSON author.

For diagnostic planning/checking, use the closed launcher mode:

```bash
TRUSTED_REVIEW_ROOT=/absolute/path/to/independently-trusted-checkout
CANDIDATE_ROOT=/absolute/path/to/candidate-checkout
"$TRUSTED_REVIEW_ROOT/build/host-python/bin/python3" -I \
  "$TRUSTED_REVIEW_ROOT/scripts/workflow_pilot/isolated_launcher.py" review-family \
  --repository-root "$CANDIDATE_ROOT" --subject-root "$CANDIDATE_ROOT" \
  --tool-revision "$REVIEWED_TOOL_SHA" --candidate "$CANDIDATE_SHA" \
  --request "$CANDIDATE_ROOT/build/review-request.json" --mode plan
```

`plan` derives obligations from exact Git source without executing candidate
code. `check` also reads GitHub and runs the selected candidate probes.
Both emit audit JSON, never a bearer credential or merge permission. `check`
lists untriaged review IDs and leaves handoff eligibility false: a file
argument cannot establish an independent task or complete coordinator
triage. The coordinator consumes actual task results with the in-process API;
it must not convert `source_audit_complete` into approval.
Expected missing-object and ancestry failures are translated at the existing
Git adapter into bounded, nonzero `review-family:` diagnostics in both direct
and isolated entrypoints, not tracebacks or successful fallback assessments.
The actual direct `GitTree.git` path supplies a 60-second deadline through the
existing reporter Git runner's optional timeout seam; unrelated callers keep
their original default behavior. Its timeout is translated through the same
direct adapter boundary, independently of the launcher helper.
The fixed launcher's Git helper also translates its expected subprocess
timeout into a bounded `workflow-pilot-launcher:` failure. The GitHub adapter
translates expected CLI timeout/launch errors once into its existing
`ValueError` boundary; both entrypoints report bounded `review-family:`
diagnostics. Unexpected programming exceptions are not relabeled as ordinary
tool unavailability.

### Finite coverage and actual evidence

| Family | Required roles |
| --- | --- |
| action | actions, items, targets |
| lifecycle | entries, preservation, resets, terminals |
| wire | producers, consumers, validators, replay, stale-bindings |
| generated | owners, outputs, consumers, drift-checks |
| resource | enabled, disabled |

Roles are not the entire concrete member set. Every obligation identifies its
actual producer/predicate, consumer, representation, revalidation, profile,
source inputs, evidence classes and expected `kind` (`host`, `native`,
`parsed` or `arm-object`). Parse the declared enum/schema/model;
never infer completeness from a filename alone. The trusted reviewer and
coordinator must select the model that genuinely represents the finding.
Unknown, ambiguous, added/deleted or remapped members need a reviewed model
and explicit removal evidence; they cannot disappear silently. Current
bindings reject such changes rather than manufacture not-applicable results.
The total bound is 250 obligations, not five arbitrarily selected siblings.

An obligation's immutable `inputs` retain its semantic subject attachment.
Its separate `execution_inputs` bind the complete shared worker staging
closure; the adapter does not add undeclared candidate files. AoE inputs include the
compiled core/reference sources and the complete staged header set. Generated
inputs include all schemas imported by the existing registry, the staged
generated-data code/data/headers/inventories and declared authored resources.
Every observation's `source_objects` bind the complete execution union,
including mixed-subject requests; narrowing that closure cannot borrow
undeclared bytes staged for another member. Adding another subject does not
expand a member's semantic `inputs`: an unrelated generated/workflow source
cannot attach to an AoE finding merely because it was staged or another
subject failed. The fixed reviewed tool overlays remain
bound by `tool_revision`, not a candidate-supplied program. This is the actual
finite staging boundary, not a generic Python import/capability guarantee.

The shipped unrelated subject uses are:

- **`TC-GAMEPLAY-006 / aoe-item-dispatch`:** actual typed AoE phase, route and
  target predicates in `src/expansion_aoe.c`. The existing C driver has closed
  per-phase/shape/route/target selectors, preserving its no-argument full run.
  It executes real functions with positive and adversarial inputs. Separate
  shape selectors build the actual range map and compare every cell with
  independent selected-shape bitmaps, not merely tile totals or another
  production geometry helper. A same-nine-cell square cannot stand in for
  the cross geometry.
  Separate
  enabled/disabled native reference and ARM object symbol/section checks
  establish the resource boundary. Each enabled core/reference object must
  contain a nonempty `ewram_data` section; an absent placement is not zero
  budget usage. The aggregate 128-byte EWRAM and 8-KiB text limits and disabled
  symbol omission remain required. The modern linker gate passes its resolved
  `MODERN_CC`, `MODERN_NM` and `MODERN_SIZE` paths, including
  `MODERN_TOOLCHAIN_ROOT` and explicit compiler overrides, to the workers.
  Direct coordinator calls use the same environment settings or the closed
  `ReviewTools(..., arm_tools={...})` mapping; defaults resolve through PATH.
  Missing selected tools remain unavailable, never a fallback to system tools.
  The parsed phase/shape enum values must match the existing zero-based,
  contiguous selector mapping and count sentinels: aliased or missing numeric
  cases cannot masquerade as independent siblings. The finite parser supports
  implicit increments and nonnegative decimal/octal/hex literal assignments,
  not general C expression evaluation. Equivalent formatting or explicitly
  reordered declarations preserving values remain valid.
  This binding does not claim every future
  downstream route provider or in-game UI path is covered.
- **`TC-CORE-004 / generated-eventlists`:** the real event-list schema's
  required and optional owner declarations, authored source, generated C,
  typed consumer round trip and committed inventory. Optional chapter/strategy
  owners run their existing schema validation as well as event-list reference
  checks, not those independent features' full ROM/runtime gates. Existing
  observation inputs include the concrete producer/parser/inventory, shared
  validation modules and authored resources consumed by this binding; valid
  findings in those sources participate in actual before/after sibling sweeps.
- **`TC-WORKFLOW-REVIEW-FAMILY-001 / review-session`:** actual reducer and
  request code executed as a registered subject with finite lifecycle/wire
  controls. This explicit binding never redirects unrelated findings to
  workflow-governance code.

`affected-fixed` requires the same reviewed semantic probe to find a contract
violation at the actual finding origin and to pass the candidate. Unaffected
siblings must pass their own probes before and after. Each accepted defect
requires an affected-fixed row for its **reported member itself**; an
already-satisfied reported member cannot borrow a different sibling's failure.
Both direct handoff assessment and the coordinator adapter use that same
predicate. Being included in the execution closure establishes staged-byte
binding, not semantic finding attachment or damage: unrelated inputs or
all-pass tests cannot repair a finding.
Missing/duplicated/wrong-subject/stale observations reject. Imports, compile
errors, missing tools, zero/skipped tests and timeouts are **unavailable**,
not useful failing controls. Native exit codes come from the selected trusted
driver, not a candidate PASS label. Host, native, generated and ARM object
results are explicitly typed; none is relabeled as target-ROM execution.
When common generated-source validation fails before an output, consumer or
drift predicate, the selected member is `unavailable` with zero checks and
`blocked_by: ["owners:eventlists"]`. Optional owners' additional event-list
reference validation uses the same attribution, without hiding their own
validation failures. The reducer requires an actually failed owner observation
in that same subject/family/origin and a satisfied candidate, and reports the
sibling as `prerequisite-fixed`, never `affected-fixed` or
`verified-unaffected`. Missing or unobserved prerequisites still reject.
Reporting that blocked drift/consumer as the defect cannot pass the mandatory
reported-member check. Genuine owner defects and executed member failures
retain their normal before/after evidence.
The worker reports `probe`, `kind`, `verdict`, strict integer `checks`, a
`blocked_by` member list (empty unless attributed as above) and a
nonblank `detail` of at most 2,000 characters. The adapter preserves those
fields together with the captured source objects, and validates the reported
kind against the obligation instead of deriving a replacement from the probe
name. Missing fields, wrong kinds/counts/types and unknown fields reject.
Successful and contract-violation rows retain the actual executor's kind and
positive check count (a failed semantic assertion counts as an executed check).
Unavailable rows always have zero checks: they retain the attempted executor's
kind when known, or use `null` when no executor/result was obtained. Neither
form grants successful or affected-fixed evidence credit. A wrongly routed
native/host executor cannot establish an ARM/parsed obligation, even when its
own assertions pass or report a genuine violation.
Actual gameplay changes still require their applicable existing ROM scenario.

### Review triage, holds, persistence and metrics

GitHub collection authenticates the actual Copilot Bot identity and retains
complete bounded review content. IDs, heads, actor and timestamps are facts;
the coordinator's complete-content triage determines clean/change-request/
untriaged status. COMMENTED, no inline comments and natural-language approval
phrases never automatically mean clean. Changed content invalidates old
triage; missing/incomplete observations fail closed. Dismissed review facts are
retained as history but never clean authority: only active COMMENTED/APPROVED
facts can support clean triage on the exact head. Handoff eligibility and
`exact_head_review_clean` share one readiness condition: every collected fact
has complete triage and zero unresolved conversations, including earlier-head
and historical reviews. Exact clean additionally requires the latest
current-head active clean triage; handoff readiness alone is not final remote
approval and remains subject to the architecture hold. A later clean review
cannot hide an older unresolved thread or untriaged record. Resolution is
observed from fresh GitHub facts and requires the normal changed-fact retriage
before either readiness or clean assessment.

Keep one current record per review ID, with immutable review head, actor and
submission identity inside the frozen repository/PR session. An unchanged
provisional `untriaged` observation can receive complete triage once; an
unchanged finalized replay rejects. Fresh edited content, thread state or
dismissal invalidates the previous decision and handoff, including changes
observed during probe execution. Explicit coordinator retriage replaces the
record instead of adding a round. Formal CHANGES_REQUESTED observations count
before content triage, but do not emit actionable handoffs until triaged.
The finalized `dismissed` outcome is permitted only for an actual DISMISSED
fact; it completes historical triage without granting clean authority.
Previously accepted finding bindings remain required, including when newer
content omits them. Retriage refreshes handoffs from that accepted set.

First and second consecutive change requests produce bounded handoffs.
Each returned `round_handoffs` entry binds `candidate_sha` and `tool_revision`
and contains `outcome_refs`, indexes into that assessment's `outcomes`.
Each outcome identifies the finding and full subject/family/member, with
`origin_evidence` and `candidate_evidence` indexes into the same response's
`evidence` array. Those validated records retain the member identity, probe,
profile, evidence classes, actual kind/checks/verdict/detail and prerequisite
attribution. Their `source_set` indexes address the response's `source_sets`,
each containing the exact revision/tool binding and captured path/Git-object
pairs. Source sets and member observations are emitted once, not copied into
every finding/round; no source payload or external evidence registry is added.
References are local to the enclosing assessment, not permission tokens or
identities to reuse across candidates. An auditor can follow each round to
every examined sibling's origin and candidate evidence without reconstructing
discarded worker results. Later round refreshes do not mutate returned reports.
Before a hold, clean triage resets the sequence. The third request creates a
sticky hold bound to its review ID and head. New heads and later clean
reports cannot reset it. Only the coordinator's bound `redesign`, `decompose`
or `retain-with-evidence` disposition, with a reason, permits resumption.
Stale/wrong-head/reused dispositions fail.
A valid disposition starts the next count window. Retriaging an older current
record cannot recount completed rounds or silently reopen the old hold.

Preserve #207: stop new narrow work and eligibility during the hold, but
immediately publish already-created commits on their assigned branch as
explicitly ineligible WIP. No side branch or post-commit persistence delay is
introduced. Retain the decision in existing coordinator state/canonical
evidence; saved audit JSON is not authorization.

#176's historical baseline v1, fixtures and formulas remain unchanged.
Use `reviews.rounds`, `reviews.valid_findings_per_kloc`,
`delivery.first_push_to_clean_review` and the existing `pilot_coordination` /
`metadata_maintenance` event fields. Record actual coordination work; do not
invent saved minutes or a second metric ledger.

All exact-head Copilot/security/Build, unresolved-review, exact-master Build
and remote-completion gates remain mandatory. The audit never grants merge
permission. There is no gameplay/save/config/locale/generated-game-data,
ROM/RAM, modern/archival, Build topology or required-context change. Rollback
is the dedicated #179 PR revert, with no manual-only criterion.

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
Apply only to task workspaces the coordinator explicitly knows are **released
and quiescent**. A green inventory result does not establish that precondition.
Retain uncertain registrations or workspaces with possible continuing writers;
never guess that another active session's work is disposable.
The helper keeps the invoking/source/main/master worktrees, broad
home/repository/session roots and ancestors, other registered worktree
ancestors, explicit preserved paths, and active process working directories.
It checks exact Git common-directory and metadata-backlink ownership, branch
and HEAD, ordinary and hidden-index changes, all untracked files, private
worktree refs, incomplete Git operations, and upstream divergence.
Detached, unassociated/reused branches, missing registrations, populated
submodules/nested Git repositories (including bare or separated Git metadata
without a `.git` child), mounts, special files, and unknown ignored local data remain
held, not guessed disposable.

An **unpopulated gitlink** can qualify only when its directory is present,
real, and completely empty. The parsed index and immutable HEAD tree must
have identical gitlink path sets, modes, and object IDs. Staged additions,
deletions, renames, mode/ID changes, and unresolved stages remain held.
Superproject status uses `--ignore-submodules=all` to avoid executing nested
Git commands; that status is not a substitute for the independent identity
comparison. The helper never runs Git in a submodule to establish emptiness.
Filesystem observation starts from an opened worktree-root descriptor. Each
relative directory component uses fd-relative `O_DIRECTORY|O_NOFOLLOW`, with
entry/descriptor identity checks before and after observation. The size scan
uses the same anchored traversal, not full-path resolution, stat or directory
opening through a potentially substituted parent. Symlink targets are never
traversed to decide whether to reject the link.
Missing directories, symlinks (including parent components), mounts, any
contained entry, and changing directory identities or modification times
retain the workspace. Known generated-output names do not exempt contents
inside a gitlink. Do not create missing directories, deinitialize submodules,
or rewrite the index to make a retained real worktree qualify.

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
identity/status/lock/process/private-recovery/empty-gitlink checks and the
nested-Git/size scan immediately before normal `git worktree remove`.
The size scans also recheck the same gitlink directory observations, including
emptiness: a zero-byte file can leave the allocated size unchanged, and normal
Git removal alone can delete ordinary files inside an uninitialized gitlink.
Every expected gitlink must actually be visited and checked; a substituted
ancestor cannot hide a gitlink even when the total allocated size is unchanged.
There is no force, deinit, index rewrite, unlock, branch deletion, global prune,
or recursive filesystem deletion fallback.

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
blocks removal. Do not reassign targets while cleanup is running.
**Residual write window:** new gitlink content after its final empty-directory
observation and before Git deletes the workspace can be lost; ordinary Git
removal does not inspect that uninitialized gitlink content. Rechecking observed
changes is not atomic exclusion of arbitrary writers. If the coordinator cannot
establish a known released, quiescent target throughout apply, retain it.
API limits, unavailable history, ambiguous identities, and retained user data
are precise operational holds, never reasons to broaden deletion.
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
`build`, `extended-host-tests`, `legacy`, and `summary`.
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
adapters, `extended-host-tests`/`legacy` are exactly
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
Master-only packaging is part of `build`; a packaging failure fails that
required worker and summary. PR and metadata runs skip only those packaging steps.
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
SHA.
Patch packaging trusts reviewed, merged master source and pinned/declared
tools, like ordinary CI. It is not a sandbox against malicious repository
writers, hostile same-UID code, compromised dependencies, or runner compromise.
The normal modern job builds/checks the named release profile once from its
fresh exact checkout. Only an authenticated master push packages that existing
ROM and metadata in the same job; PRs and forks neither receive the private
base nor upload a patch. The packaging script invokes no Make target.
The existing producer checks the approved base hash/header, target header and
embedded metadata, exact commit/profile, BPS round trip and three-file artifact.
Private input uses a unique mode-0700 directory, mode-0400 base and failure/
signal cleanup. Download diagnostics never disclose the URL or private bytes.
Only verified BPS/manifest/README files are uploaded after private cleanup;
the ROM stays inside the build job and is never an artifact/cache handoff.
Packaging or cleanup failure fails `build` and therefore the required summary.
No custom UID, namespace, cgroup, supervisor, broker or capability platform is
part of this contract. The retired isolation proposals are superseded, not
claimed to have passed their tests.

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
The current Build workflow also supports the input-free full
`workflow_dispatch` described in [adaptive candidate gating](#adaptive-review-first-candidate-gates).
That dispatch retains all eight jobs; preflight alone or an early/unbound run
is not full candidate evidence, and publication remains master-push-only.

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
The real `gh` subprocess is captured as bytes, bounded before decoding, and
decoded explicitly as UTF-8 without universal-newline translation. Request
JSON is likewise encoded explicitly as UTF-8. Invalid UTF-8 fails before
authority parsing, and the isolated-launcher fake-`gh` controls exercise raw
LF, CRLF, bare-CR, and mixed-line-ending responses through this production path.
The `gh api --include` envelope has one explicitly recognized rendering form:
an LF-terminated status line followed by uniformly CRLF-terminated header
fields and separator. Only that first status separator is special; mixed
endings or bare CR inside the header fields still reject. The plain HTTP
parser remains strict unless its caller explicitly identifies the `gh`
transport envelope. This matches the real GitHub CLI output without changing
or normalizing any received bytes.
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
or mutate. Materialization changes across the initial, pre-intent, and
post-intent run/job snapshots also defer.

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
metadata. A mutation-eligible default edit takes three complete exact-candidate
run/job snapshots: initial, pre-intent, and post-intent immediately before
PATCH. It returns `deferred` when a later snapshot differs from its predecessor
or no longer proves the same successful full Build. If the pre-intent snapshot
already requires deferral, the helper stops there without creating an intent
or taking the third snapshot.
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
reason in the canonical evidence comment. An essential edit may also correct
the contract of a fully observed terminal failed or cancelled full Build.
Every job in that terminal snapshot must also be terminal; an inconsistent
run-completed/job-active response cannot authorize the edit.
Permission to edit does not authorize successful CI continuity: that failed
full run remains ineligible even if a later metadata-only run is green.
Default nonessential edits still require successful full evidence, and an
essential edit still needs an exact full-run identity to bind.

Transaction-comment creation accepts HTTP 201 `Location` only when it names
the same canonical API comment resource attested by the response body.
Redirect statuses, unexpected `Location` on ordinary HTTP 200 responses,
misbound resource URLs, and malformed creation responses remain rejected;
the helper never follows a creation header as a redirect.

No tracked or local pending-state
ledger is created: GitHub's run number, attempt, event, workflow, PR, head,
base, mode, status, conclusion, and job identities derive whether
reconciliation remains pending. The helper uses two append-only,
owner-authored GitHub comments rather than a local receipt:

1. Before PATCH, an immutable **intent** comment records a unique nonce,
   repository/PR/head/base/workflow identity, complete pre-state and target
   title/body digests, `provided_fields` digests for every supplied file,
   `changed_fields` digests only for values differing from pre-state, the exact pre-edit
   metadata-version authority, and the highest fully observed pre-PATCH run
   ID/number/creation-time watermark.
2. The helper PATCHes only `changed_fields` and strictly validates the complete
   title/body response. A provided-but-unchanged field is never PATCHed and
   requires no metadata-version advance, but remains bound by its provided
   digest, the pre-state, target digest, response, recovery, and current state.
   It then queries metadata-specific GitHub authority: the latest exact
   `RenamedTitleEvent` for title changes and the latest immutable GraphQL
   `UserContentEdit` node for body changes. Body authority binds connection
   `totalCount`, newest node ID, timestamps, deletion state, owner editor,
   `diff`, and `pageInfo`; PR `lastEditedAt` is only a consistency field.
3. An immutable **confirmation** comment references the exact intent comment
   ID/nonce and binds the complete target state plus that metadata-version
   authority.

Successful updates and target-state recovery return validated intent and
confirmation comment IDs/URLs. A read-only ambiguous-outcome hold may return
only its authenticated intent ID/URL; that deferred result is not an
authoritative pair and cannot authorize no-op or reconciliation. No
caller-authored receipt file or mutable local ledger exists.

The REST `body` field is required but nullable; GraphQL's `PullRequest.body`
is a non-null string. The helper normalizes REST null to the canonical empty
string when constructing observed PR state. Body comparisons, digests, changed
fields, PATCH-response attestation, and reconciliation therefore agree on an
empty body without conflating an absent or malformed field with valid empty
content. GraphQL must still supply a string. Nonempty bodies and their digests
are unchanged. Omitting `--body-file` still means no body request; supplying an
empty file for an already empty body does not fabricate a body-edit version,
while clearing nonempty content requires the real new edit node.

GitHub's GraphQL `Actor` interface exposes `__typename` and `login`, but not
`databaseId` directly. The production query therefore selects
`editor { __typename login ... on User { databaseId } }` and the same shape
for `RenamedTitleEvent.actor`. Owner authority requires the `User` fragment's
exact numeric ID and login; bots and deleted/null actors cannot authorize the
requested edit.

`userContentEdits(first: 2)` is newest-first. The helper requires exact
0/2-node cardinality from `totalCount`, canonical page flags and cursors, a
unique newest node, strict timestamp chronology, distinct IDs, null
`deletedAt`, and newest-node `diff` equal to the current body. No-edit state
requires zero count plus empty nodes, cursors, `lastEditedAt`, and editor.
The first body PATCH must advance `totalCount` from **0 to 2**: GitHub
materializes both the original snapshot and the first edited revision.
This is not permission for two intervening edits. The older node's `editedAt`
must equal the same PR's `createdAt` and strictly precede the latest edit; its
editor must match the PR's original `User` author by numeric ID and login.
Both nodes must have distinct IDs and share their immutable `createdAt` /
`updatedAt` materialization time. The original `diff` must have the same
canonical body digest as the intent's `pre_fields.body`, including an empty
original body. The latest revision still requires the repository-owner editor
and exact current body. A subsequent body PATCH must increase the count by
exactly one and introduce a new latest node.

The metadata version's required `body_original` field preserves that proof
when `totalCount` is 2: `edit_id`, `body_sha256`, `author_id`, `author_login`,
`authored_at`, and `materialized_at`. It is explicitly null for no-edit state
and for later histories whose bounded newest-two query no longer includes
the original. Confirmation, recovery, and reconciliation bind the complete
proof, not only the count. A single-revision history, two real intervening
edits, forged original content/identity/timing, deleted or malformed nodes,
and creation/first-edit timestamps in the same ambiguous second fail closed.
Missing proof is never synthesized from a cached body or silently upgraded.
Reconciliation binds the exact count/node/original identity, so a later
same-second edit/revert invalidates confirmation even when body text and
`lastEditedAt` return to prior values.

```json
{
  "base_sha": "<40-lowercase-hex>",
  "head_sha": "<40-lowercase-hex>",
  "nonce": "<64-lowercase-hex>",
  "pre_fields": {"body": "<sha256>", "title": "<sha256>"},
  "pre_metadata_sha256": "<sha256>",
  "pre_version": {
    "body_editor_id": 123,
    "body_editor_login": "owner",
    "body_edit_total_count": 3,
    "body_edit_id": "UCE_node",
    "body_edit_created_at": "YYYY-MM-DDTHH:MM:SSZ",
    "body_edit_edited_at": "YYYY-MM-DDTHH:MM:SSZ",
    "body_edit_updated_at": "YYYY-MM-DDTHH:MM:SSZ",
    "body_last_edited_at": "YYYY-MM-DDTHH:MM:SSZ",
    "body_original": null,
    "title_actor_id": 123,
    "title_actor_login": "owner",
    "title_current": "Current title",
    "title_event_created_at": "YYYY-MM-DDTHH:MM:SSZ",
    "title_event_id": "RTE_node",
    "title_previous": "Previous title"
  },
  "pr_number": 123,
  "repository": "owner/name",
  "repository_id": 123,
  "provided_fields": {"body": "<sha256>", "title": "<sha256>"},
  "changed_fields": {"title": "<sha256>"},
  "schema_version": 1,
  "target_metadata_sha256": "<sha256>",
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
deleted-author comments remain permitted and cannot disable the helper.
A successful authority read performs two complete bounded page walks and
requires the same ordered parsed comment records in both, including ordinary
comments whose deletion can shift protected records across page boundaries.
Each pass retains canonical Link and page/cardinality/identity checks; an
invalid or differing pass raises before its result is used. There is no retry
loop or fallback to the first incomplete observation. Transaction selection
and canonical evidence updates share this stabilized reader. This does not
make a later GitHub mutation atomic or replace the single-writer contract.
At each subsequent transaction refresh, the selected intent must still be
present as the same complete owner-authenticated parsed comment, including
its canonical receipt and timestamps. Matching only its comment ID or
equal-second creation/update timestamps is insufficient. Missing, changed,
unmarked, or no-longer-owner intents raise before any further PATCH or terminal
comment; cached receipt data cannot authorize an abort. The helper rebinds to
the fresh record before using it. After a validated PATCH, and on target-state
recovery without PATCH, it refreshes the transaction graph again before the
final metadata-version read and confirmation. An existing confirmation or
abort instead defers without writing another terminal. Reconciliation likewise
compares the complete selected intent and confirmation across its refreshes.
The one marked canonical evidence comment must be the repository owner's
owner-associated `User` comment with the exact owner numeric user ID from the
validated PR repository payload and exactly one standalone marker.
Marked missing/deleted authors, bots, non-owner associations, cross-repository
IDs, duplicate or embedded markers, and a PATCH response that does not attest
the same author/comment plus the requested body are rejected.
Response author identity includes the numeric ID, login, user type,
`site_admin: false`, and owner association; matching only a subset cannot
authorize a canonical update.
One shared protected-marker classifier validates both outbound bodies and
owner-authenticated responses. A canonical evidence replacement must contain
only its one standalone evidence marker and no intent, confirmation, or abort
marker, even embedded in quoted text. Transaction creation also validates its
single marker and complete canonical typed body before POST. Invalid bodies
therefore fail before any write rather than poisoning future comment scans.
Structured results reserve `run_id` exclusively for an Actions workflow run
and `comment_id` exclusively for the canonical issue comment. Both fields are
optional and serialized in every result, but they are mutually exclusive;
`comment-updated` requires only `comment_id`.
The isolated CLI writes exactly one canonical JSON line for a decision: exit
status `0` means successful, no-op, or already complete; `3` means deferred or
refused; and `2` reports invalid arguments, unreadable files, or malformed API
authority without decision JSON on stdout.
The intent/confirmation pair is not a secret, signature, or authentication
token. Reconciliation refetches every transaction comment, parses both closed
canonical schemas, requires exact repository/PR/comment URLs and
repository-owner numeric ID/login/`User`/`OWNER` authority, rejects edited
comments, duplicate nonces, duplicate confirmations, and contradictory pairs.
All protected comments are validated before selection, so malformed historical
records remain fatal. Well-formed records are then grouped by repository, PR,
head, base, and workflow; superseded candidate groups are ignored. Recovery
first links every confirmation and abort to an exact intent ID/nonce/candidate,
rejecting duplicate or conflicting terminal edges. Confirmed and aborted
intents are terminal and excluded before active-intent selection. Recovery
uses the unique latest active intent only within the exact current candidate
group. Multiple unclosed active intents sharing a `createdAt` second remain
ambiguous. Historical/automatic-recovery ordering uses `(createdAt, comment ID)` only
after proving terminal comments do not predate their intent. Thus an
equal-second aborted predecessor followed by a higher-ID successor is valid.
Post-intent revalidation checks the selected intent's terminal state before
classifying active-intent drift. An observed confirmation returns `deferred`
with that exact confirmation's reconciliation command, never a conflicting
abort or a second PATCH. An observed abort returns `deferred` without another
terminal comment. A concurrently observed terminal is not continuity success:
reconciliation must still validate the current metadata version and exact
Build evidence. The `mutated` result records any intent created by this
invocation or PATCH already applied, even when another invocation subsequently
terminates the intent.
Every confirmation/abort edge independently requires both
`terminal.createdAt >= intent.createdAt` and
`terminal.comment_id > intent.comment_id`; a later timestamp never excuses an
equal or lower ID.
Explicit reconciliation resolves the requested confirmation comment to its
referenced intent within the exact candidate and workflow. A later unconfirmed
intent, even one in the same timestamp second, does not supersede a valid
confirmed pair. The selected pair must still match current title/body,
metadata-version, run, and watermark authority; a later actual edit or
edit-and-revert remains invalidating. Automatic edit recovery retains its
separate latest-active-intent rule.
Intent and confirmation may share a second because exact comment ID and nonce
provide linkage; confirmation may not predate intent. It refetches the metadata-specific version and complete current
title/body state; later direct edits and edit-and-revert change
`RenamedTitleEvent` or body `UserContentEdit` count/node authority and
invalidate the pair.
Transaction comments are never edited or deleted by reconciliation and their
`issue_comment` creation does not trigger Build CI.
The separately marked canonical evidence comment may continue to be updated;
it never becomes a receipt and does not supersede the latest receipt marker.

The live metadata-history probe uses the separate read-only
`inspect_metadata_history()` entrypoint. It validates the same repository,
owner, PR, head/base, and metadata-version identities for either an open or a
closed PR, so its documented fixture remains usable after merge. It performs
only a REST GET and the metadata GraphQL query, returning
history rather than mutation authority. Every edit, reconciliation, and
evidence-comment mode still requires an open PR; unknown PR states fail closed
even in the history probe.

After the newest exact full Build succeeds, use confirmation-comment-bound
`pr-metadata reconcile`:

```bash
/usr/bin/python3 -I scripts/workflow_pilot/isolated_launcher.py \
  pr-metadata reconcile \
  --repository "$repo" --pr "$pr" \
  --head-sha "$head_sha" --base-sha "$base_sha" \
  --confirmation-comment-id <confirmation-comment-id>
```

Reconciliation revalidates head/base and all exact run authority twice, then
uses two independent run identities. The newest exact-head/base full run is
only the current authorization gate: active defers, noncanonical/failure
holds, and canonical success permits processing. Explicit-same metadata runs
strictly above the intent watermark are only candidates, not proof of an edit's
cause. An earlier direct edit's run can be absent from every pre-PATCH listing
and appear later above that watermark. Neither its run number nor its creation
time can authorize reconciliation or an authoritative no-op.

The existing trusted-base `event-router` now runs the isolated
`attest-metadata-event` producer against the **immutable original**
`GITHUB_EVENT_PATH`, never a current PR query or cached receipt. Its canonical
transition fingerprint binds repository/owner/PR numeric and node identities,
head/base refs and SHAs, complete pre-field and target-state digests, the exact
changed-field set/values, the event's native metadata `updated_at`, workflow
path, and run ID/number/attempt. The `metadata-classifier` publishes it in one
successful `workflow-pilot-metadata-event:v1:<sha256>` step name. The normal
GitHub jobs API supplies this run-bound observation without an artifact,
another job, extra permissions, or a mutable ledger. The consumer checks the
step's number, uniqueness, success, digest format and job-bounded chronology.
The upstream verifier independently validates both steps and their output link
as closed CI setup; neither becomes one of its 28 locally executed gates.

The fingerprint must match the authenticated intent/confirmation's exact
transition. Its event metadata instant must identify the confirmed native
history uniquely: every changed field has that same edit instant, and each
field's pre-version strictly precedes it. First-body original-snapshot proof
and subsequent count-plus-one validation remain required. Equal-second
repeated revisions, disagreeing title/body edit instants, or unavailable native
history cannot establish attribution. This is an exact raw-event/content/
native-version join, not a timestamp-only ordering guess. Actions and
issue-comment timestamps are never compared for causality.

An attested unrelated transition is ignored even if its run arrived late above
the watermark. Multiple distinct edit-attested metadata IDs are ambiguous and
fail closed; an otherwise plausible run lacking attestation also holds rather
than being guessed away. Only the unique matching stable ID/number survives
rerun attempts, whose proof is bound to the new attempt of the same original
event. Reconciliation refreshes that proof before rerun, and authoritative
no-op uses the same selector. If the matching run is absent or attribution is
unprovable, the result explicitly defers without rerun, `complete`, or `no-op`.
Older trusted bases without the producer, invalid/unowned event payloads, and
historical runs without the proof step remain unprovable: the router leaves
the digest absent without changing the established Build classification.
Do not reconstruct missing trigger evidence from today's PR state or
retroactively invent an attestation.
A later successful full run does not hide or replace the edit-bound metadata
identity; the metadata run is never compared to that full run's number. The
eligible run's identity/router/classifier and adapter jobs must be canonical
successes, expensive jobs must be canonical skips; a historical extra publisher job must also be skipped, and
summary must be the canonical failure. Cancelled, timed-out, action-required,
startup-failure, skipped, neutral, stale, active, unknown, unbound, or malformed
runs are never rerun. It dispatches no full Build, edits or deletes no
transaction comment, and exposes no cancellation operation. An already-active eligible
metadata run is deferred, and an already-successful eligible one is complete.

If PATCH succeeds but confirmation creation fails or its response is
indeterminate, retry the same edit command. The helper refetches the latest
unmatched immutable intent. If the exact target state and metadata-version
authority match, it creates the missing confirmation without another PATCH.
If the exact pre-state and pre-version remain, the helper refreshes complete
authority and returns a read-only deferred intent hold: the first request
might still apply. Neither a duplicate PATCH nor an abort is safe merely
because a read still shows pre-state. Any third state fails closed. A no-op
without an authoritative intent/confirmation pair is refused. With a pair, it
returns no-op only when the exact confirmation-bound post-watermark metadata
run is a canonical completed success. An absent or active run defers; a
canonical failure defers with reconciliation/rerun guidance; malformed or
unbound runs fail closed or remain ineligible.

A definite PATCH rejection has a separate, bounded recovery path. The
`GitHubClient` preserves the method, endpoint, and parsed HTTP response in a
typed error even when `gh api` exits `1`. The same byte limits, strict UTF-8,
raw HTTP framing, media type, headers, and JSON validation apply to error
responses. Only a complete response with status **400, 401, 403, 404, 409, 422,
or 429**, from the exact PR PATCH endpoint and a normally exiting `gh`
(status `0` or `1`), counts as a definite rejection. Stderr text such as
`HTTP 422` is not evidence. Timeout/network/start failures, HTTP 408, server
errors, other statuses, malformed/absent responses, abnormal subprocess exits,
and success-shaped HTTP responses with a nonzero exit remain ambiguous.

After a definite rejection, the helper takes a fourth complete run/job
snapshot and a fresh stable two-pass comment walk, rebinds every field of the
selected intent, and honors any observed terminal before proceeding. One final
complete authenticated GraphQL observation must still attest the exact
candidate, pre-state, and metadata version. Only then does it append an
immutable `patch-rejected` abort and return a **deferred**, not successful,
decision. Correct the requested values and retry: the retained abort allows
a strictly newer, independently validated successor intent and its normal
PATCH/confirmation. No immutable record is edited or deleted.

The fourth snapshot must equal the complete parsed run/job snapshot immediately
before PATCH, not merely retain the intent's run watermark. Added or removed
runs, changed attempts, bindings, status, conclusions, timestamps, or job
authority leave the intent held, even when each individual response is valid.
This also applies to normal progress of an essential edit's active Build.
After terminal precedence, the selected intent must remain the unique latest
active intent for its exact candidate/workflow; a newer active intent or
equal-second ambiguity cannot authorize an abort. Ordinary comments and
well-formed superseded-candidate intents do not change that selection.
Unlike drift detected before a newly created intent's first PATCH, changed
authority after a rejected PATCH is not a reason to append a drift abort.

Missing, malformed, or changed fresh authority leaves the intent held without
a fabricated abort. If rejection recovery crashes or abort delivery fails, a
later invocation may consume an actually created terminal, or recover an
authenticated applied target, but cannot retrospectively infer rejection from
pre-state alone. Do not change request fields, blindly retry PATCH, or manually
invent an abort to clear an ambiguous intent.

This full authorization policy is intentionally narrower than mutation
eligibility. A new title/body PATCH still uses `_blocking_active_runs` and
defers for any concurrent exact/unproven active full run. Reconciliation and
authoritative no-op collapse repeated attempts of the same run ID/number to the
highest attempt, then evaluate only the newest distinct relevant full/
unproven identity. Older active or failed full runs cannot override a newer
terminal full success; a newer terminal failure cannot be masked by an older
active run. Reused run numbers across distinct IDs and duplicate attempts fail
closed.

Field-set recovery is immutable. Retries must supply the same
`provided_fields` values and target digest as the unmatched intent; the helper
cannot add, remove, or reclassify fields. Version advancement and the PATCH
payload always use the intent's `changed_fields`. If every provided field
already equals pre-state, no intent is created and authoritative no-op
semantics apply.

Immediately before PATCH, the helper refetches complete PR, run, metadata
and transaction-comment authority, then makes one final complete
repository/owner/PR/ref/title/body/updatedAt/editor/title-event/UserContentEdit GraphQL
request as the last network operation before PATCH. For a newly created intent
that is still present, unchanged, and active, candidate, pre-state, provided-field,
version, run-snapshot, or active-intent drift creates an immutable owner-authored
abort comment and performs zero PATCH. An already observed confirmation or
abort instead defers without appending another terminal. Abort comments
reference the exact intent ID/nonce and observed state/version, are never
edited/deleted, and close that intent. Retry recognizes a maybe-created abort;
an unmatched intent without a terminal remains held if the prior outcome is
unknown. Malformed, duplicate, forged, or contradictory aborts are fatal.
This still cannot make the final GET and PATCH
atomic. The authoritative PATCH response's exact head/base and complete
title/body attestation detects drift occurring in that irreducible window.

Observed abort identity, metadata digest, and version come from the same
validated final GraphQL response, including when run or transaction authority
has already drifted. The response is first validated as an observation of the
same repository/PR, then compared with the immutable intent. A new head/base
or changed body is recorded as actually observed rather than replaced with the
cached pre-intent state. Malformed, inconsistent, or unavailable final authority
raises an error before abort creation; cached state/version is never relabeled
as a fresh observation.

A valid abort closes only its referenced intent. On a later invocation, an
unchanged target follows ordinary authoritative-pair/no-op/refusal behavior;
a remaining real target change creates a strictly newer successor intent with
a fresh nonce. The successor must order after the abort and is independently
revalidated before PATCH. Repeated drift therefore produces bounded
intent/abort generations rather than permanently blocking the candidate.

Comment identity/body/timestamps are always validated. Protected transaction
parsing is applied only after exact repository-owner numeric ID/login,
`User`/`OWNER`, and non-site-admin authentication. Marker-like text from a
contributor, bot, deleted account, or other non-owner is ordinary ignored
comment content and cannot poison transaction history. An owner-authored
malformed protected marker remains fatal.

Every successful title/body update returns this reconciliation command. The
third (post-intent) run snapshot closes the deterministic pre-PATCH run-state
race; only an external same-SHA full rerun that starts after that final authority query is an
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
`"$GITHUB_WORKSPACE"` explicitly, runs both the reporter/schema suite and the
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
reviewed actions, commands, and fields. Hydration and the stdlib-only baseline
gate use `/usr/bin/python3 -I scripts/workflow_pilot/isolated_launcher.py`.
Reporter test discovery instead uses the absolute
`"$GITHUB_WORKSPACE/build/host-python/bin/python3" -I` from the
[locked setup below](#isolated-host-python-dependencies), followed by the same
launcher and `reporter-tests` mode. Python completes isolated startup before
the launcher inserts only its resolved source root and dispatches its closed
modes; it exposes no arbitrary module, command, or evaluation mode. The
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

## Isolated host Python dependencies

Issue #216 supplies the shared schema-test prerequisite for the handoff
(#178) and authenticated broker (#205), without implementing either protocol.
The supported profile is **CPython 3.12 on Linux x86_64 with glibc >= 2.17**.
Other Python versions, architectures and libc implementations fail explicitly;
review new wheel artifacts before extending this profile.

From a source checkout, install the distribution's `python3-venv` package if
necessary, then use the same setup as Build CI:

```bash
/usr/bin/python3 -I scripts/host_python.py create
build/host-python/bin/python3 -I scripts/host_python.py check
build/host-python/bin/python3 -I scripts/workflow_pilot/isolated_launcher.py reporter-tests
```

[`host-tests.txt`](../.github/requirements/host-tests.txt) records one exact
version and SHA-256 wheel hash for every runtime dependency: `jsonschema`,
`attrs`, `jsonschema-specifications`, `referencing`, `rpds-py`,
`typing-extensions`, `rfc3339-validator`, and `six`. The two schema consumers
use Draft 2020-12 and `FormatChecker` with **`date-time`**. That optional
validator needs `rfc3339-validator` and `six`; merely importing `jsonschema`
would silently leave this format unchecked. The bootstrap verifies format
registration and actual valid/invalid draft and timestamp behavior. Other
optional formats are not promised: a consumer adding one must extend the
locked closure and its behavioral probes together.

The stdlib bootstrap creates only a new path under this checkout's ignored
`build/`, refusing an existing or symlinked target. OS-provided `venv`/`ensurepip`
supplies pip; there is no pip self-upgrade or new package manager. Pip runs
under that environment's isolated interpreter with a closed environment,
disabled config/cache, and owned scratch/home directories. It downloads only
the locked binary wheels from trusted PyPI using `--require-hashes` and
`--no-deps`, then installs those wheels with `--no-index` and the same hash
checks. The exact installed set, `pip check`, disabled system/user sites, and
schema probes must all pass. Neither global nor user packages are modified.

The verified wheels remain in `build/host-python/wheelhouse` so the regression
suite can replay clean installs and damaged/missing/incompatible inputs
**offline**. An explicit offline recreation is also available:

```bash
/usr/bin/python3 -I scripts/host_python.py create \
  --environment build/host-python-replay \
  --wheelhouse build/host-python/wheelhouse
```

Use `--environment` only for a fresh owned path within this checkout's
`build/`. Failed setup leaves that path for diagnosis rather than erasing or
reusing it. After tests finish, remove only environments you created, for
example `rm -r -- build/host-python-replay`. To refresh the primary environment,
first remove your own `build/host-python`, then rerun `create`.

Only the full-build host dependency step and reporter-test interpreter change.
Metadata-only attestation still performs no checkout/bootstrap; baseline,
hydration, classifier and publisher authority retain their existing
interpreters. Local `upstream_port verify` resolves the same absolute owned
interpreter against its target checkout but never installs packages itself.
Job IDs, required contexts, routing, permissions, checkout authority and every
candidate/master gate remain unchanged. Conflicts are ambient-only/unpinned
installation, environment reuse, or unsupported wheel profiles. There are no
gameplay, save/configuration, ROM/RAM, generated-game-data, locale, modern
debug/release or archival compiler interactions. Revert this dedicated
bootstrap change on regression; never remove required consumer tests.

The complete human procedure and deterministic negative controls are indexed
as [TC-WORKFLOW-HOST-PYTHON-DEPS-001](test-cases/workflow-governance.md#tc-workflow-host-python-deps-001-bootstrap-isolated-schema-test-dependencies).

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

Issue #178 implements Discussion #174's bounded coordination capability, not a
credential or publication platform. Its
[approved scope](https://github.com/laqieer/fireemblem8-expansion/issues/178#issuecomment-5556967123)
replaces the unmerged signed/broker design. The supported host is Linux with
Python 3, Git, native Copilot CLI events and existing process controls. Live
run reconciliation uses the coordinator's existing `gh` authentication;
deterministic tests need no token, live workflow, emulator or ROM.

### Assignment, result and trust boundary

The closed [JSON Schema](../scripts/workflow_pilot/agent_handoff.schema.json)
describes v3 assignments, candidate results, coordinator state and verdicts.
V2 handoff documents and authority/broker flags are rejected, not migrated.
There are no released v2 handoff consumers. Reporter v1 is a different,
unchanged baseline contract.

The coordinator retains one assignment with issue/PR, owner/session/dispatch
IDs, exact parent SHA, branch/worktree, unique allowed paths/directory prefixes,
exact permitted upstream inputs, findings, keyed acceptance criteria/checks,
named evidence, line/ROM/RAM/protocol budgets, lifetime/RSS limits and the complete
prohibited remote-action set. A successor names its predecessor. No executable
or permission is accepted from an implementation result:

```json
{
  "schema_version": 3,
  "assignment_id": "review-178-1",
  "assigned_parent_sha": "1111111111111111111111111111111111111111",
  "result_sha": "2222222222222222222222222222222222222222",
  "evidence_refs": ["raw-diff", "focused-case"]
}
```

Those SHAs are illustrative, not executable evidence. `load_assignment`,
`load_result`, `parse_assignment(bytes)` and `parse_result(bytes)` reject
over-1-MiB input **before** decoding/copying; duplicate keys, nonfinite values,
unknown fields, invalid Unicode, excessive depth/nodes/strings and unsupported
versions reject. Collections are bounded: 128 assignments/watchers, 256 scope
entries, 32 checks/criteria, 16 upstream inputs and 16 inputs per protocol check.
There are five lifecycle states; text is at most 16 KiB. Keyed criteria/checks and unique
arrays avoid duplicate wire identities. The independent Draft 2020-12 tests
exercise structural/schema agreement. Git identity, cross-record correlation,
chronology, ownership and evidence completeness require additional runtime
checks, not a schema claim of authenticity.

Assignment, owner, session and actual dispatch IDs are each unique across the
retained state, including closed history. A successor or independent assignment
needs a fresh dispatch identity; changing other IDs cannot reuse an old task
correlation.

Reusable text and path fields accept Unicode scalar values, including
supplementary characters and valid JSON escapes. Escaped lone UTF-16
surrogates reject in the independent schema and the byte API/CLI alike.
Protocol paths must also be disjoint across required check definitions:
duplicate or partially overlapping definitions reject at runtime. The ordinary
schema checks each definition's shape/unique inputs, not this cross-record
relationship or the resulting aggregate budget.

The one bounded wire decoder normalizes mathematically integral JSON numbers
(`178.0`, `17.8e1`) to native integers before validation or OS operations.
Boolean, fractional, nonfinite and out-of-range integer inputs still reject;
fractional tokens that would round or underflow to an integer also reject.
Downstream typed APIs keep their strict integer contract. This uses the
published Draft 2020-12 semantics, not a custom schema validator.

Coordinator observations are trusted operational input, **not authenticated
data**. The single canonical state path and its short nonblocking lock prevent
accidental competing writers; unsigned JSON, modes, IDs and source labels do not
authenticate the caller. Existing platform role/tool permissions prohibit
implementation-owner remote actions. Recorded prohibited requests/actions reject;
this module does not prove that an arbitrary hostile same-UID process performed
no hidden action. No signature, HMAC ledger, protected installation, daemon,
remote ref mutation or publication endpoint is present.

State updates exclusively create a uniquely named sibling staging file, fsync
its complete bytes and atomically replace the canonical document under the
existing lock. Normal/error cleanup removes only the current transaction's
created staging file. A crash can leave partial or complete staging, including
the former fixed `.new` name; later transactions leave those files untouched
and read only canonical state. No staging file is promoted as an allegedly
completed operation. A name collision fails without modifying the existing
file, and a retry uses a fresh name. No recovery journal or ownership layer is
needed; a random filename is not authority.

### Coordinator integration and commands

Use a reviewed source checkout, not the implementation worktree, for the
existing isolated launcher. Python `-I` avoids ambient imports; it is not an OS
sandbox or permission to execute arbitrary candidate code with credentials.
The only built-in process check runs the fixed reviewed raw-diff checker with
a closed credential-free environment. Additional focused checks use
`capture_check(entry, check_id, result_sha, trusted_executor)` from the existing
approved coordinator test/CI route. That executor returns an actual owned
process capture and its parsed measurements, never candidate command strings
or displayed `passed` labels. Unregistered/missing executors remain incomplete.
Use existing `linker_report.budget` map/ELF checks for runtime-resource measures;
there is no new resource-closure or file-to-gate graph.

The coordinator creates the initial document with
`new_state(repository, coordinator_id, availability)` and `json_bytes`, in its
session storage outside the implementation worktree. `availability` names
`mode` (`always-on` or `plan`), UTC `observed_at`/`valid_until`, both Dev Box stop
settings and nullable `plan` text. Record Boolean stop settings only when
actually known. In `plan` mode an unavailable setting may remain explicitly
`null`; do not invent a value for a non-Dev-Box or uninspected host.
Unknown settings never qualify for `always-on` mode. The nonblank recovery
plan, bounded UTC coverage and native interruption checks remain required.
A plan is a decision covering the unattended interval, not
proof of perpetual availability. Boot/suspend observations invalidate stale
coverage until the coordinator makes a fresh availability decision.

Set `REVIEWED_SOURCE`, `COORDINATOR_STATE`, `ASSIGNMENT`, `OWNER_EVENTS`,
`RESULT` and `WORKTREE` to those actual paths. The four operations are:

```bash
/usr/bin/python3 -I "$REVIEWED_SOURCE/scripts/workflow_pilot/isolated_launcher.py" agent-handoff assign \
  --state "$COORDINATOR_STATE" --assignment "$ASSIGNMENT"
```

```bash
/usr/bin/python3 -I "$REVIEWED_SOURCE/scripts/workflow_pilot/isolated_launcher.py" agent-handoff observe \
  --state "$COORDINATOR_STATE" --assignment-id review-178-1 --runtime-events "$OWNER_EVENTS"
```

```bash
/usr/bin/python3 -I "$REVIEWED_SOURCE/scripts/workflow_pilot/isolated_launcher.py" agent-handoff validate \
  --state "$COORDINATOR_STATE" --result "$RESULT" --worktree "$WORKTREE"
```

```bash
/usr/bin/python3 -I "$REVIEWED_SOURCE/scripts/workflow_pilot/isolated_launcher.py" agent-handoff reconcile-run \
  --state "$COORDINATOR_STATE" --run-id 123456
```

`assign` reserves ownership before dispatch; it does not launch agents.
`observe` reads the existing native JSONL producer incrementally with no-follow
file identity/cursors. Pass the coordinator dispatch log, then the matching
owner session log (repeat `observe` as actual runtime notifications arrive).
Include `[handoff:review-178-1]` in the actual assigned prompt. `dispatch_id`
correlates the coordinator's task/write-agent/shell tool call and must equal
the native receipt's `data.parentAgentTaskId`; `session_id` and `owner_id`
identify the actual assigned session/runtime. A correlation marker alone is
not receipt evidence. Missing, opaque or wrong task identity stays incomplete.
Each event uses the session context established before it, starting from the
previous cursor; a later `session.start` cannot retroactively attach earlier
receipt, progress or delivery to that session. Incremental and bounded batches
retain this context without storing another event history. An old session
start may establish resumed context, but events predating the assignment do
not acknowledge or count its work. Future-dated events reject.

| State | Actual observation |
| --- | --- |
| `assignment_sent` | Matching native `tool.execution_start` dispatch |
| `assignment_received` | Matching session's native `user.message` with the assignment marker and matching `parentAgentTaskId` |
| `progressing` | A separate post-receipt `tool.execution_start` |
| `committed` | Git observes the delivered SHA as real worktree HEAD |
| `handed_off` | Native `assistant.message` delivers `{"handoff_result": ...}` after the commit observation |

`subagent.started` does not imply receipt or progress.
`tool.execution_complete.success` is transport status, not an OS exit code.
The owner emits its final result as the JSON envelope shown in the last row,
without Markdown fences; the inner object follows the result schema.
The adapter records separate observation timestamps/source IDs, not inferred
states from a single success response. A second committed handoff from the
same owner is rejected.

Bind a real owner PID using `observe --pid <pid>` or `bind_process`. Kernel
boot ID/PID/start ticks and runtime handle identify the process; PID sampling
alone cannot supply an exit status or complete peak RSS. The existing runtime
that owns the `Popen` child calls the nonblocking
`observe_owned_exit(process, identity)` and `record_process` at its completion
notification. These consume `wait4` exit/RSS data. An opaque handle, already
consumed wait status or inaccessible process remains unknown and cannot certify
budget compliance. Do not parse printed exit labels, kill unrelated processes,
or add a custom agent runtime to manufacture missing observations. Keep the
existing runtime/timeout lifetime limit; validation also rejects elapsed/RSS
overages and unretired owners.

`validate` emits a bounded verdict and exits 0 only for a ready local handoff;
rejected/incomplete handoffs exit 2. It is not merge or push authorization.
It verifies exact Git/worktree identity, strict ancestry, clean status/index,
the task-owned first-parent chain and both terminal Copilot trailers. Normal
merges of recorded exact upstream inputs are allowed: imported history needs
no task trailers, and unchanged upstream-owned paths are not task scope.
Unrecorded merge inputs, arbitrary out-of-scope resolutions, hidden index flags,
dirty/conflicting trees and stale/non-HEAD results reject.

Incremental changed lines count task additions + deletions after excluding
authorized unchanged upstream paths. This is **not** the full-PR 20K review
gate; a specifically budgeted deletion/refactor may have a large incremental
diff while reducing the final PR. Binary/unquantified changes do not become zero.
Known host-only task paths or an observed result with no task-owned paths
(such as a pure authorized import) may derive zero task ROM/RAM. Zero numstat
alone is not a resource observation: non-host mode/type changes and empty-file
additions/deletions still need actual coordinator measurements. A measured zero
is valid evidence; a missing measurement is not. The raw check captures any
justified inferred zeros in its existing measurement fields, so later reporting
does not guess from allowed scope, numstat or a removed worktree.
Protocol checks compare presence and typed parsed immutable JSON values,
not schema-version increments or source spelling. Creating/deleting a valid
JSON `null` document consumes one change; absent/absent and unchanged `null`
consume none. Key order, integral numeric spelling and valid Unicode escapes
do not change a value, but Boolean/numeric substitutions and array order do.
Comparison retains the original input bounds without re-encoding valid
Unicode into a larger escaped document.
Each declared input belongs to one protocol check. Multiple disjoint checks
are supported, and their changed-input counts sum across the assignment.
An unknown or impossible count cannot be masked by another check or by a
coordinator's aggregate measure. ROM/RAM measures refer to the same whole-task
growth and use the greatest observed value, not a sum of repeated measurements.
Missing measurements and overages reject.
The raw checker pins whitespace/diff/config behavior, disables hooks/fsmonitor/
textconv/external diff, bounds object/output bytes, and rechecks Git after checks.
Combined stdout/stderr is bounded to 4 MiB and raw processes to 30 seconds.

### Rotation, watchers and preserved recovery

A completed handoff retires that owner; a review successor has a fresh
owner/session and the accepted exact result as its assigned parent. Reservations
reject duplicate/overlapping owners, reused retired owners and multiple
successors. The state permits only one initial root per issue or non-null PR,
including after retirement/interruption: later work must use the real lineage,
not another `initial` assignment that bypasses retained-worktree recovery.
Independent issue/PR roots remain separate. Only one coordinator manages the
canonical state.

Accepted completion requires a genuinely observed zero owner exit and complete
owned RSS, not merely an exited process. Unknown exit evidence stays incomplete.
An observed nonzero or signal exit after delivery uses the existing interruption
preservation path before closing the owner; it is never accepted completion.
The same eligibility check guards optional accepted reporting. Honest abnormal
exit observations remain valid state. A committed WIP checkpoint is retained and
may still be published immediately by the existing #207 coordinator workflow;
WIP publication and accepted handoff completion are separate decisions.

One pure owner-acceptance predicate covers zero completion, assignment-to-close
wall-clock lifetime, process age and RSS in both live validation and reporting.
A small process age cannot override an exceeded wall-clock limit. Validation
rechecks eligibility after focused checks and records a new close and verdict
at the same observation instant, so time spent checking cannot evade the limit.
Future or negative owner/lifecycle/clock chronology is invalid, not zero elapsed
time. Closed history uses its captured interval without requiring its worktree;
an open report whose recorded clock predates assignment is incomplete rather
than silently clamped to zero.

Register the existing direct shell watcher with `reserve_watcher`, binding
repository/run/attempt/head and an actually running process. Boot/PID/start
identity is unique across all owners and watchers regardless of runtime handle,
run or attempt; an exited or unreaped zombie cannot be reserved as active.
Use the established
`timeout 90m gh run watch <run-id> --interval 30 --exit-status` command through
the shell runtime; no reasoning agent waits for it. `finish_watcher` consumes
an owned exit observation or retains unknown exit status. A replacement watcher
cannot overlap the old process. Active records require a running observation
without an exit code; completed records require an exited observation, whose
exit/RSS may remain unknown. `reconcile-run` makes one bounded `gh api`
query of that exact run/attempt: authoritative success survives watcher timeout,
failure/cancellation remains failure, nonterminal remains pending and query or
identity errors remain unknown. None changes Build topology or existing
candidate-evidence/metadata classification.

On an owned interruption, use `observe --interruption` or
`preserve_interruption`. SIGKILL needs an actual exit observation; call it OOM
only when bounded kernel evidence matches boot/PID/time. Permission failures
remain “OOM unconfirmed.” Retain the original linked worktree, index, staged/
unstaged/untracked content and modes; apply Git's existing retention lock,
which #208 cleanup respects. No reset, deletion or recovery-copy engine runs.
Already finished failures remain failures; a registered running check stays
incomplete. `begin_check` lets the existing executor register its real child
before asynchronous execution.

The existing interruption record carries one local-only
`retained_data_sha256`: a bounded aggregate of the actual index, Git-enumerated
dirty/untracked file bytes, symlink target text and relevant file/directory
modes. Clean committed content still derives from Git and is not hashed into
a source/blob ledger. The existing nofollow reader streams mutable content,
with a combined 4-MiB/30-second bound and at most 256 mutable paths. A bounded
32,768-entry nofollow type scan catches special files that Git omits; it does
not read clean content or follow symlink targets. Unsupported types, unsafe
metadata, changed data or an observation overage hold without deleting work.

An interruption has a non-null close at the same parsed timestamp, after its
assignment and lifecycle observations and before any replacement assignment.
Loaded contradictory/future times reject, so recovery cost cannot become
negative. The schema requires the close field; timestamp comparison and
replacement chronology remain runtime checks rather than authentication.

Only after the owner is terminal and preservation succeeds may one fresh
replacement reuse that same worktree and exact HEAD. Reassignment reobserves
and compares mutable integrity, not just HEAD and status pathnames.
Changed/missing retention
state or an un-lockable primary worktree gives a precise hold without deleting
anything. Repeated replacement is not automatic. The retained physical worktree
is not an authenticated content snapshot; coordinator ownership prevents other
writers while it is reassigned. Existing completed-worktree cleanup remains
the sole cleanup mechanism.
If observation fails after the retention lock is applied, the owner stays open
and the lock protects the original worktree; retry may reuse that same reason
after safe observation is possible. No second recovery store or copy is made.

### Optional operational report and compatibility

Reporter `--handoffs <state.json>` first validates the unchanged v1 baseline,
then emits `{schema_version: 2, baseline: <v1>, implementation_handoffs: <metrics>}`.
Without the flag, v1 fixtures, arguments, expected values, seals and deletion
proofs are unchanged. Counts include accepted/rejected/interrupted/in-progress,
stale responses, lifetime, measured RSS, unknown RSS, native coordination turns,
recovery cost and separate authoritative CI states. Missing observations never
become zero measurements or authenticated offline delivery proof. A locally
accepted handoff is not a merged/delivered PR. Each watcher row uses its own
run/attempt observation and query error: a newer success on the same head does
not relabel earlier failures, cancellations, pending or unknown observations.
Current-head CI selection elsewhere still selects the latest run/attempt.

Live handoff validation and accepted reporting use the same pure
focused-check/resource evidence predicate: required evidence references,
check identity, completion, observation times and ROM/RAM/protocol budgets
must all agree. Reporting uses the recorded verdict's observation time rather
than requiring historical Git/files or rerunning checks. Mutating an accepted
record to missing/null/mistyped/over-budget observations cannot leave it
counted accepted. Complete captured host-only, pure-import and measured
non-host records remain reportable after their worktree is removed.
An older accepted label without necessary captured measurements is incomplete,
not permission to invent zeros; reobserve it only if the actual inputs remain
available. Honest rejected/in-progress records with unknown measurements remain
reportable. None of these consistency checks authenticates the stored data.

Dependencies: #176 and #216's locked `jsonschema` test environment, delivered
by merged PR #217. This work was a genuine child while that dependency was
open; that stack is history and the current immediate base is `master`.
No #205/#211 dependency or #179 API is adopted. Dependent: #181.
Conflicts: reused owners/watchers, arbitrary candidate execution or broker
formats. No game/runtime, save, ROM/RAM content,
locale, generated data, modern/archival profile, Build topology or final-gate
change. Revert the dedicated #178 change if needed; owner publication,
metadata-event handling, immediate checkpoint publication and #208 cleanup stay.

The complete human/automated procedure is
[TC-WORKFLOW-AGENT-HANDOFF-001](test-cases/workflow-governance.md#tc-workflow-agent-handoff-001-validate-bounded-exact-sha-agent-handoffs).
