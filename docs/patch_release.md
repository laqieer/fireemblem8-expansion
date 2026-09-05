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
  dynamic executable paths, unmatched arguments or redirects, callbacks,
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
  makes that mount read-only before use.
  No arbitrary Python code string or heredoc is an approved substitute.

Before staging candidate inputs, the producer runs the isolated authority CLI
immediately after enabling strict shell options. Its exact-tree check derives
the complete parser/registry/program/consumer import closure, package
initializers, file modes and blob identities from Git. Missing, dirty,
symlinked, hardlinked, executable-mode-changed, or redirected inputs reject.
It compares both the trusted source checkout and the selected checkout to that
tree **before importing local authority modules**. The isolated CLI then
compiles the same captured, verified bytes through a source-only loader for
the complete closure, including package initializers. It neither consults
bytecode/extension caches nor reopens source paths after verification;
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
variables. The staging signature binds the Git executable, exact commit and
source path, and output redirect to
`$PATCH_RUNTIME_ROOT/publisher-programs.py`. The builder's registered install
reads that same trusted runtime file before masking the host tree. Changing
the source/ref/output or making sanitization/staging conditional fails both
semantic consumers independently of the outer raw-shell identity.
Appending an unknown command anywhere in either trusted step also rejects.
Import-time policy permits only the captured repository module set and
standard-library modules from trusted interpreter directories, built-ins or
frozen modules. It checks cached imports too; dynamic `builtins.__import__`
and `importlib.import_module` cannot fall back to ambient packages, custom
finders, or repository/site-package standard-library shadows. Git blob sizes
are queried and capped at 1 MiB **before** a bounded content read.

From a clean source checkout at the exact candidate:

```bash
python3 -I -S scripts/workflow_pilot/publisher_inventory.py \
  --repository-root . --commit "$(git rev-parse HEAD)"
```

### Foundation boundary and downstream API

Issue #200 deliberately does **not** implement the #177/#195 membership
fix-forward or the #201 phase machine. The current production `grep`/`sort`
membership observations have explicit, exact **legacy observation** signatures;
their presence is not evidence that the known self-observation failure is
fixed. An added reader, even one built from fragments without the literal
membership filename, is unregistered and rejects.

The fixed checker is available only as:

```text
/usr/bin/python3 -I -S /mnt/control/publisher-programs.py membership "$$"
```

It reads only `/mnt/supervisor/cgroup/cgroup.procs`, reads at most 1,025 bytes
and permits at most 1,024, and accepts exactly two distinct canonical positive
PID records: the wrapper argument and its own PID, in either order. It emits
no success output. Additional arguments, paths, redirects, code, or a different
executable are not authorized. The signature has zero production occurrences
in this foundation; adding even the exact checker to the current builder
requires the downstream inventory update rather than a permissive optional
reader.

`validate_builder_script(source)` returns immutable `Analysis` records.
`Analysis.commands` covers every authorized statement and nested producer;
`Analysis.events` expands helper calls and retains `scope`, `call_stack`,
`context`, the parsed command, typed resource accesses, and `EventKind`.
`Signature.placements` contains typed `Placement(context, occurrences)`
records; `authorize(command, scope, context)` requires a registered placement.
`Control.context` fixes its own nesting, and `Context.branch` identifies the
operand position for operator chains. `validate(source, entry_scope=...)`
selects the builder (`entry`), verification (`producer`), or staging domain;
`Inventory.entry_scope(scope)` resolves helper domains. Program metadata
includes exact wrapper and redirection forms for the producer's fixed programs.
`CANDIDATE_LAUNCH`, `CANDIDATE_STATUS`, `LEGACY_MEMBERSHIP`,
`MEMBERSHIP_VERIFIED`, `EXPORT_OPEN`, `EXPORT_FILE`, and `EXPORT_CLOSE` are
the integration seam for #201. These are **syntactic operation events**, not
proof that a conditional command ran or succeeded. The phase consumer must
check control/operator/substitution context and completion before advancing
its state. Inventory authorization ignores harmless source spelling and
independent command order. The pre-existing raw host-shell boundary checks
remain separate; their hashes are not the behavioral oracle for this case.
Every Python program's declared inputs and outputs must be represented in its
signature's accesses; consequently the candidate-launch event includes both
the launcher's control-file read and candidate execution.

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
read-only cgroup child there and exports the ROM only when the wrapper PID is
the sole member; the host continues to use the actual cgroup path for kill and
removal. After the isolated builder is spawned, trusted wrapper failures emit
only fixed `launch`, `isolated`, or `cleanup` stage codes with numeric exits,
never candidate-controlled output. `launch` covers only the bounded,
kernel-derived stopped-session identity and exact resume operation; its detail
is one fixed enum value, never a PID or process text. `isolated` reports the
child exit, and `cleanup` reports teardown summary status whenever teardown
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
