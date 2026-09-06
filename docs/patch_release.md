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

## Build once and package in CI

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
The download uses curl's 16 MiB maximum-file-size bound, and an independent
file-size check rejects an oversized result before Python reads its contents.
The existing producer still validates the exact approved base size and identity.
Cleanup command failures report a fixed diagnostic and fail the packaging step.
Only verified BPS/manifest/README files are uploaded after private cleanup;
the ROM stays inside the build job and is never an artifact/cache handoff.
Packaging or cleanup failure fails `build` and therefore the required summary.
No custom UID, namespace, cgroup, supervisor, broker or capability platform is
part of this contract. The retired isolation proposals are superseded, not
claimed to have passed their tests.

The sole canonical invocation is
`make expansion-modern-map-menu-presentation-check -j1` in the normal `build`
job. Packaging consumes these existing outputs:

```text
build/expansion-modern-all-locales-all-features/release/aapcs/fireemblem8.gba
build/expansion-modern-all-locales-all-features/release/aapcs/generated/expansion_build_metadata.json
```

The master-only step passes the existing `BASEROM_URL`, validated
`PATCH_COMMIT`, and `PATCH_ARTIFACT_DIR` to
`bash scripts/modernize/package_ci_patch.sh`. It runs the existing
`python3 -m scripts.modernize.patch_release create` and `verify` commands;
it does not rebuild, transfer a ROM between jobs, or create a GitHub Release.
The step and upload require the canonical `laqieer/fireemblem8-expansion`
repository as well as a successful authenticated master push, so fork pushes
cannot enter the publication path even if a fork defines a similarly named secret.
Other intentional debug/configuration/host/archival checks remain mandatory.

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
