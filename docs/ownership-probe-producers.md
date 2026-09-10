# Live ownership-probe producers

Issue [#225](https://github.com/laqieer/fireemblem8-expansion/issues/225)
extends the [confined foundation](ownership-probe-foundation.md) with one
live native Make invocation per query, isolated registered producers and
validated publication. Nested queries share the active generated view without
reconstructing it. This is not complete #180 integration or a full-root/domain/
calibration result.

## Typed producer results

`Command` retains exact argv, code, sources and directory permissions. The
extension adds an optional session-issued `native_tool` and explicit relative
`outputs`. `ProcessOutput.generated` contains captured `GeneratedFile` values:
path, actual bytes and ordinary file mode. Its `input_identities` binds the
resolved code/source paths, modes and bytes at actual execution; native build
inputs and Make provenance use that receipt, not a later view's hashes.

```python
producer = Command(
    ("/usr/bin/python3", "/repo/producer.py", "/work/generated.mk"),
    code=("producer.py",), sources=("choice.txt",),
    outputs=("generated.mk",),
)
observed = probe.make(
    "all", variables=("MAKEFILE_LIST", "MAKE_RESTARTS"),
    commands={"python3 producer.py generated.mk": producer},
)
```

Native registration uses the original Make command as its key and an issued
tool in the same typed command:

```python
tool = probe.compile_native(("tools/tool.c",), headers=("include/tool.h",))
producer = Command(
    ("/native/tool", "input.bin", "/work/result.bin"),
    native_tool=tool, sources=("input.bin",), outputs=("result.bin",),
)
```

The tool still executes only in a channel-free native capsule, never in Make.
Changed/foreign handles, unsupported argv, incomplete source consumption and
invalid output declarations reject. The producer extension grants no optional
runtime inputs or dependency-compiler authority. The independently merged view
selector is exercised with P below. D's separately allocated
[dependency-only compiler command](ownership-probe-dependencies.md) reuses this
same output/publication contract without requiring that selector.

Registration is applied at actual native dispatch; it does not change Make's
executable lookup. The repository's linker discovery and link recipe explicitly
name `$(PYTHON)`. Its related gbagfx/PAL2GBAPAL recipes explicitly use shell
dispatch. These are source-authored adaptations: ordinary program arguments,
output bytes, error status and cleanup behavior remain covered by real
controls. The probe never appends syntax, synthesizes executable metadata or
installs a native image into Make. Graphics recipes remain metadata-only in
the report; their ordinary build outputs are not claimed as probe-produced.

The original direct Python entry demonstrated a different failure from a
missing program: Make successfully statted an executable source, received
`EACCES` for `X_OK` from the noexec view, then accepted empty `$(shell)` output.
That exact failed Make-mode executable lookup now terminates the probe through
the existing policy error path. Ordinary absence, nonexecutable files and
command/helper metadata probes are not blanket-rejected. Missing direct
executables still do not acquire authority merely by appearing in a registry.

An execute bit for another permission class is not proof that this caller can
execute. For example, an owner of a `0641` generated file is denied just as
for `0644`; that ordinary denial must not be blamed on noexec. The guard reads
the stopped Make process's kernel-reported identities, supplementary groups
and capabilities. Plain access uses real IDs; `AT_EACCESS` uses the actual
filesystem IDs. Owner permission takes precedence, then matching group, then
other. The existing capability-free state and relevant namespace ID mappings
are checked, not assumed. A non-owner extended ACL or unrepresentable/
capability-bearing state rejects as unsupported instead of guessing.
No credential, mode, access result, mount or metadata buffer is changed.

The independently delivered R layer composes through explicit `runtime_files`;
the default remains empty. Its captured runtime identity participates in the
live Make execution digest, and full optional metadata uses the same
revalidator. A generated include may query an explicitly captured runtime path
after a real restart, but cannot acquire an unrequested spelling, image read
or execution grant. An ordinary env recipe remains metadata-only alongside
live producers. Runtime backing and source publications retain separate
ownership and are cleaned by the same report lifetime.
If the captured runtime makes an include-search directory present, the caller
must also admit the exact legitimate search input or its actual absence; the
producer contract does not grant a whole runtime include tree implicitly.

## Shared source-only Python producer commands

Issue [#238](https://github.com/laqieer/fireemblem8-expansion/issues/238)
extracts the reusable source-only Python command helpers from downstream graph
work into `scripts/validation_ownership/python_commands.py`. The public seam
keeps ordinary Python import semantics on the selected `/repo` tree:

```python
from scripts.validation_ownership.python_commands import (
    python_command,
    directory_python_command,
    generated_dependency_command,
)
```

- `python_command(...)` returns a typed `Command` with the exact imported
  closure of the declared Python body and code roots, including repository
  packages outside `scripts/`. A standard-library-only body receives no
  implicit repository-root enumeration or repository import path.
- `directory_python_command(...)` adds the declared source-directory ancestors
  needed for metadata/enumeration without widening file-content admission.
  `directories=(".",)` explicitly requests the existing root marker;
  noncanonical aliases remain rejected.
- `generated_dependency_command(...)` adapts the three audited generated-data
  dependency CLIs (`chapterobjectives`, `autoplaystrategies`, `eventlists`)
  into one typed producer command using the modules' real `source_paths`,
  `collect_input_paths` and `render_depfile`/writer contracts. The helper keeps
  the existing source-selection and bundle-support capsules, then runs one final
  output-producing command that re-checks the module's actual
  `collect_input_paths(...)` result against the caller-derived exact admitted
  set before rendering. It does not launch a second cold collector command.

The helper does **not** add a second importer, import mode or package shim.
Normal namespace-package behavior is preserved: with a complete gitlink-aware
capture, `import scripts` keeps Python's `NamespaceLoader`,
`__spec__.origin is None`, and `/repo/scripts` in `__path__`. A raw immutable
capture missing gitlink admission still fails at the actual `/repo`
enumeration boundary instead of falling back to synthetic package objects.

Generated-dependency registrations validate their exact named option set before
lookup, order selector arguments by the declared module signature rather than
the caller's CLI order, and keep every support/discovery step inside the
existing selected-view `ProbeSession` cache. Registry commands seed code
authority from the registry's actual captured import closure, not entire
implementation/test directory trees. Directory-backed selection uses
the schema's public `source_paths` API; concrete depfile bytes and output
publication come from the real module render/writer behavior, not guessed
filenames or fabricated empty output. The same API is reusable for asset
discovery/publication, registry source selection and later graph adoption
without importing graph-specific code into the foundation.
For chapterobjectives/autoplay, the support capsule also returns the actual
implementation-module `.py` inputs discovered through
`chapterobjectives.deps._implementation_module_paths()` after the admitted
target deps module and bundle dependency modules are normally imported in the
selected snapshot. Static code closure remains the admission envelope; the
actual implementation-module paths remain ordinary collected inputs that must
match before output is published.
Each command writes the real renderer's bytes into its fresh private output
directory; the existing native publication protocol owns installation into
Make's source view. The ordinary CLI writer's temporary-file rename is not
invoked inside the command capsule, where directory-entry relocation remains
forbidden. No existing-output read error is swallowed by this adapter.

## One live native execution

The native observer preserves Make's original target, arguments, variables,
origins, shell flags and dispatch classification. Ordinary recipes remain
metadata-only; value-bearing, recursive and remake dispatches require actual
registered results. No dry-run flag, goal/PATH rewrite or synthetic restart
count is introduced.

The static interceptor submits a bounded original argv/hash/count frame at an
authenticated private syscall boundary. The supervisor validates it before
notifying the driver, queues only bounded live requests and parks that helper.
A request has no result slot yet; its request-frame count is zero. The
supervisor assigns a monotonically increasing per-call sequence.

The existing driver resolves the exact registration and executes it through
the existing isolated command/native path. Only after successful source
accounting and output capture does it prepare a request-specific private result
slot. The supervisor validates the reply's scope, sequence, slot, producer,
stdout and output declarations, then publishes into the still-live Make view.
The helper receives only its fulfilled slot and emits the authentic stdout and
completed native event. Final event counts bind to their individual receipt,
not a later table size.

A query with no `commands` registry and no inherited generated context retains
the live handshake but has no publication grant. Its private `reserved_paths`
field is `null`, not an empty list of source reservations: any attempted
publication rejects. This avoids repeatedly copying a complete immutable
inventory that the query cannot use. A registered or inherited publication
scope still carries every original reserved path, including absent sources
and empty gitlinks.

Make then loads the real include or performs its own re-exec. The persistent
source view is never deleted/recreated between observation and use. A later
metadata reader sees the actual published inode/link count/timestamps; cached
pure readers still use the core's complete current metadata revalidator.

There is no speculative empty-return pass. Unreachable producers do not run.
All actually dispatched work remains authorized, charged and included in the
one native run's provenance; a later invalid request fails the entire report.
Each helper still emits one complete frame with one `O_APPEND` write. The
physical append stream is the authoritative event order: complete frames are
parsed through the shared wire decoder, matched one-to-one by exact bytes and
multiplicity against successful full-write observations, then validated in
physical order against their fulfilled receipt slots and commands. Ptrace exit
notifications may arrive in another order without changing the append order.
Missing, extra, duplicate, partial, corrupt or forged frames and observations
still reject. Identical frame bytes cannot bypass the unique receipt-slot
check. Event, control, raw-observation, cache and replay accounting and limits
are unchanged.

## Isolation and the two-phase boundary

The private per-call socket is between the driver and supervisor, not guest
stdin, stdout or the watchdog lifetime pipe. The existing driver listens only
for that invocation in an owned mode-0700 report directory outside guest
mounts; the mode-0600 Unix socket is removed by the same report cleanup. This
is an ephemeral rendezvous, not a service or daemon. The supervisor opens it
after the existing sudo/unshare boundary, so no callback descriptor has to
survive sudo's descriptor-closing exec. No closefrom override or sudoers
change is required. Nofollow directory/socket identities and kernel peer
credentials bind the connection; the driver pins the connector's ancestry to
its own live watchdog launch. A foreign connection fails, rather than causing
another connection attempt. Short dirfd-relative `/proc/self/fd` paths avoid
Unix socket pathname truncation without moving the channel outside owned
storage.

Every candidate bootstrap closes nonstandard descriptors before exec; the
supervisor checks the actual initial kernel FD set. The producer cannot read
callback controls, choose a host callback, or acquire producer authority by
issuing a private marker. The watchdog remains solely the lifetime/stdio
owner and reaper; it neither forwards nor interprets producer traffic.

The producer sees readonly/noexec `/repo` and writable private `/work`.
Publication follows successful exit and complete bounded capture. Writing
`/work/data/new` does not make that name visible through `/repo/data` inside
the producer. This is an explicit two-phase result contract, not unrestricted
same-directory read-own-write behavior. A required producer needing the latter
is a hold or needs an existing logical-output adaptation with real evidence;
source aliases and returned metadata are never fabricated.

Every original admitted path/pin remains reserved even when absent in the
snapshot. Outputs cannot replace those names or enter their reserved
namespaces. Missing, undeclared, nonregular, escaping, oversized or conflicting
results reject. Publication uses bounded nofollow descriptor-relative handling
and retains the runner-ownership rule for new objects on the sudo route.
Only a normalized logical producer can replace its own generated output.

`MakeObservation.generated` retains the final confirmed `GeneratedFile`
objects, including their actual bytes and modes, after publication cleanup.
It is empty for a query with no published output. Replacements retain the last
confirmed version; nested observations retain the outputs visible at their
own completion. These immutable results reuse already charged output objects,
not a second execution, publisher or persistent source-worktree artifact.

Output-producing invocations execute genuinely for every actual dispatch.
Identical storage/provenance can deduplicate, but that does not erase a call or
publication effect. Pure reuse remains subject to complete current observations.
Resolved source membership and declared published code/source bytes and modes
also bind the cache key: a reader using only `open`/`read` must not reuse old
output after its generated input or code is replaced, even when it made no
metadata syscall on that input. Explicitly declared generated code is admitted
only while its actual published object exists; a cached result cannot extend
that admission past cleanup. Unchanged inputs retain valid reuse, including
unchanged glob membership. Identity storage and comparison spend the existing
cache/control budgets. A cache hit retains its original execution receipt;
current input hashes never relabel earlier stdout.
The kernel's metadata on published objects is authoritative, not metadata
copied from private output files.

## Nested queries and publication lifetime

A trusted registration resolver can call the same session's `make` API while
its parent query is parked. These are separate real Make observations in one
current source view, not independent V-style views and not a new recursive
command executor. The caller retains the nested observation for its own
consumer logic and still returns a typed registration with genuine command
output. No shell output is fabricated from a graph or substituted observation.

Completed child outputs remain present until the outermost active Make query
finishes. Removing them when the child returns would invalidate the context
the parent is about to observe. Independent later queries start without those
generated files; cached results must pass current input/metadata validation
again. An explicitly selected generated Makefile can be an entry point while
its publication is live, but loses admission after outer cleanup.

The existing publication ownership map follows this lifetime. A child
supervisor receives the current completed bindings. Before its parent resumes,
changed bindings cross the existing protected result slot as a bounded
publication transfer, bound to that reply by a checksum. Each binding contains
the path, normalized producer, mode, size and actual content digest. The
supervisor checks declarations, original-source/pin exclusion, ownership and
the real regular file through its readonly/noexec source mount with nofollow
walks. These reads must not change atime or replace source objects. Invalid,
missing, conflicting or changed bindings reject.

Adoption updates ownership only: it neither republishes files nor refunds or
recharges their earlier creation/write effects. Its actual verification reads
and retained mapping/version data spend the existing control/cache/mapping
budgets. All parked ancestors keep their process and funded-VM reservations.
Same-producer replacement works across nested queries; another producer cannot
take over a child's or parent's outputs. Failure, parent lifetime loss or
interruption tears down the entire active scope with the existing sole reaper.
The producer path shares the same closed metadata transport as ordinary
commands and Make: supervisor reports carry one
`vo-metadata-frame`/`zlib-base64` envelope whose decoded bytes are the
unchanged replay frame, while result slots, `stdout_sha256`, adoption hashes
and publication ownership semantics remain unchanged.

## One resource ledger and lifetime

Before nested work, the supervisor settles actual monotonic counter deltas.
Already admitted filesystem operations finish at supervised stops; blocked
nonmutating waits/pipes remain parked rather than waiting for Make to exit.
No new syscall is admitted while the producer callback owns the parked phase.
A kernel-authenticated vfork parent is also waiting-only after its child's
creation event: it may be suspended in `clone`, `vfork` or `clone3` while that
child is parked before exec. Waiting for the parent syscall to return would
deadlock. The child and all funded credits remain reserved; unrelated creation
or filesystem operations are not exempted from settlement.

All parked live processes, pending creation reservations and funded
`RLIMIT_AS` credits remain reserved. The nested capsule receives only residual
global live-process/VM capacity. On completion, its actual work reduces the
outer remaining authorization before resumption. Final settlement charges
only the unsent delta, not counters already settled at earlier requests.

A cold shell producer needs capacity for Make, its parked interceptor and the
producer. A two-live-process report cannot launch that third process. It can
consume a compatible pure result that was genuinely executed before Make, with
that earlier work still charged. The lowered view regression exercises that
composition and repeated native queries before exhausting its unchanged
descendant allowance; the cold-producer negative retains the same two-slot
denial.

The same deadline, launches/states, pending count, descendants, syscalls,
observations, creation limits and all byte budgets remain. Callback frames,
declarations, output, mapping, cache and publication data spend their existing
categories. No category refund/reset, second budget, cap increase or calibration
is part of this implementation. The metadata transport savings term is the
old complete report minus the new report, one charged decoded frame and the
additional encoded-payload retention reservation at the parent. Both
reservations precede decoding; fixed streaming scratch is not counted as a
saving. Raw metadata observation, cached legacy records and helper replay
costs do not move or shrink.

Malformed, foreign, duplicate, stale or out-of-order traffic, premature EOF,
unknown slots, parked-process death, callback failure and interruption are terminal.
A partially sent reply is never retried as another effectful request. Nested
work monitors its ancestor channel, so losing the outer lifetime interrupts
the producer rather than leaving it alive. The supervisor also monitors
unsolicited traffic between requests while Make continues. At completion it
waits for the driver's write-half EOF before accepting the final report: a
separately delivered last reply or even one partial framing byte cannot hide
after the final exchange. Unexpected trailing input is drained only within the
existing byte/deadline bounds to preserve the failed report and its final
counter delta; it still rejects. The driver rejects extra completion traffic,
binds the completion notification to the actual transcript and waits for
supervisor EOF after the report is written. This terminal barrier closes both
directions without retrying work. Existing pidfd/watchdog/sole-reaper
cleanup removes owned channels, private roots and generated paths.

## Evidence and remaining scope

The deterministic procedure is
[`TC-WORKFLOW-PROBE-PRODUCER-001`](test-cases/workflow-governance.md#tc-workflow-probe-producer-001-preserve-live-producer-context-and-native-remakes).
Foundation, producer and dependency modules belong to the one existing
`extended-host-tests` owner in full Build mode. Metadata-only and review-first
preflight runs skip that owner; their attestations are not full native evidence.
That owner installs the existing libpng/pkg-config and ARM-binutils prerequisites
for its real graphics and linker controls, in addition to the host compiler and
Python environment. No new job or duplicate native execution is introduced.
The suite covers the live producer/include/metadata-reader transition,
authentic one/two restarts, real repository scaninc, aliases and repeated
effects, source/output rejection, request/reply corruption, descriptor/channel
separation, parked death/lifetime loss and residual resources. It also covers
parse-time versus remade includes with the full Make assignment context,
generated native file results, ownership-transfer failure, inherited nested
ownership, readonly adoption and all 13 ordinary file-stat fields.

Core tests whose old implementation required an empty speculative pass are
adapted to ordinary native behavior: an unreachable producer is absent, not
executed to populate a cache. Actual bad dispatched inputs still reject.
Each live request is validated before its execution; completed transcript
corruption is rejected after actual work, not represented as prevalidation of
future unobserved requests. Native command order/bytes and all charges remain.

The unapproved d9 allocation remains the reference for retained requirements,
not evidence of correctness. P retains its native registration, publication,
remake, generated-context and resource/control cases under this live model.
The old speculative-execution assertions are replaced by actual unreachable-
branch nonexecution. Cache reuse after publication/cleanup is conditional on
all observations, not on restoring old directory names alone. The merged V
layer supplies CURRENT/BASE selection and storage reuse. Both
original shared V/P combinations are exercised with one budget, restored
per-view caches/native handles, isolated generated visibility and charged
metadata revalidation. A view cannot be switched while a Make publication
scope is active, even after its last native process exits.

The full-tree CURRENT/BASE pair retains both complete registry queries,
restored CURRENT Make, all actual metadata buffers and default cumulative
limits. Only unused static-query publication authority is omitted; no
observation charge or record is dropped. The active foreign-view-exit fixture
publishes its ready marker by linking an already closed value file, so file
creation alone cannot be mistaken for a completed BASE write.

Route controls distinguish unavailable prerequisites from passing execution.
The same-UID sudo comparison requires user namespaces and skips the whole
comparison when they are unavailable, rather than indexing missing results.
Where the existing privileged namespace route is available, a separate real
sudo/watchdog control observes root supervisor peer credentials, dropped guest
identities, static/live results and summed resource reports. On a host where
user namespaces work, only that preliminary selection is modeled as a denial;
sudo, namespace setup, credential dropping and every capsule still run
genuinely. An unavailable sudo policy or namespace route remains an explicit
unsupported outcome, never a host-security change.
Watchdog status 125 and unexpected launcher faults fail the control; they
cannot be relabeled as an optional permission skip. A driver-UID foreign peer
fails at credentials on the privileged route and at ancestry on the direct
route. Separate same-UID kernel-peer controls prove both a live owned launch
and rejection of another live launch without changing expected credentials.

Full P acceptance remains separate from implementation checkpoints. The
identified original direct consumers have explicit source adaptations; this
does not grant arbitrary unadapted executables or unrestricted same-directory
read-own-publication. No broad current/BASE/domain graph, public #180 completion
or raised-budget diagnostic is claimed.

P depends on #206 and composes with the independently merged V/#226 and R/#227
implementations from PRs #230 and #231. D/#228 depends on P, not V/R.
No gameplay, save/config, locale, generated game content,
modern/archival profile, privilege or Build-job change is introduced.
Main owns publication, independent review and all remote delivery gates.
