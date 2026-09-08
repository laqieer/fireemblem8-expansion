# Confined ownership-probe foundation

Issue [#206](https://github.com/laqieer/fireemblem8-expansion/issues/206) supplies
a **framework capability**: one bounded execution and observation authority for
GNU Make and declared generated-source consumers. It does **not** select,
replace, or skip validation.

This delivery is the static, single-view core. Optional producer, view,
runtime-input and dependency-compiler contracts are allocated to #225--#228,
not exposed as core APIs. See the [archived delivery allocation](https://github.com/laqieer/fireemblem8-expansion/blob/56e0a206ffae088b0dbc1fe8aa6339a8ee820f33/docs/ownership-probe-allocation.json)
and [downstream boundary](#contract-allocation-and-downstream-integration).

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
The existing Build `tests/workflows` discovery imports this same process suite;
there is no added workflow, job, duplicate gate or required-context name.

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
silently becoming sparse absence. Optional explicit runtime inputs belong to
#227; mandatory runtime closure remains in the core.

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
in another channel-free capsule. It never becomes a Make-capsule executable.
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

The configured observation-record count bounds the **entire report**. Each
capsule limits the sum of attempted `consumed`, `code_consumed` and `accessed`
records to the remaining allowance before insertion. Repeating a value in the
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

`ProcessOutput.metadata` contains operation-aware source observations:
syscall number, canonical guest path, flags, mask, buffer size, directory
offset, actual signed kernel result, and complete input/output buffer bytes.
Stat/lstat/fstat/newfstatat, supported statx/fstatfs, access/readlink variants
and directory results retain their actual supported ABI data. Failed operations
keep their status without becoming successful source consumption. Unreadable
buffers and unsupported requests are explicit, not empty successful records.

The same fixed native comparison routine in the existing interceptor serves
command-cache checks and authenticated Make mapping selection. It reissues the
recorded operations in the authoritative guest context, using the complete
buffers and original flags/masks. Input buffers retain caller-owned padding;
kernel-returned metadata is neither masked nor normalized, and old output
bytes are not substituted for fresh kernel results.

A private metadata-validation invocation uses the existing supervisor, mounts,
limits and sole reaper, but executes only that trusted static routine. It is
not a registered candidate command and exposes no control channel to candidate
code. Metadata paths/operations are constrained by the selected completed
record. Ordinary candidate capsules remain channel-free. Make uses the same
routine in its authenticated interceptor, not a Python executor in Make.

Native event writes are recorded only after complete successful kernel writes
in the existing closed supervisor report. The reader compares those bytes to
the event file before resolving any command. The existing argv/hash/count
framing is retained: nonnegative matches identify completed mappings, `-1`
requires real resolution, and `-2` rejects unsupported metadata reuse. There
is no new protocol version, signer, broker, namespace service or filesystem
simulation.

An unchanged compatible result can be reused. Changed metadata causes genuine
execution; a result that cannot be reproduced in the native Make context
rejects rather than supplying stale matched output. In particular, filesystem
capacity from `fstatfs` may change even without a source edit. Its complete
returned buffer remains part of validation; no universal stable mount-ID or
free-block assumption is made. Invalid-pointer metadata may execute directly
and report its real error, but cannot authorize unsupported Make replay.

Metadata revalidation has real process/syscall/observation and byte costs.
Request records, complete buffers, descriptor metadata, private map reads and
native event-write evidence spend the existing cumulative bounds. Cache hits
avoid candidate execution, not the required metadata validation cost. No
counter, cap, deadline or budget meaning is relaxed.

This core does not recreate source contexts or publish generated files.
Generated metadata fidelity belongs to held #225; names/type agreement in the
unapproved reference is not sufficient evidence for that extension.

Registry success additionally requires the typed reported `source_paths` to
equal that set. Reported JSON is candidate data, not supervisor evidence.
The generated-registry driver obtains cardinality through the selected schema's
existing `manifest_record_count(records)` API. Structured records and
sequence-backed tables therefore retain their schema-defined meaning; a schema
missing that contract fails rather than falling back to container length.
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
CURRENT/BASE selection belongs to #226. Checked-out submodule files and moving branch HEADs never
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

The existing **3,600-second maximum is one monotonic deadline**, including
snapshotting, compilation, all subprocesses and replay. Every subprocess gets
the remaining lifetime. Defaults bound 4,096 states/launches, 32 simultaneously
live traced guest processes, 16,384 total guest-process creations per report,
32 pending command resolutions, two million
syscalls and 32,768 snapshot entries. There are no futures or hidden worker
queues. Variant plans are checked before any variant launch.

Make validates the complete native event stream and protected-map associations
before resolving work from it. It then streams unresolved commands in their
original first-occurrence order, resolving and installing each actual result
before admitting the next. The existing completed mapping deduplicates repeated
events; there is no separate materialized backlog of pending command strings.
Completed outputs, caches and mappings retain their existing byte charges.
The entire valid batch is resolved within the same pass, followed by the
unchanged native replay and final-pass-only identity selection.

`ProbeSession.pending_commands` measures active resolutions, including nested
registration work; `pending_commands_peak` retains their actual maximum, not
the configured limit. Admission enforces `Limits.pending` before starting
another resolution, and the existing signal-safe cleanup restores the prior
count on success, failure or interruption. The former extra limit on the
**whole observed per-pass backlog** is replaced by this bounded serial
resolution. The pending/fanout maximum and dynamic-pass limit are not raised,
and resolution windows do not add extra Make replay passes.

`Limits.processes` bounds live capacity, including the capsule root, stopped
newborns and suspended vfork ancestors. `Limits.descendants` bounds cumulative
actual creation across every core capsule, command and replay.
The old extra 32-total-per-capsule restriction is explicitly replaced by the
live-capacity bound; none of the numerical maxima is increased.

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

The existing pending-byte category is cumulative **lifetime traffic**, not a
live outstanding-memory gauge: uncached command declarations, variant inputs,
binary stdin and subprocess launcher arguments spend it without refunds.
Serial resolution does not reset that byte counter or reclassify completed
traffic. A later pending-byte exhaustion is a distinct measured limit, not
permission to increase it or silently turn it into a reusable credit pool.

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
support and the original typed stdout-only command surface. It does not expose
`Command.native_tool`, `Command.outputs`, `Command.dependency_only`,
`ProcessOutput.generated`, `ProbeSession.select_view`, optional `runtime_files`,
native output capture or `Snapshot(reuse=...)`.

#225 remains explicitly held: its current physical delete/recreate replay does
not preserve all allowed metadata. Its prior generated-listing successes do
not replace the retained nlink/timestamp and reconstruction counterexamples.
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
