# Patch-release artifact cases

These procedures cover issue [#49](https://github.com/laqieer/fireemblem8-expansion/issues/49)'s
transient BPS-only Actions artifact and the equivalent source profile. They
never distribute, commit, cache, or request publication of a private base. The
target ROM remains only in the publisher-local isolated handoff and private
staging. Actions uploads only BPS/manifest/README with 30-day retention; there
is no internal or final ROM artifact.

## TC-CI-PATCH-049-001: Validate and write a trusted BPS artifact locally

- **Feature / originating issue:** `patch-release-artifact` /
  [issue #49](https://github.com/laqieer/fireemblem8-expansion/issues/49).
- **Supported configuration or artifact:** the trusted, SHA-named
  `modern-release-all-locales-all-features-aapcs-bps-<commit>` artifact from a
  successful `master` push, or the equivalent source profile built with
  `make expansion-modern-all-locales-all-features-patch-check`.
- **Prerequisites and clean starting state:** start at the repository root
  with Python 3 and an unpacked three-file BPS artifact. Independently obtain
  the documented legal FE8U revision-0 base locally; do not publish, commit,
  upload, or share that base or any patched ROM. Choose a new output path and
  use blank or disposable SRAM when switching to the 32 MiB profile.

### Actions

1. Confirm the artifact directory contains only the named `.bps`,
   `manifest.json`, and `README.txt` files.
2. Validate the locally held base and unpacked artifact:

   ```bash
   python3 -m scripts.modernize.patch_release verify \
     --base /path/to/legal-fe8u-rev0 \
     --artifact-dir /path/to/unpacked-artifact
   ```

3. Write a separate local output without replacing the base:

   ```bash
   python3 -m scripts.modernize.bps_patch apply \
     --source /path/to/legal-fe8u-rev0 \
     --patch /path/to/unpacked-artifact/fireemblem8-expansion-all-locales-all-features-aapcs.bps \
     --output /path/to/patched-fireemblem8-expansion.gba
   ```

4. For the source-build equivalent, run:

   ```bash
   make expansion-modern-all-locales-all-features-patch-check \
     PATCH_BASE_ROM=/path/to/legal-fe8u-rev0
   ```

### Expected result

`patch_release verify` reports `patch release artifact verified` after
validating the exact legal base, canonical manifest, three-file allowlist,
BPS checksums, reconstructed 32 MiB target digest, and embedded all-locales/
all-features metadata; it writes no ROM. `bps_patch apply` succeeds only when
the BPS source/target/patch CRCs match and writes the selected separate output
path. The source target builds the same named release/AAPCS profile and
round-trips the local artifact. The trusted publisher that produced that
artifact decodes recursive `/dev` mount targets from structured `findmnt
--json --submounts --output TARGET /dev` output, writes those NUL-delimited
targets into checked root-owned regular temp files under `/mnt/supervisor`,
unmounts exact descendant paths deepest-first, removes the temp files, and
verifies that only `/dev` remains before recreating the private device tree.
The root-owned mode-`0700` `/mnt/supervisor` parent denies candidate read,
write, execute, and traversal; its exact cgroup child remains read-only and is
rechecked before ROM handoff.
Before candidate code starts, the trusted wrapper also decodes structured
`findmnt --json --list --uniq --output TARGET,OPTIONS -R /` output, writes
checked NUL-framed mount target/option records through a root-owned regular
temp file, and audits every effective writable mount in the isolated
namespace. util-linux documents `--uniq` as "effectively skipping over-mounted
mount points", so the audit sees the topmost visible layer for each target
rather than failing on legitimate duplicate rows from hidden lower mounts.
Only `/dev/shm`, `/mnt/handoff`, `/mnt/home`, `/mnt/source`,
`/mnt/supervisor`, `/mnt/tmp`, and `/tmp` may carry an exact `rw` option token.
`/mnt/supervisor` is the sole mount-level `rw` exception that candidate code
cannot read, write, execute, or traverse: mode-`0700` root ownership and
candidate negative access probes preserve the boundary without the invalid
late parent remount over its read-only cgroup child. Spaces and backslashes in
decoded target paths are handled losslessly, while control-character targets,
malformed option-token grammar, duplicate or extra JSON rows, raw escaped or
whitespace-delimited mount-target transport, and any unexpected writable
effective mount fail closed. Hidden lower layers remain irrelevant unless the
wrapper or candidate can expose them; this publisher denies that by keeping
the candidate unprivileged and never granting mount or unmount capability.

### Negative control

The English-only, default-off 16 MiB bare `make` path creates neither this
profile nor a trusted artifact. A wrong or one-byte-modified base causes
validation and BPS application to fail before a patched output is written.

### Interactions and save compatibility

This depends on the modern configuration metadata, generated data,
localization, linker-budget, boot/runtime, artifact guard, and `preserve` BGM
policy seams. It conflicts with untrusted secret access, output-root
contamination, a different base revision, and non-`preserve` BGM policy; there
are no other feature conflicts and no public C API, localization-ID, or
archival-lane change. The profile changes no save layout, migration, or
compatibility epoch, but a blank or disposable SRAM avoids cross-profile save
use.

### Automation

- `python3 -m unittest scripts.modernize.tests.test_patch_release -v` —
  `scripts/modernize/tests/test_patch_release.py` proves BPS checksums,
  generated README semantics, artifact validation, profile identity, and
  malformed-input failures with synthetic data.
- `make expansion-modern-all-locales-all-features-check` —
  `modern.mk` proves the isolated 32 MiB release/AAPCS profile, metadata,
  budget, boot, and runtime checks.
- `make expansion-modern-all-locales-all-features-patch-check PATCH_BASE_ROM=/path/to/legal-fe8u-rev0`
  — `modern.mk` exercises the real local create/verify round trip. Acquiring
  the legal base is intentionally manual because this repository publishes no
  ROM.

### Cleanup and limitations

Delete only the separately chosen local output and run `make clean_fast` if
the profile artifacts are no longer needed. The artifact expires after 30
days, and `patch_release verify` is validation only; it never writes an
output file or substitutes for the explicit BPS apply command.

## TC-CI-PATCH-049-002: Reject untrusted or malformed patch inputs

- **Feature / originating issue:** `patch-release-artifact` /
  [issue #49](https://github.com/laqieer/fireemblem8-expansion/issues/49).
- **Supported configuration or artifact:** a clean source checkout with
  Python 3 and the synthetic contract fixtures; an optional disposable copy of
  an independently acquired legal base may exercise the local command.
- **Prerequisites and clean starting state:** run from the repository root.
  Never alter, publish, upload, or commit a legal base; if testing a
  one-byte mutation locally, use only a disposable local copy and a new output
  path. No save, savestate, or emulator state is required.

### Actions

1. Run the synthetic malformed-base, corrupted-BPS, artifact-allowlist, and
   generated README/CLI tests:

   ```bash
   python3 -m unittest scripts.modernize.tests.test_patch_release -v
   ```

2. Run the trusted-event/no-PR-secret workflow contract:

   ```bash
   python3 -m unittest tests.workflows.test_patch_release_workflow -v
   python3 -m unittest tests.workflows.test_publisher_transport_helper_reads -v
   python3 -m unittest tests.upstream_port.test_verify -v
   ```

3. If a legal base is available locally, make a disposable one-byte-modified
   copy and run the `patch_release verify` and `bps_patch apply` commands from
   `TC-CI-PATCH-049-001` against that copy. Retain their nonzero status and do
   not disclose the copied bytes, path, or resulting error context.

### Expected result

Missing, malformed, wrong-size, wrong-header, wrong-hash, modified-base,
corrupted-BPS, noncanonical-manifest, extra-file, directory, and symlink
inputs fail closed. A wrong source never writes the requested BPS output, the
workflow is trusted `push` to `master` only, pull requests receive no base
secret or artifact upload, and diagnostics expose no protected base content.
The publisher finishes every repository-controlled command before download,
builds the exact validated after tree as a dedicated unprivileged UID in
mount/PID/network isolation with private propagation, recursively read-only
host root/system/tool mounts, private `/tmp`/`run`/`proc`/`dev`, and masked host
D-Bus/container/service sockets. Every builder descendant remains in one exact
cgroup v2. It transfers no complete target ROM through an Actions artifact,
cache, release, or log; rejects a device, symlink, hardlink, escaped path, or
unexpected isolated handoff output. With shell monitor mode disabled, a
trusted no-fork Python launcher calls `setsid()`, verifies its PID is both the
session and process-group ID, and self-stops before it can execute `timeout`,
`sudo`, `unshare`, or candidate code. The host authenticates that exact
stopped child from the kernel process table before resuming it. Cleanup
rechecks that PID/session before signaling the group, kills the exact builder
cgroup for namespace descendants that leave it, terminates and waits for the
exact launcher PID, proves the session, `cgroup.procs`, and builder UID are
empty, removes only owned state, and uses no broad UID signal. It stages its
producer from the same exact after commit without source hash pins, uses an
unpredictable mode-restricted base path, invokes only that staged tool through
absolute isolated Python while the base exists, removes it on success/failure,
verifies cleanup, revalidates the BPS/manifest/README allowlist immediately
before upload, and uploads only that patch artifact.
The launcher's parent-death `SIGKILL` prevents an unresumed authenticated
child from outliving the trusted shell. Before every cleanup signal, the host
immediately rechecks the immutable shell-parent PID, PID/SID/PGID tuple,
expected stopped/running state, and `/proc` start time. A missing, forged, or
parent-group identity during launch never records a session. The primary
`launch` rejection already reports failure; if the owned cgroup and builder
UID are empty, cleanup succeeds with no cleanup diagnostic. A cleanup summary
appears only for residual cgroup/UID state or an authenticated cleanup failure.
Stale or reused authenticated identities cause no PID or process-group signal;
only the owned cgroup kill remains available, and cleanup reports failure.
After a valid group signal, escalation requires another exact tuple check,
while the exact shell-child wait is used only to reap an observed exit.
Before candidate code, a trusted child launcher closes inherited descriptors
above 2, while stdin becomes private `/dev/null` and stdout/stderr permanently
target that same null device.
The candidate receives no GitHub workflow command-file paths and cannot recover
the Actions log through proc FDs, `/dev/stdout`, console/kmsg, `tee`, xtrace,
helpers, or forks. ROM-sized output is discarded and never replayed; arbitrary
output volume cannot fail an otherwise successful build. No output sink exists.
Fixed trusted text and a numeric exit classification preserve post-spawn build
failure without exposing candidate bytes. After the isolated builder is
spawned, `launch` covers only bounded stopped-session identity and exact resume
validation and emits one fixed detail enum, never process text or an ID.
`isolated` reports the authenticated supervisor's exit-status substage. Root
and candidate traps map only namespace, mount-audit, candidate preflight/venv/
pip/build-tools/make/handoff, output-validation, export, and post-check
failures to reserved numeric codes. The host accepts them only from `wait` on
the exact authenticated supervisor and renders fixed enum text; unknown exits
and signals become `detail=transport exit=125`. This authenticated transport
has no file, pipe, candidate descriptor, symlink, nonregular inode, or TOCTOU
path and carries no candidate output, data, path, or secret. Every terminal
trusted namespace and mount-audit rejection calls the current-stage normalizer
directly. The ERR trap handles ordinary command failures and propagated helper
`return 125` paths; only reserved normalization, intentional candidate-status
forwarding, and success `0` retain explicit shell exits. Explicit trusted
failures therefore produce exact `namespace` or `mount-audit` detail instead
of falling through to `transport`. Candidate-launcher status uses conditional
status capture, which suppresses ERR handling for that command under Bash
semantics. `set +e` is absent because it does not disable an active ERR trap.
Candidate codes `71` through `76` reach forwarding exactly once, arbitrary
nonzero candidate status becomes `77`, while launcher validation `125` and
exec failure `126` pass through to the outer `detail=transport exit=125`
rejection. Success remains zero. `cleanup` reports teardown summary status
whenever teardown fails. Earlier trusted pre-spawn setup and later post-child
handoff validation still use normal shell failure output and are outside this
stage enum; cleanup may therefore be the only stage text even when the failure
began before spawn.
The wrapper binds the exact owned cgroup read-only under root-only mode-`0700`
`/mnt/supervisor` before masking `/sys`. The candidate cannot read, write,
execute, or traverse that parent, while the exact cgroup child remains
read-only; fixed isolated Python accepts only the wrapper and its own transient
checker PID before ROM handoff, then exits before export. Only the exact
initial `printf` join writes
raw `$cgroup_path/cgroup.procs`; bind identity uses directory inodes and every
membership read uses the read-only supervisor view, so no raw `cgroup.procs`
read remains. Exactly one main-scope `cgroup_path="$1"` initialization is
required on the empty, unconditional `builder_main` control path before
join/bind/stat use; conditional/grouped placement and all later assignment,
array, declaration, writer, unset, function, and dynamic mutations reject.
Quote-segment-aware
resolution expands only unquoted and double-quoted active segments.
Single-quoted or escaped dollars remain literal, while mixed active/inert
segments concatenate without recursive expansion. Parsed mutations preserve
the fixed checker while adding
raw mapfile/readarray, cat, sort, redirection, environment/shell wrappers,
spacing, direct aliases, and composed root/leaf aliases; all fail closed.
Braced default/assign/error/alternate, prefix/suffix removal, substring, case,
transformation, length, and indirect operators fail closed whenever they use
the tracked raw root, in quoted, unquoted, direct, or aliased form. Active
nested braced expansion rejects conservatively; single-quoted text stays inert.
Scalar
`+=` appends to tracked aliases, so `raw_leaf=cgroup; raw_leaf+=.procs` cannot
hide the membership path. Indexed and associative assignments/declarations,
array literals, indexed `+=`, `[0]`/`[key]`/`[@]`, indirection, and nested
indices reject tracked data immediately or fail closed when an ambiguous
expansion is combined with `cgroup.procs`. An executable control proves
`${raw[0]}` reads the raw marker after the safe supervisor read without the
guard, while unrelated arrays remain accepted. Builder argument `$1`/`${1}`
is seeded as the raw root through quoted/unquoted use, every tracked braced
operator, indirection, scalar and array aliases, `+=`, and indexed assignment.
A positional-alias runtime control proves the hidden read. Any shell command
consuming the raw root or composing `cgroup.procs` fails unless it is an exact
reviewed join/bind/stat signature; absolute programs are not exempt. Unrelated
`$2` forms stay valid. The fixed Python read is authorized only after the exact bind, read-only remount,
single canonical supervisor assignment, and directory-inode verification.
Literal, alias, parameter-operator, append, indexed/associative array, and
`unset` reassignments invalidate it before the safe line.
Function-local positional parameters do not inherit the builder's `$1` model.
Calls to unanalyzed helpers reject tracked raw-root arguments and split
arguments that compose `cgroup.procs`; direct, `function` keyword, nested, and
aliased call controls all read the raw marker without the guard. Exact
production helper call signatures are the only reviewed exception for
ambiguous transport values. The parsed helper inventory compares exact names,
body-command/operator/execution/control-scope identities, and multiplicities
without depending on definition order; added, removed, duplicated, modified,
backgrounded, or pipelined helpers reject. Calls
re-evaluate authenticated bodies against current caller aliases, so a
no-argument closure defined while `raw_root` is safe rejects after assignment
or `printf -v` retargets it. Calls initialize a sequential frame with caller
globals and callee positional arguments. Local assignment, declaration, and
modeled `printf -v` overwrites affect subsequent commands and disappear on
return; global writes propagate. `read`, `mapfile`, `readarray`, dynamic
values, and branch- or loop-dependent writes taint or reject conservatively.
Recursive and dynamic calls reject. Function
declarations cannot shadow security-sensitive builtins or commands, and
inline, dynamic, duplicate, and ambiguous definitions reject. Every trap
change except literal `trap isolated_stage_failure ERR` rejects, including
wrapped/dynamic DEBUG, RETURN, and EXIT forms. `mapfile`/`readarray` callbacks
reject, including dynamic `-C`. The membership decision has no mutable
`cgroup_members` shell variable: fixed isolated Python validates the literal
supervisor path and exact wrapper/checker PID set. Its AST is checked
semantically; formatting-only changes remain valid while path, count, or set
changes reject.
Split `name()` followed by `{` is normalized as one pending declaration before
inventory and scope processing, so it cannot hide a sensitive function shadow.
Leading assignment words and reserved `if`/`then`/loop/negation/group prefixes
are stripped before dispatch analysis. The checker must occur exactly once on
the unconditional `builder_main` path; false conditions, dynamic conditions,
and loops reject despite retaining the exact checker AST.
Parsed command records preserve control-operator edges over physical/logical
line continuations. The canonical initializer, raw join, supervisor bind,
read-only remount, inode verification, supervisor alias, reviewed trap, and
checker each require one operator-free execution record; `&&`, `||`, `|`,
`|&`, and background `&` placement reject. Quoted or escaped operator text
remains inert.
One central predicate combines operator edges, active control scopes, nested
execution scopes, and main-function position for every mandatory action.
Protected `for`/`select` targets cannot overwrite cgroup, supervisor, dispatch,
reserved membership, or tracked alias state. Parenthesized subshells,
multiline command/backtick substitutions, and input/output process
substitutions retain scope; mandatory actions reject within them even when
`|| true` would otherwise continue to export.
Each parenthesis scope is owned by the quote context that opened it, so
single/double-quoted or escaped `)` text cannot close an outer substitution.
Attached redirections are lexed from unquoted syntax-active segments,
including descriptor, here-string, and here-document forms; protected
read/mapfile/readarray targets reject regardless of whitespace. Quoted
redirection text and process-substitution inputs remain valid.
Helper declaration identity includes outer control/execution/operator scope,
and encountered-definition state requires every helper to execute before its
first call or reviewed trap use. Reordering is valid only within that pre-use
region.
Generic `<<`/`<<-` heredocs are lexed before command records, including
attached/fd forms, quoted or escaped delimiters, line continuations, tab
stripping, and multiple bodies in declared order. Body and delimiter lines
cannot satisfy mandatory action, helper, order, or state-write counts.
Unquoted expansion-active bodies reject; quoted bodies remain data. `<<<`
here-strings stay ordinary redirections.
Heredoc detection receives only the shared syntax-active pre-comment source.
`#` begins a comment only unquoted at a valid word boundary; escaped, quoted,
in-word, and parameter-operator hashes remain data. Commented fake
`<<`/`<<-`/fd/multiple delimiters cannot consume following commands, while a
real heredoc may retain a trailing comment.
ANSI-C `$'…'` and locale `$"…"` prefixes use dedicated states. ANSI escapes
and escaped quotes dequote ordinary words and heredoc delimiters while keeping
hashes/operators/parentheses inert; locale strings keep double-quote
expansion. Arithmetic `$((…))`, `((…))`, and `$[…]` nesting prevents `<<`,
`>>`, or base-`#` syntax from being interpreted as comments, redirections, or
heredocs.
Syntax-active grouping, control, and redirection metacharacters restore Bash
word-boundary state, including no-space `(`, `)`, `;`, `&&`, `||`, `|`, `&`,
`<`, and `>`. A following unquoted `#` starts a comment, while hashes inside
words, parameter operators, arithmetic bases, array subscripts, quotes,
escapes, and quoted/escaped redirection targets remain data.
Physical-line execution frames distinguish token-ending subshell/group
parentheses from word-embedded command/backtick/process substitutions.
`$(…)#` and `<(…)#` retain a literal hash in the same word, while `(…)#`
starts a comment. Nested, quoted, escaped, adjacent, and multiline variants
retain their owner context, so a real heredoc after an embedded-substitution
hash remains queued and its body stays inert.
Standalone arithmetic command `((…))` and embedded `$((…))`/`$[…]` expansions
have distinct frame kinds. Command close restores a comment boundary in
standalone, `if`, `while`, and `for` positions; expansion close resumes the
same word. Nested parentheses and command substitutions restore the outer
arithmetic frame, while adjacent comments/suffixes/redirections/operators
retain Bash behavior and malformed frames reject.
`checked_supervisor_transport_output` and
`checked_runtime_transport_output` are reserved after their exact reviewed
reader writes. The supervisor reader runs twice and runtime reader once on
unconditional main paths. Every other assignment, declaration, unset,
nameref, writer builtin, loop target, callback, trap, helper, dynamic target,
duplicate producer, or conditional producer rejects, preventing skipped
unmounts and empty writable-mount validation.
The semantic phase machine starts each array unavailable and admits only the
reviewed producer/consumer event sequence. Active length, key, index, copy,
arithmetic, loop, redirection, indirect, and dynamic reads bind to the latest
generation. Pre-use, stale/interphase, duplicate, reordered, and extra
consumers reject; the two supervisor generations remain distinct.
Helper bodies consume only when invoked. Any direct, indirect, nameref,
arithmetic, default-expression, or array dereference in a helper,
nested-helper, substitution, callback, or trap rejects as an unreviewed
consumer. Passing, printing, or comparing the literal reserved identifier
remains data; inert quoted helper data remains nonexecuting. Prefix-name
enumeration (`${!prefix*}`/`${!prefix@}`) does not resolve `prefix`'s value.
Known associative-array keys remain string data, whereas indexed and unknown
subscripts retain Bash arithmetic recursive-dereference checks. Direct
reserved-array key enumeration remains a consumer. Array type persists across
compound, scalar, append, and element assignment plus element/`[@]` unset;
whole-variable unset erases type. Opposite-type nonlocal redeclaration errors
without retagging, and a first local declaration creates a typed shadow.
`readonly`, `-r`, and typed readonly declarations preserve value and type
across rejected unset/write/retag/removal attempts, including suppressed
failures; subshell readonly state does not escape.
Readonly is attribute metadata rather than value data: direct/indirect alias
chains, `${!name}` assignment/default/error operators, arithmetic, namerefs,
helper captures, and raw paths resolve the unchanged semantic value.
Arithmetic lvalues are parsed across standalone commands, `let`, expansions,
legacy expansions, `for` clauses, integer declarations, nested expressions,
and subscript side effects. Every protected/tracked write form rejects while
unrelated writes and reviewed pure-read phase events remain valid.
Unresolved environment/positional/default expansions, substitutions,
backticks, ambiguity markers, and dynamic nameref targets fail closed through
multi-hop readonly or mutable arithmetic/subscript aliases; fixed constants pass.
Syntax-active `coproc`, `getopts`, `shift`, and positional-mutating `set`
forms reject globally, including aliases, wrappers, helpers, and control
placements. Reviewed shell-option `set` forms and quoted data remain valid.
The `set` grammar admits valid reviewed short flags, named `-o`/`+o` toggles,
and nonmutating bare output forms; invalid long/cluster/name/dynamic forms,
operands, and `--` reject.
Redirections are removed without truncating later argv; only cluster-ending
`o` consumes the next option name, matching `set -Eeuo pipefail`.
FD duplication/close and combined output redirects remain atomic; true
background, AND-list, and pipeline operators retain their topology.
Every `wait -p` output-variable form rejects, including clustered/attached
options and dynamic or wrapped destinations; reviewed ordinary waits remain.
`BASH_CMDS` and `BASH_ALIASES` reject every modeled writer. Function shadows
reject the canonical audited builtin set, including `trap`, `exec`, `ulimit`,
`return`, `cd`, and `exit`.
`command` (`-p`/`--`) and `builtin` prefixes are normalized after resolving
wrapper, builtin, and target aliases. Wrapped unset and mutating declare/
typeset/local/export/readonly/read/mapfile/readarray/`printf -v`, eval, source,
and ambiguous mutation surfaces fail closed. `command -v/-V` queries and
unrelated wrapped commands remain valid. Clustered `command` query options
`-pv`, `-vp`, `-pV`, `-Vp`, and repeated/mixed `p`/`v`/`V` forms are
nonmutating; `-p` alone keeps normal execution semantics, and clusters
containing any other character fail closed. Executable runtime controls prove
both query preservation and invalid-option status `2`; wrapped-unset controls
prove the safe read would otherwise encounter an unbound variable.
Array-backed executable slots are universally rejected; array-backed
`command`/`builtin` options, mutators, and targets reject when supervisor
authorization is involved. Runtime array wrappers and `-pp` flags prove the
mutation. Query-only array `-pv`/`-pV` forms are nonmutating in Bash but
conservatively rejected because the security parser does not normalize
ambiguous array-backed dispatch. Scalar/literal query clusters remain valid.
Scalar alias assignment/append values containing command/process/arithmetic
substitution, backticks, unresolved parameter transforms, glob/brace/tilde, or
other runtime evaluation retain ambiguous dynamic taint. Ordinary data use is
allowed, but executable, wrapper, option, mutator, target, and supervisor-
sensitive sinks reject. Runtime command-substitution, backtick, and
parameter-transform targets otherwise unset or rewrite `supervisor_cgroup`;
fixed single-quoted literal spellings remain accepted.
Variable-writing builtins update alias state after each command. Fixed
`printf -v` targets propagate modeled literal or `%s` values exactly; other
formats, `read`, `mapfile`, `readarray`, and bare declarations taint their
destinations. Assignments and initialized declarations retain their exact or
tracked values. Resolved declaration assignment targets and values apply once;
direct syntactic assignments remain on the ordinary assignment path, including
single `+=` application. Dynamic writer targets, values, namerefs, and options
reject immediately. Runtime controls retarget a safe alias through each writer
and read the raw membership marker; exact safe declaration and `printf -v`
overwrites clear prior taint and read only the safe marker.
The trusted builder contains no nameref. Direct `-n`, clustered/repeated
options containing `n`, `+n` toggles, wrapped/aliased builtins, array/dynamic
options, assignments, declarations, and eval/source/function ambiguity all
fail closed for declare/typeset/local/export/readonly. Checked transport
readers use fixed result arrays instead of `local -n`. Runtime controls prove
nameref `unset`, assignment, and redirected `read` mutate the underlying
supervisor alias; ordinary non-`n` declarations remain valid.
Alias, unalias, shopt, enable, and hash dispatch configuration is prohibited
through direct, `command`/`builtin`, aliased, dynamic, or array-backed forms.
All shopt modes, including query and clustered options for `expand_aliases`,
reject. BASHOPTS/SHELLOPTS/BASH_ENV/ENV/PATH and hashall/POSIX dispatch
configuration reject. Scalar targets and option names resolve through aliases,
chains, and `+=`; array, substitution, transform, and other dynamic values
remain tainted at `unset`, `printf -v`, `read`, `readarray`, `mapfile`,
declaration/environment, and `set -o/+o` sinks. Query-only `command -v/-V`,
ordinary `set ±e`, and current commands remain valid. Runtime controls prove
an executable alias can dispatch `printf -v` and rewrite the trusted
supervisor alias, indirect targets can rewrite `PATH` and `BASH_ENV`, and
indirect options can enable `posix` and `hashall`; fixed single-quoted target
and option literals do not execute and remain accepted.
The explicit exit inventory is a parsed multiset: source reordering preserves
the contract, while addition, deletion, or duplication fails its exact count.
The parsed `security_contract` registry object is the behavioral documentation
oracle for outputs, diagnostics, launcher/cgroup invariants, membership
access, alias-state writes, and evidence type. Prose is informational, so an
equivalent rewording does not alter the automated result.
Raw-root filename
glob, brace, bracket, extglob, command/process/arithmetic substitution, tilde,
and other dynamic syntax fail closed. Executable `cgroup{.,_}procs` and
`cgroup.proc?` controls both read the raw marker after the safe supervisor read
without the guard.
Decoded recursive `/dev` mount targets are
emitted through NUL-delimited trusted JSON parsing, staged through checked
root-owned regular temp files under `/mnt/supervisor`, unmounted deepest-first,
and rechecked so only `/dev` remains before the private device tree is
recreated. Retained descendants, raw escaped or whitespace-delimited
mount-target transport, paths outside `/dev`, malformed JSON, duplicate
targets, NUL-bearing targets, and unsafe transport files are rejected.
After the private device tree is recreated and before candidate code starts,
the trusted wrapper decodes structured `findmnt --json --list --uniq --output
TARGET,OPTIONS -R /` output into checked NUL-framed mount target/option
records, then audits every effective writable mount. util-linux documents
`--uniq` as "effectively skipping over-mounted mount points", so legitimate
duplicate target rows from hidden lower layers do not fail the audit and do
not hide the topmost visible mount. Only `/dev/shm`, `/mnt/handoff`,
`/mnt/home`, `/mnt/source`, `/mnt/supervisor`, `/mnt/tmp`, and `/tmp` may
expose an exact `rw` option token. The root-owned mode-`0700`
`/mnt/supervisor` is the sole mount-level `rw` exception that candidate code
cannot read, write, execute, or traverse; this avoids the invalid late parent
remount over its read-only cgroup child without granting candidate access.
Decoded targets with spaces or backslashes remain lossless; control-character
targets, malformed or ambiguous option-token grammar, duplicate or extra JSON
rows, raw escaped or whitespace-delimited mount-target transport, parser
failure, unchecked process substitution, and any unexpected writable
effective mount fail closed. Hidden lower layers remain irrelevant unless the
wrapper or candidate can expose them, and this publisher never grants that
capability.

### Negative control

A valid synthetic three-file artifact with the matching synthetic base
round-trips successfully; it proves the rejection tests are not
success-shaped. For issue #177's publisher regression, exact failing master
`8d81c30b298ef6265ba9c5335c3ca8c8f94e60e6` rejects the root-only writable
`/mnt/supervisor` during the effective-mount audit, while the fixed workflow
accepts that path and still rejects every candidate access probe. Exact
failing master `0456f181ad53645a7bc2b677abab05978ab9f35c` then rejects a valid
asynchronous `setsid` wrapper because `$!` need not equal its observed process
group. The fixed live namespace harness authenticates the self-stopped
launcher, resumes it, terminates the exact session and cgroup, and leaves no
orphan. Missing, forged, parent-process-group, and reused-start-time identities
leave an unrelated live process untouched; valid owned identity terminates,
and the cgroup path still removes namespace descendants. A disposable parent
exits before `SIGCONT`; the stopped launcher's saved PID/start-time identity
disappears promptly, its session has no descendant, and no orphan remains. The
exact failing master
`5779c38e245d9a14f063338b53851a97bb92d0c0` then reaches output validation but
fails because its `sort` reader joins the builder cgroup while reading
`cgroup.procs`, making its single-wrapper assertion self-defeating. The fixed
Python checker admits exactly the canonical wrapper and transient checker PIDs
in either order. Empty, malformed nonnumeric, blank, signed, zero,
leading/trailing-whitespace, missing-newline, oversized, duplicate,
external-only, and extra-before/extra-after snapshots all fail
before success or export markers and never signal the unrelated live process.
The default bare `make` path remains 16 MiB/default-off and does not receive a
base secret, patch artifact, or publish step.

### Interactions and save compatibility

Dependencies and conflicts are the same as `TC-CI-PATCH-049-001`: trusted
modern profile metadata and `preserve` BGM are required; untrusted events,
incorrect bases, and output-root contamination are rejected. No save field,
layout, migration, compatibility epoch, public C API, localization-ID, or
archival-lane behavior changes.

### Automation

- `python3 -m unittest scripts.modernize.tests.test_patch_release -v` —
  `scripts/modernize/tests/test_patch_release.py` covers deterministic
  malformed, allowlist, source-checksum, output-write, and no-path-disclosure
  controls.
- `python3 -m unittest tests.workflows.test_patch_release_workflow -v` —
  `tests/workflows/test_patch_release_workflow.py` maps the trusted event,
  secret scope, no-PR publication, candidate-before-download ordering,
  exact-after isolated tool, no-ROM-transfer boundary, dedicated builder UID
  and namespaces, read-only host/private-filesystem probes, exact cgroup-v2 and
  process teardown, decoded recursive `/dev` target parsing and deepest-first
  unmount order, the exact failing-master/current-workflow rootless namespace
  regression for the root-only writable supervisor mount, the exact
  failing-master PID/PGID mismatch and fixed self-stopped session-launcher
  runtime with no orphan, the disposable-parent pre-resume parent-death
  PID/start-time proof, missing/forged/parent-identity adversaries, and
  residual-state-only cleanup diagnostics, the closed authenticated isolated
  substage exit-status protocol, and exact failing-master `sort` self-
  observation versus the fixed wrapper/checker Python membership set, conditional candidate
  status capture with all six mapped stages plus unknown/success controls,
  the executable empty/malformed/duplicate/additional/order membership matrix
  with downstream-marker and external-process controls, safe-plus-raw cgroup
  read mutations across direct/wrapped/aliased forms in both workflow and
  upstream parsers, tracked braced-operator and scalar-append mutations,
  indexed/associative array mutations plus the executable hidden-read control,
  positional `$1`/`${1}` direct/alias/operator/array mutations plus runtime,
  unknown-root rejection and unrelated-parameter controls, launcher
  `125`/`126` transport controls, supervisor reassignment mutations and
  wrapped-unset runtime controls, unrelated wrapper/query negatives, and
  clustered `command` query/invalid-option controls, array-backed executable/
  wrapper/option/mutator/target mutations and query controls, executable
  brace/glob filename controls, dynamic scalar alias sink mutations and
  command-substitution/backtick/transform runtime controls, broad nameref
  declaration mutations and unset/assignment/redirected-read runtime proofs,
  alias/unalias/shopt/enable/hash and shell-option environment mutations plus
  the executable expand-aliases rewrite proof, scalar-chain/append/array/
  dynamic dispatch-target and `set -o/+o` option mutations plus executable
  PATH/BASH_ENV/posix/hashall and fixed-quoted-literal controls, stateful
  assignment/declaration/printf/read/mapfile/readarray writer mutations plus
  raw-marker and exact-safe-overwrite runtime controls, parsed exit multiset
  reorder/add/delete/duplicate controls, resolved declaration-target safe/raw/
  dynamic controls for declare/typeset/export/readonly/local in both parsers,
  direct/function-keyword/nested/aliased helper-call composition and
  sensitive-function-shadow mutations, no-argument assignment/`printf -v`
  closure captures, recursive/
  dynamic dispatch, helper inventory add/modify/duplicate/reorder controls in
  both parsers, sequential local/positional assignment/declaration/printf
  safe controls, read/mapfile/readarray/dynamic/branch taint controls, global
  write-back, nested-brace/trap/absolute-command/mapfile-callback/reserved-
  membership-state repros, fixed membership-checker runtime/AST mutations,
  canonical cgroup-path missing/duplicate/reassignment/writer mutations,
  conditional/branch/case/loop/group/dynamic initializer placement,
  single-quoted/escaped/mixed quote-segment controls,
  split-function shadowing, assignment/control-prefix writer execution, and
  conditional-checker skip/runtime controls in both semantic validators,
  mandatory-action `&&`/`||`/pipeline/background mutations with physical and
  logical continuations plus quoted/escaped operator controls,
  helper topology identity mutations, protected for/select targets, central
  mandatory control contexts, and multiline command/backtick/subshell/
  process-substitution checker bypasses,
  quote-owned parenthesis nesting, attached/fd/here-string/here-document
  redirections, conditional/subshell helper declarations, and call-before-
  definition runtime/mutation controls,
  generic heredoc initializer/trap/checker/join/helper spoofs, quoted/unquoted/
  escaped/attached/fd/`<<-`/multiple/continued delimiters, expansion side
  effects, and here-string distinction,
  commented fake delimiters hiding raw reads and protected mutations plus
  quoted/escaped/in-word/parameter-operator/trailing-comment controls,
  ANSI-C/locale quote and delimiter parity, escaped ANSI quotes, arithmetic
  shifts/base/nesting/legacy forms, and raw-read adversaries after inert quote
  or arithmetic prefixes,
  parenthesis/control/case no-space comment boundaries hiding raw reads plus
  word/parameter/arithmetic/array/quote/escape/redirection-target controls,
  command/backtick/input/output substitution word-continuation versus
  subshell/group boundaries, quoted/mixed/nested parentheses, and inert
  initializer heredoc bodies,
  standalone/if/while/for/nested arithmetic-command comment boundaries versus
  `$((…))`/`$[…]` word continuation and adjacent redirection controls,
  parameterized supervisor/runtime transport-result mutations plus executable
  descendant-unmount and writable-mount replacement bypasses,
  set-u pre-use failures, active consumer families, stale/interphase/order
  mutations, and the reviewed two-supervisor/one-runtime phase sequence,
  parsed registry-contract mutations,
  recursive
  command/process-substitution inspection for
  `$()`/backticks/`<(...)`/`>(...)`, structured `env -S` shell-c evasions
  through inline `else`/brace/case/loop forms, `setsid`-wrapped and common
  outer-wrapper (`nohup`/`taskset`/`ionice`/`flock`) `env`/BusyBox command
  slots, regular flock lockfile and command-string forms, clustered mount
  short-option remount parsing (`-ro`, attached/separate `-o`, and `-w`/`rw`
  override semantics), inline-function fail-closed behavior, literal
  quoted/escaped non-execution controls, socket/daemon/cgroup-escape
  adversaries, two-file handoff rejection controls, unpredictable private
  path, cleanup-before-upload, late artifact revalidation, null/no-replay
  candidate output adversaries, the old Bash-FD-255/memfd exit-125
  reproducer, inherited pipe/memfd/socket closure in the child launcher, and
  profile/verifier requirements.

### Cleanup and limitations

Remove only disposable local copies and outputs created for the negative
control. The legal-acquisition decision remains manual and private because no
ROM can be published; every deterministic validation is automated, and the
case does not test an unpublished base through CI.
