# Framework support matrix

This is the authoritative reference for **which hosts, toolchains, build
targets, and outputs are actually supported** by this repository, and where
to go for setup steps and troubleshooting. It intentionally does not
duplicate command-by-command instructions that already live in
[`docs/quickstart.md`](quickstart.md) and [`docs/config_identity.md`](config_identity.md) —
it links to them.

## Supported hosts

| Host | Package manager | Auto-installed by `scripts/quickstart.sh` | CI-verified |
| --- | --- | --- | --- |
| Ubuntu / Debian / WSL | `apt` | Yes | Yes — automatic `.github/workflows/build.yml` on `ubuntu-latest`, with parallel broader host and archival lanes on both PR and master runs |
| Arch Linux | `pacman` | Yes | No (community-supported; same script path as Ubuntu) |
| macOS | Homebrew (`brew`) | Yes | No (community-supported) |

Source: `scripts/quickstart.sh` detects `apt-get`, `pacman`, or `brew` (in
that order) and stops with an actionable message on any other package
manager — see the "Unsupported distro" entry in
[`docs/quickstart.md`](quickstart.md#troubleshooting). There is no native
Windows package-manager path; Windows users go through WSL (which is the
Ubuntu/`apt` path above). Do not read this as a native-Windows guarantee —
none of `scripts/quickstart.sh`, the Makefile, or CI target Windows
directly.

**Automatic Build CI is the only host this repository re-verifies on every
source-changing push/PR.** A PR candidate uses the complete combined Build
gate and Copilot review concurrently. Parsed body/title-only edits retain the
identity validator/router plus the running `metadata-classifier` context and
the canonical worker checks `host-tests`, `build`, `extended-host-tests`,
`legacy`, `patch-release`, and `summary`. In metadata mode, `host-tests` and
`build` run only a trusted no-checkout continuity attestation so the existing
required live contexts stay green; `extended-host-tests` and `legacy` remain
platform-skipped with no runner. That attestation reads the runner-owned
file-backed `GITHUB_EVENT_PATH` payload directly instead of env-copying the
PR body/title/changes JSON. Metadata `summary` is also a continuity-only
attestation: it succeeds only after a trusted no-checkout Actions API proof
classifies exact prior runs newest-first, skips only conclusively metadata
runs, and confirms the newest conclusively full Build CI run for the same
repository, PR number, authoritative base SHA, and immutable head SHA
completed successfully. That proof first requires complete paginated results
with stable `total_count`, single-page `Link` omission, exact non-final
`next`/`last` relations, no final `next`, exact per-page cardinality, stable
`workflow_id`, ordered positive `run_number`/`run_attempt` values, one exact
current-run observation, and rejects redirects before any second
authenticated request. A newer failed,
cancelled, in-progress, or malformed full run blocks older successes. Live
branch protection therefore remains the current canonical `host-tests` +
`build` + `summary` Build contract while preserving any existing independent
security/review contexts, and metadata-only runs still remain ineligible
candidate evidence by themselves.
Base, mixed, unknown/incomplete,
opened, synchronize, and reopened events with complete identity fail closed to
the complete graph. Any missing, malformed, or incoherent base ref/SHA with a
valid exact PR head also runs the four workers at that head and fails normal
summary; a valid base SHA may be retained only for diagnostics. Missing,
malformed, stale, or spoofed head identity runs none. A classifier failure with
a validated authoritative PR head runs all four workers at that exact head
under canonical worker names, then summary still fails. A master-push
classifier failure does the same at validated
`github.sha` and audits the master-only publisher before failing; without a
validated event-specific fallback SHA, it starts no worker or publisher. The
classifier bootstrap may use the trusted
default branch when PR base identity is missing or unusable; worker checkouts
never use a merge/default fallback.
Default-branch validation is deferred until that bootstrap is needed. A
missing or malformed default branch never invalidates an independently valid
PR-head or push fallback. If no classifier authority remains, the router fails
without checkout, the classifier fails, exact fallback workers (and the push
publisher) still run, and summary remains failed.
Base refs are bounded to 1024 UTF-8 bytes and must satisfy full
`git check-ref-format refs/heads/<base.ref>` semantics; `--branch` shorthand
is not used, and lone `@` is rejected. Python applies the equivalent grammar
without a subprocess, while trusted bootstrap quotes the full ref to system
Git and never checks it out. Invalid base refs are incomplete identity: a
valid exact head runs all four workers and fails summary; an invalid head runs
none.
Trusted event setup accepts identity only as an exact lowercase 40-hex SHA. A
PR also requires its numeric event number and exact
`refs/pull/<number>/merge` ref; a push requires `refs/heads/master` and equal
event `after`/`github.sha`. Every successful full/metadata classification,
worker, and summary binds to that kind and SHA. Missing, uppercase, short,
nonhex, ref-name, ref-number-mismatched, malformed, or cross-event identities
run no worker and cannot produce a successful summary. Candidate normalization
requires successful common identity and router setup in both modes and rejects
missing, failed, skipped, renamed, duplicate, or unknown setup contexts.
A canonical successful `event-identity` context is mandatory in both modes.
A canonical successful `event-router` context is mandatory in both modes.
Metadata-only mode is PR-only; push-shaped metadata output
fails into the validated full fallback. Workers consume only that validated
SHA. The publisher uses the same validated push SHA,
verifies `/usr/bin/git rev-parse HEAD` immediately after checkout, and exposes
`BASEROM_URL` only after the exact validated after tree has been built in a
fresh hosted publisher as a dedicated unprivileged UID inside mount, PID, and
network namespaces with no network, capabilities, secrets, `BASH_ENV`, or
`GITHUB_ENV`. Private mount propagation and recursively read-only host root,
`/usr/share`, and `/opt` leave only private candidate source, home,
temporary, and handoff mounts writable. Private `/tmp`, `/run`, `/proc`,
`/dev`, and `/dev/shm` mounts hide host D-Bus, Docker, containerd, systemd,
snap, and other service/runtime sockets. Every builder descendant remains in
one exact cgroup v2. The trusted host stops that cgroup and the exact process
group, verifies `cgroup.procs` is empty, proves no builder-UID process remains,
and removes only the
owned cgroup, then admits exactly one regular, nonsymlink, single-link 32 MiB
target ROM plus bounded nonexecuting metadata. Devices, escaped paths, and
unexpected handoff outputs fail. It removes the builder user, tree,
wheelhouse, and candidate checkout before private download. No complete target
ROM enters an Actions artifact, cache, release, or log. The three-file patch
producer is staged from that exact validated after commit with no whole-file
source hash pins.
Before `/sys` is masked, the exact owned cgroup is bound read-only below a
root-only `0700` `/mnt/supervisor`; the candidate cannot read, write, execute,
or traverse that parent. The exact cgroup child there remains read-only. The
fixed checker reads that supervisor view after `/sys` is masked and accepts
exactly the wrapper plus its own transient PID, with no candidate descendant.
The wrapper is alone after the synchronous checker is reaped and before
handoff/export. Host-side kill/removal still uses the actual cgroup path.
Unavailable mount/cgroup features fail closed, and cleanup sends no UID-wide
signal.
Before candidate code starts, a trusted child launcher closes inherited file descriptors
above 2, redirects stdin/stdout/stderr permanently to private `/dev/null`, and
passes no GitHub workflow command-file paths.
Candidate output is never replayed, logged, or uploaded; the trusted host emits
only fixed status text with a numeric exit classification. Arbitrary output
volume cannot change an otherwise successful build. All other writable roots
and regular files retain tmpfs/ulimit bounds; no output sink exists.
The comprehensive `build` job alone has a 90-minute ceiling for observed
shared-runner compile variance. Host, extended-host, legacy, and patch
publication remain 60 minutes; identity/router/classifier and summary remain
5. No Build content or required gate changes. A coordinator running
`timeout 90m gh run watch <run-id> --interval 30 --exit-status` may reach its
own limit near the job ceiling; it queries that exact run once and re-arms one
watcher once only if the run is still nonterminal.
The base is then downloaded to an unpredictable mode-restricted path and only
absolute isolated Python from an empty runtime CWD/environment may consume the
staged producer, target, and base. The base is deleted on success/failure,
cleanup is verified, and only then is the patch artifact uploaded.
The exact BPS/manifest/README regular-file allowlist is revalidated after
private cleanup and immediately before upload.
All repository/candidate-controlled commands finish before private download.
No candidate command runs while the base exists. Cleanup is verified before
upload.
Before the base exists, that publisher proves that no
candidate-written `GITHUB_ENV`, `BASH_ENV`, background process, checkout, or
executable state can survive the builder teardown.
The same Build jobs rerun on `master`; only the
technically used patch publisher is master-only. Arch and macOS support is
exercised by the same script logic but is not re-run in CI; treat regressions
there as community-reported, not CI-caught.

## Supported toolchains

| Toolchain | Status | Used for |
| --- | --- | --- |
| `arm-none-eabi` GCC (modern, AAPCS) | **Supported release lane** | The default `make`/`make all` target, every `expansion-modern-*` target, and CI's linker/boot gates |
| `arm-none-eabi-gdb` or `gdb-multiarch` | **Supported developer debugger** | Register, stack, symbol, memory, and control-flow diagnosis; installed and ARM-probed by quickstart, but not required by unattended Build CI |
| mGBA SDL GDB server | **Supported debug target** | Runs the cross-compiled debug ROM under `mgba --gdb`; `make expansion-modern-gdb-smoke` proves remote attach, registers, symbolic breakpoint, continue, and backtrace |
| agbcc (original GBA-era GCC 2.95 fork) | **Archival only, not a supported release lane** | `make legacy` (`make fireemblem8.gba`) — decomp-matching work only; see [`docs/archival-decomp.md`](archival-decomp.md) |

A bare `make`/`make all` never requires, builds, or resolves to a
`tools/agbcc` executable or library (issue #15; see `Makefile`'s `all:`
target and `docs/quickstart.md`'s "Modern GCC compile-only object cohort"
section). agbcc is fetched and built **only** when `make legacy`,
`make fireemblem8.gba`, or `./scripts/quickstart.sh --legacy` is invoked by
name.

## Build targets and outputs

| Command | What it produces | Builds a ROM? | Needs libmGBA? |
| --- | --- | --- | --- |
| `make` / `make all` | Modern release AAPCS ROM, boot-verified: `build/expansion-modern/release/aapcs/fireemblem8.gba` | Yes | Yes |
| `make expansion-modern-toolchain-check` | Verifies the modern compiler/assembler/flags resolve; no build output | No | No |
| `make expansion-modern-cohort` | Compile-only modern objects for the fast dependency-closure subset (`MODERN_COHORT_OBJECTS` in `modern.mk`, a `src/*.c` subset plus a small set of handwritten-assembly objects; reproduce the current split with `make print-MODERN_COHORT_C_OBJECTS`/`print-MODERN_COHORT_ASM_OBJECTS`/`print-MODERN_COHORT_OBJECTS` -- treat those commands, not any number written here, as authoritative). Accepts `MODERN_ABI=aapcs` (default) or `MODERN_ABI=apcs-gnu`; neither ABI choice links here, so both are safe compile-only comparisons -- see the ABI contract note below the table. | No | No |
| `make expansion-modern-all` | Compile-only modern objects for the full currently-supported source set (`MODERN_ALL_OBJECTS` in `modern.mk`, `wildcard`-derived from `src/*.c`/`src/data/**/*.c` + handwritten asm; reproduce the current split with `make print-MODERN_ALL_C_OBJECTS`/`print-MODERN_ALL_DATA_OBJECTS`/`print-MODERN_ALL_ASM_OBJECTS`/`print-MODERN_ALL_OBJECTS`); this drifts as source files are added/removed and is not re-verified on every unrelated edit -- treat the command, not any number, as authoritative. Accepts `MODERN_ABI=apcs-gnu` for the same compile-only comparison use as `expansion-modern-cohort` above. | No | No |
| `make expansion-modern-elf MODERN_CONFIG=<debug\|release> MODERN_ABI=aapcs` | Linked modern ELF + map. `aapcs` is the only ABI this (or any other linked/ROM/runtime target below) accepts -- `MODERN_ABI=apcs-gnu` fails fast in `modern.mk`'s linked-goal guard instead of producing an EABI5-incompatible link; see the ABI contract note below the table. | No | No |
| `make expansion-modern-rom MODERN_CONFIG=... MODERN_ABI=aapcs` | Header-verified modern ROM | Yes | No |
| `make expansion-modern-boot-check MODERN_CONFIG=... MODERN_ABI=aapcs` | Modern ROM + deterministic boot-fingerprint verification (frames 0/60/120) | Yes | Yes |
| `make expansion-modern-gdb-smoke` | Debug ELF/ROM + live ARM GDB session through the headless mGBA GDB server | Yes | mGBA SDL frontend |
| `make expansion-modern-linker-check MODERN_CONFIG=... MODERN_ABI=aapcs` | Boot-check plus budget/shift/overlay/title-fingerprint gates | Yes | Yes |
| `make legacy` / `make fireemblem8.gba` | Archival agbcc `fireemblem8.gba` | Yes | No (agbcc, fetched on first use) |
| `make clean` / `make clean_fast` | Removes build artifacts (see [`README.md`](../README.md)) | — | — |
| `make generated-data-validate` / `-generate` / `-check` / `-test` | Structured content authoring (see [`docs/generated_data_tutorial.md`](generated_data_tutorial.md)) | No | No |
| `make localization-validate` / `make localization-generate` / `make localization-check` / `make localization-test` | Expansion locale registry/catalog authoring and host tests (see [`localization.md`](localization.md)) | No | No |
| `make expansion-modern-starter-runtime-check MODERN_CONFIG=... MODERN_ABI=aapcs` | Issue #6 enabled/disabled mechanics + Danger runtime matrix | Yes | Yes |
| `make expansion-modern-aoe-check MODERN_CONFIG=... MODERN_ABI=aapcs` | Issue #42 typed AoE enabled reference + default-disabled semantic runtime matrix | Yes | Yes |
| `make expansion-modern-hq-mixer-check MODERN_CONFIG=... MODERN_ABI=aapcs` | Issue #83 enabled/disabled HQ PCM mixer, linker budget, and libmGBA PCM/interrupt-buffer matrix | Yes | Yes |
| `make expansion-modern-localization-budget-check MODERN_CONFIG=... MODERN_ABI=aapcs` | Issue #18 catalog/resolver/UI source+linker budget and real region headroom | No new ROM beyond its linked prerequisite | No |
| `python3 -m scripts.upstream_port {scan,drift,report,verify,...}` | Upstream-drift tracking (see [`docs/upstream-porting.md`](upstream-porting.md)) | No for `scan`/`drift`/`report`; `verify` builds the full gate set | No for `scan`/`drift`/`report`; depends on the gate set for `verify` |

**ABI contract:** `MODERN_ABI=aapcs` is the only supported choice for every
linked, ROM-producing, or runtime-gate target above (`expansion-modern-elf`,
`-rom`, `-boot-check`, `-linker-check`, and every target that transitively
depends on them, e.g. `-savefmt-check`/`-title-check`/`-debugtools-*-check`/
`-budget`/`-budget-check`/`-relocs`/`-overlay-audit`/`-shifted-check`).
Requesting `MODERN_ABI=apcs-gnu` for any of them fails fast in `modern.mk`
(`... requires MODERN_ABI=aapcs; ... apcs-gnu objects are incompatible with
EABI5 newlib/libgcc`) rather than silently producing a broken link --
reproduce this yourself with
`make -n expansion-modern-elf MODERN_CONFIG=debug MODERN_ABI=apcs-gnu`
(dry-run; the error still fires before any recipe would run). The **only**
targets that accept `MODERN_ABI=apcs-gnu` are the compile-only
`expansion-modern-cohort`/`expansion-modern-all` object targets above, for
cross-ABI struct-layout comparison (see
[`docs/save_format.md`](save_format.md#cross-compiler-persisted-struct-layout-compatibility));
neither of those targets links, so apcs-gnu objects never reach a linker
there.

Every `make TARGET` invocation on this page is checked by
[`scripts/check_docs.py`](../scripts/check_docs.py) (`parse_make_targets`/
`make_target_exists`, a static parse of the `Makefile`/`modern.mk`/
`generated_data.mk` include graph -- see
[`reports/issue17_documentation_audit.md`](../reports/issue17_documentation_audit.md#stale-reference-and-command-existence-evidence)
for how that check works) so a renamed/removed target fails
`scripts/check_docs.py --check` before merge. To reproduce target
resolution or object counts yourself against the current worktree, run
`make -n <target>` (dry-run, never invokes a compiler) or
`make print-<VARIABLE>` (e.g. `make print-MODERN_COHORT_OBJECTS`) --
no ROM build or network access is required for either.

### Fast (no-ROM) vs. full (ROM, optionally + libmGBA) commands

- **Fast / no-ROM**: `expansion-modern-toolchain-check`, `expansion-modern-cohort`,
  `expansion-modern-all`, `expansion-modern-elf`,
  `generated-data-validate`/`-generate`/`-check`/`-test`,
  `scripts.upstream_port scan`/`drift`/`report`, `scripts/artifact_guard.py`,
  any `python3 -m unittest discover -s .../tests`.
- **Full / builds a ROM**: `expansion-modern-rom` (no libmGBA needed),
  `make legacy`/`make fireemblem8.gba` (no libmGBA needed), the bare
  `make`/`make all` default, `expansion-modern-boot-check`,
  `expansion-modern-linker-check`, `expansion-modern-debugtools-*-check`,
  `expansion-modern-savefmt-check` (these five need libmGBA too), and
  `scripts.upstream_port verify`.

### Consolidated Build CI

Prefer focused local checks during iteration. A no-checkout `event-identity`
validator, base-authoritative `event-router`, and mode-specific classifier
check precede candidate `host-tests`, `build`, `extended-host-tests`, `legacy`,
and fail-closed `summary` jobs plus Copilot review. Metadata uses runner-backed
`host-tests`/`build` continuity adapters and still emits canonical `summary`;
those adapters independently revalidate the raw edited pull-request event from
the runner-owned file-backed `GITHUB_EVENT_PATH`, exact body/title-only
`changes` keys, and changed prior values without any checkout or candidate
import. They accept only a same-owner regular event file up to 1 MiB, read at
most one additional EOF byte, and never copy large body/title/changes JSON
through env. Metadata `summary` succeeds only after a trusted no-checkout
Actions API proof classifies exact prior runs newest-first, skips only
conclusively metadata runs, and confirms the newest conclusively full Build CI
run for the same repository, PR number, authoritative base SHA, and immutable
head SHA completed successfully. It first requires complete paginated results
with stable `total_count`, single-page `Link` omission, exact non-final
`next`/`last` relations, no final `next`, exact per-page cardinality, stable
`workflow_id`, ordered positive `run_number`/`run_attempt` values, one exact
current-run observation, and rejects redirects before any second
authenticated request. A newer failed,
cancelled, in-progress, or malformed full run blocks older successes. Normal
`summary` remains the sole
candidate attestation, and
`candidate_evidence` still treats a metadata-only run as ineligible by itself.
Only parsed body/title-only edits suppress the expensive worker execution; the
two required adapters and the canonical summary continuity job may still take
runners briefly while they perform only their fixed trusted attestation/API
proof. A
merged `master` push reruns the complete combined gate and adds only
`patch-release`. Unique CJK/font, codec,
configuration/budget, and archival evidence stays parallel with Build-owned
modern debug/release, artifact, documentation, generated-data, and
localization commands. The expected wall clock is approximately 35–40 minutes,
not a hard duration gate.

The canonical Build CI gate runs `expansion-modern-linker-check` with `-j2`:
battle-animation producers retain the last complete object while staging a
replacement and then publish it with same-directory atomic replacement, so
concurrent linker consumers cannot observe a missing or torn object. The
object and its `.sym.o` sidecar are a generation pair: every supported linker
consumer holds the same output lock while opening both paths, so it observes
either the complete old pair or the complete new pair rather than the
individual replacement gap.
`TC-BUILD-BANIM-001` maps that producer/consumer overlap to
`python3 -m unittest scripts.modernize.tests.test_arm_compressing_linker_lock -v`;
its explicit legacy delete-before-build control preserves the pre-fix
missing-input failure, while staged-output and first-build assertions cover
failed/interrupted production cleanup and no-prior-publication behavior.

## Configuration surface

The full settings reference (versions, ROM identity, `MODERN_CONFIG`/
`MODERN_ABI`/`MODERN_ROM_SIZE`/`MODERN_TEXT_SHIFT`, the config-identity
fingerprint, and what is/isn't save-compatibility-relevant) lives in
[`docs/config_identity.md`](config_identity.md); this document does not
duplicate it. Persistent feature/profile choices use the GNU Autoconf front
end (`./configure --help`, then `make`); direct Make overrides remain
supported for one-off builds.

## Troubleshooting

Setup troubleshooting (missing sudo, stale Arch package DB, already-installed
toolchain, slow rebuilds) is maintained in one place:
[`docs/quickstart.md`](quickstart.md#troubleshooting). Modern-toolchain
compile-probe failures and the Homebrew cask-vs-formula pitfall are covered
in [`docs/quickstart.md`](quickstart.md#modern-gcc-compile-only-object-cohort).

## Merged framework contracts

Issues **#6**, **#10**, **#11**, **#13**, and **#18** have implementation
merged into the current source tree. This is not an issue-closure action; each
surface remains bounded by its live reference and evidence report.

- **#6 starter features:**
  `EXPANSION_MECHANICS_HOOKS`, `EXPANSION_MECHANICS_SAMPLE`,
  `EXPANSION_DANGER_OVERLAY_MENU`, and `EXPANSION_STARTER_CONTENT` all default
  to `0`. Sample requires hooks; starter content requires hooks and
  `FE8_ITEM_ID_CAP>=0xCE`. The mechanics registry has typed callbacks, eight
  slots, copied key/label storage, deterministic order, explicit error codes,
  and a reentrancy guard. Debug and release both run enabled and default-disabled runtime negatives;
  the content profile rides gates 20-21 of the current
  `scripts/upstream_port/verify.py` sequence. See
  [`starter_features.md`](starter_features.md).
- **#10 typed IDs:** DEFAULT committed and ACTIVE build-local contracts,
  consumer census, and modern-only item cap `0xCE` pilot are supported; its
  debug/release runtime commands are gates 20-21 of the current-master
  28-gate upstream-port verifier; gate 22 builds the all-locales/all-features
  patch profile once and runs the required map-menu presentation scenario.
  There is no class/chapter/unit/character
  widening
  or implied save migration. See
  [`id_space.md`](id_space.md).
- **#11 debug tools:** release-safe config gate, fixed-capacity action API,
  title/map/prep entry points, five bounded tools, and scalar diagnostics are
  supported. No full debug-print protocol, arbitrary memory editor, or
  in-ROM interactive debugger is claimed. External ARM GDB is a separate,
  supported developer tool installed by quickstart. See
  [`debugtools.md`](debugtools.md).
- **#13 runtime harness:** deterministic JSON scenarios/fingerprints,
  `GBA_PLAYTEST_HOST_ONLY=1`, timeout/retry/provenance policy, and live ROM
  verification are supported. Ubuntu + `arm-none-eabi` is the only CI matrix;
  macOS/Homebrew remains documented local support, not CI evidence.
- **#18 localization:** append-only locale/message IDs, English and generated
  `qps-ploc`, build config/derived defines, resolver/cache, independently
  checksummed prefs, save format/epoch 2 migration precedence, first-start
  selector/repair, settings and soft-reset persistence, source/linker budgets,
  and host/debug/release/shifted/save runtime matrices are supported. Reserved
  locale slots have no catalog content and pseudo is not a translation. See
  [`localization.md`](localization.md) and [`save_format.md`](save_format.md).
- **#42 typed AoE:** fixed-capacity shape/filter targeting, deterministic
  effect execution, one shared item/action/AI route registry, explicit
  EXP/animation/event/AI/save contracts, and a default-off project-neutral
  radius-heal reference are supported in modern AAPCS builds. See
  [`aoe.md`](aoe.md).

The archival agbcc lane remains explicit and default-only for these expansion
features. Modern output is judged by link/boot/runtime behavior, never vanilla
ROM byte identity.

## Tester-facing procedure

[`TC-CORE-009`](test-cases/core-framework.md#tc-core-009-reproduce-default-release-from-a-successful-build-ci-run)
documents how to bind a successful Build CI URL/head SHA to a locally
reproduced default AAPCS release, verify its header, and distinguish that
reproduction from issue #49's published maximal BPS artifact.

## Future versioned release work (issue #9)

No release automation, semantic-version/tag/changelog contract, versioned
artifact publication, or downstream updater exists in the current tree.
[`release-migration-template.md`](release-migration-template.md) is unfilled
future scaffolding, not a current release procedure.
