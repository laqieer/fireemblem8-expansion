# Workflow-governance cases

These source-only procedures cover the repository's agent delivery policy.
They exercise documented orchestration contracts without dispatching a
workflow, using credentials, or changing ROM behavior.

## TC-WORKFLOW-HOST-PYTHON-DEPS-001: Bootstrap isolated schema-test dependencies

- **Feature / originating issue:** `workflow-governance` /
  [issue #216](https://github.com/laqieer/fireemblem8-expansion/issues/216).
- **Supported configuration or artifact:** source checkout, CPython 3.12,
  Linux x86_64 with glibc >= 2.17, and the OS `python3-venv`/`ensurepip`
  component. No ROM, emulator, ARM compiler or credential is needed.
- **Prerequisites and clean starting state:** ordinary access to trusted PyPI
  during initial setup; run from the source root. Ensure
  `build/host-python-missing` and `build/host-python` do not already exist.
  Never replace somebody else's environment. The bootstrap and regression
  fixtures use exclusively owned paths under this checkout's `build/`.

### Actions

1. Reproduce the undeclared-dependency failure before setup:

   ```bash
   /usr/bin/python3 -I -m venv --without-pip build/host-python-missing
   build/host-python-missing/bin/python3 -I -c 'import jsonschema'
   ```

   Expect exit 1 with `ModuleNotFoundError`, not an ambient-package success.
   Remove only this owned probe: `rm -r -- build/host-python-missing`.
2. Run the actual CI bootstrap and inspect its isolated result:

   ```bash
   /usr/bin/python3 -I scripts/host_python.py create
   build/host-python/bin/python3 -I scripts/host_python.py check
   ```

   Expect exit 0 and a JSON report naming the owned environment, exact
   committed package versions, draft `2020-12` and format `date-time`.
3. Run the focused automated positive and adversarial replay:

   ```bash
   build/host-python/bin/python3 -I -m unittest discover \
     -s scripts/workflow_pilot/tests -t . -p test_host_python.py -v
   ```

   Valid leap-day/offset/fractional RFC3339 timestamps pass. Non-leap dates,
   invalid hours, malformed timestamps, wrong prefix-item types and surplus
   items fail. Removing the optional format checker or making it accept
   everything must fail the probe, not silently skip format validation.
4. The same suite replays verified offline installs, corrupts an actual wheel,
   removes a required wheel, supplies a CPython-3.11-only wheel to 3.12, and
   omits optional or transitive dependencies from a fixture lock. Every
   rejected input must fail explicitly. Unsupported platform profiles fail
   before environment creation. Existing/symlink targets remain unchanged.
   A real installation into a fixture-only user site is visible to ordinary
   Python but cannot satisfy the fresh `-I` interpreter or dependency check.
   Hostile pip configuration/install-target variables cannot redirect setup.
5. Exercise the existing reporter entrypoint, then the parsed Build and local
   argv mirror contracts:

   ```bash
   build/host-python/bin/python3 -I scripts/workflow_pilot/isolated_launcher.py reporter-tests
   python3 -m unittest tests.workflows.test_build_ci_topology \
     tests.upstream_port.test_verify.VerifyGatesMirrorWorkflowTests -v
   ```

   Reporter discovery uses the installed interpreter; its existing immutable
   Git-authority requirements remain mandatory. A shallow/local clone missing
   the baseline objects must restore the
   [documented Git authority](../workflow-pilot.md#build-event-classification-and-candidate-evidence),
   not weaken reporter checks. Parsed setup and reporter paths agree; removing
   bootstrap/venv support or switching back to system Python is rejected.
6. The regression removes only its own UUID-named fixtures. When finished,
   remove your own bootstrap environment with
   `rm -r -- build/host-python`, or retain it for subsequent host checks.

### Expected result

Every schema dependency comes from the committed pinned/hash-verified closure
in a fresh environment, with no system/user-site reliance. Required optional
formats really validate adversarial data. The bootstrapped import and reporter
discovery pass.

### Negative control

The pre-fix clean import fails. Damaged, missing,
incompatible, unpinned, unhashed and redirected inputs cannot produce a
successful environment check. Reordering/commenting the same lock records
stays valid; preserving words while bypassing validation does not.

### Interactions and save compatibility

Dependents are #178/#191 handoff and #205/#211 broker schema tests, not new
protocol implementations in this case. Dependencies are OS Python/venv,
trusted PyPI at setup time and the committed wheel closure. Only `date-time`
is the shared required optional format; new formats need new closure/probe
coverage. Other interpreters, architectures and libc profiles are unsupported.
There are no game, save/config, ROM/RAM, localization, generated-game-data,
modern profile or archival compiler interactions. No feature gate is needed.

### Automation

Automation is `scripts/workflow_pilot/tests/test_host_python.py` plus the
existing parsed workflow topology/argv mirror suites; real pip installs,
metadata closure and schema execution are the evidence, not raw source text.
Full/metadata routing, required contexts, permissions, checkout authority and
every candidate/master gate are unchanged.

### Cleanup and limitations

Tests remove only their UUID-owned fixtures. Retain an explicitly owned
`build/host-python` environment for later checks or remove only that environment
afterward as described in step 6. No visual/audio/manual-only criterion applies.
This setup cannot supply missing Git authority or discharge
the consumers' independent protected-principal/deployment requirements.

## TC-WORKFLOW-REVIEW-FIRST-001: Gate expensive Builds after accepted review

- **Feature / issue:** `workflow-governance` /
  [#181](https://github.com/laqieer/fireemblem8-expansion/issues/181).
- **Profile / prerequisites:** source checkout with the existing locked host
  Python, Git and local process controls. Local cases own Git repositories,
  real handoff processes and disposable HTTP responses below `build`.
  Remote exercise is performed only by the delivery coordinator with normal
  repository-owner credentials and existing review/runtime adapters.
- **Compatibility:** no gameplay, save/config, locale, generated game-data,
  ROM/RAM, compiler profile or archival change. Dependencies are #176–#179;
  #180/#206 are independent and #196 extraction is out of scope.

### Actions

1. Run the existing runner:

   ```bash
   build/host-python/bin/python3 -I -c \
     'import sys, unittest; sys.path.insert(0, "."); unittest.main(module=None)' \
     scripts.workflow_pilot.tests.test_adaptive_gate \
     scripts.workflow_pilot.tests.test_candidate_identity \
     scripts.workflow_pilot.tests.test_live_stack \
     scripts.workflow_pilot.tests.test_refless_runs \
     scripts.workflow_pilot.tests.test_live_pause \
     scripts.workflow_pilot.tests.test_coordinator_local -v
   ```

   An existing equivalent locked interpreter may be supplied instead; do not
   create another dependency bootstrap.
   The dispatch-discovery controls run the actual embedded Bash/jq program
   against owned Git and complete provider fixtures. One eligible candidate
   plus a same-named fork must bind in either order. Multiple eligible
   candidates, unknown nodes, mismatched counts and incomplete pages must stay
   unbound; no real fork or repository mutation is needed for these controls.
2. Select small/low-risk, exactly 2,000 lines, greater-than-2,000 lines and each
   named high risk. Expect concurrent for the first two and review-first for
   large/named-risk candidates. Missing/unknown records and paused decisions
   keep the broader workflow with explicit reasons.
3. Validate an override from its actual committed introduction and reviewed
   Git trees using #176. A pre-review override can change timing; a late or
   missing introduction is unknown/broader, never authenticated by a reason
   string. Independent local review is not waived by the timing override.
   Exercise `route_event` itself with actual immutable decision/commit/review
   responses: a valid override changes the large-change route, while late,
   missing, changed or unavailable first-reviewed authority remains unknown
   and uses the broader workflow.
   Exercise actual registered generated/catalog files, Markdown and whole-file
   deletions through the production route with native Git-derived diff facts.
   A large runtime/archival/`none` claim, mixed runtime work, unregistered
   generated path, partial runtime deletion or unrelated decision-file edit
   is known **ineligible** and keeps ordinary review-first size timing.
   Missing/stale/truncated/paginated file authority or a head/base-ref change
   during observation stays **unknown** and broader. A risk label/reason cannot
   substitute for these facts; named high risk always remains review-first.
   The bookkeeping exception covers only this PR's decision entry, not another
   decision, artifact or arbitrary metadata file. No deletion percentage or
   blanket source-directory exemption is used; all final gates still apply.
   Advance the base with an independent decision edit: genuine documentation
   remains eligible, but a candidate's unrelated decision edit cannot be hidden
   by a matching live-tip change. Compare bookkeeping at the actual merge base.
4. Execute the actual parsed Build job guards and preflight shell steps.
   Initial review-first runs only the fast path, while concurrent, dispatched
   full and master events retain all four comprehensive worker jobs and all
   nine total jobs. Wrong head/base/decision identity fails preflight.
   Execute the no-checkout dispatch bootstrap with a pre-feature default tree,
   a genuine feature-containing integration base and a child whose launcher
   would fail if executed. Require exact base checkout and a parsed
   PR/head/merge-base binding from the isolated classifier. Absent, ambiguous,
   wrong-repository/ref/head, unavailable and changed-base observations must
   remain unbound or fail. An old base and plain manual dispatch remain broad;
   the deployed root-PR path still binds. These controls do not dispatch CI.
5. Complete a real local #178 handoff, then consume typed #179 task and review
   observations and exact security checks. Fully triaged zero-finding review,
   zero unresolved conversations and clean exact security permit one full
   dispatch. Observe its exact run/attempt and all full jobs before merge
   eligibility. Missing local, review, security or objective/manual evidence
   remains a hold.
   Also exercise already-committed coordinator-owned work with no assignments:
   explicitly register raw Git plus every semantic local criterion, capture
   their real native results, and require all checks before local readiness.
   Use actual config/baseline/document validators for the excluded fixture.
   Use the same actual coordinator/implementation owner in `ReviewSession`
   when applicable. Self-review must reject before task launch; a distinct
   read-only reviewer must still satisfy task/head/scope and completion bounds.
   Reject raw-only/pass-label registration, incomplete/failed captures,
   changed definitions/check sets, head/base/worktree drift and stale native
   availability. An applicable incomplete or invalid delegated owner still
   blocks this alternative. No fixture commit, owner, receipt or LLM PID is
   manufactured to obtain local proof.
   If host stop settings are unknown, retain explicit nulls in a bounded
   `plan` with concrete recovery steps; never fabricate Boolean settings or
   use unknowns for `always-on`. Expired coverage and native interruption
   observations still reject availability.
6. Accept a valid finding: record abandonment and deny full dispatch/merge.
   Later clean or cancelled/successful Build results cannot revive that head.
   Resolve a false positive before acceptance, refresh actual facts and
   retriage; the same un-abandoned head can become clean. A failed security
   check remains unclean until actual successful check evidence is observed.
7. Exercise stale review/security/head/base, unresolved threads, duplicate
   full runs, changed attempts, early owner dispatch and duplicate watchers.
   All reject admission. Record dispatch before the simulated network call;
   a failed/unknown delivery must not retry into a second dispatch.
   Lose the second state write after an accepted POST. Reconcile through the
   actual parsed unique full run and original watermark without repeating POST
   or inventing an HTTP acknowledgement time. Zero/multiple/unclassified,
   earlier and wrong identity/attempt/workflow observations remain uncertain;
   abandoned/superseded heads may be recorded only for cleanup.
   Combine unrelated base-tip movement with a queued same-head/branch PR run:
   it remains visible and blocks dispatch/merge until its immutable candidate
   marker establishes the binding. A confirmed different marker stays unrelated.
   Observe a real parsed queued same-head/branch dispatch with no jobs/binding,
   both alone and beside a completed full success: it must hold both dispatch
   and merge until classified. Compare a fractional native reservation with
   the provider's same-second run creation: retain both actual values and
   accept the legitimate correlation, while earlier seconds, old watermarks,
   changed attempts and duplicate identities still reject.
8. Compile actual Git ancestry for an unrelated master advance. The frozen
   merge base remains valid and no candidate is cancelled. A changed head,
   base ref or unique merge base supersedes the old binding.
   Route the immutable raw event against that advanced live tip: preserve its
   original base/head output and reject tampered event/ref data. After a real
   same-head base rebind, fully triaged history plus a fresh clean review and
   fresh security can proceed; old untriaged/unresolved content or accepted
   valid findings must still hold or abandon the candidate.
   Fast-forward a real integration base into an intermediate ancestor of an
   unchanged candidate head, keeping both refs unchanged. Retain old/new
   frozen-base records, reservations and parsed runs. Complete the new native
   local checks and fresh clean-review/security evidence; reconcile only its
   exact full identity. The old snapshot may record its own run for abandoned
   cleanup, never admission. Retarget a base ref with the same frozen base.
   Current v1 witnesses must include the actual historical ref; legacy ref-less
   or unmarked runs remain unproven, regardless of today's mutable PR association
   or whether the old record was assessed. Test both concurrent and reserved
   marked/unmarked cases, admitting only the actual current complete witness.
   Send the same real Git head through metadata transaction parsing and the
   actual inline summary after a ref-only retarget. Legacy ref-less and absent
   markers must remain unbound; canonical exact-ref witnesses succeed, while
   wrong/noncanonical/invalid-UTF-8 refs fail. Metadata-only results never
   replace a full Build, and a newer unproven full run blocks reuse of older
   success. Keep the 32768-byte source bound and behavior-backed raw/AST guards.
   Zero/multiple/unclassified runs, wrong
   head/base/ref/workflow/bound attempt, missing acknowledgement and partial
   identity lookups cannot authorize a candidate. Unrelated base-tip movement
   with the same unique merge base still permits normal dispatch.
   Exercise actual root, depth-one, depth-two and exceptional depth-three
   decision/parent chains through production routing. The shared #176 validator
   must reject missing parents/exceptions, bad depths, self/multi-parent cycles,
   branch mismatches, unavailable parent decisions and unsynced/moving parent
   heads. Read each parent's own committed decision; never supply a fabricated
   parent or use the child's copy as parent authority. Preserve the genuine
   feature-parent bootstrap when the default tree predates adaptive gating.
9. Execute the existing metadata summary against disposable local HTTP
   responses for a full dispatch and its current merge base. It retains
   complete nine-job success; missing/wrong/foreign marker or stale base
   cannot stand in for full candidate evidence.
10. In owned Git repositories, publish an unpaused default decision and create
    H, then normally publish a pause to the current default branch without
    changing H or its frozen merge base. Run the production reader and require
    broad timing for unchanged H and a new head. A paused excluded sibling
    counts; a paused immediate stack parent is not the global source.
    Validate every collection member and the actual repository/default
    ref/commit/regular blob; missing, malformed, truncated or moving data is
    unknown/broad, never unpause or admission authority.
    Use controlled #176 incident fixtures and actual native reproductions to
    exercise `pause_pilot`, local `safety_publication` hold, ordinary non-default
    branch preparation, local bare branch publication/merge and confirmed
    current-control readback. Never manufacture a real repository escape or
    change production master for this test. Ordinary pre-merge findings,
    arbitrary event names, failed attribution and wrong automatic-master
    event/branch/workflow/head/attempt/outcome must not create a latch.
    Publication failure retains the local hold without claiming global visibility.
    Give an old exact preflight one reserved full fallback while quality is
    pending; inspect the persisted watermark before the only input-free POST.
    Unknown control/history, unbound original review context, missing native
    coverage, active/unknown/duplicate full work, abandonment and architecture
    holds must block inappropriate scheduling. Both initial concurrent PR full
    ownership and initial review-first reserved ownership survive pause/unpause.
    Prove explicit native recovery, current complete master/security evidence
    and normal owner publication before unpause. Missing data and unrelated
    green runs cannot unpause. All final review/security/local/family/manual/
    candidate/master gates remain mandatory.
    Exercise marker lifecycle through the real candidate-run parser: absent,
    queued/in-progress-null, successful, terminal non-success and malformed/
    contradictory cases. Valid non-success remains unbound history; only
    successful exact witnesses bind, and newer unbound full work blocks reuse
    of old green results.
    Use the actual `assess_observed` callback inside `dispatch_full`, with
    complete controlled API responses—not a reducer-only stub. Empty, partial
    and coherent pending security quality permits the known-pause fallback
    while preventing merge even after all other quality is ready. Wrong app/
    head, invalid fields/pagination/lifecycle, unknown authority and accepted
    findings retain their strict dispatch/abandonment behavior.

### Actual disposable-PR exercise — coordinator only

Do not run these remote mutations from an implementation agent. Never count
these PRs as merged pilot samples.

1. Publish the tested implementation checkpoint immediately and obtain its
   required independent review before hosted review. Record its full SHA as
   `CANDIDATE`. The introducing PR's base predates adaptive gating, so its
   normal full Build remains required. Do not call that bootstrap run a
   review-first observation.
   The current PR221 root decision is explicitly pilot-excluded and has no
   override. Its new presence is not historical pre-review proof. Validate
   the existing schema/actual root relation and unchanged baseline before
   syncing a genuine child to the committed parent.
2. For a pre-merge exercise, create disposable child branches against the
   actual introducing PR's dependency-ready head branch, not an invented
   installed helper or a candidate bootstrap. Record the genuine parent PR
   and depth-one stack. If external CodeQL/GHAS does not produce exact checks
   for that non-default base, stop that exercise without fabricating them.
   Before full dispatch, verify the authenticated bootstrap selects that
   genuine parent's exact SHA rather than the pre-feature default branch.
   Do not deploy the feature dormant to work around missing classifier
   authority or waive this pre-merge exercise.
3. In an owned fixture worktree, make a real documentation change and open a
   **draft** disposable PR with `gh pr create --draft --base "$EXERCISE_BASE"`.
   Its first head may have no committed PR-number decision and correctly
   receive the broader full workflow. Record that exact bootstrap head/run.
4. Once the PR number exists, append its record to the existing decision file:
   `risk_boundaries: ["lifecycle"]`, `gate_mode: "review-first"`, the actual
   root/stack record, `threshold.triggers: ["risk-boundary"]`,
   `override_history: []`, and
   `pilot: {"included": false, "disposition": "excluded"}`. Commit and
   owner-push a **new** head. The explicit historical-cohort projection keeps
   baseline v1 unchanged. Observe the new head before marking the bootstrap
   head superseded in the existing coordinator state; only then may its full
   run be cancelled.
5. Confirm the new exact-head Build has `review-first-classifier`, successful
   fast `host-tests`/`build`, skipped extended/legacy, and the explicit pending
   full `summary` failure. Register this candidate before requesting reviews.
   Complete its actual applicable local proof: a delegated terminal handoff,
   or explicit registered native checks for coordinator-authored work. Never
   manufacture an assignment, worker budget or process measurement. Complete
   independent review with a reviewer different from coordinator and implementer
   (who may be one actual owner), and request
   exact-head Copilot while the existing security checks run concurrently.
   Inspect complete review content and all threads; do not infer clean from
   COMMENTED, a heading or zero new inline comments.
6. In the trusted coordinator, use the actual objects already collected:

   ```python
   from scripts.workflow_pilot import adaptive_gate as gate
   from scripts.workflow_pilot import coordinator_observations as observations
   from scripts.workflow_pilot import pr_metadata

   client = pr_metadata.GitHubClient("/usr/bin/gh")
   pr, changed_lines = gate.fetch_candidate(client, repository, pr_number)
   decision = gate.fetch_decision(client, pr, changed_lines)
   with observations.locked_state(state_path) as state:
       gate.begin_observed_candidate(client, state, pr_number)

   def assess_for_dispatch(state):
       current, _ = gate.fetch_candidate(client, repository, pr_number)
       identity = (current.number, current.head_sha,
                   gate.frozen_base(client, current), current.base_ref)
       record = gate.find_candidate(state, identity)
       assessment, runs = gate.assess_observed(
           client, state, record, review_session, tuple(review_session.rounds.events),
           review_tools, family_evidence=validated_family_inputs,
           accepted_security=accepted_security_findings,
           criteria_ready=existing_objective_and_manual_gates_complete)
       return record, assessment, runs

   gate.dispatch_full(client, state_path, pr, assess_for_dispatch)
   ```

   `review_session`, `review_tools`, triage, family inputs, security findings
   and criterion completion must be the real existing coordinator observations,
   not success-shaped JSON fixtures. Use one locked state transaction for each
   normal assessment and the existing canonical-comment updater for its result.
7. Observe the actual dispatched run ID/attempt after the saved watermark.
   Record one existing #178 watcher and run exactly
   `timeout 90m gh run watch "$RUN_ID" --interval 30 --exit-status` as an
   attached asynchronous shell. No reasoning agent waits. Verify exactly one
   input-free full dispatch and all nine completed jobs, with no publisher
   on the PR/dispatch. Reassess fresh review/security/criteria before eligibility.
8. Owner-push a real second change. Observe supersession and prove that the
   earlier full success and earlier review/security cannot authorize the new
   head. Do not cancel any independent PR merely because master moved.
   Exercise an accepted real local/remote finding on a separate negative head;
   persist its abandonment before cancelling any associated full run. A known
   negative/WIP head is not a successful local validation sample. Repair it
   using #179's actual sibling-family evidence before another clean head.
9. Repeat with a separate small/low-risk record in concurrent mode. Confirm
   that the initial event runs the complete graph without waiting for review,
   while merge eligibility still requires the same final gates. Test pause by
   updating the existing record and observing the broader route on a new head.
10. Save exact PR/head/base/decision/run/attempt/review/check identities and
    actual commands/results in the canonical evidence comment. Close, do not
    merge, disposable PRs; retire only their owned processes/worktrees through
    existing cleanup after no active work remains. Main separately verifies
    automatic full master Build and its real build-once publisher after the
    implementation merge.

### Expected result

Adaptive timing saves only unneeded early comprehensive runs. A candidate can
be merge-eligible only after actual clean review/security, complete local and
objective/manual evidence and one exact full success. Master retains the
complete automatic graph and real publisher.

### Negative control

The pre-feature workflow starts full Build concurrently for every code event.
Missing/unknown decisions intentionally retain that broader behavior. Valid
findings, stale or duplicate identities, missing observations and cancelled
runs never become merge evidence in either timing mode.

### Interactions and save compatibility

Reuse #176 decisions/metrics/pause, #177 metadata continuity, #178 local
handoffs/watchers and #179 review/family/hold authority. #180/#206 remain
parallel. No gameplay, save/config, localization, resource allocation,
modern/archival or manual audiovisual compatibility changes.

### Automation

`scripts.workflow_pilot.tests.test_adaptive_gate` runs real local Git/handoff,
typed state, HTTP-response and actual preflight/summary shell controls. The
`scripts.workflow_pilot.tests.test_coordinator_local` selector exercises
registered coordinator-owned native checks and their negative controls. The
`scripts.workflow_pilot.tests.test_live_pause` selector covers current-control
identity, causal native attribution, owned bare publication/readback, schema
and the unchanged-head fallback/ownership family. The
existing topology, publisher, metadata, schema and review selectors cover
their directly coupled integration contracts. The coordinator records the
actual disposable-PR exercise separately; fixtures are never pilot samples.

Load `test_live_pause` and `test_adaptive_gate` through ordinary unittest
module collection. Their case IDs must be disjoint; fixture reuse must not
collect imported adaptive-gate cases a second time. Run
`test_live_pause.DiscoveryTests.test_module_discovery_contains_only_owned_cases`,
then both original module suites. Every original case remains in its owning
module. Restoring a global imported fixture TestCase must make the collection
guard fail. Remove that temporary binding afterward; do not drop scenarios or
change CI budgets to reduce the count. This is source-only collection evidence,
not a remote pause exercise or a claim that the full host workload fits.

### Cleanup and limitations

Local HTTP/task records are controlled fixtures, not actual GitHub reviewer
launches or remote delivery evidence. The real exercise above remains required
and is owned by main. There is no subjective manual-only criterion for this
host orchestration feature, no owner-dispatch prevention guarantee, and no
same-UID sandbox or authenticated receipt claim. Three weeks or 20 real
post-deployment merged pilot PRs plus measured efficiency/non-inferiority are
future promotion criteria; no fixture or baseline refresh substitutes for them.
The pause differential uses owned local publication and controlled provider/
incident data. Main may separately record an actual unpaused provider read
for endpoint/permission evidence; it is not proof of an actual escaped defect.

## TC-WORKFLOW-REVIEW-FAMILY-001: Expand valid findings across complete sibling families

- **Feature / originating issue:** `workflow-governance` /
  [issue #179](https://github.com/laqieer/fireemblem8-expansion/issues/179);
  ordinary subprocess cleanup regression
  [#223](https://github.com/laqieer/fireemblem8-expansion/issues/223).
- **Supported configuration:** Linux source checkout, the existing #216 locked
  CPython 3.12 host environment, Git and native GCC for host coverage; the
  existing modern Build lane's ARM GCC/binutils for mandatory object positives.
- **Prerequisites and starting state:** run from the repository root. Follow
  the [existing host setup](../workflow-pilot.md#isolated-host-python-dependencies)
  if needed. Tests create only owned source copies/Git histories below
  `build/review-family-*` and `build/review-process-tests-*`. No live PR,
  credential, ROM, emulator, protected installation or new agent backend is
  needed. CLI fixtures invoke the
  independently trusted test checkout's existing fixed launcher outside
  the owned candidate repository; they do not execute a candidate bootstrap.

### Actions

1. Run the focused suite with the inherited locked interpreter:

   ```bash
   build/host-python/bin/python3 -I -m unittest discover \
     -s scripts/workflow_pilot/tests -t . -p 'test_*review*.py' -v
   ```

   For the #223 lifetime regression alone, select:

   ```bash
   build/host-python/bin/python3 -I -m unittest discover \
     -s scripts/workflow_pilot/tests -t . -p 'test_review_process_cleanup.py' -v
   ```

   The preserved pre-fix control starts an ordinary sleeping grandchild through
   the real command and staged `run_obligations` path: timeout used to leave it
   live even as the staging directory was removed. The current fixtures shorten
   only test deadlines and observe actual process identity/exit state before
   directory cleanup. Repeat inner command/native and outer worker timeouts,
   closed or inherited stdio, early leader exit, full or closed stdin, output
   overage, `SIGINT` and normal `SIGTERM`. Every owned child must be terminated
   and reaped before cleanup, while an unrelated process and the caller's group
   remain alive. Positive byte input/output and real native 0/1/other exit
   classifications must remain unchanged. An unavailable cleanup observation
   retains staging and never supplies satisfied evidence.
   At the real `Popen` boundary, deliver `SIGINT` and a handled `SIGTERM` after
   successful creation but before the handle returns. Neither may bypass
   cleanup protection: interruption propagates out of the runner only after
   owned work is reaped, the caller's handler is restored, and the payload inherits no
   unintended signal mask. Repeat with a real creation error. Deny the
   kernel-handle termination operation after a real staged launch: pidfds must
   show live owned work and the directory must remain until the test restores
   termination and cleans its own processes.
   Combine that real staged termination failure with a failed subreaper-state
   restoration. Staging must still remain with nonterminal owned pidfds, and
   its unavailable diagnostic must expose both failures. Restore-only failure
   must still raise, while successful restoration returns the real output.
   Force initial pidfd acquisition failure after a real staged launch, then
   reject group termination. The pidfds held by the test must show live owned
   work, staging must remain, and the unsafe diagnostic must retain its cause.
   Repeat healthy fallback, `ESRCH`, timeout, failed wait and an already-reaped
   leader; missing-leader evidence must not authorize a possibly reused group.
   Exercise subsequent selector/descriptor/stream/mask/handler restoration
   failures during unsafe cleanup. They must not erase the hold or the primary
   diagnostic. Conversely, ordinary release errors after verified cleanup must
   not retain staging. Restore fault injections before cleaning only owned
   fixture processes and paths.
   Run the complete operation-local timing matrix: after creation, during body
   work, at normal/error cleanup entry, a second signal after the first
   interruption, reaper/handler restoration and after positive cleanup
   confirmation. Observe actual callback invocation, pidfd/exit state and the
   directory cleanup boundary, not success-shaped labels. Nonraising caller
   handlers must retain both successful output and ordinary timeout behavior;
   unrelated processes, caller handlers and masks remain intact. Combine close
   and restoration failures with genuinely unconfirmed termination.
   Interrupt the actual cleanup transition with an ordinary error while real
   children remain live: without the current runner's private positive cleanup
   notification, staging must remain regardless of exception class. Verified
   tool/timeout failures must still remove their owned staging.
   Remove or corrupt the checkout helper and give the candidate a different
   committed helper: both coordinator and staged worker must still execute the
   exact selected tool-tree bytes. Overlapping tool module instances must also
   restore the process-wide reaper setting after their owned work finishes.

2. With the supported ARM compiler/binutils on PATH (or the resolved
   `MODERN_CC`, `MODERN_NM` and `MODERN_SIZE` environment paths), run the
   separate ARM-positive selector:

   ```bash
   python3 -m unittest scripts.workflow_pilot.tests.arm_review_subjects -v
   ```

   The existing `expansion-modern-linker-check` recipe also runs this selector
   in both modern Build configurations, without a new workflow job or verifier
   gate. Missing tools fail rather than skip. The host reporter suite needs no
   ARM installation and retains all native/source-backed family coverage;
   its parsed Make contract verifies that both configurations retain the
   mandatory ARM selector and resolved tool paths. The owned non-system
   toolchain control exercises both `MODERN_TOOLCHAIN_ROOT` and an explicit
   compiler override through Make, then records actual compiler/nm/size
   execution; a system-only installation is not required.
   Inspect the actual AoE controls: every public item phase, typed route checks,
   each shape and bounded/stable target behavior runs its selected native
   driver group. Each shape requests the actual range map and checks every
   cell against an independent radius-two selected-shape bitmap. Change CROSS
   to a 3x3 square that still reports nine tiles and preserves the source
   target: its own selector must fail on geometry, then pass restored source.
   Enabled and disabled reference drivers and ARM object
   symbols/sections pass in their respective profiles, including formatting-only
   source changes. The ARM selector also runs one mixed native/ARM/generated/host
   staged scenario through the shared process runner; host-only discovery does
   not acquire an ARM-tool installation requirement. Remove EWRAM placement
   from the core, reference, then both
   in owned source revisions. Inspect the real compiled objects: each missing
   section must reject the enabled ARM member even though total EWRAM is
   below budget. Restored enabled placement, aggregate EWRAM/text budgets and
   the supported disabled omission remain good.
   Remove the real AI_NEVER rejection in an owned
   origin commit; the same probe fails before and passes restored source.
   Compile and read the actual header's phase/shape enum values. Original and
   equivalent explicitly valued/reordered declarations retain the same
   zero-based mapping. Alias `BEGIN_USE` to `0`, alias the square shape, add
   a numeric gap or change the count sentinel: compilation confirms the
   numeric difference and the finite coverage model must reject before
   advertising independent siblings. No general C interpreter is involved.
   Make the included `bmunit.h` unit-validity predicate reject valid units,
   then restore it. The native stable-slot member fails before and passes
   after; that actual header dependency is bound and its finding is admissible.
   Reporting an unaffected item-route member instead must still reject.
3. Inspect the unrelated generated-eventlist controls: resolve actual schema
   owners, validate authored references, generate and parse C output, compare
   the consumer and inventory. Introduce a nonexistent event-script reference
   in owned source; validation fails before and passes after restoration.
   Report an unchanged inventory or consumer as the defect instead: the
   selected predicate never executed, so its unavailable/zero-check
   prerequisite observation cannot claim an affected-fixed repair. The
   owner repair remains admissible with those siblings explicitly
   `prerequisite-fixed`, bound to the actually failed same-origin owner.
   Remove, invent or substitute a satisfied prerequisite owner and reject.
   Introduce real inventory drift and consumer output differences separately;
   their own predicates fail and restoration passes.
   Optional owners also run their existing semantic validators: invalid
   callbacks on selected non-reference strategies and chapter bundle metadata
   fail. Default-off reference records remain inactive. Mutate the actual
   generator, consumer parser and inventory producer, restore them and make
   formatting-only edits. Their source-bound findings must sweep the family
   and pass only after repair, without unrelated feature-wide ROM checks.
   Compare actual staged candidate bytes with every obligation's declared
   execution closure and observed Git objects for AoE, generated and mixed-subject
   workers. All staged headers, imported registry schemas and authored inputs
   must be bound. Narrowing one execution closure while another still stages
   those bytes rejects before execution; fixed tool overlays retain their
   tool-revision binding. Semantic `inputs` must match the corresponding
   standalone subject's mapping, not the mixed staging union. Break real AoE,
   generated drift and session predicates in the same origin, then repair
   them. Their correctly attached findings pass; swapping each finding's
   source path to a different subject's staged source rejects.
4. Execute the real review reducer as a source subject. The lifecycle and wire
   probes cover entries/preservation/resets/terminals and
   producers/consumers/validators/replay/stale bindings. Mutating the hold
   behavior must fail both affected families, not just a string check.
5. Drop a sibling observation after fixing the reported member. Repeat for
   all family roles, duplicate observations, added/deleted enums or files,
   unknown cases/members/probes, wrong subjects/heads/tool revisions and
   unrelated all-pass evidence. Every incomplete handoff rejects.
   Report a wire validator that already passed at the origin while a different
   stale-binding sibling actually failed. Both direct handoff assessment and
   the coordinator adapter must reject the misclassified finding; only the
   reported member's own affected-fixed row can satisfy it.
6. Make compilation/import unavailable and provide zero/skipped/unknown probe
   results. The owned missing-compiler fixture changes only its
   coordinator-selected compiler path to a nonexistent file: native checks still pass,
   enabled/disabled object observations are `unavailable` with zero checks,
   and both those observations and their omission reject handoff. No system
   compiler or global environment is changed. A passing host regression proves
   honest unavailability, not satisfied ARM obligations.
   None can count as an affected-fixed negative control. Preserve
   the distinction between actual native, parsed, host and ARM object evidence;
   no group or whole-suite result can masquerade as a ROM scenario.
   Route an ARM probe to the actual native executor and a generated probe to
   the actual host reducer in owned tool commits. Repeat with passing, broken
   and unavailable source. The returned kind stays native/host and the
   mismatched obligation rejects in all three cases. Corrupt the actual
   worker output's kind, verdict, count, detail, fields or row shape; missing,
   wrong and malformed records reject rather than being relabeled.
   Genuine executor failures retain their kind, while a worker exiting before
   any result has `kind: null`; unavailable rows always have zero checks.
7. Exercise the existing task adapter's bounded read-only role and actual
   returned task metadata. Make `runtime.start` return None, empty/whitespace
   strings, Booleans, numbers, bytes and containers. Each must reject before
   lease creation without retaining its reservation or admitting a local
   finding. Retry with a valid opaque string ID: preserve it unchanged,
   complete the review and allow subsequent acquisition.
   Denied mutation operations never reach the bound
   tool; duplicate/overlapping owners, wrong/stale/incomplete task results and
   excessive duration reject. This proves the operational interface, not
   same-UID OS containment or that a synthetic test launched a live agent.
   Disjoint scopes cannot create a second active reviewer for the same
   repository/PR even at a different head, or for the same candidate head even
   on another PR. Work with both a different repository/PR identity and a
   different head remains independent, even with overlapping scope. Completed
   sessions release both exclusions; denied starts never reach the runtime.
   Attempt head advance during the lease, including after expiration or
   a nonterminal stop acknowledgment: the session/head binding must remain
   unchanged. Only actual completed/aborted/timed-out terminal release permits
   normal head advance, while retaining the report's old origin.
   Both runtime `completed` and `read_only` must be Boolean `True`; repeat with
   false, truthy strings/numbers, containers and missing fields. Request a
   completed report with empty/partial allowed actions: both `read-candidate`
   and `emit-report` must have been observed. Their pair passes with or without
   optional `read-evidence`; any subset missing either required action rejects.
   A rejected but truly terminal task may still be explicitly aborted without
   admitting its report. Request a
   ten-file lease, return ten files, then eleven; only the first completes.
   Repeat requested/returned bounds with Boolean, fractional/integral float,
   string, negative and over-global-cap values. Only strict integer counts
   within the requested bound (at most 200) pass. Malformed adjacent runtime
   fields leave ownership active; a subsequent valid result can finish it.
   Expire a lease while the runtime still reports it running: observe the
   status, reject completion and keep fresh-reviewer admission blocked.
   Then supply actual terminal completion; reject the late report but close
   the lease as `timed-out` and allow a fresh reviewer. Repeat with the
   deadline passing during the runtime read; no late report is admitted.
   Exercise explicit abort through the runtime's existing stop capability.
   A stop acknowledgment without terminal status, read/stop failure, unknown
   status, missing capability, wrong identity or malformed terminal chronology
   must not release ownership. Once terminal evidence is observed, close as
   `aborted` without accepting a report; an already completed failed report
   needs no second stop. Normal completion retains `outcome: completed`.
   Every returned typed finding needs explicit accepted/rejected local triage
   with a reason. Omission rejects even on repaired source; acceptance enters
   the existing sibling sweep, and rejection never silently accepts a finding.
   Mutate the runtime's timestamps, actions, scope, findings, bounds and
   completion flags after finish, and during the ownership-release callback.
   The returned internal report must retain its validated immutable values,
   including nested collections and typed findings. A timely review remains
   timely; changing a recorded late completion to an earlier time cannot
   admit pre-review eligibility.
8. Exercise exact GitHub actor/head/content collection and complete coordinator
   triage. COMMENTED, approval phrases and zero inline findings remain
   untriaged until that decision. Change content or the head and require
   rejection. The live adapter's command is read-only GraphQL.
   Dismissed exact-head facts remain visible but cannot be clean authority.
   Promote a formal untriaged request to full triage without adding a round.
   Edit an actually observed clean/requested review, require invalidation, then
   retriage that same record; repeat with dismissal and a later active clean
   review. Handoffs refresh, accepted findings remain required, unchanged final
   replay and identity rebinding reject, and sticky holds survive retriage.
   Finalized `dismissed` triage closes only actual historical dismissals.
   Leave an older-head review conversation unresolved while adding a clean
   current-head review. Both readiness and exact-head clean status stay false.
   Resolve the conversation in the observed GitHub facts and reject stale
   triage. While the earlier record remains untriaged, both flags must still
   be false. Complete normal retriage and require both positive flags.
   Unrelated live-base fast-forwards preserve the actual candidate merge base;
   substituting an older ancestor or a base outside that lineage rejects.
9. Exercise the real isolated launcher from a separately trusted checkout or
   installation, using the [documented command](../workflow-pilot.md#public-request-and-execution-api)
   and explicitly reviewed tool revision stored in the candidate repository.
   Substitute the candidate's bootstrap, initializers and validator/gate files;
   the candidate marker must not run, and only intended captured Git bytes
   may execute. This PR's launcher can be independently trusted in another
   checkout; it is not authenticated by a candidate SHA or a fresh copied file.
   A same-feature reviewed binding works through this external entrypoint
   without a forced base-first adapter installation.
   Wrong source, unsafe Git
   modes, ambient hooks/import state, duplicate request fields and candidate
   success/program/trust fields fail rather than enabling a fallback.
   A real unattached tree object containing an initializer must be rejected
   before that initializer executes.
   A subdirectory supplied as candidate storage must likewise reject before a
   reviewed initializer executes; retain exact top-level, commit and source
   controls and the original same-root rules for other protected modes.
   Missing candidate/base objects and invalid ancestry in both real direct
   and isolated CLI entrypoints fail nonzero with bounded diagnostics, not a traceback.
   Test valid request JSON padded to exactly 1 MiB, one byte over, malformed
   JSON/record types, missing files, directories, symlinks and a FIFO without
   a writer. Only the exact-bound regular valid request succeeds. Both real
   entrypoints also reject a 256 MiB sparse input under a 128 MiB child
   address-space limit without allocation failure, blocking or a traceback.
   Diagnostic detail remains bounded even for a very long duplicate JSON key.
   In both direct and isolated entrypoints, force an actual local Git process
   timeout and route the GH adapter to a timed-out local child or missing
   executable. Require bounded nonzero diagnostics and no traceback or
   successful fallback. The API must translate only the expected OS/timeout
   errors, not an unrelated programming exception. No remote API is contacted
   by these controlled error-path tests.
   The direct Git timeout control must enter `trusted_review_gate.main` /
   `GitTree.git`, not call the launcher under a direct label. Verify its
   explicit bounded call raises a real local subprocess timeout while a
   default reporter Git call retains its original behavior; exercise the
   external launcher's existing timeout path separately.
10. For two consecutive generated-data remediation rounds, introduce actual
    inventory drift, then a consumer mismatch, and repair both. Parse each
    returned round's `outcome_refs`, then its outcomes' origin/candidate
    evidence references. Every expected sibling must resolve to the actual
    validated member identity, profile/probe, evidence classes, kind, checks,
    verdict and revision-bound source-object set. Verify affected-fixed versus
    unaffected states from those real worker observations; shared source sets
    must not be duplicated per sibling. Later review-content refresh must not
    rewrite the already returned assessment.
    First/second request rounds produce bounded handoffs; the third remains
    held through later heads and clean reports until a valid coordinator
    disposition names the held round/head. Preserve already-created commits
    on their assigned branch as ineligible WIP; do not delay publication or
    invent side branches.

### Expected result

Both unrelated production subjects have real positive and before/after
negative controls. Every expected member and applicable evidence class is
present. Actual execution observations determine outcomes. Source audit JSON
is not task provenance, approval or merge permission. Even a zero-finding
local review retains exact-head Copilot/security/Build and exact-master gates.

### Negative control

The original fixed #179 predicates could not detect unrelated gameplay
repairs. The source mutations now fail those actual predicates. Missing
siblings, wrong source/identity, untriaged content or an undisposed third
request never pass. Formatting-only source changes remain green. A missing
tool or failed import/compile is unavailable rather than proof of a fix.
The pre-intake-fix implementation allocated oversized files before rejection,
accepted truthy completion and over-lease counts, and relabeled wrong-routed
worker kinds. The same deterministic inputs now reject without granting
completion or evidence credit; actual correctly typed positive results pass.

### Interactions and save compatibility

Depends on #176 metrics/risk decisions, existing Git/GitHub/task/test tools and
#216's locked schema dependencies. #181 depends on it; #178 is independent and
#204 is not a dependency. Preserve #207's publication and #208's cleanup
contracts. No gameplay/save/config, generated-game-data, locale,
ROM/RAM, modern/archival, topology or required-context change occurs.

### Automation

The commands in step 1 run the existing unittest runner against the host review
test modules. Together with the ARM selector they exercise actual native/ARM,
generated-data and source-reducer observations, closed CLI behavior, independent schema parity
and coordinator task/GitHub adapters. No live task, remote mutation or broad
ROM/profile matrix is part of this deterministic test case.

### Cleanup and limitations

Tests remove only their owned fixtures; retain actual task work and diagnostic
logs. A reported unverified process cleanup retains its staged directory until
owned work is confirmed terminal; never remove it merely to hide the diagnostic.

The trusted reviewer/coordinator selects the authoritative finite model:
filenames do not establish semantic completeness. Unknown or newly changed
coverage requires a reviewed binding/model, which can be selected at an exact
tool revision in the same feature PR. A model is not another canonical case
catalog or an installation prerequisite. Read-only roles/minimal environments
are not hostile same-UID isolation. Applicable real gameplay runtime evidence
remains required for an actual gameplay change. No manual-only criterion
applies to this workflow case. The ordinary cleanup regression does not promise
containment of deliberately escaping descendants, abrupt coordinator `SIGKILL`
or arbitrary concurrent host mutation, and does not reinstate #204/#210.

## TC-WORKFLOW-REVIEW-PATHS-001: Bind coverage to actual immutable candidate-file reads

- **Feature / originating issue:** `workflow-governance` /
  [issue #243](https://github.com/laqieer/fireemblem8-expansion/issues/243).
- **Supported configuration:** Linux source checkout, the existing #216 locked
  CPython 3.12 host environment, Git, and the current workflow-pilot review
  APIs. No emulator, ROM, new runtime service or graph import is required.
- **Prerequisites and starting state:** run from the repository root. Follow
  the [existing host setup](../workflow-pilot.md#isolated-host-python-dependencies)
  if needed. Tests create only owned snapshot repositories and staging below
  `build/review-family-*`. No remote mutation is required.

### Actions

1. Run the focused model/session coverage suite:

   ```bash
   build/host-python/bin/python3 -I -c \
     'import sys, unittest; sys.path.insert(0, "."); unittest.main(module=None)' \
     scripts.workflow_pilot.tests.test_review_family.CandidateCoverageTests -v
   ```

   Use real Git BASE/head fixtures with added, modified, deleted and
   executable-mode-only paths. Route every covered read through
   `ReviewSession.read_action("read-candidate", path, side)` and the bound
   immutable `trusted_review_gate.CandidateReader`.
   Every marked candidate reader must expose both immutable Git-tree bindings;
   omitting either must reject before review launch. Unmarked generic readers
   remain compatible but cannot provide candidate coverage.
   Freeze its resolved checkout root with the exact BASE/head pair at `session.begin`,
   deriving that binding from the reader's immutable Git-tree identity rather
   than mutable public copies. Confirm returned bytes, mode, object ID, side
   and revision for head/base selections, including one explicit two-sided
   requirement and an unrelated support read.
2. In the same suite, mutate the live worktree and index after freezing the
   candidate pair. The returned bytes must remain the selected immutable Git
   blobs, not the drifted working copy. Preserve the negatives: runtime file
   counts, equal-count unrelated reads, `read-evidence` calls and a final
   runtime `reviewed_paths` list cannot prove required-path coverage.
   An empty requirement set must fail the coverage assertion both with no
   candidate reads and after a real read; generic report completion remains
   available without claiming coverage.
   Validated preview paths still spend the existing logical-path budget even
   if describe/backend later fails, while coverage remains empty until a
   complete read succeeds.
3. Exercise deleted and explicit two-sided negatives in the same session API.
   Head-side absence for a deleted path, a one-sided explicit mode/change
   read, wrong pair/root/revision/path/mode/object/bytes, unsupported Git
   object kinds, partial/failed reads and noncanonical or NUL paths must reject or
   remain uncovered. Generic existing `read_action("read-candidate", ...)`
   callers may still finish, but without trusted candidate coverage.
   Repeat the same path and side: it must retain one summary and one logical
   slot, leaving room for another path below the unchanged cap. A changed
   duplicate observation must reject without replacing the original summary.
   Two distinct validated failures at a lower `max_files` bound must spend
   both slots, so a third distinct path rejects before describe/backend. A
   retry of an already attempted path may still succeed inside that same slot.
4. Exercise the public gate adapter locally:

   ```bash
   build/host-python/bin/python3 -I -c \
     'import sys, unittest; sys.path.insert(0, "."); unittest.main(module=None)' \
     scripts.workflow_pilot.tests.test_github_review.GitHubReviewTests.test_candidate_reader_public_api_binds_exact_bytes_and_check_mode_stays_local \
     scripts.workflow_pilot.tests.test_github_review.GitHubReviewTests.test_programmatic_gate_requires_isolated_startup -v
   ```

   Describe the immutable change set through `ReviewTools.candidate_changes`,
   read exact bytes through `ReviewTools.candidate_reader`, and run
   `trusted_review_gate.main([...])` in local check mode. Pass the typed
   immutable requirements object returned by `ReviewTools.candidate_changes`
   directly to `require_candidate_path_coverage(report, requirements)`.
   A same-SHA report from another checkout must still fail that check, and
   pre-begin tampering of mutable wrapper claims must reject before the review
   starts when those claims diverge from the reader's actual Git trees. Raw
   list/dict/namespace requirement echoes cannot qualify coverage. The
   diagnostic may prove source-audit coverage, but it cannot authenticate
   coordinator task provenance or manufacture handoff eligibility from request
   JSON.
   Programmatic `main([...])` calls must reject outside isolated Python just
   like the CLI; the paired isolated call must still return its actual plan.
5. Validate the human case, catalog entry and mirrored membership:

   ```bash
   python3 -m unittest \
     scripts.docs_check_tests.test_check_docs.TesterCaseRegistryTests.test_review_path_coverage_case_is_indexed_with_focused_procedure \
     scripts.docs_check_tests.test_development_workflow_skill.DevelopmentWorkflowSkillTests.test_review_path_coverage_case_is_indexed_and_required -v
   python3 scripts/check_docs.py --check
   ```

   Keep one real evidence path per automation record. The live host
   orchestrator must route actual reviewer reads through the seam; after-the-fact
   reads, historical fixtures and final path lists do not prove the current
   review head.

### Expected result

Only successful complete reads served by the bound immutable candidate reader
accumulate canonical path/side/revision/mode/object summaries. Added and
modified head-present paths require a head read, deleted paths require the
base blob, and mode-only or explicit two-sided requirements need both sides.
Coverage is tied to one exact resolved checkout root plus BASE/head pair, the
finished report remains immutable, duplicate reads do not inflate logical-path
counts, and the public check adapter stays local-only and non-authoritative
for handoff admission. Attempted logical-path capacity is stricter than
coverage: failed validated reads spend a slot, but only successful complete
reads enter the finished coverage report.

### Negative control

Runtime file counts, unrelated support reads, `read-evidence`, final runtime
path lists, empty coverage requirements, stale/wrong candidate pairs,
wrong same-SHA checkout roots,
deleted-head absence, one-sided explicit coverage, wrong
bytes/mode/object/revision/path, unsupported object kinds, failed/partial
reads and a 201st logical path must not supply trusted coverage. Existing
generic reviews can still complete, but remain uncovered.

### Interactions and save compatibility

Depends on the delivered #179 review-family and #181/#221 trusted orchestration
contracts and on the existing immutable GitTree reader. Dependent #180 owns
the live outside-candidate adapter exercise; this case does not invent a
second backend or qualify that downstream integration. No gameplay, save,
config identity, localization, generated game-data, ROM/RAM, modern profile
or archival interaction exists.

### Automation

```bash
build/host-python/bin/python3 -I -c \
  'import sys, unittest; sys.path.insert(0, "."); unittest.main(module=None)' \
  scripts.workflow_pilot.tests.test_review_family.CandidateCoverageTests -v
build/host-python/bin/python3 -I -c \
  'import sys, unittest; sys.path.insert(0, "."); unittest.main(module=None)' \
  scripts.workflow_pilot.tests.test_github_review.GitHubReviewTests.test_candidate_reader_public_api_binds_exact_bytes_and_check_mode_stays_local \
  scripts.workflow_pilot.tests.test_github_review.GitHubReviewTests.test_programmatic_gate_requires_isolated_startup -v
python3 -m unittest \
  scripts.docs_check_tests.test_check_docs.TesterCaseRegistryTests.test_review_path_coverage_case_is_indexed_with_focused_procedure \
  scripts.docs_check_tests.test_development_workflow_skill.DevelopmentWorkflowSkillTests.test_review_path_coverage_case_is_indexed_and_required -v
python3 scripts/check_docs.py --check
```

Real Git objects, returned bytes, modes, object IDs and validated registry
records are the evidence, not source-text echoes or final runtime summaries.

### Cleanup and limitations

Tests remove only their own snapshot repositories and staging roots. No manual
criterion applies. The case validates the reusable reader/session/report/check
API only; #180 still owns the live coordinator adapter proof that routes
actual outside-candidate reviewer reads through this seam on an exact
candidate.

## TC-WORKFLOW-WORKTREE-CLEANUP-001: Remove only proven completed worktrees

- **Feature / originating issue:** `workflow-governance` /
  [issue #208](https://github.com/laqieer/fireemblem8-expansion/issues/208).
- **Supported configuration or artifact:** Linux source checkout with Python
  3 and Git; live use also requires authenticated `gh`. The automated case
  needs no credentials, network, ROM, emulator, or game build.
- **Prerequisites and clean starting state:** run from the source root. The
  suite creates exclusively owned disposable repositories and real linked
  worktrees under `build/worktree-cleanup-*`, not system temporary directories.
  It never selects the developer's registered historical worktrees.

### Actions

1. Run
   `python3 -m unittest scripts.workflow_pilot.tests.test_worktree_cleanup -v`.
2. Confirm that a clean feature branch with an exact merged PR/head, preserved
   Git ancestry, automatic green master Build, and successful relevant
   exact-commit CI is eligible in dry-run without any index, registration, or
   file change. Apply removes only that explicitly selected worktree, including
   known generated output, while its branch, main checkout, and symlink
   referents remain.
3. Exercise pending/failed/cancelled/latest-rerun and missing/stale/candidate
   CI; open PR, changed branch/head, unique/unpushed work, dirty/staged/untracked
   files, ignored saves, hidden-index changes, locks, incomplete Git
   operations, detached/current/master/active/foreign/broad-root targets,
   nested repositories/mounts, and missing registrations. Every case retains
   the workspace and reports its blocker.
4. Exercise explicit preserved paths for an agent between commands and a real
   live process whose CWD is in the fixture. Introduce local, lock, process,
   head, and remote proof changes after planning and just before removal.
   Revalidation must prevent removal without trusting prior eligibility.
5. Exercise paginated GitHub data beyond the first page, duplicate/invalid
   record identities, missing totals, oversized and incomplete collections,
   wrong repository identity, changed workflow detail, old historical green
   proof, and an explicit later master proof containing the merge.
6. In the owned fixtures, put a real bare repository with its own unique commit
   under ignored `build/`. Create a detached commit and return to the completed
   branch; also place unique objects in old/new entries of private reflogs,
   `ORIG_HEAD`, and a non-first `FETCH_HEAD` entry. All remain retained unless
   shared refs durably retain those objects. A common-directory reflog alone
   is not a durable ref. Repeat with changes after planning.
7. Configure an owned local promisor remote containing a proof commit absent
   from the fixture's shared object database. Dry-run must retain without
   adding or changing objects, packs, indexes, promisor markers, refs, or
   private metadata. This uses local Git transport, never network access.
   Unknown ignored graphics remain held, while tracked-source-backed
   `.4bpp.fk` and `.feimg[1-4].bin`/`.fetsa[1-4].bin` derivatives are disposable.
   Distinguish PNG bitmap sources, JASC `.pal` palette sources, and raw
   committed `.agbpal` files: palettes do not establish bitmap/header
   production, raw palettes do not establish `.gbapal` conversion, and PNG
   does not establish unsupported `.8bpp.h` output. Repeat those holds with
   `.fk`/`.lz` derivatives and confirm ignored contents remain unchanged.
8. Pass an uppercase full `--proof-sha` through the CLI and require the same
   canonical proof result; uppercase GitHub/programmatic identities still fail.
   A command failing without stderr reports its exit code. Simulated
   unsupported platforms fail closed; real-worktree tests explicitly skip a
   non-Linux or missing-`/proc` host rather than pretending removal succeeded.
9. Create real three-stage index conflicts from unreachable blobs, resolve
   back to the committed content, and verify clean status with nonempty
   `git ls-files --resolve-undo -z`. Preserve all three stages: saving only two
   via shared refs must still retain the tree. Saving all blobs through shared
   commit ancestry permits normal removal without losing the blobs. Introduce
   REUC objects or change only recorded modes after planning and immediately
   before removal; both invalidate eligibility.
10. Create worktree-local configuration (enabled, disabled and empty), index
    backups/locks, split-index bases, private excludes/hooks/rerere data, and
    uncommitted edit buffers. All remain byte-identical and retained. A valid
    index with an unfamiliar optional extension is retained even though Git
    can report clean status; malformed/checksum-invalid/truncated index and
    REUC data are held too. Ordinary DIRC v2–v4 indexes, including compressed
    long/non-UTF-8 paths and reconstructible caches, remain eligible.
    Empty private `refs/heads/`, `refs/tags/`, and nested ref containers remain
    eligible on both older Git and Git versions that initialize them.
    Any contained file or symlink retains the tree without changing shared
    refs or external data; repeat insertion on both apply passes.
    Repeat metadata/configuration/extension changes on both apply passes.
11. Move an owned Git worktree to a non-UTF-8 path; its real backlink and JSON
    path must round-trip to the original filesystem bytes, and eligible
    removal still works. Use fixture mount records with non-UTF-8 names and
    escaped spaces, tabs, newlines and backslashes. Unrelated mounts do not
    block; mounts at or inside the workspace or private Git directory do.
    Repeat mount/backlink drift on both apply passes. Malformed mount records
    retain with a reason rather than crashing or silently ignoring mounts.
    Report a retained non-UTF-8 ignored filename through strict ASCII JSON
    without losing either its name or file content.
12. In the owned completed fixture, commit `.gitmodules` and multiple gitlinks
    with real commit IDs, leaving their directories present and empty.
    Dry-run must preserve the index, shared/private metadata, files, and
    registrations; apply must remove normally while retaining shared refs.
    Repeat with a non-UTF-8 gitlink path containing spaces, tabs, and newlines.
    Remove a directory or stage a gitlink ID, path-set, mode, or conflict-stage
    change: each must retain, without creating paths or repairing the index.
    Put local/hidden/ignored data or even an empty child directory inside a
    gitlink; replace it with a file, symlink, or symlinked parent; inject fixture
    mount records. None qualifies, including names otherwise used for generated
    output. Existing locks and active/preserved paths still block.
    Repeat with actual initialized, separated, bare, and nested Git repositories.
    A test-owned submodule fsmonitor hook writes a marker under ordinary nested
    status as the negative control. Cleanup must neither execute that hook nor
    spawn nested Git, as observed through real Git Trace2 events, even if the
    initialized repository arrives between the empty observation and status.
    Introduce local data or replace the empty directory on both apply
    revalidation passes. Also introduce data during the empty scan and a
    zero-byte file before the final size scan: the data and registration remain.
    Replace a gitlink ancestor with a symlink while preserving the total
    allocated block count; an unvisited gitlink must still retain the target.
    Observe stat/open/scandir calls against a real test-owned external target.
    Existing symlink parents, replacements immediately before/after directory
    open, and replacements after scan entries are queued must cause retention
    without any external-target access. Keep ordinary and byte-path positives.
    Incomplete/duplicate index/tree observations and excessive gitlink counts
    must retain rather than treating an ambiguous inventory as empty.
13. For actual delivery, the coordinator first merges the task's PR, verifies
    all relevant exact-master CI and `make remote-completion-check`, then runs
    the [documented planner/apply commands](../workflow-pilot.md#completed-worktree-cleanup).
    Select only known released, quiescent task workspaces; a green inventory
    does not establish release or exclude writers. Preserve all assigned or
    uncertain workspaces, including other active sessions, throughout apply.
    Record removed paths, observed pre-removal allocated sizes, proof identities,
    and retained reasons.

### Expected result

Only explicitly selected, freshly proven completed worktrees are removed by
normal Git removal. Dry-run is inert. The master proof contains the PR merge;
an unrelated newer master failure does not invalidate a historical completed
proof. Current failing/pending reruns supersede old green evidence.
Apply refuses implicit targets or a missing preserved-workspace inventory.
No branch is deleted and no global prune or forced cleanup occurs.
Private recovery checks run during planning, fresh assessment, and the final
local check. A shared ref may protect recovery ancestry or a non-commit
object; a shared reflog alone may not. Every configured promisor/partial-clone
repository is retained before object access, including on Git 2.43.
Every REUC object participates in the same durable-object proof, regardless
of clean current status. Unique private configuration, recovery/index
snapshots, edit buffers and unfamiliar index extensions are not build output.
Filesystem byte paths remain lossless through Git, mount/backlink checks and
JSON. Fresh checks still cover non-UTF-8 path and private-metadata drift.
Present real empty unpopulated gitlinks qualify only with exact live
index/HEAD identity agreement; populated, missing, changed, or ambiguous
gitlinks do not. Empty-directory observations are repeated by local checks
and size scans, not inferred from ignored submodule status or allocated bytes.
Relative components are observed from a held worktree-root descriptor without
following symlink targets; entry/descriptor substitution retains the workspace.

### Negative control

The pre-fix workflow has no post-completion planner/apply operation, so
completed fixtures remain indefinitely. Within the regression, removing
revalidation or accepting merge/candidate success alone causes destructive
negative controls to fail; changing spelling or ordering without behavior
changes does not supply or invalidate deletion evidence.
The initial helper incorrectly accepted bare repositories and private-only
recovery objects, and a missing proof object could trigger a promisor fetch
during dry-run. These real-Git regressions must fail with those safeguards
removed; keeping the current branch's ancestry is not equivalent evidence.
Its first recovery fix still accepted REUC-only unreachable blobs, private
configuration/backups and unrecognized index extensions, and text decoding
crashed on valid non-UTF-8 target/backlink/mount names. The extended controls
fail against that parent helper, including after-plan/final-check drift.
The later blanket rejection of every `160000` index entry incorrectly holds
clean empty-gitlink fixtures; the new dry-run and normal-removal positives fail
against that helper. Ignoring submodule status without the index/HEAD comparison
or empty-directory checks fails the staged/data controls. A size-only final
check loses a late zero-byte file because ordinary Git removal does not protect
data inside an uninitialized gitlink. None of these controls authorizes actual
historical cleanup before this follow-up's own candidate and merged-master gates.
The resolve-based observer still rejected symlinks only after accessing their
external targets, and a queued full-path scan could do the same after a parent
replacement. The no-target-access controls fail against that implementation
even though it eventually reports retention.

### Interactions and save compatibility

Dependencies are Git, the existing automatic exact-master completion
definition, `gh` for live read operations, Linux process visibility, and the
coordinator's complete active-path inventory. Conflicts are premature/forced
cleanup, unreported active ownership, and mistaking candidate CI for master
proof. Immediate owner-push and WIP transparency remain separate and are
never delayed for cleanup. There are no game-feature, ROM/RAM, save/config,
generated-data, localization, modern profile, or archival interactions.

### Automation

`python3 -m unittest scripts.workflow_pilot.tests.test_worktree_cleanup -v`
executes real Git operations with deterministic GitHub responses. The existing
workflow-pilot test discovery includes this case without changing CI topology.
The registry entry binds this source-only procedure to its behavioral suite.

### Cleanup and limitations

The suite removes only its own UUID-named fixture roots. Live historical
cleanup is coordinator-owned, never a test side effect. Allocated sizes are
not exact physical freed bytes. Unknown ignored local data, squash/rebase
ancestry, private-only recovery/REUC objects, private configuration or unknown
metadata/index formats, promisor configuration, missing history/API evidence,
or incomplete process visibility are
retention blockers, not permission to force deletion. No visual/audio/manual
judgment is required; actual service availability and active ownership remain
live operational checks. Fixture process inventory includes only owned PIDs;
the live helper retains its full same-owner visibility requirement. Mount
records are simulated with byte files, never actual or privileged mounts.
These controls prove rejection of observed changes, not an atomic transaction
or writer lock. New content written inside a gitlink after its final empty
observation but before Git deletes it can be lost because ordinary Git does
not inspect that content. Apply is limited to explicitly known released,
quiescent task workspaces with a complete preserved inventory and no reassignment.
If that precondition cannot be established, retain the target regardless of
otherwise green proof. No permission lock or arbitrary-writer guarantee is added.

## TC-WORKFLOW-IMMEDIATE-PUSH-001: Publish new commits immediately and expose WIP ownership

- **Feature / originating issue:** `workflow-governance` /
  [issue #207](https://github.com/laqieer/fireemblem8-expansion/issues/207).
- **Supported configuration or artifact:** clean source checkout with Python
  3 and the existing CLI workflow instructions; no token, live PR, ROM, or
  emulator is needed for the source-only protocol checks.
- **Prerequisites and clean starting state:** retain the committed publication
  protocol, mirrored contributor instructions, and case registry.

### Actions

1. Parse the labeled "Immediate publication and visible work" protocol in
   `.github/skills/development-workflow/SKILL.md`.
2. Run
   `python3 -m unittest scripts.docs_check_tests.test_development_workflow_skill.DevelopmentWorkflowSkillTests.test_immediate_publication_protocol -v`.
3. Exercise missing/duplicate/unknown fields, delayed publication, delegated
   implementation-agent push, omitted WIP ownership/scope/blockers, and weakened
   final-gate controls. Reorder fields and visibility members without changing
   their meanings and require acceptance.
4. For real delivery, create a task commit and immediately owner-push its exact
   SHA even while review, CI, or remaining work is pending. Open/update its
   dedicated PR and canonical evidence comment with branch/head, owner, scope,
   state, remaining work and blockers. Keep incomplete work draft and retain
   all merge/closure holds. Synthetic temporary-repository test commits are
   not task publication events.

### Expected result

Commit persistence is not delayed by validation/review/CI or batch timing.
Implementation agents immediately hand off the commit without pushing.
Contributors can identify the active owner and scope from the issue/PR instead
of starting duplicate work. Exact-head review/security/Build, semantic handoff,
terminal authority, and exact-master completion remain independent gates.
A failed publication is reported as a blocker, not as remotely saved work.

### Negative control

The pre-fix workflow retained completed commits locally during independent
review. Delayed/softened publication, missing visibility, implementation-owned
pushes, or treating persistence as acceptance fails the parsed protocol.

### Interactions and save compatibility

Depends on existing owner-context push, the single coordinator, and canonical
evidence comments. It conflicts with post-commit pre-review holds and deferred
batches; pending #178/#179/#181 integration must preserve persistence versus
acceptance. No game, ROM/RAM, save/config identity, localization, generated-data,
modern/archival, or feature-gate impact. Worktree cleanup belongs to #208.

### Automation

The focused existing workflow-governance unittest parses the named external
CLI instruction format, including mutation and order-independent controls.
This is instruction data consumed by the CLI, not a claim that matching source
text proves runtime behavior; a ROM/compile check cannot establish this
instruction contract. Real owner-context pushes and visible GitHub refs/PR
state supply operational evidence during delivery.

### Cleanup and limitations

The source-only checks create no remote state and need no cleanup. They do not
grant credentials or make remote publication atomic; preserve failed-push
evidence and all final quality gates. No manual-only criterion applies.

## TC-PROBE-PRODUCER-CACHE-LIFETIME-001: Release non-reusable results after their owners finish

- **Feature / originating issue:** `workflow-governance` /
  [#256](https://github.com/laqieer/fireemblem8-expansion/issues/256).
- **Supported configuration or artifact:** source checkout with the existing
  Linux native ownership-probe toolchain; no ROM required.
- **Prerequisites and clean starting state:** start at the repository root and
  use only the tests' owned temporary repositories and scratch directories.

### Actions

1. Run the real declared-output lifetime control below. Dispatch the same
   writer twice and inspect complete stdout, bytes, mode and input receipts.
   Release one returned result while its session is still live; retain the
   other across session cleanup, then release it.
2. Run the original low-cache, creation, mapping and publication boundaries.
   Repeated writers must still execute and consume their original charges;
   an exhausted budget must not become successful because storage changed.
3. Run unreachable-producer, replacement and nested publication controls.
   Inspect actual command submissions/native events, active receipts and
   final/nested Make values and provenance rather than inferring nonexecution from an
   empty output-result cache.
4. Run the immutable-view control. Pure readers and native artifacts must
   retain same-view reuse, real metadata revalidation and correct
   CURRENT/BASE/CURRENT behavior.

### Expected result

Both output dispatches genuinely execute and return their full results.
Weak references show that a released result is no longer pinned by the reuse
cache before session close. A caller-held result remains usable after cleanup
and expires only when the caller releases it. Active receipts and publication
state retain the data they need. Returned Make observations preserve their
semantic/provenance and event records, not complete producer objects.
Pure-reader/native-artifact
reuse remains intact, and cumulative charges do not decrease or disappear.

### Negative control

The pre-fix real writer reproduction leaves two results alive in two cache
entries after caller release; session close finally releases them. Restoring
unconditional cache insertion makes the lifetime regression fail. Existing
low-cache repeated-writer failure remains required, and an unreachable writer
must never be submitted or observed executing.

### Interactions and save compatibility

Reuses the delivered producer, immutable-view and native-artifact APIs.
There are no new profile conflicts or dependencies. No gameplay, save/config
identity, generated game data, localization, ROM/RAM, modern or archival
behavior change. Publication metadata and write/no-op policy are not changed.

### Automation

- `python3 -m unittest scripts.validation_ownership.tests.test_producer.ProducerTests.test_declared_output_results_are_retained_only_by_actual_owners scripts.validation_ownership.tests.test_producer.ProducerTests.test_generated_publication_creation_mapping_cache_and_write_charges_are_cumulative scripts.validation_ownership.tests.test_producer.ProducerTests.test_unreachable_generated_input_changes_do_not_become_provenance_or_effects scripts.validation_ownership.tests.test_producer.ProducerTests.test_effectful_replacement_keeps_every_real_call_across_native_restart scripts.validation_ownership.tests.test_producer.ProducerTests.test_nested_generated_query_keeps_one_live_view_until_outer_completion scripts.validation_ownership.tests.test_producer.ProducerTests.test_immutable_views_isolate_cache_native_files_and_generated_make_outputs -v`
  -- `scripts/validation_ownership/tests/test_producer.py`.
- `python3 -m unittest scripts.validation_ownership.tests.test_foundation.FoundationTests.test_immutable_views_isolate_cache_native_files_and_static_make -v`
  -- `scripts/validation_ownership/tests/test_foundation.py`.

### Cleanup and limitations

The existing watchdog/session teardown removes only owned fixtures and checks
child, view and scratch cleanup. This removes a redundant lifetime extension,
principally after query cleanup or standalone caller release. It does not
free results while active receipts still own them, fix a remake loop, refund
traffic accounting, establish full graph fit or add a manual-only criterion.

## TC-PROBE-CONTENT-PUBLICATION-001: Preserve content-only publication and Make convergence

The [namespace-image CI extension](#namespace-image-ci-regression-correction)
also exercises this case's nested recorder: account separately for legitimate
identity-only revalidation, preserve the two real created/retained outcomes,
and reject unknown record shapes without assuming a helper-call count.
The catalog must also retain the whole content-publication suite and its
source evidence when supplemental automation is added or reordered. Run the
catalog guard below: reversing the records or preserving argv through shell
quoting/whitespace changes must pass; removing the whole-suite record while
focused records remain must fail. Record order is not a coverage requirement.

- **Feature / issue:** `workflow-governance` /
  [#258](https://github.com/laqieer/fireemblem8-expansion/issues/258).
- **Profile:** Linux x86-64 source checkout with the existing native ownership
  probe tools. No ROM, new package, privileged policy change or AI reviewer is
  required.
- **Starting state:** use a clean owned temporary fixture and the original
  byte limits. Guard against an unexpected second Make restart rather than
  waiting for a resource-consuming loop. Preserve the exact source revision
  and ordinary/native observations.

### Actions and expected results

1. Run the focused content-publication runner below. Its tiny FORCE-dependent
   included depfile and the real eventlists CLI/adapter must converge after
   the actual first restart. Repeat with a real new bundle member and verify
   selected/consumed inputs and emitted dependencies, not only exit status.
   The chapterobjectives and autoplay adapters must also use their real
   collection/rendering paths and converge.
2. Run the default unconditional same-byte writer. It still executes twice
   and performs its physical effects; the fixture rejects restart `2`.
   This is the negative control against blanket output deduplication.
3. For each real dependency CLI, seed an identical-content depfile at mode
   0600. Its next ordinary invocation must preserve inode, mode, mtime and
   ctime. Add an actual selected bundle member and require new dependencies
   in a replacement object at mode 0644; the fixture explicitly sets only
   the CLI child's umask to 0022. Then create a same-owner mode-0600 native
   publication and produce equal bytes in a
   private mode-0644 result with explicit content-only policy. The actual
   public object and mode remain unchanged, and effective confirmations,
   active/nested bindings and returned metadata report mode 0600. Different
   content and a missing output use actual replacement/create behavior and
   the produced mode. Atime is observed, not normalized.
   Graph queries also retain those effective bytes/modes in
   `MakeObservation.generated` after cleanup, including nested observations.
4. Supply invalid policies, malformed or forged effective confirmations,
   wrong slots/owners/digests/modes/identities, immutable-source or nonregular
   outputs, and failed/racy comparison reads. Each fails closed. A read error
   is not an instruction to overwrite or retain successfully.
5. Exercise nested publication and CURRENT/BASE/CURRENT fixtures. Preserve
   real effective modes/bytes and every invocation/receipt across view and
   outer cleanup. No view borrows another view's generated object.
6. The runner repeats the real eventlists registration with the original
   unconditional publication policy in its owned fixture. Require the actual
   native restart-`2` failure, two real produced results with the expected
   consumed inputs, and created/replaced publication effects, not merely a
   policy-name assertion. Keep every required creation/write/comparison/
   transport/cache charge and original bound.

### Expected result

The ordinary and native content-only queries converge with the same selected
dependencies. Effective metadata describes the actual retained or replaced
object, and default unconditional effects, every invocation, original resource
bounds and fail-closed ownership/protocol checks remain intact.

### Automation

- `python3 -m unittest scripts.validation_ownership.tests.test_content_publication -v`
  -- `scripts/validation_ownership/tests/test_content_publication.py`.
- `python3 -m unittest scripts.docs_check_tests.test_check_docs.TesterCaseRegistryTests.test_content_publication_suite_guard_ignores_order_but_rejects_omission -v`
  -- order-independent whole-suite coverage, with an omission negative control.
- `python3 -m unittest scripts.validation_ownership.tests.test_producer.ProducerTests.test_declared_output_results_are_retained_only_by_actual_owners scripts.validation_ownership.tests.test_producer.ProducerTests.test_generated_publication_creation_mapping_cache_and_write_charges_are_cumulative scripts.validation_ownership.tests.test_producer.ProducerTests.test_nested_scope_inherits_ownership_and_preserves_all_file_stat_fields scripts.validation_ownership.tests.test_producer.ProducerTests.test_invalid_request_rejects_before_any_producer_execution scripts.validation_ownership.tests.test_producer.ProducerTests.test_invalid_reply_never_retries_an_effectful_producer -v`
  -- existing producer/lifetime/protocol compatibility.

### Negative control

The preserved d091 ordinary/native preimage converges at restart `1` ordinarily
but reaches the finite restart-`2` guard through the old real eventlists
adapter. The committed runner retains that real-adapter negative control;
the separately preserved source-copy mutation also reached the same native
failure. Stable-content mode 0600 is retained by the ordinary writer;
comparing bytes plus requested mode would be incorrect. The negative control
must restore the behavioral failure, not an unrelated setup error.

### Interactions and save compatibility

Reuses the delivered producer/publication/view APIs and preserves #256's
cache-lifetime fix. Dependents are #180/#186 and #196; their integration is
separate. There are no other feature/profile conflicts, save migrations,
configuration/locale/generated-content format changes or ROM/GBA-RAM effects.
Modern debug/release and archival target behavior is unchanged. No subjective
manual-only criterion or full-workload resource claim is added.

### Cleanup and limitations

The existing watchdog/session owns all child, waiter, source-view and scratch
teardown; remove only owned fixtures after terminal cleanup. Source identities,
native events and receipts remain actual. No full graph/prefix/matrix or sizing
run belongs in this case.

## TC-PROBE-PENDING-ADMISSION-001: Preserve whole-record and lifetime plan admission

- **Feature / issue:** `workflow-governance` /
  [#260](https://github.com/laqieer/fireemblem8-expansion/issues/260).
- **Profile:** Linux x86-64 source checkout with the existing Python 3, GNU
  Make, host compiler and qualified native probe tools. No ROM, new package
  or privilege policy is required.
- **Starting state:** run from the repository root with clean owned temporary
  fixtures. Defaults remain unchanged. Only boundary-separation tests use
  `_PendingTrafficLimits`, which changes cumulative pending to 4 MiB and
  tightens time/runs to 30 seconds/64; every other maximum is unchanged.
  This is not a production or full-graph profile.

### Actions

1. Run `PendingAdmissionTests` below. Require actual repeated 128 KiB stdin
   and 16 KiB argv work, and nine real small commands, to cross 1 MiB of
   cumulative pending traffic without refunds. Inspect consumed lengths/
   digests, actual outputs, complete launch argv and monotonic byte counters.
2. Submit a whole 1,048,576-byte stdin record and an exactly sized complete
   launcher argv; both execute under the test profile. Add one byte to each
   and require the independent pending-record error before another `Popen`.
   Submit a normalized command whose argv alone fits but whose complete
   code/source/directory/policy descriptor is 1,048,577 bytes; reject before
   command launch. Keep all source/declaration checks.
3. Execute two small real variants selecting `VALUE=one` and `VALUE=two`.
   Require those actual observations, exactly two attempted Make states and
   one pending/global charge per admitted serialized state. Then submit
   eighteen states, each with a 60,000-character `VALUE`: every state fits
   individually, but the aggregate plan rejects before the first variant.
4. Use the same budget for two successive calls, each containing one
   ten-assignment state (`V0` through `V9`, each 60,000 characters).
   The first executes; the second exceeds aggregate plan admission before
   executing. Also exercise small CURRENT/BASE/CURRENT variant calls and
   verify that the same plan counter survives view restoration and cleanup.
5. Retain default/smaller pending and global rejection, invalid-size and
   terminal-budget controls. Run the existing variant-count, foreign/detached
   budget and one-session lifetime controls. Queued bytes must not become
   attempted states; failed traffic admission must not advance plan admission.
6. In separate owned source copies, remove only the pending-record predicate
   and run the exact/over stdin regression; it must fail because oversized
   stdin is actually admitted. Independently remove only the aggregate-plan
   predicate and run the pre-execution aggregate-plan regression. Its bounded
   negative stops after one actual unexpected Make variant rather than
   executing the rest of an oversized plan. Neither mutation changes limits,
   original charges, source admission or cleanup.

### Expected result

Whole pending records and total planned-state admission stay independently
bounded to 1 MiB, even with greater cumulative test traffic. Default and
stricter production limits still reject as before. `planned_state_bytes`
tracks cumulative admitted plan representation, not RSS/live storage, a
refundable pool or a second global charge. Real results, attempted state/run
semantics, source/view ownership and terminal cleanup remain truthful.

### Negative control

The preserved clean master473 preimage with the bounded test profile consumed
1,048,577 bytes of actual stdin and admitted an aggregate 1,080,522-byte plan
before one actual tiny Make variant. An explicit owned stop ended that
preimage. This demonstrates why a profile-only pending change is insufficient,
not a failing default-profile acceptance case or a full-workload result.
Removing either independent guard must restore its relevant admission failure;
comment, spelling or ordering changes are not mutation evidence.

### Interactions and save compatibility

Uses the existing `ProbeBudget`, `ProbeSession.variants`, producer and immutable
view APIs; preserves #256 and #258. The graph planner also admits each newly
queued replacement through the same budget without resetting admission across
queries or incrementing attempted states at enqueue. #196 follows that graph
contract. There are no
other feature/profile conflicts, gameplay or modern/archival changes, save or
configuration migration, locale/generated-content format change, or ROM/GBA
RAM effect. No runtime feature flag or manual-only judgment is required.

### Automation

- `python3 -m unittest scripts.validation_ownership.tests.test_foundation.PendingAdmissionTests -v`
  -- whole-record, plan, actual execution, counters and selected-view controls.
- `python3 -m unittest scripts.validation_ownership.tests.test_foundation.FoundationTests.test_pending_request_bytes_accumulate_after_completed_commands scripts.validation_ownership.tests.test_foundation.FoundationTests.test_variant_limit_rejects_before_any_variant_launch scripts.validation_ownership.tests.test_foundation.FoundationTests.test_authority_composition_rejects_foreign_and_detached_budgets scripts.validation_ownership.tests.test_foundation.FoundationTests.test_report_budget_binds_one_terminal_session_lifetime -v`
  -- existing cumulative, count and ownership/lifetime compatibility.
- `python3 -m unittest scripts.docs_check_tests.test_check_docs.TesterCaseRegistryTests.test_late_shipped_contracts_are_complete_and_fail_closed scripts.docs_check_tests.test_development_workflow_skill.DevelopmentWorkflowSkillTests.test_manual_handoff_json_contract_and_human_links -v`
  -- both closed catalogs retain missing/extra/duplicate and order-insensitive controls.

### Cleanup and limitations

Keep all processes under the existing budget/watchdog lifetime. Require no
owned children, waiters, source views or scratch after terminal cleanup;
remove only owned fixtures and mutation copies, preserving logs/preimages.
Caller-side Python objects may exist before admission. No coordinator AS
policy, aggregate host-RAM claim, new shipping profile, graph/prefix/matrix/
sizing run, native AI review or automatic retry is part of this case.

## TC-PROBE-OBSERVATION-ALLOWANCE-001: Separate cumulative observations from capsule and inventory admission

The [namespace-image CI extension](#namespace-image-ci-regression-correction)
retains this case's original 64-source/128-observation nested and failed-report
controls. Derived directories must not cause an earlier source-entry rejection
that prevents the native resumption callbacks from running.

- **Feature / issue:** `workflow-governance` /
  [#262](https://github.com/laqieer/fireemblem8-expansion/issues/262), an
  independent framework-capability root. Existing entries-only behavior is
  intentional, not a faulty kernel or permission decision.
- **Profile:** Linux x86-64 source checkout with existing Python 3, GNU Make
  4.3, host compiler, namespace/ptrace and lifecycle prerequisites. No ROM,
  new dependency, privilege policy, feature flag or calibration is required.
- **Starting state:** run from the source root with clean owned bounded
  fixtures. Ordinary defaults are unchanged. The primary control uses
  `entries=64, observations=128`; time/runs are tightened to 30 seconds/64.
  API-only diagnostic-subclass tests do not launch a larger workload.

### Actions

1. Run `ObservationAllowanceTests` below. Confirm the existing positional
   constructor prefix/defaults, omitted/explicit `None` alias and read-only
   numeric `observation_count`. Reject malformed and above-maximum ordinary
   values; retain typed diagnostic-subclass field-default validation.
2. With a real 64-entry inventory and `observations=128`, read 32 real source
   bytes in each of four separate capsules. Require correct returned bytes
   and source consumption; cumulative records are 32, 64, 96 and 128, with
   actual capsule grants 64, 64, 64 and 32. The next capsule must reject
   before launch. Native observation bytes still spend original control.
3. Repeat exact exhaustion with omitted/`None` observations and require the
   original 64-record lifetime. Explicitly lower observations to 32 and
   require that independent total. With total 65, spend 64 and attempt two
   further reads: the actual one-record grant rejects, retains the failed
   capsule's one observation, and leaves the session terminal at 65.
4. With surplus lifetime 128, stat and read 33 admitted source files in one
   capsule. Its 99 attempted records must reject at the actual 64-record cap.
   Separately admit a 65-entry inventory with `entries=64`; source capture
   must reject before any candidate capsule. Trusted runtime setup may
   precede snapshot admission; it is not candidate execution.
5. Revalidate a genuine cached result and select BASE then CURRENT over the
   same report. Require actual view-specific contents, new charged metadata,
   monotonic counts beyond 64 and exact total exhaustion without resetting
   the original clock. Exercise a real parked outer Make request, nested
   Make query and command; require actual returned values and resumption
   bounded by the original capsule cap and shared residual allowance.
6. Corrupt actual closed reports and live requests in the test transport:
   malformed/unaccounted counts, a 65-record checkpoint under a 64-record
   capsule but 128-record lifetime, and a stale second checkpoint must reject
   before unresolved producer execution. Original failure and cleanup remain.
   Also park a real outer Make capsule after 64 prior observations, perform
   32 nested source reads, and resume with a count grant reduced from 64 to 32.
   A subsequent denied source write produces a genuine failed native report.
   Leave its failure flag/error intact, but transport-edit its final count to
   the old cap of 64 and supply sufficient bookkeeping-byte evidence. Reject
   the resulting 160-record prospective total before changing the shared
   counter. Without that transport edit, retain the actual failed count and
   byte deltas, original native error and unchanged protected source.
7. In isolated mutation controls, make the effective alias always return
   `entries`; the multi-capsule positive must fail at the old 64-record
   lifetime. Independently remove only the initial per-capsule `min(entries,
   ...)` clamp. The actual 33-file stat/read capsule must then succeed with
   99 reported observations and correct output, causing its rejection test
   to fail. The native report count includes all metadata bookkeeping, not
   just the 33 successful source consumptions or 33 returned metadata records.
   Restore the supported implementation after each mutation; do not change
   source admission, byte limits, report validation or lifecycle guards.
   Separately restore the failed-status observation bypass and run the failed
   post-resumption overclaim regression. It must fail because the shared
   counter exceeds 128, not merely because an error message changes.

### Expected result

An explicit cumulative allowance separates repeated work from capsule and
inventory cardinality without changing ordinary maxima or legacy tightening.
Failed/successful work, cache revalidation, selected views and nested queries
remain one monotonic lifetime. Initial grants, checkpoints, resumption and
closed metadata use the correct shared total and capped capsule authority.
Every original byte charge and fixed file/frame/ABI/VM boundary remains.

### Negative control

The delivered bf1e preimage's real 64-record exact/remaining controls retain
the original terminal and prelaunch rejection. Independent allowance is
absent before this feature. Default/`None` retains that behavior after it.
The lifetime-alias mutation must reject genuine work beyond 64; the clamp
mutation must admit the actual 99-record capsule, not fail for an unrelated
setup or source error. No historical diagnostic is rewritten as sizing or
complete-report evidence.
The c8 review preimage additionally accepted the malformed failed terminal
count into shared accounting: 98 became 160 after the parent grant shrank to
32. The corrected path retains 98 on that overclaim; the unedited genuine
failure retains 99 and charges the original 201 remaining observation bytes
plus 20 metadata-decode bytes in the measured fixture. The overclaim is a
transport mutation of a real native failure, not a kernel-produced count.

### Interactions and save compatibility

Uses the delivered shared budget, producer, pending-admission, publication
and immutable-view foundations. This root has no unmerged graph dependency.
#180 / PR #186 is the direct dependent; #196 is transitive under Discussion
#174. No other feature/profile conflict, modern/archival or gameplay change,
save/config migration, locale/generated-data format change, ROM/GBA RAM
effect or manual-only criterion applies. Peer ancestry, inventory and regex
limits are independent and unchanged. Rollback is a normal root-PR revert.

### Automation

- `python3 -m unittest scripts.validation_ownership.tests.test_foundation.ObservationAllowanceTests -v`
  -- real capsule, metadata, cache/view/nested, API and malformed-counter controls.
- `python3 -m unittest scripts.validation_ownership.tests.test_foundation.FoundationTests.test_observation_total_exact_limit_cache_and_next_capsule scripts.validation_ownership.tests.test_foundation.FoundationTests.test_observation_remaining_count_reaches_next_capsule_guard scripts.validation_ownership.tests.test_foundation.FoundationTests.test_observation_totals_charge_failed_deferred_and_terminal_processes scripts.validation_ownership.tests.test_foundation.FoundationTests.test_observation_totals_cover_compiler_native_static_make_and_cache scripts.validation_ownership.tests.test_foundation.FoundationTests.test_observation_accounting_rejects_malformed_closed_reports scripts.validation_ownership.tests.test_foundation.FoundationTests.test_observation_record_limit_is_aggregate_across_collections scripts.validation_ownership.tests.test_foundation.FoundationTests.test_observation_bytes_remain_an_independent_aggregate_bound scripts.validation_ownership.tests.test_foundation.FoundationTests.test_immutable_view_observation_totals_follow_selection_without_reset -v`
  -- preserved default, byte, failed-work, compilation, cache and view contracts.
- `python3 -m unittest scripts.validation_ownership.tests.test_producer.ProducerTests.test_nested_generated_query_keeps_one_live_view_until_outer_completion scripts.validation_ownership.tests.test_producer.ProducerTests.test_live_include_preserves_actual_metadata_and_residual_resources scripts.validation_ownership.tests.test_foundation.PendingAdmissionTests.test_default_smaller_and_global_limits_remain_authoritative scripts.validation_ownership.tests.test_foundation.PendingAdmissionTests.test_small_actual_variants_keep_results_and_attempted_state_counts -v`
  -- existing nested publication/reservation, typed limits and plan compatibility.
- `python3 -m unittest scripts.docs_check_tests.test_check_docs.TesterCaseRegistryTests.test_late_shipped_contracts_are_complete_and_fail_closed scripts.docs_check_tests.test_development_workflow_skill.DevelopmentWorkflowSkillTests.test_manual_handoff_json_contract_and_human_links -v`
  -- both closed catalogs, without broad documentation/catalog discovery.

### Cleanup and limitations

Existing session/budget/watchdog teardown must leave no owned child, waiter,
active view, cache authority, parked capsule or scratch tree. Preserve the
preimage and mutation logs; remove only owned temporary fixtures after
terminal cleanup. This case proves a host policy capability, not #180 full
graph fit, a new resource value, performance, current H1, calibration or
production acceptance. No full graph/native-suite run or native AI review
belongs in these focused controls.

## TC-WORKFLOW-ASSET-DISCOVERY-001: Render captured-source asset discovery without FIFO races

- **Feature / originating issue:** `workflow-governance` /
  [issue #252](https://github.com/laqieer/fireemblem8-expansion/issues/252).
- **Supported configuration or artifact:** Linux/POSIX source checkout with
  Python 3 and GNU Make. No ROM, emulator, ARM scenario, GitHub credential,
  graph module, or live workflow is required.
- **Prerequisites and clean starting state:** start from the repository root
  with the committed asset manifest framework and an empty owned test root
  under `build/generated/assets/test-work`. The case uses the committed
  `assets/manifest.json`, its declared discovery dependencies, and
  subprocess-owned FIFO fixtures only.

### Actions

1. Reproduce or preserve the pre-fix negative control on exact
   `a616aaa63f8a976717bf5d38182ac325861adbc7`: build the complete captured
   source identity set for an owned manifest fixture, verify regular-source
   digest success, replace `source.json` with a FIFO immediately after real
   `_repo_path` validation, and require the bounded three-second controller to
   kill and reap the blocked child. Do not modify the reproduction artifact.
2. Run the focused public API selectors:

   ```bash
   python3 -m unittest \
     scripts.assets.tests.test_manifest.AssetManifestTests.test_captured_discovery_matches_git_validated_rendering \
     scripts.assets.tests.test_manifest.AssetManifestTests.test_captured_discovery_rejects_missing_source_membership \
     scripts.assets.tests.test_manifest.AssetManifestTests.test_captured_discovery_rejects_malformed_admission \
     scripts.assets.tests.test_manifest.AssetManifestTests.test_captured_discovery_keeps_source_path_validation \
     scripts.assets.tests.test_manifest.AssetManifestTests.test_discovery_artifact_uses_same_validation_rendering_and_logical_path \
     scripts.assets.tests.test_manifest.AssetManifestTests.test_discovery_artifact_rejects_malformed_or_escaping_outputs \
     scripts.assets.tests.test_manifest.AssetManifestTests.test_discovery_artifact_requires_complete_captured_identity \
     scripts.assets.tests.test_manifest.AssetManifestTests.test_discovery_artifact_rejects_nonregular_identity_modes_before_opening_sources \
     scripts.assets.tests.test_manifest.AssetManifestTests.test_discovery_artifact_rejects_manifest_fifo_before_parsing \
     scripts.assets.tests.test_manifest.AssetManifestTests.test_discovery_artifact_parses_replaced_manifest_from_verified_bytes \
     scripts.assets.tests.test_manifest.AssetManifestTests.test_discovery_artifact_does_not_require_new_hashlib_file_digest_api \
     scripts.assets.tests.test_manifest.AssetManifestTests.test_discovery_artifact_make_behavior_uses_equivalent_input_metadata \
     scripts.assets.tests.test_manifest.AssetManifestTests.test_captured_source_digest_rejects_raced_fifo_without_blocking_or_leaking \
     -v
   ```
3. Confirm ordinary Git-backed discovery and captured discovery render the same
   consumer groups on the real manifest. The captured path must not invoke a
   Git subprocess internally.
4. Render
   `build/generated/asset-discovery/captured.mk` from the repository root, a
   nested directory, and an external working directory. The returned path and
   content must be identical, repository-relative, below `build/`, and no
   destination file may be created.
5. Consume the ordinary and captured include bytes with GNU Make. Require the
   real `ASSET_*_INCBIN_CONSUMERS` values to match, the captured source digest
   to be a different 64-hex identity digest, a nonsemantic producer comment to
   leave the Make values unchanged, and a changed consumer assignment to be
   detected by parsed Make output.
6. Exercise adversarial inputs: missing tracked membership, malformed tracked
   source sets, escaping manifest source paths, absolute or malformed logical
   outputs, missing/extra/duplicate identity rows, nonregular identity modes,
   and mode/content digest mismatches. FIFO (`010600`) and directory
   (`040700`) identity-mode claims must fail before source acquisition, proven
   by an `os.open` spy with zero calls. Every input must fail explicitly.
7. Execute the native FIFO regression with regular, no-writer FIFO, and
   same-process sentinel-holder FIFO variants. The regular variant must match
   the captured identity digest. Both FIFO variants must reject promptly, close
   every captured descriptor, keep the holder descriptor separately accounted
   and closed in `finally`, and read the sentinel back unchanged after
   rejection.
8. Replace the manifest itself with a FIFO at its first captured acquisition.
   It must reject promptly before parsing and close the opened descriptor.
   Replace the same manifest path with different attested bytes between path
   validation and descriptor acquisition. The rendered consumer group and
   digest must come from those verified bytes, not from an earlier path parse.

### Expected result

`load_discovery(path, *, tracked_sources=None)` preserves ordinary Git source
verification by default and accepts only a canonical captured tracked-source set
when supplied. `render_discovery_artifact()` validates the same records and
complete `(path, mode, sha256)` identities, returns a safe
repository-relative output path plus the real rendered Make include content,
and writes nothing. Captured digests are stable identity hashes while ordinary
CLI discovery retains its mtime digest. Raced nonregular descriptors are
rejected before reads; malformed nonregular identity claims reject before any
source is opened. The manifest records are parsed from the same verified
descriptor bytes whose identity participates in the captured digest.

### Negative control

The pre-fix a616 implementation blocks indefinitely when the validated regular
source is replaced by a FIFO without a writer. Removing nonblocking descriptor
acquisition recreates that bounded timeout. Removing the pre-read regular-file
rejection consumes the same-process holder's nonregular FIFO sentinel payload.
Removing the early regular-mode identity guard attempts source acquisition for
malformed FIFO or directory identity rows. Missing, extra, duplicate,
malformed or mismatched captured identities cannot produce a successful
include. Parsing the manifest before verified descriptor acquisition can render
stale consumer records with a new attested digest and fails the replacement
control.

### Interactions and save compatibility

The case depends on the existing asset manifest framework and the external
captured-source authority that supplies canonical membership and identities.
#180/#186 is a dependent graph integration and keeps its graph-specific
resource accounting, schema, workflows, and Make routing. The only declared
conflict is duplicate implementation delta with that graph branch, resolved by
a normal merge. This case introduces no feature flag, gameplay behavior,
generated game content, localization payload, ROM/RAM allocation, save/config
identity, modern debug/release profile, or archival-lane behavior.

### Automation

The listed unittest selectors exercise the public API, real manifest, parsed
Make include behavior, identity mismatch controls, and bounded native FIFO
race. The regression uses real POSIX FIFOs and subprocess timeouts rather than
source spelling assertions. It closes the same-process FIFO holder in
`finally`, kills/reaps only its owned outer child on timeout, and keeps all
scratch paths below the unittest-owned test root.

### Cleanup and limitations

`unittest` teardown removes `build/generated/assets/test-work`, including the
FIFO and Make fixtures. The pre-fix reproduction artifact is read-only evidence
and is not modified. Caller-owned source admission, immutable source context,
declared private output, capture storage, and publication remain outside the
asset adapter. No manual-only criterion applies.

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
  [the canonical JSON contract](../../.github/manual-testing-handoff.json),
  development-workflow skill, contributor guide, this case, and registry
  unchanged.

### Actions

1. Parse `.github/manual-testing-handoff.json` and validate every required key,
   value, enum, boolean, target, comment field, and the separately identified
   positive/control artifact roles with deterministic emulator screenshot or
   synchronized emulator A/V evidence. Validate the comment's exact
   `@laqieer` mention, stable case ID, full Git SHA, artifact paths and SHA-256
   values, nonempty text, numbered steps, and true merge/closure holds.
2. Exercise the positive issue-only, one-PR, and multiple-open-PR queue shapes,
   using an independent GitHub-linked PR relationship map. Exercise malformed
   item kind/URL/state, label, assignee, per-item comment, relationship, and
   stale-state controls before filtering closed relationships.
3. Exercise completion cleanup with closed and superseded labeled PR history,
   separate label/assignee cleanup sets, retained independent ownership, and a
   typed result/evidence comment bound to the original case ID and commit.
   Accept only concrete same-repository comment, review, run, artifact, or
   commit-pinned blob links, plus GitHub user attachments. Verify each open PR's
   current head still matches the tested activation commit.
4. Exercise rejected evidence while the label, temporary assignee, merge and
   closure holds, and actionable state all remain active.
5. Run
   `python3 -m unittest scripts.docs_check_tests.test_development_workflow_skill -v`.
6. Open the documented queue:
   [`repo:laqieer/fireemblem8-expansion is:open assignee:laqieer label:"waiting-for-manual-testing"`](https://github.com/laqieer/fireemblem8-expansion/issues?q=repo%3Alaqieer%2Ffireemblem8-expansion+is%3Aopen+assignee%3Alaqieer+label%3A%22waiting-for-manual-testing%22).
   When it is empty, do not schedule notifications or comments.

### Expected result

The focused suite accepts the canonical JSON and its supported queue shapes.
Human guidance links to that file without duplicating machine behavior.

### Lifecycle summary

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
  A rejected result retains both holds and remains actionable.

### Negative control

Every leaf mutation in the structured contract fails, including a missing
artifact role/path/hash/emulator/determinism/synchronization/inspection field,
instrumented artifacts, missing or optional materiality, missing or misplaced
mention, invalid or empty numbered steps, false or mistyped comment holds,
missing per-item comments, permissive activation, wrong identifiers or targets,
disabled holds, incomplete historical cleanup, missing or mismatched completion
evidence, malformed optional booleans, duplicate artifact identities, bare or
unrelated evidence pages, malformed PR origins, stale open-PR heads, ownership
exceptions without a reason, rejected cleanup/resumption, premature
rejected-state cleanup, empty-queue notifications, and invalid independently
discovered issue/PR relationships. Reversing any lifecycle summary action or
removing this case's own subsection also fails locally.

### Interactions and save compatibility

The protocol depends on GitHub issues, pull requests, relationships, labels,
assignments, and comments when a real hold is active. Live queue contents
remain release-time evidence rather than tracked state. Cleanup history includes
every labeled PR after closure or supersession; independent ownership may retain
an assignee but never the handoff label. It changes no save, generated data,
localization, ROM/RAM, debug/release, or archival behavior.

### Automation

`python3 -m unittest scripts.docs_check_tests.test_development_workflow_skill -v`
parses the JSON contract and exercises its schema, every semantic leaf,
supported queue shapes, and fail-closed controls.

### Cleanup and limitations

This source-only case changes no remote item and cannot make the subjective
judgment itself. Live queue state and evidence remain in the relevant PR and
issue rather than this repository.
## TC-WORKFLOW-STACKED-CI-001: Run exact Build CI on a genuine stacked PR base

- **Feature / originating issue:** `workflow-governance` /
  [issue #171](https://github.com/laqieer/fireemblem8-expansion/issues/171).
- **Supported configuration or artifact:** clean source checkout with Python
  3 and the committed combined Build workflow; no GitHub token, live pull
  request, workflow dispatch, ROM, or emulator is required.
- **Prerequisites and clean starting state:** start at the repository root
  with `.github/workflows/build.yml` and the stacked-PR guidance unchanged.

### Actions

1. Run
   `python3 -m unittest discover -s tests/workflows -p "test_*.py" -v`.
2. Run
   `python3 -m unittest scripts.docs_check_tests.test_development_workflow_skill -v`.
3. Inspect the synthetic `opened`, child-head `synchronize`, `reopened`, and
   base-change `edited` pull-request fixtures, plus inline and block
   `branches` and `branches-ignore` mutations.

### Expected result

The synthetic non-master-base pull request selects the mandatory
`event-identity` setup before `event-router`, `event-classifier`, the existing
`host-tests`, `ownership-tests`, `build`, `extended-host-tests`, `legacy`, and fail-closed
`summary` jobs.

- **Parsed full-PR job set:** {`event-identity`, `event-router`,
  `event-classifier`, `host-tests`, `ownership-tests`, `build`, `extended-host-tests`, `legacy`,
  `summary`}.

Every candidate worker still checks out and verifies
`pull_request.head.sha`. The publisher is absent from pull-request execution,
while a push to `master` selects it and a push to any other branch selects no
workflow jobs.

The child remains based on its immediate parent while that parent is open, and
exact-head Build CI and Copilot review run against that genuine base. After the
parent head changes, its update is merged into the child with a normal merge
commit; the changed child head emits `synchronize`, the child-only diff is
verified again, and fresh exact-head gates replace evidence from the older
parent tree. A parent-only push does not emit an event for the child PR. After the
parent merges, the child is retargeted once to `master`, its child-only diff is
verified, and the resulting `pull_request` `edited` event starts fresh
exact-head Build CI even when the child head SHA is unchanged. The workflow
also runs for `opened`, `synchronize`, and `reopened`, but not `closed`,
`labeled`, or other unrelated activity types. An `edited` event alone is not
sufficient evidence: Build remains bound to `pull_request.head.sha`, and the
base/tree evidence, child-only diff, and fresh gate results must all be
verified.

### Negative control

Adding inline or block `branches` or `branches-ignore` filters under
`pull_request`, omitting either mandatory `event-identity` or `event-router`
setup context, removing `edited` or another required activity type, enabling
`closed`/`labeled` activity, removing either trigger, allowing non-master
pushes, exposing the patch publisher to pull requests, weakening exact-head
checkout verification, accepting an old child run after its parent head
changes, or documenting a temporary base flip solely to trigger CI makes the
focused suites fail.

### Interactions and save compatibility

This source-only contract depends on the existing combined Build jobs, exact
checkout binding, trusted owner-context pushes, and one watcher per exact run.
It conflicts with temporary base retargeting, stale evidence after a base/tree
or parent-head change, unsynchronized child branches, duplicate workflows,
duplicate matrices, weakened permissions, and PR publication. It changes no save,
generated data, localization, ROM/RAM,
debug/release, or archival behavior and needs no feature gate.

### Automation

`python3 -m unittest discover -s tests/workflows -p "test_*.py" -v` parses the
workflow, evaluates synthetic PR actions and push metadata, rejects inline and
block PR branch filters, proves a parent-only push cannot refresh the child,
checks the required child-head `synchronize` and later base-change `edited`
events, verifies exact-head checkout binding, and preserves the fail-closed
summary and publisher boundary.

`python3 -m unittest scripts.docs_check_tests.test_development_workflow_skill -v`
validates the genuine-stack workflow and rejects guidance that relies on a
temporary base flip solely to trigger CI.

### Cleanup and limitations

No cleanup is required. The case evaluates committed workflow and governance
contracts without dispatching GitHub Actions; it does not prove live service
availability or grant credentials.

## TC-WORKFLOW-BODY-EDIT-001: Suppress metadata-only Build workers

- **Feature / originating issue:** `workflow-governance` /
  [issue #177](https://github.com/laqieer/fireemblem8-expansion/issues/177).
- **Supported configuration or artifact:** clean source checkout with Python
  3, the Build workflow, and the committed GitHub event fixture; no token,
  live pull request, workflow run, ROM, emulator, or game build is required.
- **Prerequisites and clean starting state:** start at the repository root
  with `.github/workflows/build.yml`,
  `scripts/workflow_pilot/event_classifier.py`, the isolated launcher, and
  `scripts/workflow_pilot/tests/fixtures/event_classification.json` plus the
  preserved `pre_fix_build.yml` parsed graph unchanged.
  The fixture declares that the current workflow has no explicit final
  dispatch surface.

### Actions

1. Run
   `python3 -m unittest scripts.workflow_pilot.tests.test_event_classifier -v`.
2. Run
   `python3 -m unittest scripts.workflow_pilot.tests.test_candidate_evidence -v`.
3. Run
   `python3 -m unittest discover -s tests/workflows -p "test_*.py" -v`.
4. Run
   `python3 -m unittest tests.upstream_port.test_verify -v`.
5. Inspect the parsed body-only, title-only, body-and-title, base-only,
   base-plus-body, unknown-field, incomplete-change, `opened`, `synchronize`,
   `reopened`, missing/empty/malformed/mismatched base components, missing-head,
   missing-both, stacked-base,
   merge-`github.sha`, and `master`-push cases. Confirm every fixture provides
   separate PR base/head and push identity, GitHub-shaped payload, exact
   classifier result, exact selected job set, exact head/base, and expected
   summary conclusion. Base-only, mixed, and stack-retarget fixtures carry the
   production `changes.base.ref.from` plus `changes.base.sha.from` transition.
6. Inspect the disposable-event replay. It writes each payload beneath ignored
   `build/test-artifacts/`, invokes the real `/usr/bin/python3 -I` launcher and
   output-file protocol, parses the resulting job outputs, and removes the
   sandbox without reading or mutating remote state.
7. Inspect a pull request's stable body contract and canonical evidence
   comment protocol in [`../workflow-pilot.md`](../workflow-pilot.md). The
   comment carries this standalone marker:

   <!-- workflow-pilot-candidate-evidence -->

   Evolving SHA/run/review/budget/preflight values are updated there in place
   rather than in the body, title, baseline fixture, decision record, or
   another mutable ledger.
8. Parse `.github/PULL_REQUEST_TEMPLATE.md`. Confirm it contains only frozen
   scope/non-goals, classification/relationships, acceptance criteria, tester
   procedure, and compatibility decisions. Replay one canonical comment,
   missing/duplicate/non-standalone marker comments, a body marker, and every
   prohibited evolving body field.
9. Replay the same body-only event through the preserved pre-fix Build graph.
   Compare these unordered parsed sets:
   - **Parsed preserved pre-fix body-only job set:** {`host-tests`, `build`,
     `extended-host-tests`, `legacy`, `summary`}.
   - **Parsed current metadata-only job/check set:** {`event-identity`,
     `event-router`, `metadata-classifier`, `host-tests`, `ownership-tests`, `build`,
     `extended-host-tests`, `legacy`, `summary`}.
   The pre-fix graph therefore starts all four expensive workers and summary;
   the current graph retains both mandatory setup contexts, preserves the live
   canonical `host-tests`/`build`/`summary` required contexts through trusted
   branch-protection continuity adapters plus the summary continuity proof,
   keeps canonical skipped `ownership-tests`/`extended-host-tests`/`legacy` plus canonical
   skipped patch publication, and uses only the running metadata classifier
   attestation beyond those existing required names.

### Expected result

Body-only, title-only, and combined body/title edits emit
`event-identity`, `event-router`, `metadata-classifier`, the canonical
worker checks `host-tests`, `ownership-tests`, `build`, `extended-host-tests`, and `legacy`,
plus canonical `summary`. The trusted metadata-only path
starts runners for `host-tests` and `build`, but those two jobs execute only a
fixed no-checkout continuity attestation that validates exact event identity,
classifier, head, base, and the raw edited pull-request body/title-only
`changes` payload itself from the runner-owned file-backed `GITHUB_EVENT_PATH`;
that attestation accepts only a same-owner regular file up to 1 MiB, reads at
most one additional EOF byte, and never env-copies the body/title/changes
JSON. Missing, malformed, duplicate, base-retarget,
unknown, empty, or unchanged body/title changes reject both adapters. Every
existing
checkout/install/test/build step in those jobs is full/fallback-only and
remains skipped. `ownership-tests`, `extended-host-tests` and `legacy` stay platform-skipped with
no runner. Live branch protection remains unchanged and therefore still
requires canonical `host-tests`, `build`, `summary`, and the independent
GitGuardian context. Metadata `summary` succeeds only after a trusted
no-checkout Actions API proof classifies exact prior runs newest-first, skips
only conclusively metadata runs, and confirms the newest conclusively full
Build CI run for the same repository, PR number, authoritative base SHA, and
immutable head SHA completed successfully; a newer failed, cancelled,
in-progress, or malformed full run blocks older successes. That proof first
requires complete paginated results with stable `total_count`, single-page
`Link` omission, exact non-final `next`/`last` relations, no final `next`,
exact per-page cardinality, stable `workflow_id`, ordered positive
`run_number`/`run_attempt` values, one exact current-run observation, and
rejects redirects before any second authenticated request.
Without that prior green full run, metadata-only edits still block merge.
Metadata runs remain
ineligible candidate evidence even when their continuity adapters and canonical
`summary` succeed. A later metadata continuity run advances the required
canonical `summary` context only after proving that newest prior full run,
while candidate eligibility remains bound to the newest prior complete full
run. Evaluated metadata labels, duplicates,
unknown names, or spoofed worker names reject instead of becoming candidate
evidence.
The recorded `gh pr checks --required` output after the title edit and restore
shows canonical `host-tests`, `build`, and `summary` passing together, and a
protected async merge attempt no longer fails with `Required status check
"summary" is expected.`.
The summary succeeds only when classifier status is `success`, the classified
SHA equals the event's validated exact `pull_request.head.sha`, event number
matches the exact `refs/pull/<number>/merge` ref, suppression is exactly false,
`host-tests`/`build` succeed through the trusted continuity adapters, and
`ownership-tests`/`extended-host-tests`/`legacy` are exactly `skipped`, and the
trusted Actions API proof classifies exact prior runs newest-first so only the
newest conclusively full run with the same repository, PR number,
authoritative base SHA, and immutable head SHA can authorize continuity.
That proof first requires complete paginated results with stable
`total_count`, single-page `Link` omission, exact non-final `next`/`last`
relations, no final `next`, exact per-page cardinality, stable `workflow_id`,
ordered positive `run_number`/`run_attempt` values, one exact current-run
observation, and rejects redirects before any second authenticated request.
Older full
successes never override a newer failed, cancelled, in-progress, or malformed
full run.

Base-only edits, mixed edits, unknown and incomplete change records, `opened`,
`synchronize`, and `reopened` select the classifier, all five expensive
workers, and summary at the exact PR head. A `master` push additionally selects
the existing patch publisher and runs the complete graph from its separate
push SHA. Malformed/duplicate/non-finite JSON or another classifier failure with a
validated authoritative PR head runs all five workers at that exact head under
their canonical worker names, then summary still fails to expose the classifier
defect. A classifier failure on a
master push with validated `github.sha` runs all five workers and the publisher
at that exact push SHA, then summary still fails. Any
missing, empty, malformed, or event-mismatched base ref/SHA with a valid exact
PR head runs all five workers at that head and fails normal summary; a
syntactically valid direct base SHA may remain diagnostic output but is never
checkout authority. Missing, malformed, stale, or spoofed PR head or missing
push SHA starts no combined worker/publisher and fails summary.
Missing/stale successful output cannot select a fallback ref.
An accepted base retarget requires valid, differing previous/current ref and
SHA pairs; ref-only, SHA-only, same, missing, extra, or spoofed transition
records remain full fail-closed edits and never metadata suppression.
Base refs are bounded to 1024 UTF-8 bytes and must satisfy full
`git check-ref-format refs/heads/<base.ref>` semantics; `--branch` shorthand
is not used, and lone `@` is rejected. Python applies the equivalent grammar
without a subprocess; the trusted bootstrap quotes the full ref to system Git
and never checks it out. Invalid base refs are incomplete identity: a valid
exact head runs all five workers and fails summary; an invalid head runs none.
The classifier executes from the verified current PR base SHA; a missing base
uses the trusted default branch only to report invalid identity, while a base
without the new classifier uses the explicit strict bootstrap. The original
#177 workflow had no final-dispatch route; the current graph preserves all nine jobs
through its input-free dispatch and integration-base bootstrap, covered by
`TC-WORKFLOW-REVIEW-FIRST-001`.
The classifier bootstrap may use the trusted default branch when PR base
identity is missing or unusable; worker checkouts never use a merge/default
fallback.
Trusted event setup accepts identity only as an exact lowercase 40-hex SHA. A
PR also requires its numeric event number and exact
`refs/pull/<number>/merge` ref; a push requires `refs/heads/master` and equal
event `after`/`github.sha`. Successful full/metadata classifications, workers,
and summary all bind to that same kind and SHA. Missing, uppercase, short,
nonhex, ref-name, ref-number-mismatched, malformed, or cross-event identities
run no worker and cannot produce a successful summary. Candidate normalization
requires exactly one successful identity context for both full and metadata
modes and exactly one successful router setup
context; missing, failed, skipped, renamed, duplicate, or unknown setup
contexts reject the run.
A canonical successful `event-identity` context is mandatory in both modes.
A canonical successful `event-router` context is mandatory in both modes.
Metadata-only mode is accepted only for a coherently
bound pull request. Push-shaped or cross-event metadata output fails the
classifier, runs the validated full fallback workers/publisher, and leaves
normal summary failed. Workers consume only that validated SHA. Packaging
checks the same validated push SHA and reuses the normal build's existing
release outputs without a second build or a source-hash ledger.
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

Default-branch validation is deferred until classifier bootstrap is actually
needed. A missing or malformed default branch never invalidates an
independently valid PR-head or push fallback. With no classifier authority,
the router performs no checkout and fails safely, the classifier fails, exact
fallback workers plus any guarded push publisher run, and summary remains
fail-closed.

### Negative control

The preserved parsed pre-fix workflow fixture selects `host-tests`, `build`,
`extended-host-tests`, `legacy`, and `summary` for the same body-only fixture
despite its unchanged head SHA. The focused suites also reject metadata
topology that omits either mandatory `event-identity` or `event-router` setup,
suppression for same-value/spoofed/missing-current/extra-key/nested metadata
records, invalid title/body values, malformed `changes`, missing PR identity,
body/title metadata with whitespace, control, forbidden-character, dot,
slash, `.lock`, `@{`, lone-`@`, or oversized base refs,
valid-head events with malformed or mismatched base components that skip
workers or produce a successful summary,
duplicate JSON keys, `NaN`/positive or negative `Infinity`, positive/negative
exponent overflow, nonzero-to-zero underflow, huge exponents, an unused
overflow field on metadata-only input, oversized event
files, base or mixed edits, worker job-name overrides, historical
noncanonical worker names, merge-SHA fallback, malformed fallback identities,
successful classification with a cross-event/malformed/number-mismatched ref,
missing/failed/skipped/renamed/duplicate event-identity or event-router
evidence, push-shaped metadata router output,
an unverified/mutable
classifier checkout, classifier output drift, worker conditions that accept
invalid/stale identity, worker skipping after classifier exit 2 with a valid
PR/push head, cross-event fallback, worker/publisher execution after classifier
failure with no event SHA, summary success after any classifier failure, weakened
exact-head checkout, body/template evolving evidence or marker placement, and
source/target workflow mirror drift.

### Interactions and save compatibility

This confirmed workflow-efficiency fix depends on issue #176's immutable
baseline, closed workflow mirror, isolated launcher, exact-head checkout, and
summary authority. Issue #181 depends on this classification. It deliberately
changes only pull-request metadata-event selection and the evidence-comment
contract. It conflicts with manual labels, `pull_request_target`, head-authored
classifier execution, success-shaped defaults, mutable metric ledgers, and
suppression of genuine stacked-PR base edits. It has no feature flag and no
game/runtime, modern debug/release output, save, generated-data, localization,
ROM/RAM, or archival impact.

### Automation

`python3 -m unittest scripts.workflow_pilot.tests.test_event_classifier -v`
parses every fixture, replays the real isolated event-file/output protocol, and
exercises strict identity, semantic transition, non-finite JSON, malformed,
and unknown fail-closed controls.

`python3 -m unittest scripts.workflow_pilot.tests.test_candidate_evidence -v`
derives full versus metadata mode from running classifier/summary contexts and
proves a later green metadata run cannot replace a failed/missing candidate
full run; a later metadata continuity run advances the required canonical
`summary` context only after proving the newest prior successful full run,
while candidate eligibility remains bound to that prior full run.

`python3 -m unittest discover -s tests/workflows -p "test_*.py" -v` parses the
workflow and asserts exact trigger, job, head, worker-condition, summary, setup,
pin, and environment semantics, including the pre-fix negative selection.

`python3 -m unittest tests.upstream_port.test_verify -v` preserves the 34 local
gates while requiring complete nine-job source/target equivalence: the retained
issue #176 jobs remain closed and the identity/router/classifier are closed
setup-only jobs, never additional local gates. The two ownership checks remain
part of the `ownership-tests` gate set. The dedicated worker has its own
exact-head checkout, revision comparison, Git hydration and native/ARM setup;
its three coupled actions run once without changing the native extended-host
owner. Ownership has a bounded 90-minute CI envelope after its measured
60-minute timeout; the retained host budget remains 60 minutes.
Replay full PR/master/manual and exact-fallback routes, then replace the
ownership result with failure, cancellation, timeout, unexpected skip or
missing evidence. Every full admission must fail, including continuity with
an older green run. Metadata-only/review-first routes require its platform
skip, not an ownership attestation. Remove its summary dependency, disable or
duplicate the job/steps, move the verifier back to host-tests, or substitute
checkout/root/base/environment; the parsed mirror and actual summary must
reject while the unchanged metadata adapters retain their required contexts.

`python3 -m unittest scripts.docs_check_tests.test_development_workflow_skill -v`
parses the frozen PR template/body and comment collection, requiring exactly
one standalone canonical marker while rejecting missing, duplicate, inline,
or body markers and every evolving body field.

### Cleanup and limitations

The disposable sandbox is removed automatically. No remote state is read or
changed. The local replay proves GitHub's documented event-file semantics and
the exact workflow graph contract, not live service availability. No
manual-only criterion applies. Rollback is a normal revert; the prior broad
`edited` behavior then resumes.

### Planned live title-only exercise after push

The owner performs this validation-only remote exercise only after the
candidate branch is pushed. The disposable PR is never merged and does not
implement an independent issue. Do not edit the implementation PR: while its
base predates `event_classifier.py`, base-authoritative routing correctly
reports `classifier-bootstrap` and runs the full graph, so it is not a valid
metadata-suppression probe until the classifier is merged into that base.

Run all commands below in one Bash session. The discovery helper snapshots all
prior run IDs, then makes at most 60 attempts five seconds apart. It accepts
exactly one unseen `Build CI` pull-request run created after the mutation with
the exact branch and head; timeout or ambiguity fails before `gh run watch`.

1. From the issue worktree, choose unused temporary names and create a direct
   child of the exact candidate branch with one deterministic tracked probe.
   Install cleanup before the first remote mutation:

   ```bash
   set -euo pipefail
   source_root="$PWD"
   repo="$(gh repo view --json nameWithOwner --jq .nameWithOwner)"
   head_owner="${repo%%/*}"
   candidate_branch="${candidate_branch:-agent/issue-177}"
   probe_branch="${probe_branch:-validation/issue-177-title-probe}"
   probe_worktree="$(dirname "$source_root")/issue-177-title-probe"
   probe_file=".github/workflow-probes/issue-177-title-only.json"
   evidence_dir="$source_root/build/test-artifacts/issue-177-live-probe"
   pr=""
   probe_head_sha=""
   original_title="TC-WORKFLOW-BODY-EDIT-001 validation"
   probe_title="$original_title [title-only metadata probe]"
   evidence_dir_created=false
   local_ownership_intent=false
   push_ownership_intent=false
   pr_ownership_intent=false

   list_build_run_ids() {
     gh api --method GET --paginate \
       "repos/{owner}/{repo}/actions/workflows/build.yml/runs" \
       -f event=pull_request -f branch="$probe_branch" -f per_page=100 \
       --jq '.workflow_runs[].id'
   }

   discover_build_run() {
     prior_ids="$1"
     created_after="$2"
     attempt=0
     while [ "$attempt" -lt 60 ]; do
       runs_json="$(gh api --method GET --paginate \
         "repos/{owner}/{repo}/actions/workflows/build.yml/runs" \
         -f event=pull_request -f branch="$probe_branch" -f per_page=100 \
         | jq -s '.')"
       if run_id="$(RUNS_JSON="$runs_json" PRIOR_IDS="$prior_ids" \
           EXPECTED_CREATED_AFTER="$created_after" \
           EXPECTED_BRANCH="$probe_branch" EXPECTED_HEAD="$head_sha" \
           python3 - <<'PY'
   import json
   import os

   prior = set(os.environ["PRIOR_IDS"].splitlines())
   records = [
       record
       for page in json.loads(os.environ["RUNS_JSON"])
       for record in page["workflow_runs"]
   ]
   matches = [
       record
       for record in records
       if str(record["id"]) not in prior
       and record["name"] == "Build CI"
       and record["event"] == "pull_request"
       and record["head_branch"] == os.environ["EXPECTED_BRANCH"]
       and record["head_sha"] == os.environ["EXPECTED_HEAD"]
       and record["created_at"] >= os.environ["EXPECTED_CREATED_AFTER"]
   ]
   if len(matches) != 1:
       raise SystemExit(1)
   print(matches[0]["id"])
   PY
       )"; then
         test -n "$run_id"
         printf '%s\n' "$run_id"
         return 0
       fi
       attempt=$((attempt + 1))
       sleep 5
     done
     echo "timed out waiting for one unseen exact Build CI run" >&2
     return 1
   }

   watch_build_run() {
     run_id="$1"
     case "$run_id" in
       ''|*[!0-9]*) echo "invalid Build run ID" >&2; return 1 ;;
     esac
     set +e
     timeout 90m gh run watch "$run_id" --interval 30 --exit-status
     watch_status="$?"
     set -e
     if [ "$watch_status" -ne 124 ]; then
       return "$watch_status"
     fi

     run_state="$(gh run view "$run_id" --json status,conclusion \
       --jq '[.status, (.conclusion // "")] | @tsv')"
     run_status="${run_state%%$'\t'*}"
     run_conclusion="${run_state#*$'\t'}"
     case "$run_status" in
       queued|in_progress|waiting)
         test -z "$run_conclusion"
         set +e
         timeout 90m gh run watch "$run_id" --interval 30 --exit-status
         watch_status="$?"
         set -e
         if [ "$watch_status" -eq 124 ]; then
           echo "second watcher timed out for exact Build run" >&2
           return 124
         fi
         return "$watch_status"
         ;;
       completed)
         if [ "$run_conclusion" = success ]; then
           return 0
         fi
         test -n "$run_conclusion"
         echo "exact Build run completed unsuccessfully" >&2
         return 1
         ;;
       *)
         echo "exact Build run has unsupported status" >&2
         return 1
         ;;
     esac
   }

   # Each exact run uses watch_build_run. A first watcher timeout (124)
   # triggers exactly one status/conclusion query for that run. Only queued,
   # in_progress, or waiting re-arms one final 90-minute watcher; a terminal
   # result is consumed immediately, any terminal failure is preserved, and a
   # second timeout fails.

   owned_probe_pr_numbers() {
     pulls_json="$(gh api --method GET --paginate \
       "repos/$repo/pulls" -f state=open -f head="$head_owner:$probe_branch" \
       -f base="$candidate_branch" -f per_page=100 | jq -s '.')"
     PULLS_JSON="$pulls_json" EXPECTED_OWNER="$head_owner" \
       EXPECTED_BRANCH="$probe_branch" EXPECTED_BASE="$candidate_branch" \
       EXPECTED_HEAD_SHA="$probe_head_sha" \
       EXPECTED_BASE_SHA="$candidate_sha" \
       python3 - <<'PY'
   import json
   import os

   records = [
       record
       for page in json.loads(os.environ["PULLS_JSON"])
       for record in page
   ]
   matches = [
       record
       for record in records
       if record["state"] == "open"
       and record["merged_at"] is None
       and record["head"]["user"]["login"] == os.environ["EXPECTED_OWNER"]
       and record["head"]["ref"] == os.environ["EXPECTED_BRANCH"]
       and record["head"]["sha"] == os.environ["EXPECTED_HEAD_SHA"]
       and record["base"]["ref"] == os.environ["EXPECTED_BASE"]
       and record["base"]["sha"] == os.environ["EXPECTED_BASE_SHA"]
   ]
   for record in matches:
       print(record["number"])
   PY
   }

   cleanup_probe() {
     cleanup_failed=0
     set +e
     if [ "${pr_ownership_intent:-false}" = true ]; then
       matching_prs="$(owned_probe_pr_numbers)"
       query_status=$?
       if [ "$query_status" -ne 0 ]; then
         echo "cannot discover exact validation PR during cleanup" >&2
         cleanup_failed=1
       elif [ -n "$matching_prs" ]; then
         if [ "$(printf '%s\n' "$matching_prs" | grep -c .)" -ne 1 ]; then
           echo "ambiguous exact validation PRs; preserving all" >&2
           cleanup_failed=1
         else
           cleanup_pr="$matching_prs"
           cleanup_pr_body="$(gh api "repos/$repo/pulls/$cleanup_pr" \
             --jq .body)"
           cleanup_pr_title="$(gh api "repos/$repo/pulls/$cleanup_pr" \
             --jq .title)"
           if [ "$cleanup_pr_body" != \
                "Validation-only disposable PR. Never merge." ] || \
              { [ "$cleanup_pr_title" != "$original_title" ] && \
                [ "$cleanup_pr_title" != "$probe_title" ]; }; then
             echo "validation PR contract changed; preserving it" >&2
             cleanup_failed=1
           else
             if [ "$cleanup_pr_title" != "$original_title" ]; then
               gh api --method PATCH "repos/$repo/pulls/$cleanup_pr" \
                 -f title="$original_title" > /dev/null 2>&1 \
                 || cleanup_failed=1
             fi
             gh pr close "$cleanup_pr" > /dev/null 2>&1 || cleanup_failed=1
             pr_state="$(gh api "repos/$repo/pulls/$cleanup_pr" \
               --jq '[.state, (.merged_at // "")] | @tsv')"
             test "$pr_state" = "$(printf 'closed\t')" || cleanup_failed=1
           fi
         fi
       elif [ -n "${pr:-}" ]; then
         existing_pr_state="$(gh api "repos/$repo/pulls/$pr" \
           --jq '[.state, (.merged_at // "")] | @tsv')"
         if [ "$existing_pr_state" != "$(printf 'closed\t')" ]; then
           echo "recorded validation PR changed or merged; preserving it" >&2
           cleanup_failed=1
         fi
       fi
     fi
     if [ "${push_ownership_intent:-false}" = true ]; then
       remote_probe_ref="$(git ls-remote --heads origin \
         "refs/heads/$probe_branch")" || cleanup_failed=1
       if [ -n "$remote_probe_ref" ]; then
         remote_sha="$(printf '%s\n' "$remote_probe_ref" | awk 'NR == 1 {print $1}')"
         remote_ref="$(printf '%s\n' "$remote_probe_ref" | awk 'NR == 1 {print $2}')"
         if [ "$(printf '%s\n' "$remote_probe_ref" | grep -c .)" -ne 1 ] || \
            [ "$remote_ref" != "refs/heads/$probe_branch" ] || \
            [ -z "$probe_head_sha" ] || [ "$remote_sha" != "$probe_head_sha" ]; then
           echo "remote probe ref changed; preserving it for inspection" >&2
           cleanup_failed=1
         else
           git push --force-with-lease="refs/heads/$probe_branch:$probe_head_sha" \
             origin ":refs/heads/$probe_branch" > /dev/null 2>&1 \
             || cleanup_failed=1
         fi
       fi
       remote_probe_ref="$(git ls-remote --heads origin \
         "refs/heads/$probe_branch")" || cleanup_failed=1
       if [ -n "$remote_probe_ref" ]; then
         cleanup_failed=1
       fi
     fi
     if [ "${local_ownership_intent:-false}" = true ]; then
       if [ -d "$probe_worktree" ]; then
         local_head="$(git -C "$probe_worktree" rev-parse HEAD 2>/dev/null)"
         local_ref="$(git -C "$probe_worktree" symbolic-ref -q HEAD 2>/dev/null)"
         local_root="$(git -C "$probe_worktree" rev-parse --show-toplevel \
           2>/dev/null)"
         local_dirty="$(git -C "$probe_worktree" status --porcelain \
           --untracked-files=all 2>/dev/null)"
         if [ -z "$probe_head_sha" ] || [ "$local_head" != "$probe_head_sha" ] || \
            [ "$local_ref" != "refs/heads/$probe_branch" ] || \
            [ "$local_root" != "$probe_worktree" ] || [ -n "$local_dirty" ]; then
           echo "local probe worktree changed or dirty; preserving it" >&2
           cleanup_failed=1
         else
           git -C "$source_root" worktree remove "$probe_worktree" \
             || cleanup_failed=1
         fi
       fi
       if git -C "$source_root" show-ref --verify --quiet \
            "refs/heads/$probe_branch"; then
         local_branch_sha="$(git -C "$source_root" rev-parse \
           "refs/heads/$probe_branch")"
         if [ -z "$probe_head_sha" ] || \
            [ "$local_branch_sha" != "$probe_head_sha" ] || \
            [ -d "$probe_worktree" ]; then
           echo "local probe branch changed or remains checked out; preserving it" >&2
           cleanup_failed=1
         else
           git -C "$source_root" update-ref -d \
             "refs/heads/$probe_branch" "$probe_head_sha" \
             || cleanup_failed=1
         fi
       fi
     fi
     if [ "${evidence_dir_created:-false}" = true ] && \
        [ "$evidence_dir" = \
          "$source_root/build/test-artifacts/issue-177-live-probe" ]; then
       rm -rf -- "$evidence_dir" || cleanup_failed=1
     elif [ "${evidence_dir_created:-false}" = true ]; then
       cleanup_failed=1
     fi
     return "$cleanup_failed"
   }

   finish_probe() {
     primary_status="$?"
     trap - EXIT INT TERM
     set +e
     cleanup_probe
     cleanup_status="$?"
     if [ "$cleanup_status" -ne 0 ]; then
       echo "live probe cleanup failed; inspect preserved exact resources" >&2
     fi
     if [ "$primary_status" -ne 0 ]; then
       exit "$primary_status"
     fi
     exit "$cleanup_status"
   }
   trap finish_probe EXIT
   trap 'exit 130' INT
   trap 'exit 143' TERM

   test ! -e "$probe_worktree"
   test ! -e "$evidence_dir"
   if git -C "$source_root" show-ref --verify --quiet \
        "refs/heads/$probe_branch"; then
     echo "local probe branch already exists" >&2
     exit 1
   fi
   remote_probe_ref="$(git ls-remote --heads origin \
     "refs/heads/$probe_branch")"
   if [ -n "$remote_probe_ref" ]; then
     echo "remote probe branch already exists" >&2
     exit 1
   fi
   mkdir -p "$evidence_dir"
   evidence_dir_created=true
   candidate_sha="$(git rev-parse "$candidate_branch^{commit}")"
   local_ownership_intent=true
   git worktree add -b "$probe_branch" "$probe_worktree" "$candidate_sha"
   cd "$probe_worktree"
   mkdir -p "$(dirname "$probe_file")"
   printf '{"candidate_sha":"%s","case":"TC-WORKFLOW-BODY-EDIT-001"}\n' \
     "$candidate_sha" > "$probe_file"
   git add "$probe_file"
   git diff --cached --quiet && { echo "probe change is empty" >&2; exit 1; }
   git commit -m "test(ci): add title-only validation probe" \
     -m "Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
   head_sha="$(git rev-parse HEAD)"
   probe_head_sha="$head_sha"
   test "$(git rev-parse "$head_sha^")" = "$candidate_sha"
   test "$(git diff-tree --no-commit-id --name-only -r "$head_sha")" = "$probe_file"
   opened_prior_ids="$(list_build_run_ids)"
   opened_created_after="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
   push_ownership_intent=true
   git push -u origin "$probe_branch"
   existing_prs="$(owned_probe_pr_numbers)"
   test -z "$existing_prs"
   pr_ownership_intent=true
   set +e
   pr_url="$(gh pr create --head "$probe_branch" --base "$candidate_branch" \
     --title "$original_title" \
     --body "Validation-only disposable PR. Never merge.")"
   create_status="$?"
   set -e
   matching_prs="$(owned_probe_pr_numbers)"
   test "$(printf '%s\n' "$matching_prs" | grep -c .)" -eq 1
   pr="$matching_prs"
   test "$(gh api "repos/$repo/pulls/$pr" --jq .title)" = "$original_title"
   test "$(gh api "repos/$repo/pulls/$pr" --jq .body)" = \
     "Validation-only disposable PR. Never merge."
   if [ "$create_status" -ne 0 ]; then
     echo "PR create response failed; recovered exact validation PR $pr" >&2
   fi
   base_ref="$(gh api "repos/{owner}/{repo}/pulls/$pr" --jq .base.ref)"
   base_sha="$(gh api "repos/{owner}/{repo}/pulls/$pr" --jq .base.sha)"
   test "$base_ref" = "$candidate_branch"
   test "$base_sha" = "$candidate_sha"
   ```

   Never use `git commit --allow-empty`, an empty commit, or a merge commit.
   The tracked probe is deterministic for the candidate SHA and the direct
   parent assertion proves the head is a strict nonempty descendant.
2. Discover, watch, and save the opened-event full run:

   ```bash
   opened_run_id="$(discover_build_run \
     "$opened_prior_ids" "$opened_created_after")"
   test "$(gh pr view "$pr" --json headRefOid --jq .headRefOid)" = "$head_sha"
   test "$(gh api "repos/{owner}/{repo}/pulls/$pr" --jq .base.sha)" = "$base_sha"
   watch_build_run "$opened_run_id"
   test "$(gh run view "$opened_run_id" --json event --jq .event)" = "pull_request"
   test "$(gh run view "$opened_run_id" --json headSha --jq .headSha)" = "$head_sha"
   gh run view "$opened_run_id" \
     --json event,headSha,conclusion,url > "$evidence_dir/opened.json"
   gh api --method GET --paginate \
     "repos/$repo/actions/runs/$opened_run_id/jobs" -f per_page=100 \
     | jq -s '.' > "$evidence_dir/opened-jobs.json"
   ```

   - **Parsed live opened-run job set:** {`event-identity`, `event-router`,
     `event-classifier`, `host-tests`, `ownership-tests`, `build`, `extended-host-tests`, `legacy`,
     `summary`}.
3. Snapshot prior IDs, apply the title-only mutation through the owner REST
   endpoint, then discover, watch, and save its distinct metadata run:

   ```bash
   title_prior_ids="$(list_build_run_ids)"
   title_created_after="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
   gh api --method PATCH "repos/{owner}/{repo}/pulls/$pr" \
     -f title="$probe_title" > /dev/null
   title_run_id="$(discover_build_run "$title_prior_ids" "$title_created_after")"
   test "$title_run_id" != "$opened_run_id"
   test "$(gh pr view "$pr" --json headRefOid --jq .headRefOid)" = "$head_sha"
   test "$(gh api "repos/{owner}/{repo}/pulls/$pr" --jq .base.sha)" = "$base_sha"
   watch_build_run "$title_run_id"
   test "$(gh run view "$title_run_id" --json event --jq .event)" = "pull_request"
   test "$(gh run view "$title_run_id" --json headSha --jq .headSha)" = "$head_sha"
   gh run view "$title_run_id" \
     --json event,headSha,conclusion,url > "$evidence_dir/title.json"
   gh api --method GET --paginate \
     "repos/$repo/actions/runs/$title_run_id/jobs" -f per_page=100 \
     | jq -s '.' > "$evidence_dir/title-jobs.json"
   gh pr checks "$pr" --required > "$evidence_dir/title-required-checks.txt"
   ```

   - **Parsed live title-edit job/check set:** {`event-identity`,
     `event-router`, `metadata-classifier`, `host-tests`, `ownership-tests`, `build`,
     `extended-host-tests`, `legacy`, `summary`}.

   Every raw REST job record is scanned before normalization. Duplicate API
   IDs, duplicate names/stable IDs, unknown jobs, a metadata `host-tests` or
   `build` record without a runner-backed `success` conclusion, or a metadata
   `ownership-tests`/`extended-host-tests`/`legacy` record with a runner or non-`skipped`
   conclusion fail. GitHub may stamp `started_at` on a platform-skipped record;
   that timestamp is admissible only when `runner_name` is null and the
   conclusion is exactly `skipped`. Every metadata worker record is included
   with its stable ID and canonical worker name rather than hidden behind the
   running metadata classifier/summary names.
   Historical runs may additionally contain exact stable ID/name
   `patch-release`, conclusion `skipped`, and no runner. The current graph
   omits that job. Successful, failed, renamed or duplicate legacy publisher
   records reject; every actual validation job is still mandatory.
   While the enclosing old run is active, its legacy publisher may still be
   non-runner pending with no conclusion or execution timestamps. This must
   remain active full evidence and defer a default metadata edit, not fail as
   malformed. Once the run completes, require the canonical skipped shape;
   a running or runner-backed legacy publisher never satisfies compatibility.
4. Snapshot IDs before restoring the original title through the owner REST
   endpoint. Discover, watch, and save the distinct restore metadata run:

   ```bash
   restore_prior_ids="$(list_build_run_ids)"
   restore_created_after="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
   gh api --method PATCH "repos/{owner}/{repo}/pulls/$pr" \
     -f title="$original_title" > /dev/null
   restore_run_id="$(discover_build_run \
     "$restore_prior_ids" "$restore_created_after")"
   test "$restore_run_id" != "$title_run_id"
   test "$(gh pr view "$pr" --json headRefOid --jq .headRefOid)" = "$head_sha"
   test "$(gh api "repos/{owner}/{repo}/pulls/$pr" --jq .base.sha)" = "$base_sha"
   watch_build_run "$restore_run_id"
   test "$(gh run view "$restore_run_id" --json event --jq .event)" = "pull_request"
   test "$(gh run view "$restore_run_id" --json headSha --jq .headSha)" = "$head_sha"
   gh run view "$restore_run_id" \
     --json event,headSha,conclusion,url > "$evidence_dir/restore.json"
   gh api --method GET --paginate \
     "repos/$repo/actions/runs/$restore_run_id/jobs" -f per_page=100 \
     | jq -s '.' > "$evidence_dir/restore-jobs.json"
   gh pr checks "$pr" --required > "$evidence_dir/restore-required-checks.txt"
   ```

   - **Parsed live title-restore job/check set:** {`event-identity`,
     `event-router`, `metadata-classifier`, `host-tests`, `ownership-tests`, `build`,
     `extended-host-tests`, `legacy`, `summary`}.
5. Normalize all three real runs and execute the candidate evaluator's full,
   metadata-only, combined, failed-full, and missing-full assertions:

   ```bash
   python3 - "$head_sha" "$base_sha" \
     "$opened_run_id" "$evidence_dir/opened.json" "$evidence_dir/opened-jobs.json" \
     "$title_run_id" "$evidence_dir/title.json" "$evidence_dir/title-jobs.json" \
     "$restore_run_id" "$evidence_dir/restore.json" \
     "$evidence_dir/restore-jobs.json" <<'PY'
   import copy
   import json
   import sys

   from scripts.workflow_pilot import candidate_evidence

   head_sha, base_sha = sys.argv[1:3]
   run_specs = (
       ("full", int(sys.argv[3]), sys.argv[4], sys.argv[5]),
       ("metadata-only", int(sys.argv[6]), sys.argv[7], sys.argv[8]),
       ("metadata-only", int(sys.argv[9]), sys.argv[10], sys.argv[11]),
   )

   def normalize_run(mode, run_id, run_path, jobs_path):
       with open(run_path, encoding="utf-8") as source:
           raw = json.load(source)
       assert raw["event"] == "pull_request"
       assert raw["headSha"] == head_sha
       assert raw["conclusion"] == "success"
       with open(jobs_path, encoding="utf-8") as source:
           pages = json.load(source)
       raw_jobs = [
           job
           for page in pages
           for job in page["jobs"]
       ]
       stable_by_name = {
           "event-identity": "event-identity",
           "event-router": "event-router",
           "event-classifier": "event-classifier",
           "metadata-classifier": "event-classifier",
           "host-tests": "host-tests",
           "ownership-tests": "ownership-tests",
           "build": "build",
           "extended-host-tests": "extended-host-tests",
           "legacy": "legacy",
           "patch-release": "patch-release",
           "summary": "summary",
       }
       workers = {
           "host-tests",
           "ownership-tests",
           "build",
           "extended-host-tests",
           "legacy",
       }
       metadata_adapter_ids = {"host-tests", "build"}
       metadata_skipped_ids = {"ownership-tests", "extended-host-tests", "legacy"}
       required_names = (
           {
               "event-identity",
               "event-router",
               "event-classifier",
               "host-tests",
               "ownership-tests",
               "build",
               "extended-host-tests",
               "legacy",
               "summary",
           }
           if mode == "full"
           else {
               "event-identity",
               "event-router",
               "metadata-classifier",
               "host-tests",
               "ownership-tests",
               "build",
               "extended-host-tests",
               "legacy",
               "summary",
           }
       )
       seen_api_ids = set()
       seen_names = set()
       seen_stable_ids = set()
       contexts = []
       for job in raw_jobs:
           api_id = job["id"]
           name = job["name"]
           started_at = job["started_at"]
           assert isinstance(api_id, int) and api_id > 0
           assert api_id not in seen_api_ids
           assert isinstance(name, str) and name
           assert name not in seen_names
           assert name in stable_by_name
           job_id = stable_by_name[name]
           assert job_id not in seen_stable_ids
           seen_api_ids.add(api_id)
           seen_names.add(name)
           seen_stable_ids.add(job_id)
           assert name in required_names or name == "patch-release"
           if mode == "metadata-only" and job_id in metadata_adapter_ids:
               assert job["conclusion"] == "success"
               assert isinstance(job["runner_name"], str) and job["runner_name"]
               assert isinstance(started_at, str)
           elif mode == "metadata-only" and job_id in metadata_skipped_ids:
               # GitHub may stamp started_at on a skipped job, but a real
               # runner is never assigned: both facts are required together.
               assert job["conclusion"] == "skipped"
               assert job["runner_name"] is None
               assert started_at is None or isinstance(started_at, str)
           elif job_id == "patch-release":
               assert job["conclusion"] == "skipped"
               assert job["runner_name"] is None
           else:
               assert name in required_names
               assert job["conclusion"] == "success"
           contexts.append(
               {
                   "conclusion": job["conclusion"],
                   "job_id": job_id,
                   "name": name,
               }
           )
       assert required_names <= seen_names
       return {
           "base_sha": base_sha,
           "contexts": contexts,
           "event": "pull_request",
           "head_sha": head_sha,
           "run_id": run_id,
       }

   opened, title, restore = [
       normalize_run(*spec)
       for spec in run_specs
   ]
   opened_result = candidate_evidence.evaluate_candidate_runs(
       [opened], head_sha=head_sha, base_sha=base_sha
   )
   assert opened_result.eligible and opened_result.run_id == opened["run_id"]
   title_result = candidate_evidence.evaluate_candidate_runs(
       [title], head_sha=head_sha, base_sha=base_sha
   )
   assert not title_result.eligible and title_result.mode == "metadata-only"
   full_title_result = candidate_evidence.evaluate_candidate_runs(
       [opened, title], head_sha=head_sha, base_sha=base_sha
   )
   assert full_title_result.eligible
   assert full_title_result.run_id == opened["run_id"]
   latest_full = candidate_evidence.latest_contexts([opened, title])
   assert latest_full["summary"] == (title["run_id"], "success")
   assert latest_full["host-tests"] == (title["run_id"], "success")
   assert latest_full["build"] == (title["run_id"], "success")
   assert "metadata-summary" not in latest_full
   for job_id in candidate_evidence.METADATA_SKIPPED_JOB_IDS:
       assert latest_full[job_id] == (title["run_id"], "skipped")
   failed_opened = copy.deepcopy(opened)
   next(
       context
       for context in failed_opened["contexts"]
       if context["job_id"] == "summary"
   )["conclusion"] = "failure"
   failed_result = candidate_evidence.evaluate_candidate_runs(
       [failed_opened, title], head_sha=head_sha, base_sha=base_sha
   )
   assert not failed_result.eligible
   assert candidate_evidence.latest_contexts([failed_opened, title])["summary"] == (
       title["run_id"],
       "success",
   )
   all_runs_result = candidate_evidence.evaluate_candidate_runs(
       [opened, title, restore], head_sha=head_sha, base_sha=base_sha
   )
   assert all_runs_result.eligible
   assert all_runs_result.run_id == opened["run_id"]
   restore_result = candidate_evidence.evaluate_candidate_runs(
       [restore], head_sha=head_sha, base_sha=base_sha
   )
   assert not restore_result.eligible and restore_result.mode == "metadata-only"
   failed_restore_result = candidate_evidence.evaluate_candidate_runs(
       [failed_opened, title, restore], head_sha=head_sha, base_sha=base_sha
   )
   assert not failed_restore_result.eligible
   PY
   ```

   The title-only and restore runs alone prove the missing-full negative; the
   copied failed summary proves the failed-full negative without inventing a
   success-shaped fallback.
6. Run exact idempotent cleanup explicitly. The EXIT trap performs the same
   cleanup automatically on any earlier failure:

   ```bash
   exit 0
   ```

   Cleanup restores the original title if necessary, closes without merging,
   deletes the exact remote ref only through a SHA compare-and-swap lease,
   removes the exact isolated worktree/local ref only when their head/ref match
   and the worktree is clean, and deletes only the guarded exact evidence
   directory. A mismatched remote SHA, PR identity, local head/ref/path, or
   dirty worktree is preserved and reported. The EXIT trap retains the primary
   failure status while surfacing cleanup failure.
   Architecture/review comments remain unmarked; only the canonical evolving
   evidence comment carries the one marker.

## TC-WORKFLOW-METADATA-EDIT-RACE-001: Defer metadata edits and reconcile continuity

- **Feature / originating issue:** `workflow-governance` /
  [issue #199](https://github.com/laqieer/fireemblem8-expansion/issues/199).
- **Supported configuration or artifact:** clean source checkout with Python
  3 and synthetic GitHub PR, workflow-run, job, and comment responses; no
  token, live PR, workflow dispatch, ROM, emulator, or ARM runtime is required.
- **Prerequisites and clean starting state:** start at the repository root
  with `scripts/workflow_pilot/pr_metadata.py`, its isolated-launcher mode,
  Build event classifier/continuity contracts, this procedure, and the
  canonical tester registry unchanged.
- **Architecture disposition:** after the third consecutive finding round,
  accepted in place as one typed candidate-run binding model plus a two-phase
  immutable GitHub-authoritative intent/confirmation protocol. No split, new
  subsystem, cross-subsystem timestamp ordering, or mutable local ledger is
  required.

### Actions

1. Run
   `python3 -m unittest scripts.workflow_pilot.tests.test_pr_metadata -v`.
   This suite invokes `/usr/bin/python3 -I
   scripts/workflow_pilot/isolated_launcher.py pr-metadata` with a deterministic
   fake `gh` for edit, reconcile, and evidence-comment paths.
   With authenticated read access, run
   `PR_METADATA_LIVE_REPOSITORY=laqieer/fireemblem8-expansion
   PR_METADATA_LIVE_PR=202 python3 -m unittest
   scripts.workflow_pilot.tests.test_pr_metadata.LivePullRequestMetadataQueryTests
   -v`. This sends only REST/GraphQL reads through `inspect_metadata_history()`
   and executes the exact production metadata-version query. Fixtures may be
   open or closed; merging the fixture PR does not expire the probe. Repeat with
   `PR_METADATA_LIVE_PR=189` to parse a nonempty body-edit connection.
   Exercise synthetic closed fixtures as well: the history probe succeeds,
   while edit, reconcile, and evidence-comment modes reject before mutation.
   Exercise REST null and GraphQL empty-string bodies, title-only edits with
   omitted or explicitly unchanged empty bodies, first body creation, and
   clearing nonempty content when REST returns null. Require one canonical
   empty-body identity, only the actually changed PATCH fields, and body-version
   advancement only for real body changes. Missing REST body and missing,
   null, malformed, or differing GraphQL body fields remain invalid.
   Start first-edit fixtures with zero history and either empty or nonempty
   original content. Return exactly two newest-first revisions after PATCH:
   the new body plus the original, whose `editedAt` equals PR creation and
   whose author matches the PR author. Bind their distinct IDs, shared
   materialization time, and the original's canonical pre-body digest.
   Confirm and reconcile this 0-to-2 transition through the real helper and
   isolated CLI, including applied-target recovery without a second PATCH.
   Repeat a subsequent 2-to-3 edit. Return a real second intervening edit
   (0-to-3 or 2-to-4), or disguise two actual edits as two rows; neither may
   confirm. Forge original content, ID, author, creation/authorship/update
   times, or serialized proof; delete a revision, omit an author, corrupt
   pagination, and put PR creation and first edit in the same ambiguous
   second. Require failure without a confirmation or cached-data abort.
2. Exercise a same-head/same-base full Build whose complete exact Build job
   shape is queued. Attempt a default body edit with exact repository, PR,
   head, and base arguments, and confirm the initially active Build takes one
   complete run/job snapshot before fast defer. Separately exercise a
   mutation-eligible default edit with three complete run/job snapshots:
   initial, pre-intent, and post-intent immediately before PATCH. The first
   full Build succeeds; an active rerun appearing in either later snapshot
   defers the edit. A second-snapshot refusal stops before creating an intent
   or taking the third snapshot.
   Exercise queued zero-job, one-job, current nine-job topology without summary,
   unknown partial, provable active metadata-only, empty/missing-binding active
   queued/in-progress, terminal unbound, explicit-other, and
   multiple/contradictory binding shapes.
3. Repeat with a nonempty essential-contract reason, keep the same full run
   active, validate the pre-PATCH immutable intent comment, PATCH response,
   title `RenamedTitleEvent` and body `userContentEdits(first: 2)` authority,
   including count, newest node identity/timestamps/editor/deletion/diff,
   pageInfo, and `lastEditedAt` consistency, plus the immutable confirmation
   comment. Repeat the essential edit against a fully observed terminal failed
   or cancelled full Build: the edit still completes, but that run remains
   ineligible for successful continuity. The corresponding nonessential edit
   must still reject. A later green metadata-only run must not turn the failed
   full run into merge evidence.
   Make one job queued or in-progress while the run claims completion, at
   either authorization snapshot; reject before creating an intent or PATCH.
   Return real-shaped HTTP 201 creation responses with canonical `Location`
   headers for intent, confirmation, and abort comments. Require acceptance
   only when the header matches the response's exact repository/comment API
   resource. Wrong hosts, repositories, resource kinds, comment IDs, query or
   fragment suffixes, duplicate/control-bearing headers, redirects and an
   unexpected HTTP 200 `Location` must still reject.
   Then present a
   successful runner-backed full Build followed by its failed metadata-only
   continuity run and invoke `pr-metadata reconcile
   --confirmation-comment-id <confirmation-comment-id>`.
   Exercise an old successful metadata run at or below the receipt watermark
   while the new run is not visible, later failed and successful runs, equal
   edit/run timestamps ordered by run number, and a rerun attempt at the
   watermark.
   Exercise PATCH success followed by failed/indeterminate confirmation
   creation, then retry against the unmatched intent with pre-state, target
   state, and invalid third-state outcomes. An unmatched pre-state retry must
   hold without PATCH or abort, even after repeated fresh reads; target-state
   recovery must confirm the exact immutable field/version authority without
   another PATCH.
   Return a complete HTTP 422 validation rejection with `gh` exit 1 from the
   actual PR PATCH. Require a fourth complete run/job snapshot, stable two-pass
   comments, full selected-intent rebind, and a final authenticated GraphQL
   observation before one immutable `patch-rejected` abort. The result remains
   deferred, not successful. Correct the requested values, retain the original
   intent and abort, and retry: require a fresh nonce, ordered successor,
   changed-only PATCH, and matching confirmation. Repeat with supported definite
   HTTP 400/401/403/404/409/429 rejections, canonical empty bodies, and mixed
   title/body requests.
   Replay timeout/network failures, HTTP 408/5xx/202, malformed/absent raw
   envelopes, abnormal `gh` exits, diagnostic-only `HTTP 422`, and HTTP 200 with
   exit 1. Require an error without decision JSON or an abort; the subsequent
   pre-state retry returns a read-only deferred intent hold with no duplicate
   PATCH. Changed requested values remain blocked while ambiguous, but an
   actually applied, owner-authenticated exact target can recover confirmation.
   After a definite rejection, mutate run authority, either stable comment
   walk, every selected intent field/author identity, candidate, metadata
   values/version, or final GraphQL authority; require no cached-data abort.
   Keep the watermark intact while adding a full or metadata run, changing
   attempts, PR binding, status/conclusion, run/job timestamps, job identity or
   runner, or removing an older run. Repeat normal active-run/job progress
   under an essential override. Require the fourth complete snapshot to equal
   the immediately pre-PATCH snapshot: these valid but changed responses must
   produce status 2 without decision JSON, another PATCH, or any terminal.
   A later retry with unchanged pre-state remains a read-only intent hold.
   Introduce a newer active intent or two same-second active intents after
   rejection; require the same hold. Unchanged authority, ordinary comments,
   and well-formed superseded-candidate intents still permit a definite
   rejection abort and corrected successor, including an unchanged active
   Build with an essential override.
   An already observed confirmation or abort takes precedence, including with a
   successor and changed run snapshot present. Contrast run drift before the
   first PATCH: a new intent may abort with the actual final GraphQL
   head/body/version, but malformed final authority must never create an abort
   from cached pre-state. Fail abort delivery with and without actually creating
   the comment: retry consumes a real terminal, never fabricates one.
   Replay these positive/adversarial cases through the isolated fake `gh`
   launcher, including nonzero subprocess status and canonical JSON/exit codes.
   Table-test title-changed/body-provided-same, body-changed/title-provided-same,
   both-changed, and no-change requests. Assert exact `provided_fields`,
   `changed_fields`, changed-only PATCH JSON, per-field version advancement,
   mixed-field retry recovery, and rejection when a supplied unchanged field
   drifts concurrently.
   Add well-formed unmatched and paired intent records from a superseded head,
   then confirm current-candidate recovery ignores them while malformed old
   protected-marker records remain fatal. Exercise same-candidate multiple
   intent selection.
   For a maybe-created confirmation pair, exercise absent, active, failed, and
   canonical successful post-watermark metadata runs. Only the canonical
   completed success may return exit-0 no-op.
   Hide an earlier direct edit's run from all pre-PATCH snapshots, then expose
   it above the watermark before the actual edit's run exists. Replay both
   successful and failed earlier runs through reconciliation and no-op:
   neither may complete, rerun, or authorize no-op for the current edit.
   Publish the original webhook transition through `attest-metadata-event`
   and the workflow's successful fingerprint step. Bind its actual pre/target
   fields, changed set, repository/PR/refs/owner, native metadata instant and
   run ID/number/attempt. Expose a matching run alongside the earlier attested
   unrelated run: require only the matching success or failed-run rerun.
   Repeat with a later attempt of that same matching run.
   Withhold the proof step, change it on the second reconciliation snapshot,
   duplicate matching IDs, or introduce an unproven competing run; require a
   hold without guessing. Replay an earlier identical transition at a different
   native edit instant and a same-second repeated body revision whose raw
   event fields otherwise match. Both must hold; a timestamp or watermark by
   itself is not edit attribution. Ordinary Actions run materialization delay
   still permits a real event-bound positive.
   Exercise edit-bound metadata success/failure followed by a later full Build,
   active/failed/successful newest full authorization, multiple later full
   successes, same-ID full attempts, metadata attempt 2 with the same ID/number,
   ambiguous distinct metadata IDs, and a second reconcile after the exact
   metadata rerun succeeds.
   For both reconcile and authoritative no-op, exercise newest full success
   with an older active full, newest full failure with an older active full,
   and newest active full. Confirm mutation eligibility still blocks on any
   active full. Exercise duplicate attempt rejection and latest-attempt
   collapse for the same run ID/number.
   Exercise equal-second intent/confirmation comments as valid and a
   confirmation predating intent as invalid.
   After intent creation, inject candidate/head/base, pre-state, supplied
   unchanged-field, title/body version, run-snapshot, and transaction-comment
   drift before PATCH while the newly created intent remains present, unchanged,
   and active. Require zero PATCH and one immutable owner-authored abort
   comment. If a confirmation or
   abort is already observed, require deferral without a second terminal.
   Separately delete or unmark the selected intent, change its canonical nonce,
   pre-version, or creation/update timestamps while both timestamps remain
   equal, or change each owner ID/login/type/site-admin/association field or
   remove its author. Return the same changed set in both complete comment
   walks after creation and on pre-state retries, including when candidate
   drift would otherwise require an abort. Require an error, no PATCH, and no
   cached-data confirmation or abort. Repeat after an already validated PATCH
   and on target-state recovery: no further PATCH or terminal may be written.
   At that confirmation refresh, an unchanged intent with a newly observed
   confirmation or abort must instead defer, even with a successor present.
   On reconciliation's second snapshot, repeat intent deletion, canonical
   receipt/ownership drift, and equal creation/update timestamp changes on
   either selected comment; require no rerun. An unchanged complete pair still
   reruns only its failed metadata run.
   Assert that the abort's observed head/base, metadata digest, and version
   equal the validated final GraphQL observation, not the cached pre-state.
   For malformed repository identity, missing/invalid observation fields, or
   inconsistent body-version evidence, require an error without fabricated
   abort evidence or PATCH.
   Replay maybe-created, duplicate, forged, edited, and
   contradictory abort comments; malformed historical abort markers remain
   fatal.
   Assert the production GraphQL call is the final network request immediately
   before PATCH. Inject drift during the last run/comment response and expose
   it in that GraphQL response; require zero PATCH.
   Retry an aborted intent: target-match follows no-op/refusal, while a
   remaining change creates a fresh ordered successor intent with a distinct
   nonce.
   Exercise an abort and successor intent in the same second with increasing
   comment IDs, followed by confirmation and reconcile/recovery. Repeat with
   two same-second unclosed active intents and require ambiguity. Reject
   duplicate or conflicting confirmation/abort terminal links.
   For both confirmation and abort, accept equal-second higher-ID edges; reject
   later-time equal/lower IDs and earlier-time higher IDs. Timestamp and
   comment-ID ordering are independent invariants.
   Place malformed intent/confirmation/abort marker-like text from
   contributor, bot, deleted, and non-owner comments before and after valid
   owner transactions; require it to be ignored. Owner malformed markers
   remain fatal.
4. Update a synthetic canonical marked evidence comment owned by the
   repository owner while the same full Build is active.
   Add each intent, confirmation, and abort marker to its replacement body,
   including quoted, embedded, and duplicate forms; require rejection before
   PATCH. Supply mixed-marker or malformed typed transaction bodies and
   require rejection before POST. Valid canonical replacements and all three
   valid transaction kinds remain accepted by the same body classifier.
   Delete an already fetched ordinary comment between canonical page-one and
   page-two responses so an intent, confirmation, abort, or second canonical
   evidence marker shifts onto the previous page. Require two complete ordered
   observations to disagree and reject before transaction use or PATCH.
   Unchanged complete walks succeed; reordered or changed records reject.
   Reconcile an explicitly selected valid confirmation after an unconfirmed
   successor intent appears, both in the same second and later. Require the
   original pair's exact metadata/version and run evidence to remain usable;
   actual subsequent metadata/version changes must still reject the old pair.
5. Replay stale head/base before edit and reconciliation, post-edit identity
   drift, mutation-response mismatch, incomplete run/job/comment pagination,
   redirects, contradictory/malformed/duplicate/looping Link relations,
   duplicate run identities, wrong workflow/repository/PR/head/base/event/path,
   unknown conclusions, cancelled and other non-failure metadata conclusions,
   noncanonical failed-metadata jobs, missing/unauthorized comment authors,
   cross-repository comment identities, duplicate/embedded markers, duplicate
   JSON keys, malformed repository identity, mixed run/attempt/repository/head
   job pages, duplicate/identityless jobs, workflow URL/identity drift,
   canonical owner/repository and numeric-repository Link forms, other numeric
   IDs, percent/path ambiguity, and shell/API metacharacters.
   Replay bare CR/LF, NUL, tab, vertical tab, form feed, DEL, obs-fold, and
   Content-Type/Link/Location control injection. Replay same-login/wrong-owner
   IDs and strict job/run timestamps including malformed dates, 24:00,
   timezone offsets, missing fields, reversed chronology, queued/in-progress
   nullability, run bounds, and GitHub's captured one-second skipped-job quirk.
   Exercise repeated `Vary`, `Cache-Control`, and `Link` fields, forbidden
   singleton/unsupported repeats, exact JSON media type with valid parameters,
   `application/jsonp` and malformed media parameters, multiple-Link relation
   duplicates, and skipped timing deltas `-1`, `0`, `+1`, and `+28` seconds.
   Send real fake-`gh` subprocess bytes through the isolated launcher: consistent
   LF and CRLF succeed, while bare CR, mixed endings, and invalid UTF-8 reject
   before authority parsing. Confirm the subprocess does not normalize line
   endings and UTF-8 request JSON round-trips without shell interpretation.
   Accept the real `gh --include` rendering with only the status line ending
   in LF and all header fields/separator ending in CRLF; reject a mixed field
   line even under that explicitly recognized envelope.
   Accept quoted parameter values containing literal `=` and `;`; reject
   escaped quotes, every backslash, extra unquoted equals, empty names/values,
   trailing junk, duplicate names, unterminated quotes, and ambiguous spacing.
   Exercise an active run whose jobs complete after its stale `updated_at`, a
   refreshed terminal run whose updated bound precedes job completion, and
   partial active graph materialization across all three snapshots.
6. Parse `docs/test-cases/registry.json` and run
   `python3 -m unittest scripts.docs_check_tests.test_development_workflow_skill -v`.
   Run
   `python3 -m unittest tests.workflows.test_build_ci_topology.ConsolidatedBuildTopologyTests.test_metadata_event_producer_publishes_only_immutable_trigger_binding -v`.
   This executes the actual workflow producer/marker shell steps with local
   event files. Require the digest only for an authenticated valid immutable
   trigger, no digest for an invalid sender or older base without the producer,
   unchanged metadata classification on that absence, and rejection of a
   malformed marker digest.
   Run
   `python3 -m unittest tests.upstream_port.test_verify.VerifyCliCwdTests.test_metadata_event_setup_is_closed_and_not_a_local_gate -v`.
   Require the upstream verifier to accept the complete producer/output/marker
   setup while retaining the complete local gate commands. Remove, duplicate,
   relink or weaken either setup step, mutate event/run/attempt inputs, and
   fabricate proof in either no-proof branch: each must reject before gates
   execute. Equivalent command spacing and environment-mapping order must
   preserve the parsed contract.

### Expected result

The default edit returns a structured `deferred` decision, performs no PR
metadata mutation, and points to the canonical evidence-comment command. An
initially active full or unproven Build fast-defers after one complete run/job
snapshot. A mutation-eligible default edit queries three complete run/job
snapshots (initial, pre-intent, and post-intent) and refuses mutation if the
authority changes or either later snapshot has an active full Build. A
nonempty essential override revalidates exact current head/base immediately
before PATCH, refreshes run authority, requires the PATCH response to attest
the requested exact metadata, leaves every same-SHA full Build active, and
returns the exact confirmation-comment-bound reconciliation command. Before
PATCH it creates a dedicated immutable owner-authored intent comment binding
repository/PR/head/base/workflow, pre-state/target/request digests,
metadata-specific pre-version, nonce, and the highest fully observed pre-PATCH
run ID/number/creation timestamp watermark. The pair is not a secret,
signature, or authentication token. After strict PATCH attestation, GitHub
title-event and body-edit
version authority is bound by a second immutable confirmation comment. The
refetched explicitly selected pair and live identity, author, digest, version, and
watermark checks supply authority without cross-subsystem timestamp causality
or a mutable ledger. After the newest exact full Build succeeds, reconciliation
revalidates the receipt, immutable head/base, and complete run/job authority
twice, then POSTs only
`actions/runs/<metadata-run-id>/rerun`. The rerun is the existing lightweight
`pull_request: edited` path, so code is not rebuilt. Only a completed failed
metadata run with canonical successful setup/adapters, canonical skipped
expensive jobs (plus any historical skipped publisher), and failed summary is eligible. A successful or
active metadata run is respectively complete or deferred. Canonical comment
PATCH uses only the exact repository-owner comment
`issues/comments/<id>`, requires mutation-response identity/body attestation,
and emits no `pull_request: edited` event. Unmarked contributor, bot, and
deleted-author comments remain valid after exact repository/PR/comment schema
checks. Run, job, and comment pagination accepts both captured GitHub
`/repos/<owner>/<repo>/...` and `/repositories/<numeric-id>/...` Link forms
only when the PR-bound numeric ID, suffix, query, and page are exact. Workflow
and job payloads bind complete API/HTML/check-run/run/repository/head/branch/
attempt identities, including each job's exact check-run URL, before any run
can authorize mutation or reconciliation. Header parsing permits only
consistent LF/CRLF framing and visible ASCII with SP-only value separation.
The marked comment author must match the exact owner numeric user ID as well
as owner login/type/site-admin/association.
The GraphQL `Actor` selections request `__typename` and `login` on the
interface, and request `databaseId` only through `... on User` inline
fragments. User actors retain exact numeric-ID/login binding; bot, deleted,
null, and wrong-ID actors fail wherever owner edit authority is required.
Body authority requires a canonical newest-first `UserContentEdit` connection.
No-edit state is explicit. The first body edit changes `totalCount` from 0 to
2 by materializing the original snapshot with the first real edit; every
subsequent edit increases it by exactly one. The original node must bind the
same PR's creation time and author, the intent's canonical pre-body digest,
and a distinct ID. Original and latest `createdAt` / `updatedAt` share the
materialization time, while original authorship strictly precedes latest
`editedAt`. The latest editor and body remain owner/target-bound.
`body_original` retains the typed original proof in the count-2 metadata
version, so changing it invalidates a stored confirmation. A lone revision,
missing/permission-omitted connections, count rollback/jump, two real edits,
forged original snapshots, reused/duplicate nodes, deleted nodes, malformed
pageInfo, wrong editor, diff/body mismatch, and same-second ambiguous
authorship or multiple same-second edits fail closed.
Exact-head runs use typed `explicit-same`, `explicit-other`, and `unbound`
binding states. Active unbound runs block; terminal unbound runs cannot provide
evidence; explicit-other runs are ignored only after full validation; multiple
or contradictory bindings fail closed.
Canonical JSON reserves `run_id` for Actions workflow runs and `comment_id`
for the canonical issue comment; the optional fields are always serialized,
strictly mutually exclusive, and `comment-updated` requires only `comment_id`.
An `updated` decision returns only validated intent and confirmation comment
IDs/URLs, never caller-trusted transaction fields.
The intent separates `provided_fields` from `changed_fields`. Every supplied
value is digest-bound and participates in complete target-state validation;
only changed fields enter PATCH JSON and require title/body history
advancement. Mixed edits therefore cannot demand an event for an unchanged
supplied field. Zero-change input creates no intent and uses authoritative
no-op semantics.
All transaction comments are structurally and authoritatively validated before
candidate selection. Well-formed records are grouped by repository/PR/head/
base/workflow; superseded candidate groups are ignored, while malformed
historical records remain fatal. Confirmation may equal intent's second
because its exact intent ID/nonce proves linkage, but confirmation may not
predate intent.
An authoritative-pair no-op additionally requires the exact
confirmation-bound metadata run to pass canonical completed-success
validation; visible active/failure states defer with reconciliation guidance.
The final pre-PATCH revalidation completes run and transaction-comment
authority first, rebinding the complete unchanged owner-authenticated selected
intent, then performs one complete GraphQL
repository/owner/PR/ref/metadata/version request as the final network operation
before PATCH. Other authority drift emits an immutable abort/supersession
comment and zero PATCH only while that selected intent remains valid.
A missing, changed, or no-longer-owner intent cannot authorize a cached-data
abort or confirmation. Confirmation refreshes and rebinds the intent again
after PATCH or on target-state recovery, preserving observed terminal
precedence. The final GraphQL/PATCH window is explicitly non-atomic; strict
PATCH-response candidate and complete-state attestation detects any resulting
drift rather than claiming atomicity. Abort closes only its intent; a remaining
change creates a newer successor intent, while target-match follows normal
no-op/refusal behavior. Non-owner marker-like text is ordinary ignored content
and cannot enter protected parsing; owner malformed records remain fatal.
Transaction graph construction links confirmations and aborts before active
selection. Terminal intents are excluded; equal-second aborted predecessor
plus higher-ID successor is valid, while multiple equal-second unclosed active
intents remain ambiguous. Comment IDs refine same-second ordering only after
predating edges have been rejected. Every terminal edge independently requires
a strictly higher comment ID and a non-predating timestamp.
Full authorization and transaction metadata selection are independent. The
newest exact full run must be canonical success, but the transaction metadata
run additionally needs an immutable run-emitted webhook transition attestation.
Explicit-same shape and a number above the watermark are only filters.
The attested pre/target metadata and changed set must match the authenticated
pair. The event metadata instant must match every changed native field's edit
instant, with strictly earlier pre-versions; same-second repetition or
disagreeing field instants remain unprovable. This is not a comparison with
Actions run or issue-comment clocks. The uniquely matching run need not be
newer than a later full run. Its stable ID/number survives attempts; multiple
distinct edit-attested metadata IDs are ambiguous and fail closed.
Attested unrelated events are ignored; missing proof, an unproven competitor
or proof drift cannot authorize completion, no-op or rerun. Older trusted bases
and historical runs without the step explicitly hold rather than fabricating
trigger evidence from current PR metadata.
Mutation eligibility remains deliberately different: any concurrent active
full blocks a new edit. Reconcile/no-op first collapse same run ID/number to
the latest attempt, then evaluate only the newest distinct relevant full or
unproven run. Older active/failure states do not override newer terminal
authority; duplicate/ambiguous run numbers fail closed.
The isolated launcher emits one exact canonical JSON line with status `0` for
success/no-op/complete, status `3` for deferred/refused, and status `2` with no
decision JSON for invalid arguments, files, or API authority.
For a definite, freshly revalidated PATCH rejection, status `3` carries the
intent and `patch-rejected` abort IDs. Corrected values can then create a
successor. Fresh run/job authority must equal the complete pre-PATCH snapshot
and the selected intent must remain uniquely latest and active; watermark
survival alone does not authorize an abort. Valid authority drift or active
selection ambiguity returns status `2` without a new terminal, while an
already observed terminal still defers before those comparisons.
A read-only ambiguous pre-state hold carries only its intent ID/URL,
no terminal, and `mutated: false`; it is not a confirmed pair. Updated/recovered
and authoritative no-op results still require their original pair contracts.
Job/run timestamps use exact UTC-second GitHub RFC3339 syntax and enforce
status-dependent nullability and chronology, with only the captured
one-second unassigned-skip timestamp exception.
The centralized header policy combines only explicitly permitted RFC fields,
requires exact parameterized `application/json`, and rejects unsupported
repeats. The centralized job state model admits skipped completion deltas only
at `-1` or `0` seconds; positive duration cannot become runner-backed success.
Active zero/partial/unknown graphs conservatively defer before terminal
exact-set classification. A successful metadata classifier may prove an active
metadata-only run harmless. Completed runs refresh exact run authority and
retain exact job sets plus terminal upper bounds; active `updated_at` is not a
live completion bound.

### Negative control

The pre-fix selector considered a unique higher run number causal evidence:
an earlier direct edit's delayed successful run returned `complete`/`no-op`,
and its failed run could be rerun while the confirmed edit's actual run was
absent. The helper and isolated CLI now require matching immutable event
evidence. An always-hold replacement must fail the real producer/matching-run
positive, while a timestamp-only or watermark-only replacement must fail the
delayed-earlier and same-second ambiguous-version controls.
Before the coupled upstream integration fix, the verifier rejected the valid
new router output before it could recognize the producer/marker setup, so the
real `verify --dry-run` failed instead of listing its then-28 gates. The regression
must accept that complete setup without replacing the closed validator with an
any-step or any-output allowance.

Before the rejection fix, a real HTTP 422/nonzero `gh` response lost its status,
left an active intent, and permanently rejected corrected values. An unmatched
pre-state retry also issued a potentially duplicate PATCH. The actual-function
and isolated CLI regressions must fail if either behavior returns. Treating
stderr as rejection proof, aborting after an ambiguous response, skipping fresh
authority or complete rebind, or accepting an intent-only no-op also fails.
The post-rejection drift regression keeps the watermark unchanged while
introducing valid new runs, attempts, bindings, conclusions, timestamps, or job
changes. Before this correction, both the real helper and isolated launcher
created a `patch-rejected` abort anyway; newer or same-second ambiguous active
intents also incorrectly permitted an abort. Those controls must now fail
closed, without disabling unchanged definite-rejection recovery, observed
terminal precedence, or actual-observation aborts before the first PATCH.

Empty override reasons, stale/new candidate identity, incomplete or drifting
pagination, redirect or Link-header contradiction/loop, duplicate run
IDs/numbers, wrong workflow/repository/PR/head/base/event/path identity, unknown
status/conclusion, cancelled or other non-failure metadata runs,
mixed/partial/duplicate or noncanonical job names/outcomes, missing/deleted or
unauthorized comment authors, cross-repository comment IDs,
duplicate/embedded/non-standalone evidence markers, mutation-response
mismatch, wrong numeric repository IDs, owner/repository drift,
percent-encoded/path-ambiguous or cross-form looping Links, workflow payload
identity drift, duplicate/identityless jobs, mixed run/attempt/repository/head
job pages, inconsistent job URLs, malformed or duplicate-key JSON, and
same-login/wrong-owner IDs, header control/DEL or obs-fold smuggling,
malformed/24:00/offset/missing/reversed timestamps, invalid queued/in-progress
timing, out-of-run chronology, repeated singleton/unsupported headers, JSONP,
malformed media parameters, duplicate multiple-Link relations, skipped `+1`
or `+28` durations, treating a partial active graph as terminal/error/harmless,
using stale active `updated_at` as a completion bound, omitting terminal
refresh, accepting an active unbound run, using terminal unbound evidence,
accepting multiple/contradictory bindings, selecting old successful metadata
at or below the receipt watermark, accepting a rerun attempt at the watermark,
comparing Actions and comment timestamps causally, accepting an old pair after
a newer metadata edit, or accepting deleted/edited/non-owner/bot/wrong-ID/
cross-repository/duplicate/ambiguous intent or confirmation comments, later
direct metadata edit, title/body edit-and-revert, same-second body edit/revert,
malformed transaction schema/identity/field/digest/version/
workflow/watermark, or unreconciled unmatched intent, and
repository/command injection all fail
closed. No tested path calls a
cancellation endpoint or a full-workflow dispatch endpoint.
The pre-fix negative control uses two equal post-selection comment walks that
omit intent 401 or change its canonical nonce in the same second: the old
helper appended an abort for the missing intent, or PATCHed and confirmed the
cached nonce. Neither stable pagination nor equal timestamps alone proves the
selected record survived unchanged. The deterministic refresh tests exercise
that original failure and the coupled confirmation/recovery paths directly.

### Interactions and save compatibility

Dependencies are the existing Build event classifier, metadata continuity
attestation and job shapes, canonical evidence-comment contract, isolated
workflow-pilot launcher, and exact GitHub PR/run/job authority. Dependents are
automated delivery coordinators and contributors changing PR title/body after
candidate push. It conflicts with mutable pending-state ledgers, incomplete
pagination, stale identity, direct unguarded metadata edits during active
Build, and same-SHA cancellation. It changes no feature profile, save,
generated data, localization, ROM/RAM, target build, gameplay, modern
debug/release output, or archival behavior.

### Automation

`python3 -m unittest scripts.workflow_pilot.tests.test_pr_metadata -v`
behaviorally executes refusal, override, revalidation, reconciliation,
comment-only routing, pagination, schema, and injection controls through both
direct helper calls and the isolated launcher with deterministic fake `gh`.

`python3 -m unittest scripts.docs_check_tests.test_development_workflow_skill -v`
parses the contributor/workflow guidance and indexed tester contract.

### Cleanup and limitations

No cleanup is required because every GitHub response and mutation is
synthetic. The API cannot make the identity check and PATCH atomic, so the
helper fast-defers an initially active candidate after one complete snapshot;
a mutation-eligible edit takes three snapshots (initial, pre-intent, and
post-intent), creates its intent between the latter two, revalidates identity
immediately before PATCH, requires the
authoritative response and metadata-specific version authority, then creates
the confirmation. The pair constrains reconciliation to a later numbered
exact-bound metadata run without a mutable local ledger.
Replay a confirmation arriving during post-intent revalidation, including
simultaneous run drift and a new successor intent. The helper must return
`deferred` with the observed confirmation ID and reconciliation command,
without PATCHing metadata or appending an abort. Replay an observed abort and
require no duplicate terminal comment. Confirm that the original valid pair
still reconciles after its exact full and metadata runs succeed, while
misbound or contradictory terminal records continue to fail closed. These
observations do not provide a distributed writer lock: the supported delivery
coordinator serializes helper invocations.
For canonical comment updates, mutate each response author field (ID, login,
type, site-admin status, and association) independently and require rejection;
the owner-scoped unmodified response remains accepted.
There is no manual-only criterion. No ARM runtime test is needed because this
is host-only delivery orchestration.

## TC-WORKFLOW-PILOT-BASELINE-001: Freeze reproducible pilot baseline and decisions

- **Feature / originating issue:** `workflow-governance` /
  [issue #176](https://github.com/laqieer/fireemblem8-expansion/issues/176).
- **Supported configuration or artifact:** clean source checkout with Python
  3, Git, the immutable issue #176 fixture, and the versioned decision record;
  no GitHub token, live workflow, ROM, emulator, or game build is required.
- **Prerequisites and clean starting state:** start at the repository root with
  `.github/workflow-pilot-decisions.json`,
  `scripts/workflow_pilot/tests/fixtures/baseline.json`, and
  `scripts/workflow_pilot/reporter.py` unchanged. Remove any prior
  `build/test-artifacts/workflow-pilot` directory.

### Actions

1. Run
   `python3 -m unittest discover -s scripts/workflow_pilot/tests -p 'test_*.py' -v`.
2. Create `build/test-artifacts/workflow-pilot`, then run the documented
   reporter twice with `--repository-root .` over the committed fixture,
   decision record, and expected values, writing `first.json` and
   `second.json` in that directory.
3. Compare `first.json` and `second.json` byte for byte. Parse either file and
   verify the frozen window, identity arrays, 64-PR/9.4-hour delivery baseline,
   326 exhaustively partitioned Build outcomes, one active run, 51
   duplicate-SHA Builds, and PR #150's review/Build/base-change/close-reopen
   metrics. Confirm first-push-to-clean-review is `unavailable`, its reason is
   `historical-review-thread-events-not-collected`, `pilot_ready` is false,
   and `median_hours` is null while 34 reviews, 101 findings, and zero current
   unresolved findings remain reportable.
4. Inspect the focused suite's positive classification fixture for inclusive
   boundaries, cancellation, supersession, unchanged-SHA duplication, stack
   ancestry, generated-only work, bulk deletion, reverts, still-running work,
   cross-PR SHA-bound safety outcomes, and pilot overhead.
5. Inspect the temporary-Git-repository controls: an exact pre-review override
   tree passes; a missing decision file, missing/changed entry, digest
   mismatch, non-candidate SHA, post-review commit, backdating, reordering, and
   recomputed current-record mutation each fail.
6. Inspect the resolution controls: cumulative unresolved identities,
   resolution before/after review, absent source timing, direct synthetic
   `resolved_at`, and an untrusted source kind. Confirm only a complete
   identity-bound GitHub webhook history can produce numeric clean-review
   evidence.
7. Inspect the executable lifecycle controls. Confirm the reporter creates a
   bounded plain artifact sandbox beneath the checkout's ignored
   `build/test-artifacts/` directory, while retaining the original checkout
   and object database as separate immutable Git authority. Confirm it removes
   each allowlisted decision/fixture/reporter artifact copy in turn, runs the
   declared production reporter consumer and/or focused reporter consistency
   test, observes the named semantic failure, restores the copy, and observes
   success. Confirm automatic bounded cleanup, unchanged source artifact
   bytes, and fail-closed stale, fabricated, or fixture-command substitutions.
8. Inspect the CI authority-hydration fixture: an exact-ref shallow checkout
   in a new object-clean temporary repository initially lacks a test-created,
   force-pushed unreachable commit, and the prior all-head refspec still cannot
   recover it with lazy fetching disabled. The strict exact-commit seam
   restores it without changing `HEAD`, refs, or `FETCH_HEAD`. Separately,
   confirm production extraction returns exactly the unique commit identities
   in the committed full baseline and that setup remains absent from the 30
   local gates.
9. Mutate every combined worker with container/services/strategy/permissions/
   defaults/advisory/environment/concurrency/uses/secrets/shell execution
   context, and mutate the exact classifier `needs`/`if` edge, including
   spaced, quoted, escaped, tagged, explicit, flow, duplicate, reordered, and
   wrong-value forms.
10. Place a real `sitecustomize.py` that exits successfully before ordinary
    Python commands. Confirm normal startup is bypassed while the three
    baseline `/usr/bin/python3 -I` launcher modes and the event-classifier mode
    execute, and arbitrary modes, arguments, roots, or launcher/`-I` changes
    fail.
11. Feed the workflow mirror parser a long repeated
    `a\n        ` environment adversary and require bounded completion with
    the same accepted/rejected structural results.
12. Run `python3 scripts/check_docs.py --check`.

### Expected result

Both reporter outputs are byte-identical canonical JSON and match
`baseline_expected.json`. The active run remains active at the frozen
measurement boundary even though GitHub later completed it. Current PR and
artifact states are derived from authoritative facts and disposition history;
threshold override decisions contain no editable introduction timestamp, SHA,
diff, run conclusion, or copied current-state field. Every override is read
from the cited candidate commit and first-reviewed commit trees. Every review
SHA is an exact member of its PR's candidate-history set and available by the
review timestamp. Before these calculations, the repository origin, base,
every commit object, exact parents, timestamps, messages, PR candidate/merge
ranges, and referenced history are validated against the real object database.
Commit messages are decoded as UTF-8 from the raw commit object, with exactly
one conventional terminal LF removed; additional trailing blank lines and all
other authored whitespace remain exact authority. Every numeric schema field
requires a JSON integer that is not a boolean before any range or version
comparison, while declared boolean fields remain valid.
Every Git subprocess uses resolved `/usr/bin/git`, explicit
`--no-replace-objects -C <validated-root>`, and a constructed minimal
environment with no inherited Git redirection or configuration controls.
Repository grafts, replacement refs, and object-alternate files fail closed;
hydration alone permits the fixed bounded `origin` fetch.
Commit hydration keeps `blob:none`, then strict fixture/decision relationships
derive only override-introduction and first-review decision-record blob IDs.
Those exact blobs are fetched separately while unrelated blobs, HEAD, refs,
and FETCH_HEAD remain unchanged.
The fixture parent graph also derives a minimal maximal-tip set and exact
lightweight remote anchor names. Only a complete exact fixed-origin namespace
may hydrate commits; local refs and FETCH_HEAD remain unchanged, and remote
GC/repack retains otherwise force-pushed history. The read-only print mode
emits the same mappings twice without remote mutation.
The expected cohort seal binds normalized identities plus review, event,
timestamp, PR/SHA, and other metric relationships.
Every Build and non-Build workflow run is also rejected when its creation
precedes the repository-validated commit time of its head SHA.
The independent decision seal binds all validated PR decisions, artifact
admission/disposition semantics, authoritative artifact sources, lifecycle
proofs, review boundaries/events, and dependency associations.
PR #150's clean-review timing remains explicitly unavailable because no
authoritative historical thread events were collected; no numeric result is
emitted or eligible for pilot comparison/promotion.

The positive controls report Build/review savings beside coordination and
metadata-maintenance overhead. Every authoritative workflow run has a coherent
status/timestamp interval before Build selection. Stack depths are proven from
the complete parent-decision chain, including a genuine depth-three exception.
Checkpoint, dependency-change, and pre-graduation removal proofs preserve
necessary artifacts after a named semantic failure; every historical
restoration passes, and a deletion-ready artifact passes only with `Delete`.
Every proof kind independently has the correct identity, artifact, trigger,
strictly later time, shared named reason, semantic outcome, restoration, and
current-disposition causality; one invalid record cannot be masked by valid
siblings or event ordering.
The committed artifacts additionally pass actual sandboxed removal/failure
and restoration/success execution through the closed command allowlist while
the plain `build/test-artifacts/` sandbox continues to use the immutable
original checkout and object database as separate Git authority.
Each child uses only `/usr/bin/python3 -I` and the copied closed launcher with
explicit sandbox/authority/check arguments; repository and user-site
`sitecustomize.py` exit hooks cannot run first, and root/mode/check/extra
argument mutations reject.

### Negative control

The suite rejects a boundary record outside the snapshot, terminal/active
status contradiction, cancelled-run misclassification, missing Actions page,
missing issue/commit/review/decision, derived-fact override, post-review
override insertion/backdating/reordering/current-record mutation, a cited tree
without the exact override entry, cumulative unresolved findings, post-review
resolution, direct synthetic resolution timestamps, untrusted/incomplete
resolution history, wrong-PR or missing event SHA, fabricated repository
identity/commit/parent/timestamp/message, base-only/merge-only/unrelated/future
review commits, incoherent candidate/merge or review history, any
PR/issue/review/run/finding/commit identity or metric-relationship
substitution, boolean substitution for any integer/version/count field,
commit-message trailing newline/space/blank-line/body/leading-whitespace
mutation, an omitted identity seal, PR events before creation, invalid
open/closed phases, future event or workflow-run SHAs,
earlier/equal/unrelated/fabricated reverts,
non-final/multiple/mixed-case/short/uppercase or text-padded revert trailers,
inconsistent stack parent/base/depth, missing parent decisions, stack cycles,
false depth-two or depth-three claims, an older spotlight run outside the
cohort, impossible non-Build intervals, incoherent
queued/in-progress/completed timestamps, unreported accepted conclusion,
unknown risk/event/edge/disposition, unclaimed or ambiguously owned dependency
edge, orphan/duplicate/expired artifact, deletion-ready non-Delete artifact,
non-causal or missing deletion proof, premature disposition, stale lifecycle
expiry, any failed historical restoration, an empty/fabricated Git authority,
a stale or fabricated executable proof, ambient Git directory/work-tree/object/
alternate/config/replace redirection, local graft/replace/alternate authority,
mixed proof semantic/restoration/reason/identity/time/kind/disposition state,
and any unallowlisted fixture command.
Build topology mutations also reject pilot commands hidden by `|| true`, `; true`,
`&& true`, wrappers, substitutions, or changed redirections.

### Interactions and save compatibility

Dependencies are none. Dependents are issues #177, #178, #179, #180, and
#181. Conflicts are none with CI selection, review ordering, merge gates,
runtime/gameplay, configuration, save, generated game data, localization, or
archival output. Modern debug, modern release, and archival impact are all
none; there is no feature flag and no ROM/RAM or save-format impact.

### Automation

`python3 -m unittest discover -s scripts/workflow_pilot/tests -p 'test_*.py' -v`
parses the decision and fixture schemas, reproduces every frozen calculation,
compares canonical bytes, and exercises all positive/adversarial controls
above. Build CI runs the same command plus the baseline reporter with
`--repository-root "$GITHUB_WORKSPACE"` in its existing required `host-tests`
job. Before those reporter commands, CI hydrates the fixture's exact commit
authority and proves exact `HEAD` and refs are unchanged. The parsed workflow
topology suite fails if classification, hydration, pre-pilot step
order/content, scrubbed protected-step environments, ownership, or
checked-out-root binding is removed or weakened. It also requires the
router, mode-classifier, and each combined worker's exact direct job mapping and values; no
container or alternate execution context can replace the reviewed Ubuntu
host.
The no-checkout identity validator and protected Python steps use only closed
trusted setup, so repository or
user site customization cannot run before control. The workflow mirror parser
uses deterministic line/indent parsing rather than an ambiguous multiline
regular expression. Cross-checkout verification parses target workflow data
without importing target code and requires complete source/target job and step
sequences: counts/order, unique names, setup-versus-gate roles, immutable
actions, run argv, `env`/`with` mappings, direct fields, and root execution
before dry-run or execution. Unnamed non-checkout steps, duplicate setup names,
complex keys, and extra jobs fail closed. Workflow execution context is
exactly name/triggers/read-only permissions/jobs with no workflow env,
defaults, or concurrency. The identity validator is exactly Ubuntu, five minutes, its
outputs/environment, and one trusted shell step. The router is exactly Ubuntu,
five minutes, its outputs/environment, and three setup steps; the
mode-classifier is a separate five-minute one-step check. The comprehensive
`build` job has exact identity/classifier edges, Ubuntu, 90 minutes, its
allowlisted env, and steps, including master-only packaging; host,
extended-host and legacy remain 60 minutes; ownership has a bounded 90-minute
CI envelope, while identity/router/classifier and summary remain 5;
self-hosted/container/service/strategy/default shell or any other execution
field fails before dry-run.
Patch packaging and summary remain parsed structures: successful authenticated
master-push-only steps, existing checked release output, expected commit/profile,
the existing producer/verifier, private-input cleanup and pinned patch-only
upload. No duplicate profile build or local gate is introduced. Summary retains
`always()`, identity/classifier plus all four ordered worker dependencies,
canonical name, five-minute context and fail-closed results. Historical prior PR
runs may carry an extra canonical skipped publisher job, never a missing worker.
Neither is locally executed, but any
runner/condition/needs/permission/env/step/command/action/alternate-context
drift rejects before dry-run.

`python3 scripts/check_docs.py --check` validates this complete procedure,
registry ownership, links, and automation evidence.

### Cleanup and limitations

Remove `build/test-artifacts/workflow-pilot`. No remote state was read or
changed by the procedure. The frozen fixture can reproduce only the explicitly
captured source semantics. GitHub's historical thread-resolution timing was
not captured for this baseline, so the reporter preserves counts/current
state while making the timing metric and its pilot readiness unavailable.
Future numeric evidence requires complete GitHub
`pull_request_review_thread` webhook delivery capture. No manual-only
criterion applies.

Rollback is a normal revert of issue #176's dedicated commit; no workflow or
game behavior needs a compensating change.

## TC-WORKFLOW-GATE-OWNERSHIP-001: Resolve every admitted path to complete validation ownership

- **Feature / originating issue:** `workflow-governance` /
  [issue #180](https://github.com/laqieer/fireemblem8-expansion/issues/180).
- **Supported configuration or artifact:** clean source checkout with Python
  3, Git, and the committed report-only validation ownership graph.
- **Prerequisites and clean starting state:** start at the exact repository
  root with `.github/validation-ownership-graph.json`, its strict schema,
  reporter, generated-data and tester-case registries, Build workflow,
  Makefiles, `/usr/bin/make`, `/usr/bin/unshare` with mount/network/PID
  namespaces plus either user namespaces or passwordless exact
  `/usr/bin/sudo`, a static-capable `/usr/bin/cc`, the host C++ compiler,
  libpng/zlib development headers and libraries plus `pkg-config`
  (`libpng-dev` and `pkg-config` on Ubuntu),
  ARM compiler/binutils and newlib (`binutils-arm-none-eabi`,
  `gcc-arm-none-eabi` and `libnewlib-arm-none-eabi`),
  the existing pinned host Python environment, and the manual-handoff
  contract unchanged. No token, ROM, emulator, or remote workflow is required.

### Lifecycle time controls

Run `python3 -m unittest scripts.validation_ownership.tests.test_reporter.ArtifactLifecycleTests -v`.
The deterministic controls supply a fixed test clock to the actual lifecycle
validator; production uses host UTC, with no candidate clock override. Keep
history before the check and put expiry after the last history but before,
equal to, and after the check. The first two must reject a non-Delete artifact;
future and null expiry must pass. A future Delete cannot excuse current
expiry. A valid past Delete must still satisfy every proof and strict UTC
timestamp rule. The old history-time comparison admits the expired controls;
neither sleeping nor changing the machine clock is needed.

### Bounded outcome custody prerequisite (O1–O6)

This subsection belongs to **TC-WORKFLOW-GATE-OWNERSHIP-001**, under the
[pre-edit #180 freeze](https://github.com/laqieer/fireemblem8-expansion/issues/180#issuecomment-5733870035).
It is a host API prerequisite, not a new namespace mode or a replacement for
the broader case. See the [API/storage contract](../ownership-probe-foundation.md#opt-in-outcome-custody-observation-not-qualification).

1. From the unchanged source root on Linux/CPython, run:

   ```sh
   python3 -B -m unittest scripts.validation_ownership.tests.test_foundation.OutcomeCustodyTests -v
   ```

   These O controls call the real `ProbeBudget.run`, lifecycle run/main,
   cleanup, framing, snapshot and release APIs. Process/syscall/selector/clock
   boundaries are inert; the one real FIFO control uses only owned pipes.
   They launch no child, sudo, namespace, compiler, Make, helper or native
   supervisor. No packages, ROM, SDK, policy change, credentials or remote
   workflow are prerequisites. Fixtures use the existing 30-second budget;
   ordinary 35-second wait/45-second enclosing and 1 MiB future fixture
   ceilings are not increased or allocated as a new workload.

2. **O1/O2 positive and failing results:** require 7/0/−9/125 observations
   already present at selector, stream, lifetime, reap, pidfd, descendant-FD
   and handler cleanup. Inject before-release and after-release faults.
   Every failed cleanup still raises; zero never becomes successful cleanup.
   The composed control must preserve inner 7, actual L main 125, and the C
   cleanup exception, with no returned `CompletedProcess` and no qualification.

3. **O3 precedence:** inject earlier setup/read/capture/wait/lifetime errors,
   then later cleanup faults/reaps. Require the same original exception,
   unavailable normal outer status, and no promotion of cleanup's 7/125.
   Pair pending-byte admission and Popen failures with a raising signal-mask
   restoration. The original setup fact must already be retained at restoration;
   the interruption is bounded secondary evidence, not the API primary or
   clean cleanup. Also exercise failed mask setup and successful acquisition
   followed by failed restoration. Preserve the caller's preblocked mask and
   the child's original pre-exec mask; all remaining owned cleanup still runs.
   A deadline exhausted before publication may deliver **no** before record;
   require failure/unavailability, not a promise that the cause arrived.

4. **O4 closed records:** require interleaved valid roles to parse, but reject
   partial/oversized/duplicate/unknown/misbound fields, nonfinite/type errors,
   false owned-child identities, and worker attempts at R/L framing. N0 is
   setup data, not a successful worker mode. Exercise EPIPE, short writes,
   deadline/pipe failures, capture charging/conversion and missing after/EOF.
   Seven R/W mode representations here are benign **data**, not seven real
   helper outcomes. Stderr resembling a receipt supplies no record.

5. **O5 admission/lifetime:** inspect exact B/F/E, 52-entry and cache/pending
   charges, max-size frames/streams/private W record and one-byte overshoots.
   Unsupported heap representations and insufficient reservations must fail
   before construction/launch. Saturate the same role report across cleanup
   groups with huge, unprintable exceptions; require bounded tags and explicit
   overflow rather than strings or histories. Require no repeated uncertain
   FD close, no recycled-leader signal, unreaped ownership retained, stable
   snapshot identity, release invalidation, and separately released caller
   aliases/raw traceback/context references before a retained testcase failure.
   The allocation control measures the actual CPython peak with maximum
   captures, four records, three-role accounting and transient representations.

6. **Independent restoration negatives:** preserve the immediate pre-edit
   `budget.py` and `lifecycle.py` outside tracked source before editing. Load
   one shipping function at a time in an isolated test interpreter, with only
   an adapter removing its unknown `outcome`/`outcome_token` keyword; do not
   replace its control flow. Run respectively
   `OutcomeCustodyTests.test_o1_coordinator_custody_oracle` and
   `OutcomeCustodyTests.test_o1_watchdog_custody_oracle`. Old C must lose the
   pre-cleanup status/bytes; old L must emit no owned WNOWAIT observation.
   Each oracle must fail as an assertion, not from a fixture/type/setup error.
   Remove that one in-memory substitution and require both corrected oracles
   to pass. Do not overwrite an accepted tree or commit source snapshots.
   Git supplies history/review and the restoration preimage, not behavioral
   evidence by raw source text.

7. **O6/default:** None must retain old argv, actual output/status/exception/
   notes, negative-signal and 125 behavior, with no new reports or frames.
   If checking ordinary-child compatibility separately, run only the existing
   focused regressions:

   ```sh
   python3 -B -m unittest \
     scripts.validation_ownership.tests.test_foundation.FoundationTests.test_cleanup_finishes_all_actions_and_replays_pending_signals_afterward \
     scripts.validation_ownership.tests.test_foundation.FoundationTests.test_budget_cleanup_defers_signal_until_children_and_pipes_are_closed \
     scripts.validation_ownership.tests.test_foundation.FoundationTests.test_privileged_budget_uses_real_watchdog_without_running_sudo \
     scripts.validation_ownership.tests.test_foundation.FoundationTests.test_watchdog_child_readiness_uses_the_absolute_deadline -v
   ```

   Those separately recorded existing regressions use ordinary owned children
   and unchanged bounds, replacing the privileged prefix rather than running
   sudo/unshare. The new O controls must not be repurposed to launch a witness.

**Cleanup and limits:** release the custody owner and all external CP/snapshot/
exception references; close only owned pipes and retain uncertain child
ownership as failure. No fixture path, namespace or foreign object is removed
by this API. No new case IDs/owners or existing automation are replaced.
The API is default-off by an explicit per-call object, not a gameplay/build
flag. It depends only on the existing budget/lifecycle/pipes, with no
ROM/RAM/save/locale/generated-format or modern/archival profile interaction.
Restricted-host setup and the four actual null-mount methods/seven modes
remain separate unqualified requirements. An intact token/frame is not
authentication, and a snapshot never grants a successful mode.

Run `python3 -m unittest scripts.validation_ownership.tests.test_graph_report.GraphReportTests.test_current_expiry_repair_can_compare_expired_historical_base -v`
for the bounded public comparison route. Keep an immutable, originally valid
BASE deadline before the fixed validation instant, then renew CURRENT or remove
its expiry. Require complete ownership/oracle results and review invalidation,
with lifecycle bindings issued only for CURRENT. Expired CURRENT still rejects
even when that revision is also supplied as BASE. Historical metadata still
rejects expiry already invalid at its final recorded disposition, malformed
proof semantics and nonchronological history; no candidate clock is accepted.

### Actions

1. Run
   `/usr/bin/python3 -I -S -B scripts/validation_ownership/isolated_launcher.py tests`.
2. Run
   `/usr/bin/python3 -I -S -B scripts/validation_ownership/isolated_launcher.py check --repository-root .`.
   The Make alias is a convenience for a trusted invocation, not the boundary
   against GNU Make's preloaded `MAKEFILES` or `--eval`.
3. Use the isolated reporter's `resolve` mode with `--changed` for
   `src/bm.c`, `include/global.h`, `scripts/check_docs.py`,
   `src/data/items.json`, `texts/expansion/catalog.en.json`, `config.mk`,
   `graphics/titlescreen/title_main_background_1.png`,
   `assets/banim/lorm_sp1/script.txt`,
   `graphics/banim/banim_lorm_sp1_sheet_0.png`,
   `assets/portraits/eirika/metadata.json`,
   `graphics/portrait/portrait_Eirika_chibi.png`,
   `banim/banim_lorm_sp1_motion.s`,
   `graphics/titlescreen/title_demon_king.png`,
   `sound/direct_sound_data.s`, `assets/tmx/Ch2Map.tmx`,
   `preview/tsa/MANIFEST.tsv`, `.github/manual-testing-handoff.json`,
   `.github/workflows/build.yml`, `.github/validation-ownership-graph.json`,
   `.github/CODEOWNERS`, `.github/ISSUE_TEMPLATE/bug_report.yml`,
   `scripts/modernize/package_ci_patch.sh`, and
   `tests/workflows/test_patch_release_workflow.py`, plus
   `scripts/validation_ownership/tests/test_content_publication.py`.
4. Confirm each result identifies one surface, every applicable typed edge,
   its existing authority, and a plain-language reason. Confirm the graph
   remains `report-only` with `narrowing_authorized` false.
   The content-publication test module has the native producer module's exact
   host ownership. Removing its exact admission or adding an unclassified
   neighboring test must fail; a prefix does not grant admission.
5. Inspect the suite's non-destructive fixtures against the complete canonical
   [typed contract](../validation-ownership.md#typed-contract),
   [path-coverage contract](../validation-ownership.md#maintainable-path-coverage),
   and [artifact lifecycle, measurement, and seal contract](../validation-ownership.md#artifact-lifecycle-measurements-and-seals).
   Confirm every edge, graph/schema/oracle, exclusion, artifact, lifecycle,
   authority, stale-selector, unknown/overlap, symlink/gitlink, and external
   scratch-boundary positive and negative control described there. Equivalent
   parsed ordering must remain stable; semantic changes must invalidate the
   documented scope, and outside sentinels must survive.
   Confirm actual GNU Make comparison covers the complete parsed 112-domain
   model, including environment/fallback origins and adversarial Make syntax.
   Confirm the real native scanner matches ordinary scaninc dependency bytes
   and source closure, then rejects every listed compilation-input content,
   absence, mode, and symlink mutation before `ProbeSession`. Independently
   selected reviewed scanner evolution must pass, while unlisted error-bearing
   `.cpp`/`.h` files, missing intermediate `..` components, repository escapes,
   and nonregular inputs remain outside or rejected as specified.
   Confirm one shared session and BASE view reproduce the asset producer's
   CURRENT omission and genuine deleted-source `LORM_SP1_PROOF` restoration,
   with one restart per Make query. Confirm generated-registry declarations use
   their selected view and BASE ownership for deleted sources; wrong loaders,
   stale declarations, invalid schemas, and filename-only classification fail.
   Count-support headers must appear alongside their actually consumed primary
   JSON inputs. Changing the support declaration and header in CURRENT must
   not replace BASE ownership, include unrelated headers, or survive restoration
   of CURRENT as stale cached membership. Run
   `python3 -m unittest scripts.validation_ownership.tests.test_report_views.ReportViewTests.test_registry_count_support_ownership_follows_the_selected_view -v`
   for this focused control; it does not replace the complete report above.
   Finally, confirm explicit import/root listings see the complete selected
   view without granting undeclared reads, and that the dependency adapter
   matches the real host C preprocessor's bytes, closure, included `.d`,
   restart, and provenance while rejecting wrong drivers, output directories,
   and unsupported flags.
6. Exercise the documented standalone-launcher and trusted-Make boundaries:
   controlled `MAKEFILES`, ambient/dry-run flags, and reporter `--eval` must
   prove that only the direct Python entry precedes Make startup evaluation.
   Reporter top-level imports and their subprocesses must receive cleared
   Git/Make/shell execution controls, while benign environment values and
   caller-relative repository-root arguments keep their intended meaning.
   The convenience target must reject every execution-control override before
   configurable includes or dependency suppression.
   Invoke the actual launcher/reporter from the repository's parent using
   both absolute and relative repository-root arguments. Require the same
   complete report coverage; a symlink root must still reject before payload.
   Prepend an inert copied verifier step while disabling the real ownership step,
   then duplicate the verifier within `ownership-tests` or move it back to
   `host-tests`. The structural staging
   guard must reject both, while a nonsemantic YAML comment remains stable.
   Complete the canonical
   [coordinator-owned review and capture procedure](../validation-ownership.md#coordinator-owned-review-and-capture)
   with one actual independent `ReviewSession`. Require the exact immutable
   checker and complete explicit path/edge/consumer arrays, plus their three
   domain-separated digests, to fit both unchanged 40-subject schemas.
   Register and capture the real verifier, then carry the same live
   qualification through `assess_observed`, local readiness, review-first
   dispatch, and final admission. A changed identity or scope, missing native
   context, omitted consumer, stale/candidate-only qualification, counterfeit
   checker, or legacy reviewed label without the live object must reject.
   Repeat assessment without the live qualification and require
   `exact-local-handoff` with dispatch and admission denied. Also prove an
   otherwise valid delegated handoff remains ready without reviewed
   qualification but cannot substitute for the coordinator-owned capture once
   a live qualification is supplied.
7. For the focused A/V correction, run
   `python3 -m unittest scripts.validation_ownership.tests.test_reporter.AssetOwnershipTests`.
   Inspect positive title/package selections, incorrect runtime-owner and
   broad-selector mutations, retained generated/build/manual pairs, and
   unknown/mode/exclusion boundaries. The fixtures run the real selector,
   oracle and consumer checkers with Make execution/emulator capture
   substituted; they do not replace steps 1–2 or prove live ROM behavior.
8. In that focused suite, inspect the package helper/test probes: both retain
   the complete `surface.host` positive/adversarial owner pair, bound to the
   host workflow test step. Removing or redirecting either role must fail.
   The removed `scripts/workflow_pilot/publisher_shell_contract.py` must fail
   current-tree resolution; with an explicitly selected old base, its deletion
   must still explain the nonempty host owner pair. A `patch-release` job
   authority must reject as stale. Run
   `python3 -m unittest tests.workflows.test_patch_release_workflow -v`
   to exercise the parsed nine-job/full-gate contract and the actual packaging
   helper with owned synthetic inputs. Require one profile build, no build by
   packaging, authenticated master-only publication, correct source/profile,
   real BPS round trip, patch-only outputs, private cleanup and visible
   failures. This does not prove actual private-base publication or the still
   blocked full 112-domain ownership acceptance.
9. Exercise the canonical path-admission fixtures: broad prefixes must reject
   immutable new source/script/documentation/graphics members until an exact
   selector supplies complete ownership. Generated-source and verifier-runtime
   registry admissions remain valid; no duplicate inventory or commit pin is
   introduced.
10. Exercise the canonical trusted-runtime reuse and cleanup fixtures. Repeated
    captures and failure/retry must preserve identities and remove only a
    successfully terminated, unchanged owned workspace. Pre-existing,
    substituted, residual, or unconfirmed-process work must remain untouched
    and reject with primary and cleanup diagnostics preserved.
    Run `python3 -m unittest scripts.validation_ownership.tests.test_reporter.RepositoryStatusTests -v`
    to compare complete staged/unstaged/deleted/untracked status with stock Git,
    retain Git errors, and read the actual large repository within the 512 MiB
    process address-space bound. The pre-fix optional preload fails with
    threaded-lstat allocation exhaustion on the recorded host; disabling
    that optional optimization must not omit paths or alter status bytes.
11. Run `python3 -m unittest scripts.validation_ownership.tests.test_graph_regex.GraphRegexTests -v`.
    A schema request at the exact 1 MiB boundary must succeed, while a larger
    cumulative pending allowance must not admit oversized requests or pattern
    batches. Direct compressed-worker and oversized wire declarations must
    also reject. Retain stricter caller bounds, real matching/schema results,
    the original dialect, deadline, memory and cleanup controls.
    Preserve actual positive and empty-match worker responses, then add an
    undeclared field to each otherwise valid final JSON envelope. Both must
    reject before caching, mark the shared budget failed and prevent another
    launch or cached response. Exact envelopes must still return their indices.
12. For the immutable verifier Git-read correction, run the three focused
    commands listed under Automation below. Start at a clean source checkout;
    these commands create actual owned Git repositories and extracted trusted
    trees, not a full-repository report. Compare empty, binary, executable,
    Unicode and space-containing paths with individual `git cat-file` reads,
    including an explicitly captured gitlink whose checkout has advanced.
    Count real protected `Popen` launches: each selected object database
    requires one batch per verification stage, rather than one process per
    path. Repeat with the same budget and with independent captures; every
    read must spend its own bytes/runs without changing the original clock.
    Select small inputs beside an unrequested oversized blob, then select the
    oversized blob itself. Two valid files whose combined stream exceeds one
    file limit must pass, but the per-file, aggregate stream/copy, pending,
    entry and run bounds must reject at their actual boundaries.
    Inspect the permanent per-file restoration control: bytes stay identical
    but the single-batch launch property is lost. Keep before/after stage
    timings as supplementary evidence only; do not raise limits or replace
    the complete standalone drift test with a shortened or mocked verifier.
13. Exercise the dedicated ownership CI worker without launching the public
    graph or independent H1 capture:
    `python3 -m unittest tests.workflows.test_build_ci_topology tests.upstream_port.test_verify scripts.workflow_pilot.tests.test_candidate_evidence scripts.workflow_pilot.tests.test_summary_continuity_contract -v`,
    then `actionlint .github/workflows/build.yml`.
    Parse the actual nine-job YAML: all prior host steps must remain, and the
    exact-PR-base verifier, isolated suite and public graph check must each
    have exactly one scheduled owner, `ownership-tests`, after that job's own
    exact-head checkout, revision comparison, Git hydration and native/ARM
    dependency setup. Host remains 60 minutes; ownership uses its bounded
    90-minute CI envelope. Probe and native fixture limits remain unchanged.
    Replay full PR, master push and manual routes and validated exact-head
    fallbacks; metadata-only/review-first must skip the new worker, while
    classifier/base identity failures still fail summary even if workers pass.
    Mutate ownership success to failure, cancellation, timeout, skip and
    missing; remove its summary dependency, disable or duplicate a step,
    substitute checkout/root/base/environment, and move ownership back to a
    different job. Every changed full run must reject, including after an
    older green run and a later metadata edit. A harmless YAML comment must
    remain valid. Confirm the native probe stays in `extended-host-tests`,
    all 34 mirrored commands remain, and the build-once master publisher and
    required `host-tests`/`build`/`summary` names are unchanged.
14. From the same clean checkout, run
    `python3 -m unittest scripts.validation_ownership.tests.test_reporter.AssetOwnershipTests.test_ownership_sources_and_collected_regressions_reach_actual_worker scripts.validation_ownership.tests.test_reporter.AssetOwnershipTests.test_ownership_execution_edge_removal_redirect_and_order_controls scripts.validation_ownership.tests.test_literal_bindings.LiteralBindingModuleTests.test_module_discovery_collects_only_owned_cases -v`.
    Collect the actual ownership package suite without running its bodies.
    Resolve its collected modules and the ownership implementation/support
    namespace through the real parsed graph model. Each must reach both
    `owner.validation-suite` and `owner.validation-check` in ownership-tests,
    replacing the outdated generic host pair with one owner per family. Compare the selected
    workflow definitions with the existing dependency-free workflow parser's
    normalized commands and real isolated launcher modes,
    not merely a step label. General host tools and the five separately
    collected native modules must keep their old owners without acquiring
    ownership-suite evidence. Restore host-only mapping, remove either new
    execution edge, and redirect it to a live host-only owner; each must fail
    required-owner or independently authored oracle checks. Combining old and
    new owners in the same family must still reject as ambiguous. Reorder rules/nodes/edges without
    changing their meaning and require zero oracle errors. Retain exact-path
    removal and unregistered-neighbor failures. No native test bodies, complete
    graph, H1 capture or remote workflow run are performed by these controls.
15. Run the focused complete-transition qualification command under Automation.
    In the owned Git fixture, create added, modified, deleted and mode-only
    paths and complete their actual required-side reads. The full declared
    scope must qualify. Then omit each path before dispatch, with both a
    coherently partial read set and a complete read set paired with the
    incomplete declaration. Each case must reject before qualification returns,
    despite matching reviewer/context/root/head metadata and sufficient file
    counts. The pre-fix qualifier accepts all eight omissions; its downstream
    verifier has a separate complete-diff rejection, so this is not proof of a
    successful H1 bypass. Preserve wrong-root, missing-read, stale-context,
    two-sided mode/deletion and foundation-introduction controls. These use
    the existing in-memory Runtime and real Git-backed read APIs, not an
    external review provider or current-candidate qualification. Remove only
    owned fixture files at cleanup; no CI, full graph or new allocation runs.
16. Run
    `python3 -m unittest tests.workflows.test_ownership_probe.ProbeExecutionOwnershipTests.test_graph_discovery_partitions_all_cases_without_repeating_native_owner -v`.
    This launches the real `isolated_launcher.py tests` entry under
    `/usr/bin/python3 -I -S -B` with a collection-only test runner. No test
    body executes. Require no loader/import errors and compare every selected
    ID with the ordinary complete module set minus the separately owned
    native suite, preserving uniqueness. The original module-level PyYAML
    dependency must fail this child even when ordinary Python can import it.
    The corrected mapping tests use the approved dependency-free parser and
    also run under no-site startup. Do not add site paths, install another
    dependency, drop `-S` or silently discard failed imports.
17. For the [required scoped-source capability](https://github.com/laqieer/fireemblem8-expansion/issues/180#issuecomment-5723100448),
    use the existing phase-module automation and its unchanged native helper.
    Retain the original LZ pattern/exact/unmatched declarations and consumers.
    Seed the three selected `.4bpp`/`.8bpp` operands as small, pre-existing
    regular fixture inputs before ordinary/native Make; record their identities
    and do not describe them as generated assets. Compare the actual
    `-mindist 2`, `-mindist 3` and empty-flag consumer expansions, while retaining
    the native conversion projections as projections only.

    Retain the original legacy exact-target compiler/flag assignments and the
    modern simple flag constructor plus raw target appends. Compare actual
    per-file origin/flavor/raw values and expanded native job target/ordinal/
    arguments, not just global definitions. Keep the ordinary `-rR` versus
    native built-in CPP difference visible. Require the legacy O1, recursive
    append, old compiler and global controls, and all three modern option
    overrides plus the global control. No compiler execution is inferred.

    Change an immediate RHS source before and after the scoped assignment:
    the earlier snapshot must remain. Change a deferred append RHS before use:
    the later value must be read. With `FLAGS := early; unit.o: FLAGS +=;
    FLAGS := late`, require global raw `late`, target raw empty/file/recursive
    and the exact native argument `late ` including its final space. Keep
    metadata-only dollar data lazy. Use fail-fast local native-bearing
    selections (`python3 -B -m unittest -f ...`); stop an unexpected setup or
    ownership error without changing guards, profiles or limits.
18. For [namespace refusal attribution](https://github.com/laqieer/fireemblem8-expansion/issues/180#issuecomment-5726588532),
    keep the actual original default/findstring/manual append, simple
    C-object/all-object/header constructors and words/printf consumer in the
    bounded semantic fixture. Preserve initial C absence and genuine typed
    include-remake publication. The candidate already accepts both passes;
    do not invent a temporal-snapshot failure. In the disclosed negative,
    change only the words operand to the recursive C-source carrier and
    require the existing namespace refusal, original pass/site/condition,
    carrier path, recursive-versus-simple facts and clean teardown.

    Inspect `MakeProbeError.source_attribution` only as diagnostic data.
    Compare the raw reader and already computed read form, known visit/rule/
    ordinal, unsafe/unknown causes and snapshot exclusions. Distinguish
    source obligations from actual issued job associations; unavailable
    associations must stay unavailable. These two bounded observations do
    not identify ROOT18's historical reader or grant another root allocation.
19. For the [diagnostic retention correction](https://github.com/laqieer/fireemblem8-expansion/issues/180#issuecomment-5727758683),
    use the pure phase-module controls with 201/801 original bindings and
    200/800 dependency edges. Keep default limits and precharge cache to
    leave 4096 bytes. Hold the actual source exception and its traceback
    while measuring newly retained allocations and container state; exclude
    the caller's existing input graph. Both cases must retain the same
    primary namespace refusal, without an unadmitted fan-out graph or
    partial attribution hidden in a raw secondary traceback.

    With sufficient unchanged cache, inspect the real selected carrier path,
    one predecessor per queued name and the admission before projection
    construction. Include duplicate/cyclic edges and a simple-snapshot stop.
    Check count/file/cache/deadline failures and an unprintable chained
    diagnostic exception. An unrelated successful reader must still succeed
    when optional diagnostic bookkeeping cannot fit. These are pure
    allocation/lifetime controls, not additional native witnesses.
20. For [ordinary recipe binding context](https://github.com/laqieer/fireemblem8-expansion/issues/180#issuecomment-5730756125),
    retain the original five-line generated-header filter as a later active
    source obligation after the default/findstring/manual append and simple
    C-object/all-object constructors and words reader. First exercise the
    actual pure helper and both source consumers. The ordinary recipe's
    `$$` end anchor must not invent an unknown local binder; the original
    exact version0 facts remain subject to the existing snapshot checks.

    In the focused source build, start without generated C or public build
    parents and use the existing bounded typed writer for a real C/header
    include-remake publication. Keep the filter recipe active on an
    unselected target, with its ordinary prerequisite supplied as an input.
    Require both original passes, retained defaults, actual words/printf
    arguments and clean teardown. No filter/compiler execution, profile
    expansion or root/report claim follows. This is a separately authorized
    focused positive, not a replay of the closed initial witnesses.

### Expected result

Every Git-tracked path resolves to exactly one complete owner set. The sole
gitlink is a named fail-closed exclusion whose selection cannot silently
succeed. Representative runtime/ABI, host-only, generated, localization,
configuration, manual A/V, workflow, governance, repository configuration,
issue-template, and pull-request-template paths resolve with no missing or
unexpected edge/evidence owners. `.github/PULL_REQUEST_TEMPLATE.md` selects
documentation governance; `.github/CODEOWNERS` selects the named fail-closed
external-enforcement exclusion only. Generated paths derive from
the typed generated-data registry; gate commands derive from existing Make
targets, workflow jobs/steps, and tester cases rather than a duplicate command
list. Both ownership commands are required, scrubbed `ownership-tests` gates and
members of the complete upstream gate inventory.

Ownership framework and collected regression paths reach their actual
relocated execution owners through `surface.ownership`, not host-only evidence.
The one-owner-per-family invariant and all host CI commands remain. The nonrelocated native test modules
and unrelated host tools keep their separate mappings. Independent source/test/
native-control probes, real package collection and parsed workflow commands
prove this distinction without claiming a successful full workload.
The actual no-site launcher discovers that complete partition without
site-installed test dependencies. A collection-only result is not evidence
that the discovered bodies or the full graph executed.

Reviewed-evolution qualification derives the complete immutable transition
independently of the caller's path filter, requires exact declared-set equality,
and checks actual read coverage over that same complete set. Coherent initial
omissions and complete reads with partial declarations reject. The later
standalone-verifier complete-diff guard and foundation-introduction behavior
remain unchanged.

The scoped-source positives preserve distinct global and target bindings,
raw recursive append tails and their inherited bases. Original source-point
facts govern immediate reads; native metadata and job arguments corroborate,
but never create, those facts. Each required native use is accounted for, and
source-only obligations before a remake are not mislabeled as native jobs.
Unselected active declarations and first-pass defaults survive the union.

Namespace observability preserves the old refusal/type/status. Its attached
bounded data explains the current original-state decision without re-entering
evaluation or supplying a value/proof. The existing successful candidate
remains successful, and recursive-late use remains a refusal. A diagnostic
failure cannot replace the primary source error.
Shared-prefix fan-out has linear diagnostic workspace. Rejected retention
releases temporary collections and diagnostic traceback references, including
partial projection owners. The primary source frames remain available.
Secondary stage/type evidence is explicit and bounded; no raw diagnostic
exception is retained merely to preserve its traceback.

The recipe context is established by original source syntax, not a job
association or terminal value. The shared classifier consumes literal `$$`
pairs only in proved ordinary recipe expansion. Whole-source constant and
per-pass snapshot eligibility recover without bypassing unknown writers or
creating a new temporal proof. Later unselected source obligations still count.

Immutable verifier batches return exactly the individually read bytes, preserve
path/mode/gitlink/source authority and reject every trusted-tree or loaded-module
substitution. Framing and retained payload copies are charged, and every child
is reaped with its streams closed even on a rejected Git response. The full
standalone drift regression still exercises all source content, missing-file,
mode and symlink mutations before scanner-session entry. The representative
document-serialization report remains semantically unchanged.

The ownership workload no longer consumes the near-limit host job's serial
budget. Its initial 60-minute envelope timed out on the unchanged test order:
371 matching methods had completed before cancellation, with a distributed
220-second increase over the same earlier prefix. The next incomplete case
passed independently in 128.539 seconds. The 90-minute ownership envelope is
bounded operational headroom, not a complete-workload sizing result or an
increase to probe limits. Require exact parsed timeout/negative controls:
restoring 60 for ownership or changing any other worker's budget must reject.
The full combined workload still needs complete measurement before closure.
Moving candidate YAML does not satisfy
the separate independent coordinator-owned verifier capture (H1), nor relax
any source/base/result binding, public graph or full-domain acceptance.

With the ARM compiler installed for ownership metadata queries, the explicit
host-only suite must still skip the concurrent custom-spell full-modern object
build before artifact cleanup or process launch. Configuration and host
checks in that module must still run. The host-only regression supplies an
available compiler and intercepts the first mutating operation: host-only
mode never reaches it, while normal mode still enters the full-build
runner. This is not execution evidence for the actual concurrent compile test.
Its required normal-mode owner is the existing `build` job, not the host job.
Follow the source-build procedure in
[TC-CUSTOM-SPELL-061-002](../custom_spell_effects.md#concurrent-full-modern-object-profile-isolation):
prepare the existing build tools and run
`python3 tools/gba-playtest/tests/test_custom_spell_effect.py --require-profile-isolation`
once with the installed ARM/native prerequisites. Require the two real,
concurrent complete `expansion-modern-all` object cohorts, enabled/reference
versus disabled/default manifest, isolated generated namespaces, custom-data
presence/absence, both object assertions per profile and owned-root cleanup.
This target does not link a ROM or final ELF.

Run the focused `test_custom_spell_profile_*` controls in
`tests.workflows.test_build_ci_topology` and the required-entry/host-guard
controls in `tools/gba-playtest/tests/test_host_only_mode.py`. Missing,
duplicate, disabled, wrong-job or host-only invocation, unavailable compiler,
empty selection and absent prerequisites must reject; equivalent quoting and
layout must pass. Check the exact new compile-owner pairs for the profile test
and host-mode guard against the independent oracle, without assigning this
owner to all host paths. The old host-only-only route is the negative control:
it skips the full compile test and is insufficient coverage.

This correction preserves nine jobs and the complete 34-gate inventory, the
three protected contexts, master-only build-once publication, and existing
timeouts. #196 must retain that complete topology and gate set. It does not
authorize H1/provider/native-AI/diagnostic work or a new measurement allocation.

For the [5692844782 normal master integration](https://github.com/laqieer/fireemblem8-expansion/issues/180#issuecomment-5692844782),
retain the independent #268 producer, portable reader controls and tracked
direct-writer negative, together with every prior #180 case and automation
entry. Run
`python3 -m unittest tests.workflows.test_build_ci_topology.ConsolidatedBuildTopologyTests.test_text_publication_graph_admission_collection_and_oracle_are_exact scripts.texttools.tests.test_textprocess_publication -v`.
Collect the actual module from its exact extended-host command and resolve
only its path and the producer to `surface.text-publication`. Require the
extended-host positive owner and workflow adversarial owner once each;
remove or redirect each edge to a generic host owner and require rejection.
Remove exact new-test/document admission and require rejection despite
matching prefixes. Preserve all prior oracle probes, add the two independent
text-path expectations and reseal their combined payload. Compare both parent
command inventories and case/feature/automation unions, not only the total.
Then execute the strict concurrent profile command above once: both real
object-build commands must succeed with the complete shared text outputs
and original asset assertions/cleanup. Do not serialize, retry or pre-generate
text to conceal the original partial-header failure. This remains object,
not ROM/H1/full-graph or measurement-baseline, evidence.

Run
`python3 -m unittest scripts.validation_ownership.tests.test_producer.ProducerTests.test_textprocess_imports_and_staging_consume_only_captured_python_sources scripts.validation_ownership.tests.test_producer.ProducerTests.test_textprocess_atomic_publication_retains_native_relocation_guard -v`
for the separate native source-loader boundary. The actual captured module
and sibling must import and stage a complete header under the unchanged
budget, record both code consumptions and remove the transient stage.
Missing sibling authority rejects with no live fallback. Full atomic
publication in this synthetic capsule still rejects at the pre-existing
rename guard and cleans the owned session; that negative is not evidence
that full text publication ran there. The existing host regression and
normal object-profile command supply full-publication evidence instead.
No native namespace algorithm or authority is changed to admit the rename.

The [5683697584 follow-through](https://github.com/laqieer/fireemblem8-expansion/issues/180#issuecomment-5683697584)
also requires the bounded `ProfileProcessLifecycleTests` procedure in
`test_host_only_mode.py`. Output beyond measured pipe capacity must not delay
the second child's post-output progress until the first exits; a first-child
dependency on that signal must complete. Second-launch failure, shared-deadline
exhaustion, interruption and timeout must settle complete owned sessions and
capture handles before artifact deletion. Unavailable cleanup/identity proof
or a replaced root must fail and retain the root, preserving the primary
failure. Separate regular capture files avoid blocked pipe readers and retain
per-command attribution without the unrelated Git output cap. Linux pidfd
identities and waitable leaders remain retained through session teardown.
Run the real full-object profile test once after the focused controls; earlier
successful Make runs do not establish these failure paths. The compile entry
is ordinal 21 within the 34-gate inventory; neither jobs, budgets, publication,
nor any H1/provider/native-AI/diagnostic or measurement allocation changes.

For the profile-helper integration, run
`python3 -m scripts.generated_data.consumer_census check` and
`python3 -m unittest scripts.generated_data.tests.test_consumer_census.ClassificationCoverageTests -v`.
The real OS grandchild PID is not an extensible game character ID, but the
unchanged token-level census must still report it and require its exact
reviewed-exclusion row with a functional reason. Removing that row restores
the classification failure; do not rename the variable or loosen scan rules.
After editing the classification source, regenerate its committed ID-space
outputs with `python3 -m scripts.generated_data.idspace generate`, then run
`python3 -m scripts.generated_data.idspace check` and
`python3 -m unittest scripts.generated_data.tests.test_idspace.OutputDriftTests -v`.
Preserve the stale-output preimage and inspect the generated delta: this
non-game PID changes census/audit metadata, not caps, table counts or C layout.
The consumer coverage check alone is not the separate generated-output gate.
Run `python3 tools/gba-playtest/tests/test_host_only_mode.py HostOnlyStagedWorktreeSubprocessTests ProfileProcessLifecycleTests -v`.
The hermetic stale-artifact tree must stage the actual process module/package
init. Removing the copied helper makes the subprocess fail import; restoring
it returns to the original all-class skip result without touching artifacts.
Keep the normal-mode stale-ROM negative and actual process PID/cleanup tests.
These focused checks do not execute the full generated-data or ROM suite.

The [5686491976 root-identity correction](https://github.com/laqieer/fireemblem8-expansion/issues/180#issuecomment-5686491976)
adds post-check replacement and actual root-open/final-close SIGINT controls
to the same `ProfileProcessLifecycleTests`. Cleanup must claim without
overwrite, verify against the retained root pin, and traverse only pinned
directory descriptors. A replaced public root is never freshly resolved for
recursive deletion; namespace drift preserves replacement/original data and
safe retained ownership, rather than reporting success.
Inspect actual live descriptor identities on acquisition interruption and
final close failure, including failures after the real close. Every live pin
is retained or closed, and the original exception chain survives. Root-pin
closure can fail after the empty directory is gone; its retained record must
describe that actual still-open descriptor, not pretend a root name or -1
sentinel supplies ownership.
The bounded preimage controls must expose deletion/false success and both
untracked-FD intervals, with independent safe fixture cleanup. Keep the
reviewed PID exclusion, staged real module/init pair and canonical drift
checks unchanged; regenerate changed audits only with the existing generator.
This is not a new graph/provider/H1 run or allocation: no broad compile,
ROM/archival suite or repeat full-object profile build is required, and
baseline 15 remains unallocated until independent source review closes it.

The [5687542201 initial-publication correction](https://github.com/laqieer/fireemblem8-expansion/issues/180#issuecomment-5687542201)
requires creation and pinning inside the exclusive private holder before
no-overwrite publication of the public profile root. A public `mkdir`
followed by `open` can adopt a different object and is not identity evidence.
Use the same real-runner `ProfileProcessLifecycleTests` to compare actual
created/acquired inode/device identities and original/replacement markers,
publication collision/refusal and independent owned recovery, private setup
failure, publication interruption and post-publication drift. Conflicts
must not launch children or adopt/delete the intervening entry; initialized
private pins/namespaces remain closed or explicitly retained with the
original failure chain. Preserve the corrected cleanup claim, FD traversal,
prior signal/close/process tests and qualified CI/resource contracts.
This relies on trusted private/session namespaces, not arbitrary same-user
private mutation or interpreter control. No full compile/graph/provider run
or measurement allocation follows from these focused controls.

Resolve the asset implementation package separately from its `tests/` namespace.
Implementation paths must retain asset host, generation, drift, compilation
and linked-consumer owners without inventing manual judgments. `assets.mk`
must retain its existing configuration/default/profile/boot owners in addition
to asset generation/drift/consumer evidence. Remove or redirect each new
pipeline relationship to another live owner: the independent oracle must
reject it. Authored asset manual routes and unrelated host tests remain intact.

The exact main-title inputs select `expansion-modern-title-check`, whose
title-progression scenario asserts four framebuffer checkpoints. Only the
three imported LORM_SP1_PROOF inputs select
`expansion-modern-banim-package-runtime-check`: its positive/control checks
cover selection, script/palette/OAM consumption and battle lifecycle, not
framebuffer pixels or audible sound. Eirika's three package inputs and four
existing component aliases select
`expansion-modern-portrait-package-runtime-check`, which observes palette/VRAM
words and face/minimug/mouth state, not all portraits or a full framebuffer.
Both packages retain asset generation, drift and compiled-consumer owners.
Remaining manifest sources retain generated/build owners without inventing
one scenario for arbitrary manifests. Remaining ROM A/V paths retain
host/compile/link plus manual handoff; review-only `preview/` files claim no
ROM compile/link/runtime observation. Handoff JSON itself selects governance
host checks. No reliable deterministic automation is replaced by a manual
criterion, and all broader checks remain mandatory.

The canonical report and lifecycle results must satisfy the complete
[artifact lifecycle, measurement, and seal contract](../validation-ownership.md#artifact-lifecycle-measurements-and-seals):
zero oracle pair mismatches, semantic-only seal/invalidation changes, and one
real removal/restoration cycle for both verified consumer bindings at every trigger.
Either route failing independently, stale proof reuse, unrelated check
substitution, or exact-base authority-fingerprint drift must reject. Equivalent
parsed reordering remains stable, and tester-case authority changes remain
scoped to their affected edges.

### Negative control

Unknown paths or edge types, uncovered or overlapping path patterns, cycles,
duplicate or ambiguous owners, stale Make/workflow/tester/generated/manual
targets, missing profiles or negative controls, and every removed or
redirected edge family fail with the missing contract named.

For the Git-read correction, add an extra trusted package file; remove or
replace a selected file with changed bytes, a directory or symlink; and supply
missing, extra, changed, non-Python or foreign loaded-module sources. All must
reject, including aliases of one loaded source. Corrupt actual batch responses
with missing/malformed headers, wrong object IDs/types/sizes, truncated payloads,
missing terminators or trailing/duplicate records. Remove an actual fixture
object or its Git repository authority, then exceed a real stream budget:
none may produce a partial successful result or leave an owned child running.
Restoring per-file reads must preserve these identity comparisons but fail the
launch-count regression. Restoring the batch restores that regression without
refreshing an oracle or suppressing mutations.

The pre-fix graph assigned every A/V path, including title graphics, sound,
review-only previews and handoff metadata, to
`expansion-modern-banim-presentation-check`. That runner explicitly captures
HP/policy probes with framebuffer disabled and no audio assertions. Replacing
a corrected title/package runtime edge with that still-live owner, or widening
the title selector to claim sound/unobserved manifest paths, must fail the
independent owner oracle. Removing an applicable runtime requirement together
with its edge must also fail; a manual handoff is not a substitute. Untracked
files fail before prefix matching; newly tracked files in generic asset
namespaces do not inherit exact observed-consumer ownership.

Make authority is observed from confined `/usr/bin/make`, never from a
handwritten parser. A fresh exact-tree + exact-gitlink scratch root, empty
generated-output overlay, fixed locale/time/environment, and trusted absolute
GNU Make/interceptor binaries are mandatory. Every 112-domain command-line
variant and each environment-sensitive origin executes the actual parser and
evaluator; exact concrete closures, pattern stems, expanded recipes, terminal
inputs, includes, dynamic outputs, errors, argv, environment, and tool
identities participate in authority. Comments and unrelated targets stay
stable only when their observed target semantics stay stable.
The closed external-selector syntax boundary enumerates static `?=` names,
direct/secondary references, definition dependencies, and graph-versus-recipe
positions from GNU Make-loaded inputs, while GNU Make undefined-variable
warnings cover evaluated undefined and computed references. Undeclared
`MODE`, dynamic default names, a symbolic `DEP` used as a prerequisite, and
references reached through `define`/`eval`/`call`/`foreach`/computed names
reject; a finite sealed MODE or DEP domain passes and changes concrete closure.
An unused `UNUSED` finite domain, unobserved typed variable, or stale generated
path fails registry completeness: the registry is never copied into the
observed census.
Every fallback and domain/origin variant contributes loaded sources, selector
positions, closures, and generated prerequisites to a deterministic fixed
point. A `MODE=b` branch that first loads `b.mk` must reject an undeclared
`DEP`, enumerate all sealed `DEP` values under the retained `MODE=b` context,
and continue through a `DEP`-selected include's next finite selector.
Conversely, an unselected branch's `DEP = $(SELECT)` definition cannot
backfill `SELECT` into the observed `MODE=a` census.
Branch-only recipe symbolic inputs are recorded but never enumerated, and a
finite recipe-only default such as `MESSAGE ?= fallback` stays in the observed
census without spawning closure-expansion variants. Reversed domain
declaration order produces identical authority. State/source/domain/
combination/depth/time cap fixtures reject rather than returning a partial
census, with one deadline and aggregate budget shared by the entire report.

For the original branch-loaded positive, retain `MODE ?= one`,
`ifeq ($(MODE),two)` and the actual branch.mk containing `BRANCH ?= selected`.
Supply the genuine existing empty-source witness. Original MODE/BRANCH must
be natively undefined before source evaluation. Ordinary/native MODE=one must
omit branch.mk and have no prerequisite; MODE=two must read it and select
`selected`. The complete small fixed point must use/enumerate MODE and BRANCH,
and every explicit BRANCH state must retain its MODE=two parent. The false
baseline predicate still consumes MODE and must not disappear from the census.

Run `test_branch_loaded_domains_reach_a_bounded_fixed_point` and the original
file-condition methods. Exercise direct, braced, transitive and computed
references; supported parenthesized/quoted equality and inequality; simple
snapshots versus recursive values; and rewrites after source selection.
Compare native include/prerequisite records with ordinary outputs. A later
raw MODE must not replace the original operand used for the include.
Keep literal metadata of an unused error/shell body lazy and verify no marker.
Finite disjoint alternatives may prove inequality; mixed equal/unequal
alternatives remain unknown even when one native execution selects a value.

Remove only the ordinary-operand proof and recover the witness-backed
branch-loaded rejection; the trusted MAKECMDGOALS path must still work.
Restore it and recover the exact positive, then remove the witness and require
rejection again. Unknown/effectful/opaque operands, unsealed defaults or
undefined names, symbolic graph inputs and computed universe names retain
their respective rejection boundaries. Source assignments cannot refresh
invalidated engine/history facts. Preserve native delayed POSIX folding, the
512-alternative/513th rejection, cycles and the original expired deadline.
This is bounded original predicate evidence, not whole-report acceptance;
the independent aggregate control-byte blocker is not waived or enlarged.

Run `test_ineligible_original_conditions_do_not_read_operands` with the genuine
witness. Put a130-character GNU variable name inside both a conditional nested
under a false parent and an else-if after a true branch, for equality and
inequality. Ordinary GNU and the complete small planner must succeed with
only Makefile, no added domain and unchanged prerequisites. Flip each outer
condition to make that operand active: ordinary GNU still succeeds, but the
observer must retain its existing invalid-input-name rejection. The pre-fix
observer incorrectly queried even the skipped operands. Structural conditional
matching and reads for an eligible predicate whose result is false remain;
neither the128-character active name bound nor uncertain-context guards change.

For `MODERN_SIZE`, run `test_modern_size_recipe_default_uses_sealed_contract`
with the source-faithful conditional tool declarations and actual recipe
environment consumer extracted from `modern.mk`. Keep original logical chunks
and comment-pad omitted unrelated parent context; do not analyze all of
modern.mk as a standalone program or assume a normal parsing mode. Provide
the fixture's explicit PREFIX/EXE/toolchain/CC/Python parent inputs and genuine
empty-source witness, then use the normal native source walker and census.
The actual recipe command is retained on a bounded fixture target; its
same-named Python test-module stub observes only the supplied environment,
not the real ARM review suite or the size executable.

Empty and `/selected` toolchain roots must select `arm-none-eabi-size` and
`/selected/bin/arm-none-eabi-size` in both ordinary child environments and
native recipe authority. Keep file origin, recursive flavor, conditional
default and recipe-only census evidence. An environment override must win
under the original `?=` declarations; changing only those parsed operators to
`=` must visibly lose that override. Restore the declarations. Preserve the
actual seal and reject removal of the exact ambient name, graph use of the
symbolic tool, or an unsealed neighboring default. No executable or source
admission is widened, and this slice does not prove whole-modern.mk or
repository-report acceptance.
Environment-origin graph variants are spawned only for names observed as
loaded-source defaults or actual undefined authority; explicit graph
assignments collapse that unrelated environment dimension.
Every target is a standalone native GNU Make observation; no combined-root
result is attributed to another target. Referenced recipe variables are
observed through bounded native variable pages, and inconsistent graph or
command provenance across pages rejects. A semantic recipe-variable change
must change its authority while a comment-only refactor remains stable.
Preserve references after a quoted recipe `#`, including inline recipes.
For the exact478 regression, use `FLAGS ?= first`, `NAME = FLAGS` and
`all: $($(NAME))`, with real `first`/`second` targets. Ordinary Make under
`FLAGS=first` and `FLAGS=second` selects different prerequisites. Calling FLAGS
symbolic/recipe-only must now reject; the finite two-value domain must actually
enumerate both closures. Repeat in target names, secondary prerequisites,
includes, conditional-name operands, definitions and `eval`/`call` forms.
Braced and literal prefix/suffix name templates remain supported. An internal
function-built name that the closed census cannot prove must reject unless
the selector is itself admitted through the existing native finite/fallback
domain contract.
For deferred stages, keep the same FLAGS/NAME/first/second fixture but store
`DEPS := $$($(NAME))` or `DEPS = $$($$(NAME))`, enable `.SECONDEXPANSION`,
and use `all: $(DEPS)`. Also use `DOLLAR := $$`, initially empty DEP and
`all: $(eval DEP = $(DOLLAR)($(DOLLAR)(NAME))) $(DEP)`. Ordinary first/second
must change prerequisites; symbolic FLAGS rejects and the finite domain
actually enumerates both. Inspect native raw definitions and origin/flavor:
the simple DEPS contains `$(FLAGS)`, recursively deferred DEPS retains its
escapes, and eval leaves an actual recursive DEP definition. An unconsumed
`UNUSED = $(shell touch marker)` body must remain literal and create no marker.
Raw/expanded name requests share the original 512-name bound and cannot
overlap. Opaque dollar-generated secondary expressions, generated immediate
eval or overwritten eval definitions must reject instead of reporting zero
enumerated domains. No Python implementation of arbitrary Make evaluation is
permitted.
For partial fragments, use `FLAGS ?= first`, `PREFIX := $$(F`, `END := )`,
`.SECONDEXPANSION:` and `all: $(PREFIX)LAGS$(END)`, with real first/second
targets and an `echo $(FLAGS)` recipe. Compare native prerequisites under
FLAGS=first/second, not only that recipe's output. Repeat through
`all: $(eval DEP := $(PREFIX)LAGS$(END)) $(DEP)` with initially empty DEP,
and through top-level `$(eval all: $(PREFIX)LAGS$(END))`.
The exact95/bd62 preimage accepted symbolic FLAGS and omitted its finite
domain despite different real prerequisites. These unresolved stages must
now reject explicitly.

Repeat with brace delimiters, multiple name fragments, nested incomplete
references and a literal prefix before the dollar-bearing fragment. Add an
unrelated eval alongside secondary expansion; it must not make the fragment
safe. Restore only the old four-string classifier and require the original
false acceptance to reappear. As positives, preserve both partial templates
resolved by a recursive `DEP =` eval assignment through their actual complete
native definitions and all three established complete deferred forms.
An unused partial definition and unused shell body must stay unexpanded.
Exercise exactly 512 combined raw/expanded names successfully and reject 513
before another native launch.

For transformed emitted references, use `FLAGS ?= first`, `OTHER = first`,
`TEXT := $$(OTHER)` and `PAYLOAD = $(subst OTHER,FLAGS,$(TEXT))`.
Consume PAYLOAD with `.SECONDEXPANSION:` and `all: $(PAYLOAD)`, with
`all: $(eval DEP := $(PAYLOAD)) $(DEP)` after initially empty DEP, and with
top-level `$(eval all: $(PAYLOAD))`. Ordinary and native prerequisites change
first/second with FLAGS; balanced raw syntax must not authorize symbolic FLAGS
or an empty finite census. Repeat with pattern replacement, substitution
reference syntax, a builtin call even when a same-named variable exists, and
a user macro wrapping the transformation. All unproven staged operations
reject before additional observation queries.

Keep transparent literal/reference forwarding and argument-free macros,
complete deferred forms and native recursive-eval positives. An unused opaque
body remains unused, and ordinary non-staged transformations still execute
through GNU Make. Use a stateful payload whose later expansion returns
`first` after the real prerequisite selected `second`, and a payload rewritten
after consumption: those later values are not original output evidence.
Check source expressions as well as native raw forms, never collect literal
identifier-looking words as proof, and never re-evaluate the payload to make
it appear safe. Removing only the emitted-reference contract must restore the
actual false symbolic and finite acceptances.

For complete original source, continue the `subst` call after a backslash-LF
and separately put it in a multiline `define PAYLOAD` body. After the
secondary prerequisite consumes PAYLOAD, add recipe-time
`$(eval PAYLOAD = first)`. Under `FLAGS=second`, inspect the actual native
prerequisite `second` and the later raw PAYLOAD `first`. The exact841 preimage
accepted symbolic FLAGS and omitted its finite domain; both routes now reject
the original opaque operation before further observation pages. Repeat with a
continued define body. Compare native raw values against complete collected
GNU/POSIX continuations, Make comment escaping, nested defines, tab-prefixed
body data, initial BOM, CRLF and odd/even backslashes. Recipe backslash-LF
bytes must remain identical to native Make, not shell-normalized. Unproven
dynamic define names or non-default recipe-prefix contexts must fail
explicitly. Restore the old physical census and incomplete-expression
handling in an isolated process; all three rewritten-body controls must
recover their actual false symbolic/finite acceptances.

For emitted defaults, define RULE with `MODE ?= first` and
`all: $$(MODE)`, close the define, invoke `$(eval $(RULE))`, and provide
recipe-less first/second targets. Repeat through `$(eval $(call RULE))` and
an argument-free forwarding macro. Compare real native prerequisites under
MODE=first/second. With no external/domain declaration, the exactc6 preimage
returned defaults/used/enumerated empty and only baseline; it must now reject
the unsealed default, just like the top-level declaration. Calling MODE
recipe-symbolic must also reject. With a finite first/second MODE domain,
require both command-line and environment variants and their actual
prerequisites, not only a nonempty domain list.

Compare emitted `export`, `private`, `override` and combined modifier forms
with equivalent ordinary declarations, retaining their actual native
origin/flavor and scope. A private target default keeps its distinct file and
global contexts: in GNU 4.3 the command-line value remains global/exported
while this private target lookup is undefined and its echo prints a blank
line. Do not replace that observation with an assumed inherited value.
Conditional define headers must seal their own default variable. An unused
default-producing body or deferred eval/shell body must not create its marker
or define its emitted variables; `?=` in literal values and shell arguments
must remain data. Remove only retained-assignment default sealing in an
isolated mutation: the real unsealed baselines and missing environment
dimension must reappear while native execution and ordinary-source sealing
remain intact.

For computed consumption, define RULE with `$(eval MODE ?= first)`, set
`NAME = RULE`, then use `all: $($(NAME)) $(MODE)` and an `echo $(MODE)`
recipe with real first/second targets. Native prerequisites vary with MODE,
but exactcac0 incorrectly accepted an empty default/domain baseline.
The unsealed request must now reject; a finite first/second domain must seal
MODE and include both command-line and environment variants. Repeat direct,
braced, computed-call, alias and immediately consumed assignment forms.
Chain one reached eval assigning NEXT, a NEXT-selected macro assigning LAST,
and a LAST-selected macro declaring MODE: all must participate before the
unused decision. A known selector on a line must not disappear just because
another selector on that line is not resolved yet.

An unreferenced computed macro and an `origin` lookup of its body must not
execute its eval/shell contents or create its marker. Combining a metadata
lookup with a real invocation must still seal the reached default. Cyclic or
genuinely unsupported computed selectors must reject, not become unused
bodies. Restore the old single-pass consumption decision in an isolated
mutation and require the original false baseline and missing environment
dimension to reappear with actual first/second native prerequisites.

For mode timing, put `.POSIX:` before FIRST and SECOND, each assigned
`alpha` followed by two spaces, backslash-LF, two spaces, backslash-LF,
one space and `beta`. Compare ordinary output, authenticated native raw
definitions and the collected values. FIRST must be `alpha beta`; SECOND
must have four spaces between the words. Moving activation before the first
collapse is a refuted correction, not a fix. A define header activates before
its body, so a continued FIRST define body and subsequent SECOND both have
four spaces. Include literal multi-target `.POSIX` rules and distinguish a
`.POSIX` variable assignment or target-specific assignment from a real rule.

For original invocation compatibility, copy the actual Makefile guard prefix
through its declarations before the checker target into an isolated fixture,
then supply benign measurement recipes for all, other and the checker-named
goal. Do not invoke the complete reporter. Compare ordinary GNU and native
MAKECMDGOALS value/origin/flavor and raw guard variables for each goal; the
all/other branches leave those guarded variables undefined. Their continuation
probe remains non-POSIX. The source/target census must consume the original
single-goal context and accept the source-faithful guard prefix, without
special-casing its private variable names.

The immutable baseline9 run34783136813/attempt1 failed at Makefile12-13 before
the first include: the missing original MAKECMDGOALS context made mode unknown
at the ownership-error branch, then raw comparison rejected one versus two
spaces after `:=`. Preserve the bounded result/scope/progress evidence and its
call chain; that allocation is closed. Compare the exact override assignment
under normal and established POSIX mode: its actual raw value is identically
empty, override/simple. Under an unknown mode, global/export/target assignments
with equal parsed scope, name, operator and RHS may pass, but mode must remain
unknown. A following alpha-beta continuation with genuinely different values,
an internal function argument difference, or a raw define body must still
reject. Do not globally trim whitespace or set unknown mode to false.

Overwrite or undefine the GNU goal, change it through an unproven eval or
generated assignment destination, and require the original control facts to
be invalidated. Unproven wildcard filter guards remain unproven. Preserve the
actual source/invocation origins, include/eval ambiguity, delayed activation
and original shared deadline. Restore missing invocation binding and the old
raw-fold-only comparison independently: the source-faithful goal controls and
equivalent-assignment controls must fail again while ordinary/native results
remain valid.

For the closed baseline10 regression, extend the source-faithful fixture
through the actual Tools/OS/EXE/PATH/optional-config/CPPFLAGS prefix, not only
the initial guard declarations. Use its original `assets-check` target,
unchanged registered uname producer and a benign absolute printf measurement.
Copy an actually tracked empty source witness into the isolated fixture.
Require both the native setup observation and `run_probe` through template/source
analysis to succeed under original limits. Native OS is undefined, PATH comes
from the original controlled environment, and CPPFLAGS retains one internal
space before `-DFE8_ARCHIVAL_BUILD=1`. Do not trim that value to obtain equality.
The proven non-Windows branch establishes file/simple/empty EXE; require that
native/source fact rather than a redundant original-input EXE query. Removing
the genuine empty witness must still reject original OS/mode evidence.
Baseline10 scope5657444621/run34793282205/result5657482497 remains consumed and
closed; these small fixtures are not a new complete graph allocation.

Run the seven-method proven/opaque-condition fixture command under Automation.
Keep literal `CHOICE = yes` equalities as positives: a simple append runs its
RHS immediately, VALUE/COPY are source-known, and a known overwrite can remove
an older deferred POSIX effect before use. Preserve the unsealed-MODE
negative, then supply the existing one-value MODE contract and require the
native flavor/prerequisite result and complete small-planner variants.

For genuine uncertainty use the effect-free opaque initializer
`CHOICE := $(subst X,yes,X)` and its `no` counterpart. Their original values
remain unknown without invalidating the namespace; native values differ.
Opaque append timing must reject even with MODE sealed, while native yes/no
distinguishes simple/immediate from recursive/deferred behavior. Mode-inert
alternatives retain VALUE input evidence and native one/two values. Potential
deferred effects reject in both abstract cases: native yes overwrites safely,
whereas native no executes the retained POSIX effect and changes raw spacing.

Retain known include/target selections as positives with exact original
mode/visit outcomes. Opaque include-selection alternatives must be exactly
ordinary.mk/posix.mk and reject; opaque include presence must reject whether
native execution visits ordinary.mk or only Makefile, even when printed
values agree. Opaque target alternatives ordinary/.POSIX likewise reject
while preserving native delayed-POSIX versus normal values. Keep missing-
witness negatives isolated from witnesses created by earlier positive cases.
Original reflected-value observation wrappers must return the real values
unchanged, not inject a proof decision.

For the closed baseline11 regression, retain the original
`GENERATED_DATA_OUT_DIR` and initial items-config declarations plus the
contiguous item-cap stamp/rule/continued-config slice of generated_data.mk.
Comment-pad omitted nondependent logical chunks to keep original physical and
logical positions. Include it from a small Makefile whose only selected recipe
prints raw stamp/config values; do not execute the retained generator recipes.
Use the existing native setup, original-input witness, template/source walker
and census route, not final native values injected into the source model.

Before the correction, observe the pending marker become unknown at the actual
`$(GENERATED_DATA_ITEM_CAP_STAMP): FORCE_GENERATED_DATA_ITEM_CAP` rule at277.
The next non-recipe statement at341 merely records it. The continuation at
343-346 then rejects, matching the immutable baseline11 error chain, although
ordinary/native Make resolves the stamp to
`build/generated/data/.item_id_cap.stamp` and joins the config paths with single
spaces. With the correction, the original model and native raw values must
agree without trimming meaningful internal data. Extend through the real
grouped active-output target and repeat with renamed variables/file spelling.
Use the actual modern output-directory declarations and pattern-rule slice
before parent continuation probes. These are source-faithful slices, not a
claim that the whole generated-data module or repository report ran.

Compare ordinary GNU and native FIRST/SECOND values for a generated ordinary
target, `.POSIX`, multiple targets containing `.POSIX`, empty targets, `%`
patterns, short/braced references, escaped spaces/colons, leading `./` forms,
grouped and double-colon rules. A `.POSIX` prerequisite is not a target; a
variable returning `.POSIX&` does not create the lexical grouped-rule marker.
The first continued statement after a `.POSIX` rule uses the old mode and the
next uses POSIX mode. An intervening empty expansion or include EOF records the
pending rule before later continuations, exactly as GNU does.

Snapshot a simple target variable from an input and then change that input;
contrast a recursive target variable, and rewrite the target variable after
its rule. Compare original CLI/environment/override/default precedence and
mode-inert branch alternatives. A later native value must not retroactively
rename an earlier target or select normal mode. A branch that may introduce
`.POSIX`, missing original input proof, an effectful target or an unproven
transformation must reject. Exercise a real wildcard match of a fixture
`.POSIX` filename as an unsupported proof, not a wildcard waiver. Introduced
separators, escaped group markers and archive-like dollar data remain explicit
unsupported contexts.

Use literal origin/flavor lookups on unused error/shell bodies as target names:
the bodies stay unread and no marker is created. Literal value lookup may
prove raw literal target data, not arbitrary computed introspection. Exercise
known-choice width9/10 targets as cardinality-one positives. With distinct
effect-free opaque CHOICE inputs, observe512 retained original value
alternatives at width9 and reject the next larger product at width10
through the existing context bound; keep the original deadline and native
combined-name/frame controls. These are abstract target alternatives, not512
native graph states; ordinary/native target output must still agree.

Restore only the old punctuation-based target classifier in an isolated
mutation. The same original and renamed slices must recover the continuation
rejection while ordinary/native values remain unchanged. Independently remove
the original simple-value snapshot proof and require the same failure. Restore
the correction and clean each owned fixture/process afterward. Keep the prior
Tools/POSIX/default/history/template/introspection/shell/append and capture
controls. The existing ARM template fixture uses a fixture generator and real
ARM objects; the source-prefix tests run no generator or ARM recipe.
Baseline11 scope5659259166/run34808192321/result5659303197 remains consumed and
closed, not a sizing point or permission for a retry.

For the closed baseline12 include regression, retain the original output/
depfile declarations, FORCE prerequisite and rule, the
`ifneq ($(MAKECMDGOALS),validation-ownership-check)` condition, the variable-
named optional include at476 and continued consumer at482-486. Comment-pad
nondependent chunks as above. Select a small fixture dependency writer through
the existing discovery Make variable; keep the real rule/include spelling.
Use `assets-check` as the native/source fixture goal, and repeat with renamed
variables/file spelling and the checker goal that skips this include.

Begin with no owned depfile. The ordinary and native writer must actually
produce a literal dependency file under the declared output root; the first
publication creates it, the second retains the same content/file identity,
and GNU reports one restart. Native MAKEFILE_LIST and file-open observations
must contain the produced file. Compare the raw depfile/config values through
the existing setup, template/source and census route. Exact73e rejects the
consumer after treating the original filename as unresolved; the correction
must visit the correctly captured source in its proven active context, not
credit an assumed empty file or replace the include with wildcard syntax.

Exercise include/-include/sinclude with original simple snapshots, recursive
references, earlier/later rewrites, CLI/environment/override precedence,
multiple/empty lists, canonical relative paths and literal filename punctuation.
Repeat ordered includes with changing names, and reject active cycles or a
file whose original continued source parses differently on a later read.
An included `.POSIX` under a proven active condition retains GNU's delayed and
include-EOF timing. Unknown conditions, differing filename alternatives and
effectful/unproven transformations remain unproven. Required missing includes
retain GNU's error; an unobserved optional filename also cannot become success.
The earlier empty exact-path wildcard fixture must remain a separate positive.

For generated history, accept only actual stable literal dependency sources.
Generate `.POSIX`, an assignment, another include, an emitted expression or
other special target and require rejection rather than using only the final
source as original authority. Preserve actual publication/source conflicts:
two writers attempting the same output are rejected by the existing ownership
boundary before source analysis. Against an actual successful native
observation, corrupt the joined source bytes, add a conflicting output
identity, supply a restart override or alter the claimed visit sequence; none
may receive source authority. Literal metadata and computed reads of restart
state retain their respective original rejection boundaries.

Restore only the old include-name proof in an isolated mutation. Original and
renamed non-checker fixtures must recover the exact consumer-mode rejection
while native included files, restart and dependency data remain real; the
checker goal still skips the include. Restore the correction and remove each
owned depfile/fixture/process afterward. Keep target/Tools/branch/wildcard,
source/default fixed-point, metadata-only, `!=`/`+=`, context/name/admission,
deadline and capture/import controls.

The fixture writer is not the full dependency collector. The initial
production-collector fixture exceeded the unchanged local control allowance
and supplies no prefix acceptance. Its log remains separate from the bounded
writer's native/source evidence. No package, registry, native permission/ABI,
budget, host-timeout, production Make/generated output or workflow change is
part of this correction. Baseline12 scope5660060610/run34814515499/
result5660116650 remains consumed and closed; these fixtures do not reopen it.

For independent source authority, create the exact0c8 regression with
`MODE ?= first`, `include .dep/src/input.d`, and the unchanged registered
`mkdir -p .dep/src/ && cc -E -nostdinc -undef src/input.c -MM -MG -MT src/input.o > .dep/src/input.d`
recipe. Provide real input/header sources, first/second targets and an all
target depending on MODE. After the include, assign
`MAKEFILE_LIST := .dep/src/input.d`. Native Make must open both the actual
primary and depfile, restart once and retain the real compiler/cc1 output.
Exact0c8 accepted only the depfile, empty default/domain sets and one empty
state. The correction must reject that omission; removing only the rewrite
must restore unsealed MODE rejection and then real first/second variants when
MODE is declared finite. This is an unchanged live registered producer case,
not an in-memory registry bypass claim.

Repeat source-list omission/reordering through assignment and define/eval
contexts. Select a differently named primary via the actual native `makefile`
argument and place an error in an unselected Makefile: never guess the primary
basename from source/list contents. Omission of another included file rejects
as well. An unused define containing a list rewrite stays unused. Ordinary
text/binary data opened by Make is retained as read evidence, not parsed as a
Makefile or fabricated into the include list.

For template metadata selection, use a literal `ITEMS := first second`, an
`INPUT := input` dependency, and this supported template:

```make
define RULE
$(1): $(INPUT)
endef
$(foreach item,$(ITEMS),$(eval $(call RULE,$(item))))
```

Provide the input file and an all target depending on first/second that prints
literal FIRST/SECOND probe values. First select the program as Makefile, then
as project.mk while the unselected Makefile contains
`$(error unselected Makefile executed)`. Run the mapped
`test_template_metadata_queries_keep_the_selected_primary` control to reach
the actual native RULE definition query, not only the first observation.
Both selected programs must retain the same dependency edges and probe values;
the metadata query must use the selected file. Exact cbfc, or an isolated
restoration that omits the selected-file argument, fails only the project.mk
route with the unselected-file error. Restore the correction and clean the
owned fixture/process state. The default reporter route and the intentionally
empty original-input witness remain unchanged; this is not full graph evidence.

For neutral generated syntax, use the bounded stable writer to emit each of
`include mode:`, `-include mode:` and `sinclude mode:`. The actually captured
file named `mode:` contains `.POSIX:`. Require ordinary/native Make to load it
and produce the corresponding POSIX continuation values, then reject neutral
history credit for the directive. The colon in its operand does not make it
a dependency rule. Keep ordinary rules named `include:` and `export:`, plus
the real renderer's canonical dependency path forms, as positives.

For export history, begin with a neutral stable-remake fixture exporting
neither control. Add `export MAKE_RESTARTS` and then `export MAKEFILE_LIST`.
Record both actual writer dispatch environments: the exported values change
from empty to1 and from the primary name to primary-plus-include respectively.
Neither run may receive insensitive history credit. Repeat supported computed
name lists, bare export-all and `.EXPORT_ALL_VARIABLES`; unsupported eval/name
effects retain their explicit rejection. Unrelated literal/computed benign
exports, inactive export branches and CLI/environment precedence stay valid.
An exported body that emits a default participates in consumption even when
its definition is retained by eval; the unexported body remains unused.

The indexed export-accounting control must join the actual native dispatch
and job-context frames by their sequence, retaining the observed recipe target
and command ordinal in full semantic equality. Both serialized native frames
and the complete retained semantics must spend the original observation/control
budgets. Do not drop job data or derive the expectation from the final result.

Restore each old root independently in an isolated mutation: list-derived
primary/source recovery, first-colon neutrality, and omitted native export
consumption must recover their corresponding false admissions with real
native observations intact. Restore the correction and clean all owned
sources/depfiles/processes. Keep actual registered-C evidence distinct from
the in-memory stable-writer controls. The existing targeted automation below
retains current FORCE/include, target/POSIX/input/metadata/shell/append,
name/admission/deadline and import behavior; it is not full graph/verifier/H1
acceptance or a new diagnostic allocation.

For call-form universe reads, place
`PHASE_LABEL := $(filter ASSET_BANIM_INCBIN_CONSUMERS,$(call .VARIABLES))`
and `export PHASE_LABEL` after the verified first-pass guard and final rule.
Repeat with `${call .VARIABLES,unused}`. Use the unchanged production emitter
and no default declaration: actual first producer environments have a present
empty label, while final raw metadata names the generated binding. Both
certification and the complete small probe must reject; the earlier phase
guard or an independent default gate must not supply that negative result.
Restoring only the omitted call-target handling recovers the false small-probe
admission with the same 347-byte output. Keep the same-position substitution
form and the unconsumed emitter as negative/positive controls, then restore
the guard and clean owned fixtures. This does not establish full graph fit.

For the generated literal-binding-module certificate, use a clean isolated
Makefile that does not print or inspect the generated names. Include the
initially absent metadata file and retain the actual empty-MAKE_RESTARTS
producer-rule / later recipe-less same-target structure. Observe output names
only with the trusted native `definitions=` query. With the real three-record
asset manifest, all15 input sources, unchanged registered producer and
custom0/item-cap0xCD profile, require the captured renderer/native bytes to
match, five parsed global literal assignments, original undefined input
metadata, one created source and one restart. The native graph stays the same.
Preserve the ordinary CLI's intentionally different mtime digest; do not make
those digest strings an equality oracle.

Repeat with unrelated inventory/count metadata names, comments, leading
assignment layout and independent statement order. The parsed values and
original behavior must remain equivalent without a producer/path/prefix waiver.
Keep the neutral dependency-file case distinct. Missing original input
evidence, supplied CLI/environment writes, protected engine or implicit-rule
names, duplicate/dynamic/modifier/nonliteral assignments and non-neutral
directives reject.

Use the real emitter's FIRST_PASS_ONLY control: immediately after the include,
an origin test defines/exports FIRST_PASS_ONLY only while a generated name is
undefined. First-pass native producer environments contain its value; final
Make state does not, despite identical final generated bytes. The certificate
must reject this consumer rather than seed a final-valued condition. Exercise
direct/recursive/computed aliases, metadata reads, variable-universe access,
exports and file/presence readers. Unrelated unexported deferred error/shell
bodies remain unused; unsupported active I/O or program effects remain explicit
rejections.

For the two binding-read closure regressions, keep that same production fixture
and GNU Make4.3/Python3.12 source-only profile. Start each run with the generated
file absent; do not add a default declaration or candidate `info`/`printf`
observer. Immediately after the include, use each of these consumers separately:

```make
SELECTOR = ASSET_BANIM_INCBIN_CONSUMERS
ifdef $(SELECTOR)
PHASE_LABEL := final
else
PHASE_LABEL := first
endif
export PHASE_LABEL
```

```make
PHASE_LABEL := $(filter ASSET_BANIM_INCBIN_CONSUMERS,$(.VARIABLES:%=%))
export PHASE_LABEL
```

1. Inspect trusted native producer dispatch environments and raw
   `definitions=("PHASE_LABEL",)` metadata, including the `all` file context.
   PHASE_LABEL must be present in both actual first-pass dispatch environments:
   `first` for the conditional, the empty string for the universe case. Final
   global/file metadata is file-origin, simple-flavor, with respectively `final`
   and `ASSET_BANIM_INCBIN_CONSUMERS`. Empty is not absent. Require identical
   347-byte generated output, real publication and MAKE_RESTARTS=1.
2. Run `test_consumers_aliases_metadata_exports_and_universe_cannot_be_pruned`.
   Both certificate/census and complete small-fixture `run_probe` must reject
   before graph authority. Repeat literal/computed ifdef/ifndef, transitive
   selectors, direct/braced/parenthesized universe and substitution forms,
   referenced substitution operands, and reached origin/flavor/value aliases.
   The census's resolved conditional read must not disappear at certification.
   Protect computed immediate restart-control reads too.
3. Keep non-interfering generic metadata and the actual emitter positive.
   Observe origin/flavor/value of an unused body containing a generated read,
   universe read and shell marker; export only the resulting metadata snapshots.
   Native first/final values must agree, raw body bytes remain raw, no marker
   appears, and the complete small planner still accepts. Unknown computed
   bodies before the producer guard remain earlier phase-proof failures. The
   same bodies after the guard and final literal rule reach consumer rejection;
   moving this fixture input is not permission to weaken phase proof.
4. Run `test_certificate_and_phase_obligations_have_independent_removal_controls`.
   Independently discard only the resolved certificate reads, then restore only
   whole-expression universe comparison. Each corresponding no-default fixture
   must recover its false certificate and complete small `run_probe` admission;
   the other intact consumer guard still rejects. Exact1e5 preimages used
   14runs/3states and 13runs/2states respectively. Restore each guard and require
   rejection again. Unlike FIRST_PASS_ONLY, these controls have no independent
   unsealed-default gate masking the small-graph admission.

The existing method selectors in the catalog cover these assertions; they are
not new full-repository graph/report, verifier, diagnostic or H1 allocations.
Dependencies and reset rules are the original source/input/phase/native
producer contract and automatic fixture/session cleanup. Feature/profile,
save, locale and modern/archival ROM interactions are none. Preserve all
original limits and the shared deadline; record earlier fixture failures
honestly, not as consumer-certificate evidence.

Alter the phase target, add another possible rule owner or an extra first-phase
assignment, omit the empty source witness, or override a written name. Reject
before authority. Original source/input/publication, primary/list/export,
directive, target/POSIX, `!=`/`+=`, context/name/plan/deadline and import
boundaries remain required. The phase proof does not grant general counter
interpretation or a native replay ledger.

In isolated removal controls, disable the new category and recover the original
generated-source rejection. Remove only consumer certification and show that
the real FIRST_PASS_ONLY history would be credited; its unsealed default is
also an independent later graph gate and must not be misreported as complete
graph success. Remove only phase certification and show that an extra unused
first-phase assignment is wrongly credited. Restore the guards and clean every
owned file/process. Record all fixture corrections and actual results. These
host-only controls are not a new full graph/verifier/H1 measurement; baselines
1-13 remain consumed/closed.

Load the literal-binding module through ordinary unittest module discovery.
Its eight collected cases must belong to that module and have unique IDs; reused
Make-probe fixture cases remain in their original module, not a second copy
here. Run `test_module_discovery_collects_only_owned_cases`, then exercise the
real production-emitter fixture. Restoring a globally imported fixture
TestCase must make the collection guard fail without dropping any original
Make-probe test or changing a CI budget.

Separately require semantic graph admission for that discovered module. The
same focused method parses the actual ownership graph and calls
`_path_rule_matches`, `_path_admission_sources` and `_path_admission`, without
building a full graph or launching native Make. Require the new module and
existing `test_make_probe.py` / `test_graph_commands.py` siblings to select
only `paths.ownership` / `surface.ownership` with `exact-ownership-rule` admission.
Remove only the new module's exact selector in the parsed rule: its prefix
match remains, but admission must reject. A neighboring unregistered
`test_unregistered_literal_bindings.py` must likewise reject. The original
module was unit-discovered yet failed this independent graph boundary; do not
repair it by changing the introduction cohort, broadening a prefix or adding
verifier bootstrap/runtime permissions. The separate execution-owner controls
above require the relocated ownership suite/check pair rather than outdated
generic host ownership or path admission alone. Unrelated host/native mappings
and every existing CI command remain unchanged.

Make the candidate Makefile itself raise an error, then query original inputs
through the native empty-witness route: no candidate program or recipe may run.
Retain raw environment values containing an unused error body, native undefined
records, builtin origin/flavor and exact PATH. Reject invocation-control queries
and oversized name requests before launch. Witness selection must use actual
captured zero-byte content, not a trusted filename spelling or a source ledger.

For original computed includes, supply that genuine witness before the
fixture's `FLAGS ?= first`, retain `NAME = FLAGS`, and include
`$($(NAME)).mk`. Keep real first.mk/second.mk sources and ordinary/native
first/second outputs. Missing witness evidence leaves FLAGS unknown even for
a direct include; do not replace `?=` or assume undefinedness to fix the test.
With the witness, require the complete small finite planner to retain both
included files and prerequisites and to classify symbolic FLAGS as a graph
input. Repeat braced and transitive aliases, prefix-built names, simple
snapshots followed by input rewrites, and NAME rewritten after its include.
Assert the original source units retain the actual name/selected-binding reads
even when later raw NAME differs from the prerequisite already selected.

Run `test_computed_include_and_unresolved_name_contracts_are_native` and the
separate `test_opaque_computed_selector_requires_its_declared_native_fallback`.
The latter keeps the original opaque subst expression: without a NAME domain
it rejects, while declared tracked-fallback enumerates both FLAGS and NAME
with real first/second prerequisites. An earlier failure cannot hide this
second case. Keep the existing original-rewrite, stateful/staged-payload and
command-contract negatives; final native values are not original source proof.

Independently restore the old computed-effect rejection and old literal-name
lookup limitation. Each must recover the computed-include rejection while
the other proof stays enabled; restore both and require acceptance again.
Remove the witness separately and require the default-dependent include to
reject. Unknown names, cycles, opaque/effectful transformations and computed
universe names remain rejected. Literal metadata reached through a computed
ordinary alias must keep its unused error/shell body raw and create no marker.
Exercise 512 original name alternatives resolving to one filename, reject the
513th, preserve cyclic/deep-name rejection and the shared expired deadline.

An originally transparent computed alias may now reach generated-binding
consumer certification rather than an earlier conservative phase rejection.
Keep its actual first/final native difference and consumer rejection. A
genuinely opaque name transform before that same producer guard must still
fail phase proof. This is additional original proof, not a weakened phase
guard, final-value substitution or blanket error-regex relaxation. The
literal-binding module still collects only its eight owned cases. These are
source-only focused fixtures, not a full graph/report/verifier/H1 allocation;
all source, admission, native, cleanup and resource contracts remain in force.

Exercise an undefined OS condition, original PATH reference, two mode-inert
simple branch alternatives and a provably empty exact-path wildcard include.
All must preserve normal continuation behavior. Recursive OS eval, unknown or
effectful branch alternatives and unresolved/generated include context still
reject. Source changes cannot be repaired by arbitrary end-of-run values or
fresh original inputs after an unproven namespace mutation. Remove the genuine
original-input witness to recover the Tools-prefix rejection while native
CPPFLAGS remains valid. The separate opaque branch and include controls must
retain their own real alternative/visit evidence; do not require every such
mutation to break a Tools branch whose original OS equality is already known.

For diagnostics, introduce an unproven input before a mode-sensitive
continuation in a small named Makefile. Check the actual path, logical ordinal,
physical span and first uncertainty input/site, with the original MakeProbeError
preserved in the exception chain. Source values and full statements must not
appear in the message. Continue to require the genuine normal/POSIX
alpha-beta difference and all prior unknown-mode, history, lazy-read, origin,
visit, native-frame and shared-deadline controls.

Repeat with `.POSIX` under literal false/true, nested, quoted and
`else`/`else ifeq` conditions. The false branch must leave both values
non-POSIX, including an ignored nested condition whose shell operand would
otherwise create a marker. Put `.POSIX`, an intervening assignment, and
`include values.mk` in the root: both included continued values must use
POSIX folding. Repeat with a continued include directive, nested includes,
inactive includes and a child that ends immediately after `.POSIX:`;
its EOF records the rule before the parent resumes. Use only the actual
native MAKEFILE_LIST sources for this comparison.

Reject unproven activation in a variable-dependent condition, generated eval
or fragmented dynamic target, and unknown include order when folding can
differ. Bind a command-line macro capable of emitting `.POSIX` to its real
invocation; unexport it in the fixture so it does not re-execute during recipe
environment construction. An unused mode body stays unused, and late
top-level activation may change actual shell `-ec` dispatch while earlier
values remain non-POSIX. Rule-producing eval in recipes must retain GNU's
actual error, not be treated as a positive late-mode fixture. Exercise a
large unused dependency graph through lazy metadata/conditional operations;
the native body must stay unexpanded and analysis must honor the existing
deadline without recursion growth or extra observations.

For shell-assignment flavor, admit an exact in-memory Python fixture producer
printing `chr(36)+"(eval .POSIX:)"`, and use it as the RHS of `PAYLOAD !=`.
Then assign `RESULT := $(PAYLOAD)`, test the continued
`ifeq (alpha` plus two spaces/backslash-LF/space/`beta,alpha beta)`, and put
`MODE ?= first` in the else branch. Provide `all: $(MODE)` with an echo recipe
and real first/second targets. Ordinary/native Make must report recursive
PAYLOAD containing the literal eval and select first/second under CLI MODE.
Exact471d instead treated the shell result as inert simple data and accepted
empty default/domain baseline authority. The corrected unproven expansion must
reject without discarding the real producer execution or substituting a later
value.

Repeat through define and eval forms, export/private/override modifiers and a
subsequent `+=`. Use recipe-less raw-metadata controls for exported effectful
payloads so the fixture does not re-expand them during recipe environment
construction. Native CPPFLAGS after the payload expansion contains two spaces,
not the census's old one-space value. Compare `:= $(shell ...)` and
`::= $(shell ...)` using the same actual producer: their results are simple,
do not re-expand that returned eval and retain normal continuation behavior.
An unused `!=` result remains recursive and raw through flavor/value metadata;
a safe literal shell result still supports an ordinary prerequisite. Immediate
define RHS evals must retain their default-sealing meaning, and target-scoped
shell assignments retain their native file/global distinction.

Restore only the old simple classification of `!=` in an isolated mutation.
The permanent regression must fail by recovering the actual empty
default/domain baseline, and the raw CPPFLAGS mismatch must reappear.
These are exact fixture-contract controls, not changes to the production
registry or claims of a current live-registry occurrence. All original
observations, native receipts, source/capture protections, counters and
deadlines remain; no baseline9/10 or complete-report allocation is reopened.

For original append timing, start a separate small fixture with `UNUSED :=`,
then `UNUSED += $(eval MODE ?= first)`, `all: $(MODE)`, an echo of MODE, and
real first/second targets. Run ordinary GNU Make with `MODE=first` and
`MODE=second`; observe both native prerequisite identities and the raw
simple UNUSED/file-recursive MODE metadata. Repeat with `override UNUSED +=`
and a `define UNUSED +=` body. Exact64b incorrectly accepted an empty
default/domain baseline even though all three RHSs executed. The corrected
probe must reject undeclared MODE and retain MODE's default plus actual
first/second command-line/environment variants when that finite input is
declared. Use only the documented small fixture command, not a full report.

Change the initial definition to recursive `UNUSED =` and leave its appended
eval/shell body unread. MODE must remain undefined, raw origin/flavor/value
observations must not run the body, and no shell marker may exist. Repeat the
recursive define append. Conversely, appending `$$(eval MODE ?= first)` to a
simple variable stores the literal returned spelling; one expansion must not
execute it again. These remain supported without inventing an external default.

Supply original command-line and environment UNUSED values and repeat ordinary,
override, private, export and override-define forms. Distinguish applying a
write from expanding its RHS: an ordinary append to a recursive CLI value stays
unused, but a simple `:=` RHS still executes even when CLI precedence rejects
the resulting write. An append to an override-origin simple binding also
expands before its ordinary write is rejected. An empty override append can
retain file origin, allowing a later ordinary recursive definition. Compare
actual native origins/flavors, not a presumed precedence shortcut.

Rewrite UNUSED after the append to the opposite flavor: the later metadata
must not replace the original timing. A prior target-local simple definition
makes that target's append immediate; a global simple definition alone does
not, nor does another target's local definition. Check the global and target
native records separately. An originally undefined append uses the existing
empty-source native input witness and remains recursive. A branch that leaves
the original flavor unproven must reject a possibly immediate effect.

Use a literal `$(eval UNUSED :=)` before the append and direct literal eval
append arguments with paired-dollar inner evals. The original statement's
binding must still determine the execution stage: simple executes the inner
default; recursive leaves it unused. The existing source/effect seam may prove
these literal emitted assignments, but forwarded or computed generated
contexts without equivalent original evidence must reject rather than borrow
the final native flavor. Keep the actual ordinary/native metadata and
prerequisites for both forms. Genuine conditional/include/eval mode ambiguity
and the full Tools-prefix protections remain required.

In an isolated restoration, restore only the old operator-only append
consumption. All three permanent preimage subtests must fail by recovering the
empty default/domain baseline while native MODE and first/second prerequisites
remain real. Separately restore stale bindings after an unproven forwarded
flavor-changing eval; that control must recover the same false baseline.
Restore the correction afterward and remove each owned fixture/process.
The automation below covers these host-only contracts under unchanged limits;
there are no new packages, native ABI/registry permissions, profile flags or
ROM/RAM/save/generated-data/localization interactions.

For assignment origin, provide environment MAYBE=literal while the source
assigns `MAYBE = $(eval .POSIX:)`, then evaluates `RESULT := $(MAYBE)` before
the continued FIRST. Ordinary/native FIRST has four spaces between words;
the census must reject that unproven generated mode instead of forcing the
environment value and reporting one space. The command-line MAYBE=literal
control remains non-POSIX and supported. Repeat defined empty values and
`?=`: a defined empty environment value still prevents the conditional
assignment. Ordinary file replacement of an effectful environment value
must not execute the replaced body; explicit override retains its real
precedence. Preserve original argv order and complete environment evidence.
Use a top-level `info` and recipe-less target for the exact origin fixture,
so an exported recursive macro is not accidentally executed again as a recipe
environment side effect. Restoring environment-as-forced behavior must recover
the native-versus-census mismatch while leaving the CLI control intact.

For completed includes, use `SWITCH =`, include mode.mk, assign
`SWITCH = $(eval .POSIX:)`, include mode.mk again, then define continued FIRST.
mode.mk contains `RESULT := $(SWITCH)`. Require the native MAKEFILE_LIST to
contain both visits and FIRST to be POSIX; the unchanged entry mode bit is
not permission to skip the second, changed input. Repeat through an outer
include. Literal replacement inputs remain supported, as do completed visits
with different mode bits but identical source units. If parsing the same
source differs between visits, reject rather than overwrite earlier history.
Active recursive include cycles reject without being confused with completed
revisits. Restore the old mode-bit-only memo in isolation and require the
original repeated-visit mismatch to return.

For semantic include visits, set FLAGS to first, OTHER to first,
`TEXT := $$(OTHER)` and `PAYLOAD = $(subst OTHER,FLAGS,$(TEXT))`.
Set MODE to no and include rules.mk, enable `.SECONDEXPANSION`, set MODE
to yes and include rules.mk again. Inside rules.mk put `all: $(PAYLOAD)` and
an `echo $(FLAGS)` recipe under `ifeq ($(MODE),yes)`; provide first/second
targets in the root. Ordinary/native prerequisites change with FLAGS and
MAKEFILE_LIST must record both visits. Exactc442 nevertheless accepted
recipe-only symbolic FLAGS or an empty finite enumeration. Both unproven
requests must now reject at the later staged occurrence. Repeat through an
outer include, and keep the original mode walker, semantic ordering, histories
and template consumers on the same occurrence stream.

Replace the opaque payload with the supported `PAYLOAD := $$(FLAGS)` form.
Repeated and nested visits must then retain actual first/second finite domains
while symbolic FLAGS rejects. Add repeated unrelated includes beside the
source-faithful real framework templates: valid inputs remain supported.
Make a repeated include instead rewrite a template's table-list input after
its callers; that changed original history must reject. Restore the old
first-visit semantic projection in isolation and require the false symbolic
and finite admissions to return while the mode walker still revisits correctly.

For recipe metadata, define `RULE = $(error unused body expanded)` and print
`$(origin RULE)` through a real printf recipe. Ordinary output is file/newline;
flavor prints recursive/newline and value prints the literal unexpanded body.
Repeat with an unused shell body and require no marker. The initial native
argv/environment must match the final record, and later pages must request raw
RULE definitions, never expanded RULE. Preserve full raw/origin/flavor evidence.
Repeat through aliases, a computed ordinary alias invocation and captured
exports; metadata remains metadata through those dependencies.

Mix `$(RULE)` with origin/flavor/value on a safe literal RULE. Retain both
expanded and raw records, never request the same name in both forms in one
native call, and preserve genuine execution errors when RULE is actually
expanded. Double-colon targets retain ordered duplicate per-file metadata
contexts. A 513-name metadata fixture must use bounded 512/1 pages rather than
enlarging the per-request cap. Keep the direct 513-name prelaunch rejection,
all native frame/string byte bounds, and existing control/cache accounting.
Computed introspection still rejects; escaped shell-literal introspection is
not a Make read, and admitted undefined metadata retains its actual origin.
Restore the old expanded-only recipe pager and require all three ordinary
metadata positives to recover their original unused-body error.

Restore only the old c6 conditional-blind/per-file collector and require
the native conditional/include mismatches to return. Separately move pending
activation before collapse in an isolated mutation: the real unconditional
timing controls must fail. All source, native/frame/record/plan budgets,
capture checks and cleanup remain unchanged. These source-only controls do
not establish whole-repository resource, verifier, H1 or delivery acceptance.

For selector history, use `FLAGS ?= first`, `NAME ?= FLAGS`, `OTHER = first`,
`all: $($(NAME))` and an `echo $(FLAGS)` recipe, then assign `NAME = OTHER`
after the rule. Also use secondary `$$($$(NAME))` followed by recipe-time
`$(eval NAME = OTHER)`. The native prerequisite for FLAGS=second remains
`second` while the later NAME is `OTHER`. Declare NAME tracked-fallback:
symbolic FLAGS must reject and finite FLAGS must enumerate first/second
alongside NAME. Repeat with a literal eval assignment and a source macro
forwarding that assignment. An unresolved earlier expression or unproven
generated assignment destination must reject, even if the final NAME is a
valid identifier. Restore only the old preference for final observed values
and require these controls to detect omitted FLAGS. Preserve direct,
target-local and unchanged native finite-name positives.

For the template-mode ordering regression, retain the actual recursive
`GENERATED_DATA_LINK_TABLE_RULES` definition/call followed by the actual
multiline `GENERATED_DATA_CONFIG_INPUTS_units` declaration. Use the existing
bounded alpha/beta inputs, original-input witness and logical-chunk helpers.
Blank-pad omitted post-call source rather than introducing artificial
continuations. Physical source positions help attribution, but native values,
effects and graph behavior are the oracle, not line numbers or macro spelling.

Require the normal-GNU small planner to finish with the exact ordinary/native
single-space config value and matching target, recipe and prerequisite
projection. Keep the short positive and renamed definition/call. An actual
preceding `.POSIX` declaration must retain its different double-space raw
value. The single-line layout variant is a diagnostic control, not permission
to flatten production source. Restoring only old late mode certification must
recover the meaningful-continuation rejection while native execution succeeds;
restoring the proof must reproduce the positive.

Test original initializer claims against GNU for prefix/suffix percent
substitution, literal-word substitution, nonmatches and empty matches. Claims
with altered values reject. Snapshot arguments at the assignment, then rewrite
their source and verify that a native value is still checked against the old
arguments. Do not add that relation to ordinary literal/condition evaluation.
Extra initializer/alias whitespace cannot disappear from an exact claim.
An oversized or unresolved initializer has no certificate; untouched unused
source is not rejected merely to manufacture a template fact.

Run `test_template_call_payload_padding_cannot_hide_parser_effects` and
`test_template_empty_pattern_claim_cannot_hide_mode_and_defaults` with the
existing scoped template variables and genuine witness. Keep unpadded calls
and harmless outer expression whitespace as positives. Padding around the
loop reference inside the second call argument must reject, not be trimmed:
the trailing-space/tab counterexample emits a separate `.POSIX` target,
double-spaces LATE and defines HIDDEN natively. Those real defaults must not
be pruned from an accepted small plan.

The empty-pattern initializer `$(patsubst ,POSIX,)` followed by a later
mode-sensitive clearing of TABLES has the same requirement. Native Make
emits `.POSIX`, preserves double spacing and defines HIDDEN; neither an empty
final table list nor an incorrect unchanged-input claim may certify zero
iterations. Decline empty-pattern certificates and their direct claims.
Retain the literal special-target rejection and nonempty pattern/empty-stem/
empty-replacement positives, with ordinary/native raw-value agreement.

For non-circularity, use a staged config initializer that activates `.POSIX`
and resets itself to a harmless final path. Add a later raw continuation and a
mode-sensitive condition writing that input again. Native final values may
look valid, but the original staged input must not receive a mode-neutral
certificate, and the source must still reject. A blanket foreach/call
exemption is not a positive. Keep unsafe emitted/special targets, unproved
origins, late macro/input writes, changed/repeated occurrences and genuine
effects as negatives. Preserve metadata-only error/shell bodies as lazy,
original snapshot aliases, namespace/wildcard checks and the existing
512-name/context, byte, deadline and cleanup boundaries. The complete
source/history/phase and graph certificate remains mandatory after this
per-occurrence mode proof.

Run the six template-mode, initializer and payload/empty-pattern
methods with directly coupled existing template/native/history controls.
These source-only fixtures do not rerun a repository graph or establish
production resource fit. Closed baseline14 and all earlier allocations remain
closed; these checks do not allocate another baseline, provider or H1 run.

For composed header-safety bounds, run the five
`test_composed_header_bound*` methods in the existing Make-probe module.
Keep the actual classes/items/supports/characters linked-source declarations,
their original simple initializers, the complete recursive definition/call,
all nine literal characters-config paths and its asset-script wildcard.
Retain the genuine empty original-input witness and the real later multiline
units-config declaration using logical-chunk selection and blank padding.
The fixture's explicit Python parent input, tiny four-table JSON and generator,
and namespace-only Python files are disclosed fixture inputs, not recovered
baseline15 target/state locals or the production generator implementation.

Require ordinary GNU and native observation to agree on the four table names,
the complete characters prerequisite list and the unnormalized raw late
continuation. The complete bounded small planner must retain the same native
target, recipe and prerequisite projection. Keep the short source and renamed
macro controls, and run the normal late fixture generator to inspect its
actual generated C. The invariant exact namespace path now independently
proves this initializer: removing header composition alone must retain the
positive, and removing exact wildcard authority alone must retain the older
header-bound positive. Disable both independent paths to recover the missing
fact, failed header check and real late-continuation rejection while ordinary
GNU values remain unchanged. Restore either proof and recover the identical
positive. A later safe config rewrite must still
fail the final original-assignment-history certificate even though native
final metadata contains that safe value.

Exercise adjacent fragments, literal separators, parenthesized/braced
references, bare aliases and supported wildcards. Check every component:
unsafe punctuation, dollars, newlines, effectful functions, universe reads,
unknown/cyclic references, malformed expressions and unsafe wildcard namespace
members cannot contribute a bound. Snapshot reference safety at the original
simple assignment; a later unsafe referent must not replace that snapshot or
authorize a new one. Conditional/default/append or namespace-uncertain inputs
must not gain composed facts. Check retained original reads and the shared
deadline. A bound may prove a prerequisite region safe, but must fail as an
exact loop parameter, target, comparison operand, recipe input or native-value
claim; a native-value query is not allowed to repair it.

Use a small native template with `PREFIX ?= first.h`, finite first/second
header choices, a wildcard snapshot through a bare alias and exported composed
HEADER. For every planned state compare actual ordinary output, native raw
HEADER, its process-environment membership/value and the full prerequisite
list. PREFIX must remain an enumerated graph dependency and visible default;
omitting its declaration or calling it recipe-only symbolic must reject.
Metadata-only reads of an unused error/shell body must stay lazy. Preserve the
earlier payload, empty-pattern, original-effect, phase/export/default, 512-name
and teardown controls. Keep all session children/waiters and fixture paths
cleaned on positive, negative and removal paths. These checks neither rerun a
full graph/report nor increase a budget; all baselines1-15 remain closed and
no baseline16, provider or H1 allocation follows from this case.

For original exact derived targets, run the ten `test_original_exact_*`
methods. Retain the actual four-table declarations, characters header bound,
`GENERATED_DATA_LINKED_C` addprefix/notdir initializer, object suffix
initializer, static header/legacy recipe/UNAME conditional, following
statement and late units continuation. Use the existing tiny generator,
genuine witness and explicitly declared fixture parent tool values; do not
run the archival compile recipe or substitute a final native object list for
original evidence. Require ordinary/native raw values, complete small-planner
target/recipe/prerequisite projections and the fixture generated C to agree.
Keep literal, original-derived, renamed, short and equivalent `patsubst`
controls. Disable only exact construction and then only target consumption:
both must recover the late refusal, with the fact absent only in the first
removal. Restoring each path must reproduce the original positive.

Compare actual GNU and native metadata for nested operands, parenthesized and
braced references, suffix changes, empty stems/lists/components, prefix
whitespace and repeated/trailing slashes. For example, `notdir` of
`a/ b/ file c/` is exactly `  file `, not a normalized path list. Unsupported
extra percent operators must remain unproved rather than silently losing a
literal percent. Keep an original snapshot through aliases, then rewrite its
referents; its old exact value must remain, while new unknown/effectful,
staged, cyclic, conditional/default/target-specific or namespace-invalid
inputs acquire no exact fact. Header-only wildcards stay nonexact.

Put literal and derived `.POSIX` in a static target list that does not match
the target pattern. GNU's warning is not rejection. Assert the first following
non-recipe continuation uses the old GNU folding and the next uses POSIX
folding. A later safe target rewrite, including one hidden behind the
mode-sensitive comparison, cannot hide the original mode or HIDDEN default.
An unmatched old native claim must reject. Retain the existing target-mode,
source/primary, staged-reference, metadata-laziness and payload/empty-pattern
guards. Reject a byte-limited join before exhausting its input generator;
removing only that funding must expose the oversized result.

Use a finite `STEMS ?= first.c` source-to-object chain with exported snapshot
aliases. Compare every default/command-line/environment state's ordinary
output, native object value, export membership/value and prerequisites.
Unsealed or recipe-only-symbolic STEMS must reject; unused metadata-only
error/shell bodies must not execute. Keep the existing 512-name/context,
deadline, native-frame and child/waiter cleanup boundaries.

Maintain the eight-header support/remaining-obligation table in
`docs/validation-ownership.md`. Exercise actual related suffix/addprefix
declarations with `.pre.c`, `.assets.d` and `.headers.d`, explicit bounded
literal leaves, and actual secondary/preproc prerequisite syntax. Keep the
aggregate consumer before `.SECONDEXPANSION`, matching the real linker rule;
the intentionally late-consumer variant must still hit the staged-reference
guard despite native graph equality. The seven former source refusals now
retain full original ancestor slices and component-removal controls, as
described below. These focused checks leave all baselines1-16 closed and
allocate no17, provider, H1 or full graph/report work.

For the cohesive seven-leaf closure, obtain namespace authority only from a
completed real native observation in its owning session. Inspect initial
materialized regular names, matching directories, known empty/absent results
and ignored untracked working-tree files. A matching unadmitted symlink or
unsupported filename/pattern must reject rather than disappear. Try a
caller-created token, copied observation, forged completed capture, changed
state/primary/restart, altered private backing and a token from an exited or
different immutable view. None may issue exact names. Exhaust the existing
cache budget and require failure before output retention.

Run a registered build-only writer and then a source-directory writer. In the
latter, publish both `src/z.c` and `src/new.c/child.txt`. Native EARLY, LATE and
recursive LAZY may all remain `src/a.c` in that process, while the final union
includes the new file/directory and a second ordinary GNU process sees them.
The source query must remain held; the unrelated build-only case may qualify.
Keep nested inherited/retained publications and cleanup restoring original
membership in the actual history. A conflicting producer identity remains an
error and cannot seal a partial capture. Do not weaken publication ownership
to manufacture a replacement sequence.

Retain actual Makefile wildcard lists, composition, generated-source
exclusion and filter ancestry; DATA's ordered multi-pattern expression;
modern recursive defaults; original BGM JSON/script availability through
`and`, `strip` and `findstring`; exact-snapshot append; and the real derived
`MODERN_CFLAGS` scope. Use the existing registry declarations for default and
ambient inputs, not an invented empty input or literal substitute for the
source list. Keep original include/secondary ordering and static headers.
Compare ordinary/native target values, complete bounded planner projections,
source reads, defaults, exports and per-file context. Disable each of the
seven components independently and recover its refusal, then restore the
identical positive.

Check per-pattern C ordering, duplicate/overlapping patterns, hidden names,
matching directories and exact empty results. Change an original directory
variable between uses of a recursive/default wildcard and require different
appropriate results; defined-empty environment/command-line values must
continue to suppress `?=`. Test substring rather than word membership,
duplicate-preserving `filter-out`, GNU `and` whitespace/short-circuit behavior
and lazy error/eval/shell bodies through simple, recursive, exported and
recipe reads. A target-local override, a possible dynamic writer or an earlier
parse-time read must prevent whole-source literal pruning.

Run the eleven indexed logical-entry, foreach-scope and C-whitespace soundness
regressions in `AuthoritativeMakeProbeTests` before accepting the seven-leaf
checkpoint. Use the genuine original-input witness and the unchanged small
fixture/session budgets. Preserve the following three pre-fix programs without
adding a recipe read of HIDDEN or an unrelated declared default that would
mask the failure. Also add standalone, assignment-tail and rule-header Make
comments containing lone dollars or fake binder/call/eval expressions to the
global-empty lazy fixture. Ordinary/native/planned behavior must remain
unchanged and the unused error body must not execute. Conversely, keep real
local foreach bindings in tab recipes, inline recipes, define-body data and
escaped-hash assignments: those must retain the actual HIDDEN obligation
and reject, not disappear under a global comment stripper.

Also exercise the parse-time, not just deferred-recipe, local scope. Define
`EMPTY :=`, `VALUE = $(and $(EMPTY),$(UNUSED))`,
`UNUSED = $(eval .POSIX:)`, then
`TRIGGER := $(foreach EMPTY,nonempty,$(VALUE))`. Follow it with a continued
`LATE = first \` / ` second`, condition `HIDDEN ?= secret` on LATE equalling
`first  second` (two spaces), and use `all: ;` without a diagnostic recipe.
Ordinary GNU succeeds; native LATE must have two spaces and HIDDEN must be
file/recursive/secret. Original effect analysis must retain the local
uncertainty before folding that continuation, and the complete small planner
must reject rather than accept `defaults=[]`. Preserve braced/nested,
referenced-binder, escaped-hash, immediately expanded define-body, local
`value` metadata and repeated global/local VALUE occurrences. Keep the
substitution-reference form rejected too. Removing only the scoped operand
protection must reproduce the original literal and metadata false admissions.

Outside the loop, global-empty VALUE must stay inert, while global-nonempty
must retain the `.POSIX` effect. Keep an unrelated local binder, an already
captured simple VALUE and a genuinely empty loop inert; compare native
LATE/HIDDEN and complete small-plan admission. The direct effect controls
also distinguish a global effect used in a loop's name/list from that name's
simple local use in the body, preserve unused metadata-only effects, and
hold a computed selector that depends on local scope. Do not infer any of
these original facts from the final native value or repair a pruned source
stream only in its final census.

The original three pre-fix programs are:

1. Create `src/ordinary.c` with no hidden files. Set
   `MATCH := $(wildcard src/.*)`, condition `HIDDEN ?= secret` on nonempty
   MATCH, and print only MATCH in the recipe. Ordinary GNU and native raw
   MATCH must be `src/. src/..`; native HIDDEN must be
   file/recursive/secret. The issuer must decline the raw dot-matching
   pattern, the source census must retain HIDDEN, and the complete small
   planner must reject the unsealed default. Repeat with a real
   `src/.hidden.c`, with braced/referenced patterns and renamed variables.
   The hidden-file variant includes both logical entries plus the real file;
   do not claim that its pre-fix nonempty condition was false. Literal
   `src/.`, `src/..` and star patterns that could match either remain
   unsupported. `src/*.c`, `src/.hidden*` and `src/.*c` must continue to agree
   with GNU, including empty and duplicate results.
2. Define `EMPTY :=`, `VALUE = $(and $(EMPTY),$(UNUSED))` and
   `UNUSED = $(eval HIDDEN ?= secret)visible`; print
   `$(foreach EMPTY,nonempty,$(VALUE))`. Ordinary GNU must print `visible`,
   native HIDDEN must be file/recursive/secret, and global EMPTY must still
   be file/simple/empty after the loop. Retain the original recursive VALUE
   body, its UNUSED read and the HIDDEN default. Repeat with renamed/braced,
   nested, referenced/computed and short-reference binders, and a called
   definition containing the loop. Literal local names must not borrow their
   global constants; unknown binders must prevent whole-source pruning.
   Called bodies already had an unknown-writer boundary and are preservation
   controls, not newly claimed pre-fix admissions.
3. Put U+00A0 between `a` and `b` in the first operand of
   `$(filter-out ...,a b)`, assign the result to VALUE, condition
   `HIDDEN ?= secret` on nonempty VALUE, and use `all: ;` with no recipe read.
   Ordinary GNU succeeds with an empty recipe; its status message is not
   recipe output. Native VALUE must be `a b`, not empty, and native HIDDEN
   must be file/recursive/secret. The unchanged unsupported pattern
   must decline, retain the HIDDEN obligation and reject the small plan.
   Also retain the MATCH-only recipe control, a braced pattern reference, a
   renamed result and U+2003. In the same no-recipe shape, replace U+00A0 with
   an ASCII space: VALUE is empty, HIDDEN is undefined and the small plan
   legitimately accepts empty defaults.
   ASCII space/tab patterns, supported non-ASCII text operands, `strip`,
   substring `findstring` and `and` must retain GNU byte and empty semantics.
   A literal or referenced U+00A0 first operand is nonempty and cannot prune
   an actually executed deferred effect.

Remove each correction independently while retaining the other two and all
original source/native/budget guards. Restore the old omission of logical
entries, global-through-local assumption, or Unicode pattern splitting to
recover the respective false accepted `defaults: []` small plan. Re-enable the
correction and recover rejection without changing source or native results.
Keep the global-only empty-operand lazy positive, an unrelated foreach binder,
an already captured simple snapshot despite later local shadowing, metadata-only
unused error/shell bodies and actual export membership. Native
metadata must not execute an otherwise unused body. Record actual values,
defaults, reads and rejection separately from diagnostic wording or source
locations, and clean fixture paths, session children and producer waiters on
every path. These are complete bounded counterexamples, not evidence of a
whole-report bypass or permission for message publication/rename/namespace
changes; the separate real message-source hold and unallocated17 remain.

For the private regular-install primitive, run:

```sh
python3 -m unittest scripts.validation_ownership.tests.test_private_install -v
```

Start with the existing native fixture, genuine empty snapshot witness and
unchanged budgets. Supply the actual `textprocess.py` and `huffman.py`, two
small messages and real control definitions. Give the unmodified producer two
fresh private destinations beneath its declared output parent. Issue the exact
command through the trusted adapter seam, then inspect generated C/header
bytes and complete native install records: header then C, success status,
single-link regular identities and exact destination coverage. Without the
issuance step, the same real producer must still hit the original native
directory-entry relocation denial. This is private-command evidence, not a
successful public message/header pipeline.

Repeat with absolute paths, relative cwd and a real directory descriptor.
Reject an existing or unlisted destination, cross-parent/source-tree path,
dot/parent spelling, directory or hardlinked source, symlink creation, open or
duplicated source descriptor, mutable mapping, unsupported rename flags and
another-actor creation. Remove and recreate an empty private parent before
install: the held original directory descriptor must prevent inode-reuse
confusion, not merely compare an old number after the fact. A destination
cannot be installed twice even if it is unlinked between attempts.

Try an equal/cloned command, copied permission record, changed command binding,
foreign session, real immutable-view switch/restoration, expired session and
replayed launch. None may inherit the grant. Observe that replay is rejected
before another native run. Drop or duplicate an actual native outcome, change
its scope/destination/identity or add fields; the paired parser must reject.
A command that merely writes its result without performing its declared
installation is not a positive. Keep all source/input, snapshot, actor,
pathname and resource guards intact rather than weakening fixture assertions.

Every success/failure path must close supervisor parent/source pins, session
children, launch records and fixture directories. Native process-suite
collection must include the new module exactly once; ordinary graph/workflow
discovery must not import its `TestCase`. The existing case IDs, original
selectors, nine jobs and34 commands remain unchanged. The C-only integration
is exercised below; header-step publication/retirement and per-read-phase
authority remain required before any complete live-message or full-report claim.

For the real text producer and its dispatch context, run:

```sh
python3 -m unittest scripts.validation_ownership.tests.test_text_producer -v
```

Use the unmodified text/huffman files, real default text and definitions, and a
tracked comparison header produced by the ordinary relative-script invocation.
Keep the Make-visible C absent before dispatch. Require ordinary/native C
bytes to agree, both real private installs to complete, and only C to be a
declared/public output. Repeat with real transitive include files. Cycles,
missing/escaping/unsupported paths and underdeclared reads must reject.
For an overdeclared control, use a genuinely unconsumed nested path: a root
member actually seen by import-directory enumeration is not an unread control.

Change one comparison-header byte and require failure before any C publication
receipt. Keep the header immutable, preserve its exact generated script line,
and clean every private comparison/temp file. Exercise a component-level real
include-remake prerequisite so the actual recipe export and native rebuilding
bit are observed. Its small readiness include is only a producer/context
control, not the required real ARM/header pipeline acceptance scenario.

Compare genuine first-create, changed-replace and unchanged-retain
publications with an issued nondefault mode. The new mode-preserving policy
must keep that mode for changed content; the old content-only policy must
still adopt the newly produced mode. Check effective identities and all old
policy/ownership refusals.

Pass two genuine inherited environments through native Make. The exact
context-aware primary command must see each value, while registration helpers
and direct commands remain canonical and cannot reuse the other environment's
cache result. Do not assume GNU 4.3 passes newly exported file variables into
parse-time `shell`; use actual native envp or a real recipe control. Corrupt a
live request's context/argv/rebuilding type and require rejection before
producer registration. Preserve native error-policy, export and cache tests.
All original numeric limits and case IDs remain unchanged; read-pass/source
authority and the five-step header pipeline are not supplied by these tests.

For native job identity, keep the first recipe command, later commands,
parallel targets with identical argv, shell expansion and include-remake
controls. Require actual target/ordinal associations even when argv cannot
distinguish targets. Use a real Makefile parallel setting; the public
assignment API's refusal of execution-authority MAKEFLAGS overrides remains
unchanged. Corrupt a live job sequence or submit an authenticated-notification
tag from an ordinary candidate command: both must reject. Wait/read hooks
must include the real fortified read entry without bypassing its libc bounds.
Missing context is a hold, never a guessed job or source epoch. Record
stopped/incomplete exploratory hook runs and their cleanup honestly.

For the per-dispatch owned header filesystem primitive, run:

```sh
python3 -m unittest scripts.validation_ownership.tests.test_header_effects -v
```

Start with the included `build/one.headers.d` absent and the real five native
recipe dispatches. The two regular data writers in this fixture deliberately
isolate filesystem authority; they are not ARM/sed or full-message acceptance.
Require actual directory creation, mode0600 scan temporary visibility to the
second writer, mode0640 filtered temporary, exact first-temporary removal and
atomic transfer of the second temporary. Inspect the real before/after
identities: inode, mode, bytes, size, mtime and single link must survive
transfer. Record the actual native target and ordinals1 through5, and require
only the final dependency file in generated output.

Repeat with a renamed nested output and an existing immutable parent witness.
Remove each required step, alter directory/source/destination operands, add an
extra removal operand or leave a partial pipeline: required execution must
fail, not return a synthetic success. Ordinary nonremake recipe observation
is a separate positive: it observes five commands with no rebuilding bit,
issues no effect and publishes nothing. Do not incorrectly require that
observation-only mode execute or reject these recipes.

Try equal/copied/mutated commands, copied issued records, repeated invocation
and foreign-session reuse. Corrupt actual native request operands, owner,
source identity/content hash, scalar/container types or fields. Omit/change
actual confirmations and created-parent identities. Every changed authority
must reject. After a transfer request is prepared, change the actual source
mode/content, replace its inode, remove it, or substitute a symlink/FIFO.
The native executor must recheck the real object and refuse every mutation
without publishing the final target. A tracked final file or either temporary
must stay byte- and metadata-unchanged; existing parent directories remain after
generated cleanup.
Keep native children, pending effects, public ownership and fixture cleanup
complete on failures as well as success.

The parsed graph must assign the new runtime exact ownership and the new test
module to the existing native owner, with complete trusted runtime staging.
Removing the test's exact declaration must recover semantic-admission refusal;
a neighboring unregistered module must still reject. Discovery must collect
each native case only in its existing process suite without `TestCase` aliases.
No budget, CI job/command count, case ID, source-phase permission or allocation17
changes. Actual ARM/sed is exercised by the following separate controls;
complete per-pass source authority is not inferred from component success.

Run the pure HeaderV3/shared-return controls without native setup:

```sh
python3 -B -m unittest \
  scripts.validation_ownership.tests.test_header_pipeline.HeaderReceiptInertTests -v
```

They use the real parser, authenticator, guard reservation/commit methods,
immutable views, private fingerprint and `ProbeSession` issue/claim/retire
methods under inert dependencies. Require valid complete, conditional-short,
repeated-occurrence and report-array-permuted transcripts. Omission,
insertion, resequencing, payload/count/digest repair with the original tag,
foreign completion fields, decimal terminals and malformed rows must reject.
The retained version-1 parser must reproduce the original repaired-count
omission acceptance as the restoration negative.

Require exact fingerprint vectors for every scalar/container tag, Unicode
surrogates, signed zero, list/tuple equivalence, dictionary-order neutrality,
aliases, cycles, unsupported subclasses, integer/float domains and cumulative
work exhaustion. Exercise exact owner/object/thread/job/view/epoch binding,
copy/replay/purpose/mutation failures, original result values after claim,
retirement, pre-growth row and terminal boundaries, retained failed charges,
single-count transfer, and key-reference release. Immutable runtime rows must
retain ordinary JSON/index/equality behavior, return themselves from
copy/deepcopy, reject every ordinary mutator/reinitialization, and contain only
the existing eight semantic keys. These controls are protocol and custody
evidence only; they do not qualify a native sed/libselinux execution.

The [HR1-HR5 correction](https://github.com/laqieer/fireemblem8-expansion/issues/180#issuecomment-5737364905)
uses that same inert selector and case ID. Keep all ordinary/native routes
separate and unchanged. From a clean checkout:

1. Fingerprint a 10,000-element array and a disordered 10,000-key dictionary.
   Observe allocation growth between actual admissions: arrays retain only
   ancestor/index frames, while dictionaries pre-admit key references and
   sorting scratch separately. Require unchanged F2b vectors, alias work and
   dictionary-order neutrality. Python allocation tracing is not RSS evidence.
2. Deny calculation capacity, including for an escape-heavy Unicode scope.
   Require zero encoder calls before refusal. Reject subclassed, non-ASCII
   and oversized wire values before slicing/copying/decoding; deny decode
   capacity before constructing a large terminal payload slice. Observe
   actual calculation/decode allocations under their representation grants.
3. Inject failures before and after attempted/accessed insertion and at
   reservation retirement, for rows and terminals. Retain spent authority
   through every attempted publication; allow conservative overcount, never
   refund. A later row's encoding/hash failure cannot sign an earlier prefix.
4. Replace issued stdout and stderr independently with distinct equal bytes.
   Both claims reject; unchanged claims retain the original byte references
   through subsequent completed/report mutations.
5. Exercise the actual supervisor control flow with process, signal, resource
   and filesystem effects replaced by inert boundaries. Early errors and
   nonzero statuses release the verifier before report construction; success
   signs before release. Signing/publication faults still clean up.

Retain actual-helper pre-fix failures and restore the affected helper in each
targeted control to recover rejection failures. Restore the correction and
require green results without changing limits, formats or selectors. These
controls launch no candidate, compiler, SDK, sed or native supervisor process;
the separate native qualification remains an independently allocated step.

The [coherent-forgery case preparation](https://github.com/laqieer/fireemblem8-expansion/issues/180#issuecomment-5737416238)
adds no execution allocation. Only after a separate exact-head authorization,
use the existing small Linux x86-64/GNU Make4.3/ARM GCC/C SDK/sed fixture and
run these two selectors in this order, stopping on the first failure:

```sh
python3 -B -m unittest -f -v \
  scripts.validation_ownership.tests.test_header_pipeline.ArmHeaderPipelineTests.test_actual_arm_sdk_and_sed_keep_five_original_dispatches \
  scripts.validation_ownership.tests.test_header_pipeline.ArmHeaderPipelineTests.test_actual_kernel_completion_rejects_repaired_forgery
```

The positive retains its ordinary-Make comparison and one confined Make.
The negative uses one confined Make only. Completion of both therefore means
three Make calls across two independent fixture/session lifetimes; do not
substitute the full native class, add an ordinary negative run, or retry.

For the positive, require the original five dispatches, actual ARM/SDK inputs,
sed observations and final output agreement. For the negative, intercept
exactly one actual returned filter report with a coherent version-2/status-0
completion and nonempty contiguous rows. Duplicate one actual row under the
next sequence, repair the public count/ordered manifest and conservatively
increase observation accounting within the original public residual grants.
Keep the actual tag, scope, binding, status and every original payload.
Insufficient count/byte/file capacity fails the case; it never widens a limit.
One-row and legitimate repeated-path transcripts remain supported.

Require the genuine header authentication error to propagate through
`_sandbox_run` to the Make caller before filter capability issue/claim,
output materialization/capture, cache retention or semantic-view publication.
Earlier directory/ARM-scan effects and the private empty filter-output
preallocation are not claimed absent. Require normal owned cleanup afterward.
Never inspect a private key/signer/config or retain/print raw reports, tags,
environments or streams. The test-local transform is checked separately by
`HeaderReceiptInertTests`, including acceptance when only MAC verification
is removed; those model controls are not native results. The existing case,
class/module mapping and all broader native selectors remain unchanged.

For the actual ARM/SDK/sed header pipeline, run:

```sh
python3 -m unittest scripts.validation_ownership.tests.test_header_pipeline -v
```

Use Linux x86-64, GNU Make4.3, the installed ARM GCC/C SDK and real GNU sed.
Copy the current original five-step header rule and its include guard, retain
the exact sealed failure branches, and start with the dependency output absent.
Compare ordinary Make with the native route. A C header selected by `__arm__`
must appear while the host-only header does not; a separate real host-cc
control must choose the opposite header. Inspect both executed ARM images,
the actual SDK input records, raw `.tmp`, real sed output and final owned
transfer. Repeat ordered `-D`/`-U` and include search choices, and the supported
binutils/ABI profile. Unknown flags, sysroots, plugin/response/forced inputs,
shell expansion, extra sed actions or changed failure operands must reject.

Corrupt captured SDK bytes and attempt an ungranted data file or compiler
plugin read. Check the closed SDK root, entry, alias and input schemas; a known
ungranted member must never be relabeled absent. Remove/copy/replay the actual
opaque runtime launch and require refusal before another payload launch.
An ordinary Python syscall137 attempt still rejects without the header grant.
Drop or forge real kernel observations/completion, and require refusal.
Kernel stream receipts distinguish an actual parsed prefix from observed EOF;
never replace the real sed or disable its library constructor to avoid a call.

Compose the unmodified text/huffman producer with the original header steps
using genuine default text and definitions. Keep the C absent before native
dispatch. Require the same greater-than3MB C and final dependency bytes as
ordinary execution, six actual remake dispatches (text, then ordinals1--5),
and unchanged tracked header bytes/metadata. This is a real component
composition, not the full original-root census: do not replace the original
raw source wildcard, prune first-pass obligations from final values, or claim
an authenticated original read epoch from this result.

The existing native owner additionally installs `gcc-arm-none-eabi` and
`libnewlib-dev`; the same job and command collect the new module exactly once.
Keep all prior cases and selectors, namespace/metadata/cache boundaries,
default-deny host compiler/runtime modes and complete cleanup. Record the
initial direct-runtime-alias fixture mistake, incomplete runtime profiles and
read-only diagnostic results honestly. No full graph/report, local quota
increase, H1 or allocation17 is supplied by these tests.

For the opt-in native original-read observation, run:

```sh
python3 -m unittest scripts.validation_ownership.tests.test_read_epochs -v
```

Use the unchanged native limits and GNU4.3 x86-64 runtime. Supply a real
command-line FLAG, environment input and a raw lazy error body which source
explicitly leaves unexported. Let source override FLAG and introduce file,
eval and included-only variables. Require each actual read-pass entry to
capture its own original raw values/origins before source evaluation, without
executing the lazy body or borrowing final definitions. Final FLAG must show
the source override while the original entries retain the real CLI input.

Use an included file which is initially absent and genuinely remade by the
existing producer route. Require real failed then successful status, both
actual exec/pass entries, ordered source visits and exact original file
bytes. File builtins reading the same file must be separate ordinary I/O,
not extra source visits; recipe-time file reads must have no original-pass
claim. Exercise a parse-time producer while a source frame is active and
confirm its descendants do not inherit the parent's observation authority.
Enable the same observation on the genuine default text/ARM/sed component.
Require both original read passes and the failed-then-successful generated
header visit. The greater-than3MB generated C is compiler data, not a Make
source visit; tracing must not relabel every input file as a source.

Try an ordinary command's forged source notification. Compile the controlled
observer variants which self-signal SIGTRAP or report a different caller
frame, keeping the actual native process and all guards. Both must reject,
not become source entries. Alter captured ABI anchors/call sites and remove
or corrupt actual trace scope, exec/input/status/source bytes/goal/completion
records. Require refusal and full child, descriptor, breakpoint and scratch
cleanup. Keep the unmodified default-off case and existing observer, input,
recipe, private-install and header-pipeline behavior.

The trace is not the completed source-phase certificate. Do not turn its
data into a changed-namespace grant, infer success from a nonnull goaldep,
or replace original per-pass state with MAKE_RESTARTS, MAKEFILE_LIST or final
definitions. Independent mutation capture, original entry namespace use
contexts and every pass's read/default/export/effect/obligation union remain
the precise separate hold. No full graph/report or allocation17 is exercised.

For the immutable per-pass archive, the existing whole `test_read_epochs`
automation also owns the five archive methods. No additional suite or case
ID is needed. Start with command-line FLAG, a raw unused error expression,
source overrides, an included child and a genuinely remade include.
Reconstruct through the session's original-source archive accessor. Require
the original FLAG/raw expression in every input scope, distinct failed and
successful include records, original entry/return flags and native goal order.
The final source override must not backfill those original inputs.

Repeat an include and add same-file and after-parse file-builtin reads.
Require every real visit in entry order while nonsource reads stay separate.
Use the same actual context-aware producer to replace a generated include
between two real visits; preserve both bytes/identities rather than the latest
filename mapping. A fixture's MAKE_RESTARTS expression may control its real
behavior, but archive pass identity must come only from actual exec/read events.

Check that repeated archive access launches no native run, immutable nested
records have no writable field dictionary, and copied/changed/foreign/expired
or unqualified observations reject. Corrupt actual status, visit coverage,
input flags, source bytes and return flags below zero or above unsigned32;
require rejection. The pre-fix return-flags control accepted10-2^32 and10+2^32
in a real trace validator; this is a typed-data counterexample, not evidence
of small-plan or whole-report admission. Keep the existing owned selector,
resource limits and teardown. Archive availability does not waive general
journal coverage, source-use semantics or the complete obligation union.

For the original entry-image barrier, run:

```sh
python3 -m unittest scripts.validation_ownership.tests.test_source_phases -v
```

Start with a real `src` directory containing only a non-C witness. Let the
original source wildcard be empty, set/export HIDDEN, then genuinely create
source C and an included file before successful Make self-exec. Use the
existing context-aware producer seam: an ordinary canonical-environment
Command is not a valid producer-environment counterexample. Require actual
producer HIDDEN=secret, final HIDDEN undefined and final source membership
including the new C. The existing invocation-wide wildcard guard must still
reject rather than using final values to erase the first pass.

Enable source-phase observation. Require the native entry-image event directly
after each actual pass-entry, with exact scope/input/event/image bindings.
Inspect complete first and second source-directory membership: no new C at the
first entry and real C present after acknowledged publication and reexec.
Repeat with the genuine default text/ARM/sed component, leaving C initially
absent and the tracked header immutable. Do not call these images a completed
source-phase certificate.

Corrupt request or acknowledgement scope/pass/input/image/sequence fields,
and require refusal without another successful source entry. Copy or mutate
the observation, reuse it in another session or after lifetime expiry, and
require the private image accessor to reject. Preserve the default read trace,
ordinary producer/publication accounting and complete child/scratch cleanup.
The native owner collects the exact new module once; no job, owned command,
numeric bound, Main accounting assertion or allocation17 changes. Independent
mutation/dispatch association and every-pass obligation union remain required.

For native originating-read/publication binding, run:

```sh
python3 -m unittest scripts.validation_ownership.tests.test_source_effects -v
```

Use the same genuinely missing-C/include-remake fixture. Confirm native
producer HIDDEN=secret, final HIDDEN undefined, and two original entries.
Inspect the new source-effect sidecar: the remake originates after the first
read pass and publishes before actual self-exec, even though its host
acknowledgement arrives at the second entry barrier. The old invocation-wide
wildcard refusal must still hold. The pre-component negative has no origin
sidecar; it is not evidence of a whole-report admission.

Repeat with literal and computed renamed includes, a same-file `file` read,
`eval` and a called shell producer. Require the actual current source-reader
visit, not an extra visit invented for a file builtin or eval. Deferred shell
use and identical recipes for distinct targets must retain separate post-read
origins; suppressed recipes must not acquire invented producers or outputs.
Run the unmodified genuine default message/ARM/sed composition with C initially
absent. Require all six real publications, including each directory/scan/
filter/retirement/transfer step, actual native ordinals and immutable tracked
header bytes/metadata.

Run the existing real nested-publication composition with both traces enabled.
Require separate child/parent scopes, actual child self-exec and explicit
parent adoption; the child's fresh pass must not refresh the parent's source
origin. Corrupt source-origin scope/pass/sequence/membership on the real
channel, or remove/reorder/change origin and publication records in the
actual native report. Require refusal, preserving all physical fixture
cleanup. Changed or expired retained sidecars cannot reuse entry images.

Keep default and read-trace-only behavior, exact native-suite ownership and
the Main full native job/accounting regression unchanged. Removing the exact
new module admission or using its unregistered neighbor must reject. These
records close originating native dispatch/publication observation only;
independent intermediate/host/nested/parent/cleanup mutation coverage and
every-pass source-use/obligation union still block phase certification. No
full graph, quota change, broad suite or allocation17 is required here.

For independent fixed-directory mutation windows, run:

```sh
python3 -m unittest scripts.validation_ownership.tests.test_source_journal -v
```

Start with admitted witnesses in the source and output-parent directories,
but no generated C or include. Enable `observe_source_journal`. Require actual
kernel create/write/close events, native begin/end acknowledgements, first-pass
producer HIDDEN=secret despite final HIDDEN undefined, and separate terminal
file-removal events covering all ranges without gaps. The original wildcard
guard must still refuse the touched source namespace.

Create/delete an unregistered host file before and during a native window;
also change and restore the original Makefile bytes. Require refusal even
when final membership or bytes match. Remove the observer or corrupt its real
kernel stream with overflow/truncation/foreign-watch records; require failure,
not an empty successful journal. Alter actual request origin/sequence/stage,
acknowledgement digest or terminal native receipts and require refusal.
Copied/changed observations, remapped original directories, expired pins,
nested Make and invalid selection values must not obtain a journal.

Run the genuine default text/ARM/sed/header-transfer component with a clearly
identified admitted existing-parent witness. Preserve that witness during
ordinary-result cleanup; execute the real original recipes in both routes,
leave C initially absent and retain tracked-header byte/stat immutability.
Require all six native publication windows, the paired real rename cookie
and terminal cleanup. This is an existing-parent profile control only.
Without that witness, the original new-parent path must explicitly refuse
before directory publication. Do not precreate parents or install a late
watch and label the resulting blind interval complete.

Keep ordinary/source-phase-only behavior and Main's native job/accounting
unchanged. Preserve exact runtime/test ownership, the existing process-suite
owner,98 case IDs and all prior automation. No numerical bound, guest
permission, C ABI, CPP adapter, extra job, full graph/report or allocation17
changes. Closing general new-directory coverage and every original source
pass's obligations remains a prerequisite for full phase certification.

For the prewatched-directory profile, run:

```sh
python3 -m unittest scripts.validation_ownership.tests.test_source_directories -v
```

Start with the original source fixture and no `build` parent. Select the
prewatched journal mode explicitly. Require native private-directory creation,
an original FD/watch before public visibility, matching real rename-cookie
and self-move events, same-inode installation and later file-then-directory
cleanup. The first entry must omit the new parent and the post-reexec entry
must contain it. Keep first-pass HIDDEN and the existing wildcard refusal.

Run the unchanged genuine default message/header case with C initially absent
and no extra parent witness. Require real text, ARM/cc1/sed and all six original
publication dispatches, all three parent handoffs and deepest-first directory
cleanup, ordinary/native output equality and immutable tracked header bytes/
stat fields. This is actual required component execution, not a benign
pre-scan or complete original-root planner/census admission.

Create/delete a child in the watched private stage before movement and in the
public directory before host pathname rebinding. Both must refuse; the old
late-watch blind interval is not accepted. Nonempty/replaced/chmod stages,
changed source/public-parent bindings, forged request/acknowledgement/terminal
records and missing child-watch movement evidence must likewise fail.
Race an existing destination against atomic installation and require its
inode to survive. Repeat with a terminal report failure before acknowledgement:
cleanup must not delete a directory which no installed handoff owned. The
pre-fix terminal control demonstrates the old predicted-cleanup failure.

Require copied/expired observations and invalid profile selections to reject,
and keep ungranted ordinary directory rename forbidden. Preserve the original
fixed-profile new-parent refusal, native-suite owner, exact module admission,
98 case IDs, all prior automation and old resource limits. Main's historical
BASE-renewal and CPP scopes are unchanged. Directory coverage does not waive
every-pass source semantics/obligation union, full-root admission or allocation17.

For every-pass source interpretation and obligation union, run:

```sh
python3 -m unittest scripts.validation_ownership.tests.test_phase_census -v
```

Use the real missing-source/include-remake fixture with the prewatched
journal. Interpret both original passes. Require first-pass HIDDEN=secret
in the actual producer environment and final HIDDEN undefined, while the
union still contains its first-pass default. The complete small phase planner
must reject that unsealed default. A no-default positive must complete, and
constants differing between passes must be absent from the shared pruning set.
Remove only the every-pass union in the paired control: the complete small
planner again admits an empty default census despite that same actual first
producer environment. Restore the union and require refusal. This is a
small-planner counterexample, not a whole-report admission claim. First-pass
graph, recipe and exported-variable dependencies must also survive the union.

Compare recursive wildcard aliases, simple source-time snapshots, target/local
overrides and literal metadata reads. Snapshots and body-lazy metadata controls
remain valid; deferred recursive/opaque namespace use, parse-time/non-remake
or unclosed mutations and unsupported initial flags/scopes reject. Copied,
changed, foreign or expired observation data cannot supply the original images.
Require actual failed-then-successful include visits and publication versions,
not final MAKEFILE_LIST or attempted-open membership.
Repeated includes remain distinct visits, while same-file and Make-looking
data reads through `file` do not become additional source evaluations. A real
within-pass replacement retains both observed source versions but cannot
authorize source-time namespace evaluation. An actual original command-line
input must govern its earlier snapshot despite a later source override;
unused effectful bodies and literal metadata reads stay lazy.

Use an actual native writer that creates an included `GENERATED_BINDING`
assignment, and export a phase label filtered from `.VARIABLES`. Keep the
fixture free of conditional defaults. The first producer environment must
contain an empty label, while final raw metadata contains `GENERATED_BINDING`.
The per-pass checker and complete small planner must refuse the unsupported
universe read. Repeat with direct/braced references, identity and computed
substitutions, transitive/computed aliases, a conditional and an actual
universe export. The closure check must run even without a wildcard read.

Remove only the per-pass executed-read check: the primary and computed-alias
small planners again admit an empty default census despite that same actual
first producer environment. Restore the check and require refusal. This is
an explicit small phase-planner counterexample, not a historical invariant
planner or whole-report claim. Literal labels, unused recursive bodies,
metadata-only exports and a proven unexecuted `and` operand must still pass;
raw metadata must retain its dollars without executing the unused body.
For the GNU4.3 source-read shell path, distinguish an unpassed Make export
from an explicitly expanded command argument. The argument must reflect its
original input even if a later literal assignment empties the final value;
the executed universe read still refuses, while the unexecuted body stays lazy.
The existing native-module automation above covers these controls without
adding another owner or catalog record.

For original input execution and forced-control precedence, keep the same
real generated-include fixture and whole-module command above. Supply a
command-line `UNIVERSE` domain containing `$(.VARIABLES:%=%)`, read its origin,
then filter it into an exported phase label. Require the authentic initial
recursive binding, first producer label empty, and final label containing
`GENERATED_BINDING`. The complete small planner must refuse. Also require
refusal when only the original command-line variable is implicitly exported,
without a source-file definition or explicit body read. Do not add a default
that would mask either missing body closure.

Compare literal inputs, source-defined bodies, direct/transitive/computed
references, command-line versus environment precedence, early and late
overrides, explicit unexport and `value` metadata. Early effective overrides
must not execute an unused original body; later overrides must not erase an
earlier read. Initial-only literal exports remain valid and are recorded as
reads, not source definitions. Proven unexecuted `and`/`or`/`if` operands stay
lazy. Distinguish actual Make recipe export expansion from the GNU4.3
shell-function environment passing raw input text, both during and after
source reading. Command suppression is not the export-expansion rule: inspect
the effective GNU origin/flavor and actual export selection for both value
dispatches and recipe projections. Check all actual expansion contexts rather
than assuming one helper invocation. Unexported variables actually used by a recipe still require
their body closure; unproven target/private/local contexts remain held.
Check both inline and block `+` recipes that read an original input after a
later effective override. Both actual commands must receive the replacement
literal; the source walk must not expand the stored inline recipe early.
The genuine header component must also keep automatic `@D` and related
spellings in their deferred local context, not read an unproven global input
or invent an automatic value for pruning.

Independently remove only the original execution callback. The two intact
small-planner negatives must again admit empty default censuses with the
same actual producer inputs; restore it and require refusal. The independent
source-defined universe guard must remain intact.

Supply command-line `MAKECMDGOALS=other` while requesting goal `all`, with a
conditional `HIDDEN ?= secret` and export under that actual variable value.
Require the producer's real `HIDDEN=secret` and command-line goal metadata.
The source walk must retain the possible default instead of seeding `all`,
and the complete small planner must refuse the unsupported forced context.
The earlier single-seam removal admitted both states with empty default
censuses; keep that historical evidence. Now remove only forced/supplied-name
participation in invocation-fact seeding: the source walk must again expose
the wrong goal fact and lost default, but the independent effective-export
binding check must still reject the complete planner's actual HIDDEN mismatch.
Do not weaken that newer guard to demand another unsafe admission.
Restore the check. Apply the same rule to
every invocation-control fact, and retain the normal unforced goal, origin,
flavor and control-read facts. These are explicit small-planner corrections,
not historical-default, full-root, resource or H1 admission claims.
Repeat the original control observation with an environment assignment:
GNU still reports `MAKECMDGOALS=other` and exports `HIDDEN=secret`. Its supplied
presence must withhold the same synthesized facts without giving environment
variables command-line precedence over later source assignments.
For the original control/filter guards, compare a literal `filter` result and
a filter of the unchanged original invocation goal with real GNU observations.
Require both continued FIRST/SECOND values, including the delayed POSIX change,
to match the source census. Known original operands are positive evidence;
do not keep an obsolete unsupported-`filter` assertion as the negative.
Use a genuinely unproved original pattern for the unknown-input negative and
retain the reassignment/eval/computed/undefine refusals, invalidated snapshots
and all other malformed/effectful cases. Removing exact filter must break the
positive; replacing missing proof with empty data must break the negative.

For effective GNU export semantics, use a capture program that prints
`os.environ["UNIVERSE"]`. Define a recursive source value
`$(filter UNIVERSE,$(.VARIABLES:%=%))`, export it, and use an ordinary
`@python3 capture.py` recipe. Ordinary Make and the authenticated projected
recipe environment must both contain `UNIVERSE`, despite interceptor
suppression. The analyzer and complete small planner must reject this
unsupported executed universe read. Repeat through a command-line domain,
without adding a default or another guard that would mask the original
admission.

Next supply that expression through the original environment and use a
required `+@python3 capture.py` recipe with only an origin read in source.
Ordinary and actual confined execution must print the unchanged expression
and succeed, with implicit and explicit export. The effective binding remains
environment/recursive: export transports data rather than executing the body.
Its command-line counterpart and an explicit Make-language variable read
must actually expand and refuse. Raw equality is evidence to compare, not the
criterion used by the checker.

Exercise skipped `?=`, empty/nonempty append, override, active/inactive
conditional writes, source redefinition and simple `value` snapshots.
Require native origin/flavor/output agreement. Empty append preserves the
environment origin; nonempty append changes it to source origin and causes
recursive export expansion. Simple dollar-bearing values stay data. A named
export or unexport of an undefined name must create the actual empty
file-origin simple binding before subsequent metadata reads.

Restore only the old projection-kind exclusion and recover the source and
command-line small-planner admissions. Independently restore the
environment-body-as-executed rule: the actual raw-transport positive must
again be rejected. Restore both fixes. Keep the original input, control,
filter, namespace, lifetime, unexport/lazy and private/scoped holds intact.
All of these controls remain in the same indexed phase module and case.

#### Namespace refusal attribution controls

For ordinary-recipe binding context, vary paired-dollar punctuation and
end-of-input, odd/even dollar runs, renamed variables, braced references and
neutral command spelling. Keep unescaped unsupported dollars and incomplete
expressions negative. A rule's inline header, define/assignment body or
unproved source role must not inherit its recipe segment's context.

Retain direct/braced/nested `foreach`, computed/referenced/short binders,
context-free escaped `foreach`, and nested eval/reparse/opaque-call holds.
Operations inside single quotes or hash-containing recipe data remain Make
operations. Restore the old helper's staged classification independently:
the same original filter must again return unknown, erase eligible whole-source
read constants and disable the namespace snapshot loop. Restore the correction
and require both consumers to recover with identical original source facts
and default obligations.

An unsafe outer object alias alone can still terminate at a different safe
simple snapshot. Do not label that existing positive a new admission: the
joint local-shadowing negative must actually remove each relevant snapshot
stop. Preserve the exact old/current outcomes when authoring such controls.

Use the existing phase-module pure parsed-data/actual-function controls.
Exercise direct wildcard, unresolved selector, dependency and export-refusal
branches; unknown/eval/scoped/ambiguous/stale bindings; exact-snapshot positives;
and the existing serialization/count/cache/closed-lifetime bounds. Require
precise exclusion fields where available and explicit missing data otherwise.
Forbid evaluator re-entry during formatting. Inject formatting/accounting
failure and retain the exact primary source error with a secondary cause/note,
never partial success or silent truncation. These pure controls are not
native/root proof.

Restore the published ancestry-copy/late-retention behavior independently.
The 201/801 retained-state regression must fail at its actual collection or
allocation bound, not merely because a new attribute is absent. Separately
restore raw secondary traceback retention while keeping the linear traversal
and admissions intact; actual projection owners must remain alive and fail
the lifetime control. Restore the correction and require both controls to
pass. Use runtime container structure, measured retained allocations and
the selected dependency path together, not exact-byte fingerprints, source
spelling or a larger stress graph.

The source error's custom attribution is available to an in-process caller.
The ordinary reporter prints only the existing primary message. The current
diagnostic harness records exception chains and code-frame identities, plus
counters/native-failure metadata; it does not include custom attributes or
notes. A chained diagnostic failure can appear as a secondary exception, but
the corrected source helper supplies only a sanitized bounded stage/type
exception, not raw failed-construction frames. That does not publish the
detailed source record or fix the formatter for other exception families.
Keep any harness publication
change or later bounded root run under a separate accepted scope.

#### Required scoped-source negative and restoration controls

Independently restore the old literal-pattern, scoped-immediate-RHS and
blanket deferred-scoped-name holds. The corresponding original LZ, legacy
and modern positives must fail at the restored boundary, while retaining the
same genuine issued observation and clean teardown. Do not substitute a
fixture/path-policy failure for an intended removal result.

Remove only scoped lookup or the inherited append base and require the
actual native consumer consistency to fail. Replace an earlier immediate
RHS with the recorded later global value and require rejection, not a new
source fact. Preserve first-pass HIDDEN/default obligations in the real
include-remake fixture; removing only their union must expose the lost
obligation without claiming a whole-report bypass.

Require unknown/computed/escaped destinations, overlapping scopes,
parent/shared-child inheritance, private/override combinations and
automatic-sensitive assignments to remain held. Literal metadata must not
execute an unused body, and command-line precedence must not erase a genuinely
evaluated immediate RHS. Copied/expired use contexts and stricter existing
count/byte/deadline allowances must reject rather than reset their budgets.

For the two-job corroboration control, declare its synthetic aggregation
target `.PHONY: all`, retain both scoped values and the generic object recipe,
then make exactly one actual job unproved. The other valid context cannot
hide it. The earlier non-phony fixture failed on an `all.o` pathname before
the mutation and had incomplete-journal cleanup; that remains setup evidence,
not the intended control or an inferred syscall history. The precise fixture
correction changes no production Makefile, native policy or permission.

Restore the original raw C-source wildcard/default/append and derived header
constructor to the genuine text/ARM/sed component. Retain its real goal/include
guard, initially absent C/parents, actual outputs and unchanged tracked header.
Both source passes must interpret from their own images, including the first
failed dependency include. Exact source-time include/filter relations use the
bounded grammar and GNU whitespace; non-ASCII pattern normalization is not a
proof. Keep old constructor/POSIX/metadata/default/export and numeric controls.

The separate full-root attempt also keeps the original header order-only
toolchain check and genuine recipe. Source correctness closes before Main's
separately frozen contained resource scope; the old cap is not a prerequisite
to sizing itself. No prerequisite removal, shell/compiler broadening or
completed original-root report follows from this case. The source-phase option is explicit and the
ordinary invariant planner remains unchanged. Preserve the existing native
owner, case IDs, all automation and limits; no allocation17/H1 follows.

### Terminal generated-file cleanup ownership

The [5710465329 correction](https://github.com/laqieer/fireemblem8-expansion/issues/180#issuecomment-5710465329)
addresses terminal cleanup deleting foreign files at names that were only
planned, or deleting replacements after the actual output was displaced before
acknowledgement. Both original native reports failed; they were not successful
quota or source-phase/H1 bypasses. Preserve those preimages separately from
the following corrected results.

Use the existing Linux/native-probe prerequisites and an owned source fixture
with the original `src` and `build` parent witnesses. Do not inject faults into
live project outputs. Run:

```sh
python3 -m unittest scripts.validation_ownership.tests.test_file_ownership -v
```

1. Let the actual producer reach its planned-output/pre-publication boundary.
   Place a foreign file at the reserved name and inject terminal failure.
   Its bytes, inode and namespace link must survive in ordinary, fixed-journal
   and prewatched-directory modes. A reservation alone must not authorize
   targeted or blanket cleanup.
2. Let native publication install the real output, then displace it and create
   a foreign same-path replacement before the final acknowledgement. Require
   both objects to survive, with the original inode still held by its actual
   descriptor. Repeat before descriptor acknowledgement and at the first byte
   charge after descriptor receipt; uncertain handles must remain registered.
3. Replace the public entry exactly between the preliminary check and atomic
   removal claim. The replacement must not be deleted. Block restoration with
   another public occupant; neither that occupant nor the privately retained
   claim may be overwritten. Move a pinned parent during the claim and require
   restoration into the original parent, not deletion through the new mapping.
4. Unlink an owned output while its pin is live, then create a foreign file.
   The old inode cannot be reused while pinned. A forged numeric identity
   cannot replace the real descriptor; no foreign cleanup follows.
5. Keep healthy cleanup and known-owned failed-report cleanup functional.
   A lost reply after actual pin approval still removes the known-owned
   partial output. Corrupted file scope/owner/path/identity/parent records
   reject without inventing cleanup authority.
6. Exercise lost retirement/transfer acknowledgements with the existing real
   header primitives. Keep the same pinned object across its possible names.
   Exercise a selected BASE view, restore CURRENT, and exit the outer session:
   retained foreign/displaced objects must not be erased by either cleanup.
7. Restore the old reservation-only deletion condition and recover the actual
   pre-publication foreign deletion. Independently restore stat-then-unlink
   removal and recover deletion at the real removal race. Do not weaken the
   corrected object/FD assertions or pin an arbitrary helper-call count.

The native publisher's cleanup registration is a closed private descriptor
handoff, not final publication or phase evidence. Final content/mode/source
acknowledgements still validate normally. Public cleanup uses an atomic
no-overwrite claim and the actual pinned inode; the independent healthy
journal records that move and verifies final unlink state. Failed/incomplete
journals remain invalid while known-owned, quiescent cleanup proceeds.

On uncertainty, the original failure and cleanup diagnostics remain visible.
The report/view and ownership handles stay retained. Inspect their actual
objects before explicitly releasing retained handles; handle release alone
never deletes a retained path. Tests perform independent outer cleanup only
after checking the retained bytes, identities, descriptors and zero children/
waiters. Retain the existing directory actual-install rule, numeric limits,
default source permissions, native test owner and all prior automation.
No new case ID, runtime platform, permission, workflow job, full-root
certificate or measurement allocation is introduced.

#### Native reply and retained-object fixture compatibility

The [5713730754 follow-through](https://github.com/laqieer/fireemblem8-expansion/issues/180#issuecomment-5713730754)
preserves this case's native assertions across actual-file registration.
The four unchanged methods reproduced seven failures/two errors in 47.885s
at `c99b2af68f83d0cfa8dfc0d8dc5bbfbc9eb94742`. The 7.961s ACK-latch and
14.687s retention runs were read-only diagnostic experiments, not a committed
fix or complete graph/H1 evidence. Keep their provenance distinct.

Use fresh, independently owned Linux/native fixtures for each fault, with the
existing Python, native tools and limits. Do not inject into live project
outputs. Run these existing selectors together:

```sh
python3 -m unittest \
  scripts.validation_ownership.tests.test_producer.ProducerTests.test_late_reply_family_rejects_partial_stale_foreign_and_unknown_messages \
  scripts.validation_ownership.tests.test_producer.ProducerTests.test_separately_sent_final_reply_is_rejected_while_make_continues \
  scripts.validation_ownership.tests.test_header_effects.HeaderEffectTests.test_native_transfer_rechecks_actual_source_object_after_request_preparation \
  scripts.validation_ownership.tests.test_source_directories.SourceDirectoryTests.test_missing_child_watch_evidence_and_remapped_public_parent_reject -v
```

1. Let the real driver send the original producer result and return to its
   receive loop. Require the actual-file descriptor exchange and its uniquely
   bound `file-pinned` acknowledgement before the fixture waits for the native
   Make-continuation marker. Match scope, producer/sequence, owner and the
   original one-output path. Keep the original five/30-second bounds.
2. Require positive `VALUE=observed`, one actual producer and settled native
   receipts. After real continuation, send each original partial, stale,
   foreign, unknown or duplicate late reply. Each must still reject natively.
   Restoring the old synchronous result-send latch must recover the native
   stall; changing an ACK association must not forge continuation credit.
3. Preserve transfer fault order: mode, contents, replacement, symlink, FIFO,
   missing. Every original native rejection, changed-list, publication and
   header-registry assertion remains required. Mode/contents/missing clean
   fully. Replacement/symlink/FIFO retain the actual foreign object with its
   bytes or target, type, matching descriptor/path identity and `nlink=1`;
   the old owned inode has `nlink=0`. A fresh fixture for each fault prevents
   retained scratch from contaminating the later missing-file control.
4. Drop the original child-watch move evidence and require its native rejection
   plus strict cleanup. Separately remap the public root at the original watch
   boundary, preserve the native rejection and restore the original inode in
   the original `finally` step. The report must remain explicitly retained
   despite that restoration; no lifetime uncertainty is reset.
5. Keep the original inner native error distinct from the expected outer
   retained-report error. Require zero execution resources, registered live
   retained handles and surviving objects before release. Explicit release
   closes handles but leaves the report and paths present and retained.
   Only the independent fixture's later teardown removes its own arena; this
   is not successful production cleanup. A retained-object deletion or silent
   retention-removal mutation must fail these actual-state assertions.

`assert_clean` still requires both execution closure and absent report/scratch.
The narrower test-only execution-closed helper does not mark retained state
clean and never releases or deletes anything. All existing case IDs, native
owners and automation remain; production protocol, permissions, limits and
the independent full-report control-byte boundary are unchanged.

#### Native job correlation after helper exit

The [5721273772 correction](https://github.com/laqieer/fireemblem8-expansion/issues/180#issuecomment-5721273772)
keeps authenticated job identity separate from a live helper's lifetime.
At `3c3d79484ec55e66f6ab3fa922b6832249ec2f42`, exactly three bounded direct
Make diagnostics preserved a real ordering defect: delaying one actual parent
job-context stop until its helper exited left dispatch/helper/policy records
complete but the job-context map empty. The original CI log did not record
those sets or stop order, so its precise historical missing set is not claimed.
The separate unchanged-method 12.093s pass does not clear this race. The third
diagnostic's inverse hold did not activate and is not credited as exercised.

Use the existing Linux/native prerequisites and independently owned fixtures,
not live project outputs. Run:

```sh
python3 -m unittest \
  scripts.validation_ownership.tests.test_producer.ProducerTests.test_retired_job_context_survives_actual_helper_exit \
  scripts.validation_ownership.tests.test_producer.ProducerTests.test_retired_job_context_old_drop_restores_missing_job \
  scripts.validation_ownership.tests.test_producer.ProducerTests.test_retired_job_context_reobservations_preserve_identity \
  scripts.validation_ownership.tests.test_producer.ProducerTests.test_retired_job_context_rejects_authority_and_identity_changes \
  scripts.validation_ownership.tests.test_producer.ProducerTests.test_retired_job_context_spends_existing_metadata_and_count_bounds \
  scripts.validation_ownership.tests.test_producer.ProducerTests.test_retired_job_reports_require_complete_unique_native_bindings -v
```

1. Use the original multiline literal-space recipe. Hold one **real** parent
   `VO_JOB_CONTEXT` stop until the actual helper has exited, then deliver that
   unchanged stop. Require the same dispatch sequence, recipe target/ordinal,
   observed helper PID and its native policy as the before-exit order.
   All four final set relations and helper-PID uniqueness remain mandatory.
2. Observe that the original live `Process` is removed and its actual pidfd
   is closed before the late context is handled. Only immutable plain
   correlation metadata remains: PID, sequence, role/helper kind and an
   optional validated context. No FD map, memory reservation, live actor or
   executable authority may be retained as a tombstone.
3. Repeat the actual context and require the same binding. A changed target
   or command ordinal, wrong parent/observer, unknown closed process, missing
   helper identity, duplicate PID/sequence or reused retired identity rejects.
   New-child failures must still own the actor for complete cleanup.
4. Remove only the old closed-PID early-return fix. The real late context must
   again be dropped and the unchanged driver must reject missing job evidence.
   Independently remove/corrupt job/helper/policy records or uniqueness: no
   malformed report may become a successful observation.
5. Exhaust the existing admitted descendant/dispatch count or remaining
   observation bytes before insertion/update. No unfunded retired record or
   context is stored, and spent bytes are not refunded. Count capacity uses
   admitted cumulative extent, not a larger new ceiling or live-process
   retention.
6. Run the unchanged multiline, PID-policy, parallel-job and actual
   target/ordinal controls. Require closed pidfds/children, zero pending/
   parked/waiter state and complete fixture cleanup after positives and
   negatives. Never synthesize a context from argv, source text, exit status
   or a default policy, or serialize all helper execution as a workaround.

The native C observer, interceptor and 24-byte context wire format remain
unchanged. The receiver emits through the existing validated path; the final
driver completeness checks are not weakened. This invocation-scoped metadata
is not a persistent ledger, source/export fix, permission/profile change,
resource calibration, full-root certificate or H1/17 allocation. The existing
case and sole native test owner remain; all prior automation is preserved.

For both literal and derived target lists, a new target-local flags append
must remain recursive: a later RHS change produces `global later`, while
native per-file raw metadata remains `$(FLAGS)`. Do not eagerly copy the
global simple flavor. Preserve all original append precedence, POSIX
first/next-line timing, metadata-only reads, header-bound nonvalue, source
guards, staged/unknown/cycle, byte/name/deadline and child/waiter checks.
Keep the current support/hold ledger with these results. A genuinely touched
or uncertifiable required namespace remains a hold, not full-source success.

For the real framework template contract, copy the complete current
`GENERATED_DATA_LINK_TABLE_RULES` and
`GENERATED_DATA_MODERN_OVERRIDE_RULES` definitions and their actual
`foreach`/`eval`/`call` invocations into isolated same-named Makefile fragments.
Supply alpha/beta JSON, shared and table-specific Python files, config
headers, a small fixture generator and the installed ARM compiler. Keep the
same native observer and ordinary Make routes. Inspect the full generated-C
prerequisite set, generated-C-to-modern-object edge, recipes and real C/ELF
outputs. Change generated and modern output directories, table selection,
linked-table list, config and shared Python selectors through finite domains;
every recorded closure must equal a separate actual native observation.
Adding a table-specific module must change its real wildcard prerequisites.
Rename both macros without changing their bodies or callers' semantics:
the native graph must remain equal, proving there is no symbol-name allowlist.

Wrap the real generated-data template include in `ifeq (yes,yes)`. It is
provably active, not an uncertainty negative. Require the same target,
prerequisite and recipe projection from the native observer and small graph
planner as the unconditional form. Run ordinary GNU builds for both, forcing
the second build, and compare the generated C and actual ARM ELF object.
Changing the condition to false must lose the required generated target.
For the uncertainty negative, use
`ifeq ($(eval TEMPLATE_CONTEXT := touched),)` around the same include. Native
Make must still include it, set TEMPLATE_CONTEXT to file/simple/touched and
produce the same graph, but original effect/source proof must reject.
Retain every other opaque-template and macro-identity negative; neither
predicate handling nor original-source guards are relaxed by these fixtures.
Keep a later `.SECONDEXPANSION` directive, as in the real source ordering;
also exercise secondary activation before/after an included ordinary rule.

Reject transformed headers, immediate/deferred stateful recipe operations,
extra dollar stages or parameters, non-identifier parameter data, unproven
initializers, external macro overrides, late input/macro rewrites, undefine,
dynamic write destinations, uncertain include context and unsupported
wildcard patterns/names. Specifically make alpha's config depend on FLAGS,
then rewrite the linked-table list to beta only after both real callers.
The original alpha rule must still contain the first/second config selected
by FLAGS while the later list reads beta; that later list is not permission
to analyze only beta. Removing only the original-assignment-order guard in an
isolated mutation must restore the false symbolic acceptance and omitted
finite FLAGS domain. Every source byte, native receipt and existing budget
remains in force. The source-faithful slices use a fixture generator, not the
real production generator, and do not establish whole-repository resource,
verifier, H1 or delivery acceptance.

For native ignore policy, execute real `exit 7` recipes with no ignore rule,
`.IGNORE: unrelated-target`, `.IGNORE: all`, empty `.IGNORE:`, `MAKEFLAGS += -i`
and a command-local `-`, including an expanded `-` prefix. Ordinary Make exits
2 for the first two and 0 for the remaining forms. Captured native bits must
agree. Mixed one/two targets and mixed commands retain `[true, false]`, not
one bit inferred from `.IGNORE` existence. The real unrelated scoped rule
must preserve a valid lifecycle checker. Join authenticated GNU wait-policy
receipts to actual helper PIDs/dispatches; missing, malformed, mismatched or
forged receipts reject without falling back to global flags. Keep actual Make
restarts, parallel dispatch and metadata-only recipe behavior intact.

Set `export OPTION = first` and run a recipe
`printf '%s\n' "$$OPTION"`, then change the value to `second`. Both ordinary
outputs and native authority must differ. Repeat with target-specific exports,
inherited global `unexport OPTION`, and swapped exports on two scheduled
targets. Equivalent declaration ordering must stay stable. Inspect actual
`native_dispatches` environments and their original observation/control-byte
charges; a host-only variable and observer bootstrap variables must be absent.
No unused variable is expanded to discover exports. GNU Make 4.3 does not
support a target-specific `unexport` directive; preserve its actual failure.
A single-quoted `'$$OPTION'` prints literal shell data, so it is not an
output-changing variable-expansion control.
For assignment-order controls, compare ordinary Make's variable
values/origins/flavors and prerequisites separately from its recipe's actual
exported `MAKEFLAGS` bytes. Reversing CLI `A=one` and `B=$(A)-two` leaves the
first projection equal but changes the environment and complete native
identity. Do not sort or mask those bytes. For quote controls,
`printf %s 'a b'`, `printf '%s' 'a b'` and `printf %s a\ b` have identical
direct native argv. GNU Make instead selects `/bin/sh -c` for
`printf "%s" "a b"`: registered output and VALUE still agree, but complete
native identity must differ.

For recipe literal preservation, use a single-quoted `printf` argument whose
backslash-newline continuation has a physical `#first` line, then change it to
`#second`. Repeat with quoted whitespace and a here-document. Native recipe
bytes, scheduled argv and real ordinary output must retain each change.
Non-recipe Make comments remain stable; an actual additional recipe dispatch
is not erased as a purported comment-only equivalence.
Use a computed `value`/`origin`/`flavor` selector whose native prerequisites
change under a sealed domain: the graph must reject the unsupported selector
instead of reporting an incomplete successful census. Unconsumed debug
recipes and semicolons inside Make expressions retain native behavior.
The shared foundation owns snapshot/command caches within the report and
selected immutable view; there is no reporter-global graph cache or per-target
session/deadline reset. Wildcard-visible paths, modes, source bytes and generated
outputs retain their actual captured identity. BASE registry/models are measured
in one grouped public view, and lifecycle removal/restoration reuses that
already validated model without recursive report execution.
For the lifecycle dispatch negatives, replace the consistency root with
`.#missing || true`: the real shell exits zero after the launcher's missing-root
failure, but no lifecycle proof may be issued. Replace the Make program with
`scripts/absent/../validation_ownership/isolated_launcher.py`, or use root
`missing/..` or `Makefile/..`. Actual execution fails and the original path
components must reject before proof. A real existing traversal such as
`scripts/generated_data/../validation_ownership/isolated_launcher.py` with
quoted root `scripts/..` still passes. Preserve the original shell/native
failures, restore fixture files and close every child/session. Removing only
the comment-boundary or component-validation correction must restore the
old false three-proof acceptance in the owned fixture, not merely change an
error message.

For operator identity, independently append `'>' /dev/null`, `">" /dev/null`,
`\> /dev/null` and quote-concatenated equivalents to both declared routes.
Ordinary execution rejects those extra arguments before checking the artifact;
the report must retain them as arguments and issue no proof. Exercise both
native direct argv and shell-dispatched Make forms: GNU Make may choose direct
argv for single quotes/escaping but a shell for double quotes; explicit
`SHELL := /bin/bash` also exercises the shell path. The exact95 defect issued
three proofs for the real shell-dispatched forms despite ordinary exit 2.
Restore the old quote-losing decoder as an isolated mutation and require that
same false proof acceptance to reappear.

Real unquoted `> /dev/null`, `> '/dev/null'`, `>"/dev/null"` and
`>''/dev/null` must still bind and execute successfully through both roles.
Keep quoted roots, adjacent quote parts, comments and the existing
conditional/startup/path/source/model controls. Compare generic literal
punctuation tokens with actual shell argv; do not blacklist only one spelling
or infer operators from decoded word values.

For the live producer grammar regression, use the unchanged
legacy-dependency-dry-run-recipes registration with:
`mkdir -p .dep/src/ && cc -E -Iinclude -nostdinc -undef -DUNUSED=1||true src/input.c -MM -MG -MT src/input.o > .dep/src/input.d`.
Make input.c include header.h. Ordinary shell execution exits zero after cc
reports no input files and true creates an empty redirected depfile. Exact4d7
instead passed `-DUNUSED=1||true` as one compiler argument and genuinely
published the complete input/header dependency rule. The corrected adapter
must reject the unconsumed operator before compiler execution. Quoting or
escaping the operator bytes inside that argument must preserve the complete
ordinary/native dependency output, actual GCC/cc1 execution and source/output
receipts. Existing outer regexes already reject the quoted fixed &&/> slots;
do not report those as additional live-registry bypasses.

Use separately labelled in-memory contracts for generic syntax controls.
A quoted/escaped pipe must remain a printf argument, not launch the Python
pipeline. Quoted/escaped whole FE8_ITEM_ID_CAP=value words cause ordinary shell
command lookup failure and must not become adapter environment assignments.
Genuine unquoted assignment prefixes with quoted values remain supported,
including on the Python side of a real pipe; an assignment scoped only to the
printf side cannot be transferred to Python. Preserve literal fd-looking
arguments before genuine trailing stderr syntax. Distinguish adjacent
unquoted IO numbers from quoted, escaped or separated digits.

Exercise all simple adapter branches with adjacent active `||`, `&&`, `;`,
pipe and output-redirection tails; no unused syntax may disappear. Keep the
existing modern wrapper envelopes and exact compiler argv controls. Compare
quiet-program argv/output for real stderr suffixes without claiming a new
general stderr/descriptor interpreter; the native process policy is unchanged.
Restore only the old word-based dependency/registration consumers in isolation:
the live dependency discrepancy and the separately labelled generic pipe,
assignment and fd-literal discrepancies must reappear with real execution.

For registered file discovery, create two matching files and one nonmatching
file below both `texts` and `scripts/assets`. Compare the complete ordinary,
registered and native Make results for `find texts -type f -name '*.txt'`
and `find scripts/assets -type f -name '*.py' -print`. Both current sealed
contracts must work; extra arguments/actions or active operators reject.
Restore the six-token-only arity guard and require the asset command to fail
while the implicit-print text control still succeeds.

For whitespace, append an actual CR to the consistency command's dot root.
The real shell passes `.\r`, and the launcher fails before artifact checking;
the report must not normalize it to dot and issue proofs. With a Bash Make
recipe, also place CR after `@` before the executable or after the root before
a real comment. These literal bytes must survive prefix trimming and decoding
and preserve the ordinary failure. The complete f33 preimages show false
proofs for the case trailing CR and Make leading CR; the separate 29-case
shell/parser matrix is not 29 graph runs.

Compare both public parser consumers with actual shell argv for trailing and
quoted CR, VT, FF, FS, GS, RS, NEL, line separator and paragraph separator.
Extra lines beginning with CR, NBSP, EM SPACE or IDEOGRAPHIC SPACE plus `#`
remain commands, not comments. The three non-CR leading Unicode executable
names were already preserved in the preimage and are not additional bypasses.
Keep ASCII tab/blank/comment behavior, quoted literal backslash-LF, LF
continuation at EOF and continued-word quote/comment boundaries. Restore the
old normalizer and old word decoder independently: each must reproduce the
actual CR false proof. A bare backslash at EOF without LF remains a named
unsupported grammar form, not a justification for banning control/Unicode data.

For trailing comments, compare both public parser consumers with actual shell
argv for `ok # note`, `a#b`, quoted `'#'` and escaped `\#`. Only the real
word-boundary comment disappears. Repeat with a continued word followed by a
comment, a hash joined to that word across backslash-LF, and ignored unmatched
quotes/backslashes inside the comment. Registration and upstream workflow
matching must see the same argv as the typed consumer. Restore loss of the
detected comment endpoint and require the real-shell differential to fail.

For startup authority, set `export LD_DEBUG = help` on the genuine Make
checker. Ordinary Make exits zero with loader help whether the authoritative
graph is present, committed as removed, or restored; the report must reject
that captured context before issuing any lifecycle proof. Repeat with
`LD_TRACE_LOADED_OBJECTS=1`. With `/bin/bash`, repeat using `SHELLOPTS=noexec`,
a fixture-owned `BASH_ENV` script that exits zero, and an exported `python3`
function that returns zero. These are actual startup skips, not successful
checker execution. Keep other loader/shell startup controls fail-closed under
the documented supported profile and reject a missing environment projection
or unqualified checker with an uncontrolled PATH.

As the positive counterpart, retain a benign `PROJECT_LABEL` export and an
ignored `PYTHONPATH`: real present/removed/restored executions must return
success/failure/success, and the complete environment remains captured.
For empty-root handling, use `--repository-root ''` independently in Make and
case automation. Ordinary opening fails with ENOENT, so neither route receives
proof. Dot, an actual absolute case root, quoting and native CURDIR remain
positive controls. Do not sanitize an unsupported environment and claim it
was the captured invocation.
Count actual output-producing dispatches through the graph consumer: a single
dependency-include remake must execute once, not once for registration and
again for Make. The completed observation must retain the real final bytes
used by the source census while the temporary publication is removed.
Create two independent captures of identical asset Git inputs and execute the
real producer/remake in each. Generated bytes, dynamic-command identity and
Make authority must agree even when actual source mtimes differ, and the
source mtimes must remain untouched. A real source content/mode/membership
change must still affect the captured digest. Preserve ordinary CLI stamping
and consumer behavior; do not compare its mtime digest as if it were the
captured identity digest.
Exercise unknown optional includes in relative, `./` and `/repo/` forms.
Native Make may ignore the real absence, but the graph must reject the
unadmitted attempted input even when `MAKEFILE_LIST` omits it. A tracked include
and a genuine producer-created include still pass. The captured syscall
spelling is not claimed to be the original pre-normalization Makefile token.
For a directory-backed table, add and remove matching bundle members between
CURRENT and BASE while retaining a nonmatching JSON neighbor. Only the schema's
real selected members receive generated-source ownership. Discovery must not
read member contents; the subsequent loader must reject an unreported read.
Ordinary bundle loading and inventory results remain unchanged.
Inspect the real graph discovery plan alongside the required
`ownership-probe-test` selection. Foundation, producer and dependency cases
must remain in their one extended-host owner, absent from both graph and
workflow discovery. The union must still contain every allocated case;
neither duplicate execution nor omitting a case is an acceptable correction.
Owned Git fixture commands disable automatic maintenance/GC, detachment and
hooks through per-command configuration. A real Trace2 control with locally
enabled maintenance must show ordinary Git spawning maintenance while the
fixture helper does not. Cleanup still reports real failures; it does not
ignore racing filenames or sleep until maintenance might finish.
Make metadata sections share one fully validated registry object per selected
loader/budget. Repeated references reuse funded input bytes, not a second Git
read for each section. Cross-view reuse, stale/missing inputs and malformed
seals still reject; the actual full-report result must remain unchanged.
Run the bounded-regex controls under one report lifetime. The
worker input ceiling must follow the actual validated encoded message length,
not the cumulative pending allowance. Exercise differently sized compile,
Unicode fullmatch, and schema requests through the real worker under unchanged
limits; preserve oversized-input and output-bound rejection. The
worker transport must reconstruct the complete original JSON before execution.
Compare real identity and lossless transports for the same repository pattern
requests and require reduced actual pending traffic; truncated, trailing or
over-expanding compressed input must fail. Identity transport remains available
when compression would grow the message. The catastrophic
command input and candidate-schema pattern must actually enter their worker
before the report deadline terminates them. Verify preserved ordinary and
multiline matches, standard-engine syntax, the actual address-space limit,
malformed/deep patterns, oversized input/output, cache-after-deadline failure
and interruption cleanup. The old unbounded matcher is reproduced only in an
owned externally watched child; its outer safety timeout is not production
budget evidence. Do not raise report limits or relax sources/seals to pass.
Every local scratch path component is opened relative to the trusted
repository descriptor with no symlink following before any temporary
directory is created. Tracked `build` and intermediate `test-artifacts` or
`validation-ownership` symlinks reject without touching their destinations.

Behavioral fixtures compare direct GNU Make with `MODE=two` conditional
selection, multiple words, nested functions and variables, `$(eval $(RULE))`,
`define`/`call`, pattern and static-pattern stems, target-specific values,
secondary prerequisites, `${NAME}`, one-character `$C`, and automatic
variables. Mutating a `%.out: %.in` child recipe changes its concrete parent
authority. Literal missing prerequisites and active `$(error)` reject.
Each target is probed with its standalone `MAKECMDGOALS`; an `a b` invocation
whose `a` branch differs from solo `a` must record the solo branch for `a` in
both input orders.
Unbounded/unregistered domains, malformed or oversized values, cyclic/dynamic/
escaping/unknown targets, stale finite domains, and symbolic recipe
classifications that reach any graph-shaping position reject.

The Make sandbox exposes no executable shell other than the static
interceptor. Trace or normalized recipe text never authorizes an interceptor
event. Unknown direct `$(shell)` in a recipe, an unused eager `!=` (including
the exact same or normalized multiline command as a printed recipe),
unregistered include-remake command, ambiguous registry regex, output
nonconvergence, or any attempted unsandboxed process rejects. Registered
source-dependent commands execute only in the second networkless exact-tree
command sandbox and bind their concrete output hash plus tool/input authority.
Normal `-n` recipes emit no event; forced-recursive/include-remake commands
must match one sealed contract and are intercepted rather than executed by
Make.
Absolute `/dev/stdin`, dynamic, escaping, symlink, missing, and untracked
includes reject. `build/../../work/evil.mk`, dot/repeated/encoded separator
aliases, and intermediate symlinks cannot acquire `build/` authority;
canonical regular build descendants are rebuilt from an empty overlay.
Supervisor mappings and events are absent from candidate-visible paths. The
trusted launcher opens a read-only mapping-directory descriptor and an
append-only event descriptor before chroot; no `/proc` or `/dev/fd` alias is
mounted. Make scratch, registered-command scratch, and build output are
separate. Candidate `$(file ...)` attempts and a registered Python forgery
script can create only private decoys, while truncated, wrong-count,
wrong-hash, malformed, or unknown-match event records reject.

Generated-data ownership comes from a trusted typed probe of the exact
candidate registry in a credential-free, networkless, read-only-tree process,
not an import of the base registry into the trusted reporter. Its bounded JSON
must have sorted unique schema names/dependencies, valid versions, and confined
tracked source/inventory paths. A synthetic candidate adds a new typed table
and path successfully; write/control forgery and malformed output reject.

The namespace launcher uses unprivileged user/mount/network/PID namespaces
when available. A mocked Ubuntu-24.04 control blocks that probe and admits only
passwordless `/usr/bin/sudo -n /usr/bin/unshare`; before Make starts, the
launcher clears groups/capabilities, drops to the original runner UID/GID, and
sets no-new-privileges. The local positive control selects user-namespace mode.
Loss of both modes, a different sudo/unshare path, or retained root identity
rejects.

For pull requests whose exact base contains the verifier package, host CI
archives the clean base into an unpredictable, inode-checked directory below
the trusted runner temporary root and directly invokes its `ci_verifier.py`
with `-I -S -B` against the exact candidate Git tree, without starting Make
first. It never creates or recursively removes
staging below candidate `build`; trusted Make and registry probes also use
that external verifier runtime. Every trusted package and loaded module
identity comes from base Git objects, and candidate paths never enter verifier
`sys.path`; compromised candidate reporter/interceptor fixtures remain data,
not authority. After full trusted validation of both graph versions, resolved
candidate `(edge_type, evidence_id)` pairs must equal the independent base
oracle byte-for-byte, oracle-backed authority fingerprints must equal the
exact base, and trusted edge invalidation must be empty for those edges. A
shape-valid retarget from `owner.validation-check` to the live gba-playtest
workflow step therefore rejects; an unrelated semantics-stable change passes.
Real docs and generated-schema paths ensure every graph surface and every
non-dependency owner edge is oracle-backed. Removing either probe, omitting
`generated-schema.owns-test`, or changing an unrepresented dependency edge
fails before exact-base authority can succeed.
For this introducing PR, the complete merged foundation-only BASE selects
`foundation-introduction`; its independently selected verifier source and
actual exact-head capture must report that mode, not authority-none bootstrap.
Run `NonReviewedCaptureBindingTests` below to verify real valid exact-BASE and
foundation captures, then substitute HEAD as BASE, another worktree, a different
check definition or another candidate. Every mismatch must be rejected inside
the trusted executor before a verifier can supply successful local readiness.
Reviewed qualifications remain additional authority, never a substitute for
these universal bindings. The exact478 preimage produced a real successful H/H
verifier result while capture recorded B and managed readiness was true;
removing the universal assignment check must expose that same wrong acceptance.
A bounded foundation fixture retains already-existing support code in BASE
rather than increasing the raw-diff size or hiding its source checks.
The reviewed-capture fixture must restore both `sys.modules` and the
`scripts.workflow_pilot` package attributes before deleting its source tree.
The real immutable tool loader installs `raw_diff_check`, `review_family` and
`review_subjects` under their canonical owned names; leaving those entries
behind caused the exact478 suite's later graph checks to resolve one already
removed fixture directory. Run `CaptureModuleLifetimeTests`: load the real
tools, close the fixture, then run the CURRENT/BASE exclusion case in the same
process. Repeat with partial tool-source loading failure. Original module
objects must be restored before directory deletion, and the original failure
must remain visible. An intentionally unscoped live wrong-root or deleted
owned module must still fail the unchanged trusted-source verifier; this is
fixture lifetime cleanup, not permission to ignore unrelated-looking imports
or swallow `FileNotFoundError`. These controls create no review session or
current H1 authority.
A genuine earlier base lacking every package/graph/oracle marker instead emits
`bootstrap-not-authoritative` with no claimed graph authority. Preserve that
separate historical/negative case rather than treating it as this PR's current
base. A complete graph-bearing base selects `exact-base-pinned`; partial
authority rejects instead of downgrading, including a base containing only the
Make-dynamics marker. Before the first Git command, the hosted step must unset
the exact ten path-bearing Git redirects while retaining its config,
replacement, and lazy-fetch scrubs. Missing/changed base staging, candidate
gate paths, candidate-as-base SHA substitution, or loss of the bootstrap state
rejects.

The root Makefile rejects command-line ownership of `MAKECMDGOALS` before
reading any include. `MAKECMDGOALS= -n`, all dry-run/touch/question/silent/
ignore aliases, hostile `MAKEFLAGS`/`MFLAGS`/`GNUMAKEFLAGS`/`MAKEOVERRIDES`,
mixed goals, and `AUTOTOOLS_CONFIG_MK=/dev/stdin SHELL=...` therefore fail
during trusted bootstrap. The exact sole goal skips every normal config,
generated, asset, and dependency include; plain and allowed parallel public
invocations execute the checker rather than printing success.

Lifecycle checks reuse the report's already validated complete model and
recheck the copied artifact's schema, graph identity and exact oracle pairs;
the consistency route also checks its captured tester-case registry. The
driver never recursively repeats the whole report. Every trigger has its own
real removal/restoration cycle. Native Make dispatch and captured case
automation must first bind to the real isolated checker and its actual trusted
sources, correct root/context and current session/model. The shared checker
runs for those bindings before removal, while absent and after restoration.
Results say `verified-dispatch-and-shared-checker`, not that the full consumer
or testcase executed during proof. Missing graph must fail with the named
reason and restoration must pass; no earlier trigger's successful cycle can
stand in for a later checkpoint, dependency-change or pre-graduation proof.

Run the focused `LifecycleBindingTests` command below with clean owned
fixtures. Actual public Make and consistency commands must consume their
authoritative graph and fail its removal, then pass restoration. Public
`lifecycle-check` must honor both bound identities without recursive proof;
the separately rooted standalone verifier must reject a no-op consumer.
Keep the genuine baseline/alternate consuming fixtures and original authority
invalidation controls. Equivalent quoting and isolation-flag order remain
valid; changing either role to a no-op, conditional/redirected/help-only or
wrong-root command must reject. A skipped existing target, ignored failure,
substituted checker source, missing automation or copied/forged/stale binding
cannot receive a proof.

For isolated mutations, bypass only consumer-dispatch verification and run
each no-op regression independently. Each must fail because the report again
accepts three removal/restoration proofs for the no-op role. Independently
bypass loaded-source verification and require the substituted-checker
regression to expose the same wrong acceptance. Restore the supported
implementation after each mutation; do not weaken admission or run the full
repository graph. The preserved bc02 preimage really removed the artifact and
made eighteen shared-checker calls, but neither consumer executed; its
independent no-op roles still received three proofs.

Exact current/base Git mode and provenance is mandatory. Symlinks, synthetic
gitlinks under owned prefixes, untracked/ignored/nonexistent changed paths,
mode changes, escaping authorities, untracked recursive Make includes, and
nonblob authority entries reject. Only the actual `mgfembp` gitlink reaches
the named exclusion.

A manual visual/audio/UX edge must point to
`.github/manual-testing-handoff.json` and remains supplementary. Removing a
required host, adversarial, compile, link, generated or observing runtime edge
fails. Applicability follows the actual consumer: preview-only files have no
ROM build/runtime roles, and unobserved art/audio cannot claim runtime
evidence. The independently sealed oracle also rejects removing a requirement
and its owner together.

### Interactions and save compatibility

This capability depends on issue #176's artifact admission/deletion lifecycle,
strict source boundaries, isolated startup, clean-Git checks, seals, and
maintenance reporting. Each checkpoint, dependency-change, and pre-graduation
event applies the verified-dispatch/shared-checker contract against a bounded
remove/restore sandbox with one fixed failure reason. Actual route execution
is separately identified in fixture evidence. It reuses existing Make targets, Build workflow
structure, the generated-data registry, tester-case registry, and manual
handoff contract. Issue #181 is parallel and not a dependency. There is no
feature flag and no gameplay, runtime, save, localization payload, generated
game data, ABI, ROM/RAM, modern debug/release, or archival behavior change.
The immutable Git-read correction reuses the existing authority/budget
foundation; issue #264's lifecycle-launch performance work is independent and
not required by this correction. Native/generated-binding semantics, process
policy, CI topology/timeouts and publisher limits remain unchanged.

The scoped-source extension depends on the existing issued source/job
authority and is independent of the separately pinned root-harness preparation.
Its dependents are later genuine-root/required-scope acceptance and public
reporter adoption; it does not change that public selector or authorize a
resource/provider/H1 run. Save, generated-data format and ROM/RAM are unchanged.

Refusal attribution is diagnostic-only and depends on the existing original
source state and error/budget seams. It changes neither snapshot admission nor
public/harness routing. Successful small snapshot evidence is not a semantic
fix or retrospective attribution of an earlier root failure.
The diagnostic retention correction also preserves these boundaries:
no evaluator/source permission, numerical allowance or refund is introduced.
It retains original input objects as legitimate caller/source context while
releasing diagnostic-only temporaries on failure.

Ordinary-recipe dollar context is a framework parsing capability, not a
temporal-snapshot change or optional gameplay flag. It depends on the original
unit role, existing inline split and both binder consumers. Staged/effectful
contexts, source/input/history/default/export/native guards and diagnostic
retention remain dependencies. Public source selection, Make recipes,
native profiles, ROM/RAM/save, generated-data and locale formats are unchanged.

The graph does not execute selected gates and cannot skip or narrow validation.
A later independently accepted issue must prove selection non-inferiority
before any delivery behavior can consume these explanations.

### Automation

The focused unittest suite owns parsed schema and graph invariants,
whole-repository coverage, target existence and drift, every edge-family
mutation, exact Git modes, confined authorities, independent oracle mutation,
executable lifecycle deletion/restoration, review invalidation, canonical
reporting, mixed-goal rejection, bounded scratch symlink rejection, and
unchanged source bytes/Git state across simulated fixture exceptions.
`make validation-ownership-check` runs the same reporter through
`/usr/bin/python3 -I -S -B`.

- `python3 -m unittest scripts.validation_ownership.tests.test_graph_report.LifecycleBindingTests -v`
  -- real public route, public lifecycle and standalone verifier references,
  native dispatch/skip/failure propagation, source substitution and model
  binding negatives. This runs only owned small fixtures, not the full report.
- `python3 -m unittest scripts.validation_ownership.tests.test_coordinator_capture.NonReviewedCaptureBindingTests -v`
  -- universal assignment/expectation binding and real non-reviewed managed
  capture/readiness; no native review qualification or current H1 is created.
- `python3 -m unittest scripts.validation_ownership.tests.test_coordinator_capture.CaptureModuleLifetimeTests -v`
  -- real immutable tool-loading cleanup followed by the CURRENT/BASE graph
  case, partial-load failure, and strict stale-owned-module negatives.
- `python3 -m unittest scripts.validation_ownership.tests.test_make_probe.AuthoritativeMakeProbeTests.test_append_uses_original_inputs_and_retains_unknown_branch_timing scripts.validation_ownership.tests.test_make_probe.AuthoritativeMakeProbeTests.test_complete_actual_tools_prefix_has_original_input_effect_evidence scripts.validation_ownership.tests.test_make_probe.AuthoritativeMakeProbeTests.test_original_effect_free_inputs_and_branch_alternatives_do_not_invent_modes scripts.validation_ownership.tests.test_make_probe.AuthoritativeMakeProbeTests.test_original_input_effect_evidence_does_not_hide_effectful_alternatives scripts.validation_ownership.tests.test_make_probe.AuthoritativeMakeProbeTests.test_original_target_value_alternatives_keep_the_existing_context_bound scripts.validation_ownership.tests.test_make_probe.AuthoritativeMakeProbeTests.test_unproven_and_missing_include_outcomes_do_not_become_empty_success scripts.validation_ownership.tests.test_make_probe.AuthoritativeMakeProbeTests.test_unproven_target_results_do_not_gain_final_value_or_normal_mode_authority -v`
- `python3 -m unittest tests.workflows.test_ownership_probe.ProbeExecutionOwnershipTests.test_graph_discovery_partitions_all_cases_without_repeating_native_owner -v`
- `python3 -m unittest scripts.validation_ownership.tests.test_make_probe.AuthoritativeMakeProbeTests.test_ineligible_original_conditions_do_not_read_operands -v`
- `python3 -m unittest scripts.validation_ownership.tests.test_coordinator_capture.ReviewedEvolutionCaptureTests.test_initial_qualification_requires_the_complete_immutable_change_set scripts.validation_ownership.tests.test_coordinator_capture.ReviewedEvolutionCaptureTests.test_required_path_coverage_rejects_counts_wrong_paths_and_another_root scripts.validation_ownership.tests.test_coordinator_capture.ReviewedEvolutionCaptureTests.test_deleted_and_mode_only_paths_require_actual_correct_side_reads scripts.validation_ownership.tests.test_coordinator_capture.ReviewedEvolutionCaptureTests.test_explicit_review_context_is_delivered_before_qualification scripts.validation_ownership.tests.test_coordinator_capture.ReviewedEvolutionCaptureTests.test_missing_wrong_identity_partial_scope_and_unqualified_expectations_reject scripts.validation_ownership.tests.test_coordinator_capture.IntroductionCaptureTests.test_real_foundation_introduction_is_explicit_not_exact_base -v`
- `python3 -m unittest scripts.validation_ownership.tests.test_reporter.AssetOwnershipTests.test_ownership_sources_and_collected_regressions_reach_actual_worker scripts.validation_ownership.tests.test_reporter.AssetOwnershipTests.test_ownership_execution_edge_removal_redirect_and_order_controls scripts.validation_ownership.tests.test_literal_bindings.LiteralBindingModuleTests.test_module_discovery_collects_only_owned_cases -v`
- `python3 -m unittest scripts.validation_ownership.tests.test_ci_verifier.ImmutableBlobBatchTests scripts.validation_ownership.tests.test_ci_verifier.BatchedVerifierSourceTests -v`
  -- actual Git byte equivalence, exact captures/gitlinks, bounded framing and
  copies, no cross-context reuse, real protected launch counts, per-file
  restoration, malformed-stream and trusted/live-module rejection/cleanup.
- `python3 -m unittest scripts.validation_ownership.tests.test_ci_verifier.ReviewedEvolutionVerifierTests.test_trusted_sources_reject_candidate_drift_before_session -v`
  -- the unchanged exhaustive fresh standalone verifier regression, including
  every candidate source's content, absence, mode and symlink mutation.
- `python3 -m unittest scripts.validation_ownership.tests.test_graph_report.GraphReportTests.test_document_serialization_without_semantic_change_does_not_invalidate -v`
  -- one real small CURRENT/BASE report, not a full repository measurement.
- `python3 -B -m unittest -f -v scripts.validation_ownership.tests.test_phase_census`
  -- existing whole-module ownership also covers the scoped native positives,
  raw/expanded and source-time distinctions, independent restored holds,
  corroboration loss, empty append, unqualified contexts and shared limits.
  No new module, case ID or execution owner is introduced.
  The same module also covers bounded namespace attribution and its pure
  formatting/accounting/no-reentry controls, plus the existing native
  recursive-alias and snapshot/laziness cases.
  Pure shared-prefix, duplicate/cycle, pre-admission and retained-exception
  controls cover linear storage and cleanup without adding a module, owner,
  case or native witness allocation.
  The phase module also covers ordinary recipe contexts, both pure consumers,
  staged/local negatives and the focused native include-remake positive.
  The already-mapped make-probe scope/laziness method retains its staged
  default controls and adds explicit ordinary-context cases. No new selector,
  blanket automation, case ID, module or ownership permission is required.

The focused A/V fixtures also execute the title fingerprint comparator with
missing/changed framebuffer controls, the actual presentation runner's
scenario construction, and the package checker's successful/resource-failure
inputs. The live parsed model, not the older 80-variable synthetic Make fixture,
owns the tester procedure's 112-domain scope. Parsing that count is not
complete domain execution: full 112-domain graph/oracle/lifecycle/public-gate
adoption of the independent
[issue #206 foundation](https://github.com/laqieer/fireemblem8-expansion/issues/206)
remains a separate acceptance requirement.
This is a required dependency, including registered native-tool results and
generated include outputs reaching the shared Make observer through admitted
APIs. Do not accept an old duplicated probe or a successful command whose
generated include never reached Make as that evidence. The existing host setup
must supply `png.h` and libpng/zlib for actual native `gbagfx` compilation;
either supported namespace mode is valid, while unavailable confinement or
native dependencies must still fail.

### Cleanup and limitations

Scratch fixtures are bounded beneath ignored
`build/test-artifacts/validation-ownership` and are removed automatically. No
remote state is read or changed. No manual-only criterion applies.
The Git-read controls additionally own unique directories beneath
`build/test-artifacts/verifier-git-batch` and `build/test-artifacts/full-report`;
their cleanup removes those exact fixtures after reaping children, including
failure cases. Never delete another run's directory to reset a test. Retain
benchmark logs outside the committed source and remove only owned scratch
artifacts when finished. These focused results do not certify full graph,
calibration, H1, native review, or whole Build CI performance.

Scoped-source fixtures keep all original input identities unchanged, close the
actual session, clear source archives/contexts and remove only their owned
scratch directories. Preserve setup failures and their first cause before any
authorized fixture correction. A non-fail-fast development batch continuing
after a failure is not immediate stopping; later local native-bearing selections
must be fail-fast. Successful projected recipes are not produced objects/assets,
full-source acceptance, a public report or resource calibration.

Retain the original source failure if diagnostic retention fails, with explicit
secondary evidence. Only a complete charged attribution record is attached;
missing visits/sites/associations remain missing. Preserve the first accepted
semantic candidate and the separately labeled recursive-late refusal; never
replace either with a claim about an unrecorded historical root context.
Do not keep rejected diagnostic graphs through raw chained exceptions or
failed helper frames. Clear transient source notes on every exit and retain
only the bounded secondary stage/type. Never clear the primary caller/source
context or refund spent admission to make a control pass.

Keep pure model-only fixture data separate from issued native authority.
Its template and phase facades must share the same real bounded budget; never
make production templates accept a missing session. Preserve setup failures
and failed author expectations honestly, together with the corrected evidence.
The native filter obligation is source-only when unselected; do not invent
a corresponding job. Source acceptance, future genuine-root allocation,
public reporting, resource sizing and H1 remain separate gates.

Rollback is a normal revert of issue #180's dedicated commit; existing broader
validation behavior is unchanged.

### Namespace-image CI regression correction

The [5696038608 correction](https://github.com/laqieer/fireemblem8-expansion/issues/180#issuecomment-5696038608)
addresses two production namespace regressions and one test-recorder
incompatibility. Use the existing Linux/Python/native-probe prerequisites and
owned temporary fixtures; no ROM, new package, profile, privilege or budget
change is needed.

1. Run the original eight scenarios unchanged:

   ```bash
   python3 -m unittest \
     scripts.validation_ownership.tests.test_foundation.FoundationTests.test_changed_metadata_forces_real_execution_without_mutating_validation \
     scripts.validation_ownership.tests.test_foundation.FoundationTests.test_immutable_view_metadata_changes_do_not_hide_behind_shared_bytes \
     scripts.validation_ownership.tests.test_foundation.FoundationTests.test_static_metadata_uses_persistent_objects_for_cache_and_native_make \
     scripts.validation_ownership.tests.test_foundation.ObservationAllowanceTests.test_nested_queries_resume_only_with_capped_shared_residual_grants \
     scripts.validation_ownership.tests.test_foundation.ObservationAllowanceTests.test_failed_resumption_overclaim_rejects_before_observation_settlement \
     scripts.validation_ownership.tests.test_foundation.ObservationAllowanceTests.test_valid_failed_resumption_retains_native_count_bytes_and_error \
     scripts.validation_ownership.tests.test_content_publication.ContentPublicationTests.test_nested_content_only_publication_keeps_effective_mode_and_ownership \
     scripts.validation_ownership.tests.test_producer.ProducerTests.test_cached_and_already_dispatched_listings_follow_live_publication -v
   ```

2. Exercise the added capture and recorder controls:

   ```bash
   python3 -m unittest \
     scripts.validation_ownership.tests.test_foundation.NamespaceImageTests \
     scripts.validation_ownership.tests.test_producer.ProducerTests.test_direct_namespace_capture_keeps_cached_listing_without_a_make_phase \
     scripts.validation_ownership.tests.test_content_publication.ContentPublicationTests.test_nested_recorder_accounts_for_revalidation_without_call_count_assumptions \
     scripts.validation_ownership.tests.test_content_publication.ContentPublicationTests.test_recorder_rejects_unknown_closed_variant_shapes \
     scripts.validation_ownership.tests.test_content_publication.ContentPublicationTests.test_indiscriminate_recorder_mutation_recovers_original_nested_failure -v
   ```

3. Before the operation, initialize only the owned fixture directories'
   timestamps. Direct image capture, with no Make query or source-phase
   transition, must preserve all 13 native stat fields and exact cached
   command/listing identity at the root, nested directories and selected
   BASE/restored CURRENT views. Real mtime and publication changes must still
   invalidate reuse. Deny metadata-neutral access at root and component opens;
   require an explicit error, no ordinary-open fallback and actual closed
   descriptors before retry/fixture cleanup.
4. Keep one tiny source limit fixed at four. A four-file flat cohort and a
   three-file deeply nested cohort must include their complete root/parent
   scaffolding. Five sources still reject at Snapshot admission. Extra
   physical entries, directory/symlink substitutions, missing members and
   unreceipted inherited output must reject, never produce a partial image.
   The original 64-source/128-observation tests must now reach their native
   nested callbacks and retain their original grant, failed-count and byte
   settlement assertions.
5. Observe real nested publication. Its validated effects remain exactly
   created then retained, with the same actual identity and effective mode
   0600, equal inner/outer generated ownership and original event counts.
   The verifier's closed identity-only revalidation variant is recorded
   separately, without requiring a particular call count. Unknown shapes,
   wrong retained identities/modes and malformed confirmations still fail.
6. Run the independent breaking controls. Removing only `O_NOATIME` from the
   actual directory descriptors must recover atime drift and false cache
   misses. Restoring the mixed source-limit counter must reject both otherwise
   admitted cohorts. Restoring the indiscriminate test recorder must recover
   the original missing-`effect` failure, not a different native setup error.
   The original pre-correction eight-case result was six failures and two
   errors; preserve that evidence rather than relaxing its assertions.

For the directly coupled boundaries, retain the existing
`ObservationAllowanceTests` exact-exhaustion, oversized-capsule and
oversized-source-inventory controls and `ContentPublicationTests` confirmation
parser/actual-wire mutation controls. These use their original small budgets.
All cases close owned sessions, descriptors, children and waiters before
removing their disposable fixtures. This is host regression evidence, not
full-graph fit, H1, native authority or calibration. Nine jobs, 34 commands,
publication policy/schema and all original resource limits are unchanged.

## TC-WORKFLOW-OWNERSHIP-PROBE-SANDBOX-001: Confine and bound authentic probe execution

The [namespace-image CI extension](#namespace-image-ci-regression-correction)
adds direct metadata/cache and real root/component FD failure controls to this
case. It preserves complete comparisons, including atime, rather than masking
fields or restoring metadata after validation.

### Feature and configuration

Issues [#206](https://github.com/laqieer/fireemblem8-expansion/issues/206) and
[#264](https://github.com/laqieer/fireemblem8-expansion/issues/264);
supported Linux x86-64 source checkout, GNU Make 4.3, Python 3, a static-capable
host C compiler, C++ compiler for native-tool controls, and working private
namespaces on Linux 5.12 or later. See the [foundation contract](../ownership-probe-foundation.md).
No ROM, emulator, credentials, remote workflow or feature flag is required.
Start from a clean checkout; fixtures use only ignored `build/test-artifacts`.

### Actions

1. Run `make -f scripts/validation_ownership/foundation.mk ownership-probe-check`.
   Inspect the returned native `localization-check` dependency on
   `localization-generate`, the actual output-directory value and the real
   chapterbundle registry result containing `src/data/ch2_bundle.json`.
   For the live consumer, use the foundation guide's fresh linked worktree
   without initialized submodules. The automated real-source fixture creates
   its own checkout, changes the admitted output-directory assignment and
   requires that actual live value. An initialized-gitlink CLI control must
   reject explicit-admission absence, while explicitly admitted live paths and
   immutable pinned inputs retain their distinct bytes. Do not borrow a CI
   checkout's incidental submodule state as the positive fixture.
   Registry controls must accept equivalent repository-relative and `/repo`
   schema paths, reject parent/outside paths and absolute source arguments,
   and retain exact observed source agreement. Two real gitlinks must resolve
   their own different pins with only one shared common-directory lookup.
2. Run `make -f scripts/validation_ownership/foundation.mk ownership-probe-test`.
   Positive fixtures use GNU Make include/define/eval, finite domain values,
   patterns, target variables and order-only prerequisites. Their typed
   observations must describe actual targets, not candidate stdout.
   Build CI runs the same complete native suite in its existing required
   `extended-host-tests` worker. The lightweight workflow contract compares
   the public target's actual unittest selection with every native case and
   rejects missing, duplicate, conditional or advisory ownership. The host
   workflow discovery must not execute the native suite a second time.
3. The same suite compiles benign pre-fix `load`/native-SHELL payloads and
   demonstrates actual writes to an explicitly inherited test FD. The confined
   payloads must reject without a forged byte. File/include/eval, supervisor
   paths, device/proc FD paths, alternate SHELL/loaders, shell flags and
   stdout-shaped observations cannot forge channels. Canonical argv preserves
   argument boundaries and equivalent quote spellings.
4. Observe actual admitted open/mmap/stat/glob/directory inputs. Repeat with
   undeclared and dynamically constructed paths, caught exceptions, unused or
   falsely reported source declarations, and symlink/FIFO controls. Every
   mismatch must fail closed. Real C/C++ candidate tools compile and run only
   in channel-free capsules; changed ELF handles and channel/FD access reject.
   Compare complete returned `statx` buffers and reject a corrupted mount ID.
   Different guest namespaces need not have equal mount IDs; do not infer
   equality from an unchanged source inode or a successful serialized retry.
   Namespace and filesystem-capacity records remain in production validation.
   The alias controls create a relative symlink from a deeper cwd and relocate
   a cwd/dirfd ancestor before a `..` lookup. All symlink and rename variants
   must reject before dispatch; they cannot hide an undeclared attempt under
   a recorded `/work` path.
   An owned, namespace-only runtime alias fixture also checks relative and
   absolute trusted symlinks: declared source open/mmap still reports the real
   source, while direct, cwd and directory-FD undeclared accesses reject.
5. Change unrelated docs/source/modes/symlink targets in the disposable fixture:
   execution identity changes while the unrelated owner's semantic digest
   remains stable. Change a real owner input and require changed semantics and
   command bytes; old Git entry IDs must not cause stale output reuse.
6. Exhaust variant, process, pending-request, event, mapping, output, cache,
   scratch-write and file-creation limits. Two individually short real
   processes cannot reset the aggregate deadline. Malformed binary/text
   protocols, worker failure and SIGTERM interruption must reject and leave
   no live owned child, descriptor, mapping, cache or owned scratch tree.
   Creation controls include `O_TMPFILE`, `creat`, directory creation,
   hardlinks/`linkat`, `AT_EMPTY_PATH` and a one-creation limit across commands.
   Supported attempts consume quota before dispatch; symlink, relocation and
   special-file alternatives reject rather than escaping accounting.
7. Run the same suite's mapping controls. A read-only/`PROT_NONE` shared
   anonymous mapping upgraded writable and inherited by a child must reject,
   as must mutable file backing even through a read-only private mmap and
   duplicated/closed descriptors. The benign unconfined control demonstrates
   child-written pathname and `writev` vector bytes reaching real syscalls.
   Writable protection upgrades, remap clones/fixed/DONTUNMAP aliases and
   alternate memory APIs reject. Private COW fork, ordinary private resize,
   read-only source mmap and suspended-parent native spawn remain positive.
8. The bootstrap controls reproduce a nondumpable stopped child and a
   pathname ending at an unmapped page. The old memory reads fail with `EIO`.
   Post-drop observation must work without restoring credentials/capabilities,
   and NUL-terminated boundary strings must read correctly while malformed
   UTF-8 and overlong paths still reject. Every control reaps its own child.
9. Exercise the same suite's lifecycle controls: complete a worker that leaves
   an owned child, close its caller lifetime pipe, exhaust the shared deadline,
   overflow output, and interrupt it. The watchdog must reap its entire owned
   tree before returning. Modeled outer `PermissionError` cannot bypass that
   cleanup. Missing kernel support or a missing/closed lifetime pipe must reject
   before launch. These fixtures use same-UID processes, never real sudo; hold
   any claim of full sudo-route validation until separate exact-candidate
   evidence actually exercises that credential transition.
   The same-UID adapter validates the production launcher contract but runs the
   actual watchdog/payload without `unshare`, including on hosts that deny user
   namespaces. Budget/interruption controls require a payload-start marker and
   the intended error, not an early launcher failure. Real capsule namespace
   execution remains a separate production-path check.
   For #264, also run the focused readiness procedure below: actual child
   pidfd readiness, not a completion polling interval, must wake that same
   watchdog while the caller lifetime pipe remains independently active.
10. Capture Make's parsed ELF interpreter and runtime closure, then run the real
    Make observation with only an Arch-shaped, non-multiarch `/usr/lib` library
    layout. Require the authentic prerequisite/value and read-only captured
    files, even after the fixture's runtime source map is cleared. Mutable or
    non-system aliases, malformed ELF headers and unresolved dependencies
    reject. This controlled layout is not a native Arch host validation claim.
11. Attempt `chmod('/work', 0)`, nested and directory-FD permission removal,
    restrictive directory creation and owner-masking umasks, even while catching
    errors in the candidate. Require the original policy rejection and complete
    owned scratch removal, not a cleanup `PermissionError`. Confirm that safe
    directory permissions, regular-file `fchmod`, and real C/C++ compiler output
    remain supported. Fixtures retain only their own directory FDs to safely
    restore permissions if a regression re-admits the old behavior.
12. Run the same suite's ordinary-Make context comparisons. Conditional
    prerequisites must match real `/usr/bin/make` for `SHELL`/`MAKE` values and
    origins, `.SHELLFLAGS` origin, and dry-run/always-make/job/print flags.
    Compare complete value/origin/flavor records in default, file-shell,
    requested-domain and POSIX contexts. Identical recipe and `$(shell)` argv
    must remain metadata-only and value-bearing respectively; recursive and
    Makefile-remake commands still require actual registered mappings.
13. Reproduce the benign `GNUMAKEFLAGS=--eval=INJECTED=yes` control in the owned
    fixture with ordinary Make; its graph changes. Passing that option channel
    through either probe assignment origin must reject before a Make launch.
    Private dispatch queries, observer-image reads and control-path reads must
    not become a candidate-controlled context or evidence channel.
14. Fail scratch construction after new parents exist: use a tracked leaf,
    overlong component, injected open/mkdir failures, an inaccessible owned
    directory for an unprivileged runner, and interruption. All new parents and
    FDs must be released, while an existing parent/sentinel survives.
    SIGTERM delivered during allocation must wait until ownership is recorded.
    A modeled cleanup failure must preserve the primary setup exception; the
    fixture teardown, not production code, removes that deliberately retained
    test residue.
15. Complete an ordinary budget command while an owned descendant still holds
    its streams, including a descendant that first starts its own session.
    Require prompt termination/reaping and preserved normal exit status.
    Model PID reuse by assigning an already-reaped handle the numeric PID of
    a live test-owned canary: budget, watchdog and tracee cleanup must leave
    that unrelated canary alive. Tracee cleanup uses the old pidfd identity,
    never a start-time check followed by a numeric signal. Binary stdin must
    remain separate from the lifetime channel, with no extra payload FD.
16. Fork small native processes, then grow their stacks using only page faults.
    The old control's eight children touch only 16 MiB total yet hold
    39,317,504 virtual bytes under a 33,554,432-byte aggregate limit.
    Require the new admission to prevent that overspend while a smaller
    supported stack-growth case succeeds. Directly exhaust a funded stopped
    process's virtual credit and continue it without another supervisor
    accounting call: kernel limits must block the stack fault. Verify the sum
    of assigned kernel bounds plus pending fork credit, shared-VM growth, and
    repeated vfork/exec accounting in the owned compiler-role driver.
    Registered native command reexec is deliberately denied, not an exception
    to that startup policy. No RSS, huge allocation, global setting or
    unrelated process is part of these controls.
17. In the selected production namespace route, create only owned, size-limited
    1 MiB tmpfs fixtures with two submount levels. Recursively bind them into
    read-only/noexec and read-only/executable views. Kernel mount flags must
    include nosuid/nodev at every level; writes and disallowed executable
    launches must fail, while explicit executable views work. Preserve a
    source submount's stronger noexec flag and the original source mount flags.
    Verify inherited root submounts are sealed before explicit writable
    work/control and read-only executable helper exceptions. Simulated
    `mount_setattr` unavailability must release its FD and reject before
    supervision, never fall back to a top-only remount.
18. Invoke `probe_generated_registry` repeatedly under one report session.
    An unchanged compatible request must reuse candidate results while spending
    genuine guest metadata-validation costs; a distinct request must exhaust the same creation quota, including
    a creation already performed for Make. Expire the original deadline and
    require even a cached request to reject without another launch. Missing,
    `None`, inactive, foreign-loader and mismatched-budget owners must reject.
    The actual consumer must create one session and use the same budget for
    tree capture, Make and registry discovery; there is no helper scratch-root
    or implicit fresh-owner path.
19. Exercise all five PID-based signal senders (`kill`, `tkill`, `tgkill`,
    `rt_sigqueueinfo`, `rt_tgsigqueueinfo`) against the sender and a forked,
    exclusively test-owned sibling. Block SIGUSR1 and consume it synchronously:
    self delivery must work, sibling delivery must be denied before dispatch.
    Test group/broadcast and mixed thread selectors only through the policy
    function, never by issuing those requests to the kernel. Keep pidfd,
    asynchronous-owner, timer/mqueue and signal-generating ioctl alternatives
    closed using owned pipes, self identities or invalid descriptors.
    Run the complete foundation suite, including both unfunded and funded
    stack-fault branches; the funded case uses only its current child's size.
20. Declare a regular source, make an `O_DIRECTORY` open or metadata/readlink
    operation fail, catch the error and report the declared name anyway.
    Registry equality must reject it. Use real legacy/wide getdents with an
    undersized buffer and a one-entry buffer: credit must match only the actual
    returned names, independent of ordering, never all declared siblings.
    Positive stat/access/open/read/mmap observations must still pass.
    The owned stopped-syscall controls separately cover FD read/pread/readv,
    fstat/fstatfs, seek, mmap, dup/fcntl, close, EOF and partial reads without
    an earlier open masking failure. Test code and Make-path evidence too.
    Reuse stale directory-buffer bytes at EOF, corrupt actual returned records,
    and exceed buffer/observation bounds; none may forge consumption.
21. Create an owned Python prefix containing a harmless `.pth`/`sitecustomize`
    marker. Replay the actual public Make/direct, namespace, watchdog, version
    query and registered-Python launch vectors with that interpreter. Removing
    `-S` must execute the owned hooks; the real `-I -S` vectors must not. Never
    install a hook in a global/user site directory.
22. Send real SIGINT/SIGTERM only to owned test processes during partial setup,
    report/config unlinking, command/Make directory deletion, session removal,
    and process/pipe reaping. Prior Python handlers must observe complete
    cleanup, and an original operation error must remain primary. Multiple
    deferred signals must not disappear because one handler raises.
    Preserve a caller's already-blocked mask, and verify default SIGTERM in an
    owned subprocess takes effect only after its session scratch is removed.
23. Let an unresolved `$(shell ...)` temporarily select another registered
    command, then disappear once the real result is replayed. Repeat with a
    branch reached only after a later expansion resolves. The semantic command
    list must match only the final successful pass. Change the discarded
    command's source, code and output: execution identity may change, but the
    requested owner's digest must not. Changing a final command's actual
    inputs/output must still change its identity. Repeated final events,
    equivalent command aliases and reordered/equivalent source declarations
    must not duplicate records or change the digest. An unregistered command,
    failed source observation or exhausted mapping quota must still reject,
    even when that speculative branch would later disappear.
24. Capture a real fixture Git tree with an explicit report budget. Omit the
    budget from capture, loader or session construction; pass `None`, another
    budget or a detached entries dictionary; attempt foreign-budget snapshot
    materialization. All must reject before launching or writing anything.
    A valid loader/read/snapshot/Make sequence must retain the capture's exact
    budget, original deadline and increasing counters. With a one-launch
    limit, tree capture must prevent any subsequent read, snapshot or session
    launch. Repeated live and immutable reads must exhaust the same byte
    quota. Expire the capture deadline and close a successful session: neither
    may start another authority stage. A second session, even through another
    loader, must reject without disrupting the still-active original owner.
25. Run ordinary GNU Make and the probe with two unique assignments in both
    orders, using each supported origin and both mixed-origin combinations.
    Include a recursive `B=$(A)-two` value. Equivalent native prerequisites,
    values, origins and flavors must produce the same semantic digest.
    Changed values/origins must still change it. Then use `MAKEOVERRIDES` to
    select a prerequisite from command-line order: both the ordinary result
    and native observation must preserve that meaningful difference. Sorting
    executed argv or discarding the order-sensitive observation must fail.
26. Run a registered Python command that writes its initial startup flags to
    an owned `/work/initial` marker. Require `[1,1,1]` for `-I/-S/-B`. Then
    attempt reexec with changed argv/environment, no startup flags, a runtime
    alias, a raw syscall and a forked child. Each must reject at exec entry,
    before replacement startup can create `/work/reexecuted`.
    Compile an owned native control and repeat direct, fork, vfork and
    suspended-parent clone reexec. Its initial launch must still work.
    Path-based and descriptor/empty-path `execveat` stay rejected in both
    controls. Real compiler and Make dispatch positives must remain accepted;
    every negative must remove owned children, channels, cache and scratch.

### Focused watchdog readiness regression (#264)

**Prerequisites and reset:** use the supported source checkout and existing
Linux pidfd/Python signal APIs with readable private proc child/FD state.
The measured environment is Linux x86-64, GNU Make 4.3 and Python 3.12; this
fix adds no host requirement. These same-UID lifecycle fixtures need neither
namespace privileges nor a ROM. Start without another child in each isolated
watchdog process. All fixtures use owned pipes/processes and ignored
`build/test-artifacts`; the test harness reaps its processes and removes only
its fixture roots. Preserve failing logs before rerunning; never kill by
process name or remove another checkout's files.

1. Run the focused command below. The readiness case starts `/usr/bin/true`,
   real pipe-gated children returning 0 and 7, and a child exiting by SIGUSR1.
   Keep the caller lifetime writer open and use a different pipe for payload
   input. Check that the registered pidfd identifies the owned child in kernel
   FD metadata, is non-inheritable, and produces `EVENT_READ` when that child
   exits. A controlled clock supplies a five-second deadline remainder to the
   real selector; this is an argument/FD-event assertion, not a wall-time
   threshold. Two `WNOWAIT` observations must still see the same leader before
   sole-reaper teardown; return statuses must be 0, 7 and `-SIGUSR1` as applicable.
2. The same case removes only the child pidfd's selector registration in an
   isolated negative run. The still-live lifetime pipe cannot supply child
   completion readiness, so the readiness contract must fail, while owned
   cleanup still succeeds. Restoring the old 50 ms completion tick must also
   fail the deadline-argument assertion. No production bypass or faster
   polling interval is an accepted alternative.
3. Inject pidfd acquisition exhaustion and unsupported-kernel errors after a
   real launch, plus selector registration failure. Deliver SIGHUP, SIGINT
   and SIGTERM during acquisition and registration. Combine setup failure
   with a pending signal: the original exception must remain primary and the
   interruption must remain diagnostic evidence. No path may leave an owned
   child, pidfd or selector, change caller handlers/masks, or silently retry.
4. Inject a reported close or terminate failure **after real release**, with
   and without an existing operation error. Include selector close, and send
   a real signal during teardown. Every remaining close/reap must run before
   the restored caller handler observes the resource state; primary errors
   retain precedence and cleanup/signal diagnostics remain attached. These
   deliberate post-release error controls do not claim that an OS-denied
   termination succeeded.
5. Keep the coupled controls: independent lifetime EOF, the actual aggregate
   deadline, signal teardown, orphaned and escaped descendants, stale-PID
   canaries, binary stdin and no inherited payload FD. Missing pidfd/kernel
   or lifetime support must reject before launch. The privileged-route adapter
   runs the real watchdog without invoking sudo; it is not a credential-
   transition proof. All tests must pass together.

```sh
python3 -m unittest \
  scripts.validation_ownership.tests.test_foundation.FoundationTests.test_watchdog_child_readiness_uses_the_absolute_deadline \
  scripts.validation_ownership.tests.test_foundation.FoundationTests.test_watchdog_pidfd_setup_failures_and_signals_release_ownership \
  scripts.validation_ownership.tests.test_foundation.FoundationTests.test_watchdog_pidfd_cleanup_errors_preserve_primary_and_deferred_signals \
  scripts.validation_ownership.tests.test_foundation.FoundationTests.test_watchdog_teardown_delivers_signal_after_sole_reaping \
  scripts.validation_ownership.tests.test_foundation.FoundationTests.test_watchdog_payload_stdin_is_binary_and_separate_from_lifetime \
  scripts.validation_ownership.tests.test_foundation.FoundationTests.test_missing_pidfd_support_rejects_before_payload_or_tracee_launch \
  scripts.validation_ownership.tests.test_foundation.FoundationTests.test_watchdog_rejects_missing_lifetime_and_kernel_support_before_launch \
  scripts.validation_ownership.tests.test_foundation.FoundationTests.test_watchdog_reaps_owned_orphans_on_completion_eof_deadline_and_signal \
  scripts.validation_ownership.tests.test_foundation.FoundationTests.test_reaped_process_handles_never_signal_an_unrelated_owned_canary \
  scripts.validation_ownership.tests.test_foundation.FoundationTests.test_tracee_cleanup_uses_pinned_identity_after_numeric_pid_reuse \
  scripts.validation_ownership.tests.test_foundation.FoundationTests.test_budget_normal_completion_reaps_group_and_escaped_descendants \
  scripts.validation_ownership.tests.test_foundation.FoundationTests.test_budget_launch_interruption_waits_for_handle_ownership \
  scripts.validation_ownership.tests.test_foundation.FoundationTests.test_privileged_budget_uses_real_watchdog_without_running_sudo \
  scripts.validation_ownership.tests.test_foundation.FoundationTests.test_privileged_lifetime_closes_on_budget_rejection_and_interruption \
  scripts.validation_ownership.tests.test_foundation.FoundationTests.test_privileged_cleanup_delegates_before_wait_and_never_hides_permission_errors \
  scripts.validation_ownership.tests.test_foundation.FoundationTests.test_cleanup_preserves_a_callers_already_blocked_signal_mask \
  scripts.validation_ownership.tests.test_foundation.FoundationTests.test_budget_payload_does_not_inherit_the_temporary_setup_mask \
  scripts.validation_ownership.tests.test_foundation.FoundationTests.test_budget_cleanup_defers_signal_until_children_and_pipes_are_closed -v
```

**Expected and pre-fix results:** real child readiness and the original absolute
deadline replace periodic completion polling, with unchanged lifetime and sole-
reaper semantics. The pre-fix watchdog has no child readiness FD in its selector,
so the permanent actual-FD regression fails; a registration-only removal fails
the same contract. Isolated before/after runs of 20 short commands can support
the performance diagnosis, but no exact runtime threshold belongs in this case.

**Interactions and limitations:** #264 is an independent foundation fix used by
#180; it does not prove the full graph, extraction #196, whole-CI runtime or a
real sudo transition. Other game/profile conflicts are none. No resource limit,
counter, permission, namespace, protocol or authority changes; no save migration,
generated game data, locale, ROM/RAM, modern debug/release or archival change.
All assertions are deterministic host automation; no manual-only criterion or
human approval applies. A normal revert of the dedicated fix is the rollback.

### Expected result

The real consumer reports the native localization prerequisite and exactly the
declared chapterbundle source. Candidate programs cannot forge observation
channels or hide undeclared source access; unsupported aliases, shared mutable
memory and resource exhaustion reject before unsafe work proceeds. Immutable
source mmap, private COW fork and valid boundary pathnames remain supported.
Execution snapshots change independently of unrelated semantic owner identity,
and every failure clears owned processes, channels, mappings and caches.

### Negative control

The pre-fix native Make processes really forge the inherited test channel;
unconfined source functions really read/stat/enumerate undeclared fixture
paths; a per-process timeout really admits work beyond a single total budget.
The #264 pre-fix selector contains only the caller lifetime pipe, so a real
child exit cannot wake it; the new real-FD and removal controls pin that defect
without using a fragile elapsed-time threshold.
These controls are restricted to disposable test inputs and are not a
production bypass switch. Normal positive behavior is tested alongside every
boundary; no failure is converted to successful evidence.

The original #206 guard additionally admitted relative symlink and relocated
cwd/dirfd aliases, shared mapping upgrades and private mappings of mutable
backing files; it reported zero creations for `O_TMPFILE` and omitted hardlink
entries. The frozen process regressions fail against that guard. Their benign
unconfined mapping controls use only owned buffers, fixture paths and reaped
child PIDs; no timed race against unrelated host data or process is required.

The prior sudo caller's unprivileged `killpg` raised `PermissionError` before
reaping; the lifecycle contract reproduces that failure without acquiring
privileges. The prior Make root attempted a nonexistent Debian multiarch libc
on a non-multiarch layout. A real confined `chmod('/work', 0)` followed by a
failing command formerly masked the original error with cleanup
`PermissionError` and left an inaccessible owned directory. The new regressions
preserve these negative controls while limiting all effects to owned fixtures.

Before the normal-context correction, benign ordinary Make selected `genuine`
while the probe selected `hidden` for seven independent flag/value/origin
conditions. `GNUMAKEFLAGS --eval` also injected an unrequested definition.
Partial setup rejected a tracked leaf but left newly created parents behind
because ownership had not reached the session. These are behavioral negative
controls, not source-spelling checks.

The previous budget reaped its leader before calling `killpg`, and the owned
PID-reuse model actually killed its unrelated test-owned canary. The previous
memory guard only checked selected syscalls; held post-fork stacks exceeded
the aggregate virtual limit without another checked allocation. The controls
use exclusively owned process identities, disabled core dumps for the deliberate
stack-fault case, and small bounded memory.

The previous recursive bind reported restricted top-level flags `4111` but
left a copied tmpfs submount at `4096`: writes and executable launches through
that supposedly read-only/noexec child really succeeded. The regression uses
only private namespaces and bounded owned mounts, which disappear on exit.

The prior optional helper succeeded twice by creating two independent sessions:
each created one file while the active caller's one-file quota and process
counter stayed unchanged. This is the per-registry multiplication the explicit
report-session API rejects; sharing only a budget while resetting session-owned
counters is not equivalent.

The prior unconditional `rt_sigqueueinfo` allowance really delivered SIGUSR1
and its queued value to an owned sibling. The old funded-stack fixture also
raised `NameError` in both branches before checking the kernel bound because
it referenced another test's `first`/`second` locals. Both failures are retained
as negative controls; partial selections cannot stand in for running these paths.

Before exit-time credit, an `O_DIRECTORY` open returning `ENOTDIR` still made a
declared regular file count as consumed. A getdents call returning `-1`, or just
one 24-byte entry, credited both declared siblings and accepted forged registry
JSON. The new regressions preserve those actual syscall failures/partial results,
while legitimate successful metadata and content observations remain positive.

The earlier `-I -B` public invocation executed an owned prefix's `.pth` hook.
The earlier teardown restored the caller handler before `rmtree`; a real
SIGTERM then saw live scratch, interrupted its removal and masked an existing
failure. The new executable startup and owned-signal controls retain those
negative cases without changing global site or kernel policy.

The earlier replay accumulator retained a discarded branch's command even
though final native events contained only the selected command. Changing the
discarded source/output or code changed the owner's digest while ordinary Make
and the final prerequisite graph stayed identical. Real conditional Make
fixtures preserve this control; final-pass filtering cannot excuse failed
source accounting or speculative work that exceeds aggregate bounds.

The earlier reusable API spent a capture budget's single launch, discarded its
identity at the loader and accepted a real command under a fresh session
budget. Equivalent reordered environment/command-line assignment contexts
also produced different digests despite matching ordinary Make observations.
The new order control retains the genuinely different `MAKEOVERRIDES` graph.
The earlier path-only exec guard let a registered Python command replace
itself with only `-S` and write an owned marker with flags `[0,1,0]`. Omitting
all isolation flags reached site-startup metadata before rejecting for an
unrelated path violation. The corrected guard must reject the exec itself,
not rely on that later error or an absent marker alone.

The exact `bc136` consumer reproduction compiled the actual repository scaninc
C++ sources and returned `proof.bin` from `proof.s` with native source evidence,
but Make could not register that issued tool. Its generated-include producer
correctly could not write `/repo`; redirecting to private `/work` returned zero
but discarded `generated.mk`. Ordinary Make selected `observed`, while the
old probe returned an empty prerequisite list, undefined `SELECTED`, and only
`Makefile` in `MAKEFILE_LIST`. Returning zero or precreating the file before a
fresh pass is not an acceptable substitute for the actual native context.

### Captured gitlinks and admitted live inputs

1. Request an existing immutable `160000` entry through typed
   `GitlinkSource(path, git_dir)`. Require its exact pinned bytes, original
   prefixed guest paths and owner identity, not a moving checkout. Wrong,
   unavailable/non-commit pins, unrequested links, unsafe databases and
   symlink/nonregular/nested source namespaces reject.
2. An exact empty pin is a real directory. An undeclared root listing rejects;
   an explicitly declared listing sees both the Makefile and the admitted
   module name. The same names-only declaration cannot read member content.
3. For live mode, freeze HEAD/default or explicitly supplied path admission.
   Change an admitted file's bytes and executable mode, delete another, and
   create staged/untracked/ignored synthetic extras. Only admitted live bytes,
   mode and genuine absence enter the view; extras gain no Make-readable
   content. Reject unsupported type changes without pretending they are files.
4. Preserve an actually empty live gitlink directory. Reject nonempty
   initialized contents without explicit source-path admission. Do not walk
   them because they are nonignored or materialize an invented empty subtree.
   Explicit immutable pin admission and explicit live path admission remain
   distinct authorities.

CURRENT/BASE switching belongs to #226, optional runtime inputs to #227, and
the complete generated/dependency root adoption to #225/#228/#180. Their
original procedures and controls remain allocated against the unapproved
reference, not silently removed or represented as current core APIs.

### Live process capacity and total work

`Limits.processes=32` is the simultaneous live traced guest capacity, including
root, newborn/unresolved and vfork states. `Limits.descendants=16384` is total
actual process creation for the entire core report across capsules/retries.
The former extra 32-total-per-capsule restriction is withdrawn explicitly; all
numeric, deadline, run/state/syscall/memory/byte/source/execution/cleanup limits
stay unchanged.

Run the existing suite's `test_process_*` methods:

1. Execute forty sequential actual Make shell expansions. Require all native
   values/events, total creation greater than 32 and live peak within 32.
2. Hold children live under a small live cap with ample total allowance.
   Reject the next creation before excess child progress; verify actual totals,
   live peak, closed budget and owned teardown.
3. Reap children sequentially under a small total allowance. Reject cumulative
   exhaustion while live usage remains low. Spend additional work across
   command caching, metadata validation and Make replay without reset/refund.
4. Exercise reserved/newborn-first/normal process states without double credit,
   failed clone3/fork returns, actual native/compiler vfork/exec, root failure,
   normal exit and interruption cleanup. Total creation includes real failures;
   live peak comes from tracked state, not the configured cap. Memory credits
   are not RSS.
5. Preserve the historical child root-process evidence in the allocated
   integration work. It is not a current core full-root or 112-domain result,
   nor permission for a new limit or authority expansion.

### Bounded serial resolution of observed command batches

Run the `test_serial_resolution_*` and
`test_pending_request_bytes_accumulate_after_completed_commands` selectors:

1. Execute an ordinary Make batch with more than 32 distinct real, source-reading
   producers, repeated entries and ordered spaced/empty arguments. Repeat
   through the public session with a one-item pending limit. Require identical
   actual values/prerequisites, first-occurrence execution order, one execution
   per repeated command, and final matched replay. The measured active peak
   must be one, with the initial and final Make passes only; do not add a new
   pass per resolution window or increase `MAX_DYNAMIC_PASSES`.
2. Corrupt a late native frame, hash, mapping count or claimed protected-map
   match after actual Make capture. The complete stream/map contract must
   reject before any new producer lookup/execution. A later replay that misses
   a known mapping must not rerun a completed producer.
3. Resolve an actual nested registration through another Make query. A lowered
   pending-depth limit must reject before excess work, while a sufficient
   existing limit permits it. Observe the actual current/peak counts and their
   restoration, not the configured maximum reported as a measurement.
4. Fail a real producer, interrupt it after actual output, and overflow its
   stream bound. Preserve prior completed work's charges, stop later
   resolutions and clean all owned state. Existing mapping/event/cache,
   source/view/final-pass and generated-output/restart controls still apply.
5. Complete a real argument-consuming command with no outstanding child,
   then submit another distinct command under a lowered pending-byte limit.
   The existing lifetime traffic charge must accumulate and reject rather
   than refunding completed work. Keep the default byte maximum unchanged.
6. Run the latest frozen root with its real adapters and this parent package.
   Retain actual resolution counts/peak, byte categories, final native outcome
   or next precise bound. Observed command names are not completed producers,
   and no subset is full graph/112-domain acceptance.

Before this correction, exact `cd21586` builds the entire unresolved backlog
and rejects it against the pending count before serial execution begins.
The replacement streams fully validated observations through bounded
resolutions using the existing charged completed map for deduplication. The
extra whole-backlog ceiling is explicitly removed; pending/fanout safety,
lifetime byte charges and every other numerical bound remain enforced.

### Aggregate attempted observations and absent compiler metadata

The same case's `test_observation_*`, `test_failed_observations_*` and
`test_compile_executable_metadata_*` controls freeze the two existing guard
corrections:

1. Set the existing observation-record limit to three in a direct guard
   fixture. Admit one record in each of `consumed`, `code_consumed` and
   `accessed`, then repeat them without another record/byte charge. A fourth
   record in any collection must reject **before insertion**, even though that
   individual collection has room. With ample records, independently exhaust
   the observation-byte limit and require the unchanged fail-closed error.
2. Defer failed observations in all three collections. The attempts must spend
   bookkeeping but grant no consumption. A successful retry reuses the charge;
   a later failure does not erase successful evidence. A new failed attempt at
   the aggregate count limit rejects before entering deferred state. Retain
   the neighboring real syscall successful/failed source/FD controls.
3. Permit only compile-mode metadata of exact `/proc/self/exe`. Run an owned
   native probe in the existing test compiler-role capsule and require actual
   `ENOENT` from stat, lstat, access and readlink. There is no proc mount or link
   materialization; do not interpret this as resolving the host executable.
   Run the same probe as a normal command and require rejection.
4. Other command/Make modes, read/write/exec, unknown/proc FD paths and nearby
   process/sys/device metadata remain denied. Distinguish pre-observer Make's
   exact runtime guard from the ready-phase namespace guard, and exercise every
   intended operation/path variant with its actual positive counterpart.
   Native/compiler/process/source neighbors stay in core; optional runtime and
   dependency/output interactions remain allocated to their owning layers.
5. Run `test_observation_total_*`, `test_observation_totals_*` and
   `test_observation_remaining_*` through public session operations. Repeated
   metadata/open/read of the same input deduplicates within a capsule. Real
   commands must reach an exact report-wide limit. Reused candidate results
   still charge actual metadata revalidation, and a fresh capsule must reject
   when no allowance remains.
   If a positive allowance remains, the next capsule must enforce that smaller
   count before inserting its next record.
6. Account real compiler, native, command, static Make and metadata-validation
   work in that same session. V owns selected-view continuation, and P/D own
   generated/dependency interactions. Failed deferred probes spend records
   without successful consumption; process/source failures preserve the total
   and make the entire lifetime unusable. The captured-source entry limit stays
   independent; no numerical limit is raised.
7. Run `test_observation_accounting_*`. Require the new count in the existing
   closed supervisor result, rejecting missing, nonintegral, boolean, negative,
   excessive or inconsistent values. Candidate stdout cannot replace that
   trusted count. An actual source-free capsule may report zero; malformed
   accounting may not be silently converted to zero. Cache/failure cleanup
   must retain the same terminal report lifetime.

Before the capsule-local correction, exact `0b4cc7d` admits the fourth
cross-collection record at a three-record aggregate bound and rejects the compile metadata
probe before the kernel can return its real absence. These are regression
controls, not new budgets, APIs or authority. All numerical limits and one
report lifetime remain unchanged. Exact `e403445` still admits successive real
capsules whose combined observations exceed that report's configured count.
The report-wide correction passes the remaining allowance to each capsule and
accumulates its measured attempted records; it does not merely repeat the
local-guard control. The actual child root/112-domain adoption is independent
and is not rerun or claimed by this bounded correction.

### Static metadata, admission and retained safety

1. Run the static metadata fixture with no directory-enumeration declaration.
   It queries regular files and implicit code/source ancestors. Compare the
   actual inode with the persistent source view, all recorded stat fields,
   and the final native Make value. A cache hit must reuse the genuine result
   while charging the real metadata-validation process and observations.
2. Capture complete stat/lstat/fstat/newfstatat/statx/fstatfs and access/readlink
   buffers, return status, flags and masks through real syscalls. Include
   caller-initialized buffers, read-only access failure and regular-file
   readlink failure. The actual returned bytes must equal the operation records,
   not a selected list of convenient fields or a source fingerprint.
3. Change actual owned source metadata. Require cache invalidation and real
   execution, then truthful unchanged reuse and final Make values. Compare
   source stat before/after revalidation to prove it does not update atime or
   other fields through a writable alias.
4. `fstatfs` includes shared capacity. A real owned filesystem allocation must
   invalidate that complete record; the command must execute genuinely again.
   The core does not assume a coincidentally equal mount ID or free-block value
   is universally stable, nor filter this operation out of production reuse.
5. An invalid metadata buffer must return its real kernel error without
   successful source credit. It may execute directly, but unsupported reuse
   must reject in Make with a precise cause. Malformed ABI sizes, paths,
   flags, buffers, status and native-event reports reject before unsafe use.
6. Retain explicit directory type/listing checks and no member-content grant,
   actual complete backing, initializer/module/cache absence and nonregular
   namespace controls. Source/code ancestors get metadata, not implicit
   enumeration. True absence is evaluated in the complete admitted view.
7. Run both real immutable and repaired live localization/chapterbundle
   consumers. Use an owned changed live fixture to prove live bytes, not HEAD
   substitution. Keep HEAD/staged/untracked/deletion/mode/type/gitlink controls
   alongside the source guard, not as independent text-only checks.
8. Preserve the actual deep owned cleanup, symlink-target, replaced-entry,
   permission, signal and schema-count controls. The structured autoplay and
   sequence-backed registry counts use `manifest_record_count`, with exact
   declared/consumed/reported paths and no fallback for incomplete schemas.
9. Exercise the complete compiler/proc family both before and after Make's
   authenticated observer-ready boundary. Earlier exact runtime denial of the
   attempted operation is causal evidence; an unrelated startup failure is not.
   Pair private channel/image/library-discovery denials with actual legitimate
   loader and static registered-dispatch positives. Metadata validation roles
   cannot be acquired by a candidate command or forged marker.

The preserved d9 static sparse control returned another inode even with no
generation. Its source-ancestor nlink and explicit-directory timestamp examples
also produced stale all-matched results. Those are behavioral negatives.
Generated reconstruction changes inode/ctime/timestamps and remains a held
#225 requirement, not an implemented static-core success.

The complete unapproved reference and every original selector/procedure
allocation are preserved as
[historical delivery evidence](https://github.com/laqieer/fireemblem8-expansion/blob/56e0a206ffae088b0dbc1fe8aa6339a8ee820f33/docs/ownership-probe-allocation.json),
not a current runtime or validation authority.
Between live admission and snapshot construction, an empty gitlink becoming
absent must remain absent in the materialized view and actual Make result.
A newly appearing, replaced non-directory or nonempty unadmitted namespace
must reject rather than become a fabricated empty directory.
Core, P/V/R/D and #180 retain their assigned positive/adversarial requirements;
no optional API is silently removed or claimed delivered.

### Interactions and save compatibility

This host-only contract changes no save, migration, config identity, generated
game content, localization, modern/archival behavior or ROM/RAM. Other feature
interactions: none. The authority and existing generated-registry schema stay
shared; PR #186 / #180 must still perform their downstream graph adoption.

### Automation

`python3 -m unittest scripts.validation_ownership.tests.test_foundation -v`
executes the real process scenarios, native compile/link checks, parsed ELF and
binary/JSON protocols, exact source sets, semantic identity and cleanup.
The standalone Make check is a separate real consumer, not a test-name alias.
No subjective/manual-only criterion applies.

### Cleanup and limitations

Fixtures and session channels are removed automatically. Remove the empty
`build/test-artifacts/ownership-foundation-tests` parent if desired. No remote
state is read or changed.

The introducing root does not claim PR #186's full graph, domain matrix, oracle,
lifecycle or absent-on-master `validation-ownership-check` target. Those remain
explicit downstream integration gates under #180. Unsupported native Make
ABIs/platforms fail rather than running a weaker probe. Roll back by reverting
this dedicated foundation; broader validation remains required.

## TC-WORKFLOW-PROBE-PRODUCER-001: Preserve live producer context and native remakes

The [namespace-image CI extension](#namespace-image-ci-regression-correction)
retains the original live-publication listing assertions and adds unchanged
cache identity across direct namespace capture without any Make phase.

### Feature and configuration

Issue [#225](https://github.com/laqieer/fireemblem8-expansion/issues/225);
the supported Linux x86-64/GNU Make 4.3 foundation host, Python and existing
static-capable C/C++ toolchain, libpng/pkg-config and ARM binutils for the real
graphics/linker controls. The existing extended worker installs those dependencies.
Start from a clean source checkout. All inputs,
sentinels and channels are owned fixtures under ignored `build/test-artifacts`.
No ROM, emulator, credentials, remote workflow or new feature flag is needed.
See [live producers](../ownership-probe-producers.md).

### Actions

1. Run `python3 -m unittest scripts.validation_ownership.tests.test_producer -v`.
   The live include fixture compares ordinary Make's selected prerequisites,
   `MAKEFILE_LIST`, value/origin/flavor and native `MAKE_RESTARTS`. It starts
   exactly one Make capsule, executes an isolated producer, publishes its
   declared include/data and invokes a later metadata reader.
2. Require the reader's inode, link count, mtime and ctime to equal the actual
   still-live source view before cleanup. Prime an older reader result first:
   publication must force valid current observation, not a stale cache/map.
   Repeat with two generated includes and require two genuine native restarts.
   Replace a declared generated input between two readers that use only
   `open`/`read`, without stat on that input. Require new source bytes to
   invalidate reuse, while an intervening unchanged read reuses its real result.
   Repeat for explicitly admitted generated code, a mode-only change and
   changed glob membership. Require each result's recorded inputs and stdout
   to belong to the same execution, never a new input hash attached to stale
   output. After publication cleanup, generated code must lose admission before
   another execution or cache return.
3. Inspect actual nested launch configurations and complete supervisor reports.
   Parked Make/helper processes and every funded virtual-memory credit must
   remain reserved. Nested process/VM limits plus reservations equal the one
   global allowance; summed actual report counters equal session totals, not
   counters charged twice at intermediate and final settlement.
4. Use a producer that succeeds alone but cannot fit alongside parked Make's
   funded VM. Require the residual-memory failure after reaching the producer
   request. Separately exhaust residual live-process and total-creation
   capacity before another guest launches. Never accept an unrelated earlier
   bootstrap failure as this control.
   Keep the lowered view fixture at two live processes and nine descendants.
   Genuinely execute a pure result before Make, reuse it in two native queries,
   then select BASE and reach the original cumulative exhaustion/cleanup
   boundary. Require the cold-producer counterpart to reject because both
   live slots are already reserved; no parked process is discounted.
5. Corrupt request scope, sequence, completion frontier, frame hash/count or
   length. Reject before producer execution. Corrupt replies and deliver a
   duplicate later request: earlier actual effects stay charged, no possibly
   effectful request is retried, and no partial transcript succeeds.
   Let real Make continue after its last accepted reply, then separately send
   a duplicate, partial, stale, foreign or unknown message. All reject. Repeat
   immediately before the final write-half shutdown to cover the terminal EOF
   barrier, with a normal no-extra-message counterpart.
6. Kill only the test-owned parked helper through its pinned pidfd. Close only
   the owned outer lifetime during a started nested producer. Require terminal
   failure, no unconfirmed publication and complete cleanup of both lifetimes.
   The candidate starts with only standard descriptors; attempts to use a
   callback/private descriptor, read controls, write source or forge the
   producer marker must fail after the intended payload-start evidence.
7. Reject missing/extra/nonregular/escaping/oversized output, unused declared
   source, and output collision with every admitted path/pin, including an
   actually absent admitted file. Retain normalized same-producer ownership;
   different producers cannot replace each other's result.
8. Invoke an output-producing registration twice, including an equivalent
   alias. Require two real executions/publication effects while equivalent
   provenance deduplicates. Run real repository scaninc through an issued
   native tool and require actual `proof.bin` prerequisites and source/tool
   provenance without mounting the native executable into Make.
9. Compare an ordinary-Make branch whose real first result excludes another
   producer. The excluded producer must never execute. Do not manufacture the
   old speculative empty-output pass to populate its cache. Keep separate
   actual-dispatch malformed/source/budget negatives and completed-transcript
   corruption controls.
10. Where existing sudo policy and user namespaces allow the same-UID control, run both a static
    Make query and a real include/remake through actual sudo and the watchdog,
    with no inherited callback descriptor or closefrom override. Compare with
    the direct route; both retain one restart, exact inputs and standard-only
    guest descriptors. This measures real descriptor closing, not a root
    credential transition. If user namespaces are unavailable, require one
    whole-comparison skip and no empty-result indexing error. Where the
    existing privileged namespace route works, run the separate real
    `test_real_privileged_fallback_preserves_live_results_credentials_and_accounting`
    control. Require actual root supervisor peer credentials, dropped non-root
    guest identities, one real restart, closed guest descriptors and summed
    capsule counters. If user namespaces work locally, only their preliminary
    route-selection result is modeled as denied; the downstream sudo/watchdog/
    namespace/capsule execution remains real. Report unavailable sudo or
    namespace permission explicitly, without changing host policy.
    A watchdog status 125/`EBADF` or an unexpected launcher fault must fail,
    not become an optional permission skip.
    Reject a driver-UID foreign connection at the credential boundary on the
    privileged route and at ancestry on the direct route. Independently pair
    a real same-UID owned connector with wrong-credential and different-live-
    launch controls. Retain replaced/nonprivate directory or socket and wrong
    listener-credential rejections.
    The long-path rendezvous must stay inside owned ignored storage and leave
    no socket after either outcome.
11. Resolve a real nested Make query while its parent is parked. Compare the
    ordinary compound's before/value/after results (`2`, `observed`, `3`),
    require the child's genuine include restart, then let the parent consume
    the still-present child output. Preserve all 13 file-stat fields across a
    child return and readonly ownership adoption. Same-producer replacement
    works in both directions; different producers reject. Corrupt the protected
    transfer checksum, content binding, mode, duplicate entry or reserved/missing
    path and require failure without a second execution. Generated Makefile
    entry points lose admission when the outermost query cleans up.
12. Exercise the complete generated-result family: parse-time includes have no
    fabricated restart, remade includes preserve assignment argv order and
    value/origin/flavor, native outputs use the same capture/publication path,
    aliases retain binary bytes/modes and real effects, and ownership transfer
    changes only newly created objects. Require the real publisher's recorded
    denial when publication exhausts the global write budget. Repeat nested
    work with insufficient residual process capacity, outer lifetime EOF and
    SIGTERM after actual child publication; every owned output must disappear.
    In the two-job control, deliver actual kernel stops so a vfork parent is
    waiting in clone/clone3 while its child is stopped before exec. Both
    requests must complete under the unchanged deadline, with all three
    parked processes and their funded VM still reserved.
    The fixture must release its withheld exec stop after delivering the
    ordered producer-ready event at the next native supervisor poll, not by
    an immediate helper-name match. Removing only the native vfork unsettled
    exemption must still deadlock after that stop is delivered; retain full
    owned cleanup and do not increase the fixture deadline.
13. Keep the dispatch boundary explicit. A missing direct native executable
    must fail before consulting registrations; do not add a placeholder or
    writable/executable source mount to make it appear present. Run the real
    original linker dependency expression: its successful source stat followed
    by noexec `X_OK`/`EACCES` must reject, not authorize empty `INPUTS`. The
    permission-class controls use real producer `fchmod`: owned `0644`, `0641`,
    `0650`, `0601` and `0610` remain ordinary nonexecutability, whereas owner-
    executable modes still reject under noexec. Check real versus filesystem
    credential selection, supplementary-group/other precedence and bounded
    rejection of unsupported capability/identity/ACL states. The exact old
    any-execute-bit guard must fail these controls when restored as a mutation.
    No successful access result or file mode is forced by the probe. The
    source-authored explicit Python form returns the same real dependency
    bytes. Exercise all nine related graphics rules with the actual gbagfx:
    original/adapted argv, valid output bytes, output-path failures and cleanup
    agree. Their probe observations are metadata-only, not generated graphics.
    Where the existing ARM binutils are available, also compare the real
    linker recipe's tiny ARM output/symbol pair and failed-publish behavior.
14. Run the two original V/P combinations against the merged view API:
    `test_immutable_views_isolate_cache_native_files_and_generated_make_outputs`
    and `test_explicit_enumeration_tracks_selected_and_generated_views_without_extra_reads`
    in `ProducerTests`. CURRENT and BASE must use their actual captured inputs,
    separate caches/native handles and generated files, then restore CURRENT
    under the same deadline and cumulative counters. A view switch during an
    active publication remains invalid even after its native process exits.
15. Explicitly request runtime inputs alongside a live include producer.
    After the actual restart, admitted runtime metadata must remain observable,
    while an optional parent-spelling violation rejects. An ordinary env
    recipe must not execute its sentinel. Retain runtime identity in the Make
    execution digest, and repeat nested publication and owner-permission
    controls with the captured runtime backing. Both source and runtime state
    must clean on success and failure.
16. A static query without registrations or inherited outputs retains its live
    handshake but has no publication authority. Require `reserved_paths: null`
    in its parsed private configuration and require the real publisher to
    reject a generated mapping before creating output. Registered and inherited
    scopes still reserve the complete original authority. Run the unchanged
    full-tree CURRENT/BASE query pair: both complete registry executions and
    restored CURRENT Make must fit the original default budget, with every
    metadata buffer and observation charge retained. Do not replace that
    workload with a smaller fixture, third registry query or raised limit.

### Expected result

Real native requests alone cause producer work. The producer remains isolated,
its complete successful source/output contract is checked, and validated files
publish before the requesting helper returns. Native Make owns include loading
and re-exec. Observed source objects are not reconstructed between observation
and use. Child publications remain in that same view until outer completion;
ownership adoption reads only through the readonly mount. All resource/lifetime
limits remain cumulative and unchanged.

### Negative control

The unapproved d9 replay physically recreated generated views: retained actual
source-ancestor link-count and explicit-directory timestamp controls produced
stale all-matched results. Separate owned reconstruction evidence showed
different generated inodes/ctime. Those failures are not replaced by a
names-only hash or a synthetic restart count.

The reviewed live checkpoint also had three actual controls: generated-source
replacement returned `[1,1]` where ordinary Make returned `[1,2]`; real
same-UID sudo closed the inherited callback descriptor and the watchdog exited
125 with `EBADF`; a separately sent final reply succeeded after Make had
continued. The retained failures are complemented by actual generated-code,
mode/membership and terminal-handshake controls, not weaker error matching.

The earlier vertical implementation rejected a genuinely dispatched nested
producer even though ordinary Make completed the same producer/reader
composition. Keeping child files until outer completion fixes that boundary
without recreating a source context. The real original gbagfx recipe failed
before dispatch; the direct linker lookup was worse, returning a successful
empty value. The required source-authored adaptations and narrow denial guard
replace those failures without weakening source noexec or runtime closure.
Run the real linker lookup and MakeCommands linker-adapter controls with their
unchanged script, expression and linker inputs. Read full `INPUTS` from the
recipe-less `measure-inputs` target and compare every byte with ordinary Make's
print result, removing only its one terminating newline. The exact-d9 result
contains 1,475 paths and 56,561 producer bytes; its flattened value is 56,560
bytes. The original direct noexec lookup must still reject before registration.
Separately select the print target in the confined session: its oversized
argument must reject at the unchanged native string bound. Preserve cleanup
and original rejection instead of truncating output or enlarging a cap.

The first integrated CI also exposed a stale two-slot cold-producer fixture,
unused full-tree publication inventory exhausting the complete pair's control
budget, a ready-file creation/write race, an unavailable same-UID comparison
indexing no results and an earlier real credential rejection on the sudo route.
Preserve those failures. Their corrections use genuine prior-result reuse,
an explicitly absent publication grant, a completed-value readiness marker,
whole-comparison prerequisite handling and separate credential/ancestry controls,
not cap or expected-value changes.

Private `/work` output is deliberately not immediately visible through readonly
`/repo` inside a producer. The measured same-code source/output namespace
difference remains a documented two-phase boundary, not permission to expose
writable source aliases. Required unsupported read-own-publication behavior
remains a precise hold.

### Interactions and save compatibility

P depends on core #206 and composes with the independently merged V/#226 and
R/#227 layers. D/#228 depends on P, with no new V/R dependency.
#180 retains full integration/domain/graph/oracle/public
acceptance. No gameplay, saves, config identity, localization, generated game
content, modern/archival profile or ROM/RAM behavior changes.

### Automation

`python3 -m unittest scripts.validation_ownership.tests.test_producer -v`
executes the real producer/control/resource cases through the existing host
runner. The existing `ownership-probe-test` target selects foundation, producer
and dependency modules exactly once in `extended-host-tests`. Lightweight
workflow discovery checks that selection with `PlanCollector`; it does not
import native test classes for execution in another job. The actual adaptive
job condition selects all three modules only in full mode and skips them for
metadata-only and review-first preflight runs. No new job or standalone gate
is added.
Its dependency step supplies libpng, pkg-config and ARM binutils before native
execution. Parsed owner/dependency controls reject missing, disabled, masked or
late installation without duplicating the process suite.

### Cleanup and limitations

All owned source fixtures, captured outputs, channels, roots and children are
removed on success or failure. No process-name killing or other-worktree
cleanup occurs. Implementation evidence is not full P or root/112-domain
acceptance or budget calibration. The actual same-UID sudo control does not
claim a root credential transition. Only a completed separate real privileged
control is evidence for that route; a missing existing sudo/namespace
permission is an explicit unsupported outcome. No sudo policy changes occur.
Nested queries share the active generated view. Both originally allocated V/P
combinations now run against the real merged selector, not a copied view
implementation. This does not grant arbitrary missing executables or
unrestricted same-directory read-own-publication. Main owns the remaining
acceptance and delivery gates.

## TC-WORKFLOW-PROBE-RUNTIME-INPUTS-001: Observe explicit runtime inputs without executing recipes

### Feature and configuration

Issue [#227](https://github.com/laqieer/fireemblem8-expansion/issues/227);
the [explicit runtime-input contract](../ownership-probe-foundation.md#explicit-runtime-discovery-inputs)
depends only on the delivered #206 / PR #212 core. #227 is a standalone
`master`-based root (depth zero). #226 is independent; #225 and #228 are
not prerequisites. #180 / PR #186 owns complete-root integration, not this
case.

Use a clean Linux x86-64 checkout with GNU Make 4.3, Python 3, glibc, a
static-capable host C compiler and the core's working private namespaces,
pidfd/ptrace and Linux 5.12+ recursive mount attributes. Stock controls require
the actual root-owned `/bin -> /usr/bin` link and ordinary root-owned
`/usr/bin/mkdir`, `/usr/bin/env`, `/usr/bin/cat` and `/usr/include/stdio.h` files. Do not create
or replace system paths to satisfy a fixture. The newlib control observes the
real header if present, or its genuine absence; it never installs newlib.
The include-search controls derive matching relative and `/usr/include/`
absence names from their uniquely owned fixture directory and verify actual
absence. Legitimate ambient `build` or `.dep` include entries do not prevent
the case from running; no host contents are deleted.

No ROM, ARM compiler, emulator, credentials, remote mutation or subjective
judgment is required. Use the repository's existing locked host Python when
available; no additional Python package is required by this runtime family.

The byte/access/full-buffer controls discover ordinary standard-library data
with `/usr/bin/python3 -I -S -B -c 'import calendar; print(calendar.__file__)'`.
The result must be an ordinary trusted regular file accepted by the existing
capture and command policy, not a reserved image. Its path and byte size are
discovered, not a Debian unversioned `libc.so` prerequisite or pinned Python
version. `calendar` avoids Python's `os.py` startup-landmark stat, which would
legitimately add incompatible live-inode metadata to the access-only reuse
control. The complete incompatible-inode/buffer/status assertions remain in
their separate control; no metadata is filtered to make the fixture work.
Modeling an unavailable linker-script pathname proves only fixture independence,
not an alternate native host's platform support. Never install that library or
skip an authority check to satisfy these tests.

### Actions

1. From the repository root, run this complete ordinary/confined comparison.
   It creates a unique owned fixture below ignored `build/test-artifacts`,
   captures a real Git tree, and removes only that fixture in `finally`.
   The ordinary env recipe must first create a real sentinel with its selected
   variable removed. That sentinel is removed **before** confined observation.

   ```sh
   python3 -B - <<'PY'
   import json
   import os
   import secrets
   import shutil
   import subprocess
   from pathlib import Path
   from scripts.validation_ownership.authority import AuthorityLoader, ENVIRONMENT, git_tree_entries
   from scripts.validation_ownership.budget import MakeProbeError, ProbeBudget
   from scripts.validation_ownership.make_probe import ProbeSession

   root = Path("build/test-artifacts").resolve() / ("runtime-case-" + secrets.token_hex(8))
   root.mkdir(parents=True)
   header = "/usr/include/stdio.h"
   missing = "/usr/include/" + root.name
   missing_tool = "/bin/" + root.name
   names = ("HEADER", "MISSING", "MISSING_TOOL", "MISSING_TOOL_CANON", "PATH", "MKDIR", "ENV")
   assert Path(header).is_file() and not Path(missing).exists()
   assert not Path(missing_tool).exists() and not Path("/usr/bin/" + root.name).exists()
   assert Path("/bin").resolve() == Path("/usr/bin")
   assert Path("/usr/bin/cat").is_file()
   try:
       makefile = (
           "TOOLCHAIN ?= $(DEVKITARM)\nexport PATH := $(TOOLCHAIN)/bin:$(PATH)\n"
           "export RUNTIME_INPUT_CASE := ordinary-only\n"
           f"HEADER := $(wildcard {header})\nMISSING := $(wildcard {missing})\n"
           f"MISSING_TOOL := $(wildcard {missing_tool}/child.h)\n"
           f"MISSING_TOOL_CANON := $(wildcard /usr/bin/{root.name}/child.h)\n"
           "MKDIR := $(realpath /bin/mkdir)\nENV := $(realpath /bin/env)\n"
           + "".join("$(info " + name + "=$(" + name + "))\n" for name in names)
           + "all:\n\t@mkdir -p owned-mkdir\n"
           "\t@env -u RUNTIME_INPUT_CASE /usr/bin/python3 -I -S -B sentinel.py\n"
       )
       (root / "Makefile").write_text(makefile)
       (root / "sentinel.py").write_text(
           "import os\nfrom pathlib import Path\n"
           "assert 'RUNTIME_INPUT_CASE' not in os.environ\n"
           "Path('env-executed').write_text('ordinary payload ran')\n"
       )
       ordinary = subprocess.run(
           ["/usr/bin/make", "-f", "Makefile", "all"], cwd=root,
           env=ENVIRONMENT, capture_output=True, check=True, timeout=10,
       )
       expected = dict(line.split("=", 1) for line in ordinary.stdout.decode().splitlines())
       assert (root / "env-executed").read_text() == "ordinary payload ran"
       (root / "env-executed").unlink()
       (root / "owned-mkdir").rmdir()
       def git(*args):
           return subprocess.run(
               ["/usr/bin/git", *args], cwd=root, env=ENVIRONMENT,
               capture_output=True, check=True, timeout=10,
           ).stdout.decode().strip()
       git("init", "--quiet")
       git("add", "Makefile", "sentinel.py")
       revision = git("write-tree")
       for requested in ((header, missing, missing_tool, "/bin/mkdir", "/bin/env", "/usr/bin/env"), ()):
           budget = ProbeBudget()
           entries = git_tree_entries(root, revision, budget=budget)
           loader = AuthorityLoader(root, entries, revision, budget=budget)
           session = ProbeSession(
               loader, scratch_root=root / "build/probe", budget=budget,
               runtime_files=requested,
           )
           try:
               with session:
                   observed = session.make("all", variables=names)
                   assert requested, "disabled lookup must not fabricate absence"
                   actual = {name: observed.semantics["domains"][name]["value"] for name in names}
                   assert actual == expected
                   assert observed.events == () and observed.semantics["dynamic_commands"] == []
                   assert not (session.tree / "env-executed").exists()
                   assert not (session.tree / "owned-mkdir").exists()
                   print(json.dumps({
                       "requested": True, "ordinary_equals_confined": actual,
                       "runs": budget.runs, "processes": session.processes_used,
                       "live_peak": session.live_process_peak, "syscalls": session.syscalls_used,
                       "observations": session.observations_used, "bytes": budget.bytes,
                       "sudo_drop": session.sudo_drop,
                   }))
           except MakeProbeError as error:
               assert not requested
               assert f"uncaptured Make runtime access: metadata {header}" in str(error), error
               print("disabled control:", error)
           assert not (root / "env-executed").exists()
           assert not (root / "build/probe").exists()
           assert session.runtime_inputs == () and session.runtime_root is None
           assert not budget.children
       for requested in (("/bin/cat", "/usr/bin/cat"), ("/usr/bin/cat", "/bin/cat")):
           budget = ProbeBudget()
           entries = git_tree_entries(root, revision, budget=budget)
           loader = AuthorityLoader(root, entries, revision, budget=budget)
           session = ProbeSession(
               loader, scratch_root=root / "build/probe", budget=budget,
               runtime_files=requested,
           )
           try:
               with session:
                   raise AssertionError("ordinary canonical duplicate was admitted")
           except MakeProbeError as error:
               assert str(error) == "duplicate/overlapping optional runtime inputs", error
               print("duplicate control:", requested, error)
           assert not (root / "build/probe").exists() and not budget.children
       print("runtime input case: PASS; ordinary sentinel ran, confined sentinel did not")
   finally:
       shutil.rmtree(root)
   PY
   ```

2. Run the focused automation command below. The ordinary/confined controls
   compare actual newlib wildcard/include-search behavior, explicit file bytes,
   original/canonical mkdir and env paths, variable values/origins/flavors,
   native recipe text and both env declaration orders. Ordinary duplicate cat
   aliases and overlapping original/canonical missing prefixes must reject in
   both orders; distinct component names remain valid. Original and canonical
   descendants of a genuinely captured `/bin` absence must both return real
   absence. Inspect its named test
   results; an unsupported fixture or missing tool is not a passing negative.
   The spelling controls capture the discovered data and an actual nested
   standard-library input so each traversed intermediate directory really
   exists. Canonical present/absent Make `wildcard` and `file` lookups containing
   parent components must reject when they need optional runtime authority,
   like the stock-alias controls. Plain captured lookups pass full native
   metadata revalidation. The separate FD-relative registered-command,
   source-parent and mandatory-file/directory controls must still succeed.
3. Check the named denial controls: unrequested existing **and missing**
   files, unrequested `/bin` aliases, escaping spellings, nonregular/replaced
   captures, readback of intercepted images, writes, directory enumeration,
   and unsupported `cat`/env public dispatch. Make's directory wildcard is
   denied at its actual unrequested directory open; its exact-file wildcard
   counterpart succeeds. A requested `cat` is actually found before the
   authenticated dispatch rejects it. No error-regex widening substitutes
   for those positive counterparts.
4. Inspect the metadata controls' real syscall evidence: full
   stat/lstat/fstat/newfstatat/statx/fstatfs/access/readlink buffers, status and
   flags/masks. Unchanged optional Make metadata and compatible registered
   observations must pass the same native comparison as source records.
   Changing an owned captured timestamp must invalidate the old full record
   without validation changing atime. Different actual command-runtime
   inode/status results must reject reuse, not be replaced with the captured
   object's metadata.
5. Confirm mandatory Make/interpreter/ELF closure and phase-bound loader
   probes remain independent of the opt-in. Low existing capture/control
   quotas and failures must terminate with no owned processes, descriptors,
   source/runtime backing or control files left over.
6. Run the same family's owned-fixture shape controls. Model each ambient
   `/usr/include/build` and `.dep` entry as occupied and verify actual owned
   include-search positives still run. Model a regular system Python and an
   absent multiarch include directory; neither may prevent the owned regular
   capture, real directory/symlink/FIFO denials or inode-replacement denial.
   These models test fixture independence, not another native platform.
   On the actual owned regular inode, add set-UID, set-GID, sticky and all
   combinations to mode `0644`. Each special-mode capture must fail at its
   type/mode predicate before any data read, not an unrelated trust failure.
   Restore `0644` after every attempt and require successful capture again.
7. Run `test_runtime_inputs_optional_image_mapping_is_read_only_at_make_entry`
   through the same focused automation. The owned image contains actual
   captured non-intercepted executable bytes. Ordinary kernel read and
   read-plus-execute mappings must both succeed and expose the expected bytes;
   no mapped instruction is executed. The existing stopped-tracee helper then
   models post-observer Make state and its explicit runtime declaration:
   read mapping reaches the real kernel with actual memory reservation, while
   RX must fail at supervisor syscall entry before resumption. This is not
   injection into GNU Make, dispatch denial, a noexec substitute or a stubbed
   guard/reservation. Actual command-library RX and mandatory Make loading
   remain positive; the separate program-dispatch rejection is retained.
   This pointer-independent mmap case uses a fresh exact system-Python exec
   before its trace stop, with the same fixed `256 MiB` helper address-space
   policy. Only declared descriptors cross exec, and their actual inode/device
   identity and the caller's inheritance flags are checked. Other helpers keep
   their deliberate fork/copied-parent-pointer semantics.
   Repeat with an owned `256 MiB` PROT_NONE parent reservation: measure actual
   parent and stopped-tracee virtual memory, require each fresh tracee to fit
   the unchanged policy while the parent exceeds it, and release the
   reservation afterward. Returning this case to inherited-parent VM must
   reproduce real reservation failure. An isolated pass, larger allowance,
   trimmed test order or skipped read-positive cannot substitute for this
   enlarged-parent control.

### Expected result

The script prints the ordinary-equal header, missing path, PATH and canonical
tool values, the precise disabled runtime denial, and its final `PASS`.
The ordinary env sentinel demonstrably ran; neither that payload nor the mkdir
recipe executes in the confined observation. All focused controls pass through
the actual syscall/Make path. Full metadata goes through the core comparator,
not a names/types-only witness or a second optional-runtime predicate.

### Negative control

The same fixture without `runtime_files` rejects its existing header lookup;
it must not choose a false-absence branch. Explicitly captured true absence is
a separate positive, not that denial. On exact `2f48d7fa96a58020f2aceed8c509efd09a7ea88b`,
ordinary `/bin/cat` plus `/usr/bin/cat` was accepted only in one order, while
the other order was misclassified as a mandatory image collision. A captured
missing `/bin` prefix also rejected its own descendant although the equivalent
canonical descendant worked. The paired real Make controls must now agree,
without accepting sibling names, `..`, writes or enumeration.
On exact `415c7a5be36c329257271b12fc693005ef2939c6`, canonical parent spellings
could still acquire optional Make authority: real present/content and absent
queries with valid intermediate directories completed and passed native
revalidation, while stock-alias equivalents rejected. The new scoped controls
must reject these optional lookups without breaking independently authorized
command/source/mandatory parent paths. Reverting that boundary or the owned
fixture corrections must fail the corresponding regression family.
The historical absent-env materialization
unit control additionally models a host without env and must not install its
interceptor, including both env declaration orders. Actual true-absence assertions use the original absent paths,
not that modeled environment. Eager, recursive and include-remake env recipes
reject without an exact real registration; requesting env cannot produce
unittest output. The real nonregular/replacement controls mutate only owned
test inodes, with host-root trust checks tested independently. They do not
assume a Python executable is a symlink or require a Debian include directory.
Removing the special-bit predicate must fail every special-mode control;
removing the optional-image mapping predicate must fail the paired RX control.
These are test-only coverage mutations of existing guards, not newly
discovered implementation failures or a reset of any bound review hold.

### Interactions and save compatibility

Reuses #206's source admission, persistent complete read-only/noexec source
mount, authenticated dispatch, metadata frames/native comparison and aggregate
lifecycle. The optional runtime capture is fixed for that session; selection
across views is separately owned by #226, not a prerequisite here.
No generated producer, native Make registration or dependency-only action is
introduced. Game/profile conflicts are **none**. No save/migration, game/config
identity, ROM/RAM, localization, generated game output, modern/archival profile,
budget number, workflow topology/publisher, service or permission changes.

### Automation

```sh
python3 -m unittest scripts.validation_ownership.tests.test_foundation -k runtime_inputs -k stock_runtime_alias -k explicit_env -k absent_captured_env -k make_uncaptured_runtime -v
```

The required core metadata/closure/lifecycle neighbors remain in their existing
family; no duplicate channel-policy tests or whole-root report are introduced.

### Cleanup and limitations

The script and tests remove their own fixtures; session teardown also clears
runtime records and removes persistent owned backing after failure or quota
exhaustion. Do not remove or modify host includes, `/bin`, env or Make images.
Unsupported layouts, metadata reuse and host/kernel facilities reject rather
than falling back to live mounts, fabricated metadata or arbitrary execution.
This proves the optional runtime contract, not the complete #180 report,
producer/view/dependency integration or ROM behavior. No manual-only criterion
remains; rollback removes the optional layer or fixes forward without widening
the mandatory core.

## TC-WORKFLOW-PROBE-VIEWS-001: Select immutable ownership views with one report budget

The [namespace-image CI extension](#namespace-image-ci-regression-correction)
requires metadata-neutral root/nested capture and genuine cached reuse in the
selected BASE view and restored CURRENT, retaining real hardlink/metadata
invalidation and the original lifetime.

### Feature and configuration

Issue [#226](https://github.com/laqieer/fireemblem8-expansion/issues/226);
Linux x86-64 source checkout with the
[foundation prerequisites](../ownership-probe-foundation.md#run-the-real-consumer):
GNU Make 4.3, Python 3, static-capable C/C++ host compilers and supported private
namespaces. No ROM, emulator, feature flag, credentials or remote mutation.
Use a clean checkout. All fixtures, Git commits and native outputs are owned
under ignored `build/test-artifacts`; never use another checkout's report.
The full-tree case also requires this checkout's local `HEAD` and first parent
`HEAD^1`, with the captured gitlinks' object databases already available.
Missing history or pins fails rather than selecting a smaller fixture or
substituting another revision.

### Actions

1. Run the focused family:

   ```sh
   python3 -m unittest scripts.validation_ownership.tests.test_foundation -k immutable_view -v
   ```

   The principal case creates real BASE and CURRENT Git commits, using the
   actual `SchemaRegistry`, `ShopsTableSchema`, JSON loader and record-count
   implementation. BASE declares `src/data/deleted_generated.json` with a
   shop record. CURRENT removes that path, declares
   `src/data/current_generated.json` and contains an additional shop record.
   These are disposable source inputs, not generated game output.

2. Inspect the same real consumer directly from the source root:

   ```sh
   python3 - <<'PY'
   import json
   from dataclasses import replace
   from scripts.validation_ownership.budget import ProbeBudget
   from scripts.validation_ownership.make_probe import ProbeSession, probe_generated_registry
   from scripts.validation_ownership.tests.test_foundation import FoundationTests

   fixture = FoundationTests()
   fixture.setUp()
   budget = ProbeBudget()
   try:
       base, current, (old, new) = fixture.deleted_source_views(budget)
       declarations = replace(new, argv=("/usr/bin/python3", "/repo/declarations.py"), sources=())
       print("BASE", base.revision, "CURRENT", current.revision)
       with ProbeSession(current, scratch_root=fixture.scratch, budget=budget) as probe:
           previous = probe.snapshot, probe.tree
           def observe(label, loader, command):
               registry = probe_generated_registry(loader, command=command, session=probe)
               owners = json.loads(probe.command(declarations).stdout)
               print(label, json.dumps({"registry": registry, **owners}, sort_keys=True))
           observe("CURRENT", current, new)
           with probe.select_view(base) as selected:
               assert selected is probe
               observe("BASE", base, old)
           assert (probe.snapshot, probe.tree) == previous
           observe("RESTORED CURRENT", current, new)
           print("cumulative", budget.runs, budget.states, budget.bytes, probe.observations_used)
       fixture.assert_clean(probe)
   finally:
       budget.close()
       fixture.tearDown()
   PY
   ```

   CURRENT must classify the removed path as unowned; BASE must classify it
   as owned by `shops`, report its original path and original record count,
   and read its actual captured bytes. Restored CURRENT must match the first
   CURRENT record/declarations. The automated case also obtains BASE and
   restored CURRENT declarations through real confined GNU Make, with no
   generated publication or native-command registration.

3. Preserve the historical wrong-BASE shortcut as a negative: using CURRENT's
   declarations for the removed path gives an empty owner list, whereas
   BASE's real registry gives `["shops"]`. Before the selector, the core also
   rejects a foreign-loader helper call, a second same-budget session and a
   missing deleted input in CURRENT. The pre-feature positive composition
   fails because `select_view` is absent. Do not repair that failure with a
   union, fixture-only classification, separate budget or prefixed paths.

4. Exercise nested selection, default-live restoration after an actual
   post-capture edit, wrong root/revision, foreign budget/repository, detached
   capture, mutable alternate and inactive/closed authority. Admission failures
   leave the original healthy owner unchanged. Exceptions during snapshot
   construction, materialization, body, interruption and teardown restore
   the actual previous state but leave the report terminal. Late or misnested
   context exits cannot revive closed state; outer cleanup clears suspended
   caches and native handles, not just the currently selected dictionaries.

   Exercise the owner-exit regressions directly, without rerunning an unrelated
   native/profile suite:

   ```sh
   python3 -m unittest scripts.validation_ownership.tests.test_foundation.FoundationTests.test_immutable_view_foreign_exit_preserves_correct_owner_unwind scripts.validation_ownership.tests.test_foundation.FoundationTests.test_immutable_view_foreign_exit_during_command_preserves_backing_and_cache_owner -v
   ```

   The entering worker warms real CURRENT/BASE command caches, then another
   owned thread attempts normal exit, exceptional exit and misnested exit.
   Record backing, cache identity/content, view stack, registered children,
   handlers and accounting **before the owner resumes**. Every attempt must
   raise the worker-violation error before the context generator is resumed
   or receives an exception. Only the existing failed-budget flag changes:
   there is no foreign cleanup or signal restoration. The owner must still
   be able to unwind the preserved context normally or exceptionally.

   In the active-command case, the genuine confined BASE child first reads
   its source, closes an owned value file and hardlinks the ready marker to
   that completed file, then waits on an owned
   release marker. Pause the owner at its existing budget check while the
   other thread attempts exit. Before releasing the owner/child, require
   intact BASE backing/cache/stack and the same live registered child with
   an open lifetime pipe. On resumption the owner encounters the failed
   budget and cleans up; BASE output must not enter CURRENT's cache.
   The synchronization is test-only, not a cross-thread production scheduler.
   Every reproduction thread must join and correct-owner cleanup must finish,
   including when exercising the pre-fix negative.
   Merely observing a file's creation is not readiness: an empty file before
   its write cannot trigger the foreign-exit action.

5. Observe file-to-directory and directory-to-file changes, different file
   values, complete selected directory listings, module presence/absence,
   symlink/unadmitted-gitlink rejection and exact empty admitted gitlinks.
   A directory declaration must not grant member bytes. Real renamed gitlink
   paths must use their respective immutable pins, not checked-out contents.
   Native tools compile/run in the selected channel-free view. Suspended,
   copied/forged or expired handles reject even for identical ELF/snapshot
   bytes; a normal restoration preserves the original CURRENT handle.

6. Inspect certified storage reuse under the lowered fixture envelope:
   unchanged original-path/mode/type/object entries share bytes and source
   inodes; changed, missing or mode-different entries do not. Source writes
   remain denied, and selected storage is removed on exit. Real stat/fstat
   output must reflect the changed hardlink count/ctime and different directory
   inodes. Restored CURRENT revalidates and executes afresh when its old
   metadata changed, then supports compatible reuse.
   Complete statx buffers must retain actual mount/UID/GID fields; access and
   readlink retain actual status, flags, masks and unchanged caller buffer
   tails. Altered namespace-field records cannot authorize reuse.
   The unchanged-record core controls still prove native Make uses this same
   complete backing and revalidation does not mutate metadata.
   Run the committed full-tree capture-envelope regression, also included
   in the `immutable_view` family:

   ```sh
   python3 -m unittest scripts.validation_ownership.tests.test_foundation.FoundationTests.test_immutable_view_real_repository_query_pair -v
   ```

   It resolves CURRENT `HEAD` and BASE `HEAD^1` once from the actual local Git
   repository, then uses their complete immutable trees and existing explicit
   gitlink/source declarations. One **default** `ProbeBudget`/`ProbeSession`
   performs CURRENT localization Make plus chapterbundle registry, selected
   BASE Make plus registry, and restored CURRENT Make. Expect exactly two
   full-registry queries; there is **no third full-registry query**. Verify
   actual source sets, prerequisites, registry records, certified byte/inode
   reuse, restored ownership and CURRENT semantics, cumulative counters,
   unchanged deadline/limits and complete cleanup. No historical file/byte
   census is the oracle. Keep a precise hold if default limits fail: do not
   raise caps, mask metadata, shrink the tree or substitute two independent
   `consumer.check` calls. This is the real workload, not an ignored harness
   or a replacement for the smaller changed-declaration controls.

7. Exhaust existing state, snapshot-byte, launch, creation, observation and
   descendant allowances across selection/restoration. Cache hits with no
   metadata avoid new work only within their owning view; required metadata
   validation remains charged. Make, capture and BASE work share the original
   deadline and every cumulative counter. Failed/closed budgets reject before
   another launch, with complete owned cleanup and no refunded work.
   The two-live-process/nine-descendant fixture executes a genuine pure result
   before two native Make queries, allowing cache reuse beside the two parked
   guests without launching a third. Its BASE work still reaches exactly the
   original descendant limit. The separate cold-producer control rejects at
   the two-slot reservation boundary rather than weakening it.

### Expected result

The same report yields genuine CURRENT, BASE and restored CURRENT results.
BASE owns the deleted path through its own registry, with original consumed
bytes and count. View state and handles restore correctly; complete actual
metadata governs reuse even when storage is shared. All cumulative counters
and the original deadline remain in force, with complete owned cleanup.
The full-tree `HEAD`/`HEAD^1` case completes two localization/chapterbundle
query pairs and the restored CURRENT Make observation under default limits.
It does not request a third full-registry replay or claim full #180 acceptance.

### Negative control

Retain the actual pre-feature missing-selector failure and wrong-loader,
second-session and missing-CURRENT-source rejections. Borrowing CURRENT's
owner list gives the wrong answer for the deleted BASE source. Unsupported
types, stale metadata/handles, failed or closed budgets and exhausted
resources must reject, never produce success-shaped replacement evidence.
The pre-fix owner-exit controls delete selected storage and clear/restore
caches from a foreign thread. Exceptional exit closes the budget; misnested
exit also attempts main-thread signal restoration and produces signal errors.
The synchronized active case returns real BASE output into the restored
CURRENT cache under its BASE request key. This proves broken cache ownership,
not a demonstrated key collision. Keep those outcomes as negatives; a check
inside generator cleanup cannot preserve the context for its later owner.

### Interactions and save compatibility

Depends on the delivered #206 / PR #212 core. Issue #226 is now a standalone
`master`-based root (depth zero); #180 / PR #186 owns downstream integration.
No dependency on independent #225 producer work, #227 runtime-input work or
#228 dependency compilation. Their mixed historical tests retain their
respective integration requirements: generated-context publication/remakes,
optional runtime lifetime and dependency-header output are not enabled or
claimed here. The view projections retain native/cache isolation, exact
listing/absence, pins and all cumulative controls.

Game/profile conflicts: **none**. No save/migration/config identity, generated
game output, localization, ROM/RAM, modern debug/release, archival, workflow
topology, patch publisher or default numerical limit changes. The default
single-view API remains supported. Revert this layer normally if necessary;
never restore borrowed CURRENT ownership for BASE.

### Automation

The focused command above maps every deterministic action to real Git,
confined Python/Make/native processes, parsed JSON/ELF/metadata, counters and
owned state. The existing core metadata, source and lifecycle tests remain
neighboring evidence under the same runner.

### Cleanup and limitations

Fixtures and session roots clean themselves; remove only the empty owned
test parent if desired.

No manual-only criterion applies. Unsupported hosts or metadata reproduction
reject instead of falling back. Large changed views can still exhaust the
unchanged envelope, and repeated full-registry metadata can exhaust the
control allowance even when source reuse fits. Neither the owned registry case nor a real
localization/chapterbundle pair is complete #180 CURRENT/BASE/112-domain,
census, graph, oracle, lifecycle or public-gate acceptance. User-namespace
evidence does not imply a separately untested sudo credential transition.

## TC-PROBE-METADATA-TRANSPORT-001: Transport native metadata losslessly

### Feature and configuration

Issue [#240](https://github.com/laqieer/fireemblem8-expansion/issues/240);
the supported Linux x86-64/GNU Make 4.3 ownership-probe foundation with the
existing namespace/watchdog route and one extended native owner. Start from a
clean source checkout. The public legacy surface remains
`ProcessOutput.metadata`; only the supervisor-parent transport changes to the
closed `vo-metadata-frame` envelope described in the
[foundation contract](../ownership-probe-foundation.md#complete-metadata-and-static-reuse).
Fixtures stay under owned ignored `build/test-artifacts`; no ROM, new job,
graph planner, extra runtime or remote action is involved.

### Actions

1. Run the focused prototype coverage below.
2. Exercise a real mixed-source metadata reader with complete before/after
   buffers, failures, flags and masks, plus a real 4096-byte directory
   enumeration capture with recorded offsets. For both captures, compare the
   decoded public records with the original supervisor envelope, run unchanged
   metadata replay, and verify the legacy cache charge still uses
   `encoded(ProcessOutput.metadata)`.
3. Measure the exact old full-report JSON size, new full-report JSON size,
   envelope JSON size, decoded binary frame F, encoded-payload retention P and
   fixed 4096-byte trusted scratch bound. Require a positive conservative saving
   where `old_report > new_report + F + P` on the representative native captures.
   F and P must be charged before decoding; scratch and bounded transient codec
   chunks are not savings terms or the existing guest `memory_peak` metric.
4. Corrupt envelope fields, version, encoding, base64, truncated or trailing
   zlib streams, decoded-size/count claims, frame paths, duplicate records and
   ABI sizes. Require fail-closed rejection before unsafe allocation or replay.
   Include base64-valid non-zlib data and a stream whose decoded-size boundary
   precedes more output in the next input chunk. It must not grow the reserved
   frame. A valid split footer with no extra output must still succeed. Exercise
   the single-frame allocation, direct scratch reuse and pre-decode control
   exhaustion controls. Keep the modeled privileged-lifecycle control on the
   new envelope and prove owned cleanup on failure.
5. Reuse the unchanged metadata replay path through cache validation and
   CURRENT/BASE view selection. Compatible records must still replay and reuse;
   incompatible namespace or source changes must still force fresh execution
   rather than stale acceptance.

### Expected result

Supervisor reports serialize metadata as one strict
`{"format":"vo-metadata-frame","version":1,"encoding":"zlib-base64",...}`
object. Decoding that envelope yields the same legacy tuples previously
returned to consumers, with unchanged raw observation accounting, unchanged
legacy per-record dedupe/hashes, unchanged cache charges on
`encoded(ProcessOutput.metadata)`, and unchanged `validate.meta` replay bytes.
Representative native captures show a positive conservative control saving
without truncating buffers or weakening authority checks.

### Negative control

Do not accept mixed legacy-list fallback, unknown transport fields or versions,
invalid base64, incomplete or concatenated zlib data, decoded-size or
record-count mismatches, duplicate records, noncanonical frame paths, ABI-size
changes or tampered replay bytes. Do not claim savings from wire payload alone,
refund raw observation cost, move charges to another category, or materialize a
second complete decoded frame at the parent. Unsupported metadata replay and
selected-view mismatches must still reject or re-execute exactly as before.

### Interactions and save compatibility

Depends on the delivered source authority, native producer, immutable-view,
runtime-input and dependency-compiler contracts (#206, #225, #226, #227 and
#228). Cached native commands and Python producers share this transport;
#180/#186 consumes it downstream. No additional feature conflicts apply. Save
formats, configuration identity, generated game data, localization, ROM/RAM,
modern/archival profiles and existing replay ABI remain unchanged.

### Automation

```sh
python3 -m unittest \
  scripts.validation_ownership.tests.test_metadata_transport \
  scripts.validation_ownership.tests.test_foundation.FoundationTests.test_static_metadata_uses_persistent_objects_for_cache_and_native_make \
  scripts.validation_ownership.tests.test_foundation.FoundationTests.test_metadata_captures_complete_syscall_buffers_status_flags_and_masks \
  scripts.validation_ownership.tests.test_foundation.FoundationTests.test_runtime_inputs_capture_full_optional_buffers_status_flags_and_masks \
  scripts.validation_ownership.tests.test_foundation.FoundationTests.test_metadata_transport_preserves_mixed_syscalls_cache_and_replay \
  scripts.validation_ownership.tests.test_foundation.FoundationTests.test_metadata_transport_preserves_directory_enumeration_offsets_and_savings \
  scripts.validation_ownership.tests.test_foundation.FoundationTests.test_metadata_transport_keeps_selected_view_metadata_boundaries \
  scripts.validation_ownership.tests.test_foundation.FoundationTests.test_metadata_report_validation_rejects_malformed_buffers_and_native_writes \
  scripts.validation_ownership.tests.test_foundation.FoundationTests.test_directory_observation_transfer_and_buffer_limits_are_bounded \
  scripts.validation_ownership.tests.test_foundation.FoundationTests.test_sudo_preflight_and_capsules_share_the_privileged_lifecycle_contract \
  scripts.validation_ownership.tests.test_producer.ProducerTests.test_make_lookup_guard_preserves_ordinary_absence_nonexecutables_and_metadata -v
```

The focused command above runs the real command, Make, runtime-input,
selected-view, producer and modeled privileged-lifecycle scenarios together
with pure codec corruption coverage. The integrated native owner remains
`make -f scripts/validation_ownership/foundation.mk ownership-probe-test`; no
new worker or job is introduced.

### Cleanup and limitations

Fixtures and session roots clean themselves; remove only the empty owned test
parents if desired. No manual-only criterion applies. This bounded prototype
does not claim full #180/#186 graph fit, attributed producer accounting or
remote completion; broader integration remains a downstream gate.

## TC-PROBE-NATIVE-EVENT-ORDER-001: Validate native events in physical append order

### Feature and configuration

Issue [#246](https://github.com/laqieer/fireemblem8-expansion/issues/246);
the supported Linux x86-64/GNU Make ownership-probe foundation with its
existing native producer interceptor, ptrace supervisor and private namespace
route. Start from a clean source checkout. Fixtures and deterministic schedule
proofs remain under owned ignored `build/test-artifacts`; no ROM, graph query,
new service, privilege, dependency, runtime platform, remote action or limit
change is involved.

### Actions

1. Run the focused automation below.
2. Execute the real two-helper `make -j2` producer fixture with the deterministic
   test supervisor. Let both helpers issue their unchanged single complete
   `O_APPEND` writes, but return the successful ptrace write-exit stops in the
   reverse order. Record the physical file bytes and the supervisor's exact
   successful-write bytes without rewriting either.
3. Decode the physical stream with the production wire parser. Compare exact
   frame bytes with multiplicity against the successful full-write
   observations, then verify the returned events, distinct slots, output values
   and publication receipts follow physical append order.
4. Run the ordinary unscheduled parallel case. Inject missing, extra,
   duplicated and bit-flipped physical frames and observations, plus partial
   and trailing physical bytes. Pair consistent mutations for wrong slot,
   mapping count, command and hash so no sequence-only mismatch hides the
   underlying receipt or frame guard.
5. Reuse the existing parked-helper, malformed producer request, nested
   publication-transfer, event/control budget and cleanup controls. Require
   every invalid transcript to terminate without partial success or retained
   output.

### Expected result

The controlled run succeeds with both real output values and two distinct
receipts. Exact physical frame bytes and successful write observations have
equal multisets, while their deliberately reversed sequences differ. Returned
events follow the physical file sequence. The ordinary case remains supported,
and successful full-write checks, slot/command/hash/mapping-count validation,
publication acknowledgement, cumulative accounting and owned cleanup retain
their existing behavior.

### Negative control

The pre-fix implementation rejects the controlled legitimate schedule with
`trusted interceptor frame differs from its native write`. Missing, extra,
duplicate, corrupt, partial or trailing physical/observed data must still
reject. A duplicated exact frame cannot satisfy two receipt slots; consistently
rewritten slot, command, hash or mapping-count bytes cannot pass the existing
wire and receipt checks. Partial writes, incomplete producer/publication
evidence, exhausted event/control budgets and failed cleanup never become
success-shaped output.

### Interactions and save compatibility

Depends on the delivered #206/#212 foundation and #225/#232 native producer
protocol, and composes with delivered #240/#241 metadata transport. #242/#245
and #243/#244 are independent. #180/#186 and other native consumers use the
same corrected foundation. Conflicts: **none**. The on-disk/supervisor ABI,
atomic append, scheduling, parking, privileges, public APIs and all numeric
limits remain unchanged.

No gameplay, save/migration/config identity, generated game data, localization,
ROM/RAM, modern debug/release or archival profile changes apply.

### Automation

```sh
python3 -m unittest \
  scripts.validation_ownership.tests.test_producer.ProducerTests.test_native_parallel_events_follow_physical_append_order_not_exit_stop_order \
  scripts.validation_ownership.tests.test_producer.ProducerTests.test_native_parallel_dispatch_keeps_distinct_request_receipts \
  scripts.validation_ownership.tests.test_producer.ProducerTests.test_native_event_stream_and_write_observations_match_exact_bytes_with_multiplicity \
  scripts.validation_ownership.tests.test_foundation.FoundationTests.test_serial_resolution_rejects_known_mapping_miss_without_rerunning_worker \
  scripts.validation_ownership.tests.test_foundation.FoundationTests.test_strict_named_protocols_reject_binary_and_truncated_frames \
  scripts.validation_ownership.tests.test_foundation.FoundationTests.test_event_mapping_and_pending_byte_bounds_cover_real_commands \
  scripts.validation_ownership.tests.test_producer.ProducerTests.test_parked_helper_death_aborts_without_publication \
  scripts.validation_ownership.tests.test_producer.ProducerTests.test_nested_publication_transfer_rejects_corruption_and_never_retries_work -v
```

This single focused path owns the deterministic reversed-exit proof, ordinary
parallel behavior, exact-byte/multiplicity adversaries, shared parser framing,
incomplete publication evidence, unchanged budgets and cleanup.

### Cleanup and limitations

The tests remove only their owned fixture roots, generated publications and
schedule proof. No manual-only criterion applies. The schedule controls ptrace
notification delivery only; it does not alter helper frame bytes, receipt IDs,
kernel writes, producer authority or production scheduling. This focused case
does not claim complete #180/#186 graph integration or remote completion.

## TC-PROBE-PYTHON-PRODUCERS-001: Share source-only Python producer commands

### Feature and configuration

Issue [#238](https://github.com/laqieer/fireemblem8-expansion/issues/238);
the supported Linux x86-64/GNU Make 4.3 ownership-probe host with Python 3,
the existing namespace/watchdog route, and the delivered generated-data source
selection foundations. Start from a clean source checkout. The public API lives
in `scripts/validation_ownership/python_commands.py`; see
[live producers](../ownership-probe-producers.md). Fixtures remain under owned
ignored `build/test-artifacts`; no ROM, remote action, graph planner or new
workflow job is involved.

### Actions

1. Run the existing native owner:
   `make -f scripts/validation_ownership/foundation.mk ownership-probe-test`.
   This selects the same foundation, producer and dependency modules as the
   `extended-host-tests` Build worker; no standalone job or runner is added.
2. Exercise the shared `python_command(...)` closure in small owned fixtures.
   Import one helper through another Python module and require only the actual
   imported code paths in the closure and execution receipt; unrelated modules
   must stay outside the declaration. Repeat with a repository package under
   `tools/`. A body with no repository code or explicit root directory must
   not enumerate `/repo`; explicitly requesting `directories=(".",)` permits
   that root listing without accepting noncanonical aliases.
3. Use a real top-level gitlink fixture plus standard `import scripts`.
   A raw immutable capture without admitted gitlink sources must still fail at
   `nonregular namespace in source enumeration: /repo`. The same command under
   a complete gitlink-aware capture must succeed with Python's ordinary
   `NamespaceLoader`, `__spec__.origin == None` and `['/repo/scripts']`.
4. Run the metadata-only registry source selector through the shared command
   helper. The schema's public `source_paths(...)` must drive the returned
   bundle list; `load_records(...)` must not execute for the selector path.
   An attempted read of an unrelated test module must reject, not gain code
   authority merely because it is under `scripts/generated_data/`.
5. Capture BASE, change the same source file and helper module path in CURRENT,
   then run the same `python_command(...)` before, during and after
   `select_view(BASE)`. Require CURRENT bytes, BASE bytes, then restored
   CURRENT bytes with no stale cross-view reuse.
6. In a compact real generated-data fixture, adapt all three audited deps
   modules through `generated_dependency_command(...)`:
   `chapterobjectives`, `autoplaystrategies`, `eventlists`. Compare ordinary
   CLI depfile bytes with the confined producer hashes, publish the three
   depfiles into live Make and require `MAKE_RESTARTS=1`.
7. Reorder the autoplay strategies named options in ordinary CLI form and in
   the typed option mapping. Require identical semantic depfile inputs. Omit a
   required option and add an extra one: both must reject before a result.
8. Reject an output path that collides with an immutable source and a missing
   admitted companion such as `assets/manifest.json`. No helper may fabricate
   depfile output, widen admission or rewrite metadata when those negatives fail.
9. Repeat CURRENT/BASE/CURRENT through the actual generated-dependency factory
   in a bounded file-input fixture with changed same-path module and source
   bytes. Verify the discovered paths, actual input identities and depfile
   bytes on all three visits; restoration must match the first CURRENT result.
10. Pass malformed objective, strategy and bundle JSON through the respective
    real dependency tools. Require failure, no published depfile and complete
    session cleanup.

### Expected result

The shared source-only API preserves normal Python import behavior while
binding source/code/directory rights to the active selected view. Complete
capture plus declared ancestors succeeds; incomplete capture and missing
ancestors still fail at their actual guard boundaries. Registry source
selection uses only the explicit `source_paths` API. The typed generated
dependency helper binds exact named options, concrete file/directory inputs,
module code and one declared depfile output, and native Make consumes the
published depfiles with one real restart.

### Negative control

Do not reintroduce a synthetic package loader, alternate import mode, private
cache or guessed depfile renderer. Raw immutable capture without admitted
gitlinks, missing source ancestors, unrelated closure members, stale selected
views, missing/extra options, conflicting outputs and missing companions must
reject instead of returning success-shaped fallback results.

### Interactions and save compatibility

Depends on the delivered #206 / PR #212 core and composes with the delivered
producer (#225), view (#226), runtime (#227), dependency (#228) and source
selection (#234) APIs. #180 / PR #186 and #196 consume this seam later; graph
dispatch, shell normalization, review/oracle/coordinator logic and full-root
resource acceptance remain outside this issue. No gameplay, save/config
identity, localization, generated game content, ROM/RAM, workflow topology or
resource-budget meaning changes.

### Automation

`make -f scripts/validation_ownership/foundation.mk ownership-probe-test`
remains the sole native owner. Its existing command expands to the foundation,
producer and dependency modules exactly once; no new workflow, required
context or duplicate unittest owner is added.

### Cleanup and limitations

All owned fixture trees, selected views, depfiles and private session roots
clean through the existing watchdog/session cleanup. This case does not prove
#180's graph dispatch, shell-line continuation normalization, full repository
query fit or resource calibration. No manual-only criterion applies.
The view-identity case uses small executable source-only modules with the same
factory signatures, separately from the real three-module publication and
malformed-input cases. It proves view binding, not whole-repository resource
fit; larger attempted view fixtures that hit unchanged resource limits are
not counted as successful acceptance.

## TC-PROBE-DEPENDENCY-FUSION-001: Fuse generated dependency producer stages

### Feature and configuration

Issue [#242](https://github.com/laqieer/fireemblem8-expansion/issues/242);
the supported Linux x86-64/GNU Make 4.3 ownership-probe host with Python 3,
the existing namespace/watchdog route, and the delivered producer/view/runtime/
dependency/source-selection foundations. Start from a clean source checkout.
The public API remains `generated_dependency_command(...)` in
`scripts/validation_ownership/python_commands.py`; no new router, importer,
runtime service or workflow job is introduced.

### Actions

1. Run the existing native owner:
   `python3 -m unittest scripts.validation_ownership.tests.test_producer -v`.
   This keeps dependency fusion under the same producer-native owner already
   used by the existing foundation workflow.
2. Capture actual capsule counts for all three audited dependency modules.
   Compare a legacy three-stage reconstruction against the corrected helper:
   chapterobjectives drops from five command capsules to four, autoplay from
   three to two, and the eventlists file-plus-bundle-directory case from three
   to two. The existing selector and bundle-support capsules stay intact; only
   the separate cold collector execution disappears.
3. Compare each module's confined depfile bytes with the ordinary module CLI
   and consume the published depfiles through native Make. Require the same
   include/restart behavior and one real output publication per invocation.
4. Preserve CURRENT/BASE/CURRENT binding for support-from-bundle modules.
   Change the selected bundle bytes, the target deps module and one declared
   bundle dependency module, then require chapterobjectives and autoplay to
   return current output, base output, then restored current output with input
   identities bound to the selected snapshot.
5. Mutate actual collected inputs so the module omits, adds, duplicates or
   escapes a path beyond the admitted selector/support evidence. Reject before
   output publication. Missing and extra named options still reject before any
   capsule starts. Missing/conflicting output targets and the forbidden rename
   guard remain unchanged.
6. Invoke the same output-producing registration twice through the existing
   producer workflow. Require two real executions/publications while equivalent
   provenance still deduplicates under the existing producer contract.

### Expected result

The helper still derives authority only from the existing `source_paths`
selectors and the existing chapterbundle-support capsule, including the actual
implementation-module `.py` inputs reported by
`chapterobjectives.deps._implementation_module_paths()` in the selected
snapshot. One final output command normally imports the real module, checks its
actual `collect_input_paths(...)` result for exact canonical duplicate-free
agreement with that admitted set, validates tracked source identities, calls
the real `render_depfile(...)`, and writes fresh private `/work` output. No
second cold collector execution remains.

### Negative control

Do not add a second importer, namespace shim, whole-tree source grant, new
runtime platform, output cache bypass or ordinary Make workaround. Missing and
extra options, unexpected/omitted/duplicate/escaping collected paths, missing
companions, conflicting outputs and forbidden rename behavior must still reject
before publication. Repeated output registrations still execute and publish
twice; the fusion only removes the redundant collector stage.

### Interactions and save compatibility

Depends on the delivered #206 / PR #212 core, producer (#225), view (#226),
runtime (#227), dependency (#228) and Python producer (#238) APIs. #240 / #241
metadata transport is independent because the public producer ABI and metadata
replay format remain unchanged. #180 / PR #186 consumes this correction later;
graph query fit, new quotas and broader resource acceptance remain outside this
issue. No gameplay, save/config identity, localization, generated game content,
ROM/RAM, workflow topology or accounting-meaning changes land here.

### Automation

`python3 -m unittest scripts.validation_ownership.tests.test_producer -v`
provides the native command, Make, CURRENT/BASE, negative-control and capsule-
count evidence. The existing `make -f scripts/validation_ownership/foundation.mk
ownership-probe-test` owner remains the wider integration command; no new CI
job or alternate owner is added.

### Cleanup and limitations

All owned fixture trees, depfiles, selected views and private session roots
clean through the existing watchdog/session cleanup. This case proves the
shared helper correction only; it does not claim full #180 graph fit, a fresh
full-query run, resource calibration or any manual-only criterion.

## TC-WORKFLOW-PROBE-DEPENDENCY-001: Observe real confined compiler dependencies

### Feature and configuration

Issue [#228](https://github.com/laqieer/fireemblem8-expansion/issues/228);
supported Linux x86-64, GNU Make 4.3, Python, the existing GNU HOST C driver
and cc1, and the existing namespace/watchdog route. Start from a clean source
checkout. See the [public command profile](../ownership-probe-dependencies.md).
Fixtures and sentinel/output paths remain under owned ignored
`build/test-artifacts`; no ROM, ARM/agbcc setup, package installation, remote
workflow or generated game data is needed.

### Actions

1. Run the complete focused deterministic case:
   `python3 -m unittest scripts.validation_ownership.tests.test_dependency -v`.
   The fixture source includes a recursive quoted-header pair, a `priority.h`
   selected through two ordered `-I` paths, an `ENABLED` conditional and
   `future/generated.h`. The last header is initially genuinely absent.
2. Compare ordinary `cc -E ... -MM -MG -MT query.o` stdout with the confined
   command's declared `out/query.d` **raw bytes**. Require nonempty identical
   output, ordinary mode, empty confined stdout, no ELF/native handle and
   actual successful driver then cc1 exec receipts. Compare the union of
   consumed source/header paths and its captured byte/mode identities.
3. Reverse `first` and `second` include search order. Interleave
   `-DENABLED=1`, `-UENABLED`, `-DENABLED=0`, and reverse that macro choice.
   Reverse two `-iquote` directories using joined and separate values too.
   Require the real corresponding priority/conditional headers and ordinary
   byte equality. Inactive pool headers must not become provenance. Repeat
   with the exact union declared as `sources` instead of an optional header
   `code` pool.
4. Repeat an identical valid query. Require genuine effectful executions and
   compatible bytes/receipts while the session reuses only its resolved host
   profile. Change a checkout header after the source snapshot was captured:
   results must still bind the original captured source, not later host data.
   Replace a published conditional header between identical compiler calls;
   require two corresponding dependency receipts and fresh output from the
   pure reader of the replaced `.d`.
5. In one live Make, publish `quote/future/generated.h` from `header.in`,
   then run the real dependency command and publish its declared `.d`.
   Compare ordinary prerequisites, `MAKEFILE_LIST` and `MAKE_RESTARTS=1`.
   Require one Make capsule and actual generated-header provenance. No nested
   `session.make()` publication or guessed restart is involved.
6. Include a header named `two words.h`. Require GCC's real escaped dependency
   bytes and let ordinary/native GNU Make parse them. Confirm the resulting
   prerequisite is the single filename, not words from a copied `.d` parser.
7. Run the real `src/worldmap_tm_confront.c` dependency recipe with this
   checkout's actual header bytes and original host argument/search order,
   including the three asset include paths. Compare ordinary/confined raw
   bytes and actual source/header receipts. Missing archival/generated
   inputs remain genuine `-MG` missing inputs; this is not an archival build,
   installation or full-root ownership report.
8. Leave `dependency_only=False`, pass a non-boolean, remove a required mode,
   add compilation/assembler/linker, response/plugin/specs/wrapper/output
   flags, combine a forged native handle, or declare an escaping/wrong/multiple
   output. Require rejection without a payload launch. Omitting `-MG` from
   the real missing-header query must preserve the actual compiler failure.
9. Omit an existing header from the admitted pool, including an
   `__has_include` branch with its valid counterpart. Reject rather than
   reporting false absence. Attempt a regular file as a directory ancestor
   and a symlink header. Include-search metadata is not member-content or
   directory-enumeration authority; unused required sources still reject.
10. Exhaust the remaining aggregate output allowance after a successful
    query and require the real repeated compiler result to fail capture.
    Give a session enough live capacity for standalone driver/cc1 but not
    for both alongside parked Make/helpers: require failure at that actual
    nested compiler boundary. Corrupt its execution receipt and inject a
    cleanup failure; neither may yield successful evidence.
11. Attempt an actual host `#include` and an `__has_include` branch selecting
    an admitted repository header. Include through a declared recursive
    header too. Ordinary GCC can consume the host file, but the D command
    must reject before returning dependency evidence. Cover system/local
    includes, GCC private/include-fixed/libexec paths, library/sysroot trees,
    Python data, extensionless files, aliases, parent spellings, missing
    names and wrong types. Necessary resolved driver/interpreter/library
    paths and bounded runtime probes must still execute successfully.
12. Run the owned mutation control that bypasses only D's host decision while
    retaining its `/repo` source policy. It must reproduce the old accepted
    host branch with the host input absent from source identities. Restoring
    the decision must reject the same query. A post-hoc `.d` parser is not
    a repair, because `__has_include` need not emit the host probe at all.
13. For joined/separate `-I` and `-iquote`, try `=`, `=include`, `=/include`,
    `$SYSROOT`, `$SYSROOTinclude` and `$SYSROOT/include`: reject all before
    a payload launch. Compare real ordinary/confined success with canonical
    literal names containing those characters internally. Keep `./`, `..`
    and explicit `--sysroot` negatives. This closes profile ambiguity; the
    observed default sysroot is empty, and the ordinary nonempty-sysroot
    controls do not establish another confined escape on that default.
14. Read a real repository `before.h`, then use
    `__has_include("/etc/ld.so.cache")` to select `present.h` or `absent.h`.
    Where the host cache exists, ordinary GCC selects `present.h`; D must
    reject the source probe instead of treating its bootstrap-view absence
    as source evidence. Exercise the other negative loader-probe names too,
    including ones genuinely absent on both sides. Restore the old
    path/operation-only negative rule in an owned mutation and require that
    the ordinary/confined branch mismatch reappears.
15. Keep real loader `ENOENT` probes and driver specs/search metadata working.
    Bind each exception to the actual executable and mapped syscall origin:
    resolved interpreter for loader probes, verified driver/driver-or-libc
    for driver metadata. Spoof a claimed executable, PID or instruction
    pointer and require rejection. Changing only mapping pathname labels
    must not change the result; invalid mapping identities must reject.
16. After `before.h` has actually been consumed, read an admitted libc image
    and retain ordinary-identical dependency results. Keep an explicit
    `Command.directories` enumeration positive and its undeclared-content
    negative. These are intended capabilities; do not close every runtime
    grant after the first source access or reject all directory declarations.
    Unexpected owned cache files/directories, source reads of specs or
    directories, and unlisted neighboring names must remain denied.
17. Return malformed trusted-runtime listings to the existing Make and
    dependency callers. Both must fail terminally before candidate payload
    execution with owned cleanup. A shared diagnostic is not proof of a new
    source-authority defect.
18. In the owned mapping-record models, replace the matching mapping offset
    with malformed, unsupported-range, unaligned and beyond-image values.
    Require rejection while retaining the actual PID, stopped instruction
    and executable. Substitute a valid interpreter device/inode into the late
    libc mapping record: its implied instruction position must fit that image
    and match its bounded file bytes, not merely its pathname or identity.
    Restore only the old omission of offset/span verification and require the
    modeled acceptance/mismatch to reappear. Separately use actual owned file
    descriptors to reject a substituted object, wrong instruction bytes and
    a file changed during the bounded read, with descriptor cleanup.

### Expected result

Actual confined cc/cc1 execution produces one declared dependency file with
ordinary-identical bytes and execution-bound source/header provenance. P's
validated publication precedes authentic GNU Make consumption and restart.
The default API, full metadata, noexec source, protected channels, exact
handle/output checks and one aggregate lifetime remain intact. No compiler
permission, numeric cap or CI topology is broadened.
Host access is decided against D's finite necessary runtime before either
generic runtime prefix branch; other host preprocessing cannot become an
unreported input or a falsely successful missing-header branch.
Negative exceptions additionally require verified executable/mapping purpose
at the actual stopped syscall, while late admitted runtime-file access and
explicit directory capabilities remain valid.
The matching map's bounded file offset and instruction span are checked
against the revalidated opened runtime image. Kernel/procfs remains trusted;
this is bounded consistency verification, not a general hostile-kernel claim.

### Negative control and retained evidence

At parent `974b1400c978892814c4bbefbdf0ec68e600f151`, constructing a command
with `dependency_only=True` fails because the keyword does not exist.
The original [host compiler checkpoint](https://github.com/laqieer/fireemblem8-expansion/issues/206#issuecomment-5563209086)
also records ordinary host availability and the public unsupported-command
rejection with zero payload launches. Preserve that default-false rejection,
not a fake compiler result. The original integrated d9 comparisons remain
historical, unapproved evidence rather than a restoration source.

At `11848c4e0294d6397eda6733850dfb382a1d0a1b`, real
`#include "/usr/include/linux/version.h"` produced ordinary-identical
dependency bytes and successful driver/cc1 receipts, but the source/header
union contained only `src/query.c`. A real `__has_include` query selected
`quote/enabled.h` while omitting the host probe even from the `.d`.
The host decision now rejects both; the owned mutation retains this original
negative without changing host files or weakening generic compiler tests.

The distinct remaining purpose defect at
`b05f5c871fdce9ee9dbf31301c744dc46dad4595` accepted
`query.o: query.c before.h absent.h` where ordinary GCC returned
`query.o: query.c before.h present.h`. The kernel's cache `ENOENT` was real;
the error was using loader absence as a source-level fact after `before.h`.
The purpose correction rejects that source probe while preserving bootstrap
negatives. Admitted runtime files after source reads and explicit directory
declarations remain by design, not additional defects.

The unmodified `bba829e5d8081c5bcb0170ca3dbc0c31862d9e0d` purpose guard rejects
the real cache-source mismatch. Its narrower mapping-consistency controls
accepted malformed/impossible offsets, and an injected valid-image identity
at a late libc IP implied offset `1159843` beyond a `236616`-byte nominated
interpreter. Those measured values are historical fault-injection evidence;
the tests derive current mapping/image values rather than pinning a distro
version or claiming a candidate-C/kernel exploit.

### Interactions and save compatibility

D depends on P/#225, merged through
[PR #232](https://github.com/laqieer/fireemblem8-expansion/pull/232), and
delivered core/#206. D/#233 now targets `master` as a root delivery unit at
depth zero. It has no V/#226 or R/#227 dependency. P's post-merge verification
and issue closure remain separate Main-owned gates; nested generated publication
and complete #180/PR #186 CURRENT/BASE/domain integration remain separate.

No manual criterion, gameplay, save/config, locale, generated game output,
ROM/RAM, modern/archival profile, package or publisher change is involved.

### Automation

Run the complete mapped host case:

```bash
python3 -m unittest scripts.validation_ownership.tests.test_dependency -v
```

The suite executes the real compiler and Make paths described above, including
their positive, rejection, resource and cleanup controls.

### Cleanup and limitations

Tests reset their owned fixtures and clean session state, processes, caches,
private outputs and generated publications after success or failure. Runtime
uses the existing read-only host model with D's finite runtime/source
decision, not an immutable runtime binary snapshot. Generic compiler/native
policy is unchanged. Unsupported compiler platforms/modes fail closed.

## TC-WORKFLOW-AGENT-HANDOFF-001: Validate bounded exact-SHA agent handoffs

- **Feature / originating issue:** `workflow-governance` /
  [issue #178](https://github.com/laqieer/fireemblem8-expansion/issues/178).
- **Supported configuration or artifact:** source-only Linux checkout with the
  [locked host Python environment](../workflow-pilot.md#isolated-host-python-dependencies),
  Git and the reviewed v3 handoff tools. No token/live workflow, ROM or emulator.
- **Prerequisites and clean starting state:** use the exact source root and
  #176 baseline; create/probe #216's owned environment as documented. The
  fixtures allocate only their own `build/test-artifacts/agent-handoff-*`
  directories and harmless child processes. Do not clean another owner's data.

### Actions

1. From the source root, run the focused suite with the inherited interpreter:

   ```bash
   build/host-python/bin/python3 -I -c 'import sys, unittest; sys.path.insert(0, "."); unittest.main(module=None)' \
     scripts.workflow_pilot.tests.test_agent_handoff \
     scripts.workflow_pilot.tests.test_coordinator_observations -v
   ```

2. Inspect the real-Git positive fixture: exact clean strict descendant, named
   evidence, real raw-check exit/PID, measured owner exit/RSS, distinct native
   dispatch/receipt/progress/delivery and Git commit observations. One and
   multiple task commits pass. Merge an authorized exact upstream input with
   no task trailers, plus a task commit with trailers: imported paths/history
   pass without consuming task scope/line budget. An unrecorded merge rejects.
3. Apply stale/wrong-parent/non-HEAD/unrelated result, dirty/conflicting tree,
   missing trailers/evidence/checks, out-of-scope path, hidden index flags,
   whitespace/checker replacement, and incremental line-budget controls.
   Remove only the failing condition and confirm success returns. Candidate
   `passed` fields and printed `exit 0` cannot replace a failing OS exit.
4. Exercise actual parsed protocol and resource inputs. Unknown/non-host
   resources, unquantified binary changes and ROM/RAM/protocol overages reject.
   Schema/runtime controls reject unknown fields, duplicate scope/keys,
   bad timestamps/types/enums, oversize/deep input and nonregular/symlink
   metadata. Equivalent JSON ordering stays equivalent.
   Send integral decimal/exponent integers through the byte API and real
   isolated CLI: schema and runtime agree, and a reloaded actual PID is an
   integer usable by the process adapter. Boolean/fractional/nonfinite/
   out-of-range values reject, including fractions that would round to an
   integer. No custom schema validator or per-field float bypass is used.
   Use actual non-host mode-only, regular-to-symlink and empty-file add/delete
   commits whose numstat is zero: missing resource observations must reject.
   Actual linker observations of zero pass; nonzero growth rejects an
   insufficient budget and passes with sufficient limits. Host-only zero-line
   changes and pure authorized imports with no task-owned paths still pass.
   Create/delete a valid `null` protocol file: each consumes one change;
   absent/absent and unchanged `null` consume zero. Preserve positives for
   object key order, integral numeric spelling and equivalent Unicode escapes;
   Boolean-to-number substitutions and changed array order must consume a
   change. Valid Unicode input within the existing byte bound stays valid even
   when escaped re-encoding would be larger.
   Partition the same changed inputs among multiple disjoint protocol checks
   and reorder their declarations: the assignment total stays the same.
   Fully or partially overlapping input definitions reject through assignment,
   loaded-state and CLI admission; ordinary schema validation does not claim
   this runtime-only cross-record check. Missing/impossible per-check counts
   cannot be hidden by other check measurements.
   Send escaped lone high/low surrogates through text/path fields: independent
   Draft 2020-12 and the actual byte API/CLI reject. Ordinary Unicode,
   supplementary characters and valid escapes pass, including after a
   CLI round trip. Rejected CLI input leaves canonical state unchanged.
5. Exercise dispatch-only and received-only native event streams. Confirm
   the matching session's receipt needs both the assignment marker and
   `data.parentAgentTaskId` equal to the dispatched task ID. Wrong-task,
   unrelated, absent or opaque identities/content leave receipt incomplete.
   `subagent.started`/transport success infer nothing; actual tool start
   advances progress once. Replayed event cursors cannot multiply metrics.
   Put receipt, turn, progress and delivery before the first matching
   `session.start`: none may acquire its later context. Repeat within a batch,
   across incremental calls and at the 128-event boundary; valid subsequent
   events with retained cursor context still complete normally. An old session
   start may establish context, but stale pre-assignment task events must not
   acknowledge or count the new assignment. Exercise the actual CLI and check
   that a future-clock rejection leaves canonical state unchanged.
   Real process exit/RSS pass; opaque/reused identities, missing complete RSS,
   lifetime/RSS overage and repeated committed-owner work do not pass.
   Deliver a real committed checkpoint, then observe owner exit 7 or SIGKILL:
   local acceptance and an accepted report reject it, preservation happens
   before retirement, and Git HEAD/index/check facts remain intact. A missing
   owned exit observation never becomes zero. Actual zero-exit completion
   still passes; immediate WIP publication is a separate coordinator decision.
   Compare captured assignment-to-close lifetime just below, exactly at and
   one microsecond beyond the limit while actual process age stays small.
   Live admission and historical accepted reporting must agree; retained valid
   history still reports after removing only its fixture worktree. Advance a
   controlled clock past the limit after a successful real raw check: final
   acceptance must reject, with close and verdict using one timestamp.
   Missing/future/negative lifecycle times and an open report clock predating
   assignment never become fabricated zero elapsed time.
6. Reserve duplicate/overlapping owners/watchers and attempt a second state
   writer: each rejects. Try different watcher IDs/runs with the same actual
   boot/PID/start identity, both through reservation and loaded state: reject.
   Distinct live processes pass; an owned unreaped zombie cannot reserve a
   watcher. Running/ended records must agree with process state and exit data;
   completed observations with unknown exit/RSS remain valid and reconcilable.
   Reuse a dispatch ID with otherwise fresh assignment/owner/session IDs in
   a review or independent reservation, and mutate a loaded state similarly:
   reject. A genuinely fresh dispatch remains valid, and the existing other
   identity-uniqueness controls still reject duplicates.
   Record an implementation-owner prohibited remote
   action and confirm rejection. This is operational/role-policy checking,
   not authentication against a hostile same-UID process.
   SIGKILL only an owned writer after partial and complete staging writes:
   canonical bytes stay intact and repeated new transactions succeed without
   promoting the interrupted state. Legacy staging, user files and symlinks
   stay untouched; forced exclusive-create collisions fail safely and can be
   retried with a fresh staging name. A failed replace preserves canonical
   state and removes only the current transaction's staging.
7. Use the exact GitHub run-response fixtures through the production query
   adapter. Watcher timeout plus authoritative success stays success;
   failure/cancellation stays failure, in-progress stays pending, API errors
   and wrong run/attempt/head stay unknown. No watcher status substitutes for
   the run. A replacement cannot overlap a still-live watcher. Report multiple
   runs/attempts on one head: a later success must not erase earlier failures,
   cancellations, pending work or a query error (even after an older success).
8. Kill only the fixture's owned child and inspect inert matching kernel
   evidence. Preserve exact staged/unstaged/untracked bytes, mode and index;
   confirm the original linked worktree is locked and reused by one replacement,
   with the running check incomplete. Missing OOM authority stays unknown.
   Move interruption time before assignment, after close or after replacement;
   remove its close or put both close/interruption in the future: reject in
   loaded state, reporting and replacement admission without changing retained
   file/index/lock bytes. Actual interruption matches close and yields positive,
   never negative, replacement recovery cost. Independent schema checks the
   required close, while time ordering/equality remains a runtime contract.
   A live owner, lost retention state or second replacement rejects without
   resetting/deleting the worktree or copying it into a new recovery engine.
   After completion or interruption, try a fresh `initial` assignment for the
   same issue or PR, including a different clean worktree: reject in live and
   loaded state. The real review/replacement lineage and independent issue/PR
   initial assignments still pass; a null PR does not merge unrelated issues.
   Change staged, unstaged or untracked bytes, index or file/directory modes
   while retaining the same HEAD/status pathnames: reassignment must reject.
   Restore the exact data/modes in the owned fixture and genuine reuse passes.
   Check the closed integrity field with independent schema/runtime mutations.
   Large clean committed content and external symlink target bytes are not
   read into recovery integrity; changing the link text itself rejects.
   FIFO/over-budget input holds with the original work and retention lock
   intact and the owner not closed; retry after restoring safe observability.
9. Exercise explicit availability plans and always-on observations. Enabled
   stop triggers without a plan, expired/future coverage and a detected
   suspend/boot change reject. A declared plan is not an uptime guarantee.
10. Execute the [documented production CLI](../workflow-pilot.md#coordinator-integration-and-commands)
    against the generated state/result: clean positive exits 0; the dirty
    negative exits 2. Removed publication verbs reject. Exercise optional
    reporting, confirm stale/incomplete and unknown RSS remain visible, and
    confirm baseline v1 without the option remains unchanged.
    Starting from a genuinely accepted handoff, mutate captured ROM/RAM and
    protocol measurements to missing, null, wrong types or over budget; also
    remove checks/evidence or change identity, completion and times. Both live
    validation without re-running checks and accepted reporting must reject.
    Independent partial ROM/RAM observations may together cover one global
    growth measure; repeated observations do not multiply that growth.
    Honest rejected records with unknown resources still report as rejected.
    Capture justified host-only/pure-import zeros, then remove only the owned
    fixture worktree: complete historical host/import/non-host observations
    still report without Git access. A missing captured zero must not be
    reconstructed from a verdict label or allowed scope.

### Expected result

Only an exact, clean, scoped, measured local handoff is ready. Task-owned
first-parent commits carry both Copilot trailers; authorized imported upstream
history does not need this session's trailers. Each lifecycle state has a
separate actual observation. Ownership and watcher reservations are exclusive,
the implementation owner retires, and one interrupted-worktree replacement is
bounded. GitHub remains CI authority. No validation result publishes anything
or satisfies the separate full-PR/final delivery gates.

### Negative control

Each adversary above fails or remains explicitly unknown/incomplete.
Runtime-check mutation must fail even if command/pass labels are preserved.
Equivalent source/JSON wording or order does not affect semantic evidence.
There is no disabled gameplay profile: omitting optional handoff reporting
preserves the baseline v1 report. No manual-only criterion applies.

### Interactions and save compatibility

Depends on #176 and #216/#217's locked test environment; dependent #181.
#179 is independent and #205/#211 is not required. Conflicts are stale/duplicate
ownership and the removed broker format. Preserve #199 metadata events,
#207 immediate publication, #208 cleanup and all original final gates.
No ROM/RAM content, save/configuration, locale, generated game data, modern
debug/release or archival behavior changes.

### Automation

The focused command above runs real Git/process/raw-check/CLI tests plus
independent Draft 2020-12 positive/adversarial schema validation.
`test_reporter.BaselineFixtureTests` and the existing isolated-lifecycle
launcher test cover unchanged v1 neighbors. The existing development-workflow
and catalog tests check both case mirrors and the public CLI mapping. Full
Build CI remains the comprehensive integration gate; no ROM build or broad
local suite is needed for this source-only change.

### Cleanup and limitations

Each fixture removes its own directories and reaps only its own children.
No real OOM pressure, Dev Box setting, unrelated process or GitHub state is
changed. Raw kernel/GitHub responses use inert fixtures at external boundaries;
the production file/process/query adapters themselves run in the tests.
Opaque runtime handles cannot certify exit/RSS; a missing real observation
remains an explicit hold, never an authenticated label or invented backend.
Revert the dedicated #178 change on regression.

## TC-WORKFLOW-OWNERSHIP-MODERN-TOOLCHAIN-001: Execute the original modern toolchain prerequisite

**Issue:** [#180](https://github.com/laqieer/fireemblem8-expansion/issues/180),
[frozen adapter contract](https://github.com/laqieer/fireemblem8-expansion/issues/180#issuecomment-5712094798).
This is a confirmed missing ownership-probe execution profile, not a defect
in the modern build recipe. The preserved bounded original-root preimage
reaches the actual order-only toolchain prerequisite after text production,
then refuses `registered command has unconsumed active shell syntax`.
The earlier five-step header-only fixture omitted that separate prerequisite.
Neither fixture is a complete repository graph or resource certificate.

### Supported profile and clean starting state

Use the existing Linux x86-64 Foundation/native ownership environment:
GNU Make 4.3, Python 3, namespace/watchdog support, the installed trusted
`arm-none-eabi-gcc`, its actual `cc1` and ARM assembler, and newlib C headers.
No ROM, emulator, archival compiler, additional test framework or remote
workflow is needed. Install nothing unless an actual dependency is missing.

The fixture creates its own ignored `build/test-artifacts` projection. It
extracts the complete original toolchain recipe, header order-only
prerequisite and five-step header/include rule directly from `modern.mk`,
and copies genuine repository headers. Generated header-output parents
start absent; it does not precreate message C or replace the original check
with a benign command. Default release/AAPCS and focused debug/APCS-GNU
inputs are supported.

The setup-only null-mount controls below additionally require Linux x86-64
with `mount_setattr`, a nonzero invoking UID/GID, and unprivileged user/mount
namespaces with util-linux `--map-current-user --keep-caps`. They do not run
Make, a compiler, a native supervisor, a root report or SDK discovery. Their
private fixture is bounded to 1 MiB, output to 256 KiB and the independent
watchdog to 30 seconds. Run them fail-fast; no sudo, new device nodes, host
mount changes or permission fallback is part of this regression.

The separate [O1–O6 custody prerequisite](#bounded-outcome-custody-prerequisite-o1o6)
only preserves bounded observations across the existing C/L cleanup boundaries.
It implements neither the restricted backend nor trusted R/N/W code. Its mock
and owned-pipe evidence does not qualify creation/entry policy, the old-remount
control, actual selective helper I/O/denials, or any of these seven modes.
Keep startup failures (including actual outer 125 and missing custody) failing.
The future restricted bootstrap still requires its own technical freeze,
representative host mechanism proof and every original effect/denial/cleanup.

### Actions

1. From the source root, run the command under **Automation** below.

2. Compare ordinary execution of the original
   `expansion-modern-toolchain-check` recipe with **each** original native
   prerequisite invocation, including the real Make restart. Require equal
   stdout, stderr and successful status, not a successful empty mapping.
   The native results must identify the real driver queries (`--version`,
   `-dumpmachine`, `-print-prog-name=as`), the actual `arm-none-eabi` target
   and executable ARM assembler, and these exact compiler inputs:

   ```c
   #include "global.h"
   ```

   ```c
   void modern_arm7tdmi_thumb_probe(void) {}
   ```

   Each input includes its original final newline. Require actual GCC/cc1
   syntax-only execution with the original ordered architecture, language,
   macro, include and driver arguments; then actual GCC/cc1/as execution of
   the original freestanding Thumb/interwork compile to `/dev/null`.
   Inspect authenticated executable identities, consumed repository/SDK
   identities and actual stdin observations. Source contents and complete
   file stat results must remain unchanged.
   Repeat in a genuine immutable selected view: reused repository headers may
   be hardlinked to the prior immutable snapshot, but retain exact captured
   bytes and view-bound authority. Captured SDK copies remain singly owned.

3. Exercise debug/ABI settings and ordered define/undefine/include controls.
   A conditional header must select the first actual include directory;
   reversing the macro's final value or selecting the other header must not
   be mistaken for the same behavior. Compiler warnings must reach the real
   Make stderr relay. A genuine controlled syntax error must preserve the
   original error text, exit the recipe unsuccessfully, and fail native Make.
   An explicit Make ignore-error rule may retain Make's own zero exit, but
   must never turn that failed required check into an ownership certificate.

4. Exercise `-B/usr/bin/` and `-B/bin/` using their real resolution results.
   These flags do **not** imply that the selected assembler is ARM. On
   systems where they select the host `as`, ordinary compilation fails and
   the confined adapter must explicitly refuse that actual assembler before
   executing it. Do not replace it with a working ARM assembler, substitute
   host C, expand executable search roots, or report that case as a positive.

5. Remove only the adapter route and require the original native
   unconsumed-shell-syntax refusal; restore it and require genuine success.
   Change the driver, target test, recipe/failure branch, flags, stdin,
   repository/SDK input, include search, exported environment, executable
   list or owned launch namespace. Each rejects before an unauthorized
   effect, or preserves a genuine compiler failure. Missing, copied, forged,
   replayed, cross-session and expired capabilities must exercise actual
   launch denial, not merely compare object fields.

6. Independently disable meaningful enforcement boundaries and run their
   corresponding adversarial regressions. Those regressions must fail;
   restore the boundary and require them to pass. Equivalent literal quoting,
   whitespace and parsed-record ordering changes must remain green.
   Failed fixture drafts are diagnostic development records, not preserved
   production-defect evidence or successful validation.

7. Run the separate setup-only command under **Automation**. In a private
   outer user/mount namespace, keep the invoking nonzero self-mapped IDs for
   setup, then drop all five capability sets and set NNP before the inner
   root mapping. Bind existing null/zero devices; do not create devices.
   Compare inherited readonly and ordinary writable parent cases using the
   actual production helper. Require the same null object and mount ID,
   exactly `flags_after = flags_before & ~NODEV`, and no change to readonly,
   nosuid, noexec or unrelated attributes. After the privilege drop, null
   returns EOF and accepts one tiny write; readonly regular source/runtime
   writes and the other nodev-protected device remain denied.

### Expected result

Each supported ordinary and confined invocation agrees on stdout, stderr and
status while preserving the original source bytes and file metadata. Actual
compiler, input and SDK observations support the result; a failed required
check cannot become an ownership certificate even when Make ignores its exit.
The setup-only null exception preserves the inherited mount restrictions and
the exact device/mount through a single nonrecursive attribute transition.
No descriptor, process or private mount/fixture remains after the controls.

### Negative control

Removing the adapter restores the original refusal. The invalid inputs and
capabilities in steps 4-6 must reject or retain the genuine compiler failure;
removing an enforcement boundary must make its adversarial regression fail.
Equivalent literal quoting, whitespace and record ordering remain supported.

#### Shell lexical-role regression

The [lexical-role correction](https://github.com/laqieer/fireemblem8-expansion/issues/180#issuecomment-5714258206)
retains this case and its existing full-module automation. In the owned
`ModernToolchainTests` fixture only, change the first `if [ -z "$$cc_path" ]`
to `'if' [ -z "$$cc_path" ]`. Ordinary Make must report the real shell syntax
failure. The confined probe must reject **before any toolchain substep**,
not run the compiler against a different interpretation. The preserved
pre-fix native comparison incorrectly succeeded, executed stages 0-4 twice
and generated the header dependency. The owner's persistent reproduction is
distinct from C's original inline review executions.

Exercise quoted, partially quoted and escaped structural words at every
required `if`/`then`/`fi`, `case`/`in`/`esac`, grouping and pipeline-negation
position. Check real operator and redirection-descriptor roles too.
Quoted `'!'` remains valid inside `[ '!' -x ... ]`, where it is an argument;
`if '!' ...` is not pipeline negation. Actual shell execution, not just
`sh -n`, must distinguish these behaviors.

Require an unquoted compiler assignment name and equals sign. For example,
`cc="arm-none-eabi-gcc"` and `cc=''arm-none-eabi-gcc` remain valid, while
`'cc'=arm-none-eabi-gcc`, `c''c=arm-none-eabi-gcc` and
`cc\=arm-none-eabi-gcc` are not assignment words. Case patterns retain active
wildcards: `'/'*` and `/''*` are equivalent to `/*`, but `'/*'` and `/\*`
match a literal asterisk. Likewise `''*` preserves the fallback wildcard;
`'*'` does not. Test absolute, relative and literal-asterisk inputs so a
working default assembler path cannot hide a changed branch.

The valid-quoting native control combines quoted assignment values,
`'exit' '1'`, test-argument negation and equivalent case-pattern quoting.
It must preserve ordinary stdout/stderr/status, actual GCC/cc1/as execution,
stdin, repository/SDK identities, unchanged source bytes/stat and cleanup.
Restoring only the old role-erasing signature must recover false native
admission for the keyword, assignment and pattern controls. Existing
whitespace and parser-local refactors remain green; no blanket quote ban,
general shell evaluator, source identity gate or new execution grant is added.

#### Inherited readonly null-device regression

The [selective-transition contract](https://github.com/laqieer/fireemblem8-expansion/issues/180#issuecomment-5724379393)
and [nonzero-parent witness amendment](https://github.com/laqieer/fireemblem8-expansion/issues/180#issuecomment-5724703509)
retain this case. The closed hosted root17 failed at the old full bind-remount;
its exact kernel lock/LSM branch was not recorded. The local pre-edit evidence
has three cumulative setup attempts, not three successful transitions: the
first parent-UID0 setup stopped at its nested UID map, then the amended old
and selective arms qualified. Preserve that distinction and the actual local
groups/maps; local namespace identity is not a hosted observation.

Restoring only the old `0x102a` remount must reproduce real `EPERM` on the
readonly-parent fixture. The actual selective helper must still succeed on
that fixture without clearing readonly. A wrong device must fail before the
attribute call. A same-null replacement mount at the syscall boundary must
not receive the transition: its mount ID changes, it remains nodev-protected,
and the helper reports substitution. Controlled `ENOSYS` and locked-attribute
`EPERM` return unchanged state and no remount fallback. Unissued launch/config
controls and the original root-setup cold-import/recursive-attribute refusal
remain required. These controls neither repair nor rerun SDK/resource work.

### Interactions and save compatibility

Dependencies are the delivered native dispatch/runtime-tool/C-SDK foundation;
full-root per-pass interpretation and terminal file-ownership integration
remain separately owned requirements. Selected journal profiles still reject
nested Make. No gameplay, ROM/RAM, save, localization, generated-content
format, archival or modern build behavior changes.

### Automation

```bash
python3 -m unittest scripts.validation_ownership.tests.test_toolchain_runtime -v
```

Setup-only selective-null coverage, without the compiler suite:

```sh
python3 -m unittest -f \
  scripts.validation_ownership.tests.test_foundation.FoundationTests.test_toolchain_null_mount_preserves_readonly_and_writable_parents \
  scripts.validation_ownership.tests.test_foundation.FoundationTests.test_toolchain_null_mount_rejects_wrong_and_substituted_devices \
  scripts.validation_ownership.tests.test_foundation.FoundationTests.test_toolchain_null_mount_failures_have_no_remount_fallback \
  scripts.validation_ownership.tests.test_foundation.FoundationTests.test_toolchain_null_old_remount_restores_readonly_rejection \
  scripts.validation_ownership.tests.test_foundation.FoundationTests.test_toolchain_null_exception_requires_an_issued_launch \
  scripts.validation_ownership.tests.test_foundation.FoundationTests.test_recursive_mount_attribute_failure_has_no_top_only_fallback \
  scripts.validation_ownership.tests.test_foundation.FoundationTests.test_root_setup_rejects_unsupported_recursive_attributes_before_supervision -v
```

The runtime file belongs to the existing trusted verifier inventory and
ownership rule; this regression belongs to the single existing native suite,
not duplicated graph discovery. The case uses the existing tester registry.

### Cleanup and limitations

The existing `MakeCommands` selection is followed by an issued original-job,
session, snapshot, tree and namespace-epoch binding. Each internal compiler
step and one-shot launch also binds its original arguments, environment,
stdin and lifetime. A public `Command` has no arbitrary stdin permission.
The original command's driver identity is verified before and after the
complete recipe; every internal execution verifies its actual image.
There is no general shell, compiler, plugin/specs, prefix-search or source-write
grant, and the existing query/header-only profiles keep their separate
authority.

Only an absent original `TMPDIR` is mapped to the issued private `/work`
(the already exact `/work` spelling is also accepted). Other temporary or
compiler-search environments refuse. The selected syntax/compile launch may
use only the real bound null device and its owned assembly temporary; other
devices remain denied. The existing budgets charge all work, and original
private roots, children, waiters and capability registries are cleaned on
both success and failure.

The setup-only tests use their own unprivileged namespace/watchdog and exact
owned temporary fixture. After workers exit, namespace teardown removes only
their private mounts; descriptors and the fixture are closed/removed by their
independent owner. Never unmount, remove or change a host/global or another
worktree path. A setup, identity, restriction or cleanup failure is a failure,
not a skipped native proof. No source acceptance, diagnostic18 or new H1
allocation follows from these local controls.

No full graph resource,
provider/H1, managed-admission or remote-completion claim follows from this
component. Reverting the coherent adapter restores explicit refusal without
changing `modern.mk`.
