# Validation ownership graph

Issue [#180](https://github.com/laqieer/fireemblem8-expansion/issues/180)
is an accepted **framework capability**: a machine-readable, fail-closed map
from admitted repository paths to existing validation evidence. It records
semantic ownership that Git history cannot derive reliably.

The graph is observational. It reports additive owners and review invalidation;
it does not execute a selected gate, skip a gate, or narrow local checks.
Build CI requires the ownership suite and whole-tree check in `ownership-tests`,
but those checks only validate this contract. Any use for narrower validation
requires a later independently accepted issue with non-inferiority evidence.
Issue #181 is parallel and does not consume or authorize this graph.

The dedicated worker keeps the exact-PR-base verifier, isolated regression
suite and public graph check together. It performs its own exact-head checkout,
revision comparison, Git-authority hydration and native/ARM query dependency
setup. It runs on full PR, master-push, manual and exact-identity fallback
routes; metadata-only/review-first events platform-skip it without gaining
full-run evidence. All nine Build jobs remain present, with the existing
`host-tests`/`build`/`summary` branch-protection contexts unchanged. Full summary
and trusted prior-run evidence require ownership success in that same exact
run; missing, skipped, failed, cancelled or timed-out ownership rejects.

The ownership CI envelope is 90 minutes, separate from the unchanged
60-minute host budget. Its initial 60-minute envelope was exhausted while the
same regression sequence was still progressing; the next incomplete case
also completed independently. The bounded increase provides shared-runner
headroom without dropping tests, moving the native probe owner or changing
any individual probe/fixture limit. It is not full-graph resource calibration:
the complete combined ownership workload must still be assessed before
completion. A timeout remains a failure. This job is ordinary CI invocation
evidence, not the independent coordinator-owned verifier capture (H1).

Graph test discovery uses unittest's package `load_tests` protocol to exclude
the foundation, producer, dependency and metadata codec modules. They run once in
the required `ownership-probe-test` owner in `extended-host-tests`; they are
not repeated by the graph gate or workflow discovery. The parsed discovery
partition requires every test ID to retain exactly one execution owner.

Complete integration of the shared
[issue #206 / PR #212 foundation](https://github.com/laqieer/fireemblem8-expansion/pull/212)
is a required dependency, not optional hardening. Its small real consumer and
the asset-specific host tests do not prove the full 112-domain graph, oracle,
lifecycle or public command. Registered native-tool results and generated Make
include outputs must reach that shared observer through admitted APIs before
this acceptance can pass; an older duplicate sandbox or fabricated empty
command output is not a substitute.
The primary-source discovery API from
[#234 / #235](https://github.com/laqieer/fireemblem8-expansion/pull/235)
is also a genuine prerequisite. It must be available in the actual selected
BASE before directory-backed registry comparison is qualified. No CURRENT
schema implementation is substituted into a historical BASE, and introducing
the API does not claim that older trees already supplied it.
The graph reads generated include bytes from the completed
`MakeObservation.generated` result corresponding to the actual
`MAKEFILE_LIST`. Resolving a registration does not execute its producer
in advance. Native dispatch, source receipts, replacement and cleanup remain
owned by P; no preparatory sample supplies the graph's source census.
The graph also checks actual repository file-open attempts, not only the
successfully loaded `MAKEFILE_LIST`. An unknown optional include cannot acquire
authority merely because Make ignored its absence. Attempts must resolve to
captured regular sources or actual completed producer outputs; known generated
include/remake behavior remains valid. Syscall spellings and real metadata
remain intact, rather than being inferred from a new Make parser.

Asset discovery uses the [captured-source API](asset_manifest.md#captured-source-discovery)
with immutable path/mode/content identities, not candidate claims or validation
bypasses. Its digest is stable across equal Git materializations; ordinary CLI
stamping and full metadata/cache validation remain unchanged. Exact declared/
consumed inputs and private outputs stay enforced.
Issue [#238](https://github.com/laqieer/fireemblem8-expansion/issues/238)
now owns the reusable Python command closure, registry-source selection and
generated-dependency publication API in
`scripts/validation_ownership/python_commands.py`. The graph consumes that
shared seam rather than carrying its own duplicate importer/depfile adapters.
Shell continuation normalization is still graph-local integration because it
binds the sealed Make-command spellings to the shared typed registrations.
Python command registrations explicitly declare the repository root, the
adapted command's actual repository import closure, and the import directories
needed for that closure through `Command.directories`. Enumeration uses the
complete active immutable view, never a sparse code-only tree; seeing a member
name does not grant permission to read its content. Registry and asset controls
exercise both properties. Root enumeration admits gitlink namespaces only by
capturing their actual recorded pins from the repository's common-Git module
databases.

Private regular-file installation is a separate default-deny capability.
`ProbeSession._private_install_command` is a trusted-adapter seam: it issues
permission to one exact command object, input identity set and active view.
Neither a `Command` field nor `publication_policy` grants rename rights. Equal
command copies, fabricated records, foreign/restored views and expired
lifetimes do not inherit the issued identity. Each execution consumes a new
workspace-bound launch object before the supervisor receives its configuration.

The `private_install.py` protocol binds the launch scope and argv/code/source/
environment digest to a complete pinned `/work` parent chain and exact
destinations. The supervisor holds those parent descriptors from before
candidate execution through teardown; an inode-number comparison alone would
allow remove/recreate reuse. A granted command cannot create another actor.
Only a closed single-link regular temporary may move to an absent, unused
destination in the same private parent. Source/runtime paths, directory moves,
existing destinations, aliases, active file descriptors, mappings and unknown
rename flags remain denied. Relative cwd/dirfd lookups must match the actual
kernel directory identity.

Each actual kernel outcome has a scoped, sequenced `private-install:` record.
The paired consumer checks exact fields, identity/type/link count, complete
destination coverage and successful outcomes before accepting command output.
Original private-output capture still rejects extra comparison files or missing
declared results. The existing native process suite owns these controls; graph
discovery excludes that module rather than importing another `TestCase` alias.

The text adapter now uses this primitive for the unmodified
`textprocess.py`/`huffman.py` render. It selects the main/definition files and
actual transitive include grammar, not the larger `TEXT_SRC` prerequisite
inventory. The original relative script name is preserved. Both installs occur
privately, then bounded no-follow regular reads compare the complete header
against its immutable tracked input. Mismatch or failure yields no successful
C publication. The comparison file is removed and only C is a declared output.

Its `if-content-changed-preserve-mode` publication policy appends to the closed
policy set without changing old indexes or behavior. Equal content retains the
actual object; changed content retains an existing issued object's mode.
First creation uses the actual staged mode. The host independently checks the
effective confirmation against its captured initial version, not a caller's
mode claim.

Context-aware typed commands receive the actual authenticated dispatch
environment only during their own execution; direct/registration-time helpers
and existing canonical-environment adapters retain their previous behavior.
Cache identity includes the environment actually used. GNU 4.3's parse-time
`shell` environment is not guessed from final export declarations.
The same live context now carries the actual native recipe target/command
ordinal or target-less expansion kind. Real pre-read/pre-wait hooks supply
it after GNU has linked the job; it is not reconstructed from argv, directory
spelling or a source line number. The host associates records by exact
dispatch sequence rather than the order of metadata strings.

Header filesystem effects now use separate pending typed requests, not
successful `ProcessOutput` stand-ins. An identity-issued pipeline binds one
canonical `build/...headers.d` family to the actual native remake target and
ordinals: directory creation, scan publication, filter publication, temporary
retirement and final temporary transfer. Exact mkdir/rm/mv operands, command
identity, scope, active view, lifetime and issued intermediate versions must
agree. Ordinary nonremake recipe observation remains observation-only and does
not issue these effects.

The parked supervisor performs real no-follow parent creation, exact owned
single-link regular-file retirement and same-parent atomic transfer. A transfer
preserves the actual source inode, bytes, mode and mtime; only its ctime may
change. Existing immutable/foreign destinations, stale versions, missing
steps, copied commands and malformed/replayed packets reject. The tagged
`header_effects.py` confirmation reports actual directories and before/after
file identities. The host independently checks those outcomes before updating
publication ownership, the namespace mutation journal and cleanup state.
No successful effect is inferred merely from an empty stdout slot.

The original five header recipe bodies now have per-dispatch adapters. The
real selected ARM driver and cc1 execute `-MM -MG` with ordered modern flags,
macros and repository include paths; host cc/`-undef` is not a substitute.
The separate `arm_headers.py` profile captures the installed C SDK namespace
and `.h` bytes, including the actual include/alternatives alias. Unknown SDK
entries and C++ subtrees are refusals, never guessed absent files. SDK missing
lookups retain verified frontend instruction provenance; driver-only metadata
for tool/search candidates does not grant plugin contents or execution.

The real sed invocation consumes the issued scan temporary and produces its
actual redirected stream. Only the original literal basename substitution and
failure branch are supported. Shell expansion, extra expressions, in-place or
execution flags, mismatched operands and compiler search/output environment
overrides reject. Neither command text nor `Command.runtime_tool` supplies
permission: an identity-issued, single-use launch binds the actual header job,
view, workspace, complete argv/input/mount profile and environment.

The measured sed/libselinux runtime additionally needs two exact statfs paths
and two readonly proc input files. `header_runtime.py` closes that profile;
the capsule never receives a whole `/sys` or `/proc` mount or arbitrary
filesystem-query permission. Actual syscall results and kernel-delivered
stream bytes are recorded, with honest EOF status and complete sequence
accounting. Filter launches use version 2 with a fresh private HMAC key. The
guard incrementally authenticates the complete ordered occurrence transcript,
including legitimate repeated operation/path pairs, and publishes one signed
version-2 completion through the existing `accessed` channel. The profile is
still an allowlist and presence declaration: it does not require all five
paths, predict a libselinux branch, or synthesize an operation that did not
occur. Readonly-bind flags and private-namespace kernel inputs remain actual
runtime facts, not host/source namespace invariance claims. An ordinary
command, forged launch, escaped path, stream alias or unclosed input rejects.

The parent authenticates that completion only after the ordinary supervisor
schema, accounting, execution and status checks succeed. A single private
`ProbeSession` native-return registry then binds the exact launch owner, live
job, source view/epoch, completed/report objects, original status and output
bytes, and the streaming full-report fingerprint. Claim is one-use and removes
the record before returning the accepted slots-based transcript. Both output
streams must still be the exact issued immutable byte objects; equal-value
replacement is not an accepted pre-claim mutation. Header
semantics receive immutable JSON-compatible eight-key dictionary views; the
raw rows and terminal are never reparsed as authority after claim. The key and
config are promptly unlinked and references are released on every path; this
is not a memory-zeroization claim. The future GCC step receipt must extend this
same purpose-sealed seam rather than introduce another gateway.
The fingerprint schedules one child per ancestor frame; key-reference storage
and sort workspace are admitted independently. Header size calculations and
payload decoding admit their actual representations before encoding or copying.
Publication retains spent reservation authority until both attempted/accessed
insertions complete and retirement succeeds. Failure cannot refund or resume
completion. Failed/nonzero report construction sees no owned verifier;
successful signing precedes its release, and unconditional cleanup remains.
The existing native worker installs the ARM compiler and C SDK headers for
these controls without adding a job or owned command.

This is still **not** complete live-message graph support. The genuine default
text/SDK/sed component comparison keeps C initially absent, preserves the
tracked header and uses the original header rule/include guard. It does not
replace the original root's raw source wildcard, independently reconstruct
earlier Make read passes or prove a full bounded planner admission. Those
authenticated original-read-phase criteria remain held. No namespace-image
authority or general shell/rename permission is supplied by this component.

The optional `ProbeSession.make(..., observe_read_epochs=True)` observation
now closes the native entry/stream/status boundary, not that remaining phase
authority. It reads original variable sets at the actual `read_all_makefiles`
entry through the verified GNU4.3 x86-64 ABI without expanding their values.
Readonly Make instructions and real hardware-breakpoint stops identify
source-reader entry/return; the transparent observer fopen bridge ties each
source stream to its actual call site, native frame and kernel descriptor.
File builtins and eval are not source visits merely because they read the
same name. Failed includes retain their real errno and ordered search attempts;
successful files retain their pinned original bytes and object identity.

`MakeObservation.read_trace` contains ordered successful-exec/read-pass/source
events, independent original input sets and deduplicated source bytes. It is
default-off and has no namespace-invariance permission. Original inputs may
differ from final definitions, and a returned goaldep pointer is not success:
its actual status and stream must agree. Missing/forged/replayed fields,
unknown ABI/code/stack, self-signaled traps, incomplete streams/passes and
changed source objects reject. Existing bounds and the existing native owner
apply; no source hash ledger or new workflow is introduced.

Complete mutation journaling, original entry namespace images/use context and
the source walk/obligation union for every pass are still required before a
changed raw-source namespace can be certified. This trace alone must not prune
first-pass HIDDEN/default/export obligations or authorize a full-root planner
result from final values.

`observe_source_phases=True` additionally captures complete original namespace
entry images at those actual native read barriers. It implies read tracing
but does not alter the original read-only/default-off APIs. The supervisor
parks the invocation before source evaluation; the host acknowledges completed
real publications and captures the pinned namespace through the existing
metadata-neutral helpers. A separate closed barrier/acknowledgement binds
scope, exec, pass, original-input event and image digest without consuming a
producer slot or changing the native dispatch/job dictionaries.

Version2 read traces require each `entry-image` immediately after its actual
`pass-entry`. `MakeObservation.source_phases` describes those images, while
the owning session retains their real image objects and view/lifetime binding.
Copies, mutations, foreign scopes or expired views do not acquire that binding.
The initial source directory can therefore be recorded without generated C
and the post-reexec entry with it, without substituting the final namespace.
The existing invocation-wide wildcard refusal remains in place: entry images
alone do not complete the independent mutation journal or per-pass source
census and grant no changed-namespace exception.

That option also records `MakeObservation.source_effects`, a separate native
origin/dispatch/producer/publication sidecar. The actual authenticated Make
dispatch stop records its current hardware-derived original read interval
before spawning. Its supervisor-owned identity follows the real child and is
bound only at successful helper exec; actual producer requests, native job and
frame bindings, and independently checked publication confirmations must agree.
Acknowledgement at a later read barrier cannot move a first-pass effect into
that later pass. The existing native dispatch/job dictionaries are unchanged.

The sidecar preserves parse-time include/file-builtin/eval contexts, deferred
post-read uses, suppressed recipes without invented publications, all genuine
message/ARM/sed/header effects and distinct nested adoption. Missing, reordered,
forged or changed origin/request/publication data rejects. Its `closed` flag
closes only this native observation, not the independent filesystem journal:
intermediate namespace writes, unobserved host mutations and terminal cleanup
still need complete coverage before any source-phase certificate is issued.
Every-pass source reconstruction and first-pass obligation union remain held.
Default and read-trace-only observations do not select this sidecar.

The session can reconstruct an authenticated source-phase observation with
`_original_source_archive(observation)`. Its immutable records retain every
actual read pass, original input scope/raw variable row, source entry and
return flags/status, source version, open result and native goal-visit order.
Byte-identical source images may share storage, but repeated visits and changed
versions of the same pathname are never collapsed. Failed opens retain their
negative result and absent source image; file-builtin and other non-source
reads stay separate, including reads after source evaluation.

Cached access rechecks the original observation/view/lifetime and performs
no new native run. Copied, changed, foreign, expired or read-trace-only
observations cannot supply the identity binding. Raw variable values and
flags are not expanded or reinterpreted as effective special-variable
semantics. Native return flags retain their unsigned32 ABI bound.
This is a lossless source-data archive, not an include-omission permission,
namespace certificate, source interpreter or completed obligation union.
Those consumers still require the independent coverage and use-context proof.

`observe_source_journal=True` adds a default-off **fixed-original-directory**
mutation collector and implies those source observations. Independent kernel
events are captured through held original directory descriptors. Real native
publication begin/end barriers, unchanged ownership/outcome checks and separate
terminal file cleanup delimit the ordered windows. Unknown activity, changed
inputs, extra paths/events, overflow, truncation, watch loss, remapping, forged
barriers/receipts and nested Make queries reject. Directory watches remain
bound to the original view; copied or expired observations cannot borrow pins.

This deliberately refuses creation/removal of directories. A new, unwatched
subtree can hide transient mutations before a later watch is installed; adding
a watch afterward or comparing final directory bytes does not close that gap.
All output parents must already belong to the admitted original directory
image. An existing-parent fixture runs the genuine message/ARM/sed pipeline,
but the unchanged required path that needs new `build` parents remains held.
No parent is silently precreated. Kernel write/attribute coalescing is not
reported as exact syscall counts. `source_journal.closed` closes only this
fixed-directory observation, not general journal coverage or a source-phase
certificate. New-directory coverage, every-pass reconstruction/obligation
union and complete original-root small-plan acceptance remain outstanding.

For actual new parents, select
`observe_source_journal=True, source_journal_mode="prewatched-directories"`.
The native publisher creates a fresh empty directory in an unexposed private
staging parent. The host pins and watches that inode before it becomes public;
the native supervisor then installs the same inode with atomic
`RENAME_NOREPLACE`. The host requires matching staging/public move events and
the child's `MOVE_SELF` before relabeling the watch or allowing descendants.
Transients before movement and before pathname rebinding remain observable.
No existing destination or existing/nonempty/foreign directory can be adopted,
and original/public parent mappings and every lease are revalidated.

This closes the measured late-watch gap for the already-authorized parent
creation path. The unchanged missing-parent default message/header fixture
runs without an added parent witness, using real ARM/cc1/sed and all original
dispatches. Directory cleanup ownership begins only at a confirmed installed
handoff, not at a predicted future parent, including terminal-failure paths.
Created directories retain their original pins after observed removal.
The old fixed profile still refuses new parents; private regular-file
installation and the host-only CPP adapter remain unchanged. No general
directory relocation, guest permission or quota is added. Nested/general
source-use interpretation and complete every-pass obligation union still
precede any changed-namespace certificate or original-root planner claim.

The public reporter always selects `run_probe(..., source_phases=True)` through
its shared Make-authority caller. CURRENT, BASE and lifecycle authority
construction retain the same selected loader, session, budget, targets and
dispatch obligations. A missing or rejected per-pass source proof propagates
as an ownership failure; there is no public opt-out or legacy retry. The
private low-level probe default remains available for its explicitly scoped
baseline controls, not for public report authority.

The `run_probe(..., source_phases=True)` path interprets each
authenticated read pass independently and unions its source obligations.
It requires the completed issued archive, entry images and mutation journal;
every source mutation must belong to a real after-read/remake interval that
closes before the next successful exec. Unknown, parse-time, non-remake,
nested and unclosed final-pass mutation histories refuse. Successful source
versions must match immutable input or prior actual publication identity and
bytes. A failed include may be omitted only with actual original ENOENT,
entry-image absence, an intervening publication and a successful next-pass
visit.

The focused `test_reporter.PublicMakeSourceTests` checks this caller contract
without native execution, including shared CURRENT/BASE context and refusal
propagation. Those routing controls do not qualify original-root behavior or
complete-report resource fit; genuine source-phase and full-report evidence
remain independently required.

Supported initial bindings come from a complete plain global native scope,
not final definitions or an empty-program guess. Special/private/per-target/
expanding input behavior and unsupported scope/origin forms hold. Original
source-time exact include and `filter` relations reuse the bounded parser,
explicit GNU whitespace and pattern grammar. Recursive namespace aliases in
deferred recipes/exports hold unless they terminate in a proven simple source
snapshot; literal metadata reads remain body-lazy. Each pass has fresh source
state, and only constants valid and equal in every pass survive the union.
First-pass defaults remain obligations even when absent from final metadata.
The union also retains first-pass graph, recipe, export and dependency reads.
Parameterized rules retain immutable reference units and exact target/
prerequisite facts at their original caller occurrence. Repeated visits to
the same source site remain separate occurrences. Earlier `TABLES`, macro
bodies and selected input names cannot come from a terminal Make query,
even when the terminal graph is stable. Missing original facts refuse;
only a proven empty parameter list may legitimately emit no rules.
Local-binder analysis uses the original source-unit role. In a proven active
ordinary recipe, including an inline recipe segment, each `$$` pair is literal
dollar data for that Make expansion. This supports shell/awk/printf data and
regex end anchors without tool, target, variable or pathname exceptions.
Strict checking of active dollar references and incomplete expressions remains
enabled separately; this is not a global non-staged scan.

Rule headers, assignments, define bodies and unproved source roles retain the
conservative staged context. An active nested `eval`, opaque `call` or `guile`
keeps that entire expression conservative because another expansion can
activate escaped operations. Genuine `foreach` binders inside shell quotes or
hash-containing recipe data still count. Both whole-source read constants and
per-pass namespace snapshots use this same classification, including later
active unselected source obligations. Existing snapshot uniqueness, scope,
flavor, version and exact-reference checks are unchanged; no new temporal
snapshot or terminal-value permission is granted.

Repeated source visits retain their order and bytes; same-file or Make-looking
`file` data reads do not supply source evaluations. Within-pass replacement
remains unsupported even when both versions were successfully captured.
An issued entry image does not implement variable-universe semantics.
Every pass checks the original executed expression/body closure, including
computed aliases, before admission. Direct, braced, substitution and invoked
`.VARIABLES` reads, universe conditionals and actual universe exports remain
unsupported even when no wildcard was evaluated. Unexecuted bodies and
literal `origin`/`flavor`/`value` reads stay body-lazy; no final definitions
seed this check.
Ordinary, short, scoped and nested references share the dollar-parity
scanner. The active third/fifth dollar after escaped pairs still reads its
binding, including an alias of an unsupported universe or unsealed input.
Even-dollar literal controls stay inert; shell quoting does not suppress
Make expansion. Literal-binding-module consumer checks use the same closure.
Actually executed original command-line/environment bodies also contribute
their effective, source-context read forms and dependency edges. They do not
need a source-file declaration and are not reclassified as source definitions.
An earlier effective override replaces the body; a later override cannot erase
an earlier read. Literal metadata, explicitly unexported inputs and proven
unexecuted `and`/`or`/`if` operands remain lazy. Native Make recipe exports
include initial-only names, independently of whether the interceptor later
executes or suppresses the command. GNU can already have expanded a source
or command-line body before a recipe projection is suppressed.
Export expansion follows the effective original binding: simple values are
data, and GNU4.3 transports recursive environment-origin values raw, including
explicit export without redefinition. Recursive source/command-line/override
bindings expand. A nonempty append or source redefinition can change the
origin; an empty append or skipped conditional default does not. Explicit
Make reads of an environment-origin body still expand it normally. Raw output
equality and command kind are never proxies for these binding semantics.
The source walk also models the native-proven empty file-origin simple
binding created by a named export/unexport of an undefined variable.
Deferred recipes are checked
separately, with existing automatic-variable spellings treated as local
context rather than global original bindings; unknown automatic values never
seed pruning.
Literal `filter` operands and an unchanged proven invocation value are now
exact original facts, not unknown contexts. Their positive evidence compares
actual native values and both first/next-line POSIX folds. An unproved pattern
input remains unknown even when the native run happens to see it undefined;
reassigned, emitted, computed or undefined control facts still cannot be
refreshed from final values.

Invocation-derived facts account for both complete definitions and authentic
forced/supplied input names before they are seeded, without confusing
environment presence with command-line write precedence. A requested goal is
not evidence for a forced `MAKECMDGOALS` value. The existing supplied-control hold applies to all
such invocation facts, while ordinary unforced facts remain available.
Required unknown target/private/local execution contexts still refuse instead
of borrowing a global input value.

The [required scoped-source capability](https://github.com/laqieer/fireemblem8-expansion/issues/180#issuecomment-5723100448)
extends that original-source model for asset-compression overrides and per-file
compiler options. Proved exact target lists and narrowly supported literal
single-percent scopes have typed, source-ordered bindings; they are not writes
to the global definition map. Scoped immediate RHS reads use their actual
source-point context. A later global/native value cannot replace the earlier
`CC1 := $(CC1_OLD)` result.

Target-specific `+=` keeps its raw recursive tail and applicable inherited
base, even when the global base is simple. A variable RHS remains deferred,
while an append to an existing local simple value retains immediate timing.
Empty inherited tails preserve GNU's separator after a nonempty base:
`FLAGS := early; unit.o: FLAGS +=; FLAGS := late` produces the actual argument
`late `, although global raw metadata is `late` and target raw metadata is an
empty file/recursive value. These three facts are deliberately distinct.
No trimming or terminal-value bootstrap supplies the proof.

The source walk retains original rule/target/prerequisite and recipe-ordinal
contexts. A required scoped use is bound to its original target/stem and
source-derived ancestry. Actual native raw recipes and expanded job arguments
corroborate that source proof; a failed association remains a refusal even if
another target uses the same recipe successfully. Direct argv and shell
dispatch are compared through their existing typed/lexical boundaries.
Source-only obligations before a remake are marked separately from native jobs.
Earlier-pass defaults and unselected active declarations remain obligations.
Owned context identity, view/lifetime checks and the existing cache,
observation-count,512-name and deadline bounds apply throughout.

Unknown or computed/escaped selectors, overlapping scopes, unqualified
parent/shared-child inheritance, private/override combinations,
automatic-sensitive assignment contexts and unproved consumer/stem relations
remain held. This does not add a general Make or shell evaluator. The genuine
small LZ witness keeps its original copied rules and three explicitly
pre-existing raw operands; its actual conversion projections do not certify
asset production. Native raw global/per-file metadata is reported separately
from expanded target-local job arguments. Legacy ordinary Make with `-rR`
selects a different CPP prefix from the unchanged native built-in context;
only the measured CC1/flag portions are compared as equivalent.

Deferred namespace refusals retain their existing `MakeProbeError` type and
message, with optional bounded `source_attribution` data for an in-process
diagnostic consumer. The record identifies the original pass/exec, known
source site/visit/rule/ordinal, the failing selector/wildcard/dependency
condition, a carrier path and relevant snapshot decisions. It records
origin/flavor/version/fact kinds, unsafe or unknown writers and already
computed read forms without recovering a value again. A repeated or missing
visit, assignment site or native-use association remains explicitly
unavailable; a source obligation is not relabeled as an executed job.

The formatter walks existing dependency facts only. It does not re-enter the
Make evaluator, inspect arbitrary frame locals, dump original/generated
streams or create a proof. Each queued name has one predecessor link; only
the selected carrier path is reconstructed. Shared prefixes are not copied
into queued children. Existing input extents and count/file/deadline bounds
admit the linear workspace against the existing cache allowance before it
grows. A data-only projection measures JSON bytes and conservative container
storage without constructing the full record; that extent is charged before
the record's containers are built. No allowance is increased or refunded.

Transient source-decision notes borrow computed source objects and check
count/storage extents before growth. If they cannot fit, an otherwise
accepted source decision still succeeds without spending diagnostic cache.
All notes are cleared on exit. A refused source decision instead reports
attribution unavailability explicitly; it never receives a partial record.

If collection, construction or accounting fails, the original source refusal
stays primary. `source_attribution_unavailable` and
`source_attribution_failure_type`, a bounded note and a sanitized secondary
`MakeProbeError` retain the stage/type. Failed diagnostic frames are cleared,
and raw secondary exceptions are not attached: their tracebacks could retain
unadmitted queues or partial records. Original caller/source context remains
on the primary exception. This does not repair the separate harness formatter
or authorize it to publish custom attributes.

This is observability, not temporal-snapshot admission. The faithful small
original C-source/object/header snapshot family already accepts both passes.
A disclosed recursive-late consumer instead produces an attributable
namespace refusal. Neither result recovers ROOT18's unrecorded historical
reader or proves whole-root/report acceptance. Header-bound facts remain
distinct from exact values; no variable-name exemption or snapshot waiver
is added.

Publication is separate: the ordinary reporter prints the unchanged primary
error string, not the attached data. The current diagnostic harness's
`policy.error_record` preserves exception chains and code-frame identities,
not custom attributes or notes; its worker adds counters and native-failure
information. Consequently the new details do not automatically appear in
that harness's artifacts. Any bounded publication routing needs separate
owner acceptance; this capability does not change the harness or allocate
another root/H1 run.

The ordinary invariant/default planner path remains available. This opt-in
source path does not relax any live command or resource boundary. The genuine
original root retains its header order-only toolchain check, original recipe
and absence conditions. Source correctness precedes the separately frozen
contained resource scope; success under the known insufficient old cap is not
a prerequisite to its own sizing. Successful small source-phase controls and
component interpretation are not full original-root or whole-report evidence.

The [5710465329 terminal-file correction](https://github.com/laqieer/fireemblem8-expansion/issues/180#issuecomment-5710465329)
separates planned output reservations from cleanup ownership. The native
publisher hands the parent a real, independently opened read-only descriptor
after exclusive file creation and before writing/final acknowledgement.
The existing authenticated private channel carries exactly one `SCM_RIGHTS`
descriptor with its scope, producer, path, owner and actual identities.
The receiver owns that pin before byte accounting can fail. Pins and claim
resources spend existing admission/byte bounds; no guest descriptor or
filesystem permission is added. The independent open-file description does
not delay the writer's real `CLOSE_WRITE` event.

Delivering a producer `result` is not a Make-resumption signal: the native
publisher may still need the driver to receive `file-opened` and send the
bound `file-pinned` acknowledgement. A late-reply fixture must return to that
real driver before waiting for native continuation. The single-output control
binds the acknowledgement to the original result's scope, producer/sequence,
owner and exact output path, then retains its real Make marker and late-message
rejections; it does not forward descriptors or synthesize a marker.

Cleanup requires owned native quiescence and the actual live file/parent pins.
It atomically claims the public entry into a private report-owned slot using
`RENAME_NOREPLACE`, checks the claimed object against the file pin, then removes
only that object. A changed entry is restored without overwrite, or retained
in the recorded claim location if another public occupant prevents restoration.
The healthy journal records the real namespace move and pinned removal, not
a fabricated `DELETE`. A failed or incomplete journal supplies no phase
authority but does not prevent safe cleanup of genuinely owned files.
Retirement, transfer, nested scopes and selected views preserve that ownership.

Foreign, displaced, unacknowledged or otherwise uncertain objects are not
deleted by a later blanket report/view cleanup. The error and session retain
the ownership state, source view and live pins. After inspecting or handling
those retained objects, an outer owner may call
`session.release_retained_file_handles()` to close the handles; this does not
delete retained paths or reactivate automatic removal. The claim namespace is
trusted private coordinator storage, not a new same-UID filesystem sandbox.
If a caller catches the original native failure inside the session, outer
context exit can still raise a distinct retained-report error. Restoring a
pathname or its original inode does not erase already recorded lifetime
uncertainty. Tests require execution closure separately from strict full
cleanup, inspect retained objects first, and use independent fixture teardown
only after explicit handle release.
The [existing tester case](test-cases/workflow-governance.md#terminal-generated-file-cleanup-ownership)
contains the real native preimages, race/mutation controls and explicit outer
fixture cleanup. Injected terminal failure is not quota calibration, H1 or
source-phase acceptance.

Registry declaration execution now has an explicit shared-session entry:
`graph_registry.observe_declarations(loader, session)`. The selected loader
must be the active public view and own the same budget. It invokes the real
candidate `REGISTRY` through a confined `Command`; the reporter then validates
the declaration fields and their captured source paths. A foreign view, stale
source or invalid schema identity rejects rather than becoming an empty registry.
Directory-backed primary inputs use the schema's metadata-only `source_paths`
selector, shared with its ordinary loader. File-backed primary inputs still run
the existing strict `load_records` consumer, so declared, reported and consumed
paths must agree there too. Discovery grants no directory-wide member contents,
and matching bundle additions/deletions follow the selected CURRENT/BASE view;
a nonmatching member is not classified as generated merely because it shares
the directory.
Modern link-library directory shells preserve the selected compiler's complete
optional `-B`, architecture and metadata-query argv. The root-owned system
`arm-none-eabi-gcc` image and required library aliases are captured, revalidated
and executed through the existing compiler confinement; the resulting stdout
is reduced with ordinary shell `dirname` semantics before Make consumes it.
The runtime receipt participates in the dynamic-command observation. A failed
required query rejects with its real nonzero status instead of becoming a
successful empty producer. Unsupported compiler modes, compiler names or
binutils roots reject before execution.

The build framework still supports checkout-local toolchain roots for ordinary
modern builds. The ownership reporter does not execute a checkout-local
filename match: those roots currently have no trusted installed-tool identity
contract equivalent to the root-owned system package capture. Selecting one
for an ownership-observed directory query therefore returns that precise
compatibility error rather than granting candidate execution authority.
The system compiler capture itself is optional at session setup: a host owner
without the ARM package retains the genuine absent `/bin`/`/usr/bin` alias
observation and can run non-toolchain graph controls. A directory query still
requires the captured compiler and fails rather than fabricating coverage.

Deleted-path resolution requires the selected BASE ownership model, not just
BASE's filename inventory. It uses that model's graph rules, generated-source
classification and evidence owners. CURRENT still enforces semantic admission;
providing BASE context cannot admit a newly tracked CURRENT file by prefix.
The report-level orchestration must supply this model from its grouped BASE
view before deletion explanations can be accepted.
The asset integration control captures the real three-record manifest as BASE,
then removes the battle record and one of its tracked inputs from CURRENT.
Both generated includes reach actual GNU Make and each restarts once. A single
public `select_view` block restores BASE's real deleted source and battle
consumer ID, without resetting the report budget or changing source paths.
That source/output test is not a substitute for the reporter's BASE ownership
model, full 112 domains, oracle, lifecycle or independently captured public gate.

The scanner adapter similarly reuses `SourceFile::GetIncludes` and
`ScanIncDependencies` from `tools/scaninc`. The ordinary CLI still uses real
file availability and its original include-search order. The graph's native
driver discovers direct includes with the actual parser, resolves availability
from the same immutable capture, then runs that same dependency traversal with
the exact admitted source closure. This avoids asking a confined process to
open undeclared absent candidates such as
`include/asm/macros/music_voice.inc`; it does not copy the scanner parser or
invent dependency output. Missing initial inputs, escaping includes and
symlink/gitlink matches fail. Native command consumption must equal its
declaration, and the resulting dependency text must reach actual GNU Make.
The four scanner C++ sources, four headers, native wrapper and scanner Makefile are trusted code,
not candidate dependency data. Before creating `ProbeSession`, the verifier
requires their candidate regular Git blobs and modes to match the independently
selected `source_sha`. Compilation admits only that explicit closure, never
extra files selected by a candidate directory listing. Intentional scanner
evolution requires a reviewed source revision containing the approved change;
ordinary candidate source data and the independently approved BASE view remain
separate. This is a narrow compiler-code authority boundary, not a whole-tree
identity gate or a committed content-hash ledger.

Verifier-source comparisons use `AuthorityLoader.read_blobs(paths, label)`,
which requires the loader's actual immutable repository/revision capture and
original report budget. Each verification stage reads only its selected regular
blobs, grouped by recorded Git object database through Snapshot's shared batch
parser. It does not create a partial execution snapshot, materialize unrelated
repository assets, or retain a cache between stages, reports, or live reads.
Every selected source and loaded-module file still receives its regularity,
location, authority and byte comparison; candidate mode/object/path changes
still reject before scanner-session entry.

Direct batch frames and copied payloads spend `output` bytes; execution
Snapshot frames and copied payloads spend `snapshot` bytes exactly once each.
Requests and protected-launch arguments spend `pending` bytes, and worktree
comparisons retain their `control` charges. The process stream is bounded by
its existing aggregate category, not by treating several blobs as one file.
Each parsed blob independently retains `file_bytes`; malformed, missing,
truncated, wrong-object/type and trailing responses, Git failures, exhausted
budgets and cleanup failures remain errors. No limit, deadline, lifecycle
wrapper, native policy, graph meaning or CI timeout changes. The focused
source-only procedure is part of
[`TC-WORKFLOW-GATE-OWNERSHIP-001`](test-cases/workflow-governance.md#tc-workflow-gate-ownership-001-resolve-every-admitted-path-to-complete-validation-ownership);
process-launch counts, not wall-clock thresholds, establish the correction.

The named scanner-build contract admits literal `=`/`:=` assignments, the
four-source/four-header `g++` profile and the existing scaninc/clean recipes.
Executable text permits only ASCII space, tab and LF grammar; NUL, CR,
non-ASCII separators and non-Make controls reject, while printable Unicode is
inert only after an ASCII `#`. Parsing precedes Make evaluation, so extra
flags, sources, functions or recipes cannot execute. Assignment order,
equivalent braces and safe comments retain ordinary behavior.
Include names are resolved only after joining each original search directory,
so repository-contained parent components in real banim sources remain valid.
The planner checks every intermediate component against the captured namespace
before collapsing `..`; an absent or non-directory prefix cannot become a
different existing file. Canonical paths bind source admission, while the
original accepted search spellings are passed to the native traversal and
retained in its ordinary output. Alias traversal remains bounded, and escaping
or nonregular namespaces still reject. The line-framed include helper rejects
embedded line breaks or NUL rather than splitting one pathname into claims.
Ordinary-versus-adapted comparisons use equivalent captured input metadata, not
an ambient worktree containing untracked files. Native adapter unit tests and
standalone producer successes are not whole-root Make acceptance.
Registered `find ... -type f -name ...` discovery keeps the real depth-first
directory traversal and captured source equality. The sealed text command
uses implicit print, while the asset-source command has an explicit final
`-print`; both complete grammars remain supported without extra arguments or
active operators. Discovery issues bounded Linux
`getdents64` requests directly instead of inheriting Python `scandir`'s 32 KiB
readdir buffer. Each request is 4096 bytes; the supervisor still records and
accounts for the complete requested before/after buffers, offsets, results and
EOF calls. Regular-file filtering does not follow symlinks, and `DT_UNKNOWN`
entries use an exact no-follow metadata fallback. Nested, Unicode,
nonmatching, multi-batch, missing and nonregular controls compare against the
ordinary `find` behavior. The small-directory traffic proof requires at least
a 50 percent real control/metadata reduction under unchanged production
limits; it is not a claim that the complete ownership report now fits.
The host C dependency adapter preserves the original ordered `cc -E -MM`
include/define arguments and the declared `.dep` destination through
`Command(dependency_only=True)`. An explicit captured header-code pool discovers
the real compiler closure; unused headers do not become dependency provenance.
The actual dependency file must reach GNU Make, with its original prerequisite
order and genuine restart. This uses neither an ARM/agbcc executable nor a
copied preprocessor, fake `.d` file or re-exec from a Python command capsule.
The root Make dependency query invokes the existing `$(PYTHON)` interpreter
explicitly rather than relying on an executable script's shebang. This keeps
ordinary output unchanged and lets the registered command reach Make without
granting execution on the captured source mount. The full linker itself is
not executed by the dependency adapter; its real `-m` CLI reads the linker
script and returns the prerequisite list.

## Authoritative files and public commands

- [`.github/validation-ownership-graph.json`](../.github/validation-ownership-graph.json)
  contains typed surfaces, evidence authorities, edges, path rules, named
  exclusions, and authoritative lifecycle events.
- [`scripts/validation_ownership/graph.schema.json`](../scripts/validation_ownership/graph.schema.json)
  is the closed JSON Schema. The stdlib reporter also applies semantic
  invariants that a schema alone cannot express.
- [`scripts/validation_ownership/probe-oracle.json`](../scripts/validation_ownership/probe-oracle.json)
  is the independent sealed probe oracle. Expected surfaces and edge families
  never come from the graph being measured.
- [`.github/validation-ownership-make-dynamics.json`](../.github/validation-ownership-make-dynamics.json)
  is the sealed allowlist for reachable shell-derived Make dependencies. Each
  expression binds tracked tools, input files/variables, automatic inputs,
  optional nonexecuting resolved values, and exact evidence owners. Its
  expressions and command patterns are confined execution-admission contracts,
  not a second inventory of validation gates. Authoritative GNU Make emits the
  actual expanded command; exactly one sealed pattern must select a typed
  adapter, which then validates its argv and source closure before execution.
  Merely observing an arbitrary candidate command cannot grant it execution
  authority, and this registry never selects, skips, or narrows a validation
  gate.
- The `scripts/assets/` implementation package retains asset generation, drift,
  compilation and linked-consumer owners, separately from authored A/V
  judgments. Its `tests/` namespace remains host-test code, matching the
  existing `ASSET_TOOL_INPUTS` exclusion. `assets.mk` additionally preserves all
  prior configuration/default-disabled/profile/boot owners while retaining
  the asset pipeline relationships; this is not a weaker generic host mapping.
- [`scripts/validation_ownership/reporter.py`](../scripts/validation_ownership/reporter.py)
  enumerates tracked paths through trusted Git, resolves live authorities,
  emits canonical JSON, and verifies that execution did not change Git state.
- [`scripts/validation_ownership/isolated_launcher.py`](../scripts/validation_ownership/isolated_launcher.py)
  retains the foundation consumer's no-mode/options entry and admits `check`,
  `resolve`, `tests`, and the closed lifecycle-check graph modes after isolated
  no-site Python startup. It removes ambient `GIT_*`, Make preload/flag/override,
  and shell-startup controls before entering its payload.
  Report options are parsed once. The controlled non-symlink repository root
  is resolved before changing directory, and the same normalized namespace is
  passed to the reporter. Relative roots, equals forms and accepted
  abbreviations cannot acquire a different meaning through a second parse.
  The BASE-staging guard uses the existing parsed workflow structure to select
  one verifier in `host-tests` and compare its full step and job context.
  An inert or duplicate textual copy cannot stand in for that executed step;
  independent coordinator invocation remains a separate mandatory boundary.

Validate whole-repository coverage without selecting or running any owner:

```bash
/usr/bin/python3 -I -S -B scripts/validation_ownership/isolated_launcher.py \
  check --repository-root "$PWD"
```

Explain one or more changed or deleted paths:

```bash
/usr/bin/python3 -I -S -B scripts/validation_ownership/isolated_launcher.py \
  resolve --repository-root "$PWD" \
  --changed src/bm.c \
  --changed src/data/items.json
```

Add `--base-revision <revision>` to derive whether authoritative graph-edge
changes invalidate review evidence. Output is recursively sorted canonical
ASCII JSON with one trailing newline.

For a trusted Make invocation, `make validation-ownership-check` remains a
convenience alias and must be the sole goal. A mixed invocation
such as `make validation-ownership-check compare` fails before NODEP or
generated-include suppression can affect `compare`.
It is not a pre-evaluation boundary: GNU Make processes ambient `MAKEFILES`
and command-line `--eval` before reading the root Makefile. Use the standalone
entry above for untrusted evaluation; do not prepend a Make invocation.

The host Build setup installs `build-essential`, `libmgba-dev`, `libpng-dev`,
`python3-venv` and `pkg-config`, plus `binutils-arm-none-eabi`,
`gcc-arm-none-eabi` and `libnewlib-arm-none-eabi` for the real sealed modern
library-directory queries. The ownership consumer also compiles native
`gbagfx` against `png.h`, libpng and zlib, so the ARM compiler alone is
insufficient; `libpng-dev` supplies its development dependency closure on the
supported Ubuntu host, and its existing Makefile queries libpng through
`pkg-config`. Installing the query compiler does not opt the explicit
`GBA_PLAYTEST_HOST_ONLY=1` suite into full modern object builds. The concurrent
custom-spell profile build uses the existing live-artifact class guard and
registry; its configuration/host checks still run, and normal-mode build
workload is unchanged. Its sole normal-mode CI owner is now the existing
`build` worker's required profile-isolation step. The strict single-test entry
rejects missing compilers, host-only skips and empty selection. Both complete
`expansion-modern-all` builds remain concurrent in separate enabled/disabled
roots; they produce relocatable objects, not a ROM or final ELF. This restores
coverage rather than moving the workload back into the near-limit host job.
Use the existing
[pinned host Python environment](workflow-pilot.md) for local host tests.
Both `user-namespace` and the supported `sudo-drop` launcher are valid
observations. Missing native dependencies or unavailable confinement remain
errors; a platform-specific mode assertion must not turn a supported fallback
into a failure.

The profile test module, `tools/gba-playtest/tests/host_mode.py` and its reused
`scripts/workflow_pilot/raw_diff_check.py` process primitives have exact
selectors in `surface.custom-spell-profile-tests`. They retain their host
positive/adversarial owners and add `owner.compile-custom-spell-profiles`,
bound to the actual required `build` step. Independent oracle probes require
all three pairs; unrelated host paths do not acquire this compile owner.
The local mirror contains 34 gates, including every previous gate. Nine jobs,
the protected three contexts, publisher build-once behavior and existing
90/60-minute bounds remain unchanged. This compile evidence is not H1 or a
whole-graph/runtime acceptance substitute.

The [5692844782 master integration](https://github.com/laqieer/fireemblem8-expansion/issues/180#issuecomment-5692844782)
retains the independent [atomic text publisher](text_generation.md) and its
single explicit `extended-host-tests` command. Exact implementation and test
selectors resolve to `surface.text-publication`, with positive evidence from
`owner.host-text-publication` and adversarial workflow evidence from
`owner.host-workflow`. The generic host suite does not execute this new module.
Actual unittest collection, parsed job/command ownership and two independent
oracle probes bind that scope without broadening other texttools paths.
The new documentation has its own exact documentation-governance admission.
Removing exact admission or redirecting either owner rejects; all previous
oracle probes and both parent command inventories remain.

The native source-closure control imports the actual captured standalone
producer and sibling `huffman`, then exercises its real staging context
(including `secrets`, `stat` and `contextlib`) at the existing default budget.
Omitting the sibling code must reject rather than read a live-checkout copy.
This is not full publication inside a native capsule: the unchanged native
guard rejects the producer's `os.replace`, as an explicit separate negative
control. Ordinary text publication and concurrent object builds execute in
their existing host/build owners; this integration grants no new native
rename authority or quota.

The root-boundary correction in
[5686491976](https://github.com/laqieer/fireemblem8-expansion/issues/180#issuecomment-5686491976)
keeps recursive profile cleanup bound to its actual directory pin through a
verified no-overwrite cleanup claim and FD-relative traversal, not a checked
then re-resolved pathname. Real acquisition/final-close SIGINT and close-error
fixtures inspect live descriptors and retained identity state. Namespace
drift remains failure with unrelated replacement/displaced content preserved.
These bounded controls retain the existing workload, process ownership,
shared helper source, PID census classification and all measurement limits.

The subsequent [5687542201 publication correction](https://github.com/laqieer/fireemblem8-expansion/issues/180#issuecomment-5687542201)
establishes the root pin privately before publishing the public name with
no overwrite. It reuses the same private holder for verified cleanup, opens
captures relative to the known root pin, and refuses intervening public
entries before workload entry. Private initialization/publication failures
retain all acquired ownership and the primary exception; the old public
create/open sequence is an explicit wrong-inode/false-success control.
The private namespace is trusted, not an interpreter/same-user sandbox.

The profile runner's [owned-process follow-through](https://github.com/laqieer/fireemblem8-expansion/issues/180#issuecomment-5683697584)
uses separate regular capture files, one shared 600-second work deadline and
the existing raw-diff tool's pidfd/subreaper quiescence primitives. It does not
inherit that tool's unrelated 4 MiB cap. Partial launch, timeout and interruption
settle complete owned sessions before captures and artifacts are removed;
unverified cleanup retains the pinned identities and exact root. Bounded
Python-child regressions prove post-output overlap and dependency completion,
not merely two PIDs or a successful historical Make run. The actual original
full-object workload and its single required entry remain unchanged.

The host-only stale-artifact fixture stages the actual
`scripts.workflow_pilot` package init and `raw_diff_check` module alongside
its existing dependencies. Removing that staged helper must fail import;
restoring it restores all host-only skips without touching the staged
artifacts. This is real dependency closure, not a live-root fallback.
The new OS `pid` test symbol remains visible to the extensible-ID census and
has one explicit reviewed-exclusion explaining its pidfd/process role.
Renaming it to evade scanning or weakening the census is not a repair.
Classification changes also require the canonical
`python3 -m scripts.generated_data.idspace generate` output update and its
separate check. The OS-PID exclusion changes only audit/census metadata;
default caps, record counts and the generated C ABI header remain unchanged.

## Typed contract

Surface nodes use the closed types `source`, `schema`, `configuration`,
`generated`, and `manual`. Evidence nodes use `host`, `compile`, `link`,
`runtime`, and `manual`. An evidence authority references only:

- a statically discovered Make target;
- a parsed Build workflow job or named step;
- a stable case in `docs/test-cases/registry.json`;
- the typed `scripts.generated_data.registry:REGISTRY`; or
- `.github/manual-testing-handoff.json`.

The graph stores identities, not copied commands. A single root-confined loader
requires every graph, schema, probe, tester-case, manual, generated-data, Make,
and workflow authority to be a Git-tracked regular blob. Recursive literal
Make includes pass through that loader. Workflow jobs and steps remain bound
to the existing strict Build workflow parser. Name scalars share its closed,
dependency-free YAML decoder: plain-scalar comments are not name data,
single-quoted apostrophes and supported double-quoted escapes are decoded,
and ambiguous, multiline, tagged or aliased names reject. Equivalent name
quoting and trailing YAML comments preserve authority; run-script bytes are
not normalized as YAML name data. The five graph-owned issue-number labels
are quoted so their declared identities exist in parsed Build YAML.
Original workflow, job, step and scalar text must pass the shared raw YAML
character check before line splitting, comment removal or name decoding.
Forbidden controls cannot disappear inside comments; supported printable
Unicode and ordinary comment data remain valid. The mandatory topology guard
also resolves coupled consumer names through the shared decoded step fields,
while retaining exact environment checks and consumer ordering.
Generated paths and owners come
from registered table schemas. Symlinks, escapes, untracked includes,
non-blob modes, target removal, registry drift, and workflow structural drift
therefore fail without a second command or filename-derived owner registry.

Make authority fingerprints contain normalized target declarations, exact
prerequisite order, ordered recipes, target/global assignment operator and
flavor, ordered repeated assignments, conditional context, and transitively
referenced variable definitions. Comments, nonsemantic spacing, and unrelated
`.mk` targets do not invalidate another target. First-prerequisite swaps,
assignment reordering, operator changes, and false/different conditional
wrapping do. Workflow fingerprints are job/step-specific normalized
structures. Review invalidation reports only edge IDs whose endpoint, type,
owner, target authority, path mapping, or referenced target/job semantics
changed. A semantic change to the schema or the independently sealed oracle
invalidates every current edge, even when the graph declarations and owner
fingerprints remain unchanged. Adding, removing, or semantically changing a
fail-closed exclusion likewise invalidates every existing edge: exclusion
authority is part of whole-tree admission even when no oracle probe names the
new exclusion. Exclusion-list, selector-list, JSON whitespace, and object-key
reordering with equal parsed semantics do not invalidate review.
Path-rule `include` and `exclude` arrays are unordered any-match collections
and are canonicalized only for declaration comparison. Changing selector
membership still invalidates the affected surface's owners and exact-base
oracle authority. Recipe, prerequisite, workflow-step and disposition-history
order retain their existing meaning.
The artifact's ownership/consumer/consistency/disposition record and lifecycle
event authority are also part of this comparison. A valid change to either
invalidates all existing owner edges, even if edge declarations themselves
are unchanged. Event-set and object-key ordering are nonsemantic; artifact
history ordering and every authoritative record field remain significant.

The closed edge families are:

| Edge | Required meaning |
| --- | --- |
| `owns-test` | Changed path to its existing positive host evidence. |
| `adversarial-control` | Malformed/boundary fixture owner. |
| `compile-owner` / `link-owner` | Existing compile and link properties. |
| `target-scenario` | Runtime or ABI surface to a real target scenario. |
| `generated-by` | Existing generator target. |
| `drift-check` | Existing generation/schema/round-trip drift target. |
| `generated-consumer` | Existing compiled or linked consumer. |
| `dependent-profile` | Shared contract to dependent supported profiles. |
| `negative-control` | Shared contract to default/disabled control. |
| `manual-handoff` | Supplementary visual/audio/UX judgment route. |
| `depends-on` | Typed surface dependency; cycles fail closed. |

Each surface declares applicable requirements. The reporter converts those
requirements to exact required edge families, rejects missing or inapplicable
edges, and rejects more than one owner for a family. Every evidence identity
is unique. Unknown nodes, keys, enum values, selectors, edge types, paths, and
targets fail closed.

## Maintainable path coverage

Coverage uses exact `HEAD` and optional base tree entries, including Git mode,
object type, and identity. Prefixes describe the initially admitted cohort,
not permission to classify every future filename. That cohort is derived from
the unique graph-introduction tree in the selected Git history; no path list,
commit pin, blob list or content-hash ledger is stored beside Git. Missing or
ambiguous introduction history fails closed.

A later tracked path needs an exact selector in the existing ownership graph,
actual generated-source registry membership, or the canonical verifier-runtime
source registry. Thus a newly tracked `src/foo.c` fails despite `src/`, and
renaming or rearranging the prefix rule does not admit it. An exact selector
records the semantic owner choice and still requires the surface's complete
positive/adversarial/build/runtime roles. Known post-introduction framework
components have explicit entries in their existing semantic rules, not a
second inventory registry.

Unit-test discovery is a separate boundary. The literal-binding test module
`scripts/validation_ownership/tests/test_literal_bindings.py` has an exact
`paths.ownership` declaration, retaining `surface.ownership` and the same owner set as
the Make-probe and graph-command test modules. Its eight discoverable cases do
not themselves grant graph admission. Removing only that declaration leaves
the generic prefix match but rejects admission, as does a neighboring
unregistered test path. This is ordinary exact ownership, not a verifier
bootstrap/runtime allowlist entry or a change to the Git-derived cohort.

The ownership implementation, support files and collected regression modules
resolve through that dedicated surface to `owner.validation-suite` and
`owner.validation-check` in `ownership-tests`, replacing their outdated generic
host pair and preserving one owner per evidence family. Moving the suite's job without moving
these path-to-evidence edges is an incomplete report even when full CI still
requires the worker. The five native regression modules excluded by the
ownership package's real `load_tests` remain separate under their existing
host mapping; the new rule does not claim those bodies run in ownership-tests.
General host tooling likewise does not acquire ownership-suite evidence.
Every existing host CI job and command remains mandatory and unchanged.

Focused controls collect actual test modules without executing their bodies,
resolve every ownership-namespace path, and bind the two execution owners to
the existing dependency-free workflow parser's normalized launcher and mode.
They require no site-installed YAML package under `-I -S -B`. The host-owned
discovery partition control invokes the actual isolated launcher in a child
process, collecting rather than executing its suite. Import errors fail, and
the selected IDs must equal all ordinary module cases minus the separately
owned native suite without duplicates. Installing a site package or weakening
`-S` cannot repair that contract. Independent oracle
probes cover an implementation, a collected regression and a nonrelocated
native control. Restoring host-only mapping, removing either execution edge,
or redirecting it to a live host-only owner rejects. Reordering equivalent
rules, nodes and edges remains valid. Existing exact-selector removal and
unregistered-neighbor controls still reject; the namespace prefix is not new
semantic admission.

Includes and explicit excludes still form a partition: zero matches are
unknown, multiple rule/exclusion matches are ambiguous, and a prefix-only
new path lacks semantic admission. All three are errors. Admission is checked
for whole-tree coverage before Make-authority execution and again when
resolving a current path; successful resolutions report the admission kind.
Every exact path-rule selector, in either the include or exclude role, and
every exact top-level exclusion selector must
name a current captured-tree member or an explicitly admitted generated-source
member. Deleting that member without removing the selector is a stale ownership
target and rejects even when no oracle probe names the path. Prefix selectors
are namespaces and need not currently match. Removing the stale exact selector
is the repair; deleted-path reporting still resolves through the separately
validated BASE graph/model and reports `selected-base-tree`.

This avoids a hand-maintained list of more than ten thousand files while
keeping overlap and unknown namespaces deterministic. Mode `120000` symlinks
always reject. The `mgfembp` mode `160000` gitlink is
a named fail-closed exclusion because nested ownership and provenance cannot
be inferred from the parent path. Resolving it as a change is an error, not an
empty success; any synthetic gitlink under an owned prefix also rejects.
Changed paths must exist in `HEAD` or the selected base, and a path whose mode
changes between them rejects. Untracked, ignored, and nonexistent paths never
inherit ownership from a matching prefix.

GitHub metadata is partitioned semantically: workflows, governance/schema,
repository host configuration, issue templates, the pull-request template,
and manual handoff are distinct surfaces. `.github/workflows/build.yml`
resolves to the workflow contract step, issue templates to their workflow
tests, and `.github/PULL_REQUEST_TEMPLATE.md` to documentation governance.
`.github/CODEOWNERS` has no deterministic repository consumer, so it is a
named fail-closed external-GitHub-enforcement exclusion rather than a circular
ownership-test claim.

The nine-job Build retains both ownership gates in `ownership-tests` and the complete mirrored
local gate inventory. Patch packaging is a master-only step in `build`, not a separate
publisher job or local gate. The existing `surface.host` mapping covers
`scripts/modernize/package_ci_patch.sh` and
`tests/workflows/test_patch_release_workflow.py`: the host job runs the
workflow suite's real packaging/producer checks, including invalid-input,
wrong-context and cleanup-failure controls. Both paths have independent
oracle probes; no new routing surface or copied command list is introduced.
The removed `scripts/workflow_pilot/publisher_shell_contract.py` cannot resolve
as a current path. With an explicitly selected base that contained it, a
deletion still selects the complete host owner pair rather than an empty
successful result. A `patch-release` workflow-job authority is stale and
rejects; historical skipped-job compatibility is not a live graph owner.
This is the [trusted build-once packaging contract](patch_release.md), not
the withdrawn malicious-build namespace or supervisor proposal.

## Manual evidence boundary

Visual, audio, and UX source maps to the existing
[manual-testing handoff](../.github/manual-testing-handoff.json). The reporter
requires that contract to keep deterministic criteria false and semantic
assertions primary. A manual surface retains positive and adversarial host
roles plus its **applicable** build, generated and observing runtime roles.
An unrelated runtime scenario is not evidence merely because it boots a ROM.
The independent oracle rejects missing or substituted owner pairs, including
removing both an applicable requirement and its edge. Manual handoff cannot
replace reliable deterministic automation; this source-only graph correction
has no manual-only acceptance criterion.

The A/V partition follows actual consumers, not directory-name similarity:

| Inputs | Deterministic owner and observation |
| --- | --- |
| The eleven exact main-title inputs in `paths.title-visual` | `Title_SetupMainGraphics` in [`src/titlescreen.c`](../src/titlescreen.c) consumes these graphics, palettes and maps. `expansion-modern-title-check` compares four framebuffer checkpoints in the default debug/release title-progression profiles. Skipped intro artwork does not inherit this edge. |
| Three `LORM_SP1_PROOF` package inputs from [`assets/manifest.json`](../assets/manifest.json) | `assets-test`, `assets-generate`, `assets-check` and the existing compiled consumers retain generated evidence. `expansion-modern-banim-package-runtime-check` observes alias selection, script/palette/OAM consumption and one battle entry/completion against a zero-state control. This is **not** an image or audible-sound oracle. |
| Eirika's three formatted-package inputs and four existing component aliases | The same asset generation/build roles apply. `expansion-modern-portrait-package-runtime-check` observes the face/minimug ID, render count, palette/VRAM words and mouth activity. It does not verify every portrait or an entire framebuffer. |
| Remaining `assets/` authoring inputs | Existing host, selected-manifest generation/drift and compile/link consumer roles remain. Arbitrary manifests have no single observing runtime scenario in this graph. |
| Remaining `banim/`, `graphics/` and `sound/` inputs | Existing host and compile/link roles remain, with the canonical manual handoff for material A/V judgment. No runtime pixels or audio are claimed. |
| `preview/` | Review-only TSA pairing/coherence material, as documented in [`preview/README.md`](../preview/README.md). Host tooling/documentation roles and manual handoff remain, but these files have no ROM compile/link/runtime consumer. |
| `.github/manual-testing-handoff.json` | Governance host evidence, not a rendered/audio asset or a ROM scenario. |

[`run_banim_presentation_checks.py`](../tools/gba-playtest/run_banim_presentation_checks.py)
records HP transitions and presentation-policy counters with framebuffer
capture disabled and no audio assertions. Its existing authority remains
registered, but no asset path selects it. New tracked paths in the existing
generic A/V namespaces require semantic admission and cannot inherit an exact observed-consumer rule;
untracked paths, unknown namespaces and named exclusions still fail closed.
The graph remains report-only and does not reduce any broader validation.

## Artifact lifecycle, measurements, and seals

The graph uses issue #176's admission fields: one owner, executable consumer,
unique decision, consistency check, bounded maintenance estimate, deletion
criterion, expiry, and disposition history. CURRENT expiry validation uses
the trusted host UTC instant captured for that metadata check, not a
disposition timestamp or a candidate-supplied clock. Future CURRENT
disposition history is invalid. A non-deleted CURRENT artifact whose expiry is at or
before that instant rejects; null expiry remains valid. A past Delete still
requires its existing deletion-proof semantics. This is an instant-specific
validation, not a perpetual freshness lease. Checkpoint, dependency-change, and
pre-graduation triggers each have one later proof bound to the artifact,
dependency edge or decision authority. Historical BASE comparison retains its
schema, chronology, expiry-versus-recorded-disposition and proof/authority
checks without requiring it to remain fresh today. The existing comparison-only
selection issues no CURRENT lifecycle binding. CURRENT is always checked first
with current-time admission, so an expired BASE cannot prevent a valid renewal
or excuse an expired CURRENT. The public check uses its bounded
session under the isolated launcher. Before any proof is credited, the
validated CURRENT model must contain issued **verified dispatch bindings** for
both declared roles. For each trigger, their shared artifact checker runs
before removal, while the graph is absent, and after restoration. The observed
outcomes must agree with the declared proof semantics and CURRENT disposition before
it receives credit. The required graph's observed fail-on-removal/pass-on-
restoration cannot certify a `Delete`/`pass` claim. Rejection retains the real
shared-checker failure and restoration sequence; historical BASE metadata
comparison remains separate and does not issue CURRENT proof credit.
The result explicitly says `semantics: verified-dispatch-and-shared-checker` and names
`verified_routes`; it does not claim the Make target or complete testcase ran
inside the proof.

The Make binding uses the captured native recipe and the actual scheduled
executable/argv/cwd, not a textual mention or target fingerprint. Dispatch
projection is selected for the declared consumer and retains ordinary Make
execution decisions: an up-to-date/skipped target supplies no dispatch.
The supported consumer is a direct leaf with one mandatory checker command;
compound/conditional recipes, effective failure-ignoring command/target/global
policy, changed
roots, help-only modes and substituted programs reject. Only the native
`CURDIR` observation is admitted as a Make substitution in that closed route.
Equivalent quoting, Python isolation-flag ordering and alternative target
names remain supported with complete evidence. Runtime aliases come from the
actual captured view rather than guessed host paths.

Lexical operator identity survives word decoding. Only an actual unquoted
`>` redirecting stdout to `/dev/null` may be elided; the destination may be
quoted or assembled from adjacent quoted/unquoted word parts. A quoted,
escaped or quote-concatenated literal `>` stays an argument and is rejected by
the same checker argument contract as ordinary execution. The shared shell
scanner marks unquoted operator spans, and `shlex` decodes words between those
spans; decoded punctuation alone never supplies operator authority.
Registered producer adapters consume the complete supported token grammar,
including adjacent operators such as the `||` in `-DUNUSED=1||true`.
The quoted `-DUNUSED='1||true'` is instead one literal compiler argument.
Dependency `&&`/`>` slots and printf/Python pipe slots require actual operators;
any remaining active operator rejects before producer execution. Raw word
spans retain assignment-prefix quoting and descriptor adjacency: a quoted or
escaped whole `NAME=value` is not an environment assignment, and quoted `2`
or `2` separated from `>` is not an IO number. Literal `2>&1` remains argv
data, even before a genuine trailing stderr redirection. Existing raw registry
envelopes and native stderr execution policy are not widened by this lexical
contract. The legacy plain-word parser remains a compatibility projection,
not an authority parser for these supported grammars.
Physical shell lines are separated only by LF. Blank/comment-prefix decisions
use ASCII space and tab, and word decoding does not treat CR as whitespace.
CR, other control separators and Unicode separators remain literal argument
or command-name data, including inside quotes; they are not banned or rewritten
to LF. Make-prefix trimming likewise removes only ASCII layout, never those
characters. LF continuations remain valid at EOF, with quote and word-boundary
state preserved across the join. Unclosed quotes and a bare backslash at EOF
without LF remain explicitly unsupported rather than being silently repaired.

Shell comments begin only at actual word boundaries: `.#missing` is one
literal path, not `.` followed by a comment. Logical-line normalization retains
that boundary for both plain argv and typed-token consumers, including after
continuations; trailing comments do not become registration or workflow
arguments. Quoted, escaped and mid-word hashes remain literal data.
Operators hidden after such a path still make the route conditional and reject.
Every original path component is checked against the selected immutable source namespace before
canonicalization. Missing or non-directory intermediates, source symlinks and
excursions outside that namespace cannot become valid through `..`.
Existing internal directory traversals, quoted roots and native `CURDIR`
remain supported. The ordinary launcher also asks the kernel to open the
original root as a directory before resolving it.
An originally empty root is invalid, even though normalizing an empty relative
path could otherwise produce the selected directory.

The binding consumes the complete captured recipe context, including its
environment. The supported Linux loader profile rejects `LD_*`, `MALLOC_*`,
`GLIBC_TUNABLES`, `GCONV_PATH`, `LOCPATH` and `NLSPATH` controls. A shell route
also rejects `BASH_ENV`, `ENV`, `SHELLOPTS`, `BASHOPTS` and exported
`BASH_FUNC_*` functions. These can change startup or prevent the checker from
starting; clearing them inside Python would be too late and would describe a
different invocation. An unqualified `python3` requires the captured
controlled PATH. Other benign exports remain in authority, including
`PYTHONPATH`, which the mandatory `-I -S -B` invocation does not use for
startup imports. Testcase automation remains one literal mandatory invocation
under the documented controlled process environment, not a claim about an
arbitrary user's ambient shell.

The consistency binding comes from actual captured testcase automation and
requires a mandatory `check` dispatch. Supplemental tests do not substitute
for that route. Both bindings use the same `graph_dispatch` argument contract
as ordinary launcher execution, require isolated/no-site Python and the
correct source root/default HEAD context, and verify actual loaded checker
sources against the immutable selected source through the existing trusted
module verification seam. A path, flags, check ID or source hash alone is not
binding evidence.

Bindings are issued objects scoped to the exact active model, graph,
authorities, loader, snapshot and session. Missing, forged, copied, changed
or closed-session bindings reject. Historical BASE comparison models are
explicitly structural-only and cannot obtain lifecycle proof credit; CURRENT,
public `lifecycle-check` and the standalone verifier require full bindings.
This preserves source evolution without treating a historical checker as the
current implementation.

Each bound shared check must produce the fixed named semantic failure on
removal and pass on restoration. These bounded results are attached to every
trigger-specific proof record.
Self-declared replacement reasons, fabricated authorities, stale timestamps,
or non-restoring proofs reject.
Each proof performs its own actual removal and restoration under the same
report budget. One successful cycle is never copied into several trigger
records; skipping a later removal must fail even if an earlier proof passed.
Both routes validate the artifact schema, equality with the already measured
graph, and independent oracle owner pairs using that same complete model.
The consistency route also checks its actual captured tester-case registry.
The allowed identities come only from that validated artifact's two declared
roles. A valid reviewed Make-consumer change retains its consumer role rather
than being mistaken for a tester-case ID; undeclared routes still reject.
The proof neither reruns Make nor recursively invokes the lifecycle driver.
Allowing removal or rejecting restoration in either route must fail the proof;
the other route's success cannot substitute for it.
Actual public Make and consistency commands are separately exercised on small
real Git fixtures, including removal/restoration of their authoritative
artifact. Those execution references are not full repository resource or H1
evidence.

The independently sealed oracle pins exact `(edge_type, evidence_id)` owner
pairs for runtime, host-only, generated,
localization, configuration, ABI, manual A/V, workflow, governance, templates,
pull-request-template, and repository-config surfaces, plus the exact
CODEOWNERS exclusion. Pair order is normalized before sealing, while duplicate
pairs, unknown families/evidence IDs, same-type wrong owners, target swaps,
stale paths/surfaces, or seal drift reject. Any missing or unexpected owner
pair makes the public check fail rather than emitting a successful report.
The report exposes zero false-negative/false-positive counts plus the bounded
maintenance estimate without modifying issue #176's immutable baseline
fixture or expected report.

Make authority comes from `/usr/bin/make`, not a repository implementation of
Make syntax. `graph_report.check` uses one caller-supplied `ProbeBudget`,
capture chain and public `ProbeSession` for all CURRENT targets/states, grouped BASE
queries and lifecycle checks. The caller supplies the budget; no target or
registry helper creates another lifetime. `graph_probe` owns only domain
planning and the reference-position census. Execution, immutable snapshots,
stock runtime inputs, native tools, generated publication and cleanup use the
[shared foundation](ownership-probe-foundation.md).

The native observer records actual target/prerequisite order, recipe text,
target-local variable values/origins/flavors, effective scheduled export
membership/values, includes and successful dynamic
provenance. It does not scrape diagnostic output or synthesize a dry-run
Makefile. Ordinary recipes are metadata only; genuine expansion and include
remakes use the declared real command/output adapters. The graph planner
retains actual generated include bytes for its census. It does not create a
second sandbox, interceptor, source loader or process executor.

The census preserves complete recipe text, including quoted shell `#`
characters. Make comments are stripped only from non-recipe statements;
inline recipes are separated without treating semicolons inside Make
references as lexical separators. Native Make still determines the actual
recipe and prerequisites after expansion.
The authority retains native recipe bytes, including trailing whitespace,
quoted physical comment/blank lines and here-document bodies. Non-recipe Make
comments and equivalent declaration ordering remain stable when the actual
observations are unchanged. Adding a recipe comment can itself schedule a
shell and construct an environment; it is not silently erased as though no
dispatch occurred.

`native_dispatches` binds each scheduled command to its actual `envp`, argv,
cwd and order. Global and target-specific exports and inherited unexport
membership are observed without eagerly expanding unconsumed names. GNU Make
4.3 has no target-specific `target: unexport NAME = value` directive; that
spelling retains its real native interpretation/failure, not invented support.
Malformed exports or values beyond the existing native frame/string bounds
reject. Recipe dispatch/environment changes between variable observation pages
also reject; additional value queries retain their ordinary command-provenance
and value checks.
Computed `value`, `origin` or `flavor` selectors in graph expressions or
consumed recipes reject rather than silently omit a possible prerequisite
domain. Literal selectors remain supported. An unconsumed debug recipe does
not become a graph input or force its symbolic values to expand.
Computed variable names are closed in targets, normal/order-only and secondary
prerequisites, includes, conditional-name operands, definitions and `eval`/`call`
positions. Literal name templates (including nested aliases, braces and
prefix/suffix fragments) and actual sealed finite/fallback selector values
provide the possible selected identifiers or existing typed automatic names.
Their dependencies participate in
graph classification and real native domain enumeration. Global and
target-local native values both contribute; a global fallback cannot hide the
different selector used by a target's secondary expansion. Cyclic, unsealed,
unsupported or otherwise unresolved name construction rejects. A function
used to construct an internal selector name is not reimplemented in Python:
admit that selector through the existing finite/fallback domain seam so its
actual GNU value is observed, or retain the rejection. Unconsumed unresolved
definitions do not execute.
Selector analysis itself remains admitted even for an unused definition.
It checkpoints the original shared budget and charges scanning, bounded
completed memo entries and combinations before allocation. A single distinct
choice does not excuse doubling work or name growth: the existing 128-character
name, input-file byte, 512-choice/reference-depth and aggregate cache/deadline
bounds apply. Resource refusal is not downgraded to an unused unresolved
selector. Memo entries are local to one definitions/observations context and
its active-reference ancestry; incomplete alternatives never enter the cache.
The observed-constant `subst`/`patsubst` fallback scans its declaration under
that same budget. Its outer expression counts toward the existing 512-entry
scanner depth: 511 nested literal delimiters fit, while 512 refuse even if
the observed selected name is short. Fallback depth, cache and deadline
failures remain hard errors, not unresolved alternatives or cached successes.
When secondary expansion or eval is involved, the existing native variable
pages also capture literal post-parse definitions without expanding their
bodies. The graph census retains immediate and deferred forms and closes their
references to a fixed point in the same session. Thus
`DEPS := $$($(NAME))` and `DEPS = $$($$(NAME))` cannot lose FLAGS when consumed
by secondary expansion. A literal-name recursive eval assignment constructed
with `DOLLAR := $$` is closed through the actual resulting GNU definition, not
an emulated eval. Graph/recipe context or producer-provenance drift across
pages rejects.

This is not a general Make interpreter. Dollar-generated immediate eval
assignments, overwritten observed eval definitions, opaque dollar-generated
secondary expressions, or unresolved dollar-bearing prerequisites reject
rather than inventing a finite census. Fragment classification uses the shared
Make-expression scanner to recognize incomplete or unsupported dollar tokens
and unmatched parenthesis/brace references at any position and nesting depth,
including deferred escapes. It is not an allowlist of four fragment strings:
`$(F`, `${F`, nested fragments and fragments following ordinary text remain
unresolved until their real context proves a complete reference.
Secondary uses are tracked outside eval spans, so an unrelated eval cannot
disable their rejection. A recursive literal-name eval assignment whose
resulting native definition is complete still closes normally; unused fragment
definitions remain unused. Ordinary supported eval/call and
secondary forms retain their native behavior. Complete production resource
fit remains separately required; these extra real observations spend the
unchanged report budget.

Balanced staged text is not proof of the reference names it will emit.
Staged inputs must satisfy the supported reference-preserving contract:
literal text, ordinary/scoped or already-proven computed references, and
argument-free literal/computed-name macro forwarding. Structural eval is
checked at its graph sink; reachable macro/variable bodies remain subject to
the same contract. General value-producing Make functions, substitution
references, unproven call forms, and stateful operations inside staged payload
definitions reject explicitly. Parameterized rule templates require the
separate original-context proof below. This is a positive supported grammar,
not a blacklist for `subst`.

Both complete source definitions and native raw forms are checked. The source
collector retains GNU logical lines, continuation/comment semantics and whole
multiline/nested `define` bodies, rather than physical fragments. It preserves
recipe escapes, recognizes LF/CRLF and an initial UTF-8 BOM, and distinguishes
GNU and literal `.POSIX` continuation spacing. Make comments are not shell
comments. Unproven dynamic definition names and recipe-prefix contexts reject
instead of silently changing the source classification.

Conditional defaults retain their declaration meaning when a supported
`eval`/argument-free `call` emits them. The retained-assignment path uses the
same parsed operator, modifier and target-scope sealing contract as ordinary
source, including environment-sensitive default variants. A macro emitting
`MODE ?= first` cannot obtain a baseline-only report without sealing MODE.
Conditional `define` headers retain their own default contract, while an
unused body or a literal `?=` in a value/recipe is not a declaration being
executed. Retention follows consumed macro references without expanding
unused bodies or discarding their original source histories.

Consumption is not frozen before computed references are resolved. Computed
dependencies, executing references, retained assignments/defaults and newly
available selector histories reach one monotonic fixed point. Resolved
selectors are retained even if another selector on the same line still needs
a later pass. Only after that closure may a body be treated as unused.
Unresolved/cyclic invocations reject; literal metadata lookups retain their
domain meaning without executing the referenced body. Proven literal eval
declarations may be forwarded through computed macros, but opaque generated
programs do not acquire authority. Distinct retained facts spend the existing
cache allowance, and every pass uses the same deadline; no native body is
re-evaluated to manufacture this proof.

GNU Make 4.3 records a pending `.POSIX` rule only after collapsing the next
active non-recipe statement. That first statement uses the prior mode; later
statements use POSIX folding. A define header records the pending rule before
its body is read. The collector preserves this timing, carries mode across
proven literal includes and include EOF, and tracks literal `ifeq`/`ifneq`,
quoted operands, nesting and `else` without evaluating arbitrary Make
conditions in Python. Inactive branches cannot enable the mode. A variable
named `.POSIX` or a target-specific variable assignment is not a `.POSIX`
rule.

Ordinary `ifeq`/`ifneq` operands can also use the existing bounded original
literal/reference resolver. The trusted invocation-control path remains first;
otherwise current original operand alternatives must all agree. Equal
singletons prove equality and disjoint alternatives prove inequality without
constructing a Cartesian product. Overlapping mixed alternatives remain
unknown. File-origin values, simple snapshots and recursive reads retain their
original versions; metadata reads remain non-executing body observations.
Invalidated invocation controls and source-history controls do not acquire new
authority through this fallback. Opaque/effectful values, unresolved names and
exhausted bounds still reject.

The predicate's reads belong to its enclosing eligible context even when its
body is proven false. They remain in graph/domain and generated-binding
consumer closure, so a false baseline condition cannot erase the input whose
next finite value loads another source. Only the skipped body is omitted.
No final native value, arbitrary function interpreter or guessed undefined
input is used for this original proof.
In contrast, a predicate under a proven-false enclosing context is not read.
Nested conditionals and `else ifeq`/`else ifneq` clauses after a proven-true
branch retain structural matching but do not resolve operands or request
original metadata. Potentially executable predicates still require proof;
making a skipped operand active retains every original input-name bound.

Generated target spelling is not itself mode uncertainty. The collector proves
the original parse-stage target result through literal concatenation, named
references (including short/braced spellings), paired dollars and literal
origin/flavor/value reads. Simple assignments snapshot proven values at their
original assignment point; recursive reads use the current original binding
history. Finite branch alternatives are retained and every alternative must
agree about `.POSIX`. This uses the existing 512-context bound, cache accounting
and shared deadline. Missing original input evidence remains unknown; final
native values never fill the gap.

The filename proof distinguishes the target side from prerequisites, consumes
a literal grouped-rule `&:` marker before expansion, and keeps an ampersand
introduced by a variable as filename data. It handles ordinary/pattern names,
empty or multiple targets, quoted filename spaces/colons and GNU's leading
`./` normalization. Thus an actual generated `.POSIX` retains the same delayed
activation as a literal rule. An ordinary proven target leaves the current
mode alone; it never resets an already unknown mode to normal. Unresolved
wildcards, tilde/archive syntax, introduced rule separators, absolute/parent
paths, escaped group markers and opaque/computed expansion operations remain
outside this narrow proof rather than being blanket-approved.

The closed baseline11 failure illustrates the distinction: the unknown marker
originated at generated_data.mk's item-cap stamp rule at277, whose original
target is `build/generated/data/.item_id_cap.stamp`. It was recorded at341,
not introduced by that literal `generated-data-check` rule, and then rejected
the meaningful continued config append at343-346. The source-faithful fixture
keeps the relevant original declarations and contiguous rule/continuation
slice, with nondependent chunks comment-padded to preserve source positions.
It uses real native/source observations without running the stamp/generator
recipes or a complete repository report. Companion modern pattern/grouped
target slices and renamed forms use the same proof; no Make/build source was
rewritten to accommodate the analyzer.

Unproven conditional activation rejects explicitly. Generated rules,
effectful/computed references and unresolved include order may leave the mode
unknown; mode-sensitive continuation data then rejects instead of selecting a
guessed fold. Identical results under both folds remain unambiguous. At a
parsed assignment boundary, both folds may also prove identical scope/prefix,
name, operator and RHS after GNU's leading-value whitespace handling; this
does not normalize meaningful internal/trailing value whitespace or raw define
bodies, and the mode remains unknown afterward. Already-established POSIX mode
cannot be undone by later source. Actual
invocation assignments participate in this analysis; final native values are
not substituted for original context. Literal metadata lookups do not expand
their operands. Analysis uses iterative dependency traversal and the existing
shared deadline, not a new quota, process or native observation ABI. A late
top-level `.POSIX` can change actual recipe shell flags without retroactively
changing earlier source values; GNU rejects attempts to emit such rules from
recipes.

The mode walker receives the original single-goal invocation, not an arbitrary
post-parse MAKECMDGOALS value. The GNU-owned goal's original value/origin/flavor
and mode-safe initial control reads support a narrow proof for literal control
references, metadata, strip and literal-word filter/filter-out guards. It does
not implement general Make evaluation or wildcard filter semantics. Source
control writes/undefines, supplied control overrides and unproven effects
invalidate those original facts. Error/warning/info/eval calls are not mistaken
for generated target text merely because their syntax contains a dollar;
their argument/effect checks still apply.

This supports the real Makefile guard prefix that rejected the sole baseline9
run before its first include: Makefile12-13's override assignment has identical
parsed meaning under both folds despite one extra leading RHS space. The
source-faithful guard fixture uses benign measurement recipes, not a complete
report/check invocation. The closed baseline9 semantic failure remains
preserved evidence, not a quota finding or permission for another graph run.

The following Tools prefix also needs original input/effect evidence.
Missing model entries are not assumed undefined or safe. When the selected
snapshot has an eligible zero-byte regular source, the shared session can use
that immutable empty witness with a fixed, recipe-less GNU goal to query raw
startup inputs before any candidate Make program is interpreted. The existing
`definitions=` protocol, same ordered invocation assignments and original
resource admissions apply. The query returns only input metadata, never graph
authority; nonempty/published witnesses, extra source reads, commands, recipes,
outputs and invocation-control substitutions reject. Without an eligible
witness the input remains unproven, not a success-shaped fallback. No committed
source/name/hash snapshot or new runtime permission is introduced.

Native undefined inputs and original environment/builtin records retain their
definedness, origin and flavor. Conditional source writes join abstract binding
alternatives: undefined and simple bindings are mode-inert, while recursive
alternatives keep their original expressions and are checked through current
dependencies. Unknown or effectful alternatives are not discarded. This
preserves the OS/EXE/PATH chain without selecting a guessed conditional value.
Once original OS selects the non-Windows branch, that source supplies
file/simple/empty EXE directly; a redundant original EXE query is not required.
Native OS/PATH receipts, native/source EXE and raw CPPFLAGS agreement remain
the evidence, and removing the genuine witness still rejects.

Uncertainty controls must remain genuinely unresolved as the original proof
becomes more precise. Literal `CHOICE = yes` equalities are known positives,
including overwrites that remove an older deferred effect before consumption.
The focused controls use effect-free `CHOICE := $(subst X,yes,X)` and its `no`
counterpart for a deliberately opaque original value. Native yes/no behavior
then distinguishes immediate/deferred append timing, alternative values,
possible POSIX effects, include visits and target modes. Declared MODE input
does not rescue an append whose original timing is still unknown.
Distinct opaque CHOICE inputs retain two PART alternatives each: the target
plan has512 alternatives at width9 and rejects width10, while known-choice
width9/10 cases each have one result. These are original abstract alternatives,
not hundreds of native states. No resolver, permission or bound is weakened
to keep an old fixture's uncertainty label.

Original input lookup is invalidated by unproven namespace effects. Exact,
literal-path `wildcard` includes may use the captured source/generated namespace
to prove an empty or present outcome; unresolved patterns/context still reject.
This is not Python evaluation of general Make functions or arbitrary globbing.

Variable-derived include names use that same original literal/reference model.
Transparent computed references such as `$($(NAME))`, braced equivalents and
literal prefix/suffix name aliases share one original-name proof between
effect analysis and value lookup. The proof uses current original bindings,
including simple snapshots and recursive reads, not final native selector
values. Name-construction reads, selected bindings and metadata endpoints
travel with their original source units into source-history and generated-
binding consumer obligations. Metadata endpoints do not execute their bodies.
Unknown/cyclic names, unsupported transformations and exhausted context or
deadline bounds remain unproven.

Only one proven filename sequence is credited; different alternatives,
effectful/computed transformations and unsupported wildcard/escape/path forms
remain unproven. `include`, `-include` and `sinclude` retain their filename
roles, including empty or multiple names. Proven active conditions propagate
their context into the existing ordered source walker. Its complete visit
sequence must match native MAKEFILE_LIST and actual file-open evidence;
missing sources, extra/misordered occurrences, active cycles and changed
original parsing cannot become absent-file success. The existing empty
exact-path wildcard proof is distinct from naming an absent optional file.

The source fixture for `FLAGS ?= first; NAME = FLAGS` and a computed include
must supply the genuine empty-source input witness; absence of that evidence
is not assumed undefined. Both finite FLAGS values select their actual native
include/prerequisite, while symbolic or unsealed graph inputs reject. This is
distinct from an opaque `NAME = $(subst X,FLAGS,X)` in an ordinary prerequisite:
its existing declared tracked-fallback contract can use GNU's actual selector
value only under the unchanged original-history guard. It does not grant
original include/phase authority to arbitrary transformed names. The opaque
fallback case runs independently, so an include regression cannot hide it.

Source recovery is independently bound to the trusted invocation's explicit
`makefile` (`-f`) selection, not the first entry of mutable MAKEFILE_LIST.
The actual open evidence supplies the available source pool, with that selected
primary first. Only proven original Make visits are decoded and included in
the semantic stream; ordinary data reads retain their evidence without being
parsed as Make programs. The candidate's list must then match those visits.
Omitting/reordering the primary or another read Makefile cannot hide the
declaration that performs the mutation, an external default, or its history.
Non-default primary filenames use the same existing invocation interface.
Template-definition and dependent-global metadata queries retain that same
selected primary, rather than falling back to an unrelated default Makefile.
The intentionally empty-source original-input witness remains a separate
query; retaining template selection does not execute the candidate for it.

An initially absent optional file may be remade. The supported generated-include
boundary requires a native one-restart observation, no supplied restart/list
override, the actual generated source and publication record, and agreement of
every recorded generated-output content identity. Only stable literal
dependency facts are credited across the absent/remade passes: no assignments,
special targets, recipes, includes or emitted expressions. Canonical `/repo/`
prerequisites from the existing dependency renderer remain source-path data.
Restart-sensitive source reads/writes and other generated-source histories
reject; this is not replay of arbitrary Make programs or substitution of final
variable values for original inputs. Existing source/publication ownership,
native protocol and all budgets remain unchanged.

Neutrality is a parsed statement property, before filename validation. The
shared source collector distinguishes directives from ordinary rules, so
`include mode:`, `-include mode:` and `sinclude mode:` remain includes even
though an operand contains a colon. A rule named `include:` remains an ordinary
rule. A first-colon heuristic cannot grant neutral restart-history authority.

Actual native dispatch environment membership also enters the same consumption
fixed point. Bare, computed and implicit/export-all declarations therefore
cannot hide exported MAKE_RESTARTS or MAKEFILE_LIST. These changing process
inputs reject insensitive remake-history credit; unrelated benign exports
retain native behavior. Exported definition bodies are consumed when their
definitions become reachable, including retained eval declarations, while
unexported unused bodies stay lazy. Unsupported export-name effects remain
subject to the original source/effect guards, not an inert default.

The baseline12 source failure at generated_data.mk482-486 followed the real
variable-named optional include at476, under the original non-checker goal
condition. Its FORCE-backed dependency writer is not an empty-wildcard case.
The regression retains that declaration/rule/condition/consumer slice and uses
an explicitly selected small fixture writer with actual create/retain/remake
receipts. It does not claim complete production-collector or repository-report
acceptance; the consumed baseline12 allocation remains closed.

A separate generated literal-binding-module certificate covers a restricted
metadata format; assignments are not reclassified as neutral dependency rules.
The shared parser derives unique, unconditional global `:=` writes with exact
literal values. Engine/history and GNU4.3 implicit-rule inputs are protected,
including originally undefined inputs such as CFLAGS and TARGET_ARCH. Original
native empty-source queries must prove every write name undefined; input
overrides, exports, missing witnesses and conflicting publication identities
reject. No asset prefix, producer ID or output filename grants eligibility.

The initial certificate supports one newly created binding module and one
actual producer dispatch under a parsed empty-MAKE_RESTARTS guard. Its first
branch has only the selected output rule and proven literal recipe commands;
the other branch has the same recipe-less target. Original rule ownership,
command expansion and actual event order must agree. Other possible owners,
unresolved targets, nested/altered phase control and other counter uses reject.
The phase test is exempted from ordinary sensitivity only after this proof.

Generated writes remain provisional in the original mode model, preserving
undefined and assigned alternatives. The complete consumption fixed point then
checks executing roots and reached bodies, including computed references and
metadata reads, against the write set. An unrelated unexported deferred body
stays unused; origin/flavor/value observations remain non-executing body reads
but still count as consumers. Actual native export membership and producer input
identities corroborate the exclusions. Variable-universe access and active
unproved file/wildcard/realpath or emitted-program operations reject rather than
receiving a normal-mode or final-value default. These restrictions are explicit
eligibility boundaries, not an implementation of arbitrary Make replay.

Certification retains the original census's resolved graph reads, including
the variable selected by `ifdef $(SELECTOR)` / `ifndef ${SELECTOR}`, rather
than checking only the selector's spelling. Transitive execution edges and
their metadata-read endpoints join that closure; a metadata endpoint does not
cause its body to execute. The restart/source-control guard also receives
resolved immediate reads, including computed substitution references. The
shared reference parser separates a substitution's base name from its pattern
and replacement, so `.VARIABLES` and `.VARIABLES:%=%` are equally observable
universe reads in either delimiter style or a reached alias. Unsupported
computed names and unknown phase/data/program contexts still reject.
Literal `call` targets use the same call-aware base parsing: `$(call .VARIABLES)`
and its braced/argument form cannot hide a universe read, even after the
verified phase guard and final rule. A missing default declaration does not
make the resulting first/final process-input difference safe.

This admits the actual asset-discovery emitter's five literal metadata
assignments in an uninstrumented, non-interfering candidate. Its captured-source
renderer and native publication agree byte-for-byte; ordinary CLI discovery
intentionally uses a different mtime-based digest while preserving the same
consumer lists. Stable final bytes alone remain insufficient: a real-emitter
control creates/exports a first-pass-only default that vanishes after restart.
Consumer and phase obligations must close before graph authority is granted.
The same context/name bounds, source/cache/control charges and shared deadline
apply. Baseline13 remains closed; this certificate does not claim full repository
eligibility or resource fit.

The source-only read-closure regression also preserves two no-default controls:
the computed conditional exports PHASE_LABEL=first to the actual producer but
has final raw value `final`; filtering the identity-substituted universe exports
an empty PHASE_LABEL but has final raw value `ASSET_BANIM_INCBIN_CONSUMERS`.
Both retain the same 347-byte production output. Corrected certification and
the complete small-fixture planner reject both. Independently restoring either
old read omission recovers that fixture's false certificate and small graph
admission, not a whole-repository report. Dependencies are the existing parsed
source stream, original input/phase evidence, registered producer and native
observations; other feature/profile conflicts are none. No native ABI, generated
format, permission, resource budget, CI policy or game/save/locale/ROM/RAM
contract changes.

Assignment timing and resulting flavor are distinct contracts. GNU Make `!=`
executes its RHS immediately but creates a recursive variable, unlike
`:=`/`::=`. If the original shell output cannot be bound safely, mode analysis
retains a recursive unknown body: a later read may execute Make functions and
cannot be treated as simple-variable inertness. Appending to that recursive
unknown retains its uncertainty. RHS effects, define/eval/modifier and target
contexts still use the actual native producer and original source contracts.
Unused shell results remain unexpanded; safe ordinary shell assignments are
not blanket-banned, and late expanded values are not substituted as evidence.

Append RHS timing comes from the original binding, not a fixed operator list
or the final native flavor. `+=` expands immediately for an existing simple
variable; a recursive or originally undefined binding keeps the appended body
deferred. Each original source occurrence carries that decision and assignment
applicability into consumption, emitted-default retention and define handling.
GNU write precedence is separate: even a rejected simple assignment or append
to an override-origin simple variable can expand its RHS. Ignored deferred
bodies do not become execution evidence. An empty append can also preserve the
previous origin rather than adopting `override`.

Target-specific histories retain their own bindings. A new target-specific
append does not borrow a global simple flavor, while original command-line
precedence remains relevant. Unproven effects invalidate old binding context;
unknown flavor alternatives cannot silently turn a possible immediate effect
into an unused body. Literal eval assignment arguments containing only literal
bytes and paired dollars can carry their original assignment decisions through
the same seam. This is a bounded syntactic output proof, not evaluation of
arbitrary functions, computed programs or later native values. Other generated
append contexts with surviving references reject when their original timing
is unproven. Invocation/mode guards remain conservative even for a proven
literal emitted assignment. One expansion of a paired-dollar eval spelling
must not be mistaken for executing that returned spelling.

Mode failures report their actual visitor path, logical statement and physical
line span, plus the first unproven source site/input name. The error chain and
failure classification remain, without dumping variable or environment values.
The baseline10 CPPFLAGS failure is intentionally not fixed by normalizing its
internal whitespace: one versus two internal spaces are different raw values.
The original data/effect and include proofs instead keep the real normal mode
established through that full source-faithful prefix.

Assignment origin remains part of that context. Command-line assignments
retain their admitted precedence; ordinary file assignments can replace
environment values, and a defined empty environment value still prevents
`?=` from assigning. Input order and native environment evidence are not
rewritten. A completed include is analyzed again with its current definitions,
not skipped because its entry POSIX bit matches an earlier visit. Nested
visits preserve their original order and share the same deadline. Identical
source units need only one retained representation, but changed parsing across
visits rejects rather than replacing an earlier history. Active include cycles
remain distinct from legitimate completed revisits.

Mode analysis and semantic consumers share one ordered occurrence stream.
Each include evaluation retains its place, conditional context and unique
occurrence identity; only immutable source text is reused. The stage census,
assignment history and template-input proof consume that same stream rather
than rebuilding a first-visit-only path walk. Proven inactive occurrences are
not executed; unknown branches remain conservative. Thus an include first read
before secondary expansion cannot hide a later active read after it. Stream
and template representations remain charged to the existing cache budget.

A later rewrite cannot erase an earlier opaque operation or selector value.
Computed-name closure retains all proven source alternatives alongside native
observations, including literal eval assignment history and target-specific
definitions. An unresolved earlier alternative or ambiguous generated
assignment history cannot be repaired by a well-formed final value. Eval's
pre-expansion input is not fabricated into its resulting raw definition.
Only a single unchanged, context-free literal-function initializer can use
its native finite-name observation in place of unsupported Python evaluation.
Secondary-expansion sinks follow the original rule/include order; enabling
the feature later does not retroactively make earlier rules secondary.
Unknown include order is conservative, not original-context evidence.
The reporter never obtains authority by expanding a stateful body again after
consumption. Ordinary non-staged Make evaluation remains native, and complete
supported deferred forms and native-proven recursive eval still resolve.

The real generation and modern-object templates in `generated_data.mk` and
`modern.mk` use a bounded reference-preserving contract, without recognizing
their symbol spellings. A top-level `foreach` passes one literal identifier
word as one positional argument through `eval`/`call` to one prior recursive
source macro. Its native raw body/origin/flavor must agree with that original
definition on the private terminal-observation path. The public per-pass path
instead consumes its original caller-time proof, without terminal metadata
queries or terminal-value substitution. External macro overrides, extra
parameters, redefinitions, uncertain/repeated include order and unsupported
generated writes reject.
The emitted rule has one literal separator and a parameter-bound confined
target. Immediate inputs must have proven original global bindings and literal
values that cannot inject another reference, line, rule or assignment. The
private terminal path additionally checks preceding assignment histories and
pure initializers; a public pass retains its actual effective source/input
facts at the caller instead. Deferred recipe references and
automatic-variable substitutions remain recipe context; deferred operations
or additional unbound scope references do not acquire template authority.

Mode effects are proved at each original call occurrence, before a later
mode-sensitive source continuation is collapsed. The shared foreach/eval/call
parser proves that the statement itself expands only to whitespace. The
loop-variable call argument must retain its exact bytes and match the
supported bare reference; padding is not trimmed into a different emission.
Outer expression whitespace does not authorize whitespace inside that payload.
The
original recursive body, parameter values and interpolated header/recipe
inputs must separately prove ordinary confined targets and no introduced
Make program or special parser target. Only that proof preserves the incoming
known mode and namespace; an already unknown context is never reset.

The proof normally uses current original literals. A simple initializer whose
ordinary value remains unknown may retain one narrow original native-claim
contract: literal/direct-reference arguments to a single `patsubst` are captured
at the original assignment, and a later raw native value must match that exact
bounded substitution relation, including GNU whitespace and empty matches.
Empty patterns, unproved escaping and multiple percent operators remain
unsupported. Empty stems/replacements for the supported nonempty patterns
retain their actual GNU behavior. Bare aliases may retain the same snapshot, but extra
literal whitespace is not discarded. This does not add function evaluation to
ordinary source conditions or the literal-value resolver, nor accept a final
value simply because it looks plausible. A literal-directory wildcard can
instead retain an original namespace header-safety bound; that is not an
exact value and cannot supply parameters or target names.

An original simple assignment can compose that header-safety bound from
literal fragments, supported bare references and supported wildcard
expressions. Every literal separator and every component must prove safe;
unknown, malformed, effectful or staged components are not empty strings.
References use the original assignment-time literals or current-version
header bounds, including existing bare aliases, never a later native value.
The actual mixed characters-config initializer (nine literal paths followed
by an asset-script wildcard) uses this generic composition, not a table,
macro or filename exemption. Recursive compositions and appends do not gain
new snapshot semantics. Bounds cannot supply exact template parameters,
targets, comparison operands, recipe values or native-value claims.

The separate exact initializer path supports bounded original `notdir`,
`addprefix` and substitution-reference chains, including nested literal and
reference operands. It snapshots the actual simple-assignment inputs and
retains an exact result without querying native values to construct it.
Existing captured `patsubst` relations share their text semantics with that
path; a native value remains only a claim checked against original operands.
GNU word/empty-component behavior is preserved: `notdir` is textual, not host
path normalization, and prefixes and output spacing are not trimmed.
Unsupported escaping, extra percent operators and unknown/effectful/staged
leaves still decline. Supported pure recursive bodies are resolved at use,
not frozen at definition. This remains a bounded exact algebra, not a general
Make interpreter.

An applicable global simple `+=` preserves a proven original value even when
its preceding initializer stored that value only as an exact template fact.
For example, `OBJECTS := $(addprefix out/,first.o)` followed by an unresolved
conditional `OBJECTS += out/second.o` retains both `out/first.o` and
`out/first.o out/second.o`, just like the equivalent literal initializer.
The shared original-simple-fact check proves the same stored binding, origin,
scope, version, source occurrence and valid namespace. The fact transfers to
the ordinary binding representation before RHS evaluation; a changed binding
or context during capture rejects. An inactive assignment leaves the original
binding and snapshot untouched.

Only exact values qualify, never header-safety bounds, stale facts, unrelated
scoped/inherited values or terminal Make observations. Recursive appends keep
their deferred semantics; precedence, empty and unknown RHSs and effect
invalidation remain distinct. Taken and not-taken alternatives are complete,
not sampled, and every retained append alternative is admitted against the
existing cache/total budget and 512-context bound. That bound is not a limit on
the number of words in one value. Concatenation uses the existing accounted
text path, sharing the original deadline. Possible `.POSIX` activation and
genuinely different continuation values still refuse.

The [conditional-append procedure](test-cases/workflow-governance.md#original-source-conditional-append-correction)
separates actual pure-API evidence, unchanged representative source slices with
explicit modeled inputs, and the separately required native qualification.
It does not certify the complete modern aggregate or borrow a native branch.

Finite value composition also consumes a genuinely exact original simple
snapshot alongside another operand's multiple values. For example, after the
conditional append above, `DATA := $(addprefix out/,data.o)` and
`ALL := $(OBJECTS) $(DATA)` retain both complete aggregates, just like a
literal `DATA := out/data.o`. The read does not replace DATA's binding,
rewrite unrelated variables or acquire assignment authority.

Append capture, exact-reference fallback, original alias forwarding and
explicit inherited-base lookup share the same binding/fact validation.
Finite composition newly uses only the current global file/override simple
fact, not a global snapshot substituted for a scoped value. Genuine scoped
and inherited values keep their existing resolution. Binding/fact identities,
scope, source occurrence, version, namespace and shared budget/clock remain
checked across resolution; aliasing cannot launder a stale fact. Snapshot
resolution never calls back into literal/exact resolution, and stored simple
dollar bytes are data, not a new recursive Make body.

All nested value reads share one bounded, temporary **outer read lifetime**.
Resolving `ALIAS = $(DATA)` retains DATA's actual binding/fact/version receipt
until the outer expression returns; nested success does not retire that
dependency. Scoped reads retain the selected binding, inherited global base,
inheritance decision and any observed missing scoped slot. Scope selection
retains the declaration sequence the matcher actually consumes, not an
arbitrary global-definition or source snapshot.

The last budget checkpoint precedes every final receipt comparison. Final
validation compares those original objects and versions without callbacks,
name re-resolution or accepting a newer binding. A later operand cannot
replace a recursive dependency or scoped/inherited value and still publish
the earlier text. Public value-entry signatures remain unchanged.
Nested success, errors and cancellation restore the caller's tracking/scope
state; an owned lifetime releases its references on exit and independent
reads start fresh. Assignment callbacks preserve their writes, but their
separate RHS/write-precedence reads do not pollute an unrelated outer read.

Receipt/selection retention is admitted before growth against the existing
entry, cache, total and deadline bounds. Repeated lookups deduplicate without
dropping transitive dependencies. No global authority registry, whole-map
ledger, new budget, refund or resource waiver is introduced. The
[provenance follow-up controls](test-cases/workflow-governance.md#mixed-fact-transitive-and-scoped-provenance-follow-up)
preserve the exact rejected3d9e75 witnesses and distinguish modeled mutation
from the separately required original native source/job-context check.

All represented combinations remain subject to the existing 512-context and
depth bounds. Scanning, assembled text and retained alternatives spend the
same cache/total budget before retention. Duplicate outcomes do not consume
new capacity. Unknown or header-only facts, cycles and effect invalidation
remain unproved; possible `.POSIX` and differing continuation data still
refuse. No operator, variable-name exception, query or resource flag is added.

The [mixed-fact procedure](test-cases/workflow-governance.md#original-source-mixed-fact-composition-correction)
uses the unchanged computed data and assembly object initializers from
`modern.mk`, with explicit modeled leaf inputs. The earlier representative
append model used literal object tails; it did not cover this composition.
Closed sizing5 still refused the native aggregate. Neither these models nor
the source correction establish its exclusive cause or native resolution.

Exact wildcard leaves require a separate **session-issued original namespace
capability**. Its initial names, types and lookup identities derive from the
admitted materialized Snapshot/view, not `mode.namespace`, a caller set, or the
final generated-file union. The private issuer binds the actual Make
invocation/state, observation, view epoch and session lifetime. It seals only
after successful native completion and cleanup, retaining the complete host/
nested publication and removal history independently of source conditions.
Inherited publications must retain their private issued identity. Tokens and
sealed records cannot be recreated from ordinary data; view changes, expired
observations, altered context or incomplete captures reject.

Literal absolute runtime leaves use that same issued observation, not the
repository-relative directory matcher. Tool setup freezes its actual capture
before subsequent setup calls, and retains it locally through materialization,
not by rereading mutable session declarations. The optional runtime image is
bound before the session is exposed: original request/input/dispatch tuple identities,
immutable input values, owned materialization and ancestor/stock-alias identities,
budget, deadline and owning thread are retained. The existing namespace capture
carries this image through actual Make admission and sealing. Lookup rechecks
both bindings and owned custody; a source-pass lookup also retains its original
source-image/journal lifetime before and after the lookup. Changed, missing,
foreign or expired evidence is a refusal, not a new capture or an empty result.

Only explicitly captured original/canonical names and descendants of a captured
absent leaf qualify. A missing ancestor can prove the requested leaf absent;
it does not admit an unrequested sibling or reverse alias. Absolute runtime
globs, directory enumeration, escaping spellings and new execution rights remain
unsupported. The lookup never reads a live system file, borrows a terminal Make
value or starts another query. Owned-image checks reuse no-follow/no-atime
directory custody without enumerating runtime directories. An unchanged captured
symlink inode/stamp retains its original target text without a repeated
`readlink` changing metadata visible to Make.

The shared directory walk acquires successor ownership before fallible
predecessor retirement and never retries a withdrawn pin. Failed traversal
attempts every remaining owned close independently. Runtime-object cleanup
preserves the original wrapped custody cause and exposes cleanup secondaries
through the existing lifecycle error/notes contract. Cleanup failure cannot
become successful metadata or an empty wildcard, including after an otherwise
valid absence result. These rules also preserve repository-directory callers;
they do not relax lookup authority or assert that an ambiguous close succeeded.

This restores the already-admitted `/usr/include/newlib/stdlib.h` discovery in
the original modern source. Both captured presence and captured absence leave
the subsequent driver-flag condition and continuation mode provable. The
conditional/effect/continuation guard itself is unchanged; removing the runtime
connection reproduces the refusal. The
[existing case's runtime-wildcard controls](test-cases/workflow-governance.md#original-source-runtime-wildcard-correction)
separate memory-only API evidence from the required native comparisons and
complete-report/resource/delivery gates.

The [5696038608 CI correction](https://github.com/laqieer/fireemblem8-expansion/issues/180#issuecomment-5696038608)
keeps namespace observation metadata-neutral. Root and component directory
pins use `O_NOATIME` with the existing directory/no-follow/close-on-exec
constraints; failure is explicit, with no ordinary-read fallback or timestamp
restoration. Complete metadata, including atime, still governs cache reuse.
The traversal extent is the exact admitted source/publication namespace,
including its root and derived parents, not a second source-entry quota.
Snapshot admission, publication creation limits, per-node charges, deadlines,
membership/types and completeness remain unchanged.

The focused [namespace-image procedure](test-cases/workflow-governance.md#namespace-image-ci-regression-correction)
preserves direct and selected-view metadata/cache behavior and the original
native residual-grant callbacks. Publication tests distinguish the closed
identity-only inherited revalidation record from actual effective publication
outcomes; they do not remove the inherited verifier or invent publication
effects. No source-phase, native permission, budget or CI topology change is
part of this correction.

The exact matcher accepts ordered GNU-whitespace-separated patterns with
confined literal directories and literal or basename-star final components.
It preserves C-byte ordering per pattern, duplicates across patterns, hidden
names, known empty matches and matching directories. Question/bracket/escape/
tilde/directory-glob forms, unadmitted nonregular lookups and ambiguous raw
whitespace filenames decline explicitly. Members, matches and assembled text
spend the existing budgets. A simple assignment snapshots the result; a
recursive/default body evaluates against its original operands at each use.
Neither a missing proof nor a header-only wildcard bound is an empty list.
GNU's logical directory entries `.` and `..` are not materialized `scandir`
members. A raw final-component pattern that could match either entry therefore
declines before path normalization: `src/.*` cannot certify an empty result
even when the source directory has no hidden files. This adds no parent or
directory-entry authority. Ordinary `src/*.c` and nonlogical hidden-name
patterns such as `src/.hidden*` and `src/.*c` keep their exact semantics.

Only invariant queried namespaces qualify. Unrelated build publication can
remain harmless, but source filename/parent creation, removal, replacement or
unsafe lookup identity retains a hold even after cleanup restores the initial
names. GNU may cache an old directory list for the whole process: EARLY, LATE
and a lazy wildcard can all remain unchanged after a real publication, while
a second process sees new names. Final directory contents cannot repair that
missing original authority; no general cache replay or new native query API
is provided.

The same original exact machinery supports `filter-out`, substring
`findstring`, `strip`, short-circuit `and`, applicable global-simple `+=` and
derived literal target scopes. Read/effect analysis retains immutable source
separately from proven lazy read forms. Deferred recipe reads may use only
whole-source immutable literal operands with no target-specific/dynamic writer
or earlier parse-time consumption; a later value cannot prune earlier reads.
Possible `foreach` locals, including nested or potentially emitted binders,
exclude their names from that global-literal context. Computed or unclosed
binders disable whole-source literal pruning; called bodies retain the existing
unknown-writer boundary. Discovering a possible binder does not expand its
unused body or change immutable source. An empty global `EMPTY` cannot hide a
deferred effect when a consuming loop locally binds `EMPTY` to nonempty text.
Binder and dynamic-writer scans use parsed Make roles: ordinary comments are
ignored, but tab/inline recipes and define-body data retain their expressions.
Escaped hash data is preserved. A dollar or fake binder in an ignored comment
cannot disable a valid lazy-read proof; a real local binding after hash data
must still retain its hidden effects and defaults.
Parse-time effect traversal carries a separate local scope at each occurrence,
including through recursive variable bodies. Foreach name/list operands use
the incoming scope; only its body sees the simple local binding. A known-empty
list does not consume the body. Lazy operand proofs in that body may use an
original global fact only when its bounded read closure cannot touch a local
name, including through literal metadata. Global and local traversals do not
share a completed proof. Unsupported computed local selectors remain unproven.
This happens before later source folding and conditional pruning: an empty
global cannot hide a locally triggered `.POSIX` eval and erase a later default.
Unshadowed globals, simple snapshots and metadata-only reads keep their lazy
behavior. This is not authority to replay arbitrary local values or effects.
The exact word operations tokenize with GNU's explicit C whitespace, not
Python's Unicode whitespace. In particular, a U+00A0 between filter patterns
does not become an ASCII separator: the unchanged unsupported token declines.
Supported text operands, `strip`, substring matching and lazy `and` preserve
non-ASCII bytes rather than trimming or splitting them as Unicode whitespace.
Target-local `+=` remains recursive, including its global inheritance and
later RHS lookup even when the global variable is simple.

An original target-only fallback can consume these exact snapshots and
existing verified `patsubst` claims when the incoming mode, namespace and
binding context remain proved. Header bounds cannot enter that path. Target
words still use the existing parser and delayed `.POSIX` recording: GNU
collapses the first following non-recipe line before recording the preceding
rule. A static target pattern is not a filter; an unmatched `.POSIX` target
warns but still activates POSIX. No final harmless rewrite, early recording
or reset to GNU mode supplies authority. Parsed fragments and constructed
text are charged before retained assembly through the existing shared budget.

Original versions, precedence and unknown/provisional alternatives invalidate
or withhold those facts. Header inputs cannot contain syntax/staged dollars;
recipe inputs cannot introduce Make expansion or new source lines. A staged
input that activates `.POSIX` and then rewrites itself to a clean final value
therefore cannot certify neutrality. A mode-sensitive later condition cannot
hide an invalidating write by borrowing a provisional GNU mode.
In particular, a padded call that emits a separate `.POSIX` target and an
empty-pattern claim followed by a native table-list rewrite both reject.
Their actual later hidden defaults cannot disappear behind guessed GNU folding.

Final graph authority is still deferred to the complete original source,
definition/input history, native body/namespace and template-reference checks.
The per-call effect proof does not waive late writes, repeated definitions,
source/remake ownership, phase/export/default obligations or special targets.
Native metadata queries and retained/expanded proof data spend the existing
shared budget; the bounded verifier does not build an unbounded output string.

The supported header wildcard has one confined literal directory and a single
ordinary pattern. GNU Make supplies its actual result; the proof conservatively
checks that the directory's admitted source/generated names cannot introduce
Make syntax. It does not implement glob expansion in Python. Only the proved
reference-analysis representation separates header inputs from recipe-only
compiler flags: the original macro, call and all source bytes still execute
through the real native Make observation. Literal definition pages and the
incrementally admitted template representation spend the existing observation,
run, cache and aggregate allowances, including the unchanged combined
512-name request bound. No synthetic Makefile supplies graph evidence.

Source-faithful template slices exercise generated paths, table selection,
shared/table-specific Python and config prerequisites, real fixture C
generation and ARM object compilation. These prove those template contracts,
not the production generator implementation or full-repository graph/resource
fit; the complete production, verifier and H1 gates remain separate.
The composed-header regression retains all four actual default linked-table
declarations, the mixed characters inputs and the later meaningful units
continuation. Short and renamed controls, composition-only removal, original
snapshot/type boundaries and finite default/export/read-closure checks keep
that evidence distinct from any unrecorded full-run target or planner state.

The derived-target fixture additionally retains the actual generated-C/object
initializers and static legacy recipe before the late continuation. Separate
constructor and target-consumer removals recover their original failures.
Related source support is intentionally narrower than whole-program closure:

| Original target list | Bounded original ancestor support | Remaining boundary |
| --- | --- | --- |
| `generated_data.mk`: `GENERATED_DATA_LINKED_OBJECTS` | Actual four-table `notdir`/`addprefix`/suffix chain and small planner | Full repository source/resource acceptance is separate |
| `Makefile`: `LEGACY_C_OBJECTS` | Actual wildcard/filter/source-list/suffix ancestry and `filter-out` | Invariant source image and original input contract required |
| `Makefile`: `ASM_OBJECTS` | Actual wildcard/composed `SFILES`, suffix and secondary prerequisite syntax | Non-invariant or unadmitted namespace remains held |
| `Makefile`: `DATA_SRC_C_OBJECTS` | Actual ordered multi-pattern wildcard, suffix and secondary/preproc syntax | Same independent namespace requirement |
| `modern.mk`: `MODERN_ALL_DATA_PRE` | Actual recursive wildcard default and `.pre.c` constructor | Genuine default/defined-empty and at-use context required |
| `modern.mk`: `MODERN_ALL_DATA_OBJECTS` | Actual recursive default and derived target-local layout-flags append | Unknown/pattern/empty scopes and unproved RHS effects still decline |
| `modern.mk`: `MODERN_ALL_DATA_ASSET_DEPS` | Shared original data-leaf mechanism and `.assets.d` constructor | Separate producer/include obligations remain |
| `modern.mk`: `MODERN_ALL_C_HEADER_DEPS` | Actual source defaults/filter, BGM wildcard/and/strip/findstring ancestry and exact append | Original branch, input and namespace certificates remain mandatory |

The seven former refusal cases now retain source-faithful fixed and independent
component-removal evidence, not substituted literal source lists. The bounded
contexts use disclosed fixture files, tool inputs and benign recipe roles;
they do not establish complete repository/resource acceptance. Genuine
namespace changes or unavailable authority remain explicit holds. Secondary fixtures retain the real
consumer order: the linker dependency is read before `.SECONDEXPANSION`.
Moving a derived-list consumer after that directive deliberately reaches the
unchanged staged-reference guard; exact target-mode proof does not waive
secondary value provenance. These distinctions do not assert that all eight
complete production contexts or a full report have been measured.
The logical-dot, local-binder and C-whitespace regressions additionally retain
actual GNU/native HIDDEN definitions and complete small-planner rejection.
Independent removal of each correction recovers its false empty-default
admission; that is bounded counterexample evidence, not a whole-report bypass.
These corrections do not authorize live message-source publication, tracked
header writes, directory-entry relocation or a changed raw-source wildcard
namespace.

Recipe observation preserves read kind through ordinary references, aliases
and captured exports. Literal `origin`, `flavor` and `value` operands use
native `definitions=` records, including raw value, origin/flavor and ordered
per-file contexts, without expanding an otherwise unused error or shell body.
Genuinely executed references still receive expanded observations. Mixed uses
retain both forms in separate disjoint requests when necessary; pages share
the original combined 512-name bound and all native byte/frame admissions.
Undefined-input sealing recognizes raw observations without granting new
computed-introspection authority. Original argv, environment, error policy and
recipe dispatch must remain equal across pages. This is not permission to
discard required body evidence or to sanitize a different invocation.

New paths brought in by the delivered producer/dependency/adaptive/cleanup
contracts have explicit selectors in the existing host and documentation
rules. The original introduction cohort is unchanged; another new path under
those prefixes, including a new changelog fragment, still requires its own
semantic admission.
The pure `scripts/workflow_pilot/tests/coordinator_support.py` helper is
explicitly classified with the existing host owners. An unclassified adjacent
helper still rejects; neither its directory nor the introduction cohort is
expanded to admit it.

All report observations share the existing deadline and cumulative resource
accounting. BASE uses one public `select_view` block, the same original source
paths and independently captured BASE registry/model. Global graph/target
caches and per-target executors are removed. The already validated model is
reused for the same artifact's nonrecursive lifecycle removal/restoration;
that reuse is not another graph evaluation or a success label.
Within each selected view, the existing Make registry is parsed and validated
once for its authority pass. Its ambient, finite, typed and generated-input
sections reuse that validated data; repeated tool/input references reuse their
captured bytes with explicit cache-byte accounting. The metadata object is
bound to its exact loader and budget, cannot cross CURRENT/BASE views, and is
not a process-global cache or a switch that skips validation.

Schema version 5 seals each external selector as either a finite exact domain,
an exact tracked fallback, or symbolic recipe/environment-only authority.
The parsed model admits 112 prerequisite domains: 111 tracked fallbacks and
the explicit `NODEP` domain (`""`, `"0"`, `"1"`). Acceptance requires every
domain to be observed through real command-line GNU Make, with
environment-sensitive domains also observed through a clean process-environment
origin. A count derived by `load_make_prerequisite_domains` proves the admitted
scope, not live execution; a smaller synthetic fixture is not a substitute.
Full-domain graph/oracle/lifecycle/public-gate adoption of the independent
[issue #206 foundation](https://github.com/laqieer/fireemblem8-expansion/issues/206)
is a separate acceptance requirement, not evidence supplied by the focused
A/V correction. The baseline plus all variant traces are
unioned per evidence target. An unclassified external input, stale symbolic
classification, oversized domain, missing target, alternate active error, or
failed variant rejects. Symbolic recipe inputs are retained in the fingerprint
but are admitted only when the closed reference-position census proves that
every transitive use is confined to a recipe/environment payload. A use in a
target, prerequisite or secondary expansion, include, conditional,
target-specific assignment, or `eval` expansion requires a finite domain even
when the registry mislabeled it as symbolic.

The named external-selector syntax census is the one irreplaceable lexical
Make boundary: a closed comment/recipe/`define`-aware parser extracts static
`?=` names, direct and secondary-expansion references, definition
dependencies, and graph-versus-recipe positions from every GNU Make-loaded
authoritative input. It rejects dynamic left-hand sides and conservatively
classifies computed names. It decides no target or recipe semantics;
behavioral mutation tests prove each classified graph position through real
GNU Make. GNU Make remains the behavioral authority, runs with
undefined-variable diagnostics, and rejects every evaluated undefined name
not covered by a finite/symbolic registry entry or a typed
builtin/automatic/scoped contract. This covers defaults and references reached
through `define`, `eval`, `call`, `foreach`, and computed names without relying
on identifier spelling as behavior evidence. The probe emits domains, symbolic
names, generated paths, and typed-variable census fields only from loaded
source and baseline/variant observations. Registry entries are expected
inputs, never synthesized observations, so an unused domain or stale generated
path rejects. Variant discovery is a deterministic fixed point: every concrete
fallback/domain/origin state contributes its loaded includes and trace sources,
defaults, use positions, closure, and generated prerequisites. A newly loaded
source or newly observed finite domain is evaluated under the exact parent
assignment context, so nested `MODE` -> `DEP` -> include selectors cannot hide
behind fallback-only parsing. Branch-only undeclared or graph-shaping symbolic
selectors reject immediately; branch-only recipe symbolic inputs contribute
authentic recipe-only census evidence, and recipe-only finite domains remain in
the observed census without spawning closure-expansion variants.
Scheduling retains every other assignment and origin in the state, including
tracked fallbacks and singleton explicit domains. Equal literal values do not
prove independence: joint command-line/environment assignments can change
`origin`, `flavor`, precedence and conditional behavior. Baseline plus one
singleton per input is not complete domain evidence. Required combinations
must fit the unchanged 512-context/shared-state and planned-byte bounds or the
whole plan refuses. There is no name-based independence shortcut or quota
increase; scalable collapse would require a separate source/phase-derived
non-interference proof. Complete 112-domain report/resource fit remains an
independent acceptance requirement, not an inference from smaller controls.

`GraphSemanticApiTests` and `OriginalTemplateApiTests` exercise these source,
template, planner and selector contracts with inert observations and actual
API behavior. The neighboring named native controls cover include remakes,
paired-dollar reads and joint domain states against GNU Make. Neither inert
observations nor copied original template construction certify a native
archive or complete-report resource fit. These controls extend
**TC-WORKFLOW-GATE-OWNERSHIP-001** and its existing probe-budget boundary,
not a new tester registry. They change no game/save/locale/ROM/RAM behavior,
containment policy, source custody or release profile.

Tool defaults such as `MODERN_SIZE` are sealed as ambient recipe-only values:
their actual values affect recipe authority, but neither graph selection nor
new executable/source admission is granted by that classification.
Its focused regression retains the actual conditional declarations and recipe
environment consumer in a source-faithful unit slice with explicit fixture
parent inputs and genuine original-input evidence. The consumer command is
unchanged, while a bounded same-named test module observes its environment;
it does not run the real ARM review suite or selected size executable.
Both toolchain-root outcomes and environment-default precedence are observed,
with seal, graph-use and unsealed-neighbor negatives. This is not standalone
semantic analysis of the whole modern.mk file.

Likewise, a constant-true include around an existing rule template is a
supported positive. Ordinary generated C/ARM objects and native/planned
graphs must agree with the unconditional include. A genuinely effectful
conditional remains an original-source rejection even when GNU Make takes
that branch. These evidence fixtures add no producer permissions, predicate
exceptions, resource allowances or broader repository-acceptance claims.
Definition-dependency expansion is
scoped to the authoritative sources GNU Make actually loaded, so an alternate
branch cannot backfill an unobserved selector into another branch's census.
Process-environment variants are likewise driven by authentic observations:
only names that the loaded sources treat as ambient defaults or actually
undefined authority spawn environment-origin graph variants; explicit
Makefile-assigned graph selectors do not.
State, context, source, domain, subprocess and byte bounds fail closed across
the entire report. Each target remains a standalone native Make goal.
One-variable domain observations extend their actual parent context when a
branch reveals a new domain or fallback. No combined goal is attributed to a
different target, no per-target deadline is restarted, and no registry entry
is backfilled as an observed domain.

Candidate command regexes are compiled and matched in a fixed isolated Python
operation launched through the existing `ProbeBudget.run` watchdog, never by
the trusted report thread's backtracking engine. Candidate JSON-schema pattern
validation uses the same bounded operation, including recursive schema checks.
The operation retains Python `re` syntax and the original search/fullmatch and
DOTALL semantics. It applies the existing address-space bound before parsing
inputs or compiling patterns and retains the report's original deadline,
launch/input/output budgets and owned cleanup. No engine, dependency, dialect,
service or numeric allowance is added.
The caller first validates encoded JSON against the explicit 1 MiB request
maximum and any stricter file/pending bound, then gives the worker the actual
wire length as its input ceiling. Pattern batches retain that same 1 MiB
maximum; the worker independently rejects larger wire or decoded declarations.
Increasing a cumulative pending allowance cannot widen those leaf limits.
A cumulative pending allowance is not an allocation request: small
messages must not reserve the report-wide traffic budget before parsing.
Repeated pattern batches use lossless zlib transport only when it is smaller
than the identity representation. The original decoded JSON still satisfies
the same per-request bounds, the actual wire length bounds the stdin read,
and decoding is limited to the declared original length plus one byte before
requiring exact length, complete stream and no trailing data. Regex/schema
execution sees every original byte; the existing pending ledger charges the
bytes actually transmitted, with no refunds or omitted observations.
The worker executes from this same verified module file rather than repeating
its program text in every argv; isolated startup and the pre-parse memory
limit remain in force.

Successful worker responses have exact envelopes: `fullmatch` contains only
`ok` and `indices`; compile/schema responses contain only `ok`. Unknown fields
reject before indices can enter the match cache, fail the shared report
budget, and cannot be reused for a later match.

All command patterns are evaluated as one batch for a concrete command; exact
completed match results are reused only within that matcher/report lifetime.
Repeating a cached match after deadline/failure/closure still fails. Metadata
sections keep the existing selected-view reuse, with compilation validation
performed once for that metadata object. Invalid patterns, excessive input,
resource exhaustion and interrupted work fail closed. The tester case observes
matching start for its benign catastrophic-backtracking negative rather than
accepting a missing fixture or pre-match error as timeout evidence.

This means GNU Make itself owns conditionals, `eval`, pattern/static-pattern
resolution, `define`/`call`, target-specific and inherited values, `${NAME}`,
one-character `$C`, automatic variables, secondary expansion, assignment
flavors/modifiers, and include rebuilding. Tests compare positive behavior to
direct GNU Make and freeze alternate `MODE=two`, `$(eval $(RULE))`, concrete
`%.out: %.in` stems, target-local prerequisites, and expanded `$@`/`$<`
recipes. A literal missing prerequisite and active `$(error)` surface the real
Make failure rather than becoming metadata.
Each evidence target is invoked as the sole requested goal for fallback and
every domain/origin variant. No combined `MAKECMDGOALS` result is attributed
to another target; target order and set iteration therefore cannot change a
record.

GNU Make can execute parse-time `$(shell)`, `!=` and makefile-remake recipes.
The foundation's native dispatch intercepts those operations without replacing
Make-visible `SHELL`/argv semantics. A graph command must match exactly one
sealed domain and execute through its public `Command`/native/dependency action.
Only actual returned bytes and declared outputs can reach replay. Unknown
commands, source/consumption mismatches, nonconvergence or unavailable
confinement reject. The graph has no control-channel implementation of its own.

Generated-data path classification likewise does not import the base
`scripts.generated_data.registry` into the trusted reporter. A small trusted
probe executes the exact candidate registry in a separate credential-free,
networkless, read-only-tree process with only isolated command scratch. The
reporter accepts only bounded UTF-8 JSON with the closed typed record schema,
sorted unique names/dependencies, valid versions, and confined tracked paths.
It binds the candidate registry AST/blob set, exact typed output, launcher, and
Python identity. Newly declared candidate generated paths therefore classify
from candidate authority, while candidate code cannot mutate or enter the
trusted gate process.

Every Make include observed by GNU Make must be the primary `Makefile`, a
tracked regular `.mk`, an exact gitlink-contained file, the trusted probe
control file, or a canonical regular descendant of the generated `build/`
overlay. Include spelling is normalized against conceptual `/repo` before
classification; dot/dot-dot, repeated or encoded separators, absolute/dynamic
aliases, missing paths, and any symlink component reject. Generated asset
discovery and manifest includes begin from an empty overlay and are rebuilt
only through registered confined commands; no live source file is written.

The public boundary is the existing standalone Python entry, started with
`-I -S -B` before any Make process. It strips `MAKEFILES`, `MAKEFLAGS`,
`GNUMAKEFLAGS`, `MAKEOVERRIDES`, `MFLAGS`, `BASH_ENV`, `ENV` and `GIT_*`;
GNU Make options such as `--eval` are not reporter arguments. The root
Makefile retains only a convenience path for a trusted invocation. Its sole-goal,
override and dry-run checks operate after Make startup and bypass normal build
includes; they cannot undo a preload or an evaluation option already executed
by GNU Make. The tests demonstrate that difference with real file/preload and
dry-run effects, not a source-spelling assertion.

Candidate CI does not use these candidate-authored modules as its own trust
root. On pull requests, `ownership-tests` first checks the exact GitHub PR-base
commit for only the stable bootstrap sentinels needed to distinguish
no-authority, foundation-only, and verifier-owned BASE states. When the BASE
already carries `scripts/validation_ownership/ci_verifier.py`, the hosted step
archives that exact BASE and lets its own verifier package validate the
complete authority set. Newer candidate-only runtime helpers therefore do not
become preflight requirements for older exact bases. When the verifier is present, it creates an
unpredictable mode-`0700` directory under the lstat-checked GitHub runner
temporary root, records its device/inode identity, and archives the complete
clean base tree there. It never removes or creates a verifier staging path
through the candidate checkout; base and candidate Make/registry probes use a
mode-`0700` runtime child under the same external trusted root, and cleanup
removes only that unchanged external identity. CI starts the extracted
`ci_verifier.py` directly with `-I -S -B`, before any Make invocation.
The root Make target remains a trusted-invocation convenience only.
The standalone base verifier verifies every
staged verifier package file and every loaded transitive `scripts.*` module
against independently selected immutable source, excludes the candidate
checkout from `sys.path`, and reads CURRENT and BASE through separate real
public views. There is no hybrid loader overwriting candidate entry identities
with BASE bytes. Every managed mode compares the union of candidate and
selected-source verifier namespaces, including new files, types and modes.
Actual transitive modules are bound before and after execution. Candidate
modifications to `reporter.py`, `make_probe.py`, the interceptor, or their tests
must match the independently selected source rather than merely being reported.
After trusted validation of both exact-base and
candidate graphs, the verifier resolves every independent-oracle probe,
requires byte-identical `(edge_type, evidence_id)` selections, compares the
resolved base/candidate authority fingerprints, and intersects trusted
graph-edge invalidation with the oracle-backed edge set. Every graph surface
and every non-dependency owned edge must be represented by a real tracked-path
probe; changed dependency or other unrepresented edge authority fails rather
than bypassing comparison. Any authority target,
gate, Make target/command/probe, workflow step, or fingerprint redirect on an
oracle-backed edge therefore rejects even when stable IDs and pairs remain.
Normalized unrelated workflow or Make semantics do not invalidate those
edges.
Before its first direct Git command, the hosted step unsets the exact ten
path-bearing Git redirects while retaining the explicit no-config,
no-replacement, and no-lazy-fetch settings. Empty or hostile inherited
`GIT_DIR`, work-tree, common-dir, index, namespace, object, replace-ref,
ceiling, exec-path, and alternate-object variables therefore cannot redirect
or break candidate/base identity checks.
### Coordinator-owned review and capture

Candidate YAML is redundant drift protection, not the authority to decide
whether verification ran. `coordinator_capture.validate_handoff` requires the
existing #178 `coordinator-check` assignment and always calls the real
standalone verifier through `capture_check`/`trusted_executor` before managed
admission. The coordinator owns the trusted source/root, actual PR BASE,
candidate/worktree and expected mode outside candidate control. Every
`trusted_executor` invocation, including coordinator-local capture that bypasses
the outer handoff wrapper, binds the registered assignment's BASE, resolved
owned worktree, exact candidate/result SHA and required-check definition before
execution and again before crediting its result. These checks apply equally
to exact-BASE and foundation-introduction modes. A verifier cannot use HEAD as
its own BASE while the captured record labels another parent. Missing or changed
bindings produce no successful capture or managed readiness.

For a reviewed
graph evolution, `qualify_reviewed_evolution` first consumes the coordinator's
actual `ReviewSession` task result and immutable `ReviewTools` tester-case
binding. The completed read-only reviewer must be independent of the
coordinator/implementer, and its exact scope binds repository/PR/BASE/head,
worktree identity, reviewed checker revision and required paths, changed tracked
paths, invalidated relationships, and every affected consumer. The review
session uses exactly four subjects under the unchanged bound: the exact
checker revision plus domain-separated SHA-256 identities of the canonical
complete path, edge-ID, and affected-consumer sets. The full sorted arrays
remain explicit in the qualification and verifier selection; the digests only
give the bounded review scope an identity, not source-content ledgers,
truncation, sampling, or independent authority. Paths are capped at the single
review's 200-file capacity before launch; edge/consumer arrays remain 256 and
the subject cap remains 40. Every path needs actual read coverage from the
same immutable root/BASE/head pair through the same `ReviewTools.model`
that created the session. Qualification invokes the shared
`require_candidate_path_coverage` over unfiltered `candidate_changes`.
The declared path tuple must first equal the complete immutable BASE-to-head
change set, including added, modified, deleted and mode-only paths. A reviewer
reading every declared path cannot qualify a coherently narrowed declaration;
even reading the entire change set does not repair an incomplete explicit
scope. The same complete requirements object is used for actual read coverage:
added/modified
paths need head reads, deleted paths need base blobs, and mode-only changes
need both sides. Empty or unrelated read sets, wrong roots and runtime file
counts cannot substitute. The same builder validates the
live session/report/lease/ownership scope and the persisted record. That
scope is delivered before review, not reconstructed from hashes afterward:
`reviewed_evolution_context` supplies the complete explicit selection and
repository/PR/BASE/head/worktree/checker identity to `ReviewSession.begin`.
The existing request-byte bound applies, and the native task observation must
carry the same dispatched context before it can become the immutable lease/
report binding. A missing or different context cannot qualify any scope. That
qualification is stored with `candidate.local_validation`, joined back to the
actual candidate record and capture assignment, and included in the generated
`ownership-reviewed-*` evidence ID. A candidate field, copied result, mode
claim, matching SHA, or freshly archived candidate checker cannot construct
it. Changing the candidate, checker, scope, task observation, or worktree
invalidates the old local capture. The actual
bounded process result, PID, exit and RSS are retained by the existing handoff
contract. Missing, stale, failed or mismatched captures and candidate pass
labels cannot grant acceptance.

The standalone verifier independently rederives that complete change set
before staging. This remains a separate defense: rejecting incomplete scope
when qualification is created prevents a misleading qualification that the
mandatory verifier could not consume. Source-faithful unit fixtures use the
existing in-memory review runtime, not a new provider allocation or actual
current-candidate/H1 qualification. Foundation-introduction is unchanged.

A base with no authority remains `bootstrap-not-authoritative`/`authority:none`;
exit0 is not managed exact-base acceptance. A complete foundation-only BASE is
the explicit `foundation-introduction` transition and needs independently
selected immutable graph-verifier source. It reports `explicit-introduction`,
not a fabricated BASE verifier or exact-base proof. Partial graph authority
fails instead of downgrading. A complete graph BASE uses `exact-base-pinned`
and exact BASE verifier source. A coordinator-qualified graph update may use
`reviewed-evolution`: the verifier comes from the independently reviewed
revision selected by the qualification, not from candidate/revision equality.
Code-only verifier evolution is also explicit: `trusted_source_changes`
records the actual selected-source/BASE difference. Exact changed-path reads
remain mandatory; edge and consumer scopes are both empty only when graph
authority is unchanged. An evolution with neither a graph-authority change nor
a trusted-source change rejects. No dummy graph edit or unexecuted future
verifier can manufacture acceptance.
BASE and candidate each validate against their own immutable schema, oracle,
graph and model. The shared document-aware authority comparison supplies
schema/oracle/authority invalidation; dependency edges are covered only when
both endpoint surfaces have complete oracle probes. The independently
qualified path, edge and affected-consumer scopes must match the actual Git
diff and invalidation exactly. Unreviewed exact-base retargets still fail, and
the PR-only hosted invocation remains strict: it accepts no candidate-supplied
review mode or scope, and its failed exact-base evolution observation is not
rewritten. The managed input-free workflow dispatch keeps that PR-only step
not-applicable; neither the dispatch event nor N/A status is authority. Final
review-first assessment, reservation, dispatch and full-run admission require
the same live qualification that produced the local capture. Production
refresh passes that object through `assess_observed(..., local_qualification=...)`;
the persisted record is only the bound comparison target and cannot recreate qualification.
If a live qualification is supplied but the candidate has no coordinator-owned
`local_validation`, delegated handoff readiness cannot substitute for the
qualified ownership capture. Ordinary delegated candidates with no reviewed
qualification retain their existing readiness path.
Reviewed capture assignments must contain the exact qualification record;
the ordinary delegation schema cannot strip it into unqualified acceptance.
Legacy delegated ownership checks bearing a reviewed evidence identity are
held from delegated-only readiness even if the live argument is omitted.
That identity can block a stale record, never grant review authority.

The exact-base verifier owns one private `.validation-ownership-runtime`
workspace inside its immutable trusted source root. Its captured parent,
device, inode, owner and mode identity is removed only after the shared budget,
owned processes and `ProbeSession` have terminated. Successful and failed
captures therefore leave the trusted tree reusable. A pre-existing workspace,
swapped directory or symlink, changed parent identity, or nonempty residual
workspace rejects without deleting the unknown path or trusted source tree.
Cleanup failure rejects success; when validation already failed, the original
failure remains primary and the cleanup failure is reported alongside it.

Git remains the identity authority; no source ledger, new service, signer,
privileged PR event or human approval is introduced.
Before/after worktree checks retain complete porcelain-v1/NUL status, including
staged, unstaged, deleted and untracked paths. They disable only Git's optional
parallel index preload so its helper-thread allocation does not prevent a
bounded status read; no status entries, errors or repository checks are hidden.

Domain-separated seals continue to cover the strict schema, probe oracle,
complete graph, resolved edges, and live evidence-authority fingerprints.
Graph-edge comparison remains the review-invalidation source; the graph still
does not execute or narrow any selected validation gate.

## Tester case and compatibility

[`TC-WORKFLOW-GATE-OWNERSHIP-001`](test-cases/workflow-governance.md#tc-workflow-gate-ownership-001-resolve-every-admitted-path-to-complete-validation-ownership)
owns representative resolution, whole-repository coverage, every edge-family
mutation/deletion, stale authority, lifecycle, clean execution, and reporting
controls.

There is no feature flag, save migration, resource allocation, generated game
content change, localization payload change, gameplay/runtime behavior change,
ABI change, or archival-lane behavior change. Reverting the dedicated commit
removes the reporting capability and leaves all broader validation mandatory.
