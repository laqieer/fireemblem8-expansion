# Disposable issue-180 CI baseline 2

**Non-delivery, diagnostic-only, never merge this branch.** This harness does
not change Build CI, production limits, the graph checkout, approval policy,
H1, publishers or deployments. It does not authorize another local full run.

The only trigger is the first creation push of
`calibration/issue-180-ci-baseline-2` by the repository owner. The workflow
also requires workflow run number **1** and attempt **1**. There is no dispatch,
PR, schedule, retry or continuation route. A failed capability preflight
consumes this attempt; it is not permission to retry or change a guard.

Baseline 1's closed hosted attempt stopped before any graph launch/import/check:
the unchanged lifecycle guard correctly rejected the setuid mount and umount
utilities. This baseline changes only that setup dispatch and its new trigger
identity. It is a separately scoped preparation, not a retry of attempt 1.

Privileged ext4 mount/unmount now uses the ordinary Python runtime under the
same watchdog. The closed helper validates the root-owned image, loop-device
identity and backing-file association, private parent, original mountpoint
identity, and actual mountinfo before fixed `mount(..., "ext4", NOSUID|NODEV)`
or non-lazy `umount2(..., 0)`. It accepts no arbitrary command, filesystem or
mount options. Cleanup inspects actual presence after uncertain setup and
requires the original root-owned mountpoint to reappear before loop detachment.
An ambiguous, replaced or unconfirmed mount preserves its resources.

## Immutable scope

The harness checkout is the actual workflow SHA, one commit directly on
`473c6c22a1ba340429072dac0a187de76eec1cc0`. Only this directory and the new
diagnostic workflow may differ from that base.

The separate candidate checkout is exactly
`3d78dbc26f2df16cb50b0684042d84a98fbc2ea9`, with full Git history and recursively
initialized Git-pinned submodules. BASE is exactly
`473c6c22a1ba340429072dac0a187de76eec1cc0`. No harness file enters that checkout's
captured inventory. Checkouts do not persist credentials.

After actual external containment and benign qualification, the unprivileged
worker imports the pinned candidate and calls its real `graph_report.check`
**once**, with actual Git changed paths, CURRENT, BASE and lifecycle enabled.
No loader, source admission, native command, publication, observation,
receipt, schema/protocol check, replay or accounting operation is skipped.
Successful reports must partition the actual pinned Git inventory, retain
zero oracle false positives/negatives, and include all three removal/restoration
lifecycle proofs. Even that outcome is `diagnostic_only`, never production/H1.
Launch requested, check attempted and check completed are distinct fields.
The pre-call marker is not represented as proof that the check body ran;
completion requires the real returned and validated full report.

## Internal accounting policy

`policy.py` classifies every original `Limits` field. An immutable subclass
outside the candidate replaces only the classified cumulative policy maxima
with the largest positive signed 64-bit accounting value. This is a deliberately
nonbinding accounting sentinel, **not an allocation or a size estimate**.
The combined `total_bytes` policy is included; this is not another pending
ratchet. The original `Limits()` and all original counter methods remain intact.

Retained mixed/structural boundaries include live guests, pending request/
source counts, entries, creation/publication cardinality, per-file/decoded/
message limits, per-process output, and the original guest/regex AS controls.
Thus a retained mixed guard or real algorithm failure may still terminate
the experiment. It must not be bypassed or relabeled as success.

The selected source predates #260. Relaxing its pending partition also removes
its implicit whole-record and aggregate-plan envelope; this is explicitly
**not** the traffic-only preserving policy from that separate issue. Fixed
regex/schema request/wire/decoded leaves stay 1 MiB. No #260 source is edited,
backported or required to deliver before this separately authorized experiment.

Counters remain cumulative actual accounting, not live heap/RSS, and are never
refunded. A read-only sampler copies actual budget counters without invoking or
patching budget operations. Samples are observational, not atomic admission
receipts. Kernel metrics are collected and persisted by the supervisor outside
the worker even if the worker cannot run its sampler.

## External envelope

The privileged supervisor requires the disposable `ubuntu-latest` hosted
Linux x86-64 VM, not a self-hosted runner or job container. It records actual
MemTotal/MemAvailable, every visible current cgroup ancestor's limits/use,
available filesystem capacity, CPU affinity and task availability.

| Boundary | Formula / absolute ceiling |
| --- | --- |
| Cgroup memory | MiB-floor of `min(8 GiB, effective total / 2, effective available - 2 GiB)` |
| Cgroup swap | `memory.swap.max = 0` |
| Group OOM | `memory.oom.group = 1`; workers have `oom_score_adj = 0` |
| Cgroup tasks | `min(256, 32 * available CPUs, effective available tasks - 64)` |
| Writable filesystem | MiB-floor of `min(8 GiB, available disk / 2, available disk - 4 GiB)` |
| Graph deadline | One absolute 3,600-second deadline, shared by budget and independent watchdog/controller |
| Worker stdout + stderr | 16 MiB combined; overflow terminates, never truncates and continues |
| Uploaded artifacts | 32 MiB total; metrics/progress each at most 4 MiB |

Insufficient capacity for at least 1 GiB memory, 1 GiB writable filesystem and
64 tasks fails before execution. Facts/envelope are refreshed after preflight.
The 64 MiB probe filesystem is closed before the fresh graph filesystem is
allocated. No allowance is inferred from incomplete prior graph usage.

The graph runs in its own root-owned cgroup with kernel `memory.max`, swap and
`pids.max` enforcement. The worker moves **itself** into that cgroup before any
candidate import; the supervisor never migrates an unverified numeric PID.
Forks, double forks, new sessions and nested namespaces remain in the cgroup.
Workers cannot write cgroup controls. `cgroup.kill` handles concurrent forks;
emptiness and the original sole-reaper watchdog's termination are required.
The existing host cgroup mount must have `nsdelegate`; the harness never
remounts it to manufacture support. The benign nested-userns probe also tries
to obtain a cgroup-namespace mount and write the **same** memory/PID limit
values. Either the route or those writes must be denied, proving that a new
namespace cannot gain writable containment controls without relaxing a limit.
Kernel `memory.peak` and `pids.peak` are recorded, alongside cumulative `io.stat`
and explicitly labeled sampled peak I/O rates. Kernel memory accounting has
its documented semantics, including possible small transient overages;
it is not a claim about summed RSS or every byte on the host.

Writable `/work`, `/repo/build`, `/tmp`, `/var/tmp`, `/run` and `/dev/shm` all
refer to one fixed-size, allocated ext4 loop filesystem. The backing image and
loop device are invisible to the worker. This bounds retained disk contents,
not cumulative I/O. Only a tiny readonly device filesystem exposes null/zero/
random devices. There is no host home, credential directory, Docker socket,
host proc view or writable sysfs.
Memory-backed filesystems that a permitted user namespace can create remain
charged to its cgroup memory envelope; they do not provide another host-disk
allocation. The owned cgroups are created at the full hosted VM's cgroup root,
with allowances conservatively derived from the runner's effective ancestry.
Delegated/container cgroup views are deliberately unsupported.

The job has a 90-minute outer timeout. The first checkout is bounded to five
minutes; the run refuses setup older than fifteen minutes after the scope
record, and refuses more than five minutes of preflight/setup before starting
the graph. The graph step has a 70-minute timeout, leaving cleanup/upload margin.

## Privilege and namespace boundary

Root is used only by the outer supervisor and trusted namespace setup.
A dedicated, no-home, non-login host UID/GID runs the coordinator. Before its
exec, setup creates private mount/PID/network/IPC/UTS namespaces, a readonly
root/runtime/candidate/harness/proc view, clears groups and all host capabilities,
sets kernel NoNewPrivs, and rearms its parent-death signal. All inherited
non-standard descriptors are closed. The worker receives an exact minimal
environment, no GitHub/Actions token, SSH agent or credential path.
Setup uses a real `pivot_root` and detaches the old root before dropping the
worker identity. A plain chroot is insufficient: it would also make Linux
reject the unchanged nested user-namespace route. Old-root detachment is an
initial isolation operation before candidate imports, not lazy teardown of
unconfirmed running work.

The coordinator deliberately remains a non-root UID in the host **user**
namespace. An outer UID-0-to-non-root user mapping would make the system's
root-owned runtime files appear unmapped and fail the unchanged runtime-owner
checks. Instead, the real existing `unshare --user --map-root-user` route is
qualified inside the outer cage. Every resulting namespace UID 0 maps to the
dedicated non-root host UID. No host-root or privileged-container authority is
given to candidate code.

On an AppArmor-enabled VM, an already-confined supervisor is unsupported.
For an initially unconfined supervisor, a temporary, process-selected
`default_allow` profile adds only `userns,`, following Ubuntu's documented
per-application mechanism. It has no global executable attachment, no sysctl
change, and is removed after confirmed cleanup. Missing ABI/kernel/profile
support or denial of the real nested route is a precise preflight failure.
No global AppArmor disable, permissive container or uncontained fallback exists.
NNP and nosuid runtime mounts prevent a later sudo attempt from elevating.

The candidate checkout's ownership is temporarily assigned to the dedicated
UID before its readonly bind and restored only after termination. No content,
mode or timestamp normalization is used. Root-side Git inspection permits only
the exact managed checkout and captured gitlink paths, not `safe.directory=*`.

## Required hosted preflight

Each benign phase has its own fresh cgroup and original watchdog. Before any
candidate import, real kernel checks establish UID/capabilities/NNP, namespaces,
actual cgroup membership/read-only controller objects, deadline, source/runtime
mounts and writable-device identity. The probes require:

1. Modest allocation, protected writable-open rejection without writing source
   contents, no host listener/network reachability, and the actual nested user
   namespace/proc-mount route.
2. Actual cgroup OOM under a 64 MiB limit; kernel OOM events must corroborate it.
3. Actual fork rejection under an eight-task cgroup; `pids.events` must corroborate it.
4. Actual ENOSPC on a separate 64 MiB filesystem, not a large host-disk fill.
5. Actual external output overflow under a smaller benign stream bound.
6. Deadline and caller-lifetime-pipe closure with a real setsid/double-fork
   descendant. Kernel pidfds establish termination. Lifetime qualification
   cannot credit the outer cleanup kill for a broken watchdog.

Unsupported guards stop the experiment. Kernel cgroup-v2 memory/PID/IO
controllers already enabled at the hosted root, `nsdelegate`, `cgroup.kill`,
`memory.peak`, `pids.peak`, root namespace/loop-mount
setup, pidfds and the exact nested isolation path are platform prerequisites.
Local tests do not claim that these hosted root facilities were exercised.
The harness never enables missing global controllers or remounts cgroupfs as
a fallback; only its newly created cgroups receive limit writes.

## Artifacts, failure and cleanup

Only the six explicitly named JSON/JSONL reports are uploaded, never source,
ROM, scratch, filesystem images or raw candidate stdout/stderr. Parsed progress
and original bounded exception records carry identities/counters, not source
file contents. Unframed output, malformed protocol, oversized required evidence
or an incomplete result fails honestly. Oversized exception messages are
identified by type/size/digest with an explicit evidence-overflow marker.

The existing `lifecycle.py` owns descendant reaping via its caller-owned lifetime
pipe and pidfds. The external supervisor additionally owns cgroup kill, kernel
metrics and the same absolute graph deadline. It preserves the first observed
failure and separately reports cleanup errors. Resources are not unmounted,
removed or identities reused while termination is unconfirmed. A catastrophic
supervisor loss still closes the watchdog pipe; VM disposal is the final
nonpersistent boundary, not proof that cleanup was observed.

## Local preparation and owner trigger

Run only the focused harness controls and lint, not the graph:

```bash
python3 -m unittest scripts.ci_calibration.test_ci_calibration -v
actionlint .github/workflows/issue180-ci-baseline-2.yml
```

Local controls cover parsed workflow/limit scope, admission-before-import,
budget-independent protocol/artifact rejection, non-loading AppArmor syntax,
and actual benign watchdog/output/deadline/escaped-descendant behavior.
Hosted preflight supplies the real privileged cgroup/mount qualification.

After Main reviews the complete commit and envelope, the only trigger is:

```bash
git push origin HEAD:refs/heads/calibration/issue-180-ci-baseline-2
```

Use the exact prepared commit, create the absent branch once, and never merge,
recreate, update for another attempt, rerun, dispatch or auto-retry it.
