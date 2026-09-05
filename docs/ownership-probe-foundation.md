# Confined ownership-probe foundation

Issue [#206](https://github.com/laqieer/fireemblem8-expansion/issues/206) supplies
a **framework capability**: one bounded execution and observation authority for
GNU Make and declared generated-source consumers. It does **not** select,
replace, or skip validation.

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
/usr/bin/python3 -I -B scripts/validation_ownership/isolated_launcher.py --worktree
```

No ARM toolchain, ROM, credentials, GitHub request, or manual judgment is needed.
The host must provide Linux x86-64, GNU Make **4.3**, Python 3, a static-capable
GNU host C compiler, a glibc runtime, and working user/mount/network/PID namespaces. Where user
namespaces are unavailable, the existing noninteractive sudo namespace route
must work; the child drops to the original non-root runner identity before
execution. Other Make native ABIs/platforms reject, rather than falling back to
unconfined evaluation. The native observer uses GNU Make's exported 4.3 data
layout and the Linux syscall-entry/exit information API (kernel 5.3 or later).
Native C++ tool consumers additionally need the existing host C++ compiler.
The existing Build `tests/workflows` discovery imports this same process suite;
there is no added workflow, job, duplicate gate or required-context name.

## Trust boundary

Candidate Makefiles are programs, including their parse-time `shell`, `file`,
`eval`, include, recipe, and load behavior. Candidate registry modules and
native tool sources are also untrusted. The caller must load this package from
its trusted revision, **not import the candidate package into the supervisor**.
The isolated launcher excludes ambient Python hooks.

Each capsule has a private mount, network and PID namespace, a read-only chroot,
no capabilities, `no_new_privs`, and no inherited descriptor beyond standard
input/output/error. Candidate source mounts preserve observable Git executable
bits but are **noexec**. No proc filesystem, device-FD aliases, host home,
credentials, or service sockets are mounted inside the candidate root.

The syscall supervisor remains outside the chroot. It follows every child,
uses kernel-identified entry/exit stops, fails on unadmitted syscalls, and
records actual open/read/mmap/metadata/directory accesses. Shared-memory
threads, untraced/reparenting clones, anonymous executable mappings, ptrace,
memfds and alternative executable dispatch reject. All failures remain failures
even if candidate code would otherwise catch an exception.

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

Only the trusted Make binary and static interceptor aliases can execute in the
Make capsule. A native guard rejects **every** Make `load` before a module entry
point, including attempts to load an already-mapped host object. Candidate
executables, alternate loaders/interpreters, nested native Make through
`SHELL`, and noncontract `.SHELLFLAGS` are not execution authority.

There is no candidate-readable generated probe program or writable domain
file. The trusted observer reads GNU Make's actual target/dependency/recipe
structures and evaluates requested global and target-scoped variables. It
finishes all candidate expression expansion **before** opening its typed result
descriptor. Only syscall instructions in that trusted observer's executable
mapping may open/write the result channel. GNU Make `file`/include/eval cannot.
Candidate stdout/stderr is retained as bytes for diagnostics, never parsed as
authoritative Make structure.

The static interceptor alone opens the event and read-only mapping channels.
Neither Make nor a registered command receives those descriptors. The original
length-framed event fields and exact FNV-keyed mapping format are retained.
Direct argv is now canonically quoted: one argument containing a space cannot
collide with two arguments. Hash hits still require exact command bytes.
Mappings contain actual results of registered, confined commands, not invented
successful values. An initial Make parse error can be retried only when there
is an observed, explicitly registered unresolved eager command; final success
requires successful GNU Make completion and its authenticated observation.

### Registered commands and native tools

Registered Python/printf/uname commands run in a distinct capsule with **no**
Make event, mapping, observer or result mount. Python always receives `-I -S -B`.
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
in another channel-free capsule. It never becomes a Make-capsule executable.
Changed or foreign-session ELF handles reject.

## Source declaration and identity contracts

`Command` declares argv, admitted code paths, candidate source paths/globs and
directories permitted for enumeration. The supervisor resolves selectors
against the selected snapshot and materializes only that view. A selector that
matches a symlink/gitlink rejects; it does not silently drop that input.
Undeclared data open, mmap, stat/access/readlink, directory/glob or dynamically
constructed paths reject. Code imports have a separately admitted code set and
bounded, absent import-cache probes.

Access observation includes metadata and enumeration, not merely byte reads.
A directory observation consumes the declared names it exposes. Command
success requires **declared = permitted = consumed** candidate sources.
Registry success additionally requires the typed reported `source_paths` to
equal that set. Reported JSON is candidate data, not supervisor evidence.
Malformed UTF-8, duplicate/nonfinite JSON, stale/omitted/extra paths, malformed
frames and unused source declarations reject. Directory source selectors must
be explicit: the foundation does not infer a generator's ownership from a
filename or arbitrarily treat every file under `src/data` as generated data.

Two identities deliberately serve different purposes:

* **Execution snapshot:** complete admitted Git path/mode/type/content state,
  including current live bytes, executable bits and symlink targets for a
  worktree snapshot, and immutable gitlink identities. It binds execution/cache
  reuse. There is no process-global cache. A session's captured view cannot
  change beneath a later invocation.
* **Semantic owner:** only the requested target's native observations, requested
  domain state, declared/recipe-owning inputs and relevant command output.
  Unrelated source/docs/symlink/mode changes cannot change every Make owner.

Do not hash the whole `MakeObservation` when computing an owner identity:
consume `semantic_digest`, not `execution_digest`. These are ephemeral
execution/semantic boundaries, not committed source ledgers or ROM identity
requirements.

## Aggregate lifetime and resources

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
loader = AuthorityLoader(root, entries, "HEAD")
with ProbeSession(loader, scratch_root=root / "build/test-artifacts/probe",
                  budget=budget) as probe:
    observation = probe.make(
        "localization-check", makefile="localization.mk",
        variables=("LOCALIZATION_OUT_DIR",), owner_inputs=("localization.mk",))
    print(observation.semantic_digest)
```

The existing **3,600-second maximum is one monotonic deadline**, including
snapshotting, compilation, all subprocesses and replay. Every subprocess gets
the remaining lifetime. Defaults bound 4,096 states/launches, 32 descendants
per capsule, 16,384 total descendants, 32 pending commands, two million
syscalls and 32,768 snapshot entries. There are no futures or hidden worker
queues. Variant plans are checked before any variant launch.

Byte accounting is also aggregate: 768 MiB total, 384 MiB snapshot processing,
64 MiB streamed output, 64 MiB capsule writes, 32 MiB each cache/mappings/control,
16 MiB events, and 1 MiB pending requests. Individual candidate output is
streamed with a 1 MiB cap; bounded files/observations are at most 16 MiB.
Capsules have a 512 MiB **aggregate** address-space ceiling (allocation/fork
reservations are checked against the whole live process group), 16 MiB stack
limits, 128 descriptors,
no core dumps and a 4,096-creation aggregate cap. Limits may be lowered, not
raised. Filesystem observations and serialized semantic results consume the
same bounded control budget. Parallel calls to one session reject; a violation
makes the entire session unusable.

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

## Dependency and downstream integration boundary

This is an independent root extracted from the concepts/APIs in PR
[#186](https://github.com/laqieer/fireemblem8-expansion/pull/186), **not** a copy
of its graph implementation. At the introducing base, `master` has no
`validation-ownership-check`, graph, reporter or domain planner. A no-op target
with that name would falsely claim the unmerged dependency was satisfied, so
this root provides the distinct executable foundation target above.

Required downstream #180/PR186 integration:

1. Import this `GitTreeEntry`/`AuthorityLoader` API instead of the reporter's
   duplicated loader and compile the interceptor/observer from the trusted base.
2. Keep the complete finite-domain/context census, oracle, graph, lifecycle,
   workflow and exact-base policy above the execution boundary. Share one
   session/budget for the **entire** report; never restart it per owner/variant.
3. Consume native `MakeObservation.semantics`/`semantic_digest`, not debug/trace
   stdout or an identity hash containing the full execution snapshot. Adapt
   registered exact command keys to the collision-free argv convention.
4. Supply complete typed code/source/enumeration declarations to registry and
   native commands. `probe_generated_registry` accepts a typed `Command` and
   optionally the existing session; its result is the verified typed record.
   Gitlink-backed tools need exact pinned materialization/admission, not a
   live submodule checkout or executable mounted into Make.
5. Restore the full public `validation-ownership-check` Make target **in PR186** and
   exercise its entire current domain matrix, generated ownership, oracle and
   lifecycle under the unchanged bound. No such matrix/graph proof is claimed
   by this foundation's small, real consumer.

Dependencies are the existing generated-registry schema and host tools above.
Conflicts: PR186's probe/interceptor/reporter surfaces must be reconciled.
Other feature/profile conflicts: **none**. Modern debug/release, archival,
save/config identity, localization content, generated game output and ROM/RAM
are unchanged. No feature flag or new Build topology/context is introduced.
Revert this dedicated change to roll back; broader validation remains mandatory
and PR186 remains blocked rather than accepting unsafe or missing evidence.

Tester procedure:
[`TC-WORKFLOW-OWNERSHIP-PROBE-SANDBOX-001`](test-cases/workflow-governance.md#tc-workflow-ownership-probe-sandbox-001-confine-and-bound-authentic-probe-execution).
