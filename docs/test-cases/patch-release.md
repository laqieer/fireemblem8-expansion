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
`isolated` reports the child exit, and `cleanup` reports teardown summary
status whenever teardown fails. Earlier trusted pre-spawn setup and later
post-child handoff validation still use normal shell failure output and are
outside this stage enum; cleanup may therefore be the only stage text even
when the failure began before spawn.
The wrapper binds the exact owned cgroup read-only under root-only mode-`0700`
`/mnt/supervisor` before masking `/sys`. The candidate cannot read, write,
execute, or traverse that parent, while the exact cgroup child remains
read-only; the post-build check remains readable and rejects any member beyond
the wrapper PID before ROM handoff. Decoded recursive `/dev` mount targets are
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
default bare `make` path remains 16 MiB/default-off and does not receive a base
secret, patch artifact, or publish step.

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
  residual-state-only cleanup diagnostics, recursive
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

## TC-WORKFLOW-PUBLISHER-COMMAND-INVENTORY-001: Enforce the closed publisher command inventory

- **Feature / originating issue:** `patch-release-artifact` /
  [issue #200](https://github.com/laqieer/fireemblem8-expansion/issues/200).
- **Supported configuration or artifact:** supported Linux host workflow
  profile at the exact candidate commit; no ROM, base image, save, emulator,
  secret, or network access is required.
- **Prerequisites and clean starting state:** start at the repository root with
  Python 3 and the committed workflow-pilot parser, typed signature validator,
  signature JSON, and patch-release workflow unchanged. Remove no production
  files; mutation fixtures use temporary copies.

### Actions

1. From a trusted parent, run the direct-exec
   [registry-check command](../publisher_authority_bootstrap.md#registry-check).

2. Run the table-driven positive, adversarial, helper, drift, deletion, and
   mirrored-validator suite through the authenticated snapshot bootstrap:

   From a trusted parent, use the direct-exec
   [workflow-consumer command](../publisher_authority_bootstrap.md#authenticated-workflow-consumers).

3. Run the upstream workflow mirror through the same bootstrap:

   From a trusted parent, use the direct-exec
   [upstream-port command](../publisher_authority_bootstrap.md#authenticated-upstream-port-consumers).

### Expected result

The complete production builder resolves every outer command, recursive helper
command, isolated-wrapper command, candidate-build command, and reviewed Python
invocation to the same counted set of typed signatures. The one membership
checker passes only as absolute `/usr/bin/python3` with exact `-I -S - "$$"`
argv, exact heredoc program digest and stdin, no output or write, one read-only
`/mnt/supervisor/cgroup/cgroup.procs` access, and its typed semantic event.
Both publisher validator suites return the same decision from the same
authority.

Composed Python, shell, awk, Perl, helper, split-string, encoded/escaped,
indirect/dynamic executable, alternate interpreter flag, stdin/file/`-c`
program, callback, trap, process-substitution, redirection, raw path fragment,
arbitrary absolute tool, shadowed builtin, and helper outer-state/array/alias
fixtures fail. Registry deletion, duplication, ambiguity, stale entries,
unreviewed byte reordering, signature mutation, authority-path substitution,
digest drift, and malformed JSON fail. Adding, removing, changing, or
redirecting a production command fails until the base-owned registry and
behavioral evidence are reviewed together.

Every canonical workflow, loader, parser, registry, consumer, package
initializer, and parent path is also checked against the selected immutable Git
tree. Symlinked files or parents, path escapes, nonregular files, hardlinks,
owner/mode drift, altered same-path content, validate-then-swap replacement,
misnamed commit/tree/blob objects, and import-cache, standard-library shadow,
or `sys.path` poisoning fail. The parser executes from the captured Git blob
and the registry is parsed from its captured blob.
CI and local commands use the same absolute `/usr/bin/python3 -I -S -c`
protocol and compressed, byte-checked bootstrap payload. Ordinary `python -m`
publisher-authority or upstream-verifier entrypoints are not accepted evidence.

The semantic inventory treats a pure cross-command reorder as the same set and
emits events without imposing their phase order. The pre-existing exact-run
tree seal still reports unreviewed workflow-byte drift; issue #201, not this
case, owns the candidate-launch/checker/export ordering policy.

### Negative control

The pre-fix substring detector reports no forbidden supervisor-parent mount for
`/usr/bin/python3 -c
'open("/mnt/supervisor/cgroup/"+"cgroup."+"procs","rb").read()'`; the typed
authority rejects that executable/program/argv/access because it has no exact
signature. A valid unmodified production builder and exact checker pass, so the
negative matrix is not success-shaped.

### Interactions and save compatibility

This depends on the existing workflow-pilot shell lexer/parser/control and
helper-normalization seams and exact-tree publisher identity checks. It is an
independent root on `master`, a prerequisite for issue #201 and the final issue
#177 publisher fix, and conflicts with PR #195 until that work consumes this
registry. There are no other feature conflicts. Modern debug/release and
archival behavior, Build triggers/jobs/contexts/artifacts, patch output format,
public C APIs, configuration identity, generated data, localization, ROM/RAM,
save fields/layout/migration/compatibility epoch, gameplay, and emulator state
are unchanged.

### Automation

- The
  [registry-check command](../publisher_authority_bootstrap.md#registry-check)
  parses the real workflow, validates the exact fixed Python programs, and
  checks complete counted equality with the closed JSON registry.
- The
  [workflow-consumer command](../publisher_authority_bootstrap.md#authenticated-workflow-consumers)
  loads the workflow-pilot, modernize, and workflow-test Python closure from
  authenticated Git bytes and proves the real positive, parent composed-reader
  negative, adversarial
  signature families, recursive helper closure, both mirrored validator
  decisions, exact checker mutations, production-command drift, and registry
  path/digest/schema/deletion/mutation controls; parameterized authority-file,
  parent, symlink, nonregular, mode, hardlink, content, path-swap, and import
  substitution cases; and proof that the semantic inventory does not enforce
  future phase-event order.
- The
  [upstream-port command](../publisher_authority_bootstrap.md#authenticated-upstream-port-consumers)
  loads the upstream-port implementation and test closure from the same
  authenticated snapshot, preserves its stdlib-only contract, and proves its
  workflow parser denies the same command mutations as the primary publisher
  validator.

### Cleanup and limitations

Temporary registry and command mutations are removed by the tests. This is a
host-only command/access identity contract, so no ARM runtime scenario is
applicable. Issue #201 owns candidate-launch/checker/export phase ordering; this
case intentionally emits but does not order those future semantic events.
