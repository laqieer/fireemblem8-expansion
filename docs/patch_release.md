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
receive an FD for it. After candidate exit, the wrapper reads the exact
read-only cgroup child there and exports the ROM only when the wrapper PID is
the sole member; the host continues to use the actual cgroup path for kill and
removal. The raw cgroup path is used only for the exact initial
`printf` membership-join write. Bind identity is checked through the raw and
supervisor directory inodes; no raw `cgroup.procs` read remains. The parsed
contract rejects direct, redirected, wrapped, spaced, or aliased raw membership
reads even when the safe `mapfile` line remains present. After the isolated
builder is spawned, trusted wrapper failures emit
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
arbitrary nonzero candidate status becomes `77`, and success continues as
zero. `cleanup` reports teardown summary status whenever teardown fails.
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

The post-candidate cgroup membership check uses Bash `mapfile`, not a child
utility. Exact failing master
`5779c38e245d9a14f063338b53851a97bb92d0c0` invoked `sort` from inside the
builder cgroup to read `cgroup.procs`; the kernel therefore included that
reader in the snapshot, so the expected wrapper-only assertion necessarily
failed. The builtin reads the same authenticated read-only cgroup view without
creating another member. Its executable fixture accepts exactly one canonical
wrapper PID; empty, malformed, signed, whitespace-padded, zero, duplicate, and
additional-member snapshots fail before success or export, regardless of
member ordering, without signaling an unrelated live process.

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
