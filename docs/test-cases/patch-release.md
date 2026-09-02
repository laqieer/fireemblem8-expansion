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
Only `/dev/shm`, `/mnt/handoff`, `/mnt/home`, `/mnt/source`, `/mnt/tmp`, and
`/tmp` may carry an exact `rw` option token; spaces and backslashes in decoded
target paths are handled losslessly, while control-character targets,
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
unexpected isolated handoff output; stops the exact process group and cgroup;
proves `cgroup.procs` and the builder UID are empty; removes only owned state;
and uses no broad UID signal. It stages its producer from the same exact after
commit without source hash pins, uses an unpredictable mode-restricted base
path, invokes only that staged tool through absolute isolated Python while the
base exists, removes it on success/failure, verifies cleanup, revalidates the
BPS/manifest/README allowlist immediately before upload, and uploads only that
patch artifact.
Before candidate code, a trusted child launcher closes inherited descriptors
above 2, while stdin becomes private `/dev/null` and stdout/stderr permanently
target that same null device.
The candidate receives no GitHub workflow command-file paths and cannot recover
the Actions log through proc FDs, `/dev/stdout`, console/kmsg, `tee`, xtrace,
helpers, or forks. ROM-sized output is discarded and never replayed; arbitrary
output volume cannot fail an otherwise successful build. No output sink exists.
Fixed trusted text and a numeric exit classification preserve post-spawn build
failure without exposing candidate bytes. After the isolated builder is
spawned, `launch` covers only process-group and launch validation, `isolated`
reports the child exit, and `cleanup` reports teardown summary status whenever
teardown fails. Earlier trusted pre-spawn setup and later post-child handoff
validation still use normal shell failure output and are outside this stage
enum; cleanup may therefore be the only stage text even when the failure began
before spawn.
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
`/mnt/home`, `/mnt/source`, `/mnt/tmp`, and `/tmp` may expose an exact `rw`
option token. Decoded targets with spaces or backslashes remain lossless;
control-character targets, malformed or ambiguous option-token grammar,
duplicate or extra JSON rows, raw escaped or whitespace-delimited mount-target
transport, parser failure, unchecked process substitution, and any unexpected
writable effective mount fail closed. Hidden lower layers remain irrelevant
unless the wrapper or candidate can expose them, and this publisher never
grants that capability.

### Negative control

A valid synthetic three-file artifact with the matching synthetic base
round-trips successfully; it proves the rejection tests are not
success-shaped. The default bare `make` path remains 16 MiB/default-off and
does not receive a base secret, patch artifact, or publish step.

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
  unmount order, structured `env -S` shell-c evasions through inline
  `else`/brace/case/loop forms and inline-function fail-closed behavior,
  socket/daemon/cgroup-escape adversaries, two-file handoff rejection
  controls, unpredictable private path, cleanup-before-upload, late artifact
  revalidation, null/no-replay candidate output adversaries, the old
  Bash-FD-255/memfd exit-125 reproducer, inherited pipe/memfd/socket closure
  in the child launcher, and profile/verifier requirements.

### Cleanup and limitations

Remove only disposable local copies and outputs created for the negative
control. The legal-acquisition decision remains manual and private because no
ROM can be published; every deterministic validation is automated, and the
case does not test an unpublished base through CI.
