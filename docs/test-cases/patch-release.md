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

Validation succeeds without writing a ROM. The explicit BPS applier writes
only the chosen output and verifies checksums. The reconstructed 32 MiB
release/AAPCS image and embedded metadata match the named profile and commit.
The base remains unchanged. CI's build-once packaging procedure is defined by
`TC-CI-PATCH-049-002`; no hostile-build isolation is promised.

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

## TC-CI-PATCH-049-002: Reject incorrect patch inputs and package one trusted build

- **Feature:** patch-release-artifact; issues #49 and #177.
- **Profile:** modern-release-all-locales-all-features-aapcs, from a clean
  exact source checkout or the matching master Actions patch artifact.
- **Prerequisites:** the declared build dependencies; synthetic owned inputs
  for host automation. Real publication additionally requires the configured
  private `BASEROM_URL`. Never provide a private base on a PR/fork.
- **Starting state:** a fresh checkout, no artifact directory, and the normal
  build's release ROM plus generated metadata. Reset only outputs you own.

### Actions

1. Run `python3 -m unittest tests.workflows.test_patch_release_workflow -v`.
   Its owned synthetic fixture executes the actual packaging script and
   producer create/verify/round-trip path. It substitutes only the approved
   base identity and download transport, not target or metadata validation.
2. Confirm the parsed workflow invokes the canonical profile once, packaging
   runs no Make target, and packaging/upload select only a successful,
   authenticated master push. All four validation workers remain mandatory.
3. Exercise wrong base, target, metadata and commit, failed partial download,
   unexpected artifact files and failed verification. Each must fail without
   private URL/base data in public output, and remove its private input.
   Supply a download larger than 16 MiB and require rejection before producer
   invocation. Confirm the transport receives the maximum-file-size bound.
   Separately force private-file removal and directory-removal failures;
   require the fixed cleanup diagnostic, a nonzero result and no private
   URL/bytes in output. The owned test fixture removes any retained test input.
4. On the resulting exact master Build, confirm packaging reuses the existing
   release/aapcs output, succeeds, cleans private input and uploads exactly
   BPS, `manifest.json` and `README.txt` under the existing SHA-named artifact
   with 30-day retention. A failed packaging/cleanup step must fail `build`
   and the combined summary. Do not download or upload a full ROM for evidence.
5. For local patch validation/application, follow `TC-CI-PATCH-049-001`.

### Expected result

Patch packaging trusts reviewed, merged master source and pinned/declared
tools, like ordinary CI. It is not a sandbox against malicious repository
writers, hostile same-UID code, compromised dependencies, or runner compromise.
The normal modern job builds/checks the named release profile once from its
fresh exact checkout. Only an authenticated master push packages that existing
ROM and metadata in the same job; PRs and forks neither receive the private
base nor upload a patch. The packaging script invokes no Make target.
The existing producer checks the approved base hash/header, target header and
embedded metadata, exact commit/profile, BPS round trip and three-file artifact.
Private input uses a unique mode-0700 directory, mode-0400 base and failure/
signal cleanup. Download diagnostics never disclose the URL or private bytes.
Only verified BPS/manifest/README files are uploaded after private cleanup;
the ROM stays inside the build job and is never an artifact/cache handoff.
Packaging or cleanup failure fails `build` and therefore the required summary.
No custom UID, namespace, cgroup, supervisor, broker or capability platform is
part of this contract. The retired isolation proposals are superseded, not
claimed to have passed their tests.

### Negative control

The pre-fix workflow at `c04724697757892a15fb53e59537e8f51e3de728`
duplicates the canonical build in a separate publisher and fails the
build-once topology requirement. Synthetic wrong-input/download/verification
controls fail; the valid fixture reconstructs the exact owned target from
its patch and base. Historical skipped publisher jobs remain admissible
only as an extra canonical skipped record; removing any real validation job
still invalidates full-run evidence.

### Interactions and save compatibility

Dependencies: exact event identity, normal modern build/checks, existing
metadata/profile and BPS producer contracts. No dependency on the retired
#195/#200/#201 architecture. No save, locale, gameplay, generated-data,
configuration fingerprint, ROM/RAM, or archival behavior change; other
intentional profile checks remain. No new feature flag or runtime service.

### Automation

- `python3 -m unittest scripts.modernize.tests.test_patch_release -v` —
  base/header/hash, BPS, artifact and metadata validation.
- `python3 -m unittest tests.workflows.test_patch_release_workflow -v` —
  actual packaging with owned synthetic inputs, cleanup and master/PR policy.
- `python3 -m unittest tests.workflows.test_build_ci_topology -v` —
  retained workers, fail-closed summary and historical job compatibility.
- `python3 -m unittest tests.upstream_port.test_verify -v` —
  the complete 30 local gates (including both ownership checks) and parsed packaging-step contract.

### Cleanup and limitations

Host fixtures remove only their owned directories. The approved trust model
does not cover malicious reviewed source or runner compromise. Host fixtures
do not prove real publication: exact-master Build and remote completion
remain required. No subjective/manual-only criterion applies.
