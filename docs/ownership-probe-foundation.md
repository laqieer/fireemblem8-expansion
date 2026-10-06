# Confined ownership-probe foundation

Issue [#206](https://github.com/laqieer/fireemblem8-expansion/issues/206) supplies
a **framework capability**: one bounded execution and observation authority for
GNU Make and declared generated-source consumers. It does **not** select,
replace, or skip validation.

The default remains the static core. The merged #226 layer adds the narrow
[same-report immutable view selector](#selecting-immutable-basecurrent-views-in-one-report).
This branch also provides the [live producer and nested-publication
extension](ownership-probe-producers.md) for #225, the independently merged
#227 [explicit optional runtime inputs](#explicit-runtime-discovery-inputs),
and the
[dependency-only host compiler](ownership-probe-dependencies.md) for #228.
D depends on P, not on the optional view or runtime-input APIs.
See the [archived delivery allocation](https://github.com/laqieer/fireemblem8-expansion/blob/56e0a206ffae088b0dbc1fe8aa6339a8ee820f33/docs/ownership-probe-allocation.json)
and [downstream boundary](#contract-allocation-and-downstream-integration).

## Native evaluator runtime-admission component

Issue [#270](https://github.com/laqieer/fireemblem8-expansion/issues/270)
separates the single-invocation native evaluator from the downstream ownership
graph. Its first component reuses the existing trusted Make ELF/loader capture
for an exact trusted system executable. `_make_runtime` delegates to that shared
mechanism. It still validates root-owned, non-mutable system paths, x86-64 ELF
and interpreter headers, runs only the captured interpreter's `--list` operation
with a clean environment from `/`, and charges the complete image/closure bytes.
It does not invoke `ldd`, load candidate ELF/code or grant native execution.
The shell control uses `/usr/bin/sh`; the existing trusted-root policy is not
widened to admit an unchecked `/bin` alias.

From a clean Linux x86-64 source checkout with the existing host toolchain:

1. Run `python3 -m unittest scripts.validation_ownership.tests.test_native_make -v`.
   Actual Make and shell captures must contain their original ELF bytes,
   interpreter and complete loader-derived libraries, all charged in one budget
   per control. The loader call must use the clean environment and `/` cwd.
2. Require untrusted candidate paths, malformed ELF, failed loader execution,
   unresolved libraries and libraries outside trusted roots to refuse. Run the
   existing `FoundationTests` runtime-capture controls for mutable paths and
   real Make execution with a relocated captured library closure.
3. Retain failures and close each budget; these controls do not write candidate
   source or create generated game data. No ROM, ARM toolchain, save reset,
   optional profile or subjective manual judgment is involved.

The existing `ownership-probe-test` target includes this component. These checks
prove runtime admission and preserved ordinary Make behavior only. Actual
natural dispatch, source/caller/job observations, generated-version lifecycle,
all eight original profiles in one budget, complete resource fitness and every
candidate/master delivery gate remain required by #270 and #180. Runtime
capture alone must never be reported as native-evaluator qualification.

### Internal readonly dispatch component

The next component of `TC-WORKFLOW-NATIVE-MAKE-001` exercises the internal
`ProbeSession._native_make_readonly` seam. It is not the graph's qualified
evaluator and is not an alternative validation selection. It shares request
admission, the original Make observer and the existing syscall supervisor.
Original authenticated Make dispatch reaches the exact captured system shell
at `/bin/sh`; the shell and its complete loader closure are private captured
files, not a host runtime mount. No replay interceptor or mapped-command
result supplies shell expansion or recipe stdout.

Run the following controls individually from the same clean host checkout:

1. Run `python3 -m unittest scripts.validation_ownership.tests.test_foundation.FoundationTests.test_native_readonly_original_shell_and_recipe_without_replay`.
   Original shell builtins must read the immutable admitted `input.txt`,
   populate the observed `VALUE` domain and execute a real recipe. Require
   actual recipe stdout, two authenticated native shell observations and the
   admitted source-access observation, while trapping any per-command replay.
   This is actual source-value/access evidence, not complete read-event custody.
2. Run the same class's
   `test_native_readonly_actual_write_and_foreign_execution_refuse`.
   An actual shell source-write attempt and an unadmitted executable must
   refuse; source stays unchanged and all owned processes/roots are closed.
3. Run its
   `test_native_readonly_mapped_runtime_authority_refuses_before_dispatch`
   and `test_native_readonly_guard_rejects_cross_lane_configuration`.
   Explicit optional runtime, mapped publication, altered executable authority,
   metadata replay, dependency confinement or a mismatched native environment
   cannot be combined with this lane.
4. Run its `test_native_readonly_shell_status_reaches_make_semantics`,
   `test_native_readonly_ignored_recipe_status_is_owned_by_make` and
   `test_native_readonly_unignored_recipe_status_still_fails_make`.
   An authenticated shell's nonzero status must reach original Make:
   `$(shell)` retains its output and `.SHELLSTATUS`, an explicitly ignored
   recipe continues, and an unignored recipe still fails through final Make
   status 2. Supervisor confinement violations never become ignored errors.
5. Run its `test_native_readonly_recursive_observer_dispatch_refuses`.
   An unused recursive variable containing `$(shell)` must refuse if exit
   observation would execute it. Refusal is explicit; the readonly component
   does not yet provide complete side-effect-free recursive-value observation.
6. Run its `test_native_readonly_shell_loader_probes_cannot_select_false_absence`
   and `test_native_readonly_loader_rejects_stale_stop_foreign_origin_and_replacement`.
   Shell builtins probing either absent `/etc/ld.so.cache` or
   `/etc/ld.so.preload` must refuse instead of selecting an absent branch.
   Genuine loader startup is admitted only from an owned actual syscall-entry
   stop, the captured interpreter's readonly executable mapping and matching
   instruction bytes. An altered actual stop, foreign libc origin or attempted
   private readonly image replacement must reject. The replacement attempt
   fails at the readonly filesystem boundary, not at a claimed inode detector.
7. Run its `test_native_readonly_runtime_overlap_uses_captured_bytes` and
   `test_native_readonly_conflicting_runtime_capture_refuses`.
   Shared images compare against already charged captured Make bytes; a disk
   reread trap must not fire and a conflicting captured library must refuse.
8. Run its `test_native_readonly_self_signal_status_reaches_make`,
   `test_native_readonly_ignored_and_unignored_self_signal_recipe`,
   `test_native_readonly_foreign_signal_and_trap_remain_refused` and
   `test_native_readonly_unadmitted_sigkill_termination_refuses`.
   Admitted shell self-SIGPIPE must produce `.SHELLSTATUS` 141; ignored and
   unignored recipes must match ordinary Make continuation/failure. Native
   bootstrap restores Python-ignored signal defaults before execution, as
   ordinary subprocess execution does. Forwarding requires actual kernel
   delivery from the same shell bound to its admitted self-signal syscall.
   Foreign sender, unauthenticated trap and unadmitted SIGKILL termination
   still refuse, including under an error-ignored recipe. Cleanup and resource
   signals do not acquire candidate self-signal authority.
   The externally injected SIGKILL control may reject at the earlier vanished
   ptrace target or terminal provenance check; it does not prove both paths.
   Also run
   `test_native_readonly_failed_queued_signal_cannot_admit_foreign_sigkill`:
   at genuine native shell syscall stops, replace self-kill arguments with
   `rt_sigqueueinfo` or `rt_tgsigqueueinfo` and a null information pointer.
   Both must actually return kernel `EFAULT`; after resuming, inject foreign
   SIGKILL. Before correction both ignored recipes continue and print `done`.
   Now both must reject terminal self-signal provenance and clean owned state.
   This is controlled syscall-argument and foreign-signal injection, not a
   hostile-process isolation claim. The existing pending-call lifecycle
   retracts a failed attempt's new signal authorization without discarding a
   preceding successful authorization.
   `test_native_readonly_failed_signal_preserves_prior_success_authorization`
   runs actual declared Python with SIGUSR1 blocked, records successful
   self-kill and a later queued `EFAULT`, and requires both the surviving
   authorization and actual kernel pending-signal bit. The signal remains
   blocked until Python exits after that fault; this is not complete signal
   queue or cancellation qualification.
   `test_native_readonly_successful_self_sigkill_status_is_preserved` requires
   `.SHELLSTATUS` 137 and ignored-recipe continuation matching ordinary Make.

The round-three supplier reconsideration removes duplicated syscall-origin
observation. Native loader and dependency negative-purpose checks share one
owned kernel-entry/mapping/instruction reader; each retains its separate
captured-image and operation predicates. Actual bounded mapping bytes remain
charged, with no budget change, reset or exempt category. A plain namespace
launcher cannot supply the required source/caller/job/generated custody, while
per-command replay exceeded the original aggregate budget. Neither is an
acceptable replacement. This simplification does not release the original
graph's held architecture disposition or qualify the complete native supplier.

Readonly dispatch admits only the original Make image and captured shell.
It cannot produce generated files or execute undeclared native tools.

`TC-OWNERSHIP-NATIVE-EXECUTABLE-001` (#270) additionally covers the explicit
`native_tool` declaration for one exact session-issued `NativeTool`. The default
is absent. Reuse the existing compiler capsule, issued-object identity, sealed
bytes and `/native/tool` placement; both direct-native and native-Make execution
materialize the bytes actually verified, not a later reread. The readonly native
Make lane admits the image only with its captured interpreter and existing
runtime resources; missing external dependencies still refuse, never resolve
implicitly. Source paths and argv are not rewritten.

With a clean fixture, run
`test_native_readonly_session_issued_tool_executes_in_original_make`: compile
the candidate C fixture, invoke its actual `/native/tool` through unchanged GNU
Make, and require `compiled` semantics/output plus actual executable, terminal
status and waited native-job evidence. Then run
`test_native_readonly_session_issued_tool_boundaries`: a structurally identical
but unissued tool object, changed sealed bytes, undeclared image metadata and
actual candidate source writes must refuse and leave owned cleanup complete.
Repeat the existing C++ direct-native test to preserve that execution lane.
Readonly native Make accepts `native_libraries=(absolute_shared_library_paths,)`
as explicit resource declarations, separate from `native_executables`.
Only exact root-verified ELF shared-library files under trusted system library
roots enter the existing sealed runtime closure; aliases to the same canonical
file, malformed paths and non-library declarations refuse. No library declaration
grants execution or write authority, and dependencies are never inferred from a
candidate ELF or resolved by executing its loader in the trusted collector.
Run `test_native_readonly_declared_cpp_library_resources`: a real compiled C++
iostream tool requires declared stdc/gcc/m resources, actual output/semantics and
waited native-job evidence. Missing dependencies, direct library execution,
library writes and malformed/duplicate/canonical-alias declarations must refuse.
The internal `/native/tool` case does not qualify original `tools/scaninc`
paths, arbitrary external-library dependencies, generated publications or the
original eight-query family. No ROM/save/profile/localization interaction applies.
Source/caller/job provenance, generated mutation/version custody and complete
unchanged-budget eight-profile fitness still require the complete #270
implementation; these passing controls do not qualify those missing contracts.
Ordinary live/mapped Make remains unchanged. No save, localization, ROM/RAM,
modern/archival build profile or public configuration change is involved.

### Original source-entry observation component

The internal readonly invocation can now request `observe_reads=True`. Only
the existing version-1 original-read contract is configured: Make/source ELF
ABI sites decoded from the captured Make bytes, actual private runtime mapping,
kernel hardware-breakpoint entry/caller frames, trusted paired `fopen`
notifications, source descriptor pins and bytes through close/return, and
complete read-pass/goal structure. It reuses the existing original read-trace
and validator source rather than introducing a second source registry.
Version 1 remains the default; the explicit readonly completion component below
admits version 4 only with its complete frozen selection and entry proof.
Generated-version mutation is not admitted. Ordinary mapped execution does not
request these internal variants.

From the same clean host checkout, run these `FoundationTests` controls:

1. `test_native_readonly_actual_nested_source_trace`: require Makefile, nested
   required includes and a missing optional include to report their actual
   visit parents, exact opened immutable source bytes/identities and complete
   pass. The original shell expression and recipe must run without command
   replay and preserve selected values and stdout.
2. `test_native_readonly_source_frame_and_pin_mutations_refuse`: altered cached
   reader-frame and pin-identity bindings at actual source callbacks must refuse.
   These are binding-mutation controls, not fabricated register samples or
   proof of physical source replacement.
3. `test_native_readonly_incomplete_trace_writes_failure_envelope`: incomplete
   actual trace state must produce a failed supervisor report, not omit the
   report or manufacture success.
4. `test_native_readonly_source_active_deadline_cleans_owned_trace`: block the
   actual source callback after an opened pin until the unchanged report
   deadline; require terminal refusal and owned session/process cleanup.
   This does not prove every register-restoration or arbitrary cancellation
   scenario required by the complete supplier.
5. `test_native_readonly_chld_trap_and_unsupported_stop`: admitted native
   self-SIGCHLD must run the shell trap and match ordinary Make stdout.
   Native SIGSTOP beyond the one actual newborn tracing stop explicitly
   refuses; it must not silently continue. Unsupported native child-origin
   signals also refuse rather than acquire unauthenticated forwarding.

All controls have ordinary immutable fixtures and teardown; no ROM or save
artifact is involved. Full assignment-completion/caller/job observations,
machine/restoration adversaries, generated-version lifecycle, all eight
original profiles in one budget and every remote delivery gate remain open.

`TC-OWNERSHIP-NATIVE-TRACE-COUNT-001` (#270) covers shared count custody.
Trace events, actual hardware-breakpoint traps and configured machine records
reserve the same aggregate count as filesystem attempts. Each trace reservation
uses the existing 128-byte bookkeeping allowance, in addition to actual
metadata bytes. Successful and failed reports settle this total into the one
session; subsequent queries receive only its remainder. Filesystem records
retain their existing deduplication, while distinct trace records and traps
are not deduplicated.

From a clean host checkout, run
`test_native_trace_count_shares_filesystem_event_machine_and_trap_limit`:
four filesystem records plus four event records fill an eight-record allowance.
The next event, machine record, trap or filesystem attempt must refuse without
incrementing past eight. Run
`test_native_readonly_trace_count_settles_across_queries`: a real readonly
Make source trace in a 1024-entry fixture must report filesystem attempts plus
every event and trap, measured from actual supervisor state at trace finish.
Successful filesystem result lists alone omit unsuccessful attempts and cannot
substitute for that count. Spend all but one remaining
entry through the ordinary command seam; the next native query must consume
that remainder, fail, settle exactly 1024 observations and clean owned state.
The initial 64-entry fixture cannot hold Make's original variable table and is
not a count-accounting control. Pre-fix reports omit the trace count.
Dependencies are the existing readonly source trace and supervisor settlement;
conflicts are none. Production limits, ordinary mapped behavior, ROM/RAM,
save compatibility, generated data and localization are unchanged. These
controls do not qualify the full supplier or the original eight-profile family.

### Original readonly job observations

`TC-OWNERSHIP-NATIVE-JOB-001` (#270) binds readonly shell work to original
Make jobs. It reuses the original GNU Make 4.3 `children`/`shell_function_pid`
ABI and trusted observer read/wait notifications. Each actual original spawn
gets one sequence, which must bind a direct, traced `/bin/sh` exec child.
Original Make supplies expansion ownership or the recipe target and command
index before waiting, then its ignored-error flags and wait status at return.
The supervisor retains the actual kernel terminal result across process exit
and requires the wait result to match it before retirement. Repeated identical
context notifications deduplicate; changed, foreign, reused or incomplete
bindings refuse.

Completed records use the existing `accessed` transport with `native-job:`
followed by a JSON object containing `sequence`, `executable`, `pid`, `context`,
`returncode`, raw `terminal_status`, `waited` and `ignored`. `context` contains `kind`, `target` and
`command_line`; expansion target/index are null. The index is Make's original
next-command index, not a source line number. Records are an observation of
actual work, never a command registry, replay instruction or permission to
write. Dispatch, exec, context, exit and wait bookkeeping share the aggregate
observation/byte bounds; no retired process retains executable or signal
authority. A failed spawn without a complete observed child refuses rather
than manufacturing a completed record.

On the same clean host checkout, run
`test_native_readonly_actual_expansion_and_recipe_job_lifecycle`. Its immutable
Make fixture evaluates one immediate shell value and two recipe commands,
the first returning ignored status 7. Require ordinary Make stdout, the actual
value and shell status, three distinct child/dispatch identities, expansion
versus target/index ownership, ignored-policy values and matching terminal
statuses. Run `test_native_readonly_job_binding_and_retirement_mutations_refuse`:
at actual callbacks, alter the PID, sender, parent, stable context or terminal
status; repeat exec/exit binding or omit wait/exit completion. Each must refuse
and clean its owned session. These are mutations of real cached bindings, not
fabricated register observations.
`test_native_readonly_job_raw_status_mismatch_with_same_exit_code_refuses`
changes the retained core-dump bit at the real SIGPIPE child's wait callback.
The normalized exit code remains identical, but the differing raw status must
refuse; a normalized-code comparison alone cannot meet this contract.
Keep the native PIPE/CHLD controls and the
ordinary mapped static-metadata/cache test as compatibility controls.
`test_mapped_make_has_no_native_job_observations` requires the original mapped
metadata-only behavior, selected domain value and absence of native job records.

Dependencies are authenticated readonly dispatch, the existing observer ABI
and shared accounting; conflicts are none. Ordinary mapped invocation emits
no native job callbacks. No ROM/RAM/save/localization/game-data or archival
behavior changes. Arbitrary descendants, source assignment/caller completion,
generated-version write/transfer/retirement custody, machine restoration,
the original eight-query resource family and final delivery remain unqualified.

`TC-OWNERSHIP-NATIVE-INPUT-001` (#270) covers original input parameter parity.
The internal readonly call accepts the same
`assignments=(("environment", name, value), ("command-line", name, value), ...)`
request as ordinary mapped Make, through the existing shared admission helper.
It does not rewrite immutable source, modify host global environment, or grant
execution authority. With no assignments, existing defaults are unchanged.

From a clean host checkout, run
`test_native_readonly_original_assignment_inputs_and_default_restore`: supply
one environment input and one command-line cap to the immutable shell/recipe
fixture. Require ordinary Make stdout and original domain value/origin/flavor.
In the same session, run again without assignments and require file defaults
with no input leakage, increasing aggregate observations, and unchanged budget
object/deadline. Run
`test_native_readonly_assignment_authority_refuses_before_launch`: loader/shell/
Make-option authority names, invalid origins, duplicate names and nonstring
values must refuse before sandbox execution and clean owned state. The
pre-component API rejects the assignment keyword. Dependencies are readonly
execution and existing Make request admission; conflicts are none. No ROM/RAM,
save, localization, game-data or archival impact. This input smoke is not
assignment-completion ancestry or the original eight-profile qualification.

`TC-OWNERSHIP-NATIVE-OBSERVE-001` (#270) covers output-independent exit
observation. Native requested domains and target `SHELL`/`.SHELLFLAGS` values
are checked through the original nonexpanding effective-variable lookup in
the same target/global scope, then flavor/raw-value metadata, before expansion.
An effective deferred append binding explicitly refuses: its nearest raw value
can hide an inherited parent's expression. The original variable-set pointer
is restored after lookup; no append expression is interpreted or replayed.
Simple and undefined bindings and literal recursive bindings remain supported.
A recursive raw value containing `$` explicitly refuses before its contents
are executed. This includes otherwise pure references and escaped dollar
expressions: general recursive-expression observation remains unsupported.
There is no builtin blacklist or alternative expression interpreter, and
ordinary mapped observation retains its existing behavior.

Run `test_native_readonly_recursive_builtin_observation_has_no_extra_effects`
on a clean host checkout. Unused info/warning/error/eval/shell bodies and a
nested reference, plus recursive shell/flags bindings, must refuse. Captured
native stdout must exactly match the ordinary invocation, including legitimate
shell/flags output emitted by Make before exit; observer-only output must not
appear. The pre-fix observer adds output, alters Make state, or executes the
requested error. Run
`test_native_readonly_simple_dollar_and_literal_recursive_values_are_safe`:
a simple dollar-bearing value, literal recursive value and undefined value
must preserve their original value/flavor and successful recipe output.
Run `test_native_readonly_inherited_append_observation_has_no_extra_effects`:
target, inherited and pattern append bindings hiding info/warning/error/eval/
shell or nested-reference parents must refuse before observation effects.
Include appended `SHELL` and `.SHELLFLAGS`; preserve their legitimate original
recipe-time output and compare captured native stdout with ordinary Make.
The pre-fix observer emits extra output or reaches the wrong failure boundary
after entering an inherited body. Run
`test_native_readonly_target_literals_and_mapped_append_keep_semantics`:
target simple dollar-bearing and literal recursive bindings and undefined
values must remain correct, global bindings must retain their original scope,
and ordinary mapped append must still return `parent tail`.
Retain the existing native shell-spawn refusal and original input/job/source/
count controls. The mapped inode-identity control also retains its existing
`readonly executable image` diagnostic and still rejects a zero inode.

Dependencies are the admitted GNU Make 4.3 file/variable prefix ABI, existing
observer metadata API and shared origin helper;
conflicts are none. No new execution authority, input profile, generated data,
ROM/RAM/save/localization or archival change. Full recursive value support,
readonly completion integration, generated custody and original eight-query
qualification remain incomplete.

### Declared original direct executables

Trusted native ELF closures are captured once per exact active immutable view
and requested executable pathname. First acquisition charges the actual
captured bytes and retained cache resources; repeat queries reuse these bytes
rather than rereading the live host. Every invocation still gets fresh readonly
backing and actual source/dispatch/job/machine observations under the same
cumulative deadline and budgets. Nested `select_view` isolates captures,
restores the outer objects and clears selected caches; session failure/exit
clears all runtime captures. ABI decoding and source selection are not cached
by this component.

Run `test_native_readonly_reuses_captured_runtime_without_second_host_read`:
execute two original shell/direct-printf Make queries in one session, deny a
second host runtime acquisition and require identical real output/values,
retained exact capture resources, lower incremental control cost, unchanged
deadline/limits and growing observations. Before reuse, the second real query
tries another host acquisition. Run
`test_native_readonly_runtime_capture_view_isolation_restoration_and_failure`:
select base/current nested immutable views, require isolated captures and
correct original outputs, restore the outer capture object, then reject a
new executable capture and prove it is not cached; cleanup empties every
retained view cache. This does not make an oversized Python runtime profile
admissible or qualify the original eight-query family.

Captured session `runtime_files` are also supported as readonly optional data,
including real absent paths and recorded stock ancestor aliases. The native
invocation builds fresh captured backing; it does not mount the session's
cached mapped interceptor runtime. Optional program bytes remain data unless
explicitly declared executable. Shared optional/native images must have
identical bytes and preserve the optional input mode. Actual dispatch, exec
and returned jobs use the installed canonical pathname; original argv and
notification bytes are not rewritten. Thus a recorded `/bin` alias can place
the original `/bin/sh` at `/usr/bin/sh` without granting arbitrary aliases.

Run `test_native_readonly_optional_runtime_data_absence_and_stock_alias_jobs`
on the clean Linux x86-64 host with the existing build-essential libc headers
and admitted stock printf: capture `/usr/include/stdlib.h`, absent `/usr/include/.dep` and
`/bin/printf`, explicitly declare `/usr/bin/printf`, read the first header line
and probe absence in original shell expansions, then invoke printf through
both spellings. Require exact values/output, four real canonical shell/direct
jobs, successful data access and actual absent kernel metadata results.
The pre-fix readonly invocation rejects these captured runtime inputs.
Run `test_native_readonly_optional_runtime_resource_and_alias_boundaries_refuse`:
adjacent uncaptured data/absence, unrequested `/bin/true`, parent traversal and
an undeclared optional program must refuse and clean owned state.
Run `test_native_readonly_optional_absence_rejects_wrong_kernel_result_projection`:
alter a real absent metadata result projection to success; the host must reject
it against the declared absent resource. This is not physical kernel mutation.
Actual optional-resource metadata requests/results/buffers and successful
accesses use existing observation settlement and shared budgets. Mapped-parent
invocations, mutable publications and root/repository submount overrides remain
forbidden. Default profiles without optional resources retain their behavior.
No Python runtime, candidate NativeTool, descendant or generated authority is
granted by these optional data/alias resources.
The generic case does not require Newlib provisioning; the exact original
eight-query fixture retains its own original Newlib resource declaration.

Run `test_native_readonly_short_underscore_matches_long_reference_completion`:
use `_ := original`, then `VALUE := $_` or `VALUE := $(_)` in separate real
source fixtures. Both scanners must select `_`; actual native output/value and
selected `_`/`VALUE` assignment completions must agree. The short-form pre-fix
case expands correctly but omits the supplying `_` completion. Both scanners
share the same admitted single-character identifier alphabet.
Retain `test_native_readonly_completion_failed_make_has_no_successful_archive`:
a real failed query keeps Make's status/diagnostic without a successful trace.
Successful-query machine/job cross-checks must not dereference the deliberately
absent archive for that failed query; the pre-fix host instead raises KeyError.

The internal readonly invocation accepts `native_executables=()`; the empty
default still admits only the original shell. Explicit ordinary trusted host
ELF paths are captured with their interpreter/library closure and installed as
readonly resources, never substituted with command handlers. Their interpreter
must be the already admitted native interpreter. Conflicting bytes, duplicate
paths or reserved execution-authority paths reject before launch. Actual GNU
Make dispatch, exec, job PID/context/raw wait and returned executable bindings
must agree. A declaration does not authorize a later shell re-exec or arbitrary
descendant. Capture and observation costs retain the same aggregate budget and
deadline.

Returned `native-job` records require exactly `sequence`, `executable`, `pid`,
`context`, `returncode`, `terminal_status`, `waited` and `ignored`. Context
distinguishes expansion from recipe target/command index; typed terminal status
must be final and agree with the return code, waiting must be complete, and
sequences/PIDs must be unique and complete. Executable/PID records must match
actual execution observations. Completion requests additionally require the
existing complete machine section and exact child dispatch/PID agreement with
these jobs. General trace consumers retain optional machine archives.

From the clean Linux x86-64 checkout with admitted GNU Make 4.3 and the existing
namespace launcher, extend `TC-OWNERSHIP-NATIVE-COMPLETION-001` (#270):

1. Run `test_native_readonly_declared_direct_executable_preserves_original_jobs`:
   define `VALUE := $(shell /usr/bin/printf %s original)` and an original direct
   `/usr/bin/printf` recipe. Explicitly declare `/usr/bin/printf`; require
   `original`, `recipe` plus newline, two actual expansion/recipe jobs with zero
   raw wait status, and exact dispatch/PID machine binding without command
   replay. Complete source/completion custody and owned-state cleanup must hold.
2. Run `test_native_readonly_direct_executable_default_invalid_and_conflicting_admission_refuse`:
   the same recipe without declaration must refuse at GNU's earlier uncaptured
   executable metadata lookup, before original dispatch;
   invalid, duplicate and reserved declarations and conflicting captured
   library bytes must reject. The empty-profile refusal is the pre-fix control.
3. Run `test_native_readonly_direct_executable_dispatch_and_returned_bindings_refuse`:
   change an actual dispatch or its job executable, or alter returned job/exec
   projections. Unissued resources and an otherwise admitted but different
   program must both reject. These are controlled binding mutations, not
   physical executable replacement.
4. Run `test_native_readonly_declared_executable_does_not_admit_shell_reexec`:
   declaring the program must not let an already dispatched shell re-exec it
   without an original authenticated Make dispatch.
5. Run `test_native_readonly_returned_completion_requires_entire_machine_section`
   and `test_native_readonly_returned_job_lifecycle_and_machine_binding_mutations_refuse`:
   delete the whole machine section after actual trace finalization, reduce
   actual returned jobs to PID/path, delete lifecycle fields, mutate typed
   wait/status/context/sequence evidence, swap otherwise complete dispatch
   bindings, and omit jobs plus execution markers while retaining real machine
   children. Before the host correction all twenty controls return successfully;
   each must now explicitly refuse and clean owned state. These are real
   return/observation projection mutations, not physical execution replacement
   or hostile same-UID report authentication.
6. Run `test_native_readonly_returned_snapshot_custody_and_image_mutations_refuse`
   (#270, `TC-OWNERSHIP-NATIVE-EXECUTABLE-001`): after genuine completion,
   change a successful source-open to well-formed publication custody, or change
   an entry-image digest without changing selection or machine evidence.
   The pre-fix host accepts both archives. The readonly host must reject both,
   requiring snapshot custody and its requested immutable snapshot digest for
   every corresponding event, and clean owned state. The shared validator
   remains publication-capable; these are request-specific return mutations,
   not proof of protection against a hostile same-UID process.

### Explicit managed Python resource directory

`TC-OWNERSHIP-NATIVE-MANAGED-RUNTIME-001` (#270) covers the internal
`native_runtime_directories=()` resource declaration. Its empty default retains
the captured-only native runtime. Up to four distinct exact Python resource
roots may be declared: `/usr/lib/pythonM.N`,
`/usr/local/lib/pythonM.N/dist-packages`, `/usr/lib/python3/dist-packages`,
and `/usr/lib/pythonM.N/dist-packages`. These use the existing readonly bind-mount seam. Before
mounting, every namespace entry is checked for root ownership and nonmutable
group/other permissions, regular-file/directory/symlink type, entry bounds and
the existing deadline; metadata admission charges the existing control budget.
Real installed symlinks are preserved, but resolving outside the declared
directory grants no target access. Captured executable/optional-resource
overlap refuses. The supervisor checks exact mount declaration, actual backing
device/inode and readonly/nosuid/nodev flags before candidate execution.

This is an explicit amendment to the early captured-runtime-only design, not a
claim of captured resource bytes. The managed system runtime must remain stable
through the session; privileged host updates are outside this trust model.
Resource reads, metadata, enumeration and executable library mappings within
the declared root do not grant additional command execution, writes,
candidate-source or generated-output authority. This namespace contains
trusted Python source and extension code. Command executable ELF and external
library admission remain separately captured and explicit; argv is not rewritten.
No broad `/usr` or library-directory mount is introduced.

Tester prerequisites are the installed system versioned Python stdlib and the
existing native namespace launcher; no ROM, save, localization or archival
interaction applies. Start with a clean fixture and run:

1. `test_native_readonly_managed_python_directory_preserves_real_namespace`:
   original shell reads a real stdlib resource and returns its actual first
   line; successful access evidence must include that file. Repeat without the
   declaration and require uncaptured-access refusal and owned cleanup.
2. `test_native_readonly_managed_python_directory_boundaries_refuse`: reject
   broad/nested/duplicate declarations, resource writes, parent escapes,
   external symlink targets and execution of an unissued executable stdlib
   file. Cleanup must retain no capsule or session cache.
3. `test_native_readonly_managed_python_startup_does_not_invent_absence`:
   dispatch real explicitly admitted Python with the fixture's actual `-I -S`
   argv. Its unadmitted `/usr/pyvenv.cfg` startup read must refuse, not become a
   false-absence branch. **Original generator invocations and full extension
   dependency closure are still unqualified.** This
   negative is not a successful Python execution or original-eight-query proof.
   Explicit optional `runtime_files` may now name ordinary trusted files or
   genuine absence beneath `/usr/`, retaining the existing captured data/mode,
   root-owned ancestry and before/after identity checks. This file-only capture
   does not mount `/usr`, grant directory enumeration or extend executable
   trust roots. A single root-owned final file symlink directly naming its
   canonical ordinary trusted target is preserved, not flattened. Capture
   verifies both link and target identities around the target byte/mode read;
   chained, dangling, untrusted or escaping targets refuse. Native executable
   image and managed-directory overlap still refuse.
   `test_native_optional_usr_data_capture_preserves_bytes_and_real_absence`
   verifies actual timezone data bytes/mode, startup-file absence, unchanged
   executable trust roots and the actual UTC alias target.
   `test_native_readonly_python_declared_startup_still_refuses_uncaptured_timezone`
   declares startup paths actually observed in the native fixture. Real Python
   then reaches and explicitly refuses `/usr/share/zoneinfo/UTC`; that final
   symlink has not been declared. This is startup progress, not successful dynamic
   import or qualification of the original nonisolated invocations.
   Explicit optional probes beneath the fixed sandbox `HOME=/nonexistent`
   may capture genuine absence with the same ancestry and before/after checks.
   Present resources there refuse before any byte acquisition; no home mount,
   user data, enumeration or executable authority is granted.
   `test_native_default_home_runtime_capture_requires_actual_absence` checks
   actual missing metadata and unchanged executable roots, plus private
   present-file refusal with only path admission substituted.
   `test_native_readonly_default_python_home_absence_does_not_hide_installed_site`
   runs the genuine default `python3 -c` command, not an isolated substitute.
   Ordinary Python returns `[1, 2]`; without the exact HOME site declaration
   confinement must refuse that real metadata probe. Declaring its actual
   absence reaches the existing `/usr/local/lib/pythonM.N/dist-packages`
   namespace, which still refuses rather than being reported missing.
   This undeclared-site boundary is an explicit negative, not successful
   nonisolated execution.
   `test_native_readonly_default_python_declared_site_startup` additionally
   declares the actual installed local/system site roots and the exact
   canonical root-owned `/etc/pythonM.N/sitecustomize.py` optional file.
   Require unchanged `python3 -c` JSON import and recipe output to match
   ordinary Python `[1, 2]`, actual JSON reads and owned cleanup. The existing
   captured startup/zip/HOME/timezone resources remain explicit. A genuinely
   absent versioned `dist-packages` beneath the declared stdlib uses the live
   readonly namespace, not a conflicting captured absence.
   `test_native_readonly_python_site_resource_boundaries` requires actual
   site write and parent-escape refusal, unissued Python metadata refusal
   before dispatch,
   sitecustomize bytes/mode capture and denial of other `/etc` paths, spelling
   extensions and nonoptional executable admission. This is not an `/etc`
   mount or a new executable root. Default startup is qualified only for this
   concrete system Python JSON invocation, not arbitrary installed package
   dependencies, original generators or the complete eight-query family.
   `test_native_readonly_python_captured_alias_and_dynamic_import` additionally
   declares that exact UTC resource, runs the fixture's genuine Python command,
   and requires `[1, 2]` from actual JSON dynamic import and recipe output plus
   JSON and canonical timezone access evidence. It does not qualify unmodified
   original generator argv or the eight-query family.
   `test_native_readonly_captured_alias_metadata_and_overlap` requires genuine
   `readlink` output `Etc/UTC`, capsule symlink/data preservation and duplicate
   canonical-resource refusal.
   `test_native_captured_file_alias_shape_and_identity_refuse` uses private
   physical link/file fixtures with only root-path admission substituted to
   mutate the real target or link during capture, and tests chained/missing
   target refusal. It separately rejects the installed chained localtime
   alias. These private fixtures prove capture consistency, not root ownership.
4. `test_native_readonly_managed_python_make_enumeration_is_complete`: original
   ready Make wildcard enumeration must match the actual declared stdlib
   directory's complete `.py` names; the adjacent parent root remains denied.
   This also checks the Make consumer rather than only a native child.
5. `test_native_readonly_managed_python_mount_and_capture_overlap_refuse`:
   reject an actual optional captured file overlapping the mount, and projected
   missing readonly mount flags. The latter tests verification, not a physical
   kernel remount or hostile host writer.

Dependencies are the existing mount/namespace and runtime trust mechanisms;
conflicts are captured-resource overlap. The full original invocation still
requires complete startup/resource and producer/job/source custody plus
unchanged aggregate accounting. This bounded resource-directory component
does not waive those criteria or any final gate.

Dependencies are the existing captured ELF closure, readonly loader-origin
proof and original job/machine lifecycle; conflicts are none. No ROM/RAM/save,
localization, generated or archival change. Original Python generator resources,
compiled candidate `NativeTool` source-path placement, optional runtime metadata profiles, arbitrary
descendants, generated versions/remakes, general recursive final observation
and original eight-query qualification remain unsupported or unqualified.
`NativeTool` remains excluded from mapped Make; explicit readonly native-Make
admission at `/native/tool` is covered above. This component does not waive
any complete-supplier or final delivery criterion.

### Original readonly completion component

`TC-OWNERSHIP-NATIVE-COMPLETION-001` (#270) is the closed immutable-source
component, not full generated-source qualification. The internal invocation
requires both `observe_reads=True` and `observe_completions=True`. It decodes
the existing version-2 completion ABI from captured private Make bytes and
returns the existing version-4 trace. The complete Snapshot inventory is
screened with the original bounded text/binary/UTF-8 rules; conservative
reference names select literal assignment sites. Screening, native assignment
sites, references, metadata/call/conditional consumers and selection validation
share one literal-name grammar: optional leading dot, initial ASCII letter or
underscore, then ASCII letters/digits/underscore/dot/hyphen. Opened source bytes, mode
and extent must match that frozen snapshot inventory, and references must be
covered, including assignment RHS dependencies rather than only roots.
Computed lookup, named-function or conditional suppliers are not yet bound
to actual native observations. Completion-enabled source facts explicitly
refuse them instead of claiming a complete literal closure; ordinary native
execution without assignment-completion evidence remains available.
Publication origins are forbidden rather than serviced by a null
publication fallback.

At the actual owned, parked Make read-entry hardware stop, the supervisor
checks the original process/read context, the single complete repository mount
without root/submount overrides, and kernel `statvfs` readback through the
actual process root. It confirms the existing entry-image barrier before the
first source entry. The Snapshot digest is only this binding; it cannot replace
entry/caller/stream/floc/modifier/effective-variable and descriptor-pin proof.
Actual shell machine executions carry the original native dispatch sequence
and must match every completed readonly job's sequence/PID. Machine shape
alone cannot substitute for that cross-binding.

From the same clean Linux x86-64 host checkout with admitted GNU Make 4.3 and
the existing namespace launcher, run these `FoundationTests` controls:

1. `test_native_readonly_actual_completion_inventory_barrier_and_jobs`: use
   immediate, deferred, conditional/ignored and continued definitions; repeated
   nested, empty, optional-missing and skipped includes; original environment/
   command-line inputs; one eager shell and one recipe. Require original
   output/values, selected raw deferred and ignored bindings, exact continued
   physical spans, four successful pin retirements, complete inventory and
   actual entry confirmation, and both actual job/machine execution pairs.
   The deferred names are referenced in an unused binding so the conservative
   selection covers them without expanding that binding. No command replay,
   deadline reset or observation reset is permitted.
2. `test_native_readonly_completion_callback_and_machine_mutations_refuse`:
   alter captured bindings at actual frame/floc/modifier/returned-variable
   callbacks; omit entry confirmation, clear/pin records or a child, change a
   child PID or duplicate its execution, and alter actual readonly flag
   readback. Every case must refuse and clean owned state. These are controlled
   binding/projection mutations, not physical remount, variable replacement or
   independently proven kernel register-restoration attacks.
3. `test_native_readonly_completion_inventory_misbind_and_invalid_options_refuse`:
   a valid-shaped but wrong source digest must fail against the actual pin;
   completion without reads and nonboolean requests must fail before launch.
   Also run `test_native_readonly_literal_supplier_names_are_not_omitted` and
   `test_native_readonly_literal_metadata_conditional_and_selection_closure`
   (#270): independent `.FLAGS` parenthesis/brace/value, `.SHELLFLAGS`,
   internal-dot/hyphen, origin/flavor/call and conditional fixtures must retain
   their actual supplying assignment completions, not merely correct final
   stdout. The pre-fix six supplier controls return correct values but omit
   those suppliers. Removing a supplier from the selected closure must refuse
   in both live-source and archive validation, including assignment RHS
   references. The existing underscore and scoped-variable controls remain.
   `test_native_readonly_function_delimiters_preserve_original_suppliers`
   compares actual ordinary Make with native completion for opposite-delimiter
   literals in both function forms, nested and ordinary arguments, and empty
   short-circuit arguments. The pre-fix native reader rejects six valid sources
   although ordinary Make succeeds; all seven fixtures must now retain exact
   output and their actual `SUPPLIER`/`VALUE` completions. The existing splitter
   uses one enclosing-delimiter depth counter instead of a mixed-delimiter
   stack; no second Make parser or source transformation is introduced.
   `test_native_readonly_computed_suppliers_refuse_incomplete_completion`
   compares fourteen real ordinary Make lookup/call/metadata/conditional forms
   with the shared source-facts parser and completion-enabled native reader.
   Before correction nine original forms returned correct values with
   incomplete supplier selection; all fourteen now explicitly refuse computed
   supplier binding. Concatenation and substitution names are included.
   `test_native_computed_supplier_execution_without_completion_still_works`
   requires original computed lookup/call output when completion evidence is
   not requested, rather than silently manufacturing an assignment trace.
   `test_native_reference_analysis_preserves_builtin_scoped_and_dead_computed_forms`
   keeps literal call arguments, ordinary function references, scoped tokens
   and multiline literal conditionals in reference analysis; a genuine
   constant-empty `and` fixture must retain empty output and semantics without
   rejecting its already-pruned computed branch. This is a bounded
   completion limitation, not qualification of arbitrary computed Make source.
   Chained `else ifdef` and `else ifndef` use the same full-name extraction
   and refusal as top-level directives. Run
   `test_native_unsupported_direct_suppliers_refuse_incomplete_completion`:
   ordinary GNU Make must return the declared value for both `1NAME` lookup
   delimiters and substitution forms, space-containing names, all four
   name-taking functions, and literal top-level/chained conditionals. The shared
   source-facts parser and completion-enabled native reader must instead
   explicitly reject the unsupported supplier; they must not return an archive
   that silently omits it. The pre-fix parser accepts the direct forms and the
   chained computed directives. Known GNU builtin function arguments, empty
   references, scoped tokens and supported literal substitution names remain
   accepted. Unknown function-like expressions are variable lookups, not
   evidence that a supplier can be ignored. The function set matches the
   admitted Make 4.3: `intcmp name` is a variable
   lookup, not the builtin added in Make 4.4, and must explicitly refuse.
   Literal chained conditional tests also require actual supplier completions and reject a returned selection
   with that supplier removed. Parser-only negatives are not whole-archive
   mutation evidence.
   The third-round disposition binds `TC-OWNERSHIP-NATIVE-COMPLETION-001`
   to finite shared source admission, not permissive assignment-regex
   fallthrough. `test_native_completion_finite_declaration_and_short_reference_admission`
   compares 77 ordinary GNU positives against shared-source and real native
   completion refusals: unsupported short names, computed names in both
   delimiters and concatenations, six assignment operators, all ordinary
   modifiers and computed define headers. Every unsupported declaration must
   refuse before publishing sites. The existing balanced scanner now classifies
   every short dollar token; escaped `$$`, trailing literal `$`, builtin/scoped
   arguments and already-pruned dead branches remain supported.
   `test_native_returned_archive_uses_finite_source_admission` validates one real
   native archive, then changes its source payload and matching inventory digest
   coherently while preserving byte length. Short-reference, computed-assignment
   and computed-define payloads must reach and fail the named admission boundary.
   These are actual returned-archive consumer mutations, not native source
   custody mutations or claims that the pre-fix entire modified archive succeeded.
   `test_native_completion_declaration_classifies_sites_and_non_site_context`
   checks parsed ordinary-site/dependency facts and deliberate recipe, target,
   directive and nested literal-define contexts. Literal define bodies retain
   dependencies but are not ordinary assignment sites; arbitrary macro/eval
   execution still needs broader supplier qualification.
   `test_native_completion_eval_assignment_requires_source_provenance`
   requires a dead literal macro to preserve empty output and undefined VALUE
   without manufacturing its completion. Evaluating the macro must return
   `original` under ordinary GNU and native execution without completion
   evidence. Completion-enabled execution must instead explicitly refuse the
   evaluated-source invocation lacking admitted provenance and clean
   owned pins/processes; it must not publish a success-shaped archive omitting
   the supplier. Rule-only eval is also unsupported with completion observation:
   it can enable secondary expansion without hitting an assignment callback.
   Native execution without completion observation remains unchanged. Direct
   `eval` and `call eval` both refuse at shared source admission. The existing
   verified eval ancestry and runtime refusal remain defense in depth.
   Version-3 legacy readers are unchanged;
   version-4 evaluated assignment provenance is unqualified, not silently
   ignored. Runtime buffer bytes modified by GNU are not original authored
   source and cannot borrow immutable-source authority.
   `test_native_completion_secondary_and_forwarded_suppliers_refuse` compares
   real ordinary GNU and native no-completion output for computed secondary
   prerequisites and `call` forwarding to `value`, `origin`, `flavor` and
   `call`. Completion-enabled source admission must explicitly refuse
   `.SECONDEXPANSION` (including either ordering in literal multi-target
   declarations), constructed target lists, evaluated-source invocations and
   these forwarded name-taking builtin targets rather
   than omit suppliers. Secondary expansion is a source-level unsupported
   completion context, not a recipe-dollar heuristic. Ordinary escaped dollars
   in values/recipes and custom literal calls remain supported.
   The returned-archive admission test also supplies coherent payload mutations
   for literal/computed/wildcard/evaluated secondary declarations and all four
   forwarded name-taking targets; each must fail
   its named admission boundary. This bounded limitation does not qualify
   arbitrary deferred prerequisite expansion or builtin forwarding.
   The source-context disposition bound to the third correction round uses
   shared Make-versus-recipe comment handling, not hash-specific supplier
   exceptions. `test_native_completion_recipe_hash_preserves_suppliers`
   exercises tab and inline recipes with single/double quoted hashes and
   actual shell comments: Make expands references in all three contexts.
   Literal suppliers must have real native completion events; computed
   suppliers must refuse instead of disappearing behind a hash. Returned
   archives reject removed literal suppliers and coherent tab/inline computed
   recipe payloads. `test_native_completion_make_comments_keep_non_recipe_context`
   preserves Make comments in ordinary/target assignments; nested expression delimiters and
   escaped literal hashes retain ordinary GNU output.
   A target-specific assignment's semicolon enters GNU rule context: hashes
   after that delimiter remain in its value. Only global assignments retain
   Make-comment semantics across semicolons.
   `test_native_completion_constructed_rules_and_exports` exercises escaped
   colons before a secondary-expansion target, both delimiters of an
   expansion-only rule, computed export lists, bare export and
   `.EXPORT_ALL_VARIABLES`. Each real GNU-positive form must refuse completion
   evidence explicitly while native no-completion execution retains its output.
   Expansion-only declarations are unsupported except direct `info`, `warning`
   and `error` expressions, which do not return parser source. This also
   refuses variable-only declarations that happen to expand to empty text;
   no evaluated value is inferred from immutable source spelling.
   Literal export lists and exported assignments instead retain their supplying
   assignment as a consumer in screening and exact source closure. Native
   completion events must bind that supplier even when the recipe uses only a
   shell environment variable. Returned archives reject removed literal-export
   suppliers and coherent payloads for every unsupported enabling form.
   `test_native_completion_define_hash_and_recipe_prefix_context` preserves
   hashes as literal multiline define-value text, including when the macro is
   expanded through `call` or in a recipe. Literal supplier dependencies require
   real native completion bindings; computed suppliers explicitly refuse.
   Ordinary comments outside definitions remain Make comments. Computed
   references in unused define bodies are conservatively unsupported too.
   Any `.RECIPEPREFIX` assignment or definition is unsupported for completion
   evidence, including a reset/default spelling: the finite source model does
   not infer the effective prefix. Real GNU and native no-completion controls
   retain non-tab recipes and tab-indented assignments after a prefix change.
   Coherent archive payloads and removed literal-define supplier selections
   must reject at their corresponding consumer boundaries.
   The third-round shared-context redesign uses one balanced, escape-aware
   statement boundary for declaration admission and inline comment handling.
   `test_native_completion_rule_context_and_special_aliases` refuses unqualified
   tab statements before an admitted local rule, including assignments after a
   global assignment resets rule context and leading tabs in included sources.
   Actual recipes after admitted rules remain supported. Special target names
   remove GNU-supported repeated `./` prefixes before secondary-expansion and
   blanket-export refusal; other filesystem normalization is not inferred.
   Real GNU/native controls and coherent returned-archive mutations cover
   literal/computed leading-tab assignments and both aliased special targets.
   `test_native_completion_private_and_directive_context` requires private
   global assignments across all six operators and private definitions to
   refuse completion evidence rather than disappear from assignment sites.
   Native no-completion execution retains ordinary GNU output. Target-local
   private bindings remain a separate non-global context. Make directive
   consumers are checked only in statement headers: plain/quiet/inline shell
   exports and export text in assignment or define values remain shell text,
   with their dollar references still analyzed. The plain recipe's command
   echo is preserved as well as its output. Coherent returned archives exercise
   every private-global declaration form.
   `test_native_completion_conditional_recipe_context_refuses_ambiguity`
   exercises false rules under all four conditional directives, else/chained
   else and nesting. Conditional transitions invalidate recipe context; rules
   within unresolved conditionals cannot authorize tab statements. Literal and
   computed tab assignments must refuse rather than disappear as recipes.
   Even genuine conditional tab recipes are unsupported until actual parser
   context is bound; native no-completion execution remains unchanged.
   A new unconditional local rule restores recipe admission. Coherent returned
   archive payloads cover skipped-rule literal and computed assignments.
   Repeat the no-completion execution control with computed assignment and short
   lookup sources: genuine native output must remain `original` without an
   invented completion trace. No native evaluator, source rewrite, new budget,
   or separate parser backend is introduced.
4. `test_native_readonly_completion_failed_make_has_no_successful_archive`:
   a real source error must retain Make's status and diagnostic, fail the
   invocation and clean active pins without a successful trace.
5. `test_native_readonly_completion_foreign_abi_and_returned_selection_refuse`:
   alter the decoded ABI's image binding, or the supervisor's copied selection
   while retaining an internally valid archive. The image admission or exact
   host request binding must reject it. These are binding mutations, not
   physical executable replacement.
6. `test_native_readonly_actual_trap_registers_preserve_original_state`:
   capture actual kernel general registers before and after every normal
   pass/source/assignment-completion hardware trap in the same real fixture.
   Require every field to remain unchanged except the intentional RF bit,
   all issued trap purposes to be exercised, and original output/values and
   cleanup to remain correct.
7. `test_native_readonly_actual_callback_register_and_restore_readback_mutations_refuse`:
   mutate the actual assignment callback's fetched register object, or alter
   the projection of an actual kernel GETREGS readback after SETREGS while the
   owned process remains stopped. Both must refuse explicitly before resume,
   fail the invocation and clean owned state. Before the guard, both cases
   returned successfully. The readback control is a projection mutation, not
   physical arbitrary kernel corruption.

The existing trap owner snapshots the original register state before callback
interpretation, permits only the existing RF update, checks the callback
projection, and verifies complete actual kernel readback after SETREGS before
rearming. Snapshot, readback and comparison-copy bytes consume the unchanged
shared metadata budget. This is normal owned hardware-trap restoration only;
it does not qualify cancellation, arbitrary descendants/signals or generated
source custody. No new trace version, restoration journal or execution
authority is introduced.

Existing version-1, mapped, input, job, signal, count and observer controls
remain required. Tests use ordinary immutable fixtures and automatic teardown;
there is no ROM, save or manual visual artifact. Dependencies are the existing
read/completion decoder, scanner, source validator, readonly dispatch/job
observations and shared budget. Mutable/generated/publication mode conflicts
and explicitly refuses; no new execution or write authority is granted.
ROM/RAM/save/localization/archival behavior is unchanged. General recursive
value observation, complete arbitrary descendants/register restoration,
natural generated-version lifecycle, the original unchanged-budget eight-query
family and every remote delivery gate remain unqualified.

## Run the real consumer

From a source checkout:

```sh
make -f scripts/validation_ownership/foundation.mk ownership-probe-check
make -f scripts/validation_ownership/foundation.mk ownership-probe-test
```

The first command reads the immutable `HEAD` tree, obtains the actual
`localization-check` prerequisite/recipe/variable structure from GNU Make, and
loads the real `chapterbundle` registry schema in a separate confined process.
Its JSON includes the concrete `src/data/ch2_bundle.json` input, not just the
directory-valued `default_source`. It does not generate game content or execute
the localization recipes. `--revision COMMIT` selects another immutable tree;
`--worktree` selects captured live bytes of paths admitted by `HEAD`:

```sh
/usr/bin/python3 -I -S -B scripts/validation_ownership/isolated_launcher.py --worktree
```

Default live capture requires gitlink paths to be genuinely absent or empty.
It rejects initialized/nonempty submodules rather than treating them as empty
or substituting their committed bytes. A caller needing their live contents
must use explicit same-budget source-path admission; the CLI does not infer
that authority. To exercise the real live consumer independently of an
already initialized build checkout, use a fresh linked worktree without
initializing its submodules:

```sh
git worktree add --detach build/ownership-probe-live HEAD
/usr/bin/python3 -I -S -B scripts/validation_ownership/isolated_launcher.py \
  --repository-root build/ownership-probe-live --worktree
git worktree remove build/ownership-probe-live
```

No ARM toolchain, ROM, credentials, GitHub request, or manual judgment is needed.
The host must provide Linux x86-64, GNU Make **4.3**, Python 3, a static-capable
GNU host C compiler, a glibc runtime, and working user/mount/network/PID namespaces. Where user
namespaces are unavailable, the existing noninteractive sudo namespace route
must work **through the trusted lifecycle watchdog**; the candidate child drops
to the original non-root runner identity before execution. An unsupported
watchdog/lifetime pipe or kernel lifecycle primitive rejects before namespace
launch, never by falling back to an unguarded privileged command. Other Make
native ABIs/platforms reject, rather than falling back to unconfined evaluation.
The native observer uses GNU Make's exported 4.3 data
layout and the Linux syscall-entry/exit information API. Linux **5.12 or later**
is required for atomic recursive mount attributes.
Kernel pidfds, Python's pidfd signal interface, per-tracee `prlimit`, private
proc child visibility, and ptrace vfork-completion stops are also required.
Missing lifecycle primitives reject before a payload runs; there is no
numeric-PID generation-check or unconfined fallback.
Native C++ tool consumers additionally need the existing host C++ compiler.
The producer suite's real graphics/linker controls also use libpng,
pkg-config and ARM binutils; its extended Build owner installs those existing
consumer prerequisites. They are not requirements of the standalone registry
command above.
The existing required `extended-host-tests` Build worker runs this complete
process suite through `ownership-probe-test` in full Build mode, in parallel
with the host localization work. Lightweight `tests/workflows` checks verify
the single full-mode owner, complete unittest selection and absence of duplicate
native discovery in the host job. Metadata-only and review-first preflight runs
do not execute that native owner. The protected host command sequence, job
timeouts, combined summary and all candidate/master requirements are unchanged;
there is no added workflow, job or required-context name.

## Trust boundary

Candidate Makefiles are programs, including their parse-time `shell`, `file`,
`eval`, include, recipe, and load behavior. Candidate registry modules and
native tool sources are also untrusted. The caller must load this package from
its trusted revision, **not import the candidate package into the supervisor**.
Trusted entrypoints use Python `-I -S -B`: isolated mode alone still imports
system `site`, including `.pth` and `sitecustomize` hooks. The public Make/direct
entry, namespace supervisor and watchdog also reject startup without `-I -S`.
The trusted version query and confined Python commands likewise suppress site
initialization. An owned-prefix hook regression exercises the actual launch
vectors without modifying global site directories.

Each capsule has a private mount, network and PID namespace, a read-only chroot,
no capabilities, `no_new_privs`, and no inherited descriptor beyond standard
input/output/error. Candidate source mounts preserve observable Git executable
bits but are **noexec**. No proc filesystem, device-FD aliases, host home,
credentials, or service sockets are mounted inside the candidate root.

Recursive bind mounts receive their restrictions through `mount_setattr` with
`AT_RECURSIVE`, using an `O_PATH`-pinned mount root. Read-only, noexec, nosuid and
nodev apply to every copied submount, not just the top bind. Attribute clearing
is never requested, so stronger source restrictions remain intact. The initial
root is recursively sealed before deliberate writable work/control mounts and
the separate read-only executable interceptor are installed. These exceptions
do not make inherited submounts writable or executable accidentally.
An unavailable recursive-attribute operation rejects before candidate
supervision; there is no top-level-remount fallback. All changes are confined
to the launcher's private mount namespace, not the host's source mounts.

The syscall supervisor remains outside the chroot. It follows every child,
uses kernel-identified entry/exit stops, fails on unadmitted syscalls, and
records actual open/read/mmap/metadata/directory accesses. Shared-memory
threads, untraced/reparenting clones, anonymous executable mappings, ptrace,
memfds and alternative executable dispatch reject. All failures remain failures
even if candidate code would otherwise catch an exception.

Signal-sending policy is shared across `kill`, `tkill`, `tgkill`,
`rt_sigqueueinfo` and `rt_tgsigqueueinfo`: every PID/TGID/TID must identify the
sender itself. Group, broadcast, sibling and mixed thread targets reject before
kernel delivery. Candidate pidfd signaling/acquisition, asynchronous FD signal
ownership, and POSIX timer/mqueue setup routes remain unadmitted. Self-local
alarms/itimers and signal handler/mask/wait operations are distinct from external signal authority; the
positive controls consume blocked, self-directed signals with `sigtimedwait`.

After dropping privileges, the trusted child bootstrap restores dumpability
before `TRACEME`; UID/GID, capabilities, `no_new_privs` and the zero core-file
limit remain unchanged. This permits the parent to observe candidate memory
without requiring `CAP_SYS_PTRACE`, including after the sudo route's
credential transition. Candidate `prctl` cannot change that observation state.
Pathname reads stop at NUL within each aligned ptrace word, so a valid string
ending at an unmapped page does not require reading the next page. Strict
UTF-8 and the 4,096-byte pathname bound still apply. Ptrace errors identify
their request rather than collapsing distinct failures into bare `EIO`.

Candidate `symlink`/`symlinkat` and the entire `rename`/`renameat`/`renameat2`
family reject before execution. A symlink target is relative to its containing
directory, not the creating process's cwd; a moved cwd/dirfd ancestor also
changes kernel `..` resolution without changing the recorded path. Neither
alias is needed by the supported consumers. Denying them keeps candidate
output-path authorization and subsequent FD authority bound to the same
destination, rather than pretending lexical normalization resolves an alias.
Hardlinks can only join authorized `/work` files and consume creation quota.
Existing trusted runtime symlinks are resolved component-by-component within
the guest root, before `..` and with each syscall's final-component follow
semantics. Absolute links never resolve against the supervisor's host root.
Open, cwd and directory-FD records retain that authorized destination, so a
runtime alias cannot disguise an undeclared `/repo` access as a library read.

Output directories must remain removable by the original runner. Pathname
`chmod`/`fchmodat` may retain owner read/write/search permissions, but may not
remove any of them, even when the path currently denotes a regular file: a
sibling could replace that file with a directory before kernel dispatch.
`fchmod` is permitted only on a kernel-confirmed regular-file FD, never a
directory or a copied directory descriptor. Restrictive `mkdir`/`mkdirat`
modes and umasks that remove owner permissions reject too; the trusted child
bootstrap starts with umask `022`. Regular-file `fchmod`, compiler executable
permissions and owner-accessible output directories remain supported. Thus
`chmod('/work', 0)` rejects before changing the host-backed directory instead
of making cleanup fail and masking the original rejection.

### Mapping, protection and fork contract

Anonymous memory must be private. Shared anonymous mappings reject even when
initially `PROT_NONE` or read-only; writable shared file mappings also reject.
Read-only mappings of immutable admitted source/runtime files remain supported,
including Python `mmap.ACCESS_READ`. No `/work`, pipe or device-backed mapping
is admitted, even with `O_RDONLY`, `MAP_PRIVATE`, a duplicated FD, or a closed
original descriptor: writes through another descriptor/process can otherwise
change still-unmodified private pages through the backing inode.

`mprotect` accepts only `PROT_NONE`/`PROT_READ`, never writable/executable
upgrades. `mremap` supports ordinary nonzero-old-size resizing with zero flags
or `MREMAP_MAYMOVE`; zero-size clones, fixed destinations, `MREMAP_DONTUNMAP`
and unknown flags reject. mmap's supported flags exclude growing/huge-page
and unknown allocation forms. Alternate shared-memory, protection-key,
process-memory, userfaultfd, remap-file-pages and asynchronous-I/O interfaces
remain unadmitted. These restrictions avoid a partial mapping-provenance model.

Private anonymous read/write mappings and copy-on-write fork remain supported.
Shared-VM clones require the existing suspended-parent `CLONE_VFORK` contract;
shared cwd/FD tables, candidate threads and unsupported `clone3` requests
reject. The real GNU C/C++ compiler/linker, native tools, Make 4.3 and registry
consumers exercise this policy without a compiler exception or untraced worker.

### Make, observer and interceptor

Only trusted Make (including its own include-remake re-exec) and its privately
mounted static interceptor can execute in the Make capsule. A native guard rejects **every** Make `load` before a module entry
point, including attempts to load an already-mapped host object. Candidate
executables, alternate loaders/interpreters, nested native Make through
`SHELL`, and noncontract `.SHELLFLAGS` are not execution authority.

The observation uses ordinary `/usr/bin/make -f FILE [declared assignments]
TARGET` context. It does not inject `-n`, `-B`, `-j1`, print-directory options,
or command-line `MAKE`/`SHELL`/`.SHELLFLAGS` overrides. Their normal values,
origins and flavors remain visible, including file-defined `/bin/bash` and
POSIX mode's `-ec` shell flags. Observer bootstrap variables and `LD_PRELOAD`
are removed before Make imports the environment. `GNUMAKEFLAGS`, like
`MAKEFLAGS`/`MFLAGS`/`MAKEFILES`, is an execution-authority channel and cannot
be supplied through either assignment origin.

The native `posix_spawn` boundary redirects execution without rewriting those
variables. Its dispatch notification is authenticated at the observer's
instruction pointer; an unnotified exec or candidate syscall cannot acquire
interceptor authority. The supervisor uses the child's actual stdout FD to
distinguish value-bearing `$(shell ...)` from ordinary recipe dispatch, even
when both commands have identical argv. The interceptor queries that
kernel-owned classification; no candidate environment marker decides it.
Unsupported native spawn paths fail closed.

Ordinary recipes are metadata-only: Make resolves its normal graph and
expands the recipes, but the interceptor does not run them or fabricate
registered output. This is not evidence of production recipe exit status,
artifacts or validation success. Expansions still require actual registered
command results. Makefile remakes, direct recursive Make and graphs containing
GNU Make's native recursive-command flags conservatively require registered
results for their dispatches, rather than silently suppressing recursion.
The fixed process/resource bounds still include interceptor children.

Make's runtime is captured once per session from its actual ELF interpreter
and that trusted interpreter's bounded `--list` dependency closure. Canonical
system tool/library paths, resolved aliases and their ancestors must be
root-owned and not group/other writable. The captured regular-file bytes are
copied into each read-only Make capsule; neither a Debian multiarch libc path
nor a live `/usr` mount is assumed. Non-multiarch `/usr/lib` layouts retain the
same GNU Make 4.3/glibc ABI requirement. No candidate ELF, `ldd` script, ambient
preload/library path or repository cwd participates in runtime discovery.
Later capsules reuse the immutable capture, not mutable aliases or host reads.

Make runtime permissions use those exact captured files and necessary metadata
parents, not library-directory prefixes. Finite loader cache and architecture
search probes are permitted only during trusted pre-observer startup and only
for absent owned-view paths. Once the observer is ready, candidate evaluation
cannot reuse that exception. Unrequested runtime files reject rather than
silently becoming sparse absence. The explicit runtime-input layer below is
optional; mandatory interpreter/ELF closure remains in the core and cannot be
replaced by a declaration.

### Explicit runtime discovery inputs

Issue [#227](https://github.com/laqieer/fireemblem8-expansion/issues/227) adds the
default-empty `ProbeSession(..., runtime_files=(... ,))` argument for Make's
existing runtime discovery. It supports optional toolchain/header detection
and metadata-only stock-tool recipe observation without host-directory grants:

```python
include_names = ("build-" + fixture.name, ".dep-" + fixture.name)
with ProbeSession(
    loader, scratch_root=scratch, budget=budget,
    runtime_files=(
        "/usr/include/newlib/stdlib.h",
        *("/usr/include/" + name for name in include_names),
        "/bin/mkdir",
        "/bin/env",
        "/usr/bin/env",
    ),
) as probe:
    observation = probe.make("requested-target", commands=commands)
```

Each request is an exact, bounded absolute pathname below the trusted system
tool/library roots or `/usr/include/`. The file and existing ancestors must
be root-owned and not group/other writable. Capture accepts an ordinary regular
file or **genuine absence**, not a directory, symlink, FIFO, device or
set-id/sticky file. Duplicate/overlapping declarations, nonstock ancestors,
changed captures, and collisions with mandatory images (including their
canonical aliases) reject. Mandatory image reservations are separate from
optional captures: ordinary original/canonical aliases of the same input
(for example `/bin/cat` and `/usr/bin/cat`) and overlapping absent prefixes
reject in either request order. The only dual-spelling exception is the
explicit `/bin/env` plus `/usr/bin/env` pair with matching captured state;
it cannot replace another mandatory image, including canonical bash.
The example's relative include names come from a uniquely owned fixture
directory and must also be used by that fixture's Makefile. Verify the resulting
absolute paths are genuinely absent; ambient `/usr/include/build` or `.dep`
entries are not test prerequisites. Never remove host contents to make a
fixture pass.

`probe.runtime_inputs` exposes frozen
`RuntimeInput(path, data, mode, parents, canonical, aliases)` records during the
session. `data is None` means captured absence; `parents` records actual
ancestor presence. Regular files retain exact captured bytes and permission
bits. An explicitly absent prefix also proves its descendants absent. No
other missing name gains authority: an unrequested existing **or missing**
file fails at its attempted operation instead of supplying an empty wildcard.
An original stock capture such as `/bin/missing-tool` preserves genuine
`/bin/missing-tool/child.h` and `/usr/bin/missing-tool/child.h` absence.
Component boundaries remain exact: `missing-tool-other`, `..` spellings and
unrelated aliases are not covered. A canonical-only capture does not add an
unrequested original alias merely because another input uses the stock root.
Parent metadata does not authorize content reads or directory enumeration.
Requests never grant writes, arbitrary program dispatch, executable mappings
of optional images, a library-prefix read, or candidate-phase loader probes.

Parent components in an actual Make pathname cannot acquire optional runtime
authority merely because guest resolution reaches a captured canonical path.
The check retains the raw pathname and its dirfd/base context until that
authorization boundary, including optional dispatch. Independently authorized
registered-command runtime, source-parent and mandatory-runtime operations stay
supported; shared mandatory directory metadata does not need an optional grant.
This is not a global traversal ban in the guest resolver. Complete metadata
comparison still uses the actual supported kernel results without masking.

The owned optional runtime backing is constructed once and reused by Make and
the existing trusted metadata helper. It is read-only, separate from the
persistent complete read-only/noexec source backing, and removed with that
session's owned scratch. Neither a live include tree nor a live Make runtime
mount is introduced. Capture, materialization, request records, actual
metadata, native comparison and output all spend the existing report budget;
no limit or accounting meaning changes. `execution_digest` binds the runtime
capture, while `semantic_digest` still reflects the actual observed owner.
Omitting the argument preserves the default API and execution identity.

Optional metadata uses the **same** complete syscall records and native
comparison as [source metadata](#complete-metadata-and-static-reuse), including
real status, flags/masks, inode, ownership, timestamps and returned buffers.
This is actual guest metadata, not fabricated host inode/UID/stat values.
The persistent optional backing permits unchanged Make observations to compare
without recreating their files, directories or stock symlink.
Changed metadata cannot match merely because names/types still agree.

Registered commands retain their existing runtime, source and executable
permissions; `runtime_files` does not grant them additional host paths.
If an already permitted command observes an explicitly captured file, its
operation joins the same metadata protocol. Compatible operations, such as
read-access tests, can reuse their genuine result in Make. A live command
runtime inode or permission failure may differ from Make's owned capture;
complete comparison then rejects reuse or requires genuine execution, never
normalizes away that difference. Filesystem-capacity fields and unsupported
metadata retain the core's explicit unsupported-reuse boundary.

### Stock runtime spelling and env recipes

On the supported root-owned `/bin -> /usr/bin` layout,
`runtime_files=("/bin/mkdir",)` captures the original path, canonical target
and actual stock root link. The owned guest reproduces that confined relative
link, preserving ordinary PATH, `realpath`, variable origins/flavors and native
Make dispatch. It does not rewrite PATH or admit `/bin/rm`, `/bin/env`,
escaping `..` spellings, or other unrequested aliases.

A requested stock spelling of an existing intercepted program keeps the
trusted interceptor image rather than overwriting it with the host program.
It grants metadata/authenticated dispatch only; reading that substituted
program image is forbidden. Explicit present `/bin/env`, `/usr/bin/env`, or
both in either request order use the same narrow seam. Canonical access to the
requested stock target is supported; a canonical-only request does not
authorize its unrequested `/bin` spelling.

Ordinary `env -u ... $(PYTHON) ...` recipes remain **observations**, not
execution of `env`, Python or a unittest payload. The observer authenticates
native dispatch and the existing interceptor suppresses the ordinary recipe.
Captured absence does not materialize an env interceptor. Eager `$(shell ...)`,
recursive recipes and include-remake invocations still need an exact
registered real result. Public `Command` execution of env remains unsupported;
capturing another program such as `cat` grants no dispatch permission.

The complete human procedure and focused positive/adversarial automation are
indexed as
[`TC-WORKFLOW-PROBE-RUNTIME-INPUTS-001`](test-cases/workflow-governance.md#tc-workflow-probe-runtime-inputs-001-observe-explicit-runtime-inputs-without-executing-recipes).
It uses the existing Linux/GNU Make/glibc/compiler/namespace prerequisites
above; stock-path controls require actual ordinary root-owned `mkdir`/`env`
files and the stock `/bin` link. Nonstock layouts reject instead of guessing
aliases. No ARM tools, ROM or subjective manual judgment is needed.

This framework capability depends on the delivered **#206 / PR #212** core.
#227 is a standalone `master`-based root (depth zero). #226 is independent,
and #225/#228 are not prerequisites. #180 / PR #186 owns downstream
complete-root integration. Shared runtime/dispatch/metadata/test/doc seams may
need ordinary conflict refreshes, not artificial stack dependencies.
Generated results, native Make registration, dependency-only compilation and
same-report view selection are non-goals. Game/profile conflicts are **none**:
no ROM/RAM, save/migration, config identity, localization, generated game
output, modern/archival profile, workflow/publisher, service or permission
change. Rollback removes this optional layer or fixes it forward without
widening the mandatory core's authority.

### Registered commands and native tools

Registered Python/printf/uname commands run in a distinct capsule with **no**
Make event, mapping, observer or result mount. Python always receives `-I -S -B`.
Only the trusted bootstrap may perform that capsule's initial exec. Subsequent
`execve` attempts reject before kernel dispatch, even for the same executable,
runtime alias or otherwise isolated argv. Fork/vfork/clone descendants inherit
the spent bootstrap state; they cannot reacquire startup authority. `execveat`,
including descriptor/empty-path dispatch, remains unadmitted. This also applies
to session-issued native commands. Compiler subprocesses and authenticated
Make/interceptor transitions retain their existing separate execution policy.
The supported runtime admits its standard-library/shared-library paths and a
small explicit set of runtime startup probes, not arbitrary `/usr/share` data.
Runtime probes into absent proc/etc locations stay absent: they do not create
a proc or credential mount. Compiler capsules additionally admit trusted
compiler programs, headers, libraries and bounded empty search probes.

`compile_native` compiles only declared candidate sources in that channel-free
capsule. Its resolved host compiler/toolchain is trusted; no candidate compiler
flags/plugins are accepted. The output must be a bounded x86-64 ELF with valid
program headers, no writable executable load segment and only the admitted
dynamic loader. A session-issued `NativeTool` is sealed before `native` runs it
in another channel-free capsule. It never becomes a mapped Make-capsule
executable. Explicit readonly native-Make admission at `/native/tool` uses the
same issued object and seal; generated/source-path placement remains unqualified.
Changed or foreign-session ELF handles reject. Native Make registration and
declared generated-file results belong to #225, not this direct native API.

## Source declaration and identity contracts

`Command` declares argv, admitted code paths, candidate source paths/globs and
directories permitted for enumeration. The supervisor resolves selectors
against one captured snapshot and uses its persistent complete backing. A selector that
matches a symlink/gitlink rejects; it does not silently drop that input.
Literal selectors identify exact repository-relative paths, not matching
basenames in unrelated directories. Explicit `*`, `?` or `[` glob selectors
retain their pattern semantics.
Undeclared data open, mmap, stat/access/readlink, directory/glob or dynamically
constructed paths reject. Code imports have a separately admitted code set and
bounded, absent import-cache probes.

Python module and import-cache exceptions prove absence against the complete
**active** owned source view. An existing undeclared `__init__`, module variant
or cache path rejects rather than being omitted from a sparse command mount.
Unadmitted nonregular namespaces reject before any
absence claim. Truly absent related probes remain permitted and spend bounded
attempt bookkeeping without successful-consumption credit. Compiler negative
search probes also require genuine absence. No live-checkout substitution or
new import authority is introduced.

Authorization remains entry-time and fail-closed, but source/code/Make-path
evidence is committed only after a successful kernel return. A successful open
is pathname/existence metadata; successful stat/access and file mappings also
remain observations. Failed calls add no evidence, and read/pread/readv/readlink
need positive returned bytes. A later failed operation does not erase a prior
valid observation. FD duplication/state changes are applied only on success.

### Independent cumulative observation allowance

Issue [#262](https://github.com/laqieer/fireemblem8-expansion/issues/262) adds a
**framework capability** for repeated target queries and cached-metadata or
BASE/CURRENT revalidation over small inventories. It separates cumulative work
from per-capsule/source cardinality, not an incorrect observation decision.

| Public API | Contract |
| --- | --- |
| `Limits.observations: int \| None = None` | Appended after the existing positional fields; `None` retains entries-only lifetime tightening |
| `Limits.observation_count` | Read-only numeric effective total: `entries` for `None`, otherwise `observations` |
| `Limits(entries=64, observations=128)` | Up to 128 cumulative observations across capsules, but at most 64 per capsule and per captured inventory |
| `Limits(entries=64, observations=32)` | Independently tighten cumulative work to 32; inventory admission still uses 64 |

Ordinary explicit values must be positive integers at most 32,768. Booleans,
floats, strings, zero, negatives, nonfinite and above-maximum values reject.
Omitted and explicit `None` preserve every default and stricter entries-only
caller. Typed diagnostic subclasses retain the existing dataclass-field
default ceiling validation: an explicit observation field default supplies
that field's ceiling; a `None` default uses the class's entries field default.
This is not a new production profile or permission to raise ordinary caps.
No configure/gameplay flag, source-inventory increase or byte-policy change
is involved.

The effective observation-record count bounds the **entire report**. Each
capsule receives `min(entries, observation_count - observations_used)` and
limits the sum of attempted `consumed`, `code_consumed` and `accessed`
records to that actual capped grant before insertion. Repeating a value in the
same collection and capsule spends no additional record or bookkeeping bytes;
a failed attempt retains its charge, and later successful consumption does not
charge it again. The independent observation-byte limit remains unchanged.

The closed supervisor result includes the actual attempted-record sum as
`observations`. `ProbeSession.observations_used` accumulates it alongside
process/syscall totals, including failed capsules. Missing, malformed,
out-of-range or inconsistent counts reject rather than defaulting to zero;
successful observation sets and bookkeeping bytes constrain the count.
Command, native, compiler, static Make and metadata revalidation share this
total. Cache hits do not repeat candidate execution, but genuine metadata
queries are new charged work. Neither reuse nor failure resets the counter.
With no records remaining,
another capsule rejects before launch. A terminal failure also forbids cached
replay. The separate captured-source entry bound still uses `Limits.entries`;
source capture is not a filesystem-observation charge.

Producer checkpoints validate both their capped capsule count and prospective
effective lifetime total. Parked/nested work spends that same total; resumption
never exceeds the capsule's initial cap or its settled count plus the remaining
lifetime allowance. Closed reports and decoded metadata still use the actual
capped config, not the larger lifetime allowance. Source/gitlink inventories,
snapshot admission, producer-peer ancestry and regex batches keep `entries`.
Independent metadata record, frame, path, syscall-buffer and VM limits remain.
Observation authority is checked before settlement even when the terminal
native report failed. Such a report cannot reclaim an initial grant that
nested work has reduced: its prospective shared count must still fit.
Valid failed counts retain their exact observation and byte deltas; other
failed-resource overshoot/charging semantics are unchanged. Malformed counts
reject before changing shared observation authority, not by clamping or refunding.

The [indexed case](test-cases/workflow-governance.md#tc-probe-observation-allowance-001-separate-cumulative-observations-from-capsule-and-inventory-admission)
uses real bounded capsules, metadata, caches, immutable views, nested requests
and guard-removal mutations. No full graph or capacity/fit claim follows.
Dependencies are the existing shared budget, producer and view lifetimes.
#180/#186 may integrate this root later; #196 remains downstream under
Discussion #174. No supported modern/archival, feature/locale, save/config,
generated-content, ROM or GBA RAM behavior changes, and no profile conflicts
or manual-only criteria apply. Rollback is an ordinary revert of this root.

Raw metadata observation accounting is unchanged: requested before/after
buffers, null/EFAULT failures and per-record legacy JSON dedupe all still spend
the original observation budget. Cache retention still charges
`encoded(ProcessOutput.metadata)`, and replay still charges the unchanged
binary-frame write/read plus helper observation. The transport delta is only
the old complete supervisor report bytes minus the new complete report, the
parent's decoded-frame reservation F and an additional encoded-payload
reservation P. Both F and P spend control before decoding. P is the ASCII
payload length and conservatively covers retaining streamed encoded parts
while joining the final payload; this cost is not hidden by a smaller wire size.
The trusted supervisor streams frame parts through zlib using one non-growing
4096-byte hex-decoding scratch buffer, passed directly as a memoryview.
Base64 conversion uses at most 4095 input bytes per part and a two-byte carry,
without copying complete compressed chunks. Fixed zlib state and bounded
transient codec chunks are not transport savings or changes to the existing
guest `memory_peak` metric.

Only compile-mode **metadata** probes of exact `/proc/self/exe` pass the
compiler exception before the general namespace denial. The capsule has no
proc mount or fabricated executable link: stat/lstat/access/readlink return
authentic absence, not a host executable identity. This grants no source
consumption, file read/write/exec, unknown descriptor or neighboring proc/sys/
device access. Ordinary command/Make modes remain denied, with Make's earlier
exact runtime guard taking precedence before observer-ready; the interceptor's
existing private protocol and other trusted absent-runtime probes are unchanged.

Directory evidence comes from parsed `getdents`/`getdents64` bytes, not the
declared sibling list. Only complete, actually returned names are credited;
failed calls, EOF, dot entries, deleted inode slots and unused buffer tails do
not credit unseen files. Returned symlink names do not observe their referents.
The x86-64 record layout, UTF-8 names and record bounds are checked within the
existing 64 KiB syscall-memory limit, and returned directory bytes spend the
aggregate observation budget. Failed lookup attempts still spend bounded
bookkeeping without becoming consumed-source evidence.

Access observation includes metadata and enumeration, not merely byte reads.
A directory observation consumes only the declared names it exposes. Command
success requires **declared = permitted = consumed** candidate sources.
`Command.directories` explicitly authorizes source enumeration; `.` denotes
the repository root. Code/source ancestors permit necessary metadata, not
implicit directory listing. Imports that actually enumerate their code paths
must declare those paths too.
Declarations must name actual directories in the selected active view before
admission or cache reuse. The syscall boundary checks directory type again;
putting a regular file in `directories` never grants its content.

For every command, the existing read-only/noexec source mount uses the complete
active owned view. The guard requires that same
directory backing before returning entries and rejects incomplete sparse or
nonregular namespaces. File reads still require their separate code/source
declarations; listing a name does not grant its contents.
The standalone registry consumer declares its import/source directories and
captures recorded gitlinks from already available local object databases so
its root listing is complete. Missing databases/pins reject; no fetch or live
submodule mount is introduced. A capture resolves the common Git directory
once, without omitting any individual gitlink pin/database checks or reads.
The shared `scripts/validation_ownership/python_commands.py` helper keeps that
same ordinary import model: it prepends `/repo` for Python's normal resolver
rather than synthesizing package objects. A raw immutable tree lacking gitlink
admission therefore still fails at the actual root enumeration boundary, while
the complete capture plus explicitly declared source ancestors succeeds with
the standard namespace-package `NamespaceLoader`, `__spec__.origin is None`,
and the real `/repo/scripts` namespace path.

The trusted registry driver accepts a repository-relative source argument.
Schema-reported paths may be repository-relative or absolute beneath `/repo`;
parent components and other absolute roots reject. Lexical normalization does
not read the filesystem, infer a source-directory base, or replace the required
exact agreement with actually consumed source paths.

For `--worktree`, default admission uses HEAD paths, not the index or every
nonignored live file. The admitted paths contribute actual live bytes,
executable modes and genuine absence/type information, not immutable HEAD blob
contents. Callers may supply an explicit same-budget source-path map.
An actually empty live gitlink directory is captured as such; nonempty
initialized contents require explicit path admission and are not traversed
automatically. Unsupported or unsafe type changes reject.
Those bytes are frozen for the report, not exposed through a continuing live
host mount. Immutable revision capture retains exact-pin object-database semantics.

### Complete metadata and static reuse

Every registered command uses the same persistent complete read-only/noexec
source backing as native Make, regardless of `Command.directories`. A
declaration grants an operation, not selection of a sparse filesystem. Implicit
source/code ancestors retain permitted metadata but do not become enumerable.
Directory declarations are type-checked; names-only listing never grants member
contents. The guard uses the mounted read-only view, never a writable alias.

`ProcessOutput.metadata` contains operation-aware source observations and any
already-permitted observations of explicitly captured runtime files:
syscall number, canonical guest path, flags, mask, buffer size, directory
offset, actual signed kernel result, and complete input/output buffer bytes.
Stat/lstat/fstat/newfstatat, supported statx/fstatfs, access/readlink variants
and directory results retain their actual supported ABI data. Failed operations
keep their status without becoming successful source consumption. Unreadable
buffers and unsupported requests are explicit, not empty successful records.

The supervisor-to-parent transport for those records is now a strict metadata
envelope:
`{"format":"vo-metadata-frame","version":1,"encoding":"zlib-base64","record_count":...,"decoded_size":...,"payload":...}`.
The payload inflates to the same canonical binary frame used by
`validate.meta`; the parent decodes that frame back into the public legacy
list/tuple records, so `ProcessOutput.metadata`, cache keys, replay inputs and
consumer semantics stay unchanged. There is no same-package fallback to the old
list-valued supervisor field.

The fixed native comparison routine in the existing interceptor serves
command-cache checks. It reissues the
recorded operations in the authoritative guest context, using the complete
buffers and original flags/masks. Input buffers retain caller-owned padding;
kernel-returned metadata is neither masked nor normalized, and old output
bytes are not substituted for fresh kernel results.

A private metadata-validation invocation uses the existing supervisor, mounts,
limits and sole reaper, but executes only that trusted static routine. It is
not a registered candidate command and exposes no control channel to candidate
code. Metadata paths/operations are constrained by the selected completed
record. Ordinary candidate capsules remain channel-free. Live Make requests
obtain actual isolated results through the producer rendezvous, never a Python
substitution or fabricated replay. The parent reserves F and P before
decompression, writes only within one preallocated frame, parses memoryviews
of that frame and releases it before later record validation. Every zlib
decode has a positive output ceiling of at most 4096 bytes and less than F;
after F bytes, a one-byte sentinel permits a split footer but rejects further
output before writing. No decoder flush or full-frame conversion is used.
Helper replay retains the existing binary ABI. Malformed envelope fields,
base64, zlib streams, decoded-size/count mismatches, noncanonical frames and
tampered replay inputs fail closed before execution. Reusable results still
use this complete comparison.

Native event writes are recorded only after complete successful kernel writes
in the existing closed supervisor report. Each live request is validated before
its execution; the final transcript must equal the complete native writes and
fulfilled receipts. The argv/hash/count framing remains: a request has no
result slot, and its completed event names the supervisor-assigned result.
Unsupported pure reuse causes genuine execution rather than stale output. There
is no new protocol version, signer, broker, namespace service or filesystem
simulation.

An unchanged compatible result can be reused. Changed metadata causes genuine
execution; a result that cannot be reproduced in the native Make context
rejects rather than supplying stale matched output. In particular, filesystem
capacity from `fstatfs` may change even without a source edit. Its complete
returned buffer remains part of validation; no universal stable mount-ID or
free-block assumption is made. Invalid-pointer metadata can report its real
error through a fresh execution; it is not admitted as an unsupported cached
replay.

Metadata revalidation has real process/syscall/observation and byte costs.
Request records, complete buffers, descriptor metadata, private map reads and
native event-write evidence spend the existing cumulative bounds. Cache hits
avoid candidate execution, not the required metadata validation cost. No
counter, cap, deadline or budget meaning is relaxed.

The live extension does not recreate historical source contexts. Validated
generated files publish into the still-live view at actual dispatch. Names/type
agreement in the unapproved reference is not sufficient evidence; complete
P acceptance remains separate from this bounded vertical checkpoint.

Registry success additionally requires the typed reported `source_paths` to
equal that set. Reported JSON is candidate data, not supervisor evidence.
The generated-registry driver obtains cardinality through the selected schema's
existing `manifest_record_count(records)` API. Structured records and
sequence-backed tables therefore retain their schema-defined meaning; a schema
missing that contract fails rather than falling back to container length.
The shared command factory also obtains the schema's optional metadata-only
`manifest_support_paths()` declaration. The final reported source set includes
those actual count inputs, and native declared/consumed/reported equality still
applies. See the [shared producer contract](ownership-probe-producers.md) for
the separate primary/support selection boundary.
Malformed UTF-8, duplicate/nonfinite JSON, stale/omitted/extra paths, malformed
frames and unused source declarations reject. Directory source selectors must
be explicit: the foundation does not infer a generator's ownership from a
filename or arbitrarily treat every file under `src/data` as generated data.

### Exact captured gitlink sources

`git_tree_entries` optionally accepts typed `GitlinkSource(path, git_dir)`
requests. Each names a gitlink already present in the captured superproject and
an existing local Git object database, not a checked-out source directory:

```python
from scripts.validation_ownership.authority import GitlinkSource

entries = git_tree_entries(
    root, revision, budget=budget,
    gitlinks=(GitlinkSource("mgfembp", module_object_database),))
loader = AuthorityLoader(root, entries, revision, budget=budget)
```

The commit is derived only from the superproject's `160000` entry. There is no
pin override, duplicated committed pin or automatic fetch. Git reads the exact
commit's complete bounded source tree; original paths such as
`mgfembp/src/main.c` and `mgfembp/include/proc.h` stay unchanged under `/repo`.
`GitTreeEntry.git_dir` retains object-database origin for admitted link records
and their blobs. Existing loader and Snapshot reads group those blobs by origin
under the same report budget; no second loader/session/sandbox is constructed.

The database must be an existing canonical absolute directory without symlink
components. Unrequested links, wrong/unavailable/non-commit objects, a working
checkout substituted for a database, escaping/conflicting names and nonregular
or nested-gitlink source entries reject. Only regular files are expanded; there
is no recursive submodule discovery. Explicit empty pins expose only their
empty root. Git storage uses the same trusted local-object-database boundary as
existing source capture, not a new hostile-Git parsing service.

Admitted gitlink namespaces are read-only/noexec. Source declarations still name actual regular
files/globs; literal names keep their exact root-relative matching. Native and
registry calls retain successful-return source accounting. Explicit
`owner_inputs=("mgfembp",)` can name the whole captured pin; file owners continue
to identify their actual path/mode/content. An unrequested pin is not an owner.

Pin admission requires a captured immutable superproject loader. Same-report
CURRENT/BASE selection uses #226's selector below. Checked-out submodule files and moving branch HEADs never
substitute for the captured commit. New entry metadata, blob reads and
materialization retain the original entry/byte/run/deadline bounds; no source
inventory/hash ledger or live submodule mount is added.

Two identities deliberately serve different purposes:

* **Execution snapshot:** complete admitted Git path/mode/type/content state,
  including current live bytes, executable bits and symlink targets for a
  worktree snapshot, and immutable gitlink identities. It binds execution/cache
  reuse together with complete actual metadata revalidation. There is no
  process-global cache or per-command sparse object identity.
* **Semantic owner:** only the requested target's native observations, requested
  domain state, declared/recipe-owning inputs and relevant command output.
  Unrelated source/docs/symlink/mode changes cannot change every Make owner.
  Dynamic command identity comes only from matched events in the final
  successful Make pass. A branch visited while an unresolved expansion
  temporarily returned empty cannot retain its discarded command's code,
  source or output identity. Results remain indexed by canonical event command;
  identical command/input/output records are emitted once even when aliases or
  repeated events use them. Every speculative command still requires complete
  authority, successful source accounting and the same aggregate charges/cache.

Unique-name assignment metadata is canonicalized by name, retaining each
origin and value. Environment and command-line assignments, including mixed
origins and recursive references, therefore have the same semantic identity
when reordering them leaves the native target/domain observations equivalent.
The executed argv and environment application order are **not** reordered.
Order-sensitive Make observations, such as a `MAKEOVERRIDES` value or a
prerequisite selected from it, remain intact and continue to change the digest.
This does not expand the metadata-only recipe contract into production recipe
execution or artifact validation.

Do not hash the whole `MakeObservation` when computing an owner identity:
consume `semantic_digest`, not `execution_digest`. These are ephemeral
execution/semantic boundaries, not committed source ledgers or ROM identity
requirements.

## Aggregate lifetime and resources

The [content-only producer policy](ownership-probe-producers.md#content-only-publication)
is an explicit `Command.publication_policy` choice, defaulting to `replace`.
It preserves real unchanged-content outputs without erasing an invocation,
weakening source/owner/nofollow checks or treating requested mode as effective
mode. Effective metadata is confirmed through the existing private protocol
and selected publication view; private `ProcessOutput.generated` stays
distinct. No-op comparisons and confirmations remain charged, while actual
public writes/creations occur only when needed. No budget is increased or
refunded, and no full-report affordability is implied.

Create **one** `ProbeBudget` before loading a report's tree, then share one
`ProbeSession` across all Make targets, variants, commands and registry calls:

```python
from pathlib import Path
from scripts.validation_ownership.authority import AuthorityLoader, git_tree_entries
from scripts.validation_ownership.budget import ProbeBudget
from scripts.validation_ownership.make_probe import ProbeSession

root = Path.cwd()
budget = ProbeBudget()
entries = git_tree_entries(root, "HEAD", budget=budget)
loader = AuthorityLoader(root, entries, "HEAD", budget=budget)
with ProbeSession(loader, scratch_root=root / "build/test-artifacts/probe",
                  budget=budget) as probe:
    observation = probe.make(
        "localization-check", makefile="localization.mk",
        variables=("LOCALIZATION_OUT_DIR",), owner_inputs=("localization.mk",))
    print(observation.semantic_digest)
```

The capture, loader and session APIs all require an explicit `budget`;
omission, `None` and foreign-budget composition reject. `git_tree_entries`
returns a mapping-compatible `GitTreeEntries` carrying the capture budget.
Detaching it into a plain dictionary does not create valid loader authority.
The loader and snapshot retain that same budget instead of rebinding it at
session entry or clearing it at exit. Direct live/immutable authority reads
also spend the report's byte/run quota before any session starts.

One budget may enter only one session lifetime, including through another
loader or a second `with` on the same session. Rejected duplicate entry does
not tear down the active owner. Closing the report budget is terminal:
capture, reads, snapshotting, materialization and execution cannot restart
after closure or expiry. The complete-operation CLI/`consumer.check` remains
the convenience owner that constructs one budget and passes it through every
stage. This is explicit trusted-caller API binding, not a global budget service
or a defense against arbitrary Python object mutation.

The registry helper requires that same active owner:
`probe_generated_registry(loader, command=command, session=probe)`.
There is no optional/sessionless path or helper-level `scratch_root` argument.
Missing, `None`, inactive, foreign-loader or mismatched-budget authority rejects
without launching work. Repeated identical requests share the session cache;
distinct requests consume the same counters and quotas, including work already
performed by Make. Even cached results cannot be reused after that report's
deadline or a terminal budget failure. The production consumer passes the one
budget used for tree capture through its Make session and registry helper.

### Independent pending-record and plan admission

Issue [#260](https://github.com/laqieer/fireemblem8-expansion/issues/260) makes
two original-size admission boundaries explicit without changing `Limits()`,
adding a larger production profile, or changing any existing charge.

| Boundary | Public contract |
| --- | --- |
| `MAX_PENDING_RECORD_BYTES` | 1,048,576 bytes for each complete `charge("pending", size)` record, independently of cumulative traffic allowance |
| `MAX_PLANNED_STATE_BYTES` | 1,048,576 admitted planned-state bytes over one `ProbeBudget` lifetime |
| `budget.planned_state_bytes` | Cumulative plan admission, separate from `budget.bytes` and attempted `budget.states` |
| `budget.admit_planned_state(size)` | Admit one serialized state's nonnegative integer byte size, charge existing pending/global accounting once, then advance plan admission |

The common record guard covers whole binary stdin, whole lifecycle/child
launcher argv, normalized command authority and serialized planned states.
It does not split records into chunks or omit declarations to fit. Individual
argument, file, message and source-admission limits remain independent.
Invalid sizes, including booleans, reject through `MakeProbeError`.

A trusted finite planner uses the existing budget before adding a state to
its own queue:

```python
from scripts.validation_ownership.authority import encoded

budget.admit_planned_state(len(encoded(state)))
planned.append(state)
```

`ProbeSession.variants()` already performs this admission while materializing
its complete finite input, before any variant executes. Do **not** precharge
its states or call `budget.plan()` for queued states. The new seam does not
increment attempted states or launches; existing execution/view admission
continues to own those counters. It neither creates a planner nor accepts a
publication, source or execution authority.

Plan bytes accumulate across separate `variants()` calls and selected
CURRENT/BASE views. Executing a state, dropping a local list, or closing the
session does not refund or reset them. They are not measured live storage,
heap or RSS, and are not charged to the global sum a second time.
If record, aggregate plan, smaller pending or global admission rejects, that
state's plan counter is not advanced; earlier admissions remain charged.
The specific new errors identify a `pending record` or `aggregate planned-state`
admission limit. Budget failure remains terminal. `run()` may already count
an attempted launch before a pre-`Popen` rejection, as before.

Caller-side states, argv and serialized Python objects can already exist
before admission; this is not a claim that their allocation was prevented.
No coordinator AS/NNP policy or aggregate host-RAM guarantee is added.
The graph planner's use of this shared seam belongs to #180's later normal
integration, not this foundation root. Dependencies are the existing budget,
producer and view APIs; other feature/profile conflicts are none. Save/config,
generated content, locale, ROM/GBA RAM and modern/archival behavior are unchanged.

See [TC-PROBE-PENDING-ADMISSION-001](test-cases/workflow-governance.md#tc-probe-pending-admission-001-preserve-whole-record-and-lifetime-plan-admission)
for the bounded positive, original-source and independent-guard controls.

### Selecting immutable BASE/CURRENT views in one report

Issue [#226](https://github.com/laqieer/fireemblem8-expansion/issues/226) is a
framework capability for revision comparisons and deleted-source ownership.
It depends on the delivered #206 / PR #212 core's source, session, metadata
and lifecycle authority. Issue #226 is now a standalone `master`-based root
(depth zero). #180 / PR #186 consumes this seam for its broader report;
#225, #227 and #228 are **not** dependencies.

Deleted sources require the actual BASE declarations and bytes. Asking the
CURRENT registry whether it owns a deleted BASE path can incorrectly classify
that path as unowned. A union of paths, borrowed CURRENT classification,
another report budget or rewritten guest prefixes cannot repair that answer.

```python
budget = ProbeBudget()
current = AuthorityLoader(
    root, git_tree_entries(root, current_revision, budget=budget),
    current_revision, budget=budget)
base = AuthorityLoader(
    root, git_tree_entries(root, base_revision, budget=budget),
    base_revision, budget=budget)
with ProbeSession(current, scratch_root=root / "build/test-artifacts/probe",
                  budget=budget) as probe:
    current_record = probe_generated_registry(
        current, command=current_command, session=probe)
    with probe.select_view(base) as selected:
        assert selected is probe
        base_record = probe_generated_registry(
            base, command=base_command, session=probe)
    # The exact previous CURRENT loader, snapshot, backing and handles are active.
```

Use immutable revisions captured by `git_tree_entries` with the same explicit
budget and repository root. Another clone/worktree, foreign budget, detached
map, mutable alternate loader or wrong captured root/revision rejects before
changing the active view. Moving refs and later checkout edits cannot replace
captured Git object bytes. A default live session may select an immutable
view, then restores its already frozen live bytes; live snapshots cannot
certify immutable byte/storage reuse.

`select_view` yields the same session. It prepares a complete immutable
`Snapshot` and materialized source tree before switching loader, snapshot,
backing, command cache, mappings and native-tool ownership together.
Every capsule still mounts that complete view read-only/noexec at `/repo`.
File/content declarations, directory types, enumeration rights and genuine
absence checks remain separate. File-to-directory, directory-to-file,
nonregular and presence changes use the selected authority, never sparse
omission. The registry helper still rejects an unselected loader.

Each selection reserves a state from the same report budget. The session's
trusted runtime, interceptor, namespace route, signal handlers and sole reaper
are shared. There is no per-view deadline, budget, process owner or global
cache. Capture, selected-state metadata, new blob reads, copied storage,
real metadata validation and candidate execution keep their cumulative byte,
run, process, observation, syscall, creation and state costs. No refund, cap
increase or accounting reset occurs on restoration.

When #227's explicit `runtime_files` are also requested, their captured bytes,
absence, stock spellings and persistent runtime backing stay fixed across
CURRENT/BASE selection and restoration. Only source-view ownership changes.
`test_runtime_inputs_share_capture_and_control_quota_across_views` exercises
real Make mapping/revalidation with changed source bytes, unchanged runtime
inputs, metadata-only env dispatch and one cumulative control quota. The two
APIs remain independent capabilities; their composition creates no new budget
or prerequisite between their issues.

`Snapshot(loader, budget, reuse=previous_snapshot)` exposes only certified
reuse. `reused_paths` identifies regular entries independently admitted by
both immutable captures with identical root, budget, original path, mode,
type, Git object ID and object-database origin. Their already funded immutable
bytes can be shared; the selector hardlinks their owned storage. Different
paths, modes, objects or missing entries require independent capture/storage.
This preserves the unchanged capture envelope without another content ledger
or duplicate loader. Per-view metadata and actual new work still cost budget.

**Storage reuse is not metadata equality.** Linking/unlinking an unchanged
file can change its link count or ctime; distinct view directories have their
own inode/timestamps. Cached results remain selected-view-owned, even for
identical snapshot/ELF bytes. Every cache lookup and native Make mapping keeps
the core's complete operation-aware guest comparison, including returned
buffers, namespace-sensitive UID/GID/mount fields, flags, masks and failures.
Restoring CURRENT can therefore require genuine command execution, not reuse
of its old result. Unsupported metadata reproduction fails closed; validation
never repairs, masks or fabricates returned fields.

Nested contexts restore their immediate predecessor. Each selected tree,
cache and native file set is discarded on exit, not retained in a view
registry. Only active nesting state is kept for complete outer cleanup.
Default-view native handles survive a normal selection; suspended, foreign,
forged and expired selected-view handles cannot execute in another view.
Group related BASE queries in one context: re-entry performs new bounded
capture/materialization work. Large changed or uncertified views may still
exhaust the unchanged limits. Full-metadata validation can also exhaust the
control budget on repeated registry queries even when immutable source storage
fits comfortably; storage reuse is not a promise of unlimited result replay.

Invalid admission leaves a healthy owner intact. Setup, body, interruption
or teardown failures after admission are terminal and restore the prior
selection without reopening its budget. Scopes must exit in nesting order;
misnested exits close the report rather than restoring an invalid backing.
Closing the outer session clears suspended caches/handles too. A late context
exit cannot reactivate a closed session. Selection during active execution or
from another worker rejects.

The returned view context also checks its worker **before** public entry or
exit delegates to the context generator. Foreign normal exit, exception
delivery and misnested exit cannot resume/throw into that generator, clear a
cache, delete backing, terminate children or restore signal/session state.
The existing worker-violation policy marks the report budget failed; it does
not perform cleanup from the offending thread. The context remains available
for the original worker's normal or exceptional unwind, even with that failed
budget. Correct-owner misnested exit still closes the report as documented.
An active owner command subsequently encounters the failed budget on its
existing checks and performs its own cleanup; there is no cross-thread
scheduler or transfer of execution authority.

The [indexed human procedure](test-cases/workflow-governance.md#tc-workflow-probe-views-001-select-immutable-ownership-views-with-one-report-budget)
maps all deterministic checks, including real Git BASE/CURRENT registry
declarations and renamed source bytes. Its discoverable
`test_immutable_view_real_repository_query_pair` additionally resolves local
`HEAD` and `HEAD^1` once and captures both complete trees with the existing
gitlink/source declarations. One default budget/session runs CURRENT Make
and chapterbundle registry, selected BASE Make and registry, then restored
CURRENT Make: exactly two full-registry queries, not a third cached replay.
It checks actual certified byte/inode reuse, source-view ownership,
cumulative accounting, the original deadline and complete cleanup without
historical source/byte census constants or raised limits.
No manual-only criterion, feature flag,
game/profile conflict, ROM/RAM, save/config identity, locale, generated game
output, modern/archival or workflow/publisher change applies. Generated
publication/reconstruction and native Make registration belong to #225;
optional runtime inputs to #227; dependency-only compilation to #228.
Cross-extension scenarios and full CURRENT/BASE/112-domain/census/oracle/public
acceptance remain #180's integration responsibility. Rollback is an ordinary
revert of this selector layer, never a return to borrowed BASE semantics.

The existing **3,600-second maximum is one monotonic deadline**, including
snapshotting, compilation, all subprocesses and replay. Every subprocess gets
the remaining lifetime. Defaults bound 4,096 states/launches, 32 simultaneously
live traced guest processes, 16,384 total guest-process creations per report,
32 pending command resolutions, two million
syscalls, 32,768 snapshot entries and (by the default `None` alias) 32,768
report-lifetime observations. There are no futures or hidden worker
queues. Variant plans are checked before any variant launch.

Make validates each authenticated live request before resolving it. The single
worker obtains its actual result and publication before native execution
continues. There is no materialized whole-backlog queue or speculative
empty-output pass. Repeated real effectful dispatches still execute; only
compatible pure data and provenance deduplicate. Completed outputs and
request-specific slots retain all existing byte charges. A later malformed
request or completed transcript fails the whole report, not a partial success.

`ProbeSession.pending_commands` measures active resolutions, including nested
registration work; `pending_commands_peak` retains their actual maximum, not
the configured limit. Admission enforces `Limits.pending` before starting
another resolution, and the existing signal-safe cleanup restores the prior
count on success, failure or interruption. Live queued requests and parked
process/VM reservations are bounded before nested execution. No new
whole-backlog ceiling or higher limit is introduced. Make performs only its
own native include re-execs; the driver does not add replay passes.

`Limits.processes` bounds live capacity, including the capsule root, stopped
newborns and suspended vfork ancestors. `Limits.descendants` bounds cumulative
actual creation across every core capsule, command and replay.
The old extra 32-total-per-capsule restriction is explicitly replaced by the
live-capacity bound; none of the numerical maxima is increased.
A cold live producer needs a third slot beside Make and its parked helper.
With only two slots, callers can reuse a compatible pure result already
executed under the same report, but cannot start another guest. Earlier work
and repeated Make queries still count toward the descendant allowance.

The caller passes `process_limit` and the remaining `descendant_limit`
separately. Each admitted fork/vfork/clone/clone3 reserves capacity before kernel
creation alongside the existing memory reservation. Failed calls release the
reservation without inventing a created process. Newborn-first stops remain
held until their parent event authenticates them and are counted once, not
again when transferred to the normal process map. A pending reservation cannot
be spent by another tracee, and an excess child never gains execution authority.
PIDFD, memory-credit, vfork-completion and sole-reaper handling remain intact.

The closed supervisor report retains `processes` as total actual creation on
success or failure and adds `live_process_peak`, measured from tracked live
processes plus unresolved newborns. `ProbeSession.processes_used` accumulates
the totals; `ProbeSession.live_process_peak` retains their maximum live peak
across serialized capsules. Reservations/configured limits are not
reported as live processes. `memory_peak` remains virtual-memory-credit
evidence, not RSS. Failure and reuse cannot reset any report allowance.

Byte accounting is also aggregate: 768 MiB total, 384 MiB snapshot processing,
64 MiB streamed output, 64 MiB capsule writes, 32 MiB each cache/mappings/control,
16 MiB events, and 1 MiB pending-request traffic. Individual candidate output is
streamed with a 1 MiB cap; bounded files/observations are at most 16 MiB.
Capsules have a 512 MiB **aggregate virtual-address-space** ceiling, 16 MiB
maximum stack limits, 128 descriptors,
no core dumps and a 4,096-creation aggregate cap. Limits may be lowered, not
raised. Filesystem observations and serialized semantic results consume the
same bounded control budget. Parallel calls to one session reject; a violation
makes the entire session unusable.
Static Make queries with neither registrations nor inherited publications keep
their authenticated live handshake but explicitly deny publication. Their
private configuration uses `reserved_paths: null` rather than repeatedly
serializing an unused full-tree reservation list. Registered/inherited scopes
keep all reservations. This does not remove any source capture, metadata
buffer, observation charge or registry execution from the full CURRENT/BASE
consumer pair.

The existing pending-byte category is cumulative **lifetime traffic**, not a
live outstanding-memory gauge: uncached command declarations, variant inputs,
binary stdin and subprocess launcher arguments spend it without refunds.
Serial resolution does not reset that byte counter or reclassify completed
traffic. A later pending-byte exhaustion is a distinct measured limit, not
permission to increase it or silently turn it into a reusable credit pool.
The independent whole-record and lifetime planned-state admission limits
described above remain 1 MiB each even in a test-only larger-traffic profile;
that profile is not a production option.

Virtual memory uses one funded credit pool, not independent per-process
512 MiB limits or a sampled/RSS threshold. The supervisor assigns kernel
`RLIMIT_AS` soft bounds whose sum, including pending fork copies, never exceeds
the pool. It reserves stack headroom before resuming execution; if the pool
cannot fund all potential stack growth, the tighter address-space bound limits
it continuously on page faults. Multiple processes cannot each spend the same
unreserved stack allowance.

Only a stopped owned address space can reclaim or request credits for
`mmap`, `brk`, `mremap`, fork or exec. Its virtual-page count informs admission;
other running spaces retain their already funded bounds. Shared-VM vfork
members have equal bounds and each counts in the aggregate. Exec transitions
reserve the new image/initial-stack exposure, and a vfork-completion stop holds
the released parent until the child's new address-space identity is accounted.
Growing-stack splits, protection changes and automatic faults remain constrained
by the same kernel-enforced bounds. `memory_peak` is the accepted virtual-credit
watermark, including transition/headroom reservations, not physical memory or
an assertion about the exact sampled live footprint.

The creation quota reserves attempts **before kernel dispatch**, including
`open`/`openat` with `O_CREAT` or `O_TMPFILE`, `creat`, `mkdir`/`mkdirat`, and
`link`/`linkat` (including `AT_EMPTY_PATH`). Closing/unlinking an object or
starting another command does not refund/reset the aggregate quota. Failed
creation attempts are conservatively charged too. Symlinks, relocation,
special-file creation and unadmitted open variants are denied, not uncounted
alternatives.

Context exit, timeout, overflow, worker failure, malformed output and
SIGINT/SIGTERM kill/reap the recorded process groups and traced descendants,
clear caches, close channels and remove only the owned scratch tree. Scratch
components and input leaves reject symlinks/FIFOs. No cleanup uses process
names, other worktrees, global caches or system temporary directories.
Owned-tree removal is iterative and descriptor-relative, with no Python
recursion-depth dependency or new tree-depth limit. Nofollow component opens
and directory identity checks prevent symlink traversal or removal of a
replaced unrelated entry. Parent identities are checked when climbing back
through an opened directory, so descriptor use stays bounded independently of
tree depth. Cleanup does not change permissions to force traversal; errors
remain visible, and the existing signal-safe lifetime closes every held FD.
Scratch allocation retains directory FDs and local ownership of every new
parent until the session takes over. A tracked component, inaccessible leaf,
open/mkdir failure or interruption cleans partial allocation too, without
removing pre-existing directories or traversing an unsafe ancestor. Setup
signals are delivered only after ownership is assigned. If the operating
system also rejects cleanup, the primary setup failure remains the cause and
cleanup diagnostics are attached rather than replacing it.
The temporary setup mask is restored in a spawned child before its exec;
payloads see the caller's intended mask, not the ownership-acquisition barrier.

Terminating signals are blocked through the complete owned teardown: per-call
reports/configs, command/Make directories, process/pipe cleanup, session state
and scratch removal, and supervisor/watchdog finalization. All cleanup actions
are attempted before prior handlers and the exact prior mask are restored.
Signals that the caller already blocked remain pending; other queued terminating
signals are delivered afterward rather than ignored. A raising Python handler
or cleanup exception is recorded without replacing an existing operation
failure. With no primary failure, the deferred exception propagates; default
OS termination actions take effect only after owned resources are removed.
Failed/interrupted cleanup never resets or extends the aggregate lifetime.

Every budget subprocess, including ordinary Git/compiler commands, namespace
availability probes and capsules, uses a fresh exclusive-reaper watchdog.
Its trusted executable must be root-owned, non-writable by other users, and
free of set-ID bits/file capabilities; candidate native tools remain capsule-only.
The outer budget may reap that watchdog but never uses its numeric PID to
signal a process or group. The watchdog keeps its own leader waitable with
`WNOWAIT` until group signaling is complete, then reaps it. Adopted descendants,
including ones that left the original group, are signaled through pidfds and
reaped before return. The syscall supervisor likewise retains pidfds across
bootstrap/exit failures; stale numeric tracee records cannot signal a new
process. Already-reaped handles are never group-signal authority.

The watchdog registers that owned leader's pidfd alongside the caller lifetime
pipe. Child exit wakes the real selector; it no longer waits for a periodic
completion tick (#264). The selector waits at most the remaining time on the
same absolute deadline. Readiness only wakes the existing `WNOWAIT` check:
it neither reaps the leader early nor replaces group or orphan cleanup.
An already-exited child keeps its normal, nonzero or signal exit status.
The existing signal-deferring guard covers selector/child/pidfd acquisition
and registration. A setup failure stays primary even if unmasking delivers a
queued interruption. Sole-reaper cleanup, pidfd close and selector close are
all attempted before restoring caller handlers; a cleanup error or deferred
signal cannot replace an existing failure. The descriptor is not inherited by
the payload. Unsupported pidfd APIs/kernel support still reject explicitly;
there is no polling or numeric-PID fallback and no new host requirement.

The outer caller owns the sole write
end of a lifetime pipe; its closure (including process death), the original
aggregate deadline, or a watchdog termination signal triggers privileged
cleanup where needed. Bounded raw command input uses a separate inherited pipe,
not the lifetime channel; payloads inherit only their standard descriptors.
The command starts a separate session and never inherits the lifetime pipe.
It uses a kernel subreaper and parent-death signal, followed by unshare's
`--kill-child` and the private PID namespace, so watchdog death also tears down
the namespace. Set-ID, file-capability, non-root-owned or writable namespace
executables fail closed before sudo: an exec privilege transition could clear
the parent-death signal. The unprivileged caller closes the pipe and waits for that
cleanup; it never attempts to kill a root-owned group or ignores a
`PermissionError`.

The process suite tests this lifecycle with the real watchdog and same-UID
owned payloads, substituting only the privileged namespace launcher. These
fixtures intentionally do not invoke `unshare`: they must also pass where
unprivileged user namespaces are unavailable. The production capsule tests
exercise the selected namespace route separately. Budget/interruption controls
require an owned payload-start marker and the intended exception, so a failed
launcher cannot masquerade as deadline or output evidence. These controls do not
claim a real sudo credential-transition positive. That route requires separate
exact-candidate evidence on a host where the documented noninteractive sudo
permission is available; never use a shared development host's credentials or
change its namespace policy to manufacture the result.
The focused #264 controls use real immediate and pipe-gated children, inspect
the kernel pidfd identity/readiness and retained wait status, and supply a
controlled clock only to check the selector's absolute-deadline argument.
They remove just child readiness registration as a negative control and inject
acquisition/registration failures, real setup signals and teardown errors
after actual resource release. Each path checks owned children, descriptor
sets and caller signal state. Twenty-short-command before/after measurements
are supporting performance evidence, not a timing assertion or a claim that
the complete #180 graph or CI fits its budget. See the existing
[focused tester procedure](test-cases/workflow-governance.md#focused-watchdog-readiness-regression-264).
This is an independent foundation correction consumed by #180, not graph or
#196 extraction work. Other feature/profile conflicts are none; modern and
archival builds, gameplay, save/config identities, generated game data,
locales, ROM/RAM and every resource limit remain unchanged.
The producer suite has a separate real privileged-route control, conditional
on that existing permission and namespace support. It observes root supervisor
socket credentials, the non-root guest identities after the real drop, actual
static/remade Make results and cumulative capsule reports. Its optional
same-UID comparison remains distinct and skips as one whole comparison when
user namespaces are unavailable. Actual foreign credentials and same-UID
foreign ancestry have separate paired kernel-socket controls; no peer check is
weakened to accommodate a different launch route.
Watchdog status 125 or an unexpected preflight failure is a failed control, not
an optional permission skip.

## Contract allocation and downstream integration

This is the complete single-view static execution/source/registry/resource/
cleanup foundation, not the #180 graph or a reduced replacement for its
acceptance. Its real immutable and HEAD-admitted live consumer must work.
The exact historical selector/API/documentation allocation is preserved in the
[delivery evidence](https://github.com/laqieer/fireemblem8-expansion/blob/56e0a206ffae088b0dbc1fe8aa6339a8ee820f33/docs/ownership-probe-allocation.json),
not maintained as an operational ledger in the current source tree.

The integrated reference
`d9bc40da63b843934b340734eb1fe0e1bc61a6d3` is explicitly **unapproved**. Its
complete implementation, test selectors and procedures remain preserved in
Git and in the designated reference worktree. The forward core extraction is
not a claim that those optional contracts are independently implemented,
reviewed or delivered:

| Issue | Complete allocated contract | Immediate dependency |
| --- | --- | --- |
| #225 | Native Make registration, declared generated results/publication, authentic remakes and generated-context metadata/reuse/provenance | #206 |
| #226 | Same-budget immutable CURRENT/BASE selection and storage reuse | #206 |
| #227 | Optional runtime-file inputs and stock alias/env metadata behavior | #206 |
| #228 | Dependency-only compiler producer with actual output/header provenance | #225 |

Shared safety remains in the lowest exposing layer. Core keeps `compile_native`
and `native` isolation, mandatory runtime closure, exact source/gitlink input
support and the original typed stdout-only command surface. The P extension
adds `Command.native_tool`, `Command.outputs`, `ProcessOutput.generated` and
native output capture. The merged #226 layer provides `ProbeSession.select_view`
and `Snapshot(reuse=...)`. The merged #227 layer provides `runtime_files` and
its captured records/metadata-only stock dispatch. The separate D extension
adds `Command.dependency_only`
and actual driver/cc1 execution receipts through P's producer contract, without
requiring the view selector or optional runtime inputs.

P/#225's implementation is merged through PR #232 and is included in this
master-based D root. It uses live rendezvous and scoped nested publication
instead of the unapproved delete/recreate model. P's final delivery evidence
is tracked in #225; code inclusion here does not substitute for those gates.
Prior generated-listing successes do not replace the retained nlink/timestamp
and reconstruction counterexamples.
All old positive/adversarial cases stay with that complete contract. No
restoration of unsafe code, mechanical code/test/doc split, artificial V/R
dependency on D, or new execution platform is implied.

#180 must integrate the relevant completed leaves with the core and retain its
entire CURRENT/BASE/domain census, graph, oracle, lifecycle and public
`validation-ownership-check` acceptance. The foundation consumer is not full
root or 112-domain evidence. Calibrating budgets remains a separately
authorized task after reviewed complete API/caller/input and workload evidence;
no elevated diagnostic or production cap is used here.

The review5133381960/head8169 architecture hold is not discharged merely by
this source split or a commit SHA. Main owns explicit disposition after actual
coherent core, complete allocation and independent delivery evidence.
Every issue keeps its own candidate/review/master/closure gates.

Host dependencies remain the existing generated-registry schema and tools.
Other game/profile conflicts are **none**. Modern debug/release, archival,
save/config identity, localization content, generated game output and ROM/RAM
are unchanged. No feature flag or Build topology/context is added.
Rollback uses an ordinary revert; broader validation remains mandatory.

Tester procedure:
[`TC-WORKFLOW-OWNERSHIP-PROBE-SANDBOX-001`](test-cases/workflow-governance.md#tc-workflow-ownership-probe-sandbox-001-confine-and-bound-authentic-probe-execution).
