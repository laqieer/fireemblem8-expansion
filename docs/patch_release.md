# Patch-only release artifact (issue #49)

The project can publish one transient, patch-only Actions artifact for the
named **modern release/AAPCS all-production-locales/all-supported-features**
profile. It is not a ROM download, a GitHub Release asset, or a substitute for
an independently obtained legal base image.

## Profile and source build

The profile name is `modern-release-all-locales-all-features-aapcs`. Its
isolated build root is `build/expansion-modern-all-locales-all-features`; its
generated data, locale catalog, metadata, objects, map, ELF, and ROM never
share a default or localization-only output root.

```bash
make expansion-modern-all-locales-all-features-check
```

The target uses release/AAPCS and 32 MiB, enables
`en,ja,zh-Hans,fr,de,es,it` with English as default, excludes `qps-ploc`, and
enables mechanics hooks/sample, Danger, starter content, the AoE
reference, localized auto-wrap, casual mode, item cap `0xCE`, and the
`preserve` BGM policy. The generated metadata and embedded
`ExpansionMetadata` retain that complete identity and a distinct configuration
fingerprint. The save layout and compatibility epoch do not change; use blank
or disposable SRAM when switching profiles.

This command is the authoritative source-build procedure. The artifact is
optional and is byte-equivalent only when it is applied to the exact approved
base and the manifest's commit/profile metadata matches the source checkout.

## Legal base contract

The BPS patch accepts exactly a legally obtained clean **Fire Emblem: The
Sacred Stones (USA), revision 0** / FE8U image:

| Field | Required value |
| --- | --- |
| Size | `16777216` bytes |
| SHA-256 | `638cda9d9b72657220fbf7e7a500cd3b64d9686c36e8a56fca69d26d13886f2f` |
| SHA-1 | `c25b145e37456171ada4b0d440bf88a19f4d509f` |
| Header | `FIREEMBLEM2E`, `BE8E`, maker `01`, fixed byte `0x96`, revision `0`, checksum `0x9D` |

The publisher checks size, both hashes, every header value, and recomputed
checksum before creating a patch. Missing, malformed, wrong, or modified
inputs fail before an artifact is created. The trusted workflow may obtain its
local input from a protected secret, but no URL, base bytes, or base image is
published, cached, or logged. No complete target ROM is uploaded to or
downloaded from an Actions artifact, cache, release, or log.

The fresh publisher checks out and verifies the exact validated master-push
after SHA. It stages the producer from that same immutable commit with no
whole-file source hash ledger. Before any secret or base exists, the candidate
tree is copied to a disposable workspace owned by a dedicated unprivileged
UID and built inside mount, PID, network, IPC, and UTS namespaces with no
network, capabilities, secrets, `BASH_ENV`, or `GITHUB_ENV`. Mount propagation
is private and all recursively visible host root/system/tool mounts, including
`/usr/share` and `/opt`, must be read-only. Only exact private source, home,
temporary, and handoff mounts are writable by candidate code. The root-only
supervisor mount remains inaccessible to the candidate. Private tmpfs `/tmp`,
`/run`, `/dev`, and `/dev/shm` plus private `/proc` hide host D-Bus
activation/service, Docker, containerd, systemd, snap, and other UNIX sockets
and runtime paths.
The trusted PID-1 wrapper is loaded into Bash `-c` memory before
`/home/runner` is masked, so no open script descriptor pins the host mount.
The private `/dev` is mounted over the host path without trying to unmount the
trusted wrapper's already-open null descriptors. Before that overmount, the
wrapper reads the recursive `/dev` mount tree as structured JSON, decodes and
validates every target, writes the NUL-delimited result into checked root-owned
regular temp files under `/mnt/supervisor`, and unmounts only descendants
deepest-first. This removes inherited `/dev/pts`, `/dev/mqueue`, `/dev/shm`,
and runner-specific child mounts without touching the root-owned mode-`0700`
supervisor parent. The candidate's writable-mount audit also consumes only
decoded structured JSON target records through checked NUL-delimited
transport, so raw escaped or whitespace-delimited mount text can never be
mistaken for an unapproved path. `/mnt/supervisor` is the sole mount-level
`rw` exception that candidate code cannot read, write, execute, or traverse;
its mode-`0700` root ownership and the candidate's negative access probes
enforce that boundary while avoiding the invalid late parent remount over its
read-only cgroup child.
Hash-locked wheels are fetched by the trusted host before isolation and
installed offline inside it. Every builder descendant is placed in one exact
cgroup v2 that the candidate cannot see or leave. With shell monitor mode
disabled, a trusted no-fork Python launcher calls `setsid()`, verifies its PID
is both the session and process-group ID, and self-stops before it can execute
`timeout`, `sudo`, `unshare`, or candidate code. The host authenticates that
exact stopped child through the kernel process table before resuming it.
The launcher also requests a parent-death `SIGKILL`, so a never-resumed child
cannot outlive the trusted shell. Cleanup immediately rechecks the immutable
shell-parent PID, PID/SID/PGID tuple, expected process state, and `/proc`
start time before every group signal. A missing, forged, or parent-group
identity during launch never records a session: the primary `launch` rejection
already reports failure, while empty owned cgroup and builder-UID cleanup
succeeds with no cleanup diagnostic. Cleanup reports its bounded summary only
for residual cgroup/UID state or a failure after session authentication.
Stale or reused authenticated identities cause no PID or process-group signal;
cleanup marks failure and uses only the owned builder cgroup. After an
authenticated group signal, cleanup either reauthenticates before escalation
or observes exit, then uses the shell's exact child wait only for reaping. It
kills the exact builder cgroup for namespace descendants that leave the group
and proves the session, cgroup, and builder UID are empty. It removes only that
owned cgroup before admitting a regular, nonsymlink, single-link 32 MiB ROM and
bounded metadata from the exact two-file handoff.
Devices, escaped paths, and unexpected outputs fail. It validates metadata
against the after SHA, copies only those public inputs into runner-owned `0400`
staging, and removes the builder user, tree, wheelhouse, and candidate
checkout. Missing mount/cgroup-v2 capabilities fail before candidate execution;
cleanup never uses `pkill`, `killall`, or a UID-wide signal.
After descendants terminate, privileged cleanup removes only the exact
owned builder root so builder-UID files cannot make teardown fail.
Before hiding `/sys`, the wrapper bind-mounts only the exact owned cgroup
read-only under the exact root-owned mode-`0700` `/mnt/supervisor` parent.
The candidate cannot read, write, execute, or traverse that parent and cannot
receive an FD for it. After candidate exit, fixed isolated Python reads the
exact read-only cgroup child and accepts only the wrapper PID plus its own
transient checker PID. The checker exits before export, leaving the wrapper
alone; the host continues to use the actual cgroup path for kill and removal.
The raw cgroup path is used only for the exact initial
`printf` membership-join write. Bind identity is checked through the raw and
supervisor directory inodes; no raw `cgroup.procs` read remains. The parsed
contract requires exactly one main-scope `cgroup_path="$1"` initialization on
an empty, unconditional `builder_main` control path before those join/bind/stat
operations and rejects conditional/grouped placement plus every later scalar,
indexed, array, declaration, writer, unset, function, or dynamic mutation.
Shell tokens retain quote segments: parameter expansion occurs only in
unquoted or double-quoted active segments, while single-quoted and escaped
dollars remain inert literal data. Mixed segments concatenate without
recursively expanding literal dollars.
The parsed
contract rejects direct, redirected, wrapped, spaced, or aliased raw membership
reads even when the fixed checker remains present. Tracked braced
parameters with default/assign/error/alternate, prefix/suffix removal,
substring, case conversion, transformation, length, or indirect operators
fail closed because their resulting path is ambiguous. Active nested braced
parameters are not modeled and reject; fixed single-quoted text remains data.
Direct and aliased forms behave identically in and out of quotes. Scalar `+=` updates the tracked
alias value, so split `cgroup` plus `.procs` construction remains tainted.
Indexed and associative assignments, declarations, literals, appends, and
expansions are not evaluated as a full Bash array model: tracked values reject
immediately, while ambiguous indexed use with `cgroup.procs` fails closed.
The runtime control demonstrates that `${raw[0]}` can otherwise read the raw
membership marker despite the safe supervisor read; the semantic guard rejects
that executable script. Builder positional argument `$1` and `${1}` are seeded
as the same raw root, including braced operators, indirection, scalar/array
aliases, and appends. A second runtime control proves a positional alias can
otherwise read the raw marker. Any shell command consuming the raw root or
composed `cgroup.procs` fragments fails closed unless it is an exact reviewed
join, bind, or stat signature; absolute programs are not exempt. Unrelated
arrays and other positional parameters remain accepted. The fixed Python read
is authorized only after the exact bind,
read-only remount, one canonical
`supervisor_cgroup=/mnt/supervisor/cgroup` assignment, and directory-inode
verification. Literal, aliased, operator, append, array, or `unset`
reassignment invalidates it. Recognized `command` (`-p`/`--`) and `builtin`
prefixes are normalized after alias resolution, so wrapped or aliased unset
and variable-mutating declare/typeset/local/export/readonly/read/mapfile/
readarray/`printf -v` forms reject. `command -v/-V` queries and unrelated
wrapped commands remain valid. Clustered `command` options such as `-pv`,
`-vp`, `-pV`, and `-Vp` accept any repeated ordering of `p`, `v`, and `V`;
any `v/V` makes the complete valid prefix query-only, while `p` alone remains
an executing wrapper. Invalid mixed clusters fail closed when they target the
supervisor alias. Eval, source, and ambiguous mutation surfaces fail closed.
User-defined helper positionals are scoped independently from the builder
arguments. Calls to any unanalyzed helper reject tracked raw-root arguments or
separate arguments that can compose `cgroup.procs`; direct, `function`
keyword, nested, and scalar-aliased calls are covered. Only the exact
production helper call signatures may carry their reviewed ambiguous
transport values. A parsed, order-independent inventory requires the exact
production helper names, body-command identities, and multiplicities; added,
removed, duplicated, or modified helpers fail closed. At invocation, helper
bodies are re-evaluated against caller alias state, so no-argument closures
cannot retain a safe definition-time value after assignment or `printf -v`
retargets it. Each invocation gets its positional arguments and a sequential
local alias frame: local assignment, declaration, and modeled `printf -v`
writes affect later commands but are discarded on return, while global writes
propagate to the caller. Unknown `read`/`mapfile`/`readarray` results and
dynamic values remain tainted. Branch- or loop-dependent alias writes reject
rather than selecting a favorable path. Recursive and dynamically resolved
calls reject. Function
declarations cannot shadow security-sensitive builtins or commands, and
inline, dynamic, duplicate, or ambiguous declarations fail closed. Every trap
change rejects except literal `trap isolated_stage_failure ERR`; wrapped,
dynamic, DEBUG, RETURN, and EXIT forms are prohibited. `mapfile` and
`readarray` callbacks are prohibited, including attached and dynamic `-C`
options. No mutable `cgroup_members` shell state exists, and any attempt to
reintroduce that name rejects.
Function inventory normalization accepts Bash's canonical split
`name()`/`{` declaration syntax only long enough to bind the pending name to
the following brace; sensitive shadows and malformed or unbalanced scopes then
reject normally. Leading assignment words and reserved control prefixes are
removed before executable dispatch analysis, so they cannot hide writers,
callbacks, traps, or helper calls.
Command records retain preceding and following control operators across
physical lines and backslash continuations. Canonical cgroup initialization,
join, bind, read-only remount, inode verification, supervisor alias, trap, and
membership checker actions must each occur exactly once without `&&`, `||`,
`|`, `|&`, or background `&` edges. Quoted and escaped operator text remains
ordinary data.
The same parsed records, including operator edges, nested execution scopes, and
derived control scopes, form each reviewed helper identity. Backgrounding or
pipelining any helper command therefore changes the inventory and rejects;
unreviewed helper calls reject such topology at call time.
One central mandatory-context predicate covers the initializer, join, bind,
remount, supervisor alias, inode check, trap, and checker, so none can be
accepted inside a conditional even without an operator edge. `for` and
`select` iteration targets may not overwrite cgroup, supervisor, dispatch, or
tracked alias state.
Runtime wrapped-unset controls leave `supervisor_cgroup` unbound before the
safe read, proving the mutation. Array-backed executable slots are always
rejected by this security guard. Array-backed `command`/`builtin` option,
mutator, or target slots are likewise rejected when supervisor authorization
is involved. This intentionally includes query-only array flags such as
`-pv`/`-pV`: Bash leaves the alias unchanged at runtime, but the parser does
not trust ambiguous array-backed dispatch. Scalar/literal query clusters
remain valid. Scalar alias assignments and appends containing command/process/
arithmetic substitution, backticks, unresolved parameter transforms, or
runtime filename expansion retain dynamic taint rather than becoming ordinary
literals. The taint may remain ordinary data, but fails closed when it reaches
an executable, wrapper, option, mutator, target, or supervisor-sensitive path.
Runtime `$(printf supervisor_cgroup)`, backtick, and parameter-transform
controls otherwise unset or rewrite the trusted alias; fixed single-quoted
literal spellings remain accepted.
The alias analysis also applies state changes made by variable-writing
builtins. A fixed `printf -v` target with a modeled literal or `%s` value
replaces the prior alias value; other formats, `read`, `mapfile`, `readarray`,
and bare declarations taint the destination until its use is proven safe.
Assignments and initialized declarations retain exact or tracked values.
After wrapper and scalar-alias resolution, declaration assignment targets and
values apply once; an ordinary direct assignment is left to the normal
assignment path, so `+=` is never duplicated. Dynamic writer targets, values,
namerefs, or options fail closed. Executable controls prove that each writer
can retarget a previously safe alias to the raw membership file, while exact
safe declaration and `printf -v` overwrites clear prior raw taint.
Nameref creation is never modeled or allowed in the trusted builder.
Direct, clustered, repeated, toggled, wrapped, aliased, array-backed, and
dynamic `n` options for declare/typeset/local/export/readonly fail closed,
including eval/source/function ambiguity. Checked transport readers use fixed
result arrays rather than `local -n`. Runtime controls prove plain `unset`,
assignment, and redirected `read` write through a nameref to the underlying
supervisor alias; ordinary non-`n` declarations remain valid.
The reviewed builder also permits no alias/unalias/shopt/enable/hash dispatch
configuration. Direct, `command`/`builtin`, aliased, dynamic, and array-backed
forms all reject; shopt set/unset/query and clustered option forms reject
uniformly, including `expand_aliases`. BASHOPTS, SHELLOPTS, BASH_ENV, ENV, PATH,
`set -h/+h`, hashall, and POSIX-mode mutation fail closed. Variable targets
for `unset`, `printf -v`, `read`, `readarray`, `mapfile`, and declaration/
environment builtins are resolved through scalar chains and `+=`; array,
substitution, transform, and other dynamic targets remain tainted and reject.
`set -o/+o` option names receive the same treatment. Runtime proves an enabled
alias can otherwise dispatch `printf -v` and rewrite the trusted supervisor
alias, resolved targets can rewrite `PATH` and `BASH_ENV`, and resolved option
names can enable `posix` and `hashall`. Fixed single-quoted target/option
literals do not execute and remain accepted. Query-only `command -v/-V`,
ordinary `set ±e`, and current non-alias commands remain valid.
Explicit builder exits are parsed as a count-preserving multiset. Reordering
unchanged exits is valid, while an added, removed, or duplicated exit changes
the contract. The machine-readable `security_contract` object in
`TC-CI-PATCH-049-002` freezes publisher outputs, diagnostics, launcher and
cgroup invariants, raw-membership access, alias-state handling, and evidence
kind; explanatory Markdown remains informational and may be reworded without
changing behavior.
Tracked raw-root filename components also reject
glob, brace, bracket, extglob, command/process/arithmetic substitution, tilde,
and other dynamic syntax unless the command is the exact join write.
Executable brace and `?` glob controls otherwise read the raw marker after the
safe supervisor read; the semantic guard rejects both.
After the isolated builder is spawned, trusted wrapper failures emit
only fixed `launch`, `isolated`, or `cleanup` stage codes with numeric exits,
never candidate-controlled output. `launch` covers only the bounded,
kernel-derived stopped-session identity and exact resume operation; its detail
is one fixed enum value, never a PID or process text. `isolated` reports the
authenticated supervisor's exit-status substage. Root and candidate traps map
only namespace, mount-audit, candidate preflight/venv/pip/build-tools/make/
handoff, output-validation, export, and post-check failures to reserved numeric
codes. The outer host accepts those codes only from `wait` on the exact
authenticated supervisor and renders a fixed enum; any other exit or signal
becomes `detail=transport exit=125`. The channel has no file, pipe, candidate
descriptor, symlink, nonregular inode, or path race, and candidate text or data
never enters it. Explicit trusted namespace and mount-audit rejections call
the current-stage normalizer directly; the ERR trap covers ordinary command
failures and propagated helper `return 125` paths. The only remaining explicit
shell exits are the normalizer's reserved codes, intentional candidate-status
forwarding, and success `0`, so explicit failures cannot fall through to
`transport`. Candidate-launcher status is captured with an `if` conditional,
which suppresses ERR handling for that command under Bash semantics; `set +e`
is intentionally absent because it does not disable an active ERR trap.
Candidate codes `71` through `76` therefore reach forwarding exactly once,
launcher validation `125` and exec failure `126` pass through to the outer
transport rejection, arbitrary other nonzero candidate status becomes `77`,
and success continues as zero. `cleanup` reports teardown summary status whenever teardown fails.
Earlier trusted pre-spawn setup and later post-child handoff validation still
use normal shell failure output and are outside this diagnostic enum; cleanup
may therefore be the only stage text even when the failure began before spawn.

Before candidate code starts, its PID-1 wrapper redirects inherited standard
input/output/error permanently to private `/dev/null`. A trusted isolated
Python child launcher closes every inherited descriptor above 2, loads the
root-owned candidate script into Bash `-c` argv, and then executes `setpriv`.
Thus
`/proc/*/fd`, `/dev/stdout`, `tee`, shell xtrace, forks, and helper/logger pipes
can reach only the null device, never the Actions log. `/dev/console` and `/dev/kmsg`
are absent. `GITHUB_STEP_SUMMARY`, `GITHUB_OUTPUT`, `GITHUB_ENV`, and
`GITHUB_PATH` are not passed. Candidate-writable source, home, temporary,
handoff, `/tmp`, and shared-memory filesystems have explicit size limits; file
size, open files, processes, virtual memory, and core dumps have ulimits.
Candidate output is never replayed, logged, or uploaded, and arbitrary output
volume cannot fail an otherwise successful build. No output sink exists. The
trusted host reports only fixed success/failure text and a numeric exit
classification for those post-spawn `launch`/`isolated`/`cleanup` outcomes; it
does not claim path-free diagnostics for earlier trusted setup or later
post-child handoff validation.

The post-candidate cgroup membership check uses fixed
`/usr/bin/python3 -I -S`, not mutable shell state. Exact failing master
`5779c38e245d9a14f063338b53851a97bb92d0c0` invoked `sort` from inside the
builder cgroup to read `cgroup.procs`; the kernel therefore included that
reader in the snapshot, so its single-wrapper assertion necessarily failed.
The replacement checker expects its own transient PID plus the wrapper
PID in either order from literal
`/mnt/supervisor/cgroup/cgroup.procs`. Its exact AST fixes the path, byte bound,
canonical positive-decimal grammar, cardinality, and PID set. Empty, malformed,
signed, whitespace-padded, zero, duplicate, missing-newline, oversized, and
additional-member snapshots fail before export without signaling another
process.
The semantic parser tracks shell control scopes and authorizes the checker
only once on the unconditional `builder_main` path. `if`, loop, or other
conditional placement rejects even when the checker heredoc and AST remain
byte-for-byte valid.
Parenthesized subshells, multiline `$()`/backtick command substitutions, and
input/output process substitutions are retained as nested execution scopes.
The checker and every other mandatory action reject in those scopes, including
when a following `|| true` would otherwise permit export.
Parenthesis scopes retain the lexical quote context that opened them; quoted
or escaped `)` data cannot close an outer substitution or subshell. Nested
substitutions and mixed quoted/unquoted words therefore keep the checker in
its true execution scope.
Attached redirections are split only in unquoted syntax-active segments,
including descriptor, `<`, `>`, `<<`, and `<<<` forms. Protected `read`,
`mapfile`, and `readarray` targets are recognized with or without whitespace,
while quoted redirection characters and process substitutions remain data or
redirection inputs as Bash defines them.
Reviewed helper declarations include their outer control, execution, and
operator scopes. Each helper definition must execute unconditionally before
its first call or trap use; safe reordering remains allowed only while every
definition stays in the pre-use region.
The shared command lexer recognizes generic `<<`/`<<-` heredocs, attached and
descriptor forms, quoted or escaped delimiters, continuations, tab stripping,
and multiple bodies in declaration order. Heredoc bodies and terminators never
become command records or satisfy mandatory/helper/state counts. Unquoted
bodies containing active parameter, command, arithmetic, or backtick expansion
fail closed; quoted bodies remain inert data. `<<<` here-strings remain normal
redirections and are never consumed as heredoc bodies.
Heredoc declaration discovery uses the same syntax-active pre-comment prefix
as command splitting. An unquoted `#` starts a comment only at a Bash word
boundary; quoted, escaped, in-word, and `${value#pattern}` hashes remain data.
Consequently commented fake `<<`/`<<-`/fd/multiple delimiters cannot hide
subsequent commands, while actual heredocs with trailing comments still bind
their bodies.
ANSI-C `$'…'` and locale `$"…"` quote prefixes have explicit lexical states.
ANSI escapes, including escaped quotes, are decoded for ordinary tokens and
heredoc delimiters; hashes, operators, and parentheses remain inert inside the
quote. Locale strings retain double-quote expansion behavior. Arithmetic
`$((…))`, `((…))`, and legacy `$[…]` contexts track nested delimiters, so
`<<`/`>>` and base-`#` syntax cannot become comments, redirections, or
heredocs; a real `<<` outside arithmetic remains a heredoc.
Syntax-active shell metacharacters restore Bash word-boundary state for comment
recognition, including no-space `(`, `)`, `;`, `&&`, `||`, `|`, `&`, `<`, and
`>`. A following unquoted `#` therefore starts a comment where Bash does.
Hashes remain data inside words, parameter operators, arithmetic bases, array
subscripts, quotes, escapes, and quoted/escaped redirection targets. Heredoc
discovery consumes only this corrected pre-comment token stream.
Nested physical-line execution state distinguishes true subshell/group
parentheses from word-embedded command, backtick, and input/output process
substitutions. Closing a group restores a token boundary; closing an embedded
substitution resumes the same word, so `$(…)#` and `<(…)#` keep `#` literal
while `(…)#` begins a comment. Quote ownership, nesting, escaped parentheses,
adjacent suffixes, and inner newlines preserve that distinction. A real
heredoc following a substitution-word hash is still queued, and its body
cannot satisfy mandatory actions.
Arithmetic state also distinguishes standalone command `((…))` from embedded
`$((…))` and legacy `$[…]` expansion. Closing an arithmetic command restores a
token/comment boundary, including `if`, `while`, and `for ((…))` contexts;
closing an expansion resumes its containing word. Nested parentheses and
command substitutions suspend and restore the outer arithmetic frame.
Adjacent comments, suffixes, redirects, and operators therefore follow Bash
semantics, and malformed frames fail closed.
The fixed `checked_supervisor_transport_output` and
`checked_runtime_transport_output` arrays are reserved transport results.
Only their matching reviewed reader helper may populate them, with the
supervisor producer called twice and runtime producer once on unconditional
main paths. Scalar/indexed/compound assignments, declarations, unset,
nameref, printf/read/mapfile writers, loop targets, callbacks, traps,
functions, dynamic targets, duplicate producers, and conditional producers
reject; consumers cannot observe stale or replaced results.
An exact phase event sequence makes each array unavailable before production
and binds every active length/key/index/copy/arithmetic/loop/redirection/
indirect consumer to the latest intended generation. Supervisor phases cover
the initial descendant-unmount list and later `/dev` singleton check; the
runtime phase covers writable-mount pairs. Pre-use, duplicate, reordered,
interphase, stale, or extra consumers reject. Runtime dereferences in helpers,
nested helpers, substitutions, callbacks, and traps are evaluated when invoked
and reject because all reviewed consumers are exact top-level phase events.
Direct, indirect, nameref, arithmetic, and array dereferences are consumers;
transporting or comparing the literal reserved name remains data, as does
literal quoted text. Bash `${!prefix*}`/`${!prefix@}` parameter-name
enumeration uses the literal prefix rather than its value, and unrelated
associative-array subscripts use string keys; neither is a transport read.
Indexed and unknown array subscripts retain arithmetic recursive resolution,
while `${!array[@]}`/`${!array[*]}` consumes keys only when `array` is a
reserved transport result or a nameref to one. Once Bash establishes indexed
or associative type, compound, scalar, append, and element assignments retain
that type. Element and `[@]` unsets retain it; whole-variable unset erases it.
An opposite-type nonlocal redeclaration fails without retagging, while a first
local declaration creates its own typed shadow. Proven readonly attributes are
also retained: rejected unset, assignment, append, element write, retag, and
readonly-removal attempts cannot change the modeled value or array type.
Readonly changes in a subshell remain scoped to that execution environment.
The readonly attribute is removed from semantic alias resolution, so alias
chains, indirection and its parameter operators, namerefs, arithmetic, helper
captures, and raw-path checks still see the underlying value.
Arithmetic commands, `let`, `$((...))`, `$[...]`, arithmetic `for` clauses,
integer declarations, nested assignments, and subscript side effects reject
writes to protected or tracked state across assignment, compound assignment,
and pre/post increment/decrement. Pure transport reads remain phase consumers;
provably readonly arithmetic writes leave modeled state unchanged.

Only after that teardown does the curl-only secret step create an
unpredictable `0700` directory and `0400` regular 16 MiB file.
The immediately following step runs only the staged tool through absolute
isolated Python from an empty runtime CWD/environment. No repository command
runs while the base exists. Success/failure traps remove the base and its
directory, a separate step verifies absence, and only then may the three-file
patch artifact be uploaded. A separate final step revalidates the exact regular,
single-link BPS/manifest/README allowlist after private cleanup and immediately
before upload, so no late candidate or process mutation can enter the artifact.

## Artifact contents and verification

Only a successful trusted `push` to `master` can upload
`modern-release-all-locales-all-features-aapcs-bps-<commit>` with 30-day
retention. Pull requests receive no base-input secret and publish nothing. The
artifact contains exactly:

```text
fireemblem8-expansion-all-locales-all-features-aapcs.bps
manifest.json
README.txt
```

The stdlib-only audited producer/applier is
`scripts/modernize/bps_patch.py` (`stdlib-bps-source-target-read-v1`). It emits
deterministic, position-aligned BPS SourceRead runs for unchanged base bytes
and TargetRead runs only for changed spans; the patch therefore cannot
reconstruct the release without the exact checked base. It validates all BPS
source, target, and patch CRCs. The manifest is canonical JSON and binds the
commit, full profile, configuration fingerprint, complete base record
(size, SHA-256, SHA-1, and header), output/patch sizes and hashes, producer
identity, and embedded output metadata.

After downloading the artifact and supplying a legal base locally, first
validate the artifact without uploading either input or result:

```bash
python3 -m scripts.modernize.patch_release verify \
  --base /path/to/legal-fe8u-rev0 \
  --artifact-dir /path/to/unpacked-artifact
```

`verify` validates the base, allowlist, manifest, BPS checksums, reconstructed
target digest, and embedded metadata. It reconstructs the target only in
memory and **does not write an output ROM**. To write a separately named local
output after validation, use the audited BPS applier:

```bash
python3 -m scripts.modernize.bps_patch apply \
  --source /path/to/legal-fe8u-rev0 \
  --patch /path/to/unpacked-artifact/fireemblem8-expansion-all-locales-all-features-aapcs.bps \
  --output /path/to/patched-fireemblem8-expansion.gba
```

The apply command validates BPS source, target, and patch CRCs before writing
the chosen output path. Do not overwrite the independently obtained base, and
do not publish the base or resulting ROM.

For a local build plus round-trip artifact check, use:

```bash
make expansion-modern-all-locales-all-features-patch-check \
  PATCH_BASE_ROM=/path/to/legal-fe8u-rev0
```

The verifier rejects an absent/extra artifact file, a noncanonical or
inconsistent manifest, wrong base digest/header, corrupted BPS, output digest
mismatch, or embedded metadata mismatch. It does not distribute or print
restricted bytes.

## Tester cases

The canonical human procedures and machine-indexed definitions are
[`TC-CI-PATCH-049-001` and `TC-CI-PATCH-049-002`](test-cases/patch-release.md).
They cover trusted local validation/application and fail-closed malformed or
untrusted inputs. Their dependency, conflict, save, cleanup, and automation
contracts are authoritative over this summary.
