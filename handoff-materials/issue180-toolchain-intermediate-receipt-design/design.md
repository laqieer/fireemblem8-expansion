# Proposed stage-4 intermediate receipt and semantic identity contract

**Status: concrete design for Main's pre-edit freeze and independent review.
Not implemented or executed. No native allocation is granted by this document.**

All source references below refer to immutable
`61856581bc9859f59fdd938edc219fabc07cd6ef` at
`F/ownership-recipe-binding-context`, where
`F=/home/laqieer/.copilot/session-state/3df36741-371b-49d8-ab09-a70c1ff3bec0/files`.
This continuation only read source and retained evidence and created this fresh
design directory. It did not change source, previous evidence or its modes,
run tests/candidate code/native work, or inspect the independent D1 work.

## 1. Established fact, scope, and acceptance boundaries

The first returned Make observation in the now-closed pair contains **two
complete original-checker semantic records**. They differ only in these two
authentic compile descendant operands:

- `cc1`'s assembly output: `/work/ccL3VdjV.s` versus `/work/ccBRFQFs.s`;
- the corresponding assembler input, with the same respective names.

Actual producer slots and native job identities—not a sorted-record index—link
the records. Their other complete command semantic fields are equal.
This is sufficient fresh source618565 evidence of the component defect. It
does not require inventing the missing second Make observation.

The pair allocation was spent: two Make calls attempted, one returned. The
second failed in SDK policy initialization before the comparator. Its failing
SDK row and launch-time bound were not retained. This remains a separate
possible capacity/admission limitation, not a finding of corrupt SDK data.
There is no paired second sed observation and no recovered root20 operand diff.

**Accepted design objective:** give one closed original stage-4 checker
intermediate an independently validated physical execution receipt, and use
that proof to represent only its writer-output and reader-input operands by a
typed semantic role. Preserve every other existing semantic authority.

**Explicit non-goals:** fixing SDK admission, relaxing quotas, changing
bootstrap/lifecycle policy, normalizing kernel inputs or executable stat
identities, changing Make/source rules, executing ordinary recipes, caching a
prior checker result, implementing a generic identity filter, or declaring a
complete root/report affordable.

Component qualification can use **one genuine Make call with two internal
checker invocations**, after a separate execution freeze. Complete cross-page
and original-root acceptance remains later, separately contained work. Passing
the already-insufficient old two-observation host experiment is not a
prerequisite for implementing or qualifying this component, and this design
does not allocate another such experiment.

## 2. Existing hooks and the exact missing proof

| Current source | Existing authority / gap |
|---|---|
| `toolchain_runtime.options:72–111`, `parse_recipe`, `Controller.register:463–499` | Bind the original shell recipe, selected driver, supported architecture/ABI, source headers and actual scheduled Make checker job. Keep unchanged. |
| `Controller.launch:547–585`, `consume_launch:587–599`, `validate_launch:272–349` | Bind exact launch config, workspace identity, executable images, stage and one-use token. Keep these checks; version their wire contract as specified below. |
| `syscall_guard.Policy.toolchain_private_access:1939–1972` | Admits only the existing single stage-4 assembly pathname family; first object must be absent/driver-created, later access checks device/inode/type/mode/link count. Does **not** prove a successful creation syscall, writer/reader roles and content, or successful retirement. |
| `Policy.entry:2311–2332,2378–2421` / `leave:2715–2809` | Already see open/read/write/close entry and exit. Add narrowly scoped intermediate evidence here; do not grant new syscalls or paths. |
| `entry:2474–2502` / `handle_stop:3012–3034` | Capture actual exec argv/environment, validate executable transition and actual image. Derive child operand roles from these authenticated argv, not from string matching over a semantic record. |
| `handle_stop:2953–2981` | Observes real process terminal status before retiring its pidfd/Process. Add writer/reader/driver completion checking here. |
| `entry:2632–2661` | Currently handles unlink/truncate/links and related mutations generically. Add intermediate-specific preconditions and successful-unlink evidence without widening existing permission. |
| `write_report:3473–3535` | Existing trusted `accessed` records, accounting and terminal result channel. Emit the new typed receipt here only after successful stage-4 completion. No new side channel. |
| `make_probe._command:3484–3495` | Validates toolchain exec/stdin records and final empty output directory. Keep both; add receipt validation before a result can be projected. |
| `make_probe.produce:4151–4187` | Copies raw `ProcessOutput.runtime_probes` into semantic identity. This is the one semantic placement to change, only for an identity-issued completed checker result. |

The old pathname-family check remains a **permission restriction**, not
evidence that any matching word in argv is replaceable. It is not the proposed
normalization rule.

## 3. One chosen architecture

Implement within existing files; no new general subsystem.

1. `toolchain_runtime.py` owns the closed child-argument role parser, raw receipt
   schema/validation, immutable evidence envelopes and typed semantic projection.
2. `syscall_guard.py` owns one `_ToolchainIntermediate` state machine per
   stage-4 launch and the actual stopped-process/file checks.
3. `make_probe.py` carries immutable raw evidence separately from semantics and
   consumes a one-use controller-issued result capability before putting the
   projected identity into `dynamic_commands`.
4. Keep `_stable_native_context`, `_semantic`, graph variable scheduling, source
   phase interpretation and their call signatures unchanged.

The projection is not callable on a caller-supplied `dict` as execution
authority. A pure parser can validate a wire value, but only the live controller
can issue the result capability that permits `produce` to select its semantic
form.

## 4. Proposed types and APIs

These are proposed interfaces, not existing APIs or executable implementation.

### Pure typed protocol helpers in `toolchain_runtime.py`

- `compile_operand_roles(executions, profile, parent_argv, *, complete) ->
  CompileRoles`
  - Returns the authenticated creator/writer/reader execution sequences and
    `ArgOperand` descriptors.
  - Native exec handling uses `complete=False` on the actual ordered execution
    prefix; only already observed actors receive roles. Parent successful-stage
    validation uses `complete=True` and requires all three actors.
  - Each descriptor contains a role, syntactically derived argv index, and the
    exact original string. An index is an output of parsing, never an admission
    whitelist.
- `intermediate_record(values, *, profile, launch, executions, returncode,
  limits, reserve) -> bytes | None`
  - Parses exactly the new receipt kind from the existing `accessed` strings.
  - Returns immutable canonical bytes after strict validation.
  - Returns `None` for non-stage-4 or genuinely failed stage-4 execution only;
    an unexpected record on another stage, or an incomplete successful stage,
    rejects.
- `project_compile_identity(raw_probes, raw_intermediate, roles) ->
  ToolchainSemanticIdentity`
  - Pure data transform used **inside** the live sealing path.
  - It changes only the two parsed operands and adds the content-bound role
    summary in section 8. All untouched rows/fields retain their exact values
    and order.
  - Pure calling of this helper does not issue a grant.

Keep the existing `toolchain_runtime.records(...)` signature and raw
exec/stdin return shape. Existing callers/tests that inspect actual
`ProcessOutput.runtime_probes` continue to see raw string argv and real
executable stat identities.

### Data carriers

Append, with an empty default, to both frozen dataclasses:

```text
ProcessOutput.toolchain_receipts: tuple[bytes, ...] = ()
MakeObservation.toolchain_receipts: tuple[bytes, ...] = ()
```

Appending avoids shifting current positional constructor arguments.
The bytes are canonical typed envelopes, not executable tokens. This avoids a
mutable raw dictionary being silently changed after issuance.

`ProcessOutput` holds per-stage raw envelopes in stage order. The aggregate
checker result has exactly the actual executed stages' envelopes; it preserves
raw `runtime_probes` separately for current direct consumers.

`MakeObservation` holds one complete recipe envelope for **each acknowledged
checker invocation**, in actual producer order, even when two resulting
semantic command records become equal and the existing exact deduplication
emits only one.

### Controller result custody

Extend the existing `Controller` with two result registries, not another
execution engine:

- `_step_results`, containing `_StepResult` capabilities;
- `_recipe_results`, containing `_RecipeResult` capabilities.

Both capability types are identity-issued, non-value-equal, weak-reference
tracked objects. Their records bind:

- exact controller/session and owner thread;
- exact `Command` and `ProcessOutput` objects plus canonical bindings of all
  relevant result fields;
- snapshot object, source tree, namespace epoch;
- original `_RecipeGrant` and live `_LiveDispatch` object;
- stage, consumed launch scope/binding, native report identity, and raw envelope;
- one-use state.

Extend `_Step` with the exact consumed launch token identity, scope/binding,
expected owned report path, actual issued file/observation/count/write bounds
and deadline, and a completion phase:
`issued -> launched -> native-returned -> sealed -> consumed`. Its existing
`require_step` remains a **pre-launch** check and keeps rejecting
`step.launched=true`. A separate completion check verifies the active original
parent/stage/binding/images and the required post-launch phase; it must not
accidentally call the pre-launch checker after execution.

After `_sandbox_run` has passed its existing trusted-report shape, accounting,
executed-image, return-status and terminal completion checks, it alone invokes
`Controller.note_native_return(launch, completed, observed)`. This verifies
the retained consumed launch identity/expected report path and registers one
opaque `_NativeResult` on that exact `_Step`. It binds the exact returned
`completed`/`observed` objects and a canonical report digest. Do not insert an
opaque Python object into `observed` or change `_sandbox_run`'s returned tuple:
existing report-inspection/capture callers must still receive the same data
shape. `_command` obtains the token once through
`Controller.claim_native_return(step, completed, observed)`, which requires
those exact object identities, the bound digest and an unclaimed completion.
The token is not a native JSON field or caller-supplied `"validated": true`
marker. Missing, copied, replayed, altered or foreign native completion rejects,
including tests that replace an apparently well-shaped `ProcessOutput` after a
real launch. The fixed one-slot ledger retains references only until sealing;
it does not copy or archive the entire supervisor report.

Proposed methods:

- `seal_step_result(step, result, *, native_return) -> ProcessOutput`
  runs from `_command` only after `_sandbox_run` and all current raw result,
  input, runtime and output-empty checks have passed. It attaches canonical
  stage-envelope bytes, and registers an opaque completion capability for that
  exact returned object. It consumes `_NativeResult`, comparing the raw decoded
  rows/result to that exact native return; `step.launched` must be true and its
  phase must be `native-returned`. No second result can be sealed for the same
  launch. A pure parser accepting a dict does not create this token.
- `consume_step_result(step, result) -> ValidatedStage`
  runs inside `Controller.execute`, before its existing `finally` removes the
  `_Step`. It checks the exact object/binding/live context, consumes the stage
  capability, and preserves its raw bytes.
- `seal_recipe_result(grant, result, stages) -> ProcessOutput`
  runs after the existing ordered stage loop, driver revalidation and result
  aggregation. It proves that the aggregate is exactly the accepted stage
  results, retains their raw envelopes, computes the semantic form, and issues
  the recipe-result capability.
- `consume_recipe_result(command, result) -> RecipeEvidence`
  runs in `produce` **inside the existing `_command_dispatches` try block**,
  before that context is popped. It verifies current command/live dispatch,
  snapshot/tree/epoch, source inputs and exact aggregate/raw binding, and
  consumes the recipe-result capability.

The original `_RecipeGrant` may be removed in `execute`'s existing `finally`;
the short-lived `_RecipeResult` explicitly binds its original grant identity
and live dispatch, not a second lookup of the now-removed registration.
`consume_recipe_result` must not require that obsolete map entry, but must
require its issued result capability and the still-live dispatch. A copied
`ProcessOutput`, copied bytes, stale view, different context or replay cannot
substitute.

For a real nonzero checker status, retain raw stage results and the existing
stderr/status relay. Do not issue a projected intermediate unless stage 4
successfully completed its proof. The existing required-check failure still
prevents a successful Make ownership observation.

## 5. Exact native wire format and version boundary

### Launch version

Change the internal `dependency.toolchain_probe.version` and matching
`toolchain_runtime.version` from **1 to 2**, keeping their existing field sets.
For version 2, successful stage 4 mandates the receipt below; other stages
forbid it. Update both the caller issuer and trusted runtime validators
atomically. Version 1 is rejected by the new execution route; no compatibility
fallback can silently omit the proof.

Existing `header_runtime`, SDK, source-journal, Make observer, producer/publication
protocols and report top-level keys are unchanged.

### New `accessed` record

Prefix: `toolchain-intermediate:`

Exactly one successful stage-4 record, with `version: 1`:

```text
{
  version: 1,
  scope: <consumed toolchain launch_scope>,
  binding: <consumed launch_binding>,
  stage: "compile",
  role: "stage4-assembly",
  path: <one exact created guest path>,
  workspace: [dev, ino, mode],
  actors: [{
    exec_sequence, pid, birth_sequence, exec_record_sha256
  }],
  creation: {
    order, syscall_sequence, syscall, exec_sequence, pid, fd,
    flags, requested_mode, result, identity
  },
  creator_close: {order, syscall_sequence, syscall, result},
  writer: {
    exec_sequence, pid,
    operand: {kind: "output", option: "-o", argv_index},
    open: {order, syscall_sequence, syscall, fd, flags, requested_mode, result, identity},
    completed: {
      order, close_order, close_syscall_sequence, close_result,
      write_calls, written_bytes, extent, sha256, identity
    },
    exit: {order, result}
  },
  reader: {
    exec_sequence, pid,
    operand: {kind: "input", argv_index},
    open: {order, syscall_sequence, syscall, fd, flags, result, identity},
    completed: {
      order, close_syscall_sequence, close_result,
      read_calls, read_bytes, extent, sha256, eof_observed, identity
    },
    exit: {order, result}
  },
  retirement: {
    order, syscall_sequence, syscall, exec_sequence, pid,
    result, before_identity, after_identity, path_absent
  },
  driver_exit: {order, result},
  complete: true
}
```

Here:

- `identity` is the existing full seven-integer
  `publication_identity`: device, inode, mode, size, mtime_ns, ctime_ns, nlink.
  Workspace uses existing `directory_identity`'s three integers.
- `syscall_sequence` is actual `Policy.calls`; `order` is a bounded monitor-local
  transition ordinal that also orders process-terminal events.
- `fd` and `pid` are the actual observed syscall/process identities. Their
  authority is the still-owned tracee/pidfd and launch, not the numbers alone.
- Execution sequences index the **authenticated ordered execution rows**:
  derive driver/`cc1`/assembler by their exact launch images and transitions.
  Do not accept arbitrary receipt sequence numbers simply because they are
  1/2/3.
- `actors` has exactly the driver/writer/reader rows, formed at their actual
  successful exec stops. `birth_sequence` is allocated at actual owned
  `Process` creation/fork, not copied from its parent. Reuse the already encoded
  actual exec row to form `exec_record_sha256`. Validate this digest against
  the corresponding unchanged raw exec record. All operation pid/exec-sequence
  pairs must match this table, and the native tracker binds them to the real
  `Process`/pidfd while live. PID reuse cannot substitute a different birth.
  These actor observations stay in the raw receipt, not in semantic
  `runtime_probes`.
- Actual exec rows keep full executable identity, raw argv and environment in
  the existing `toolchain-exec:` records. They are not duplicated into the
  intermediate receipt.
- Every object has exactly the listed keys; unexpected fields, booleans in
  integer positions, duplicated records/roles, wrong version/stage/scope,
  unbound operand or missing mandatory component rejects.
  Duplicate JSON object keys must also reject rather than accepting a decoder's
  last-value-wins result.
- Successful ordering is strict:
  creation < creator close < writer open < writer close < writer exit <
  completed-content seal < reader open < reader close < reader exit <
  retirement < driver exit. `writer.completed.close_order` denotes the close;
  `writer.completed.order` denotes the later sealed-content measurement.
- Bounds: checked unsigned counters/identities below `2**64`; syscall/exit
  results are signed 64-bit integers and must equal their required successful
  value before acceptance (the actual created/opened FD or zero). Native fd
  `< 128`,
  syscall counts within the issued syscall limit, path within the existing
  4096-byte pathname bound, existing bounded scope, at most 512 JSON nodes and
  nesting depth 10, serialized intermediate record at most 64 KiB **and**
  the issued file/observation remaining bounds. SHA-256 strings are exact
  lowercase 64-character hex.

For a genuinely nonzero stage result there is **no successful intermediate
record**. Existing raw execution/stdin and error/status evidence remains. Do
not serialize an incomplete structure with `complete:true`, and do not derive
a role identity from a partial record.

## 6. Native state machine and actual object/content proof

### 6.1 Acquisition and role assignment

`Policy.__init__` allocates only a small unarmed tracker for a version-2 stage-4
launch; it opens no new FD during constructor validation. At the successful
initial driver exec stop, under `supervise`'s existing try/finally lifetime:

1. Recheck the existing empty workspace binding.
2. Open/pin that workspace with directory/nofollow/cloexec flags, verify
   `directory_identity`, and retain one owned directory FD.
3. Assign the driver's actor from the real exec event, its existing pidfd/
   `Process`, launch image identity and execution sequence.

New `Process.toolchain_exec_sequence` records an actual exec identity. Do not
copy it as an authenticated new actor in `Process.clone`: a forked pre-exec child
does not thereby acquire the creator/writer/reader role.
Assign its launch-local `toolchain_birth_sequence` when the supervisor creates
the owned `Process`, including the newborn-stop/fork ordering path, exactly
once. The tracker records the actual exec row digest and actor reference
together; a later bare integer tuple cannot acquire that actor's authority.

The tracker has exactly one object slot and one descriptor slot per current
actor role. It never selects a role by matching arbitrary filename strings.

Its transition states are exactly:
`armed -> created -> creator-closed -> writer-open -> writer-closed ->
writer-exited -> sealed -> reader-open -> reader-closed -> reader-exited ->
retired -> complete`. Unsupported or repeated transitions reject.

Add a single `Process.toolchain_pending` tagged record for the current
intermediate syscall; do not overload/replace existing `state.pending` for
open/close/general observation work. `Policy.entry` keeps current authorization
first, then calls the narrow tracker entry hook with actual pid, registers,
resolved path/FD and syscall role. `Policy.leave` verifies that pending record
at the real exit while the process is stopped, before the normal `state.fds`
update discards close/open information. Record exact raw pathname spellings and
dirfd bindings at entry and recheck them on exit where applicable. Successful
exec and process-terminal hooks advance the separate actor states.

The existing `toolchain_private_access` path gate is not removed or generalized.
The new proof hooks supplement it; they do not make an allowed path a proven
intermediate merely by appearing in that gate.

### 6.2 Closed child argv roles

Parse raw argv captured at `execve` entry; confirm them again at the actual exec
stop, using the existing canonical image validation.

- The driver invocation still passes the existing `options(..., syntax=False)`
  grammar, including stdin `-` and output `/dev/null`.
- For `cc1`, a bounded left-to-right option-arity parser consumes supported
  driver-emitted switches and option values. Require one positional source
  `-` and one distinct `-o` value; that value is the assembly **output role**.
- For the selected assembler, the parser consumes supported assembler switches
  and the single `-o /dev/null`. Require one positional source; that source is
  the assembly **input role**.
- Both role values must equal the actually created object's exact canonical
  guest path, not merely match the existing permission regex.

For the first implementation, close the child option vocabulary to the existing
checker profile:

| Actor | Zero-operand forms | Separate-value forms | Self-contained value forms |
|---|---|---|---|
| `cc1` | `-quiet`, `-mthumb`, `-mthumb-interwork`, `-ffreestanding`, `-fno-pic`, `-fno-pie` | `-imultilib`, `-isystem`, `-dumpbase`, `-dumpbase-ext`, `-o` | `-D...`, `-mcpu=...`, `-march=...`, `-mfloat-abi=...`, `-mlibarch=...`, `-mabi=...` |
| assembler | `-mthumb`, `-mthumb-interwork` | `-o` | `-march=...`, `-mcpu=...`, `-mfpu=...`, `-mfloat-abi=...`, `-meabi=...` |

Self-contained values must be nonempty single argv tokens; they are retained
verbatim, not resolved or normalized. Macro tokens are data, not operands.
The special option values `-imultilib`, `-isystem`, `-dumpbase` and
`-dumpbase-ext` are consumed as option values, never mistaken for a positional
input. `-isystem`, when present, remains the parent-admitted newlib selection;
it grants no new search root. Retain all ordering and duplicates that the closed
role grammar deliberately admits (for example two actual `-quiet` tokens).

Reject response files, `--`/alternate option syntax not in this grammar,
duplicate output options, extra positional inputs, unknown arities/options,
non-string tokens, or another use of the actual intermediate path in
non-role argv. Do not silently broaden this parser based on a failed run.
Qualification must include the supported release/debug and ABI emitted forms;
an unobserved compiler-specific form remains a precisely named compatibility
hold, not a permission fallback.

The table describes **argument roles**, not an index whitelist and not a
normalization-by-prefix algorithm. Recorded indexes 20/7 from the spent pair
are not constants in the implementation.

### 6.3 Actual creation

At existing `open/openat/creat` entry:

- Keep the current pathname-family restriction and all global syscall policy.
- Require the authenticated original driver, an empty object slot, exact
  canonical direct-child path under the pinned workspace, and actual absence.
- Require exclusive driver creation (`O_CREAT|O_EXCL`, ordinary regular file,
  read/write access, requested mode `0600`, no append/tmpfile/directory/path
  mode); permit only existing harmless descriptor flags.
- Reserve native creation/accounting and tracker evidence before growth or
  kernel dispatch. A collision or failed creation cannot cause another
  accepted path/object; the proof fails closed.

At successful syscall exit:

- Compare the tracee FD's actual kernel identity with a nofollow relative lookup
  through the pinned workspace.
- Pin the same regular file for supervisor evidence reads.
- Require size zero, exact regular mode `0600`, link count one.
- Save full creation identity and actual successful result. Only now is the
  object “created.” A metadata attempt never substitutes for this event.
- Require the creator FD's successful close before writer exec/open.

No new candidate file permission is introduced. The monitor's pinned read FD
does not become a candidate descriptor or a command input.

### 6.4 Writer and completed content

The authenticated `cc1` exec must designate this object through its parsed
output role. Its one output open must resolve the pinned object with write-only
ordinary file semantics, truncating the known initial empty file if requested;
an `O_CREAT` bit on this existing object grants no new object. Reject append,
exclusive replacement, aliases or another file.

During the writer interval:

- Only this exact actor/FD may write the object.
- Use actual successful sequential `write` results and offsets, not attempted
  byte counts as successful output. Existing write quota still charges attempts.
- The closed first implementation supports sequential `write` and `read` for
  this intermediate. Reject intermediate `pwrite/writev/pread/readv`, seek,
  descriptor duplication/fcntl mutation, mmap, truncate, chmod/chown/link/rename
  and inherited descriptor aliases rather than pretending they fit the proof.
  Generic behavior for all other paths/commands is unchanged.
- Reject a fork/exec carrying an open intermediate FD. Kernel state and
  `Process` tracking must agree; a numeric reused FD alone is not identity.
- Check full pinned object identity at boundaries, allowing size/mtime/ctime
  evolution only during the legitimate writer interval, while device/inode/
  type/mode/link count remain the same.

On the writer's successful explicit close, retain close evidence. Require real
`cc1` exit status zero at the process-terminal hook before a reader may exec.
Before releasing a reader/other mutating actor, stream the pinned completed
file under the existing deadline, with before/after `fstat` and nofollow
workspace-entry identity checks. Save:

- final extent, which equals total successful contiguous writes;
- actual completed-file SHA-256;
- full sealed physical identity.

The writer hash denotes **completed file bytes**, not an invented digest of
intended argv or a claimed write buffer. No assembly bytes are emitted.

### 6.5 Reader and same-content consumption

The authenticated assembler exec must designate the exact same pinned object
through its parsed sole input role. Require:

- writer closed and successfully exited;
- same sealed entry/object identity and single-link regular mode;
- one actual read-only open with no creation/truncation/path/alias flags;
- tracee FD and pinned FD identity equality at open and each data boundary.

Track actual sequential successful `read` returns from that FD. Charge the
requested byte bound before copying tracee memory; hash only bytes actually
returned, and retain whether EOF was actually observed. Verify offsets
monotonically from zero. Reject reads outside the extent or any non-reader
consumer of the assembly bytes.

At successful explicit close and real assembler exit status zero:

- total actual read bytes must equal the sealed extent;
- actual returned-byte digest must equal the completed writer digest;
- full object identity must still equal the sealed writer identity.

An explicit EOF read is recorded if it occurred, but full extent plus immutable
size/content, successful close and successful actor exit prove complete
consumption even if the program closes after reading the known full extent
without issuing one extra zero-byte read. Never claim `eof_observed=true` if it
was not observed.

### 6.6 Retirement and finish

Only the original authenticated driver may unlink this exact entry, after the
writer and reader exited and no candidate intermediate FD is open.

At unlink/unlinkat entry, pin/check parent, exact spelling, path-to-object
identity and sealed full content identity. Rehash the pinned content
incrementally and require the original extent/digest. Do not make an SDK or
source read to supply this check.

At successful kernel exit:

- require return value zero;
- require the original directory entry absent;
- require the pinned object's device/inode/type/mode/size/mtime still match,
  with link count now zero; retain the actual changed ctime rather than
  fabricating equality;
- record the exact successful retirement.

Require actual driver exit zero and no live actors/FD slots. Reuse the existing
empty output-directory check in `_command` as an independent final check.
Only then emit `complete:true`.

### 6.7 Race and cleanup boundary

Role access is checked at the **actual syscall stop**, not merely at registration.
Once writer completion seals the object, no admitted actor can mutate it except
the final driver unlink. Read/write entry and exit checks use the actual owned
process and pinned object; all threads/shared mutable mappings/foreign actors
remain under existing denials. Unsupported overlap, outstanding I/O, alias or
unexpected actor is a refusal, not a reason to suspend proof requirements.

This tracker does not replace the existing process reaper, budget owner or
private command-directory cleanup. Its cleanup closes only its own workspace
and pinned-file FDs and clears fixed monitor state. It never unlinks a candidate
file to manufacture successful retirement. Native failure still invokes
existing owned command-root cleanup.

Acquire the new FDs only after supervisor setup is in its existing owned
try/finally lifetime. Register `close_toolchain_intermediate` beside existing
private-install/header cleanup, including failure paths. This is local FD
ownership for the new proof, not D1 outcome-custody or lifecycle redesign.

## 7. Raw evidence custody, binding and no replay

### Stage envelope

`ProcessOutput.toolchain_receipts` contains canonical bytes for:

```text
{
  version: 1, kind: "toolchain-stage",
  stage, launch_scope, launch_binding,
  source_snapshot, namespace_epoch,
  workspace, images,
  admission: {
    file_limit, observation_count, observation_limit, write_limit,
    creation_limit, process_limit, memory_limit, syscall_limit, deadline
  },
  executions: <unchanged actual exec rows>,
  stdin: <unchanged actual stdin rows>,
  intermediate: <validated raw receipt or null>,
  result: {
    returncode, stdout_sha256, stderr_sha256,
    consumed, code_consumed, input_identities,
    executed, runtime_receipt, runtime_sources
  }
}
```

The envelope is created while the issued stage and parent recipe are live.
Its bindings must equal the controller-held consumed launch, not values
supplied by a caller. The exact native report identity joins the private
capability binding; it is not included in semantic identity.
The `admission` values are the actual per-launch values retained at consumption,
not reconstructed larger defaults. Frozen result bindings represent binary
stdout/stderr/artifact/generated data by exact length/digest plus their typed
fields; they do not attempt to JSON-serialize Python bytes.

At step consumption, check all result fields against the envelope and its
issuer record before deleting the capability. Raw bytes alone are not a
reusable grant. At recipe sealing, verify actual stage ordering, successful
completion requirements and equality of aggregate fields to their stage
aggregation. Do not infer an unexecuted stage from the original recipe.

### Recipe/Make envelope

After `produce` consumes the recipe capability, retain its raw envelope as
pending alongside that actual producer receipt. At the existing successful
publication acknowledgement, append:

```text
{
  version: 1, kind: "toolchain-recipe",
  make_scope, producer_slot, native_dispatch_sequence,
  native_job: <actual job and remake context>,
  source_snapshot, namespace_epoch,
  command_binding,
  stages: <ordered raw stage envelopes>,
  result: <actual aggregate fields and raw runtime_probes>,
  semantic_record_sha256: <acknowledged projected complete command record>
}
```

All recipe/command environment and status/input/output fields remain bound
either explicitly in the raw result or its existing canonical command binding.
`semantic_record_sha256` points from a raw occurrence to its validated semantic
record; it is never used to locate a “similar” record by command prefix.
Append only on the existing **new acknowledgement** branch after all checks,
not when `completed == confirmed` merely repeats the identical confirmation.
Thus protocol re-acknowledgement cannot manufacture another raw occurrence.
When stage envelopes are nested in recipe-envelope JSON, decode their already
validated canonical bytes into bounded JSON objects with charged copy/storage;
do not insert Python bytes or an opaque token into a wire object. The retained
outer envelope is again immutable canonical bytes. There is no implicit
whole-dataclass serializer.

Place its immutable bytes in:

1. the returned `MakeObservation.toolchain_receipts`; and
2. `ProbeSession._toolchain_receipt_archive`, a budgeted, data-only map keyed by
   the actual Make scope.

The archive keeps complete acknowledged raw occurrences even when additional
page observations fall out of Python local scope. It is not a cache or grant
registry. A bounded data-only accessor
`ProbeSession.toolchain_receipts(scope=None)` returns immutable envelope bytes
for an active session; it cannot issue commands, project arbitrary data or
revive old authority. Per-view entries retain their original snapshot/epoch.
View changes invalidate result capabilities; archive entries remain historical
data only until session cleanup.

Append the raw-sidecar digest to `_namespace_observation_context`'s
**execution-binding tuple**, so swapping a returned sidecar cannot preserve its
issued original-namespace/source-phase authority. Do not add that digest to
`semantics` or `semantic_digest`.

`Controller.close` and namespace expiry clear outstanding result capabilities.
`ProbeSession.__exit__` clears the receipt archive. External copies of the
returned canonical bytes may remain inert evidence after cleanup, just as the
existing retained JSON does; they are never accepted as a launch/result grant.

No automatic public raw-data publisher is added. A diagnostic may explicitly
retain this bounded sidecar locally under existing privacy rules; public graph
reports still contain semantic ownership information, not raw environments or
private execution paths.

## 8. Exactly what enters semantic identity

Only a consumed, identity-issued **successful recipe result** can select this
form in `produce`.

Add this versioned field to that command identity:

```text
toolchain_semantics: {
  version: 1,
  intermediates: [{
    role: "stage4-assembly",
    stage: "compile",
    type: "regular",
    mode: 384,
    bytes: <actual completed extent>,
    sha256: <actual completed and fully consumed content digest>,
    creator: {exec_sequence: <driver>},
    writer: {exec_sequence: <cc1>, operand_kind: "output"},
    reader: {exec_sequence: <assembler>, operand_kind: "input"},
    created: true, writer_completed: true, reader_completed: true, retired: true
  }]
}
```

In a copied **semantic** `runtime_probes`, replace exactly:

- the parsed `cc1` output operand; and
- the parsed assembler input operand

with this non-string typed value:

```text
{"kind": "toolchain-intermediate-ref", "version": 1, "role": "stage4-assembly"}
```

Every other argv element remains in the same position and unchanged.
All raw exec records in `ProcessOutput` and the sidecar still have the original
string operands. Mixed string/reference argv is permitted **only** in the
versioned semantic toolchain form, never in executable argv or raw wire exec
records. A dictionary in unvalidated raw argv must reject.

Keep unchanged in semantic command identity:

- all non-role argv and their order, including original checker driver args;
- every environment entry and byte;
- executable paths and all executable stat identities;
- driver/native/runtime tool digests, modes, aliases and executed sequence;
- repository and SDK input identities and modes;
- original stdin bytes and EOF, real returncode;
- actual stdout/stderr digests and transforms;
- directories, publication policy, produced and effective outputs;
- all non-toolchain runtime probes, especially sed statfs/proc data.

The intermediate's physical path/device/inode/times, descriptor/PID and lifecycle
ordering remain in the separately validated raw receipt. They are not a
repository input or exported artifact. Only their proven stage-4 role,
regular-file mode, content identity and completed dataflow enter semantic
equality. Its content digest is **not normalized**: if genuine assembly bytes
vary (including if they embed a temporary name), semantic inequality remains.

The new semantic shape expressly distinguishes this narrowly proven private
intermediate from executable stat identities and all other input hashes.
There is no general list of “unstable fields to ignore.”

## 9. Complete consumer/cache/report trace

| Consumer or stage | Required handling |
|---|---|
| Native execution config / `toolchain_runtime.validate_launch` | Version 2 issuer/validator agreement required. Launch binding still covers all actual args/env/mount/source/tool/profile fields. The completion ledger also retains the actual issued bounds/deadline, not a larger default, for receipt validation. No projected value enters execution. |
| `syscall_guard` report | Existing exact top-level result keys; new record travels only as one typed `accessed` entry. Existing report-count, byte, observed-exec and transport validation still applies. |
| `toolchain_runtime.records` | Remains raw; no path replacement, dropped environment, sort beyond its current authenticated sequence reconstruction, or changed caller signature. |
| `ProcessOutput` / direct native tests | `runtime_probes` stays raw. Appended `toolchain_receipts` field has default empty. Direct tests of actual args/identity/stdin remain meaningful. |
| `_command` cache (`make_probe:3191–3194,3294–3300,3543–3544`) | Parent checker dispatch bypasses ordinary cache by calling `Controller.execute`; all checker substeps already exclude `toolchain_step` from cache lookup/store. Keep those exclusions. New result capabilities/receipts are not cache keys or reusable execution results. Other commands' metadata revalidation is unchanged. |
| Runtime tool/profile caches | Exact tool/profile reuse stays separate from result authority. No copied workspace/launch/intermediate/result grant is retrieved from these caches. No SDK profile mutation is included in this scope. |
| `MakeCommands.__getitem__:376–399` | Keep deliberate per-job re-registration of the checker/header/text commands. A command spelling or previous registration is not a result capability. |
| Producer hash (`make_probe:4202–4208`) | Still the original typed command/code/input/output/tool/publication derivation. Do not insert ephemeral receipt hashes or role refs there to issue different publication authority. The checker has no public outputs. |
| `acknowledge` / `command_results` | Validate each raw invocation first; retain every raw occurrence; then existing hash-of-complete-semantic-record deduplication may collapse proven-equal checker records. No command-prefix filter or new loose deduplication. Native dispatch and raw occurrence counts remain two. |
| `MakeObservation.semantic_digest` | Hash the semantic dictionary with the versioned role form. Never hash raw sidecar, scope, temporary path, PID or archive locator into it. Execution/source binding separately includes raw-sidecar digest. |
| `native_dispatches` / `recipe_dispatches` | Leave entirely unchanged. They contain GNU Make's actual command contexts, not GCC's internal `cc1`/assembler argv. Keep environment, job, source-remake role and existing sequence behavior. Do not erase a differing native dispatch to make the guard pass. |
| `_stable_native_context` (`graph_probe:3940–3954`) | Exact comparison remains unchanged: graph structure, full dynamic semantics, then native recipe contexts. It is not the authority issuer and gains no “ignore these fields” branch. |
| `_recipe_domains`, `_graph_definitions`, template/native metadata helpers | Same genuine Make observations, page requests, shared state/factory and guard calls. Source-phase and literal/raw laziness contracts are untouched. Every returned page was independently validated before comparison; raw archive prevents loss of page receipts. |
| `_semantic` (`graph_probe:3927–3937`) | Existing owner/MAKEFILE_LIST/MAKE_RESTARTS handling only; no new stripping function. Raw receipts were never placed in the dictionary it copies. |
| `run_probe` variant aggregation (`:4240,4271–4300`) | Aggregate only the same semantic records/censuses. Do not add scope-keyed raw archive beside `record`, since reporter fingerprints the target authority, not just one nested field. |
| `reporter.parse_make_targets:1482–1493`, authority fingerprinting `:1641–1660` | Return/fingerprint the versioned semantic target authority only. Its existing v1 fingerprint domain need not change globally: explicit per-command semantic version distinguishes the changed contract and only affected targets change. Recompute affected evidence; old successful output is not approval for a new candidate. |
| `graph_report.check` | One session/CURRENT/BASE lifetime still governs execution. Data-only raw archive can survive a view switch but grants cannot. Public result remains fingerprints/summary/execution counters; no raw sidecar auto-export. |
| `consumer.check:51–57` | Its current explicit `semantics`/`semantic_digest` output stays stable-shaped; it does not serialize the whole dataclass or expose sidecars. |
| `phase_census` and original namespace/source archive | Existing file/dispatch interpretation is unchanged. New raw digest is bound only through `_namespace_observation_context`, so original per-pass authority cannot be swapped while maintaining the semantic digest. No final-value seeding. |
| Remade-include provenance (`graph_probe:3848–3891`) | Actual published versions, `generated_outputs`, command `inputs` and opaque-reader checks retain their current fields and behavior. The new private compiler role grants no include/publication authority. |
| Frozen harness `root_stage.toolchain_summary:122–148` | It reads runtime rows by stage, sequence, path and original stdin; these keys are retained in the semantic form. It does not parse child argv operands. `policy.validate_root_result:348–360` explicitly permits one or more semantic checker summaries up to the actual native job count, so proven semantic deduplication does not by itself break its count contract. The frozen harness remains untouched. A future harness requiring raw authority must explicitly consume validated raw envelopes; its existing summary is not a verifier for the new proof. |

The new `toolchain_semantics.version` is an internal semantic contract marker,
not a new gameplay/configuration flag. Existing graph/dynamics/test-case JSON
registries describe commands/ownership, not these ephemeral native rows; no
registry schema or whole-source hash ledger is introduced.

`ci_verifier.TRUSTED_RUNTIME_PATHS` already stages `toolchain_runtime.py` and
the other existing implementation files. No new trusted path is needed.
Update all internal version-1 launch constructors/tests together; reject a mixed
old/new trusted runtime rather than silently accepting missing evidence.

No inspected production consumer serializes the entire `ProcessOutput` or
`MakeObservation` dataclass as the public report. Their existing explicit
semantic serializers remain unchanged. Whole-dataclass equality/`asdict` is not
a semantic identity API: it now includes raw byte sidecars. Audit the existing
constructor/`replace` tests when appending the fields, and retain empty defaults
for synthetic/non-toolchain observations rather than coercing bytes into the
semantic JSON dictionary.

## 10. Pre-growth admission, costs and cleanup ownership

No `Limits` field increases, new SDK bound, permission, refunded budget,
second budget or replacement deadline.

### Native monitor

- At most **one object, three actor identities, three actor FD slots**, two
  supervisor-owned FDs and a fixed transition record. No unbounded per-syscall
  event list.
- Reserve the tracker/receipt upper bound **before** arming/creation. A 64 KiB
  receipt bound conservatively covers the one 4096-byte path even under JSON
  escaping, bounded scope, fixed identity arrays and numeric fields.
  Use the existing `observation_limit` and `observation_count`: one attempted
  reservation plus one emitted receipt is at most two new observation entries.
  Reserve byte overhead up front; the later normal `observe` charge is retained
  too. Do not refund the conservative reservation.
- Check count/byte capacity before constructing or appending the emitted
  record. Reserve its schema-directed encoded upper bound, not `len(encoded(x))`
  only after an unbounded object was allocated.
- Reader buffer capture requires
  `requested <= SYSCALL_MEMORY_LIMIT`; charge requested bytes before copying,
  hash only successful returned bytes, and never refund short/failed reads.
- Completed-writer and pre-retirement content hashing each cost the actual
  extent `B`, reserved before opening/reading. Stream at most 64 KiB at once;
  no full assembly-file copy. New content-observation cost is bounded by
  `2*B + sum(reader_requested_bytes)` plus fixed identity/offset/record overhead.
- Offset/FD identity reads are bounded and precharged (e.g. reserve the existing
  fdinfo 4097-byte maximum before reading, then reject over 4096). Reuse its
  strict `pos:` parsing semantics; do not change unrelated metadata accounting.
- Every loop, native transition, content chunk and serialization checks the
  **same issued deadline**. Checked additions reject integer/count overflow.
  Size/extent must fit the actual launch `file_limit` and remaining observation/
  write quotas. Existing file-creation/write/descendant/process/syscall limits
  still apply independently.

### Parent

- Bound prefix bytes before parsing. A shallow schema-directed lexical count
  pass enforces the fixed 512-node/depth-10 intermediate shape before general
  object decoding; count both keys and values. Require the prefix payload to be
  ASCII JSON as emitted by `encoded`, check its length before slicing/copying,
  and reserve `4*payload_bytes + 256*node_count` against the existing cache
  allowance before decoding. This conservative nonrefundable envelope covers
  decoded strings/container bookkeeping, in addition to separately charged
  canonical retained bytes.
- Bound and charge canonical stage/recipe envelopes before copying or appending.
  Each stage retains only already admitted argv/env/identity data plus one
  intermediate receipt, never source/SDK bytes.
- Result registries allow at most one native-completion token, one live stage
  result and one live recipe result for the active checker, within existing
  pending admission.
- Archive scope/occurrence count is capped by the existing session entry/
  observation bounds. Charge its immutable bytes once for ownership retention;
  charge any independently materialized decoded/copy form as well.
  A refusal because the archive/receipt cannot fit is a real stop.
- Pending acknowledgements retain an issued raw envelope until success/failure;
  missing acknowledgement cannot yield a successful observation or
  success-shaped archive.

### Cleanup

Native monitor cleanup closes its own FDs on every exit; it does not remove the
file to repair an incomplete proof. Parent stage/recipe capabilities are
consumed or cleared on exception/view expiry/session close. The raw archive
clears on session close; immutable exported evidence has no live token.
Existing command-workspace cleanup and session assertions remain authoritative.
Add focused assertions for only the new registries/FDs; do not redesign shared
bootstrap or terminal-outcome custody.

The exact costs must be reported in later authorized qualification. This is not
a claim that the old two-call cumulative control cap can accommodate another
full observation.

## 11. Required tests, mutations and restoration

These are frozen-design requirements for later implementation, **not tests run
or allocated now**.

### Pure schema/role/dataflow controls

| Requirement | Positive | Negative / mutation |
|---|---|---|
| Role parsing | Authentic retained argv, supported ABI variants, value-taking options containing `-`, harmless permitted option position changes derive correct roles. | Old indexes 20/7 used after an option shift; duplicate `-o`, extra positional input, response file, unrecognized arity, macro/option/environment containing the temp name, or non-string raw argv cannot acquire a role. |
| Raw schema | Exact successful envelope with one complete relation validates; JSON key reordering preserves it. | Unknown version/key, bool-as-int, duplicate/missing receipt, wrong stage/scope/binding, excessive depth/count/bytes, invalid transition order or result rejects. |
| Physical object | One actually created singly linked `0600` object links both roles and final retirement. | Foreign/preexisting/extra object, hardlink count, symlink/alias, replaced inode, changed parent/workspace, stale fd, failed creation or failed unlink rejects. |
| Writer completion | Successful actual writes/close/exit, full final extent and content hash agree. | Missing writer, non-writer write, short/unaccounted extent, remaining alias, nonzero exit or “completed” before close/exit rejects. |
| Reader consumption | Actual same-object read bytes cover exactly the sealed extent and digest. | Swapped reader object, stale writer identity, missing/partial reader, changed-content same-size file, matching size but wrong digest, unauthorized write after seal, or false EOF rejects. |
| Retirement | Actual driver unlink after reader exit, absent entry and pinned nlink zero; actual driver success. | Early unlink, wrong actor/path, failed/omitted unlink, another remaining link, or cleanup deletion substituted for retirement rejects. |
| Result custody | Exact result is sealed/consumed once in its original live context. | Copied/forged `_StepResult`/`_RecipeResult`, copied `ProcessOutput`, changed raw envelope, foreign session/view/epoch, consumed/stale/replayed token rejects before semantic projection. |
| Semantics | Distinct raw paths/physical identities with equal proven dataflow/content produce equal typed role forms. | Changed completed content digest, any non-role argv/order/environment, native/executable/SDK/source input identity, stdin, status/stdout/stderr/publication data stays unequal or is rejected by its existing admission. |
| Noninterference | Existing sed/kernel/raw metadata data passes unchanged. | A generic `/work` prefix/path-field/time/hash normalizer, or a projection extended to sed/executable stat identity, fails a preservation assertion. |

### Minimal genuine component qualification

After Main separately authorizes execution:

1. Use one existing small `ModernToolchainTests` fixture/session and **one**
   direct first-style Make observation (same original checker/header/remake
   structure and original source journal). Disclose preexisting `src/query.c`,
   copied headers, absent generated parents, no text producer and empty final
   target.
2. Observe its two genuine checker invocations from native job/producer evidence.
   Require two separately issued raw recipe envelopes, their five actual stages,
   successful creation/writer/read/content/retirement receipts, and closed
   authority/cleanup for each.
3. Compare the two complete checker semantic records/role summaries derived
   from those invocations. Use actual correspondence, not list indexes.
   The existing exact command-results dedup may produce one checker entry;
   raw receipt/native-job multiplicity must still be two.
4. Do not force random filenames, patch compiler arguments, or assert randomness
   as the only negative control. Raw workspace/launch identities must differ;
   if filenames differ, retain their exact supported operand differences.
   Deterministic retained-data controls supply the name-only preimage.
5. Require actual assembly hashes to agree for semantic equality. If real bytes
   differ, preserve that fact and stop; do not normalize their digest.
6. No ordinary Make oracle, second Make page, `run_probe`, root/census, additional
   source variant or second fixture is needed to qualify this component.
   Such work needs separate authorization, not an automatic fallback if this
   single observation fails.

Retain the native syscall-shape uncertainty explicitly: the spent pair did not
capture creation flags/read-write/close/unlink details or assembly content.
The conservative sequential/explicit-close profile above must be proven by this
one genuine execution before shipping. Unsupported actual behavior requires a
new narrowly reviewed contract, not silent widening.

### Native enforcement and restoration controls

Use the existing native-runtime mutation infrastructure in
`test_toolchain_runtime.py`; do not add another compiler/native launcher.
Separately authorized targeted controls must remove **one independent proof
check at a time**, with its corresponding adversarial fixture/input:

- remove successful-create evidence binding → an otherwise shaped receipt that
  falsely claims a successful creation must expose false admission;
- remove object pin/link check → swapped/hardlinked/foreign-object control;
- remove actor/output-role binding → wrong writer/reader or unbound operand;
- remove actual read/content equality → same-size changed-content/partial-read;
- remove writer/read terminal barrier → overlap or incomplete actor;
- remove actual unlink/path-absence proof → missing/early/failed retirement;
- remove scope/epoch/one-use result binding → stale/foreign/replayed result;
- remove raw-to-semantic field-preservation check → changed non-role
  argv/environment/tool/input/status/output must be detected;
- replace typed projection with prefix filtering or old numeric indexes →
  literal/shifted-role adversaries must fail.

Preserve already-covered workspace-absence/path/creation/hardlink denials as
existing negative controls; do not add duplicate production gates merely to
obtain a new mutation label. A mutation is not attributed to a new check when
an older independent guard rejects first. New-check mutation controls must
reach that check's actual boundary with otherwise admissible context (or use a
labelled pure protocol contradiction for its parser contract). In particular,
old workspace-preexistence refusal alone does not prove the new successful
creation-event binding.

For each, preserve baseline failure, removal result and restoration. A semantic
refactor that preserves option roles/wire fields must stay green. Restoration
of the old **raw semantic equality** recovers the deterministic name-only
preimage using retained paired checker records; restoring the typed projection
passes only after all individual receipt gates hold.

The native one-Make positive does not claim `_stable_native_context` was reached
between two returned observations. Pure wrappers remain labelled pure. Broader
cross-observation/root proof is a future separately contained acceptance scope,
not a circular blocker requiring a retry of the closed pair.

## 12. Minimal coupled implementation and ownership map

| File | Exact proposed responsibility |
|---|---|
| `scripts/validation_ownership/toolchain_runtime.py` | v2 launch contract; closed descendant operand parser; v1 intermediate/envelope validation; step/recipe result capabilities; sole typed projection. |
| `scripts/validation_ownership/syscall_guard.py` | Stage-4-only tracker, stopped actual object/actor/IO/retirement hooks, bounded new native receipt, own-FD cleanup. |
| `scripts/validation_ownership/make_probe.py` | Appended raw sidecars; integrate stage sealing/recipe consumption; acknowledged raw archive; raw execution binding; unchanged actual cache exclusions and only toolchain semantic placement. |
| `scripts/validation_ownership/tests/test_toolchain_runtime.py` | Pure parser/schema/projection/custody controls and focused genuine one-Make plus independent native enforcement mutations, using existing fixture/runtime helper. |
| `scripts/validation_ownership/tests/test_make_probe.py` | Small pure comparator/receipt-placement noninterference controls if not completely covered by the existing toolchain module. No planner/source behavior change or native full-page requirement. |
| `docs/ownership-probe-foundation.md` | Raw-versus-semantic contract, retained evidence lifetime, new exact role boundary, non-goals. |
| `docs/test-cases/workflow-governance.md` | Extend existing human procedure and limitations for the checker case; preserve existing gate case/page/raw-source contracts. |

Existing owners:

- production modules and Make pure tests: `paths.ownership` /
  `surface.ownership`;
- `tests/test_toolchain_runtime.py`: exact `paths.ownership-native` /
  `surface.host`;
- primary case `TC-WORKFLOW-OWNERSHIP-MODERN-TOOLCHAIN-001`;
- integration/noninterference case `TC-WORKFLOW-GATE-OWNERSHIP-001`.

No new module/path permission, registry, renamed case, public source snapshot,
committed evidence ledger, `.github` workflow, modern Makefile, test harness or
feature flag is required. The existing whole-module native mapping covers
new methods; local validation remains targeted.

`graph_probe.py`, `graph_commands.py`, `phase_census.py`, `header_runtime.py`,
`arm_headers.py`, and the frozen root harness remain behaviorally unchanged.
Their relevant consumers require review/compatibility assertions, not an excuse
to include unrelated edits.

## 13. Pre-edit freeze and independent-review checklist

Main's future freeze should explicitly adopt or reject this **single** contract:

1. Version-2 launch requires one complete stage-4 receipt; no mixed-version
   success fallback.
2. Closed authentic child roles, actual object creation/content/read/retirement,
   and one-use result custody are inseparable from the semantic projection.
3. Raw sidecars/archive are independently bound data; no raw bytes or receipt
   hash enter the semantic target fingerprint.
4. Only the two proven operands become typed role refs; other fields and
   non-toolchain commands remain exact.
5. All new storage/reads/transitions fit existing admission and cleanup owners.
6. Supported compiler option and sequential-I/O compatibility must be qualified,
   not guessed.
7. One-Make component qualification is sufficient for this component boundary;
   complete root/report and the separate SDK/capacity limitation stay explicit.
8. No execution or implementation is authorized merely by receiving this design.

This design leaves the reported SDK admission hold untouched and does not
depend on or inspect D1's independent budget/lifecycle work. Source618565,
paired private artifacts/modes and all spent allocations remain preserved.
