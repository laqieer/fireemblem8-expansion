# Confined validation-ownership probe foundation

Issue [#206](https://github.com/laqieer/fireemblem8-expansion/issues/206)
provides the reusable execution and observation boundary required by issue
#180 and PR #186. It is an accepted **framework capability**, not validation
selection: it does not skip, narrow, rename, or replace any host, ROM,
archival, Build, or public validation target.

Run the public foundation check from a supported Linux checkout:

```bash
make validation-ownership-check
```

The goal must be selected alone and rejects command-line Make execution
controls. It runs the authentic `/usr/bin/make` implementation in a fresh
user/mount/PID/network/IPC namespace. If unprivileged namespaces are unavailable,
the only fallback is exact passwordless `sudo -n /usr/bin/unshare`; otherwise
the check fails closed. The existing maximum remains 3,600 seconds.

## Architecture and authority boundaries

| Boundary | Authority and invariant |
| --- | --- |
| Supervisor | Starts through the closed isolated launcher and owns the monotonic deadline, aggregate counts/bytes, immutable exact snapshot, command registry/cache, result bytes, and Unix socket. |
| Candidate Make | Sees a read-only, `noexec` candidate mount; no `/proc`, supervisor descriptor, compiler, Python, general shell, Make executable, result file, cache, or mapping directory is present. |
| Trusted Make executable | Is opened by the mount launcher, detached from its mount, and entered with `fexecve`; recursive candidate Make execution therefore has no executable path. |
| Shell interceptor | Is compiled supervisor-side, is the only exposed command executable, and sends exact raw argv over a read-only mounted Unix socket. The candidate cannot write, include, or open the socket with GNU Make's file functions. |
| Registered command | Must match exactly one trusted regular expression and program basename. Its raw output is byte-accounted and cached only inside the current `ProbeBudget`. |
| Candidate-derived native tool | Must be compiled supervisor-side, then bound through `RegisteredCommand.native`. Its executable digest is sealed before registration and rechecked before a second socket-free chroot starts it with `close_fds=True` and the remaining global deadline. Candidate copies remain `noexec`. |
| Generated registry code | Starts through the trusted Python bootstrap with one closed `SourceContract.admitted_imports` set, exact extension-module/ELF dependency mounts, Landlock, and a final seccomp user-notification filter owned by the supervisor. |

GNU Make's `load` directive and `override`/`eval` assignments to `SHELL`,
`.SHELLFLAGS`, `MAKE`, `MAKEFLAGS`, `MFLAGS`, or `GNUMAKEFLAGS` are rejected
before Make starts when they use the named public syntax. A trusted prelude
pins the interceptor before candidate parsing and is repeated as a postlude;
ordinary and exported assignments are therefore disabled without replacing
GNU Make's remaining semantics. The candidate mount and every writable mount
are also `noexec`, so dynamically constructed `load` or executable paths
cannot run. This exact reserved-directive scan is the named
`MAKE-EXECUTION-CONTROL-SYNTAX-v1` static security contract: spelling and
absence are the security property, while GNU Make remains the behavioral
authority for all ordinary rules, conditionals, `eval`, patterns, variables,
automatic variables, includes, and recipes.

The dispatcher does not rely on inherited file descriptors 3-5, candidate
paths, parseable text markers, or candidate-created result files. Unknown,
ambiguous, malformed, non-UTF-8 textual protocol input, changed shell argv,
unavailable native programs, and same-UID socket clients outside the exact
sandbox process ancestry reject. Raw subprocess and command output is retained
as bytes; UTF-8 is required only where Make trace, command, or JSON protocol
text is decoded.

The trusted Python bootstrap loads its declared standard-library and extension
closure from an exact copied module set before installing `no_new_privs`,
Landlock read-only access to the admitted `/repo` tree, and a seccomp
user-notification filter for
`memfd_create`, executable replacement, process/thread creation, sockets,
ptrace/process-memory APIs, namespace/mount changes, SysV IPC, BPF,
`userfaultfd`, executable-module loading, and related native escape syscalls.
It then removes non-admitted modules from `sys.modules`. Python import hooks,
audit hooks, patched `open`/`stat`, closures, and hidden Python values are not
security authorities: even if candidate code recovers loaded `ctypes`, raw
`posix`, or the original builtins, the final kernel policy remains
irreversible. Every extension's recursively resolved
ELF dependencies are mounted at their loader paths, and
`SourceObservation.runtime_sha256` binds the module bytes/metadata plus those
resolved paths and hashes. Both launcher modes create a fresh IPC
namespace, so trusted startup state cannot persist shared memory, semaphore,
or message objects into another run.

## Semantic identity versus execution integrity

`ExecutionSnapshot.digest` binds every admitted path's type, exact permission
mode, byte length, nanosecond modification time, UID/GID, and raw bytes. The
same supported metadata is materialized exactly. It is used for
execution/cache integrity and never enters the semantic owner fingerprint.

`run_make_probe` instead hashes one target/state semantic record:

- target and finite assignments;
- exact declared owner-input path, supported metadata, and content identities;
- parsed target-specific GNU Make trace and recipe output;
- exact registered command identity and output digest; and
- the trusted GNU Make executable identity.

An unrelated documentation or source change therefore changes the execution
snapshot but preserves another target's semantic owner fingerprint. Changing
a declared input, observed prerequisite/recipe, finite state, or registered
command result invalidates that target. PR #186 should consume
`MakeObservation.semantic_fingerprint` for owner identity and retain
`execution_snapshot_sha256` only for cache/execution consistency.

## Admitted generated sources

`SourceContract` is supplied by the trusted ownership graph/schema contract,
not candidate registry output. It names an exact regular-file set and may bind
one non-recursive directory pattern. Candidate metadata must equal that
trusted record.

The load phase receives one immutable materialization containing only admitted
program files and resolved sources. The supervisor receives the sealed seccomp
listener by `SCM_RIGHTS`, reads syscall path arguments with
`process_vm_readv`, canonicalizes them against the sandbox root/dirfd, and
authorizes or rejects open/openat/openat2, access, readlink, mmap, stat/lstat/
fstat/newfstatat/statx, and getdents/scandir behavior. It records the exact
normal-load operation/path set. Stat/statx responses are supervisor-emulated
from the snapshot: type, permission mode, size, `mtime_ns`, UID, and GID are
exact; inode, device, ctime, and link-count are canonical zero. Supervised
getdents likewise returns zero inode identities. The reported, permitted, and
observed source sets must exactly agree.

Omission replay reuses that immutable materialization and kernel-masks one
source, avoiding quadratic recopy. The omitted identity exists only in the
supervisor policy, never in candidate Python objects, environment, files, or
descriptors. Consumption is accepted only when the supervisor observed and
denied the exact attempted access to that source and the trusted bootstrap
returned its fixed controlled-denial result with empty output. A normal
nonzero exit, signal, crash, timeout, changed report, or self-authored text is
a probe failure, never dependency evidence. This makes declared, reported,
permitted, observed, and behaviorally consumed sets agree without
candidate-controlled tracing or a candidate-broadened declaration.

PR #186 should replace its candidate-derived source authorization and
inotify observation with `probe_generated_sources`, passing its graph/schema
declaration as `SourceContract`.

## One aggregate budget

One `ProbeBudget` instance is mandatory for the complete target/variant/source
operation. It owns:

- one monotonic deadline, capped at 3,600 seconds;
- variant/state and subprocess totals checked before launch;
- aggregate cache, event, mapping, output, snapshot, and worker-result bytes;
- aggregate cache, event, mapping, snapshot file/operation,
  pending-command, and worker-process counts;
- current pending/future fanout and a bounded worker count; and
- the remaining deadline passed to every process and worker.

Snapshot capture charges declared file sizes before reading, checks the
deadline during every chunk, and charges each stat/read/materialization
operation. Targets, variants, owner inputs, mount/command registries,
directory scans, source/program/import reports, and worker inputs are consumed
incrementally: deadline/count checks happen before append or deduplication,
and sorting occurs only after the bounded collection exists. `run_bounded_process` creates a process group, incrementally accounts stdout
and stderr, closes inherited descriptors, and terminates the group on timeout,
overflow, interruption, or parent failure. `run_bounded_futures` rejects an
oversized batch before launch and uses killable, reaped process groups rather
than threads. A blocking or noncooperative worker is terminated at the
aggregate deadline or sibling failure. Socket, mount-root, IPC namespace,
process, and scratch cleanup is context-owned.
The mount launcher also caps address space, file size, open descriptors, and
per-user process count before candidate evaluation. There are no FIFOs or
candidate-visible event/mapping files.

## Compatibility and dependencies

- **Dependencies:** Linux user/mount/PID/network/IPC namespaces and x86-64
  seccomp-BPF, GNU Make,
  `/usr/bin/python3`, `/usr/bin/cc`, libc/loader discovery through `ldd`, and
  the current Build `build-essential` packages.
- **Dependent:** issue #180 / PR #186, through `ProbeBudget`,
  `ExecutionSnapshot`, `run_make_probe`, and `probe_generated_sources`.
- **Conflicts:** PR #186's private probe budget, inherited descriptor channels,
  whole-snapshot semantic fingerprint, and inotify source observation must be
  removed at the integration seam. No other feature conflict is known.
- **Targets/data:** no validation target, gameplay, ROM, save, localization,
  generated game-data output, modern/archival lane, or Build topology changes.
- **Resources:** host-only bounded scratch/process/socket use; no ROM/RAM/save
  cost and no timeout increase.
- **Rollback:** revert issue #206 and leave PR #186 blocked.

The indexed tester procedure is
[`TC-WORKFLOW-OWNERSHIP-PROBE-SANDBOX-001`](test-cases/workflow-governance.md#tc-workflow-ownership-probe-sandbox-001-confine-and-aggregate-bound-validation-ownership-probes).
