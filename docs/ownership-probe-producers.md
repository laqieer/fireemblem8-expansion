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
invalid output declarations reject. There is no V/R API or implicit runtime
input grant in this extension. D's
[dependency-only compiler command](ownership-probe-dependencies.md) reuses this
same output/publication contract.

Registration is applied at actual native dispatch; it does not change Make's
executable lookup. A missing direct recipe executable such as `tools/native`
can fail with `ENOENT` before any producer request. The original shell-dispatched
form `tools/native;` can reach the interceptor, but the framework does not
append that syntax, synthesize executable metadata, or install the native image
into Make. An actual consumer needing an unresolved direct executable remains
a precise unsupported case until it has an accepted ordinary-Make adaptation.

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

Make then loads the real include or performs its own re-exec. The persistent
source view is never deleted/recreated between observation and use. A later
metadata reader sees the actual published inode/link count/timestamps; cached
pure readers still use the core's complete current metadata revalidator.

There is no speculative empty-return pass. Unreachable producers do not run.
All actually dispatched work remains authorized, charged and included in the
one native run's provenance; a later invalid request fails the entire report.
Final native writes must match the supervisor's recorded bytes and each
fulfilled receipt exactly.

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

The same deadline, launches/states, pending count, descendants, syscalls,
observations, creation limits and all byte budgets remain. Callback frames,
declarations, output, mapping, cache and publication data spend their existing
categories. No category refund/reset, second budget, cap increase or calibration
is part of this implementation.

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
all observations, not on restoring old directory names alone. The CURRENT/
BASE portions of shared V/P cases remain allocated to V and combined-extension
integration; this module does not add `select_view` or storage-reuse APIs.

Full P acceptance remains separate from implementation checkpoints and from
the unresolved direct-executable case above. No broad current/BASE/domain
graph, public #180 completion or raised-budget diagnostic is claimed.

P depends on #206. V/#226 and R/#227 are independent core children; D/#228
depends on P, not V/R. No gameplay, save/config, locale, generated game content,
modern/archival profile, package, privilege or Build-job change is introduced.
Main owns publication, independent review and all remote delivery gates.
