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

## Immutable native foundation and dependent writable evaluator

Issue [#274](https://github.com/laqieer/fireemblem8-expansion/issues/274)
owns the closed immutable-input native foundation below, including the
internal `_native_make_readonly` and sealed compiler/tool seams. It is a
separately deliverable component of
[#270](https://github.com/laqieer/fireemblem8-expansion/issues/270), not
qualification of that umbrella's original generated eight-query contract.
The complete writable producer **and** generated-read-version transaction
stays together in dependent
[#275](https://github.com/laqieer/fireemblem8-expansion/issues/275).

The readonly foundation never grants native output writes, `.dep` shell
redirection ownership, temporary/lock lifecycle, atomic retirement or
generated-source custody. Generated include remakes/reexec requiring those
writes remain refused. Existing mapped APIs are unchanged; neither this root
nor a successful prefix authorizes validation narrowing or graph delivery.
The dependent extension must preserve original commands, inputs, all four
profiles times both Make primaries, one unchanged budget/deadline and every
actual source/job/version outcome.

The dependent #275 branch's internal `NativeOutputs` object model keeps
successful output-descriptor operations and generated source-read pins in
one lifetime. Its focused kernel controls exercise actual file writes,
fork-inherited descriptors, paired write entry/return, actual `dup2` no-op
and replacement, failed writes/renames, atomic replacement with
an old source pin, foreign owners, in-place mutation refusal and pin cleanup:
`python3 -m unittest scripts.validation_ownership.tests.test_producer.NativeOutputCustodyTests`.
Settled versions retain generated-content digests through owned object pins;
rename/unlink verify both moved and retired bytes. Plain write returns cannot
absorb mode/link changes. Cleanup closes owned object/source pins, never the
borrowed tracee descriptor integers.
Zero-byte write returns preserve the complete entry identity and cannot absorb
an unrelated intervening content write.
The redesign's internal entry/return surface owns one pending operation per
process. It retains entry object pins and open flags before actual creation or
truncation, refuses destructive opens before an active source pin is changed,
and records paired failed opens, renames and removals without invented objects
for absent operands. Write entry retains the actual payload, offset and bounded
preimage; return compares the exact resulting bytes, including unchanged
regions and sparse gaps, rather than attributing every content change to a
nonzero return. Failed duplication creates no negative descriptor binding.
Duplication entry retains the actual `dup`, `dup2`, `dup3`, `F_DUPFD` or
`F_DUPFD_CLOEXEC` kind, requested target/minimum and exact source/target object
pins. A successful return must identify the actual source object and requested
target or minimum; allocating duplication cannot overwrite an existing binding.
`dup2` same-FD success and `dup3` same-FD failure remain distinct. Retired but
still-readable objects can be duplicated without inventing a live pathname.
Copied, stale and overlapping operation tokens refuse; terminal cleanup closes
pending owned pins as well as the shared object/source pins.
Owned teardown is terminal, not a resumable cancellation. It preserves any
unfinished borrowed descriptor/writer as failed diagnostic state and explicitly
refuses subsequent transitions; inventory completion cannot turn that state
into successful observation. On the supported Linux host, late `close` errors
`EINTR`, `EIO`, `ENOSPC` and `EDQUOT` retire the valid borrowed descriptor while
retaining the failure event; `EBADF` contradicts a live owned binding.
The late-error controls use actual descriptor close/release with a modeled
error return. They do not claim a real filesystem-induced I/O error.
Final-writer descriptor retirement does not itself complete content settlement.
A content-budget/stat/read or settlement-event failure leaves the object
unsettled even though the actual descriptor has been released. Final inventory
completion rejects that state before and after owned teardown. Digest admission
occurs only after the settlement event succeeds; a previously settled digest
cannot authorize a failed later settlement.
The existing model keeps one sticky incomplete-lifecycle state, not a second
receipt or output registry. Every event-publication exception propagates and
prevents later completion, including after successful settlement, final close
or physical source-pin release. Cleanup of an unreturned operation or unreleased
source still closes its owned resources but retains incomplete lifecycle; it
cannot manufacture a successful source return or operation outcome.
Fork observations preflight the entire tracked descriptor set, including later
collisions and repeated inputs, and fund all copies before installing any child
binding. A validation or charge failure emits no inherited prefix and changes
neither parent's nor child's writer/binding set. A later event failure remains
sticky-incomplete rather than pretending the actual kernel fork rolled back.
Pure-status `close`, rename and unlink returns require integer zero for success
or a bounded negative kernel error: positive, Boolean, float and null evidence
cannot retire a descriptor or transfer a version.
Open descriptor/status and write byte-count/status returns likewise validate
exact integer type and kernel bounds before comparison or mutation. Malformed
decoded values raise the custody-layer error and retain the pending entry or
writer; they cannot escape as a Python type error or clear incomplete evidence.
Pre-syscall write-entry preparation failures clear their provisional writer
and operation token and close only new owned operand pins. Actual borrowed
output descriptors, settled identity/digest and version remain unchanged.
This rollback applies before the kernel write, not to an actual failed write
return or incomplete post-write settlement.
The fixed-offset model does not admit append writes. Paired open entry rejects
`O_APPEND`; write entry also checks the actual pinned open-file-description
flags before marking a writer, covering post-open `F_SETFL` and duplicate FDs.
Linux `pwrite` on an append description ignores the supplied offset; it cannot
be qualified by this fixed-offset postimage model. Original finite domains
do not require append redirection. This limitation does not grant production
output authority or generated-source admission.

The existing native production observer now captures original `posix_spawn`
argv and canonical `/repo` CWD before spawning. Its private `VO_JOB_INPUTS`
notification is authenticated at the existing observer instruction/role
boundary, carried through the actual `Process` clone, and compared with the
root exec syscall inputs before execution. Runtime v5 also compares the
exec-stop command line against that pre-spawn value. Each argv pointer and
string read is charged to the original shared observation budget. Root
dispatch completion and successful root exec retire the pending value;
descendant execs retain the existing actual-tree validation.

This is an original-input binding seam, not writable-output permission,
Command-plan admission, nested-Make support, or generated-source v6 evidence.
The v5 machine/host/archive input schema remains unchanged. To reproduce the
deterministic seam controls from a clean source tree, run
`python3 -m unittest scripts.validation_ownership.tests.test_native_writer.NativeWriterTests.test_native_original_prespawn_inputs_bind_entry_and_exec_stop`.
The original recipe must print `original`; omitted, repeated, foreign-sender,
wrong-size, changed-argv/CWD and changed exec-stop bindings must refuse and
clean the owned process tree. Existing original expansion, direct-executable
and descendant pipeline cases are the positive compatibility controls.

The readonly production lane can now bind original job inputs to the existing
typed `Command` schema by passing `commands` to `_native_make_readonly`.
Its existing mapping is indexed by the **actual argv tuple**, not reconstructed
shell text. Each lookup must return a `Command` whose argv is exactly that
tuple. The host resolves the binding through the existing `ProducerChannel`
before the original spawn, without executing the command itself. The same
monotonic dispatch is bound to the actual clone, exec entry, runtime v5
exec-stop inputs and complete returned job. Repeated equal argv means repeated
real jobs, not deduplicated/replayed results. The final channel report must
cover every issued authorization and completed actual job.
The owner payload binds one frozen identity of the complete captured runtime:
Make, shell, descendant executables, interpreters/shared libraries, optional
captured resources and their alias/absence/mode declarations, plus the admitted
executable set. It also binds the active native tool's digest, source inputs
and original placement, including when that tool is used by a descendant.
Hashing and the complete encoded payload use the existing shared budget.
Different sealed tools or captured code images cannot alias merely because
the root argv and executable agree; repeating the same closure retains a
stable owner. Managed Python runtime directory roots are named trusted readonly
mounts, not sealed byte captures; this binding does not claim otherwise.
Directory declarations use the existing `_directories` authority, including
the repository root `"."`, and their normalized set participates in the owner.
The selected executable digest follows the same captured stock `/bin` alias
used by the actual supervisor; canonical `/usr/bin/sh` jobs retain their
original argv rather than being rewritten.

This intermediate path requires runtime v5, canonical `/repo`, immutable
source/code/directory declarations, the active issued native tool if one is
named, and no outputs or dependency-only execution. It grants neither a
writable mount nor generated-source acceptance. Existing source selection,
machine/archive format, shared counters/deadline and normal mapped producer
behavior are unchanged. Full #275 integration still requires original finite
output/effect plans, actual writers/read pins, generated wire crosschecks,
nested Make and the unchanged eight-query qualification.

Run the `NativeWriterTests.test_native_command_admission_*` cases in
`scripts.validation_ownership.tests.test_native_writer`. The positive case must
return the two actual expansion-root PIDs from the original Make variables
and direct recipe stdout, exactly three actual jobs and three authorizations.
Missing/substituted/writable Commands, changed views, expired deadline,
exhausted cache, cancellation, changed live/returned bindings, stale replies
and incomplete terminal counts must refuse and fully clean owned processes.
These controls are deterministic host evidence, not original-eight proof.
The output-object component distinguishes the acting producer of a readonly
open or atomic replacement from the producer of a retained prior version.
Reading or retiring that prior object does not relabel its owner; replacement
keeps the new temporary's producer and retains the prior source pin's original
owner/content. This is observation, not cross-producer output authorization:
the production supervisor must still admit the exact destination effect from
its issued Command before allowing any kernel mutation.
`NativeOutputCustodyTests.test_distinct_producers_read_and_replace_prior_versions_without_relabeling_sources`
uses actual readonly opens, `renameat2(RENAME_NOREPLACE)` failure and atomic
replacement to verify both versions, distinct acting/version owners and old
pin retirement. Producer/process labels are component inputs, not independently
observed native jobs. Foreign temporary owners and writable reopenings refuse;
unsupported rename flags and a success return contradicting NOREPLACE with an
existing destination refuse. The pre-fix component cannot read or retire a
prior producer's version. Actual supervised multi-job/source/wire qualification
remains required before #275 acceptance.
`test_native_command_admission_preserves_root_directories_and_stock_shell_alias`
must execute the original job with and without the captured `/bin` alias,
preserve its argv/stdout and complete one actual authorization, and bind equal
normalized directory sets identically. Absent, file-valued and escaping
directory declarations must refuse. The pre-fix controls reject valid root
directories and fail aliased shell admission with a missing executable digest.
`test_native_command_owner_binds_descendant_tools_and_shared_library_closure`
must execute differing real shell-launched tools with distinct owners and keep
owners distinct when a captured non-root shared-library image changes without
changing stdout. Repeating the original closure must restore its original
owner. The root-only pre-fix binding aliases both controls.

The existing session-issued native-tool `Command` capsule now connects its
regular-file effects to the live syscall supervisor's output-object model.
Open, write/writev/pwrite, duplicate, close, actual fork inheritance, process
exit, atomic replacement and unlink use stopped kernel operands and paired
return observations. Owned `/proc/PID/fd` duplicates pin the actual objects;
tracee descriptor numbers are never closed by the supervisor. Successful
returns settle content and versions; failures preserve preimages. Counters,
pin/content reads, event storage and the deadline use the existing shared
budget. No second runner or output ownership registry is introduced.

The same model now observes capsule `mkdir`/`mkdirat` and
`rmdir`/`unlinkat(AT_REMOVEDIR)` entry/return pairs. Successful creation pins
the actual empty, owner-traversable directory and its inode/mode; removal
requires that previously observed owner and records the same inode with zero
links after the kernel return. A missing removal may fail without inventing
a directory, and an existing foreign directory cannot be adopted for deletion.
Creating or removing children and regular-file replacement update an owned
parent only after verifying the exact namespace entry change and stable
parent inode/mode. Validate every affected parent before publishing any
directory/file ownership change or parent update, including both sides of a
cross-parent replacement. Entry-time identity/entry checks reject unobserved parent
changes. Failed operations retain the parent preimage. Directory pin reads,
entry inventories and events use the existing shared observation budget.
Terminal cleanup closes pending pins without publishing an unobserved return.
The existing pending-operation map also excludes overlapping namespace
operands, directory ancestors and owned-parent entry inventories across PIDs
before a second kernel operation. This prevents two admitted absent,
nonexclusive creating opens from publishing the wrong producer's inode.
Do not change the original syscall flags to force exclusive creation.
Independent names without a shared owned-parent inventory remain concurrent;
readonly opens may concurrently share a settled version without claiming
namespace mutation. Conflicting namespace operations may proceed after the
first actual return.
This is admitted tracee-operation custody, not hostile same-UID host isolation.

Run `NativeOutputCustodyTests.test_pending_namespace_excludes_raced_creation_and_all_conflicting_action_siblings`
and `NativeOutputCustodyTests.test_pending_owned_parent_inventory_excludes_sibling_namespace_mutations`.
For all five namespace actions, source/destination variants, ancestor and
same-owner/different-owner modeled bindings, require refusal before mutation
with no new pins, pending records, object ownership or events. Preserve the
first pending operation; independently create a different name, then complete
the first actual nonexclusive kernel open and permit sequential continuation.
Require exact owned-parent entries after sequential child creation.
The pre-fix model admits both absent entries and can attribute the actual
second creator's inode to the first producer processed at return. These are
actual filesystem/component controls with modeled producer/PID labels, not
qualification of distinct native Make jobs.

From a clean fixture run
`python3 -m unittest scripts.validation_ownership.tests.test_producer.ProducerTests.test_native_capsule_observes_directory_syscall_and_failed_return_family scripts.validation_ownership.tests.test_producer.NativeOutputCustodyTests`.
The actual C program must exercise both ordinary and dirfd-relative syscall
families, each producing exactly `result` bytes `final`, mode `0600`, and
stdout `once`. Require paired creation/removal identities and actual
`EEXIST`, `ENOTEMPTY` and `ENOENT` failure results without invented versions.
Creating a directory over an admitted regular file must reach actual `EEXIST`;
removing that regular file as a directory must reach `ENOTDIR`. Preserve its
typed file preimage without adopting it as a directory or refusing before
those harmless failed kernel operations.
Component controls additionally exercise nested directory/file creation,
cross-parent replacement, child retirement, foreign-owner/foreign-existing
directory rejection, unobserved parent changes and extra children introduced
between entry and return. A refused mkdir must publish no new directory
ownership; a cross-parent replacement with a bad second parent must publish
neither the first-parent update nor file transfer. Terminal cleanup after
either refusal must not publish a successful return or refresh retained
parent identities.
The pre-extension model did not observe directory syscall lifecycles and
has no paired directory API. These controls do not establish original native
Make admission, directory plans or generated-source v6 qualification.

Close returns use one supervisor path to retire output custody, source-read
bindings and generic descriptor authority together. Linux releases a tracked
descriptor on `EINTR`, `EIO`, `ENOSPC` and `EDQUOT`; these accepted late errors
must not leave a pathname authorization or be retried. `EBADF` contradicts
a live output binding and unsupported errors still refuse. Normalize the
close operand and `unlinkat` flags to the kernel's signed 32-bit argument
before selecting custody. Only normalized `0` and `AT_REMOVEDIR` unlink
operations are admitted; unsupported flags refuse before kernel mutation.

Run the two `ProducerTests` methods
`test_native_capsule_injected_late_close_retires_both_fd_maps_for_actual_binding_family`
and `test_native_capsule_unlinkat_normalizes_kernel_flags_and_refuses_unknown_operations`
from a clean fixture with `python3 -m unittest`. The close control executes
real successful kernel closes, verifies absence of each tracee `/proc/PID/fd`
entry, then injects each of the four late error returns at the stopped syscall
return. It is injected-return integration evidence, not a claim that the
filesystem naturally produced those errors. Exercise original, duplicate and
actual fork-inherited bindings with ordinary and upper-bit descriptor operands;
require both descriptor maps retired, exact final bytes/mode/stdout, four
distinct injected outcomes, and successful uninjected parent/original closes.
The pre-fix supervisor retains generic descriptor authority after the injected
error. Exercise both unlink forms with ordinary, bit-32 and bit-63 flags;
require actual zero-link retirement identities for the file and directory.
Unknown low flags must refuse, including when upper bits are present.
The pre-fix full-register comparison skips custody for the upper-bit operands.
Require complete session cleanup after every positive and refused invocation.
These capsule controls do not qualify original native Make or generated v6.

Regular output descriptor bindings retain actual observed open-file-description
lineage: each successful open is distinct, whereas duplication and actual
fork inheritance share a description. Paired `flock` observes its exact
descriptor, operation, file identity/content and kernel `/proc/PID/fdinfo`
lock mode. Shared/exclusive acquisition, nonblocking contention and explicit
unlock are supported. A failed Linux shared-to-exclusive conversion releases
the prior shared lock; report the observed unlocked mode rather than inventing
unchanged lock ownership. Closing one alias does not release its description's
lock; last close, dup2 replacement and process exit retire the appropriate
binding. Unsupported flags, unobserved mode/content changes and overlapping
description operations refuse. Terminal cleanup releases model state and
owned pins without closing borrowed tracee descriptor numbers.

Run `ProducerTests.test_native_capsule_flock_tracks_distinct_opens_duplicates_and_actual_fork_descriptions`
and `NativeOutputCustodyTests` from a clean fixture with `python3 -m unittest`.
The actual C invocation must acquire shared locks through two independent
opens of one inode, fail a nonblocking conversion with real `EWOULDBLOCK`,
release the shared lock as Linux does, and acquire exclusive after explicit
unlock. A real fork child must unlock the inherited description, permitting
the parent's independent open to acquire it. One alias close must retain the
lock; dup2 replacement of the last alias and final close release the two
distinct description locks. Require eleven parsed paired lock returns,
distinct description identities for separate opens, shared identity through
dup/fork, two actual contention failures, final bytes `final`, mode `0600`,
stdout `once`, and complete descendant/session cleanup.
Repeat with bit-32 and bit-63 additions to the actual syscall descriptor and
flag operands; kernel-width normalization must preserve all eleven mode/
flag/result observations and exact generated output.
Component controls
reject invalid flags, copied returns, overlapping aliases, false mode reports,
content/mode mutation and unobserved terminal lifecycles; an injected late
close after an actual kernel close retires the last locked description.
The pre-extension capsule executes flock but emits no paired lock evidence.
This intermediate capsule lineage does not yet authorize a shared lock across
distinct native Make owners or reconcile a successful post-bootstrap exec.
Those production resource roles and generated-source wire contracts remain
required before #275 acceptance.

Tracked output `fcntl(F_SETLK/F_SETLKW)` refuses before kernel mutation:
POSIX record locks have different process/close semantics and are outside the
original manifest's flock contract. Run
`ProducerTests.test_native_capsule_posix_lock_mutations_refuse_original_duplicate_and_inherited_bindings`
for both commands through original, duplicated and actual inherited FDs.
The old capsule silently allowed all six mutations without lock custody.
`F_GETLK` remains a query, not a granted mutation.

Paired descriptor `fchmod` requires an owned writable binding, no pinned
source readers and no unfinished mode/write/lock/object operation. Verify
unchanged inode, complete bytes, size, mtime and link count before updating
only the mode/ctime identity; preserve the same content revision/expected
writer image. Failed returns preserve the complete preimage. Other mode
and standalone truncate syscalls remain explicitly unsupported.
Close checks exclusion at the actual syscall entry, not just after the kernel
has released the descriptor. Its paired token remains pending until the
existing shared close-return handler validates and retires it; a pending close
also excludes mode entry on another alias of the same object.
Run `ProducerTests.test_native_capsule_pending_mode_refuses_actual_alias_close_before_kernel_release`
to park actual child mode and parent alias-close syscall entries with a
test-only supervisor. The rejected close must leave the actual `/proc` FD
inode and both descriptor maps live. This controlled interleaving is not a
claim about natural scheduler timing.
`NativeOutputCustodyTests.test_paired_close_excludes_mode_entry_until_the_actual_kernel_return`
checks the reciprocal pending-close exclusion, copied-token refusal and mode
continuation after an actual successful kernel close. The late-close family
still uses the same return handler, including actual kernel release followed
by injected Linux late errors.
Close and flock exclude each other at entry on the same open-file description,
including inherited aliases. A pending close retains that description in its
existing operation token; independent opens of the same inode remain distinct
and may acquire their own compatible locks.
Run `ProducerTests.test_native_capsule_pending_close_refuses_actual_inherited_flock_before_mutation`
to park actual parent close and inherited-child flock entries. On refusal,
both real `/proc` aliases and both descriptor maps must remain live, and
bounded kernel fdinfo must still show no lock. The reciprocal and independent
open controls are in
`NativeOutputCustodyTests.test_paired_close_excludes_shared_description_lock_but_not_independent_opens`.
Run `ProducerTests.test_native_capsule_descriptor_mode_tracks_actual_alias_fork_and_kernel_width_operands`
and `NativeOutputCustodyTests`: actual dup/fork aliases change one inode
through mode0644 to0755, preserving bytes `final`, stdout `once`, content
revision and cleanup. Repeat with upper descriptor/flag bits and kernel
16-bit mode truncation. Component mode failure is injected `EPERM` with an
unchanged inode, not a naturally induced filesystem failure. Copied returns,
unrelated bytes/mtime/link changes, wrong mode and all pending-object sibling
operations refuse without publishing identity or events. Existing readonly
renamed/replaced/unlinked original/dup/fork mode attempts remain forbidden.
The old actual capsule rejects fchmod entirely; these controls do not prove
original native Make's produced-executable admission or generated v6.

Paired writes retain one charged expected byte image for each writer lifetime,
initialized from its actual pinned object. Each return checks exact size,
identity/mode and the requested range, including unwritten suffixes after
partial returns. Failed and zero-byte writes retain their preimages; sparse
growth contributes zero-filled holes. At last-writer retirement, verify the
complete actual content against the expected image before settlement makes
that version eligible for immutable source capture or publication. Corruption
outside a successful write's requested range can be detected at this settlement
boundary rather than at each write return. The model never adopts those
unobserved bytes as expected content. Settlement or terminal cleanup releases
the retained image. This avoids whole-file observation at every incremental
write without adding block hashes, shadow files or larger budgets.

Run
`python3 -m unittest scripts.validation_ownership.tests.test_producer.ProducerTests.test_native_capsule_incremental_writes_keep_linear_shared_observation_cost`.
Require one actual C invocation writing 256 consecutive 4 KiB blocks, exact
1 MiB output of `X` bytes, mode `0600`, no new command-cache entry and complete
process/resource cleanup. Measure the existing shared control-byte counter
around native execution, without resetting it or increasing limits; require
less than sixteen times the generated byte count plus 1 MiB fixed overhead.
The previous whole-preimage/postimage implementation exhausts the unchanged
shared metadata budget before completing this invocation.
Component controls require touched corruption and false partial-return counts
to refuse immediately, and untouched corruption to refuse at settlement before
source capture. These remain host custody controls, not original-eight proof.
The payload-less `before_write`/`written` component facade cannot resume after
a paired write has initialized the expected image in the same writer lifetime.
Refuse that interface transition before kernel mutation; never clear the image
and adopt current bytes to make the transition appear valid. Pure facade
writes and facade-to-paired transitions remain available. The supervisor uses
paired writes exclusively, so this is not a restriction on capsule syscalls.
`NativeOutputCustodyTests.test_writer_interface_transitions_preserve_expected_image_and_refuse_before_mutation`
covers original, duplicated and modeled-inherited bindings: attempted facade
mutation must refuse with bytes, image, identity and events unchanged, followed
by successful paired continuation and exact settled source capture. Repeated
facade writes before paired entry remain a positive compatibility control.
The inherited component labels do not establish actual child-job authority.

The `/native/tool` command capsule adapter retains final exact output capture
and reports charged, sequenced output events through the existing supervisor
observation transport; those events are not yet generated-source machine/
host/archive qualification. Existing `Policy.check` capsule authority remains
the permission boundary for intermediate files; `Command.outputs` names final
publications, not temporary effects. Observe actual scratch-file creation,
unlink and regular-file replacement without adding another temporary allowlist.
Final capture still requires every declared output to exist and rejects
undeclared leftovers. Original native Make still needs its source-derived
finite effect domains; the capsule's existing authority is not such a domain.
Append, anonymous `O_TMPFILE` opens (including the work root and nested
directories), standalone truncation, other mode/ownership changes, hardlinks, and
replacement of a tracked output FD by an untracked FD refuse rather than
inventing an observed transition.
These limitations still need reconciliation with the original producer effect
plans before #275 acceptance; they do not establish a new supported restriction
on the original Make backend.

The first original native Make writer now uses the same admission channel and
object model through `_native_make_writable(..., outputs=..., commands=...)`.
This is an incomplete internal integration seam, not #275 qualification.
Each resolver returns the actual argv's `Command` with exact output paths;
the complete sealed code/input/runtime closure and output list bind the owner.
The actual pre-spawn dispatch carries that closed output list, and fork
descendants retain the actual dispatch. A union of declared paths selects
private writable ancestors with existing maximal immutable source islands;
it does not grant every job the union. File opens/writes require the job's
exact output; this first wire family does not yet admit directory or other
namespace/mode mutations, even under a writable ancestor.
Tracked source collisions and another job's output refuse before mutation.

This lane uses explicit wire version 6. Immutable version 5 still rejects
output authority and retains its original closed schema/read-only backing.
Version 6 records closed output plans, original job/input/PID/tree bindings
and paired output effects in the existing machine observations. A single
machine stream supplies both saved replay and final settlement; there is no
second `output_authority.effects` payload or collector buffer. Standalone
validation checks machine/job/effect agreement, descriptor description
lifetimes, content revisions and terminal retirement/settlement. Exec closure
also binds the already observed successful job image generation. Host artifact
capture additionally agrees with the actual settled inode and content digest;
it is not an intermediate generated-source observation.
The initial format only admits the implemented regular-file open/write/
duplicate/replacement/close/exec-closure/settlement family and the first
settled generated include reader. Shared lock roles, temporary namespace plans,
prior-version retirement, produced
executable admission and the original all-plus-eight case remain incomplete.
No renderer request/publication event or v1 source-effects journal is fabricated.

The original Make consumer pins a current settled output at its actual source
`fopen` entry through the existing `NativeOutputs` source lease. It does not
borrow the producer's expired PID or turn Make into a writer. A v6
`generated-source-entry` machine record binds the actual source visit to the
observed owner, object serial, revision, path, inode and settled digest. The
successful `source-open` references that machine record and compares the actual
returned FD's captured bytes and identity against the entry lease. Actual
source return releases the lease; the existing machine `pin-retired` event
closes replay's reader lifetime. Replay processes these records in the same
machine stream as producer effects and rejects writes/truncation while a
reader is live. Immutable v5 still admits only snapshot custody.

Run `NativeWriterTests.test_native_original_make_remakes_and_reads_generated_include_once`.
From a clean source fixture, original `-include generated.mk` first misses;
the original literal recipe creates `VALUE := produced\n`, Make naturally
restarts and reads it, and original `all` prints `once`. Require one generator
invocation, `VALUE=produced`, exact generated bytes/mode `0644`, empty stderr,
one successful generated-source visit and its actual pin retirement.
Typed producer/version/path/identity/digest/occurrence and custody-reference
mutants refuse in standalone validation. The old first-writer implementation
refuses the initial missing generated include with an unissued producer error.
`NativeWriterTests.test_native_original_make_generated_reader_refuses_writes_before_mutation`
requires a real `$(shell)` attempting to overwrite its currently parsed
generated source to refuse at writer entry, and a root Make `$(file ...)`
write to refuse without any native job. Neither output plans nor this reader
seam authorize root writes. These cases do not qualify failed generated
fopen after a pinned entry, old-version replacement, persisted generated
sources across queries or the complete original graph.
Truncation through a supported `O_TRUNC` open carries the same inode/mode/link
count, zero size and next revision into its paired writable open. This does not
admit standalone truncation. Failed-close replay accepts only the live model's
released-FD `EINTR`, `EIO`, `ENOSPC` and `EDQUOT` outcomes, retiring each binding.

Run `NativeWriterTests.test_native_original_make_job_writes_only_its_admitted_output_once`,
`NativeWriterTests.test_native_original_make_distinct_jobs_bind_their_actual_output_owners`
and `NativeWriterTests.test_native_original_make_output_plan_refuses_other_jobs_and_source_collisions`
in `scripts/validation_ownership/tests/test_native_writer.py`.
Actual original shell recipes must run once, produce exact stdout/stderr,
output bytes/mode and distinct real job PID/owner bindings, and clean the
private filesystem. The standalone controls change descriptor, revision and
path while preserving the machine record's hash; each still refuses.
Immutable v5 rejects the v6 authority payload. Negative actual invocations
exercise readonly admission, another job's output, undeclared paths and tracked
source collision. Shell redirection restoration pairs an actual foreign
descriptor's duplication onto a tracked output, retires only the old binding,
and preserves the restored stdout. The capsule still refuses that operation.
`NativeWriterTests.test_native_original_make_first_wire_refuses_unimplemented_namespace_mutations`
runs a sealed C recipe that directly calls `mkdir` and `symlink` on the issued
path. Both refuse before mutation without an external utility's additional
runtime probes; the symlink reaches the existing symlink prohibition.
`NativeWriterTests.test_native_original_make_truncating_reopen_and_close_errno_wire_match_live_model`
runs `printf first > result; printf final > result; printf once` as one original
recipe. Final bytes must be `final`, mode `0644`, stdout `once`, and stderr empty.
Hash-preserving mutations of truncation revision, FD and size refuse.
An inserted writable alias spanning the truncating open also refuses; the
actual double-redirection control closes its first writable aliases first.
Inserted `EBADF`/`EINVAL` close-failure events refuse; transformations to the
four accepted released-FD outcomes pass replay. These archive transformations
are not evidence of naturally occurring kernel close errors.
`NativeOutputCustodyTests.test_foreign_duplicate_release_preserves_failed_target_and_retires_actual_replacement`
uses actual `dup2`, stale/copied-token refusal, an explicitly injected failed
return with unchanged target, and component inherited-description exclusion.
`NativeWriterTests.test_native_original_make_separate_open_lineage_and_successful_exec_reconcile_cloexec`
uses a sealed C fixture as the original Make recipe, not a newly produced tool.
Separate opens of one inode issue distinct descriptions; dup shares one.
Actual `execve` with an invalid environment address returns `EFAULT` and
preserves all output FDs. The following successful reexec closes
`O_CLOEXEC`/`F_SETFD(FD_CLOEXEC)` bindings before the new image reuses FD3 for
`/dev/null`. An unflagged alias survives reexec and retires at actual exit.
Archive controls change the actual closure generation to a prior or future
image or a boolean while preserving the machine hash; each refuses.
`NativeOutputCustodyTests.test_exec_closure_and_target_duplicates_refuse_both_entry_orders_before_replacement`
checks `dup2`/`dup3` target replacement versus exec in both entry orders, with
unchanged actual target identity and descriptor maps on refusal, failed-return
continuation and actual `dup2` replacement after release. Inherited PID labels
in this component control are not actual child-process admission evidence.
`NativeWriterTests.test_native_original_make_failed_open_and_duplicate_preserve_actual_output`
runs a sealed original C recipe with real `O_EXCL`/`EEXIST`, self-target
`dup3`/`EINVAL`, negative `F_DUPFD`/`EINVAL` and negative-target `dup2`/`EBADF`
returns. The existing output remains exactly `final`/`0644`, stdout is `once`
and stderr is empty. Failed-open flags and failed-duplicate source/kind/
target/minimum/flags are bound to their admitted operation without a replay
state change; mutated operand records with recomputed hashes refuse.
Failed exec and foreign-replacement records require a live producer binding;
moving failed exec before output creation with renumbered records/hashes
refuses rather than inventing a preimage.
`NativeWriterTests.test_native_writable_make_admission_failure_exhausts_whole_session`
requires an invalid writable admission to fail and close the entire existing
budget; a subsequent budget operation must refuse. The writable wrapper uses
the same terminal-failure guard as the readonly wrapper.
`NativeWriterTests.test_native_original_make_actual_forked_output_alias_and_parent_wire`
runs a sealed original recipe whose real child inherits the output, duplicates
it to FD7 and writes through the shared offset. The actual parent waits and
closes its alias. Bytes must be `firstfinal`/`0644`, stdout `once`, and stderr
empty; the saved parent/child IDs must match the real job tree. List, boolean
and foreign parent-ID mutations with recomputed machine hashes refuse.
Machine replay uses the existing live ownership map, not the completed tree's
future PID set. The observed fork precedes inherited FD effects; exit expires
the actor after its descriptor retirement. Moving child inheritance before
fork or child close records after exit with renumbered records/hashes refuses.
No output effect may borrow a dead/unborn actor or follow terminal job policy.
This is actual fork/alias support, not a parked concurrent exec/replacement
race control.
Exec entry reserves the closing descriptions in the existing operation map,
excluding conflicting lock/mode/alias-close operations before kernel mutation;
successful exec verifies actual missing/surviving FDs and preserves one object
model. Mirror/hash-preserving zero-byte-write and same-inode/different-object
description-alias mutants must refuse in standalone v6 validation.

Run
`python3 -m unittest scripts.validation_ownership.tests.test_producer.ProducerTests.test_native_capsule_observes_actual_fork_dup_writes_and_atomic_retirement scripts.validation_ownership.tests.test_producer.ProducerTests.test_native_capsule_preserves_output_refusals`.
From a clean host fixture the actual C writer must run once, print `once`,
fork and duplicate its real output descriptor, write vectors followed by a
fixed-offset parent write, atomically replace the output and retire a separate
temporary version. Require `result` bytes `AxyD` and `result.tmp` bytes `final`,
both mode `0600`, distinct actual parent/child PIDs and matching sequenced
object lifecycles. Each undeclared output, append-description change,
standalone truncate, untracked FD replacement, hardlink and root/nested
anonymous-temporary control must refuse and clean the owned process tree.
Undeclared leftovers still refuse at exact final capture; unsupported operations
refuse before their kernel mutation. The existing generated-include/native-tool
publication test remains a compatibility control. These are deterministic
host cases for the existing native-custody contract, not original-eight proof,
and do not change ROM/RAM, save, locale, modern or archival build profiles.

The independent amendment controls
`test_native_capsule_keeps_retired_intermediates_separate_from_final_outputs`,
`test_native_capsule_large_and_zero_byte_write_family_matches_kernel_bytes` and
`test_native_capsule_negative_duplicate_operands_preserve_kernel_errors_and_fd`
in the same class require disappearing scratch/rename operands with only the
final `result` declared, 70,000-byte write/pwrite/vector elements within the
unchanged file budget, unused null zero-byte buffers, and all four duplicate
negative-operand error paths. Large entry buffers are read in bounded chunks;
zero-length operands read no pointer bytes. Duplicate targets/minimums use the
kernel's signed 32-bit conversion before model/binding validation. Require
140,000 bytes (`A` then `C`, 70,000 each), or `kept` after actual
`EBADF`/`EINVAL` returns, with complete process/object cleanup. The earlier
adapter instead refused legitimate scratch paths, large/zero memory operands,
and negative descriptor operands before the kernel could return normally.

`test_native_capsule_renamed_readonly_fd_keeps_unsupported_mode_changes_guarded`
covers renamed, replaced and unlinked regular objects through original,
duplicated and inherited readonly FDs. Unsupported FD mutations select the
actual `(pid, signed32_fd)` object binding, never the descriptor's stale recorded
pathname. Each actual `fchmod` attempt must refuse before changing mode;
the earlier adapter admitted all nine object/binding combinations.
`test_native_capsule_readonly_fds_do_not_pin_live_writer_content` covers original,
duplicated and inherited writers plus an `O_PATH` reader. Ordinary readonly
tracee FDs can observe later writes; they are not immutable captured
`SourcePin`s. Opening them does not force settlement while a writer remains
live. Require original `first` bytes and later `firstnext` bytes (or size nine
through `O_PATH`) using actual pipe synchronization and final mode `0600`.
Settle at actual last-writer retirement; immutable captured-source exclusions
remain unchanged. The earlier adapter refused all four live-writer cases.

The current capsule admits only its initial native-tool exec, with exactly
standard FDs. Existing policy rejects post-bootstrap root and descendant
execs; cloning does not restore bootstrap authority.
`test_native_capsule_cloexec_fds_do_not_enable_root_or_descendant_reexec`
opens an actual `O_CLOEXEC` output, sets `FD_CLOEXEC` and attempts both reexec
paths. Require refusal before kernel exec and complete process cleanup.
This does not qualify the unfinished multi-exec native-Make lane, whose actual
successful exec/descriptor reconciliation remains required.

For the held component regression family, run the class command above from
a clean child checkout. Require actual `dup`/`dup2`/`dup3` and both `F_DUPFD`
variants to preserve exact source bytes, inherited writer membership and final
settlement, with `dup2`/`dup3` rebinding preserving the old target's settled
bytes. Wrong targets, returned-object pins, minimums and copied/stale return
tokens must refuse without rebinding another tracked descriptor. Inject an
event callback failure into each transition: after physical final close or
source release and after owned teardown, completion must still refuse.
Teardown of absent-open/rename/unlink operations without their returns must
also refuse despite an otherwise empty inventory. Pre-fix controls admit a
different returned object, lose final event evidence or treat source/operation
cancellation as complete. Error-injection and modeled-child-inheritance
controls test the component observation contract, not actual native supervisor
authority; modeled late I/O errors do not prove filesystem-induced errors.
Also require malformed fork-set and aggregate-budget controls to leave the
complete binding/writer map and event count unchanged, alongside the actual
successful fork/child-retirement control. Malformed status returns must leave
bindings, versions, content and events unchanged; only the real subsequent
zero-return close/rename/unlink control may advance them.

These adapters are internal observations, not output-plan authorization.
The supervisor must derive their operands from the existing sealed Command,
actual job and finite output plan and supply actual stopped syscall inputs.
Full production integration and unchanged original eight-query budget fitness
remain required; the architecture hold is not cleared by these component cases.
Settlement or event-budget failure propagates without a successful admission.
Because the kernel open has already succeeded, its borrowed binding remains
diagnostic state until actual retirement; cleanup closes its owned duplicate
but does not invent a kernel rollback or make an active lifecycle complete.
These controls are component evidence only. Until original owned exec/syscall,
mount, source-reader and machine/host/archive consumers are wired, this object
model grants no native write authority and does not qualify
TC-WORKFLOW-NATIVE-MAKE-001 or any original eight-query outcome.

### TC-WORKFLOW-NATIVE-READONLY-001: observe immutable native Make and original tools

From a clean supported Linux x86-64 checkout with GNU Make 4.3, the existing
GCC/C++ host tools and namespace/ptrace launcher:

1. Run
   `python3 -m unittest scripts.validation_ownership.tests.test_foundation.FoundationTests.test_native_immutable_foundation_original_tool_source_jobs_and_budget`.
   The case compiles `tools/scanner/reader.c` through the original sealed
   `Command`/output/CWD compiler seam. Original Make includes immutable
   `input.mk`, eagerly executes the issued original-path reader against
   `input.txt`, and runs the original recipe. Require exactly
   `captured-input:immutable`, no stderr, the original tool and shell jobs'
   actual argv/CWD and waited success, and source FD bytes/modes/custody with
   matching machine pin retirements. A replay trap stays active. Compiler and
   Make share one original budget/deadline, and context exit removes owned
   scratch and processes.
2. Run the same class's
   `test_native_readonly_actual_write_and_foreign_execution_refuse` and
   `test_native_readonly_actual_completion_inventory_barrier_and_jobs`.
   Writes and undeclared execution must refuse without source mutation;
   actual source/completion/inventory barriers and job/machine pairs must
   remain valid. Retain the documented source-frame/pin, callback/register,
   job/signal, stale-view, runtime/tool, failure-envelope, cancellation and
   resource controls below; the integrated positive does not replace them.
   The foreign SIGKILL control requires the actual successful supervisor
   kill-return receipt, a failed probe/budget and complete owned teardown.
   Kernel stop ordering may change its denial diagnostic, never its refusal;
   the admitted self-SIGKILL controls separately preserve legitimate outcomes.
3. Run its `test_native_completion_variable_payload_boundaries` and
   `test_native_target_payload_boundaries`. Exact admitted UTF-8 byte payloads
   survive both consumer families; oversized payloads refuse. Filesystem path
   bounds stay unchanged. Preserve the original maximum-byte failure evidence.
4. The existing `ownership-probe-test` CI owner runs the complete native suite
   and exact selected-case inventory. No single positive, runtime capture or
   whole-suite label substitutes for the separate native source/job/budget/
   failure criteria. Tests create and retire only their own disposable inputs.

This source-only case requires no ROM, emulator, save reset or subjective
judgment. Dependencies are the merged immutable authority, namespace/runtime,
observer, compiler/tool, machine/archive and cleanup seams; conflicts are none
beyond their documented admission constraints. It changes no ROM/RAM/save,
locale/game content, modern/archival profile or default mapped behavior.
Unsupported native platforms, inputs or cross-lane combinations refuse.

### Native evaluator runtime-admission component

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

### Original compiler component

`ProbeSession.compile_native_command(command, cwd=".")` reuses the existing
typed `Command`, trusted compiler capsule, immutable `Snapshot`, output
collision check, compiler resource policy and session-issued `NativeTool`.
The admitted original driver names are `g++`, `gcc`, `/usr/bin/g++` and
`/usr/bin/gcc`; `cc` and arbitrary executable spellings are not admitted by
this component. The existing `compile_native` C/C++ lane is unchanged.
Declare snapshot-only `code`, exactly one repository-relative `outputs` path,
an existing snapshot directory as `cwd`, and exactly one separated `-o`
operand resolving to that output. Arguments, including argv[0] and relative
source/output operands, are not rewritten. At the owned initial exec stop,
the supervisor reads the actual bounded command line and verifies the actual
CWD inode against the configured private directory; the host binds that
structured observation to the original command before accepting its artifact.

Only this compile route derives a private source scaffold from the actual
snapshot: writable ancestors around the declared output, maximal readonly
source islands, and no precreated output. Exact output read/metadata/write
authority is compiler-only. Source/CWD ancestor mutation and undeclared
resources remain denied. The existing private `/work` compiler temporaries,
readonly trusted system runtime, aggregate budgets and owned cleanup remain;
generic native runtime limits and Make publication authority are not widened.
Only a regular output passing the existing ELF validator can be sealed with
its immutable input identities. A missing, symlink, directory or invalid
artifact is an explicit failure, not a usable tool.

For the #270 `TC-WORKFLOW-NATIVE-MAKE-001` compiler component, start from a
clean Linux host with the existing namespace launcher and trusted GCC tools.
Run `test_original_compiler_preserves_relative_argv_cwd_and_output` and
`test_original_c_compiler_preserves_relative_inputs`: actual compiler and
native execution must retain `source.cpp:7` and `source.c` relative-file
results and snapshot input owners. Run
`test_original_scaninc_compiles_original_sources_flags_cwd_and_output`: the
original four C++ files/four headers, flags, CWD and `scaninc` output compile,
and the sealed scanner reports the same actual header dependency as the
ordinary scanner, with no artifact added to the immutable tree.

Run the malformed-typed-input, command/namespace and compile-only layout controls;
nonstrings or mutable declaration containers must fail through the normal
terminal `MakeProbeError` boundary, leaving no issued tool. Wrong CWD/output,
collisions, unsupported driver, additional authority and cross-mode source
mounts must reject. The actual-initial-argv mutant must reject despite
successful compilation. In the real-write/capture controls, a linker map
targeting immutable source or its ancestor must reject at write admission;
an undeclared map target rejects at its earlier metadata probe. The direct
production resource predicate separately rejects every ancestor/source/extra
write and noncompiler output access. After a real successful compile, the
missing/directory/symlink/invalid capture mutants must refuse sealing.
Candidate executable GCC plugins remain unsupported: the ordinary plugin
control actually overwrites its private source, while the capsule rejects its
executable mapping before the plugin runs. This is not claimed as evidence of
plugin-origin write or rename interception. Every case retains source bytes,
refuses unissued tools and cleans its owned scratch.

This component has no save, ROM/RAM, localization or profile interaction.
It does not qualify generated-source versions/read pins, original Make
producer ownership, nested Make/reexec or the supervised eight-query family.
Those dependent #270 contracts and all final delivery gates remain open.

### Original compiled-tool placement

The original compiler additionally binds its declared output path into the
session-issued `NativeTool`. The key includes that path as well as sealed
bytes and immutable input owners. Default compilation/direct-native execution
and default readonly native-Make `/native/tool` placement remain unchanged.
An explicit `_native_make_readonly(..., native_tool=tool, original_tool=True)`
uses only that issued original binding; there is no caller-selected pathname
or second tool registry. Generic tools without a binding refuse. C++ shared
libraries and recipe executables still need their existing explicit resource
declarations; original placement does not infer them.

Reuse the snapshot-derived source scaffold, but mount its root readonly for
this lane. Every immutable source island and the separately materialized
verified tool image is a readonly child mount. Original source-entry barriers
verify actual source/guest device and inode pairs and readonly/nosuid/nodev
flags for every composed repository mount while the original Make is stopped.
The host admits that composed layout only for the exact session-issued tool
and captured runtime bytes, rejecting caller-supplied root/child mounts.
No binary is added to the snapshot inventory or immutable tree. Existing
native dispatch, original exec inputs, source pin/bytes, runtime and archive
validation remain active; every native/Make filesystem write still refuses.
This is not a writable producer namespace or generated-source custody.

For the #270 `TC-WORKFLOW-NATIVE-MAKE-001` original-tool member, run
`test_original_scaninc_executes_original_path_through_unchanged_make` from the
same clean compiler/namespace environment. Compile the original scanner,
declare its existing C++ libraries and the actual recipe `printf`, then run
unchanged `tools/scaninc/scaninc -I include unit.c` through original Make.
Require `include/sample.h` semantics/output, exactly one actual scanner exec
with unchanged argv and `/repo` CWD, waited/zero terminal native jobs and
snapshot input owners. No original output may persist in the immutable tree.

Run the original-native-tool binding and mount-custody controls. Copied or
changed tools, missing/generic bindings, invalid flags and foreign-session
objects must refuse. Real source/tool/ancestor writes and a genuine neighboring
executable must refuse; the ordinary neighboring executable control must
actually run. A modified composed-mount source identity must reject at the
stopped source-entry barrier. Explicit root/child mounts must reject before
launch. The same original-issued tool must still work through default
`/native/tool` placement and direct native execution. All controls retain
source bytes and owned cleanup. Generated producers/version pins, nested
Make/reexec, original supervised eight queries and every final delivery gate
remain unqualified by these member results.

### Original source-entry observation component

The existing explicit `runtime_files` declarations may capture the one
canonical kernel-data input `/proc/sys/crypto/fips_enabled`. Actual bytes or
actual absence are captured under the same trust, identity and ancestor
checks, then materialized in the sealed private root. No live `/proc` tree is
mounted into the guest and no generic proc, write or executable authority is
added. Nonoptional tool/library use and redirects remain forbidden. Present
Linux int-sysctl data has nonrepresentative zero `st_size`: its exact capture
uses a bounded 13-byte lookahead with reserved probe/result buffers and live
descriptor identity checks, not the ordinary size-based file reader. The
generic reader and every original limit remain unchanged.
For #270 `TC-WORKFLOW-NATIVE-MAKE-001`, run
`test_native_readonly_exact_kernel_fips_input_matches_actual_capture` to compare
actual host capture with original Make shell visibility (including the actual
absent input on the development host); do not relabel a fabricated fixture as
a present-kernel observation. Run
`test_native_readonly_kernel_fips_input_has_no_neighbor_write_or_executable_authority`
for uncaptured, neighbor, canonical-redirect, write and executable refusals.
Run `test_kernel_fips_zero_size_bounded_bytes_and_identity_controls` for actual
owned-file reads with explicitly shaped zero-size metadata, full signed-int
data, byte/control bounds, wrong identity and physical mid-read content change.
This finite pseudo-file control is not an actual present-kernel observation.
Run `test_kernel_fips_reader_captures_actual_linux_int_sysctl` for the same
bounded reader on a real present zero-size Linux int-sysctl inode
(`/proc/sys/kernel/pid_max`), with ordinary-reader preimage and byte equality.
That read-only kernel control does not grant its pathname any runtime-file
authority and is not an actual present FIPS-file observation.
Other runtime dependencies and the complete original eight-query outcome
remain independently required.

Native readonly inventory and source admission use the original fixed
per-file bound, not the declining remaining channel allowance. Actual
metadata/source capture still reserves aggregate observation bytes before
allocation; control, event and all other limits and charges are unchanged.
For #270 `TC-WORKFLOW-NATIVE-MAKE-001`, run
`test_native_readonly_inventory_keeps_fixed_file_admission_with_remaining_control`:
an unused valid binary inventory entry larger than remaining control capacity
must not prevent observing the small original Makefile. Run
`test_native_readonly_actual_source_keeps_aggregate_control_and_file_limits`:
actually reading the large Makefile exhausts aggregate capacity, and exceeding
the fixed per-file bound still refuses source admission; both clean up and fail
terminally. The pre-fix negative control rejects the unused inventory as
malformed. Generic command/compiler/mapped limits are unchanged. These
member cases do not qualify generated producers or the original eight queries.

The existing exact `/dev/null` mount is a nonexecuting writable exception,
not a generic device namespace. The trusted launcher verifies the actual null
character device (major 1, minor 3) and clears inherited NODEV only on that
exact leaf, retaining NOSUID/NOEXEC. Repository/runtime roots and inherited
submounts retain their recursive restrictions; other devices and directories
cannot request that clear. Syscall device-path admission is unchanged.
The #270 `TC-WORKFLOW-NATIVE-MAKE-001` null-device regression reproduces the
pre-fix original item-cap resolver redirection failure. Run
`test_exact_null_device_preserves_command_and_native_make_redirection`:
actual Python command, compiled native tool and original native-Make shell
redirection must produce usable results with zero stderr and owned cleanup.
Run `test_null_device_mount_exception_is_exact_leaf_only`: actual mount flags,
device identity, null writes/reads, unchanged root/source flags and other-device/
directory/executable/readonly/unsupported-clear refusals are required.
Repeat the existing recursive submount and unsupported-mount-setattr cases.
This fixes the admitted null resource, not missing Python runtime probes,
native producers or original eight-query qualification.

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

Trusted native ELF bodies are captured once per exact requested pathname in
the active immutable view, shared by executable closures and explicit library
declarations. The already captured core Make runtime retains its existing
whole-session lifetime and supplies identical shared bytes without another
host read. Each new executable still resolves its closure through the trusted
interpreter's original `--list`; generic command/compiler capture is unchanged.
New bodies keep the original trust, identity, file/control/deadline checks and
retained-byte charge. Map entries and closure references retain their own
metadata charges, but referencing the same byte object does not allocate or
read another body. This is not a larger quota, a global snapshot service, or
additional executable/library authority. View selection isolates the native
body map, restores the preceding map, and clears retired maps and session state.

For #270 `TC-WORKFLOW-NATIVE-MAKE-001`, run
`test_native_shared_runtime_bodies_are_captured_and_retained_once` for actual
trusted Python/sh/printf closure capture, two original guest invocations with
explicit libc, shared core byte identities and exact retained allocation cost.
The pre-fix run rereads libc three times and the interpreter once after warm
capture; the fixed run must perform no such duplicate body read while preserving
stdout. Run
`test_native_shared_runtime_cold_capture_preserves_original_quota_and_trust_failures`
for inactive-session, actual cold Python fixed-file/control/cache failures,
and an untrusted new pathname. Run the existing runtime reuse, nested view
isolation/restoration/failure and direct/shell conflicting-capture controls.
Run `test_native_shared_runtime_active_view_shutdown_and_misnesting_cleanup`
for populated outer and selected body/closure maps at explicit shutdown and
out-of-order view exit. Both retire every saved map, restore the original
loader, empty the view stack and retain normal owned cleanup; the pre-fix
saved-tuple mismatch raises `ValueError` and strands outer maps.
They require original deadline/limit identity, no second loader capture for a
warm executable, actual shared-map cleanup and terminal conflicting bytes.
Dependencies are the existing trusted capture, loader and immutable-view seams;
conflicts are none. No ROM/RAM/save/locale/profile or archival impact.

The original 2148-file first-query diagnostic remains a separate criterion.
With unchanged source, argv, original compiler and budgets, duplicate capture
removal progresses beyond aggregate exhaustion at 25,648,667 control bytes
to an undeclared original `find` executable. Explicitly capturing that
tool progresses, before path-statfs support, to unsupported syscall 137 at
26,678,061 control bytes; both
are failed queries, not original eight-query qualification. Managed-Python
directory-wide trust admission and original producer/nested/reexec custody are
unchanged by this byte-cache correction.

The original Python/hashlib runtime may also read OpenSSL configuration.
Declare only `/usr/lib/ssl/openssl.cnf` or its exact canonical
`/etc/ssl/openssl.cnf` through existing optional runtime files. The alias must
directly name that canonical regular file. The exact standard relative leaf
`../../../etc/ssl/openssl.cnf` is supported in addition to the direct absolute
leaf; other traversal or multi-hop aliases remain rejected. Existing root
ownership, nonmutable parents/file, before/after identity, control-byte/deadline
checks and sealed readonly materialization still apply. This is not an
`/etc/ssl` directory grant, an executable/library grant, or a live host mount.

For #270 `TC-WORKFLOW-NATIVE-MAKE-001`, from a clean Linux checkout with that
standard configuration, run
`test_native_readonly_openssl_configuration_preserves_actual_alias_and_bytes`
and `test_native_readonly_openssl_relative_leaf_is_sealed_and_readable`.
Compare captured bytes/mode against the actual canonical file without
publishing its contents; original Make must read the sealed guest alias in
both representations. The relative spelling uses an isolated declaration
fixture, not a modification of the host symlink. Run
`test_native_openssl_capture_has_no_neighbor_write_or_executable_authority`
and `test_native_openssl_capture_trust_and_identity_controls`: neighboring
`/etc/ssl` files, redirected canonical target, uncaptured reads and captured
writes refuse. The readonly data file cannot execute: original Make shell
expansion status 127 and its permission diagnostic match ordinary Make rather than being
converted into a successful shell result. Foreign ownership, mutable mode,
postread identity changes and an alias with missing canonical target refuse;
direct canonical absence remains optional. Use the existing physical alias
replacement/multi-hop/missing-target and generic traversal tests as shared
capture controls. No ROM/RAM/save/locale/profile or archival impact, and no
new dependency or service beyond the existing optional-file seam. This resource
member does not qualify the original eight queries: the measured original
first query with configuration capture still exhausts its unchanged aggregate
control budget. A failed spawn without a complete observed child refuses rather
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

For the #270 `TC-WORKFLOW-NATIVE-MAKE-001` returned-job regression, run
`test_native_returned_job_context_and_policy_mutations_refuse` from a clean
Linux source checkout. The original Make performs one shell expansion, two
commands on `all`, and an ignored exit 7. In both completion v4 and runtime v5,
changing only a returned recipe target, command index, context kind, or ignored
flag must reject and poison the session; the pre-fix native execution accepted
all eight well-typed changes. Run
`test_native_job_policy_machine_and_archive_bindings` for actual stdout
`onetwo`, original `all` command indices 1 and 2, expansion context, and
unignored/ignored statuses 0 and 7. Its archive controls omit or duplicate the
policy binding, change parent/child/dispatch, corrupt typed context/policy/wait
members, or move the v5 binding ahead of its actual root terminal event.

The existing machine channel records `native-policy` at the authenticated
original Make wait notification, with its child, dispatch, exact context,
ignored policy and raw wait status. Each executed child must have one policy
in the same execution/read pass. Runtime v5 additionally requires its actual
root terminal tree before that notification. The live decoder cross-checks
the complete returned-job policy against this separately captured observation;
the shared archive validator enforces the same typed lifecycle. These are
operational observations, not signatures against coherent rewriting of every
evidence channel. The existing control-byte, observation and deadline accounting
also applies to policy records. Default/no-reader and mapped paths do not gain
a new service or machine-evidence requirement. Failed Make omits completed
read/machine evidence; the readonly API rejects before publishing semantics or
job/context observations. Run
`test_native_failed_make_cannot_publish_mutated_job_context_or_policy` for actual
v4/v5 Make exit 2 after recipe exit 7 with well-typed returned diagnostic
target/policy changes. Instrumentation confirms the internal decoder reaches
that original nonzero outcome but the API poisons the session and returns no
accepted report. These failed diagnostic fields are not independently bound to
a completed machine trace, and no completion is fabricated for them. Run the
existing actual lifecycle, unignored recipe failure and mapped absence tests as
compatibility controls. Original producer/eight-query and final delivery holds
remain separate. Dependencies are the existing native dispatch/observer/machine
seams; conflicts are none, with no ROM/RAM/save/locale/archival impact.

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

Captured final file-alias targets must not contain `..` path components:
normalizing the final target does not materialize directories required by
kernel traversal of the preserved link text. Direct, `./target` and nested
canonical targets remain supported. Run
`test_captured_file_alias_traversal_refuses_and_direct_spellings_roundtrip`:
existing-host relative and absolute `sub/../target` aliases must refuse at
capture; direct/dot/nested spellings must preserve link text and round-trip
captured bytes. Retain live link/target replacement and native alias metadata
controls. This is final optional-runtime file-alias admission, not repository
include-path normalization or stock ancestor-alias behavior.

Trusted native ELF closures are captured once per exact active immutable view
and requested executable pathname. First acquisition charges the actual
captured bytes and retained cache resources; repeat queries reuse these bytes
rather than rereading the live host. Every invocation still gets fresh readonly
backing and actual source/dispatch/job/machine observations under the same
cumulative deadline and budgets. Nested `select_view` isolates captures,
restores the outer objects and clears selected caches; session failure/exit
clears all runtime captures. ABI decoding remains per invocation.

Completion source analysis enforces the existing 512-level expression limit
even without a budget object. Pruning, function classification and statement
syntax retain positional spans rather than copied bodies for every nested
expression; reference and metadata body analysis streams its results.
The host and archive source consumers pass their existing charge/checkpoint
callbacks through comment, declaration, define, recipe and assignment analysis.
Charges precede scanner stack/body allocation, and exhausted quotas/deadlines
propagate without falling back to an unaccounted scan.

For `TC-OWNERSHIP-NATIVE-COMPLETION-001`, run
`test_completion_expression_depth_rejects_before_nested_body_allocation`:
6,400 nested references in 19,201 bytes must reject at the existing depth
limit with less than 1 MiB traced temporary allocation; before correction
the scanner retains 61,436,800 body bytes. Require rejection both for dollar
references and ordinary nested grouping past 512, while 512 positional spans
remain representable without body copies. Run
`test_completion_analysis_propagates_charge_and_checkpoint_to_all_contexts`:
a 64-byte source-analysis quota must fail in assignment, define, inline recipe,
tab recipe and expansion-only contexts. A failing callback must propagate
from reference, comment and declaration analysis. The normal nested builtin,
short-reference, escaped-dollar and dead computed branch control must retain
exact names and charge/checkpoint activity; parsed source facts must retain
their actual assignment/dependency/root semantics. Retain the real native
completion and optional-machine archive controls. This bounded source fix
does not qualify generated versions or the original eight-query budget.

Completion screening retains one validated encoded selection per immutable
view. First acquisition charges the source scan and both the returned selection
and retained bytes; reuse charges a fresh decoded selection instead of scanning
the same bytes again. Each invocation still validates opened sources and
produces fresh actual observations. Nested views isolate this cache and restore
the outer bytes; failure/exit drops it. Mutating a returned dictionary cannot
alter the next request.

Run `test_native_completion_reuses_screening_without_poisoning_selection`:
capture a 786432-byte unused text input, require first screening to charge at
least its size, then require reuse to charge at least the serialized selection
but less than that input. Mutate returned names and an inventory digest and
require an unchanged next selection, two real original Make outputs/values,
unchanged deadline/limits and refusal after actual budget exhaustion.
`test_native_completion_screening_view_isolation_and_restoration` exercises
different BASE/CURRENT names in nested immutable views, real BASE execution and
exact outer cache restoration. The pre-fix repeated screening charges 787648
bytes and fails the reuse bound. This is not unchanged eight-query qualification
and does not remove any accounting category or increase limits.

`test_completion_long_statement_preserves_source_with_bounded_scan` admits a
512-KiB literal rule target, preserves its complete inline recipe/hash text and
parses the preceding VALUE assignment with unchanged dependency facts. Require
the combined comment/source scan to finish in less than three seconds on the
host. The pre-fix control takes 27.584 seconds and fails this exact bound.
Boundary escape checks visit separator candidates only, counting adjacent
backslashes without copying preceding prefixes; comment construction tracks
the current backslash run and joins accumulated characters once. Existing real
GNU/native escaped-colon, escaped-hash, recipe/hash and returned-archive controls
remain required. This restores bounded scanning cost, not runtime source
authority; the separately bound architecture hold remains in force.

Runtime effect-custody redesign is still WIP. The existing independently
validated completion ABI also derives original `do_variable_definition`,
`try_variable_definition` and `eval_buffer` executable spans; the ordinary,
direct multiline-define, reader-internal and target-specific machine callers;
and the copied eval ebuffer/floc member offsets. Derivation verifies frame
entries, parsed-name/flavor/result instructions, unique direct calls, null
file/size fields and the original `reading_file` anchor. This ABI alone does
not grant runtime completion authority.

Run `test_native_runtime_effect_abi_binds_complete_machine_call_families`
against the actually captured Make ELF. Verify each derived call targets its
real exported callee and the eval floc is 40 bytes after its ebuffer. Mutating
each caller opcode or function entry must refuse; mutating each supplied ABI
coordinate must fail independent machine-operand validation. Existing real
completion/barrier/job and foreign-ABI controls remain required. These are
machine ABI predicates, not source spelling evidence, and are necessary to
bind original parser effects without a second evaluator.

The internal readonly request `observe_runtime_completions=True` selects
version-5 observations, requires `observe_reads=True`, and conflicts with the
version-4 `observe_completions=True` request. Version 4 remains a compatibility
lane, not a substitute for runtime source authority. Version 5 inventories the
immutable view without selecting inferred supplier names. It observes original
definition arguments, actual returns, effective post-modifier bindings and
pristine nested `eval_buffer` inputs. Four hardware slots follow one real
pass/source/eval/effect invocation stack; existing kernel register restoration
and readback applies to every slot purpose. Target-specific completion follows
its independently derived private/export and command-origin merge, not the
earlier definition return; ordinary assignments/defines use their common
post-modifier continuation. Definitions inside evaluated text
use its actual consumed buffer interval. A multiline define's executed
position can be its endef, while its separate copied declaration floc names the
header. Neither is silently relabeled as the other.

The typed archive appends effects and evaluations to each `OriginalPass`;
existing constructors retain empty defaults. Runtime file visits retain pinned
immutable sources, and eval occurrences retain their pristine byte captures.
Standalone validation checks source/parent/return lifetimes, effective names,
counts and actual machine payload associations. Effect-input/result digests
bind the observer's exact captured event; eval-buffer digests bind its pristine
bytes. These are reconstruction/integrity observations, not signatures or
protection against hostile same-UID processes. Malformed read rows use the
shared versioned wire schema before runtime lifecycle interpretation.
After the read pass returns, version 5 observes original target-aware expansion
entry for recipes and both secondary prerequisite loops. An expansion binds its
actual file, verified caller, pristine unexpanded input and supervised CWD,
then uses the same source/eval/definition/innermost-return four-slot scheduler.
Unrooted post-read eval/definition still explicitly refuses. Actual Make
readiness checks these additional machine spans against readonly executable
mappings and captured bytes; each expansion validates its direct original caller
and file association without replay or a second code scan.
The `runtime_expansion_abi` discovery prerequisite identifies original
target-aware expansion and both actual secondary prerequisite loops from the
captured executable. It binds the restored variable/reader anchors, file layout,
recipe entry's saved original file, secondary entry's actual target/dependency
chain, and expansion/set-variables/split/enter callees. Neither an exported
symbol alone nor copied recipe floc authorizes secondary source attribution.
The typed archive retains `OriginalExpansion` occurrences on their originating
exec/pass, including occurrences after that pass's return. A first eval's
`ExpansionLocation` names its actual root without inventing a file visit.
Nested eval/effect locations retain their pristine eval source and consumed
interval; `visit` is null when no file reader is active. Post-read includes retain
real readonly snapshot descriptor/pin custody and may start a new source tree;
they are not members of the already-returned pass goal chain. Shared source-read
structural validation admits explicit, paired expansion intervals and validates
every post-read source event, rather than discarding those visits. Machine
expansion-input digests bind the captured root payload, and every root return
requires the actual post-read guard to be rearmed. Generated-source admission,
nested Make/restart qualification and the original aggregate-budget holds remain.
Run `test_native_runtime_expansion_abi_binds_original_callers_anchors_and_layout`
on the admitted host: captured Make must yield both verified loop callers and
the actual recipe/secondary expansion callers. Independent machine mutations
of each loop call, entry, target/dependency association, original file save,
anchor, layout and relied-on callee must refuse rather than selecting a surviving
sibling as complete evidence. This case alone is ELF/ABI evidence, not
generated-resource or eight-query resource qualification.
Version-4 staged suppliers forwarded through call-if/and/or/foreach remain
unqualified under the runtime redesign hold; compatibility results do not
grant complete supplier authority.

To exercise this WIP checkpoint on the admitted GNU Make 4.3 x86-64 host,
start each `FoundationTests` case with its temporary immutable view and let
session exit remove owned resources. Run
`test_native_runtime_effects_capture_computed_staged_define_and_eval` and
`test_native_runtime_nested_eval_include_and_effect_lifetimes`: require original
Make output, actual computed/staged/define/target bindings, distinct pristine
nested eval occurrences and properly paired returns. Run
`test_native_runtime_deferred_builtins_and_context_reset_capture_all_effects`:
forwarded if/and/or/foreach and conditional/include/target context resets must
still return and record the executed supplier. These are the pre-version-5
omission controls, not a new static supplier grammar.

Run `test_native_runtime_returned_effect_eval_and_machine_mutations_refuse`:
foreign/reordered effect/eval parameters, missing/wrong-type read fields,
coherently renumbered missing machine bindings and coherently changed pristine
eval bytes must raise `ReadEpochError`. Run
`test_native_runtime_live_effect_and_buffer_custody_mutations_refuse`: injected
live definition/effective-binding and eval-buffer custody errors must refuse
with owned cleanup. Run
`test_native_runtime_actual_private_prefix_secondary_and_failed_lifetimes`:
verify GNU private scope, a custom recipe prefix and secondary expansion, then
require a real Make error to retain its diagnostic without a successful
archive. Existing actual hardware register, job and immutable-view controls
remain required.

Run `test_native_runtime_target_modifiers_and_ignored_origin_capture_effective_bindings`:
two targets receive private exported VALUE, once with no command-line input,
once with override plus a command-line value, and once with a command-line value
that wins. Require original recipe and environment values, both captured
bindings, private/export flags and the effective winning value. The early-return
negative control omits private flags in the first two cases and records the
losing local value in the third. Target post-modifier custody is the same
runtime contract, not an additional source restriction.

This checkpoint does not complete graph-consumer integration, generated
creates/replacements/remakes, nested Make/restarts or the unchanged
original preliminary-plus-eight-query budget case. The sticky architecture
hold remains a delivery hold. Limits, accounting categories and the original
fixtures are unchanged. No ROM/RAM/save/localization, modern/archival profile or
generated-data change; no new execution authority or service. The new request
remains incompatible with generated/published resources until their real
producer custody is implemented and qualified.

Version-4 register controls establish compatibility only. Run
`test_native_runtime_actual_trap_registers_preserve_all_invocation_purposes`
for direct version-5 evidence: computed assignments, nested eval, includes and
a private target binding must return `original|target|original`. Read actual
kernel registers before/after every hardware trap and require all fields
unchanged except the required resume flag. The observed purpose set must
contain pass/source entry/return, expansion entry/return, effect entry/return/completion and eval
entry/return. Run
`test_native_runtime_callback_and_restoration_mutations_refuse_each_new_purpose`:
each of the seven runtime callbacks must reject a changed register object;
each matching trap purpose must reject a corrupted restoration readback.
Readback projections are decoder controls, not claims of physical kernel
register corruption. Both cases require owned cleanup. These are the existing
restoration criterion on actual version-5 paths, not generated/descendant or
aggregate-budget qualification.

Run `test_native_runtime_post_read_recipe_and_secondary_eval_capture`: recipe,
ordinary secondary and `.SUFFIXES` secondary expansion each evaluate a VALUE
assignment after source reading. Require `late`, a paired typed expansion/eval,
the actual effective VALUE binding with a null file visit, and complete ordered
returns. The earlier honest refusal is the pre-fix negative control.
Run `test_native_runtime_post_read_nested_skipped_eval_and_include_lifetimes`:
require `alpha|alpha|alpha|late`, three real eval occurrences, no skipped
assignment, actual post-read `post.mk`/nested `deep.mk` and optional missing
include visits, and unchanged initial pass goals. Its fifteen returned
lifecycle/payload/source/guard mutations must refuse, including independent
surrogate-string controls for target, pristine input and CWD with typed errors.
Run `test_native_runtime_post_read_actual_target_and_caller_mutations_refuse`:
null/wrong file, wrong secondary target register/loop caller and each of four
live machine-image substitutions must refuse and clean owned state. Preserve
the private/custom-prefix/ordinary-secondary and nested read-pass observations.
The returned-machine mutation case also removes the post-read guard arm or
coherently substitutes startup-only/either-missing-slot shapes, updating slot
readbacks, control bits and sequence numbers; substituting the active-reader
four-slot shape must also refuse. Each completed
pass and expansion return requires exactly one actual four-slot guard arm; the two-slot startup
shape is admitted only in its original exec context.

Version-5 native jobs additionally retain actual `argv` and supervised `cwd`.
Capture command-line bytes through the owned child's `/proc/<pid>/cmdline` at
its successful kernel exec stop, before resuming the executable. Retain at most
65536 bytes and 1024 arguments, preserving empty arguments and spaces, with
strict UTF-8 and a nonempty argv[0]. Charge captured bytes and retained job
observations under the existing cumulative accounting. The same stopped exec
machine observation carries `input_sha256` binding encoded argv/cwd to that
dispatch/PID; Make execution has a null binding. Returned jobs must match the
exact child machine binding. Version-4 and nontrace job formats are unchanged.
This is original execution-input observation, not generated-output authority,
registration matching, command replay, a signature or same-UID isolation.

Run `test_native_runtime_jobs_capture_actual_argv_cwd_and_machine_binding`:
execute an original shell expansion, recipe shell and declared direct printf.
Require `directtwo wordsinput`, their exact argv (including empty/spaced direct
arguments), actual `/repo` cwd and paired machine input bindings.
The pre-checkpoint observation lacks argv/cwd. Run
`test_native_runtime_job_execution_input_mutations_and_capture_failures_refuse`:
missing/malformed/foreign returned inputs, wrong machine input binding and
empty/nonterminated/invalid-UTF-8/oversized command-line captures must refuse
with owned cleanup. Capture substitutions are decoder controls, not actual
malformed Linux exec claims. Retain version-4 source/job/machine controls and
direct version-5 hardware parity. Generated command-to-resource matching,
descriptor/version custody and original budget qualification remain open.

Version-5 readonly native jobs retain a finite `tree` under their original Make
dispatch. Each actual fork records its parent and child. Each stopped newborn
records `start` only after its inherited hardware registers are cleared and
before its first resume; a child that never execs still needs this lifetime.
Each original exec or same-PID replacement records its generation, admitted
image, actual argv and supervised CWD after an actual register clear. Every
member must terminate before the original root's terminal event. The root's
original Make wait/context/ignore handling remains unchanged; nonzero or
signalled leaves do not fabricate a failing root shell result.

Real native SIGCHLD delivery requires kernel siginfo naming an actual forked
child of that parent, with its code/status matched to the child's observed
terminal wait status. Native SIGPIPE is admitted only after an actual
write-family syscall returns EPIPE on an owned pipe; the pipe error and actual
signal termination remain observable. Surviving/orphaned descendants refuse
publication and are removed by existing owned-process cleanup. No foreign
image, arbitrary signal, writable mount, generated-source authority, replay or
new accounting exclusion is admitted.

Returned tree events have strict shapes, order, ownership, start/exec
generations and complete terminal lifetimes. Every event is bound to the
machine packet for the same original dispatch/PID, with the complete returned
tree equal to the observed machine tree. Child start and every exec independently
require their immediately preceding actual register clear. These are captured
runtime/integrity observations, not signatures or hostile same-UID isolation.
Version 4 retains its original descendant/reexec refusal and wire format.
Version-5 exec reconciles every previously authorized descriptor, including
those above stderr, with its actual stopped-process `/proc` lifetime. Only
still-present known descriptors retain authority; closed and close-on-exec
descriptors are removed. Observations are bounded by the existing descriptor
limit and charged to the same observation budget. Unknown kernel descriptors
are never discovered into authority. This preserves original source,
directory and pipe inheritance without a second grant or CLOEXEC-flag model;
an unsuccessful exec has no exec event and does not perform this reconciliation.
Failed Make runs retain structural job/tree validation and their original
status/diagnostic, but do not publish a successful trace. Only successful runs
bind the root parent and complete returned tree to that trace's machine
observations; failed-run structural parent values are not archive authority.
Nested Make/read forests, Make restart, generated producers and the original
aggregate-budget qualification are separate unresolved contracts.

Run `test_native_runtime_descriptors_survive_only_their_actual_exec_lifetime`
and `test_native_runtime_closed_exec_descriptors_do_not_keep_authority` on the
admitted Linux GNU Make 4.3 x86-64 host. Compile `native_fd_exec.c` through the
existing NativeTool route. An original non-CLOEXEC source FD above stderr,
`dup2`, `F_DUPFD`, pipe endpoint, directory with `openat`, and fork-child exec
must retain their actual source/pipe output, matching the ordinary executable.
Clearing CLOEXEC through `F_SETFD` or `FIONCLEX`, including a descriptor created
by `F_DUPFD_CLOEXEC`, must retain it. Reopening a closed descriptor number after
exec must read the new `second` source, not its old `input` source.
Consumption of an explicitly closed, `O_CLOEXEC`, `F_DUPFD_CLOEXEC`,
`F_SETFD`/`FIOCLEX`, or close-on-exec stdin descriptor must refuse as unavailable;
so must an unknown descriptor. Before correction all six nonstandard
source/dup/pipe/directory/fork cases refuse despite ordinary success.
Run `test_native_runtime_failed_actual_exec_preserves_authorized_descriptors`:
the bounded diagnostic changes the already-admitted exec's argv register to an
invalid address, observes the actual kernel `EFAULT` return, and requires the
original live source FD/output and unchanged exec generation. This is an
injected failed-kernel-transition control, not an ordinary argv parity claim.
Every case requires owned cleanup; unchanged v4 refusal and original descendant
pipeline/SIGPIPE controls remain coupled checks. These readonly cases do not
qualify generated descriptors, nested Make/restarts or the original eight-query
aggregate budget.

On the admitted GNU Make 4.3 x86-64 host, run
`test_native_runtime_readonly_descendant_pipelines_and_exec_lifetimes`:
the unchanged eager and recipe printf pipelines must produce `second`, matching
ordinary Make; a same-PID exec
replacement must produce `replaced` with generations 1 and 2; nonzero,
SIGUSR1-terminated and nested shell leaves must preserve the successful original
root output while retaining all actual child statuses. The pre-checkpoint
pipeline refusal is the negative control. Run
`test_native_runtime_undeclared_descendant_images_refuse_before_exec`: an
undeclared false image must refuse before actual exec, both as a same-PID
replacement and under a real owned child; cleanup must retain no owned process.
Run
`test_native_runtime_fork_without_exec_retains_start_and_terminal_custody`:
compile the small owned C fixture, fork a child that exits 7 without exec and
wait for it; require a successful root, a started generation-zero child and its
actual terminal status. Run
`test_native_runtime_descendant_tree_returned_shapes_and_clear_omissions_refuse`:
foreign parent/PID/image, generation, argv/CWD, signal/status and missing
start/terminal mutations must refuse. Independently omit inherited-start clear,
replacement-exec clear or all tree machine packets; each must also refuse.
These are returned-evidence/decoder controls, not physical register-corruption
claims. Run `test_native_runtime_owned_broken_pipe_and_orphan_cleanup`:
the compiled fixture writes to its own closed-reader pipe and must actually
terminate with SIGPIPE under Make's ignored-recipe policy, recording EPIPE and
then continuing to `done`; a shell leaving a live busy-loop child must refuse.
Use each test's temporary view and require session exit to remove all owned
resources. Run
`test_native_runtime_failed_roots_and_completed_descendants_preserve_make_error`:
unignored exit 7, an actual SIGUSR1-terminated root and exit 9 after a completed
nonzero child must preserve ordinary Make's status 2 and actual diagnostic,
without accessing an absent success archive. Malformed parent/start/terminal
trees on that failed path must still refuse with typed errors. None of these
cases substitutes for original eight-query resource
qualification or generated/nested-Make integration.
Run `test_native_runtime_descendant_deadline_cleans_live_owned_tree`: start a
root shell waiting on an actual busy-loop child, capture its actual fork,
register-cleared start and exec, then shorten the supervisor deadline after
that exec. Require explicit deadline refusal, no successful terminal/archive
and owned cleanup. The earlier supervisor deadline allows its failure report
to be decoded within the unchanged host budget; this is a cancellation control,
not aggregate-budget qualification.

Version-5 native exec reconciles existing self-signal grants against actual
kernel `SigPnd` and `ShdPnd` masks at the owned exec stop. Only already
authenticated grants that remain pending survive. Both masks must occur exactly
once as 64-bit hex fields in a bounded status capture; those bytes are charged
to existing observation accounting. This neither grants a foreign signal nor
retains a successful-but-discarded signal. Forked children still start without
their parent's pending grants. Delivery/termination markers reset as before,
and actual sender/code checking remains required at delivery. Version 4 retains
its existing exec boundary.

Run `test_native_runtime_pending_self_signals_survive_exec_not_fork`: compile
`scripts/validation_ownership/tests/fixtures/native_pending_exec.c` through the
existing owned native route.
Blocked self-kill, queued self-SIGUSR1 and actual closed-pipe EPIPE/SIGPIPE must
survive same-PID exec, then reach their handler on unblock, matching the same
program's ordinary host result. Bind actual kernel pending masks and grant
states to the exec generation, not the transport collection's ordering.
A forked child must inherit neither the pending signal nor its grant. A
blocked self-SIGUSR2 discarded by changing its disposition to ignored must
lose its grant at exec while the still-pending SIGUSR1 survives. The pre-fix
kill/queued/pipe cases refuse with signals 10/10/13 instead of succeeding.
Run `test_native_runtime_exec_pending_masks_and_foreign_signal_refuse`:
missing/duplicate/malformed masks, empty/oversized/nonbyte status captures must
refuse; a real foreign SIGUSR2 sent at the owned exec stop must also refuse
rather than being granted by its pending bit. Capture substitutions are decoder
controls, not claims of physical `/proc` corruption. Require owned cleanup,
and retain failed queued-signal rollback and actual hardware parity controls.
These cases do not qualify generated versions, nested Make or the original
eight-query budget.

Self-signal admission records the attempted syscall separately and issues grants
only after its actual successful kernel return. Failed sends neither introduce
a grant nor discard an earlier successful one. Standard signals coalesce to
one origin per kernel thread/shared pending queue; kernel realtime signals (32 through 64 on the admitted Linux
x86-64 host) retain a count per successful send and consume one on each
authenticated delivery. Existing observation-count and byte budgets charge
every successful grant and bound outstanding counts. Exec pending-mask
reconciliation retains those counts only for already-authorized pending
signals, without interpreting a mask bit as a new or repeated grant.

Successful `rt_sigtimedwait` also consumes exactly one existing signal grant;
timeouts and pre-dequeue failures consume none. An output-copy `EFAULT`
can occur after the signal is dequeued. Before a wait and after `EFAULT`,
the supervisor reconciles issued origins with the actual thread and shared
kernel queues, retiring absent origins before another wait or resend.
An origin already missing at a later wait is not evidence of that wait's
consumption. `SI_QUEUE` sender fields are
caller-supplied and are not origin authentication. At the owned queued-send
entry, the supervisor temporarily replaces unused x86-64 siginfo union bytes
32 through 47 with a private 16-byte marker. It restores the caller's bytes
at the actual syscall return, retaining the marker only for a successful
send and preserving the first standard-coalesced origin. An actual queued
delivery or synchronous consumption must match that issued marker before
the supervisor restores the original union padding for the candidate.
Sender, signal, errno and application payload fields are not changed.
For successful waits with no siginfo output, bounded kernel `PTRACE_PEEKSIGINFO`
observations of both thread and shared pending queues identify the retired
marker; unknown or ambiguous custody refuses. No caller-supplied sender
field, pending-mask bit, stale count or foreign queued signal is a substitute.
Kernel-generated `SI_USER`/`SI_TKILL` and owned EPIPE/SIGPIPE keep their
existing self-origin checks. This is a local signal-lifecycle boundary,
not hostile same-UID OS isolation.

Partially accessible siginfo output is deliberately unsupported. After an
output-copy `EFAULT` retires a queued origin, an accessible padding byte causes
explicit terminal refusal before the candidate resumes; the supervisor does
not emulate partial usercopy, overwrite unrelated or read-only output, or
claim ordinary-host parity for that buffer shape. Fully inaccessible output
still returns its actual kernel `EFAULT`. Run
`test_native_runtime_partially_accessible_siginfo_fault_refuses_before_resume`:
ordinary execution covers each accessible padding prefix from zero to 16 bytes
and a 64-byte prefix against an unmapped tail; native execution must refuse
the first potentially observable partial padding for both queued-send syscalls
and standard/realtime signals, with owned cleanup. Also run
`test_native_runtime_standard_signals_coalesce_per_kernel_pending_queue`:
one shared and one thread-directed SIGUSR1 must both reach their handlers,
matching the two deliveries observed outside the supervisor.

Run `test_native_runtime_consumed_self_signal_rejects_forged_foreign_queue`
and `test_native_runtime_signal_consumption_preserves_real_payload_and_padding`.
The owned `native_signal_consume.c` fixture blocks SIGUSR1, sends an actual
self-queue through either queued-send syscall and consumes it with a real
wait. A controlled foreign process then queues SIGUSR1 with a forged recipient
PID and `SI_QUEUE`. Require terminal refusal both with no grant and when a
later genuine self-send coalesces behind that foreign signal. The pre-fix
program instead accepts the foreign handler and succeeds. Include null-output
waits, timeout/input-EFAULT controls, and ordinary-host parity for actual payload,
sender, caller siginfo memory and delivered union padding. Require owned
cleanup. Also run `test_native_runtime_output_copy_fault_retires_dequeued_origin`
for valid-mask/invalid-output `EFAULT`, genuine resend and subsequent foreign
waits with and without output, through both queued-send syscalls and standard
and realtime signals. Retain the realtime/count/coalescing, failed-send, exec/fork
and broken-pipe controls above. These cases do not qualify the original
producer, generated-version or eight-query contracts.

Self-SIGKILL does not reach an ordinary syscall return. The supervisor uses the
actual ptrace exit stop instead: it requires the exact outstanding self-send
syscall, original self-PID/signal operands and actual return register zero,
with PID/TID and signal operands interpreted through one shared signed 32-bit
request decoder at syscall entry and fatal exit, and the kernel exit status
paired to the final terminal wait. Upper register bits are not part of the
kernel's `pid_t` or `int` argument. An external
SIGKILL while stopped before the send leaves return register `-ENOSYS` and
explicitly refuses; an entry attempt is never terminal authorization.
Exit stops resume before the final wait. On refusal, owned cleanup resumes
already-parked killed processes before reaping, including processes stopped
at exit; otherwise those stops could prevent cleanup from completing.

Run `test_native_runtime_actual_sigkill_outcomes_cover_all_self_send_forms`:
compile the owned `native_sigkill.c` fixture and exercise kill,
rt_sigqueueinfo, tkill, tgkill and rt_tgsigqueueinfo. The same ordinary binary
must terminate with SIGKILL; native Make must preserve ignored status 9,
continue to `done` and retain actual zero-result/exit-status observations on
the same job PID. Run
`test_native_runtime_actual_sigkill_uses_kernel_pid_t_conversion`:
use nonzero bit 32 and all-ones upper 32 PID bits for each supported syscall;
for tgkill and rt_tgsigqueueinfo also vary TID alone and both operands. All 14
ordinary binaries must actually die by SIGKILL, and native ignored recipes must
retain raw status 9 and `done`. Before correction all 14 native invocations
refuse the successful send at exit. Upper-bit normalization must still refuse
low-32-bit foreign, zero/broadcast and negative/group target mutations.
Run `test_native_runtime_actual_signal_operands_use_kernel_int_conversion`:
compile `native_signal_width.c` and pass bit 32 or all-ones upper 32 bits in
both target and signal operands for all five syscall forms. Compare each
ordinary binary with its native execution: blocked SIGUSR1 must reach exactly
one handler with its actual sender PID and signal code; SIGKILL must terminate
with status 9 while the ignored native recipe continues to `done`. Before the
shared decoder, these successful native sends refuse despite ordinary kernel
success. Run `test_native_runtime_normalized_invalid_signals_do_not_add_grants`:
use upper-bit zero, negative and 65 signals, plus a valid blocked SIGUSR1
followed by invalid 65. Bind actual syscall returns and grants to each job's
captured argv/PID. Zero returns zero without a grant; negative and 65 return
EINVAL without a grant; the prior-send case retains exactly its earlier
SIGUSR1 grant. Every case then receives exactly one valid SIGUSR1, matching
the ordinary binary. Invalid sends must neither create authority nor erase
prior authority. These controls do not qualify generated namespace custody
or the original eight-query family.
Run
`test_native_runtime_sigkill_entry_attempt_and_outcome_mutations_refuse`:
send a real supervisor-origin SIGKILL before resuming the self-send syscall,
require its actual `-ENOSYS` outcome and explicit refusal, then independently
substitute return/syscall/target/signal/pending/terminal fields on genuine
self-send controls. Kernel-readback substitutions are decoder controls, not
physical register corruption. Every refusal must complete owned cleanup.
The pre-fix entry grant admitted an attempt without requiring a successful
send; the actual exit-stop diagnosis distinguishes those outcomes.

Run `test_native_runtime_realtime_counts_standard_coalescing_and_failed_send`:
the `native_queued_exec.c` fixture blocks and sends two realtime signals via
kill or sigqueue, execs and receives both; also receive both without exec.
Two standard signals coalesce to one. A genuine EFAULT on the middle queued
send must leave the earlier grant intact, and the later success must result
in exactly two retained realtime grants. Bind captured grant counts to actual
syscall returns and exec generations; compare ordinary binary behavior for
unmodified controls. The pre-fix two-queued/exec case rejects its second real
delivery as signal 34. Retain old successful self-SIGKILL, failed queued send
followed by foreign SIGKILL, pending-mask/fork controls, actual SIGPIPE,
orphan/deadline cleanup and direct hardware restoration. These are the
existing signal-lifetime contract, not generated custody or overall native
qualification.

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
   verifies captured timezone data bytes/mode, startup-file absence, unchanged
   executable trust roots and the UTC alias target. Positive timezone cases
   use owned physical `UTC -> Etc/UTC` and regular-file fixtures with only
   path admission substituted, then exercise the real capture and guest
   materialization at the declared system paths. Installed timezone data can
   be runner-owned or mutable and must not be treated as trusted automatically.
   `test_native_runtime_system_owner_and_write_permissions_refuse_before_capture`
   exercises the real admission predicate with foreign-owner, group-write and
   other-write metadata controls; each refuses before byte acquisition.
   These controls do not change host ownership or production trust policy.
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
   target refusal. It separately rejects an owned traversal alias.
   These private fixtures prove capture consistency, not root ownership.
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

The shared effective-variable decoder admits names up to 128 UTF-8 bytes and
values up to 65,536 UTF-8 bytes. Its bounded native string reads include one
additional byte for the terminating NUL; this does not increase the payload
limit. Both static assignment completion and runtime effect completion use
this decoder. For the #270 native completion case, run
`python3 -m unittest scripts.validation_ownership.tests.test_foundation.FoundationTests.test_native_completion_variable_payload_boundaries`.
It executes original GNU Make with 128-byte names and ASCII or multibyte
values: 65,535 and 65,536 payload bytes must survive unchanged through both
completion routes, while 65,537 bytes must refuse and retire owned state.
The pre-fix decoder rejects every 65,536-byte positive because it has no space
to observe the NUL. This host-only boundary has no ROM/RAM/save/profile impact
and does not qualify generated-source custody or the original eight-query
family.

Non-filesystem target payloads similarly admit 4,096 UTF-8 bytes, with a
separate byte for the NUL in both recipe/secondary expansion and job-context
readers. Run `FoundationTests.test_native_target_payload_boundaries` through
the same unittest module: a phony target reached from `all` uses ASCII or
multibyte names of 4,095, 4,096 and 4,097 bytes. With runtime completion enabled
and disabled, the first two produce exactly `target-ok` and preserve the exact
recipe-job target. Runtime observation also retains both recipe and secondary
expansion targets. Oversized names refuse and clean owned state. The pre-fix
readers reject all four 4,096-byte positives. Filesystem pathname admission
and its NUL-inclusive 4,096-byte read bound remain unchanged.

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
Stat/lstat/fstat/newfstatat, supported statx/statfs/fstatfs, access/readlink variants
and directory results retain their actual supported ABI data. Failed operations
keep their status without becoming successful source consumption. Unreadable
buffers and unsupported requests are explicit, not empty successful records.

Explicit optional `/proc/filesystems` and `/proc/mounts` declarations capture
their actual bounded kernel text despite an advertised zero file size. They
are copied immutable inputs, not live guest `/proc` mounts. The mount-list
declaration preserves the physical `mounts -> self/mounts` and
`self -> <collector-pid>` aliases and binds bytes to that exact collector task.
Only its task directory and mount-list leaf may use the collector's actual
UID; other objects retain root-owned/nonmutable trust. Foreign PID leaves and
direct `/proc/self/mounts` declarations are not extra admitted input names.
Neither declaration grants neighboring proc content, writes or execution.
The reader uses bounded nonblocking/no-follow reads through complete EOF,
before/after descriptor identity, original fixed-file/deadline limits, and
conservative scratch/assembled/returned control charges. A blocked, changed
or over-budget input terminates; no truncation or empty-size fallback exists.

For #270 `TC-WORKFLOW-NATIVE-MAKE-001`, on the documented Linux native host,
run `test_kernel_filesystem_text_capture_preserves_actual_proc_aliases_and_bytes`
to compare real kernel input bytes and parsed current-task alias identities.
Its pre-fix capture refuses both paths. Run
`test_native_kernel_filesystem_text_reads_sealed_actual_capture` to compile a
real C reader and invoke it through original GNU Make: guest stdout equals
the captured bytes even with a live kernel recapture trap active. Run
`test_kernel_filesystem_text_reader_keeps_bounded_complete_reads` for empty/
nonempty/multichunk EOF and actual fixed-file/control/deadline/blocked/identity
negative controls. Run
`test_kernel_filesystem_text_exact_alias_and_trust_boundaries` for foreign
PID, physical alias redirection, foreign UID, writable input and post-read
replacement refusal. Run
`test_kernel_filesystem_text_actual_absence_stays_absent_in_guest` for both
optional leaves: actual absent leaf state has no bytes or invented task
aliases, and original GNU Make sees absence in the guest. A present dangling
mount-list alias still refuses its missing target rather than becoming absence.
Run
`test_native_kernel_filesystem_text_grants_no_neighbor_write_or_execution`
for undeclared reads, proc neighbors, writes and executable admission refusal.
Retain existing exact FIPS, SSL and generic runtime-alias controls. New source
trees and session state are retired by each case. Dependencies are the existing
runtime capture/materialization/alias and bounded-reader seams; conflicts are
none, with no new default inputs or ROM/RAM/save/locale/profile/archival impact.
This does not admit present SELinux metadata directories or prove original
`find`/eight-query qualification. Captured mount-list text is a collector
snapshot, not a claim that its namespaces match later guest namespaces.

Path `statfs` (x86-64 syscall 137) uses the same guarded ownership and metadata
transport as descriptor `fstatfs` (138), not an unrestricted syscall grant.
Both have a 120-byte frame, zero flags/mask/offset, and a zero success or
negative kernel result. The native helper reissues pathname operations against
that pathname and descriptor operations against a freshly opened owned
descriptor. Neither permits an outside pathname, an unknown descriptor, a
write, or execution of a declared metadata resource.

For #270 `TC-WORKFLOW-NATIVE-MAKE-001`, start from the documented native host
setup and run `test_native_readonly_issued_statfs_preserves_source_and_kernel_outcomes`.
The session compiles a real C tool and runs it through original GNU Make.
Compare its result with ordinary execution: source pathname/descriptor
filesystem properties agree, an absent source returns `ENOENT`, and an
invalid buffer returns `EFAULT`. The pre-fix invocation refuses syscall 137.
Run `test_native_readonly_statfs_keeps_path_and_descriptor_authority` to
require terminal refusal of outside/escaping paths and an unknown descriptor.
Run `test_statfs_actual_buffers_transport_and_negative_revalidation` for
real 137/138 kernel buffers and statuses, legacy/packed round trips, native
revalidation of the authorized missing import probe, and rejected changed
status/buffer, size, flags, mask, offset and positive-result controls. Invalid
pointer results stay observable but cannot become reusable helper results.
For successful 137/138 frames, change one returned filesystem-type byte while
keeping the valid ABI and zero result: the helper must issue the real operation
successfully and still reject the changed buffer. Removing successful-137
buffer comparison must fail this control. Run the existing complete-metadata
case for both pathname-137 and descriptor-138: allocating a real file changes
their observed capacity, refuses old buffers and prevents cached reuse. This
also retains other syscall compatibility without assuming shared-host capacity
is stable enough to guarantee byte-identical successful replay. These cases
use new temporary source trees and retire
all session state; dependencies are the existing path/fd authority, compiler
and metadata seams, with no new resource grant, conflict, ROM/RAM/save,
locale/profile or archival impact.

The original-find reproduction first progressed from syscall 137 to undeclared
`/sys/fs/selinux`. On the measured host this is a present sysfs directory, not
an absence. The optional `native_metadata_directories` tuple now admits only
the exact standard `/sys/fs/selinux` and `/selinux` mount probes, with a maximum
of two distinct names. Present probes retain actual root-owned, nonmutable,
canonical directory backing through exact readonly/nosuid/nodev/noexec bind
mounts; absent probes remain absent. Content, enumeration, descendants, writes
and execution are not granted. Declaration and pre-invocation capture check
actual identities; the supervisor binds actual mount dev/inode/mode and
source/guest identities within its namespace, where host UID 0 may be unmapped.
Collector-side root ownership is not replaced by a guest UID spelling test.
Actual confined `statfs` flags are retained: a readonly bind may set
`ST_RDONLY`, so arbitrary unconfined SELinux-library equivalence is not claimed.
The accepted outcome is unchanged original filename traversal and source
queries, not fabricated host flags.

Libselinux's filesystem/mount text and `/etc/selinux/config` startup inputs
remain explicit optional file declarations. The configuration name has exact
canonical root-trusted regular-file presence/absence capture through the
existing runtime input seam, not a broad `/etc/selinux` grant. No input is
added to native defaults; nonstandard discovered mount paths still refuse.

For #270 `TC-WORKFLOW-NATIVE-MAKE-001`, run
`test_native_selinux_mount_metadata_preserves_actual_type_and_absence`:
an issued C tool in original Make observes the real present mount type/block
size, actual readonly guest flag, and `ENOENT` for the absent standard probe;
the returned 137 frame has its real status and 120-byte ABI. The pre-fix
request has no metadata-directory API and the preserved original-find
preimages refuse syscall 137 and then the unadmitted present mount. Run
`test_native_selinux_metadata_mount_has_no_content_or_descendant_authority`
for actual read/list/write/child/neighbor refusal on both standard probes.
Run `test_native_selinux_metadata_declarations_keep_exact_trust_and_identity`
for malformed/duplicate/nonstandard declarations, actual trust/type/alias
guards and changed backing before invocation. Run
`test_native_selinux_metadata_supervisor_rejects_malformed_authority` for the
actual typed supervisor parser, including unhashable paths, wrong shapes,
duplicate/count limits, boolean/negative/overflow identities, file/writable
backing and foreign ownership. Run
`test_native_selinux_metadata_absence_rejects_actual_replaced_backing` against
the supervisor's admission routine: real host and guest dangling symlinks,
files, directories and unexpected mounts cannot replace captured absence.
Only an actual `lstat` `ENOENT` is absence; a symlink-following existence
check fails the preserved negative control. Run
`test_native_selinux_config_exact_optional_file_boundary` for real config
presence/absence through original Make and exact canonical/neighbor/write
controls. Run `test_native_original_find_with_exact_selinux_startup_resources`
to declare all startup resources and compare unchanged GNU Make/find source
traversal with ordinary execution: exact `tree/a.txt` stdout and Make value.
Temporary trees/session state are retired by every case. Dependencies are
the existing trusted runtime, exact mounts, metadata transport and kernel-text
capture seams; conflicts are none, with no ROM/RAM/save/locale/profile or
archival impact.

This small original-find positive is not original eight-query qualification.
With the same 2148-file fixture, original producer/argv and unchanged limits,
the first query now progresses to an unadmitted original `/usr/bin/mkdir`
at 26,873,532 control bytes. The readonly evaluator does not grant original
producer writes merely because this diagnostic reaches them. Complete native
producer lifecycle, read-time generated versions, nested/remake/reexec,
all eight queries and final architecture/delivery gates remain open.

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
capacity from `statfs`/`fstatfs` may change even without a source edit. Its complete
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
