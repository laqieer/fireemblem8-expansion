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
enables mechanics hooks/sample, Threat Range, starter content, the AoE
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
local input from a protected secret, but no URL, base bytes, base image, ROM,
ELF, map, save, or savestate is published, cached, or logged.

## Artifact contents and verification

Only a successful trusted `push` to `master` can upload
`modern-release-all-locales-all-features-aapcs-bps-<commit>` with 30-day
retention. Pull requests and Full Matrix receive no base-input secret and
publish nothing. The artifact contains exactly:

```text
fireemblem8-expansion-all-locales-all-features-aapcs.bps
manifest.json
README.txt
```

The stdlib-only audited producer/applier is
`scripts/modernize/bps_patch.py` (`stdlib-bps-target-read-v1`). It emits
deterministic BPS TargetRead actions and validates all BPS source, target, and
patch CRCs. The manifest is canonical JSON and binds the commit, full profile,
configuration fingerprint, base/output/patch sizes and hashes, producer
identity, expected base header, and embedded output metadata.

After downloading the artifact and supplying a legal base locally, verify it
without uploading either input or result:

```bash
python3 -m scripts.modernize.patch_release verify \
  --base /path/to/legal-fe8u-rev0 \
  --artifact-dir /path/to/unpacked-artifact
```

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

`TC-CI-PATCH-049-001` validates a trusted SHA-named artifact with the approved
base: verification must reconstruct the manifest digest, prove the embedded
32 MiB profile metadata, and retain the source-build boot/runtime checks.
Its negative control is the unchanged English-only, default-off, 16 MiB bare
`make` path.

`TC-CI-PATCH-049-002` supplies a missing, malformed, wrong-size, wrong-header,
wrong-hash, or one-byte-modified base. It must fail before artifact creation
or upload and disclose no base content or protected source. The synthetic host
tests cover these input and artifact-allowlist cases; workflow contract tests
prove the trusted-event and no-PR-secret boundaries.

Dependencies are the existing modern configuration/metadata, generated-data,
localization, linker budget, boot/runtime, and artifact-guard seams. The
profile conflicts with output-root contamination, profile-incompatible SRAM,
untrusted secret access, and non-`preserve` BGM policies; it has no new public
C API, save migration, localization-ID migration, or archival-lane behavior.
