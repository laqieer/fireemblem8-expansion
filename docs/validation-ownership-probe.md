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
user/mount/PID/network namespace. If unprivileged namespaces are unavailable,
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
| Generated registry code | Receives a filesystem containing only trusted-admitted Python code and the exact declared source files. Undeclared repository paths do not exist in its chroot. |

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

## Semantic identity versus execution integrity

`ExecutionSnapshot.digest` binds every admitted path, mode, length, and raw
byte. It is used for execution/cache integrity and never enters the semantic
owner fingerprint.

`run_make_probe` instead hashes one target/state semantic record:

- target and finite assignments;
- exact declared owner-input path and content identities;
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

The load phase receives only admitted program files and resolved source files.
Open/read, mmap, stat/lstat, symlink, directory iteration, glob, dynamic path,
and import routes cannot reach an undeclared repository path because it is
absent. The reported list must exactly equal the permitted list. Each
permitted source is then removed in an adversarial replay; unchanged successful
output proves it was not consumed and rejects. This makes declared, reported,
permitted, and behaviorally consumed sets agree without a best-effort syscall
trace. A candidate declaration cannot broaden the trusted contract.

PR #186 should replace its candidate-derived source authorization and
inotify observation with `probe_generated_sources`, passing its graph/schema
declaration as `SourceContract`.

## One aggregate budget

One `ProbeBudget` instance is mandatory for the complete target/variant/source
operation. It owns:

- one monotonic deadline, capped at 3,600 seconds;
- variant/state and subprocess totals checked before launch;
- aggregate cache, event, mapping, and output bytes;
- aggregate cache, event, mapping, pending-command, and future counts;
- current pending/future fanout and a bounded worker count; and
- the remaining deadline passed to every process and worker.

`run_bounded_process` creates a process group, incrementally accounts stdout
and stderr, closes inherited descriptors, and terminates the group on timeout,
overflow, interruption, or parent failure. `run_bounded_futures` rejects an
oversized batch before submitting a worker and cancels the whole batch on
failure. Socket, mount-root, process, and scratch cleanup is context-owned.
The mount launcher also caps address space, file size, open descriptors, and
per-user process count before candidate evaluation. There are no FIFOs or
candidate-visible event/mapping files.

## Compatibility and dependencies

- **Dependencies:** Linux user/mount/PID/network namespaces, GNU Make,
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
