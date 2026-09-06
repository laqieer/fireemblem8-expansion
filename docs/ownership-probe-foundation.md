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
/usr/bin/python3 -I -S -B scripts/validation_ownership/isolated_launcher.py --worktree
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

### Explicit runtime discovery inputs

The optional `ProbeSession(..., runtime_files=(... ,))` admits exact regular
system files, or their actual absence, for Make's existing runtime discovery:

```python
with ProbeSession(
    loader, scratch_root=scratch, budget=budget,
    runtime_files=(
        "/usr/include/newlib/stdlib.h",
        "/usr/include/build",
        "/usr/include/.dep",
    ),
) as probe:
    observation = probe.make("assets-test", commands=commands)
```

The first path supports the real `modern.mk` newlib wildcard. Capturing its
actual `/usr/include` ancestor also preserves GNU Make's standard include-search
context. The other two declarations capture actual absent search prefixes for
missing relative generated/dependency includes, not empty Make assignments.
An explicitly captured absent path proves its descendants absent as well.
If such a path instead names a directory or another nonregular object, capture
rejects rather than fabricating that absence.

Files must be canonical absolute paths below the existing trusted system
tool/library roots or `/usr/include/`; root ownership and non-group/other-
writability checks also apply to existing ancestors. Symlink/non-directory
ancestors, special files, executable-image collisions, overlaps, duplicates and
excess requests reject. Present files retain captured real bytes and modes;
absent inputs are not created. No live include tree is mounted. Unrequested
runtime files and directory enumeration remain forbidden, and declarations
grant no new executable or write authority.

`probe.runtime_inputs` exposes frozen `RuntimeInput(path, data, mode, parents)`
records during the session; `data is None` denotes captured absence and
`parents` records ancestor presence. Capture, copies and observations spend the
existing control/report budgets. Runtime inputs remain fixed across BASE/current
view selection, clear on session exit and bind Make's execution identity.
Semantic identity still comes from actual native target/domain observations,
not unrelated runtime bytes. Default callers that omit `runtime_files` retain
their existing behavior.

There is no candidate-readable generated probe program or writable domain
file. The trusted observer reads GNU Make's actual target/dependency/recipe
structures and evaluates requested global and target-scoped variables. It
finishes all candidate expression expansion **before** opening its typed result
descriptor. Only syscall instructions in that trusted observer's executable
mapping may open/write the result channel. GNU Make `file`/include/eval cannot.
Candidate stdout/stderr is retained as bytes for diagnostics, never parsed as
authoritative Make structure.
After observer initialization, candidate reads of its image and enumeration of
runtime directories reject; the private interceptor is below the already
protected control namespace. There are no probe-only public shell/Make aliases.

The static interceptor alone opens the event and read-only mapping channels.
Neither Make nor a registered command receives those descriptors. The original
length-framed event fields and exact FNV-keyed mapping format are retained.
Direct argv is now canonically quoted: one argument containing a space cannot
collide with two arguments. Hash hits still require exact command bytes.
Mappings contain actual results of registered, confined commands, not invented
successful values. An initial Make parse error can be retried only when there
is an observed, explicitly registered unresolved eager command; final success
requires successful GNU Make completion and its authenticated observation.
Declared file results extend those same mappings with a bounded typed sidecar.
Only a value/remake interceptor with an exact matched command can request
publication; Make and registered-command processes cannot call that private
operation. The supervisor, outside the chroot, writes the captured bytes into
the owned backing view at that dispatch, before the interceptor returns.
Neither the candidate nor interceptor receives writable `/repo` authority.

The observer preserves GNU Make's actual `execvp` restart after remaking an
include, including argv order and native environment/`MAKE_RESTARTS` behavior.
It restores only its private bootstrap values and preload, which disappear
again before Make imports its environment. The syscall IP and original Make
PID authenticate this transition; a job child or registered command cannot
use it to execute native Make. Restarts retain the same supervisor, event
stream, budgets and 64-pass bound. A fresh outer replay starts without any
previous replay's generated files, rather than precreating an include and
silently losing the native first-parse/remake/restart context.

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
Changed or foreign-session/view ELF handles reject.

### Native Make registrations and declared file results

The existing typed `Command` also accepts `native_tool=tool` from that same
active session view. Its exact argv must begin with `/native/tool`; the rest is the
real native argv, including empty arguments. It executes through the same
channel-free command path as `native`, never through Make's mount:

```python
tool = probe.compile_native(scaninc_sources, headers=scaninc_headers, cxx=True)
registration = Command(
    ("/native/tool", "-I", "include", "-I", "", "proof.s"),
    native_tool=tool, sources=("proof.s",))
observation = probe.make("all", commands={
    'tools/scaninc/scaninc -I include -I "" proof.s': registration,
})
```

Registrations describe commands actually dispatched by Make, not permission
to install an executable or invent a successful missing-program result.
The native result retains real stdout and successful source observations.
Semantic command identity includes the sealed ELF and its declared build-input
identities, not a guessed printf replacement. A copied/forged, changed or
foreign-session tool is not an issued registration.

For a producer, declare **all** surviving files to capture with
`Command(..., outputs=("generated.mk",))`. Each name is exact, canonical and
repository-relative: the producer writes `/work/generated.mk`, and the matched
Make dispatch publishes it as `/repo/generated.mk`. For example:

```python
producer = Command(
    ("/usr/bin/python3", "/repo/producer.py", "/work/generated.mk"),
    code=("producer.py",), sources=("choice.txt",), outputs=("generated.mk",))
observation = probe.make(
    "all", variables=("SELECTED", "MAKEFILE_LIST", "MAKE_RESTARTS"),
    commands={"python3 producer.py generated.mk": producer})
```

The candidate Makefile may `include generated.mk` and supply a remake rule
whose recipe is the registered producer. GNU Make really sees the initially
missing include, dispatches the remake, re-execs, loads it, and selects its
prerequisites. Parse-time `shell` producers publish before the include instead;
their native context does not acquire an invented restart. Chained includes
can require multiple real restarts. Ordinary recipes remain metadata-only.

`command` and `native(..., outputs=...)` return
`ProcessOutput.generated`, a tuple of immutable `GeneratedFile(path, data, mode)`
values, never live scratch paths. `outputs=()` preserves existing stdout-only
callers and their disposable `/work` use. Opting into capture requires exact
equality between the declaration and surviving regular files; only their
ancestor directories may also remain. Extra, missing, nonregular, symlinked,
escaping, oversized and overlapping outputs reject. Tracked blobs, symlinks,
gitlinks and their path namespace cannot be replaced. Two distinct producers
cannot publish one path in a pass, even if their bytes happen to agree;
equivalent registrations/aliases share an identity. Repeated dispatch of that
same producer republishes the result at the actual dispatch, not once before
the pass.

File modes and bytes participate in generated-result identity; Make's mount
is still read-only/noexec/nosuid. Each file and the combined framed mapping
(including its metadata) fit the existing file limit. Capture, retained cache,
mapping/provenance, publication bytes and created files/directories spend the
same report-wide quotas, including speculative producers. Publication streams
bounded chunks under the original deadline, not an independent output copier.
Only final successful outer-replay dispatches contribute dynamic identity;
recipe-owning generated files bind to their actual output identities.

Publication is Make-call/replay-local. Every next outer replay, return, failure
or interruption removes only the declared generated files and new owned
ancestors; snapshot inputs and unrelated worktrees are untouched. Producers
still read only their declared snapshot code/sources and write fresh private
`/work` output. This is not an incremental build filesystem or an API for
feeding another producer undeclared previous outputs. The downstream graph
and full-domain adoption remain #180's responsibility.

On the sudo-drop route the supervisor transfers each newly created publication
directory/file to the configured runner UID/GID through its nofollow-opened
descriptor. Directories transfer before children are created; files transfer
before content and their declared mode are applied. Existing source/view/control
objects are never chowned. The user-namespace route performs no transfer: its
UID mapping already gives the outer runner ownership. Transfer failure remains
a visible publication failure, not permission to run privileged cleanup or
ignore residue. Live owner/group/mode checks and ordinary runner cleanup are
part of the include/restart, binary-output and interruption regressions.

## Source declaration and identity contracts

`Command` declares argv, admitted code paths, candidate source paths/globs and
directories permitted for enumeration. The supervisor resolves selectors
against the selected snapshot and materializes only that view. A selector that
matches a symlink/gitlink rejects; it does not silently drop that input.
Literal selectors identify exact repository-relative paths, not matching
basenames in unrelated directories. Explicit `*`, `?` or `[` glob selectors
retain their pattern semantics.
Undeclared data open, mmap, stat/access/readlink, directory/glob or dynamically
constructed paths reject. Code imports have a separately admitted code set and
bounded, absent import-cache probes.

Authorization remains entry-time and fail-closed, but source/code/Make-path
evidence is committed only after a successful kernel return. A successful open
is pathname/existence metadata; successful stat/access and file mappings also
remain observations. Failed calls add no evidence, and read/pread/readv/readlink
need positive returned bytes. A later failed operation does not erase a prior
valid observation. FD duplication/state changes are applied only on success.

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
Registry success additionally requires the typed reported `source_paths` to
equal that set. Reported JSON is candidate data, not supervisor evidence.
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

Admitted gitlink namespaces are read-only/noexec and remain protected against
generated-output replacement. Source declarations still name actual regular
files/globs; literal names keep their exact root-relative matching. Native and
registry calls retain successful-return source accounting. Explicit
`owner_inputs=("mgfembp",)` can name the whole captured pin; file owners continue
to identify their actual path/mode/content. An unrequested pin is not an owner.

Link admission requires a captured immutable superproject loader. Different
BASE/current captures may request different pins and paths and use the existing
`select_view` API. Checked-out submodule files and moving branch HEADs never
substitute for the captured commit. New entry metadata, blob reads and
materialization retain the original entry/byte/run/deadline bounds; no source
inventory/hash ledger or live submodule mount is added.

Two identities deliberately serve different purposes:

* **Execution snapshot:** complete admitted Git path/mode/type/content state,
  including current live bytes, executable bits and symlink targets for a
  worktree snapshot, and immutable gitlink identities. It binds execution/cache
  reuse. There is no process-global cache. A session's captured view cannot
  change beneath a later invocation.
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

### Selecting immutable BASE/current views in one report

Deleted generated inputs must be observed with their BASE registry and source
bytes, not the current registry or a union of both filesystems. Use the existing
session's explicit scoped selector:

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
    with probe.select_view(base):
        base_record = probe_generated_registry(
            base, command=base_command, session=probe)
    # The original current view and its cache are active again here.
```

The context yields that same `ProbeSession`, not a second report owner.
`git_tree_entries` binds its captured entry map to the report budget,
repository root and revision representation; loader construction rejects a
different captured root/revision. The captured object IDs determine immutable
bytes even if a ref or worktree later changes. Selection requires an immutable
loader from that capture API with the same budget and exact repository root,
not another clone/worktree that happens to contain the same objects. Detached
entry maps, mutable alternate views, foreign budgets and inactive/closed
sessions reject. Existing default worktree sessions remain supported, including
restoration of their already captured live snapshot after an immutable selection.

Guest paths stay `/repo/<original-path>`; no BASE/current prefixes or virtual
filesystem simulation are introduced. The active loader's helper check is
unchanged: using BASE outside its selection, or current inside BASE, is still a
foreign-loader error. Nested selections restore their previous view.
Invalid selection admission does not replace or tear down the active owner.
Failures after admission, including setup, body or teardown failures, are
terminal to the report; restoration never resets/reopens its budget. A late
context exit cannot reactivate a closed outer session.

Every selection reserves an existing report state and creates a distinct
immutable `Snapshot`. Exact regular entries independently admitted by both
immutable captures (same root, budget, original path, mode, type and Git object
ID) can share already funded immutable bytes and hardlinked source storage.
This avoids re-reading/copying an entire unchanged repository, not charging
less for work actually performed. Changed, missing or mode-different entries
must be captured/copied independently. Mutable snapshots do not certify Git
object bytes and cannot supply that reuse. Every view's metadata, all new
capture/read bytes and copied storage spend the same aggregate budget, without
refunds. Captures and direct loader reads also share that budget.
The deadline, launches, Make states, processes, memory limits, source/output
bytes and file-creation counters never reset. The session's trusted Make runtime,
interceptor, namespace route, signal ownership and sole-reaper cleanup are reused.

Only view-dependent loader/snapshot/tree/cache/native state is scoped. The
prior view is suspended, and its exact state is restored on normal exit.
The selected tree, cache and native files are removed rather than retained
in a view registry; unlinking shared immutable sources leaves the prior view
intact. Group related BASE queries in one selection; re-entering creates another
bounded snapshot and does not refund prior work. Large unrelated views, or
views without certified immutable reuse, may legitimately exhaust the existing
aggregate bound instead of increasing it.
Native handles must be compiled and used in their active view: even identical
ELF bytes do not grant cross-view execution. Default-view handles survive a
selection; selected-view handles expire when it ends. Generated files publish
only into the active read-only view and retain the existing dispatch/restart,
ownership-transfer and cleanup contracts.

Execution identities reflect each captured snapshot. Semantic identities do
not include a view label, host prefix or other whole-view marker: genuinely
equivalent consumed inputs/commands keep the same semantic identity despite
unrelated tree differences. Different source bytes, generated results and
native build inputs still invalidate their respective owners. This is the
existing trusted-caller authority boundary, not protection against arbitrary
Python object mutation, and it does not implement #180's full ownership graph.

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
Capsules have a 512 MiB **aggregate virtual-address-space** ceiling, 16 MiB
maximum stack limits, 128 descriptors,
no core dumps and a 4,096-creation aggregate cap. Limits may be lowered, not
raised. Filesystem observations and serialized semantic results consume the
same bounded control budget. Parallel calls to one session reject; a violation
makes the entire session unusable.

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
   Use `select_view(base_loader)` for immutable BASE registry/source queries
   needed by deleted paths; preserve original guest paths and restore current
   on scope exit rather than mutating private fields or creating another report.
3. Consume native `MakeObservation.semantics`/`semantic_digest`, not debug/trace
   stdout or an identity hash containing the full execution snapshot. Adapt
   registered exact command keys to the collision-free argv convention.
4. Supply complete typed code/source/enumeration declarations to registry and
   native commands. `probe_generated_registry` requires a typed `Command`,
   matching loader and the active report-wide `ProbeSession`; its result is the
   verified typed record. It must not create a per-registry session or budget.
   Gitlink-backed tools use explicit `GitlinkSource` capture of their exact
   superproject pin, not a live checkout or executable mounted into Make.
   Bind issued native tools through `Command.native_tool` and declare generated
   include files with `Command.outputs`; do not discard file results, precreate
   includes before a new Make invocation, or substitute synthetic stdout.
5. Restore the full public `validation-ownership-check` Make target **in PR186** and
   exercise its entire current domain matrix, generated ownership, oracle and
   lifecycle under the unchanged bound. No such matrix/graph proof is claimed
   by this foundation's small, real consumer.

In particular, the downstream 112-domain adoption is not established by the
normal-context, interception or cleanup regressions here.
The actual child root-Make prototype now observes the admitted newlib header
and pinned mgfembp source/header wildcards. Its later dependency-remake scaninc
commands still need a child-owned typed adapter, and the unchanged process
bound can reject that unresolved remake pass. The input-seam success does not
claim a complete default root observation or full graph acceptance.

Dependencies are the existing generated-registry schema and host tools above.
Conflicts: PR186's probe/interceptor/reporter surfaces must be reconciled.
Other feature/profile conflicts: **none**. Modern debug/release, archival,
save/config identity, localization content, generated game output and ROM/RAM
are unchanged. No feature flag or new Build topology/context is introduced.
Revert this dedicated change to roll back; broader validation remains mandatory
and PR186 remains blocked rather than accepting unsafe or missing evidence.

Tester procedure:
[`TC-WORKFLOW-OWNERSHIP-PROBE-SANDBOX-001`](test-cases/workflow-governance.md#tc-workflow-ownership-probe-sandbox-001-confine-and-bound-authentic-probe-execution).
