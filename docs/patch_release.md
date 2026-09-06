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

## Closed publisher command authority (issue #200)

The publisher has one **typed, default-deny command inventory** in
`scripts/workflow_pilot/publisher_signatures.py`. It is a framework capability,
not a gameplay option or a blacklist of sensitive filenames.

- `publisher_shell.py` parses the supported Bash subset without executing it.
  Words retain literal/parameter/substitution/arithmetical provenance and
  quoting. Redirects, environment prefixes, control branches, pipelines,
  backgrounds, and function declarations remain distinct.
- `publisher_inventory.py` matches complete signatures, scope, registered
  control/operator/background placements and exact per-placement occurrence
  counts. Operator position is part of the context: adding `&`, changing a
  pipeline/short-circuit, or moving a command into an existing branch does not
  preserve authorization. Main commands, nested data producers, and recursive
  helper expansion use the same inventory. Unknown commands, aliases,
  unregistered symbolic executable Words, unmatched arguments or redirects, callbacks,
  traps, and unregistered interpreter programs fail closed. A wrapper never
  hides its underlying command or loses its own arguments during normalization.
- `publisher_shell_contract.validate_builder_command_inventory` is the shared
  consumer entry point used by the publisher and upstream semantic validators.
  Historical narrow mount probes remain regression utilities; they cannot
  authorize a command or override an inventory rejection.
- `publisher_programs.py` is the single production source of the reviewed
  Python programs. The two mount readers retain their decoded JSON/NUL-record
  protocol. The workflow stages this file directly from the validated commit,
  into the trusted runtime directory, installs it into `/mnt/control`, and
  makes that mount read-only before use. The same captured-source authority
  bootstrap, also staged from the exact commit, guards each fixed runtime
  entry; Python no longer executes an unguarded standalone payload.
  No arbitrary Python code string or heredoc is an approved substitute.

Before staging candidate inputs, the producer runs the isolated authority CLI
immediately after enabling strict shell options. Its exact-tree check derives
the complete parser/registry/program/consumer import closure, package
initializers, file modes and blob identities from Git. Missing, dirty,
symlinked, hardlinked, executable-mode-changed, or redirected inputs reject.
It compares both the trusted source checkout and the selected checkout to that
tree **before importing local authority modules**. The isolated CLI then
compiles the same captured, verified bytes through a source-only loader for
the importable authority closure, including package initializers. The
canonical `Program` payload (`PROGRAM_PATH`), candidate launcher and embedded
workflow programs remain captured data for staging and AST inspection, not
importable modules. Even a committed package initializer importing
`publisher_programs` or `publisher_candidate` rejects before that program
executes. Both standalone files reject repository-local imports during closure
validation. At runtime, the existing source-only loader captures the selected
control-mount payload before execution and admits only that source plus trusted
stdlib code. A stdlib `SourceFileLoader`, direct or aliased, cannot execute
undeclared `/mnt/source` code before privilege drop. This closes the raw
standalone entry route without an import-name blacklist or a new broker.
It is not a general sandbox for hostile Python: the authority bootstrap,
stdlib and native/OS APIs remain trusted. Production installs single payloads
under `/mnt/control`, not a package or transitive module tree. The authority CLI is itself a registered
`Program`, but remains an authority entry point, not a data-only payload.
The source-only loader neither consults repository bytecode/extension caches
nor reopens repository source paths after verification;
previously imported repository modules cannot substitute for the captured
ones. `-I -S` alone does not exclude adjacent unchecked-hash Python caches,
and `-B` only disables cache writes, not reads. `bind_exact_tree` is a data
check; execution uses `validate_exact_tree` to keep verification and loading
on the same bytes. No committed content-hash ledger or source snapshot is
maintained. Git tracks source/review/history; raw Git text is not behavioral
evidence.

Each workflow step starts a fresh shell. `Inventory.validate_producer`
therefore checks the **entire** verification and candidate-staging steps,
including their helpers, final commands, control placements, and canonical
program payloads. `publisher_producer_signatures.py` contributes these rows
to the same typed inventory; it is not another parser. The existing Bash
parser also represents the producer's bounded conditionals, while loops and
quoted literal heredocs. The builder heredoc is authorized recursively by the
same builder inventory; the other payloads select their exact canonical
workflow programs rather than granting an arbitrary interpreter exemption.
Both prologues explicitly unset the established Git repository-path
variables; empty `GIT_DIR`/`GIT_WORK_TREE` values are not equivalent to absent
variables. The two staging signatures bind the Git executable, exact commit
and source paths, and output redirects to
`$PATCH_RUNTIME_ROOT/publisher-programs.py` and
`$PATCH_RUNTIME_ROOT/candidate-launcher.py`. The launcher transport and the
builder's registered installs read those same trusted runtime files before
masking the host tree; their `/mnt/control` destinations are unchanged. Changing
the source/ref/output or making sanitization/staging conditional fails both
semantic consumers independently of the outer raw-shell identity.
Both source captures must immediately follow sanitization, in either order.
An extra registered command exceeds its reviewed multiplicity; appending an
unknown command anywhere in either trusted step also rejects.
Import-time policy permits only the captured repository module set and
standard-library modules from trusted interpreter directories, built-ins or
frozen modules. Untrusted cached modules and module-valued exports from
retained modules are quarantined during validation and restored afterward, including
on failure. Dynamic `builtins.__import__`, `importlib.import_module`,
`importlib.__import__`, and previously bound import functions cannot fall
back to those caches, ambient packages, custom finders, or repository/site-package
standard-library shadows. A standard-library name may load its genuine system
module instead of a quarantined shadow. Orphaned parent bindings and aliases
are included even when the original module has no `sys.modules` entry.

An execution audit enforces the same source authority independently of import
syntax or loader method spelling. Direct, aliased, or previously bound loaders
and already compiled code cannot execute ambient Python or data-only programs.
Executed code must match code compiled from the captured authority source,
installed system standard-library source, or the interpreter's frozen module.
An allowed filename alone is insufficient: substituted source, unchecked-hash
caches and sourceless bytecode with forged filenames reject before executing.
Direct native imports also require a system standard-library name and origin.
Anonymous code generation, such as `dataclasses` and `collections.namedtuple`,
requires observed compilation and matching execution in the same verified
stdlib frame; a loader handling anonymous cached bytecode does not qualify.
The audit is active only during the source-only validation context.

This is a **module/code-origin boundary, not a malicious-Python sandbox**.
The launcher, reviewed exact-tree authority, interpreter and installed system
stdlib are trusted. Validation is single-threaded; it does not contain
deliberate in-process policy tampering, arbitrary already-running Python
callbacks, or unsafe actions requested through trusted native/process/code
generation APIs. Those are review and publisher-isolation responsibilities,
not guarantees supplied by import hooks or audit events. No second program
registry or filename blacklist authorizes code: the existing canonical program
source stays data, and executable code is derived from the captured import
closure and trusted interpreter sources.

Git blob sizes are queried and capped at 1 MiB **before** a bounded content read.

From a clean source checkout at the exact candidate:

```bash
python3 -I -S scripts/workflow_pilot/publisher_inventory.py \
  --repository-root . --commit "$(git rev-parse HEAD)"
```

### Production program and event API

Issue #201 consumes the #200 foundation and activates the fixed membership
producer. The legacy `grep`/`sort` membership statements and their inventory
rows are removed, not left as an optional fallback. An added reader, even one
built from fragments without the literal membership filename, is unregistered
and rejects.

The fixed checker is available only as:

```text
/usr/bin/python3 -I -S /mnt/control/publisher-inventory.py --runtime-program membership "$$"
```

It reads only `/mnt/supervisor/cgroup/cgroup.procs`, reads at most 1,025 bytes
and permits at most 1,024, and accepts exactly two distinct canonical positive
PID records: the wrapper argument and its own PID, in either order. It emits
no success output. It also requires the wrapper argument to be its actual
parent, in the same session and process group. Additional arguments, paths,
redirects, code, or a different executable are not authorized. The production
signature has exactly **one** occurrence; a missing or additional checker
fails. The separate canonical `publisher_candidate.py` retains the reviewed
no-fork exec and inherited-FD closure. Its installed
`/mnt/control/candidate-launcher.py` identity is unchanged; its source is now
captured as standalone data in the same Git-derived exact-tree program closure,
without executing it or the registry during discovery. Repository-local
launcher imports reject because production installs one standalone file. The
fixed bootstrap dispatches `candidate-launcher` through the same source-only
guard without adding a fork; the launcher's FD closure, setpriv transition,
clean environment, `-I -S` startup and foreground wait/reap remain unchanged.
No caller-supplied path or mode can select another payload.

`validate_builder_script(source)` returns immutable `Analysis` records.
`Analysis.commands` covers every authorized statement and nested producer;
`Analysis.events` expands helper calls and retains `scope`, `call_stack`,
`context`, the parsed command, typed resource accesses, and `EventKind`.
`Signature.placements` contains typed `Placement(context, occurrences)`
records; `authorize(command, scope, context)` requires a registered placement.
`Control.context` fixes its own nesting, and `Context.branch` identifies the
operand position for operator chains. `validate(source, entry_scope=...)`
selects the builder (`entry`), verification (`producer`), staging (`staging`),
or explicit candidate (`candidate`) domain;
`Inventory.entry_scope(scope)` resolves helper domains. Program metadata
includes exact wrapper and redirection forms for the producer's fixed programs.
`Command.conditional` and `Invocation.conditional` distinguish Bash conditional
keywords from literal command names without erasing quoting provenance.
`CANDIDATE_LAUNCH`, `CANDIDATE_STATUS`, `MEMBERSHIP_VERIFIED`, `EXPORT_OPEN`,
`EXPORT_FILE`, `EXPORT_CLOSE`, and `POST_CHECK` are the integration seam for
#201. A legacy observation event cannot advance the machine. These are
**syntactic operation events**, not
proof that a conditional command ran or succeeded. The phase consumer must
check control/operator/substitution context and completion before advancing
its state. Production `validate_builder_script`, `validate_workflow`, and the
exact-tree CLI all apply that phase consumer after inventory authorization.
The inventory alone remains a parser/authorization API, not a production
completion bypass. Authorization ignores harmless source spelling; the phase
policy permits independent initializer/file-install reorderings but rejects
changes to runtime prerequisites. Cgroup input/ownership, bind/read-only setup
and canonical aliases dominate their inode/options consumers. Each initial,
remaining-device and runtime snapshot must be created, produced and checked
before use; the remaining snapshot follows descendant removal. Helper call
events are consumed in their exact invocation frames with fresh local
bindings and before/read/after checks. Per-iteration values precede the
corresponding case checks. The pre-existing raw host-shell boundary checks
remain separate; their hashes are not the behavioral oracle for this case.
Every Python program's declared inputs and outputs must be represented in its
signature's accesses; consequently the candidate-launch event includes both
the launcher's control-file read and candidate execution.

#### Exact candidate invocation profiles

The candidate domain uses the **same** `Inventory`, `Signature`, `Scope`,
`Control`, `Placement`, and `Analysis` types. It is not a second permission
registry. `publisher_candidate_signatures.register` contributes independently
defined production rows to that same registry; no permission is learned from
`CANDIDATE_BUILD` or its canonical workflow copy. The shared candidate-root
validator checks complete forms, placements and counts and returns typed
`Analysis` before #201 applies its phase policy. Removing the phase policy
does not remove candidate command authorization.
The candidate profile API and candidate build payload are unchanged by the
bounded source-entry repair; only the trusted staging/runtime dispatch changes.

`Signature.executable` optionally binds an independently specified exact
`publisher_shell.Word` as well as the complete `Signature.form`. Inside
`publisher_signatures.inventory`, the existing `add` helper accepts the same
`executable=` keyword for explicit literal executables:

```python
add("candidate", "make",
    "make expansion-modern-map-menu-presentation-check -j1",
    Resource.CANDIDATE, Access.EXECUTE,
    executable=shell.command("make").argv[0])
add("candidate", "tools", "./build_tools.sh",
    Resource.CANDIDATE, Access.EXECUTE,
    executable=shell.command("./build_tools.sh").argv[0])
```

These illustrate independent registry edits. Production uses the corresponding
`candidate.make.run` and `candidate.build-tools.run` records. There is no
bare-name/path-prefix allowlist. A different
executable, target, argument, environment, wrapper, redirection, context, or
count still fails complete invocation equality.

A bounded view of those same records permits safe connected local binder
renaming and disjoint candidate case-arm ordering. It changes only local
identifier bindings and branch positions after matching the registered
preflight headers and exit-pattern set; executable paths, free parameters,
arguments, redirects and program text are never inferred from the submitted
workflow. Binder names must be distinct and cannot capture executable roles.
This is not another registry or parser.

Python always uses `Family.PYTHON` and a `Program`. Its appended fields are:

| Field | Contract |
| --- | --- |
| `interpreter` | Exact `Word`; defaults to literal `/usr/bin/python3`. |
| `startup` | Exact tuple of literal flags, default `("-I", "-S")`. Supported flags are `-I`, `-S`, `-E`, `-s`, `-B`, `-u`, `-P`, one per word, without duplicates. Empty `()` honestly represents normal non-isolated startup. Dispatch switches, compound flags, and operand-taking options are not startup flags. |
| `kind` | Typed `ProgramKind.FILE` (default), `INLINE`, or `MODULE`. |
| `text` | Independently registered full literal program for `INLINE`; `None` otherwise. |
| `environment` | Exact assignment-prefix Words, default empty; wrappers and redirects retain their existing exact fields. |

For `FILE`, `runtime_path` is one shell-encoded path Word and `mode` is an
optional first program argument, preserving the existing isolated file forms.
For `INLINE`, use `runtime_path="-c"`, `mode=None`, and the full `text`;
changing only the command's code without its independently reviewed program
record rejects. For `MODULE`, use `runtime_path="-m"` and an exact dotted
module name in `mode`. For example, the current pip invocation can be
represented without pretending it is isolated or a generic executable:

```python
pip = Program(
    "candidate-pip", WORKFLOW_PATH, "-m", "pip",
    (ResourceAccess(Resource.CANDIDATE, Access.READ),),
    (ResourceAccess(Resource.CANDIDATE, Access.WRITE),),
    interpreter=shell.command('"$HOME/venv/bin/python3"').argv[0],
    startup=(), kind=ProgramKind.MODULE,
)
add("candidate", "pip",
    '"$HOME/venv/bin/python3" -m pip install --no-index '
    '--find-links="$WHEELHOUSE" --require-hashes --only-binary=:all: '
    '--no-deps -r "$GITHUB_WORKSPACE/.github/requirements/build.txt"',
    Resource.CANDIDATE, Access.READ, program=pip, extra=pip.outputs)
```

The analogous venv profile uses the default interpreter, `startup=()`,
`kind=ProgramKind.MODULE`, `runtime_path="-m"`, `mode="venv"`, and the complete
`/usr/bin/python3 -m venv "$HOME/venv"` form. `Program.invocation_prefix()`
cross-checks dispatch metadata only; it never authorizes argument suffixes.
All argv Words, including module operands and inline program text, remain
bound by complete `Signature.invocation` equality.

`normalize_invocation(command, executable=word)` accepts a symbolic executable
only when it equals that exact registry-owned Word. Its symbolic grammar
permits quoted named parameters with fixed literal components, not bare
variable dispatch, unquoted expansion, substitutions, arithmetic, or patterns.
The inventory supplies this Word only from registered Python programs in the
current scope; generic executables cannot use that escape. `$HOME` and
`${HOME}` inside equivalent quoting normalize alike; changed variables,
suffixes, or quoting do not. Normalization alone is **not authorization**.
After validation, `Analysis.commands` provides each authorized command's
`signature.invocation`, including symbolic executable Words. Phase consumers
must use that exact registered result, not derive a permissive executable
profile from the submitted command or retry an inventory rejection by prefix.

`Program.source_path` identifies the captured launch/program source (the
workflow for embedded programs and external module launches), not installed
stdlib/venv/pip code. Program text and invocation records must be independently
declared in the reviewed registry, never reconstructed from submitted workflow
statements or their self-derived canonical payload. Changing that payload alone
cannot authorize a candidate operation. Deliberately updating the independent
registry can, subject to the same complete contract and tests.
Non-isolated `-m` launch profiles do not prove module resolution or installed
package integrity; runtime environment and phase containment remain downstream
responsibilities. These descriptors neither import those modules into the
validator nor alter the captured-data, cache, or direct-loader origin controls.

Dependencies are the existing workflow parser, Git exact-tree verification,
and Linux/Python standard-library tools. #201 depends on this API; #195 must be
rebuilt on both foundations rather than restoring its alternate analyzer.
There are no gameplay-profile, save, generated-data, localization, ROM/RAM,
modern debug/release, or archival conflicts or changes. No feature flag or new
runtime package is introduced.

The complete human procedure and automated positive, adversarial, mutation,
deletion, and drift evidence are indexed as
[TC-WORKFLOW-PUBLISHER-COMMAND-INVENTORY-001](test-cases/workflow-governance.md#tc-workflow-publisher-command-inventory-001-authorize-only-reviewed-publisher-commands).
Reverting this foundation blocks its dependents; it does not enable a
substring-based fallback.

## Mandatory publisher phases (issue #201)

`scripts/workflow_pilot/publisher_phase.py` is one bounded explicit state
machine shared by both semantic validators and the exact-tree CLI. It uses
only #200's authorized AST nodes, events, registry identities, scopes, control
frames, and expanded call stacks. It does not parse another command language
or maintain another command allowlist.

The accepted success path is:

| State | Required runtime operation and binding |
| --- | --- |
| Preparing | Unconditional strict `errexit`/`errtrace`, fixed ERR handler, cgroup join, inode-bound read-only supervisor view, complete isolation setup, and initial read-only export seal. |
| Launch started | The fixed exec-only launcher is the sole **foreground condition** of the top-level candidate-launch `if` in `builder_main`. The `if` itself cannot be skipped, backgrounded, or evaluated from another helper. |
| Launch completed and reaped | Bash synchronously waits for that exact foreground process. The success edge records zero; the failure edge immediately captures `$?`. The adjacent closed result guard exits on every nonzero result. An assignment/event is not itself a reap receipt. |
| Membership verified | The exact isolated checker runs once, unconditionally, after the result guard and before any handoff read or export. Its nonzero exit reaches the fixed output-validation failure handler. |
| Export started | Every exact handoff validation has succeeded; only then may the export bind mount become writable. Both allowlisted files must be installed before ownership changes. |
| Export committed | The completed two-file export has the exact host ownership and is successfully remounted read-only. The initial setup seal cannot be mistaken for this final close. |
| Post-check completed | The fixed `publisher-programs.py post-check "$$" "$host_uid" "$host_gid"` authenticates its real parent/session/group, verifies the kernel read-only mount flag, and checks the actual two-file regular/single-link/`0400` inventory, ownership, 32 MiB target, and bounded nonempty metadata. Only then can the builder exit zero. |

All mandatory operations are bound to their real control frames. The exact
launch condition and its two result edges are deliberately modeled rather
than treating every conditional event as executed. A checker or export
operation in any branch, helper, callback, trap, background, pipeline,
subshell, command/process substitution, or wrong entry frame is rejected.
Missing/duplicate/early/late operations, status-edge swaps, premature success,
and remapping the failure handler also reject. The ERR callback is the one
registered failure-only exception: it can exit with a fixed code, never
establish a successful phase.

Preparation also retains the reserved diagnostic substages: namespace setup
must stay in `namespace` (81), while the writable-mount audit and resource-limit
operations stay in `mount-audit` (82). Failure-only branches retain their own
substage. Independent initialization, limit and file-install operations can
reorder within their respective substages, but moving the mount-audit boundary
before namespace completion rejects. Otherwise, even an unchanged failing
hidden-directory check would be mislabeled 82 instead of 81.

The machine proves the required **success-path structure**, not that a build
has run. Actual completion evidence comes from executing the foreground
launcher, checker, exporter, and post-check. A live checker invoked before
launch can legitimately see only itself and the wrapper; that passing snapshot
does **not** prove a future candidate completed. The regression reproduces
this and shows why the phase authority is mandatory.

Failure output remains suppressed inside the namespace. Only the trusted
host translates child status to a fixed `stage=isolated detail=... exit=...`
diagnostic:

| Exit | Detail |
| --- | --- |
| 71–76 | `candidate-preflight`, `candidate-venv`, `candidate-pip`, `candidate-build-tools`, `candidate-make`, `candidate-handoff` respectively |
| 77 | `candidate-unknown` |
| 81–85 | `namespace`, `mount-audit`, `output-validate`, `export`, `post-check` respectively |
| Other | `transport`, normalized to exit 125 |

The trusted candidate script assigns 71–76 through its fixed failure handler.
A candidate result outside those codes or 125/126 becomes 77 before returning
to the host; in particular a candidate cannot impersonate an isolated 81–85
substage. Authenticated supervisor launch, immediate signal reauthentication,
owned-cgroup cleanup, output suppression, and pre-secret host post-checks are
retained. No candidate-controlled output, PID, path, or free text is added to
these diagnostics.

Canonical payload equality alone does not prove this diagnostic contract.
The child phase policy consumes the registered staging and candidate `Analysis`
and their authenticated command/event frames, after independent full
invocation authorization by the shared registry. It binds the ERR callback
to its actual state variable, requires the six literal stage transitions, and
compares each parsed exit arm with the registered host candidate-detail map.
Preflight checks, venv creation, pip/working-directory setup, build-tools,
make and handoff operations must execute in their assigned stages; moving a
stage write or mandatory action into another control frame rejects.
The wildcard fallback must remain last and executable, not a quoted literal.
Connected handler/state renaming, equivalent quoting, disjoint candidate
case-arm reordering and independent handoff install order remain valid.

The same staging policy requires the host failure continuation to select its
detail, emit the fixed diagnostic, then exit, in both the parsed control
sequence and the bound event stream. Printing before the case map would read
an unset variable under `set -u`; exiting early would silently lose the
diagnostic. Both reorderings reject even with refreshed outer identities.
This adds phase/dataflow checks, not another command permission list, parser,
entry scope or source-loader authority. The candidate payload stays data-only.

Candidate statement coverage is exact, not an executable/argument-prefix
classifier. Every prelude, callback, assignment, nested producer and build
operation matches its independently registered full form. The legitimate
read-only `find` socket probe is bound inside the full preflight test Word;
changed query arguments, redirects, environment, or surrounding predicates
reject. The complete FD-check text and venv/pip launch profiles are separately
registered `Program` data. Changing only the workflow's canonical payload
cannot change any of these permissions.

Even an additional literal notice needs an explicit registry record; it is a
new command, not a free refactor exemption. Tests preserve safe local renaming,
quoting and independent order separately, and prove that deliberately updating
both a full form and its `Program` metadata authorizes only that new behavior.
Canonical payload comparison cannot substitute for exact authorization.
Callback renaming must not
shadow an executed command; renaming the ERR helper to `make` would otherwise
replace the build invocation rather than preserve its semantics.

### Dependencies, compatibility, and evidence boundary

This is a genuine depth-one child of #200. While the parent PR is open the
child stays on its immediate implementation branch; parent updates are merged
normally, and delivery is bottom-up. #177 recovery depends on both contracts.
#195 overlaps the publisher/validators and must be reconciled on their merged
base; this change neither closes nor supersedes it. There are no other feature
or profile conflicts. There is no feature flag, artifact-format change, Build
topology/required-check change, new host package, or target ROM/RAM, save,
configuration identity, generated game data, localization, modern debug/release,
or archival impact.

[TC-WORKFLOW-PUBLISHER-PHASE-001](test-cases/workflow-governance.md#tc-workflow-publisher-phase-001-bind-verification-to-completed-candidate-execution)
provides the human procedure and focused automation. Its live rootless
user/PID/mount-namespace fixture executes real wait/reap, no-fork exec,
capability drop, the canonical checker, install/chown, read-only remount, and
post-check, including live and detached descendants. A same-PID exec adapter
mirrors the live private `/proc` process set into the read-only literal
cgroup-view path; it does not decide success or emit phase events. The
single-ID user namespace uses `--keep-groups` instead of unavailable
`setgroups`. This fixture is **not** evidence of privileged cgroup join/kill
or dedicated-UID isolation. Those unchanged boundaries, and the complete
publisher, require the real supported runner and exact-master Build; a local
fixture cannot release #177's recovery hold.

The live fixture first probes its exact user/PID/mount namespace, private proc,
read-only bind remount and capability-drop prerequisites without running
candidate code. A host denial skips only the namespace-dependent scenarios
with a bounded diagnostic; it is missing runtime evidence, not a passing
scenario. After a successful preflight, every runtime failure remains a test
failure. The fixed handler, semantic phase and source-authority checks still
run on restricted hosts.

No subjective/manual-only criterion applies. Revert this dedicated child on a
regression and retain the recovery hold; do not introduce a phase bypass or
claim the unchanged failing publisher is healthy.

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
receive an FD for it. After candidate exit, the wrapper reads the exact
read-only cgroup child there and exports the ROM only when its fixed checker
observes exactly the wrapper and itself, with no candidate descendant. The
checker is then synchronously reaped; the wrapper is the sole member. The host
continues to use the actual cgroup path for kill and removal. After the isolated builder is spawned, trusted wrapper failures emit
only fixed `launch`, `isolated`, or `cleanup` stage codes with numeric exits,
never candidate-controlled output. `launch` covers only the bounded,
kernel-derived stopped-session identity and exact resume operation; its detail
is one fixed enum value, never a PID or process text. `isolated` reports the
fixed substage detail and mapped child exit listed above, and `cleanup` reports teardown summary status whenever teardown
fails. Earlier trusted pre-spawn setup and later post-child handoff validation
still use normal shell failure output and are outside this diagnostic enum;
cleanup may therefore be the only stage text even when the failure began
before spawn.

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
