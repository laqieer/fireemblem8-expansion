# Copilot Instructions — fireemblem8-expansion

This is a ROM-hack base derived from the **Fire Emblem: The Sacred Stones**
(GBA) decompilation. The expansion output is not required to be byte-identical
to the original ROM.

## Build

This repository's default, supported path is the **modern
`arm-none-eabi` GCC/AAPCS release framework** (see `docs/quickstart.md`
and `docs/framework-support.md`). A bare `make`/`make all` always builds
and boot-verifies the modern release ROM and never requires, builds, or
resolves to a `tools/agbcc` executable or library. The original agbcc-
based decompilation build is preserved as an explicit, separate
**archival** lane (`make legacy` / `make fireemblem8.gba`) for byte-for-
byte decomp-matching work only — see `docs/archival-decomp.md`.

First-time setup: `./scripts/quickstart.sh` installs/probes the modern
toolchain, an ARM GDB debugger, the mGBA GDB-server frontend, and libmGBA by
default, **no agbcc of any kind**; pass
`--legacy` (or `--refresh-agbcc`) only when you need the archival lane,
which installs agbcc instead. A legally obtained `baserom.gba` is
optional and only needed by `asmdiff.sh`.

Persistent feature/profile choices use the committed GNU Autoconf front end:
run `./configure --help`, select flags/locales/caps, then run `make`.
`configure` writes ignored `config.autotools.mk` and `GNUmakefile` outputs;
direct `make VAR=value` remains supported for one-off overrides.

For generated content use `docs/generated_data_tutorial.md`; for the four
validated, default-off starter flags and typed mechanics registry use
`docs/starter_features.md`; for stable locale/message IDs, catalog authoring,
prefs and selector behavior use `docs/localization.md`. Never hand-edit
build-local generated output.

```bash
# Default, supported path: modern release ROM (no agbcc involved)
make

# Archival lane: agbcc-based fireemblem8.gba, explicit target required
make fireemblem8.gba -j$(nproc)   # equivalently: make legacy

# Clean all build artifacts (slow — recompresses battle animations)
make clean

# Clean everything except battle animation compression outputs
make clean_fast
```

A successful build exits cleanly. Modern ROM correctness is judged by
successful link, boot, and runtime behavior, not by equality with the
vanilla ROM. The archival lane remains available for byte-level matching
investigations, but no whole-source/object/ROM identity hash is enforced.
`./asmdiff.sh <hex_addr> <byte_len>` remains available for legacy matching
investigations when `baserom.gba` is present.

Release provenance derives candidate paths, modes, and gitlinks from the
immutable target tree. Do not add committed source/blob/object/commit snapshots,
per-file content-hash ledgers, or duplicate submodule pins. Do not keep
human provenance/legal-review facts in committed metadata. Configuration fingerprints,
external input/dependency hashes, extracted-content integrity, behavioral
framebuffer/SRAM hashes, format checksums, exact candidate SHA binding, and
release-time manifest/archive hashes remain valid independent boundaries.

## Remote completion gate

For any task that changes tracked repository files, local implementation and
tests are not completion. Unless the user explicitly says not to commit or
push, do not call `task_complete` until all intended changes are committed and
pushed, the candidate Build CI and Copilot review are clean, the change is
merged, automatic consolidated Build CI succeeds on the exact `master`
revision, and `make remote-completion-check` succeeds. Candidate and master
Build runs contain the same broader host, archival, and summary jobs; only
the technically used patch publisher is master-only.

For an objective to resolve all repository issues, also close the resolved
GitHub issues and require `make all-issues-completion-check` to succeed. Create explicit
dependent todos for commit, push, CI, and issue closure at the start of such a
task. Memory is advisory context; these executable gates are the completion
authority.

Delivery dependencies are typed. A child issue's `code_contract`
implementation dependency is satisfied when the parent merge is done and the
target tree contains the contract; it must not depend on the parent's
post-merge Build, evidence/closure, or remote-completion task. Those remain
`delivery_gate` dependencies for completing and closing the parent itself.
Async watcher state is orthogonal and must never be a todo dependency. While a
healthy exact-master watcher is pending, immediately continue every
dependency-ready child or independent task. A terminal failed master run
starts fix-forward/revert work, but cannot retroactively justify idle waiting
during its earlier pending state.

Each bounded implementation handoff must map to exactly one typed child
relationship and implementation task with matching issue, PR, candidate, and
lifecycle-derived status. Bind parent post-merge Build state to its
authoritative exact-SHA workflow run. Required checks use only closed
structured non-shell contracts with sealed execution receipts; never execute
a caller-authored command or trust a passed label without rerunning its safe
contract.

Use immutable numeric GitHub IDs for every owner, coordinator, and remote
event. One root owner exists for each issue authority. A closed commit may
have one nonoverlapping `review_successor`; an interrupted current owner may
have one `oom_replacement`. Both edges are linear, causal, fresh-owner
transitions. Reporter v2 separates live eligibility from historical metrics:
an external finalize operation signs the complete canonical source document,
validation result, summary, outcomes, and verified metrics. The unkeyed result
hash is integrity-only. Historical verification requires that signature and
authority/anchor ancestry without the old worktree HEAD.

Create the issue-scoped protected authority branch before a PR exists and
never rename or reset it at PR creation. Bind the eventual PR by appending an
immutable, externally signed GitHub PR API response containing state `OPEN`,
unmerged status, repository, number, base/head branches and OIDs, head
repository, creation/observation times, and coordinator numeric ID. Compare it
to independent delivery values frozen in protected genesis/root assignment
and the pre-observation publication request, never to fields copied from the
response itself. Keep `delivery_expectation.immediate_base_oid` as frozen
provenance, bind the live current base OID separately, and require the frozen
base to be an ancestor of both the live current base and the candidate head.
Every authority and independent anchor update consumes one short-lived signed
publication-plan identity through the authenticated external broker, which
derives both direct-parent updates and performs atomic preflight/push. Split,
stale, arbitrary-ref, wrong-object, or substituted plans reject. A terminal signed GitHub ruleset API response must prove active
exact-branch targeting, restricted updates/non-fast-forwards/deletion, and
only expected GitHub `User` bypass records whose `actor_id` and `database_id`
both equal the frozen coordinator user ID with exact `always` mode.
`RepositoryRole` IDs are roles, not users; other types require a separately
typed frozen authorization and default to rejection. Bounded
remote-OID-before/fetch/remote-OID-after reads and a final dual-ref
observation check reject replay, rollback, and ABA. Historical reads also
recompute the stored PR binding digest and the exact canonical digest of each
sealed history receipt. Every protected handoff commit also carries one
bounded private `history_carrier` containing the exact signed handoff
document, the canonical validation result, and the selected handoff ID;
readback re-verifies the original coordinator, PR, and authority signatures,
replays `make_history_receipt()` from that carrier, and requires the signed
publication attestation's `history_carrier_digest` and
`history_receipt_digest` to match the full carried carrier and receipt bytes
rather than a projected event subset. Returned authority snapshots keep
`history_carrier: null` so later handoff documents do not recursively embed
older carriers. Historical reads also require the signed publication
attestation's `binding_expectation` to match the stored frozen delivery plus
stored live current-base fields, and re-check that the frozen base remains an
ancestor of the stored live base OID. The stored `pr_binding.head_oid`, its
digest, and `binding_expectation.head_oid` must all match the immediately
prior sealed handoff candidate carried by the current
`handoff_sequence`/`head_seal`. Derive the stored observation's
`authority_object_id` from the bind commit's canonical `previous_object_id`.
Derive the stored observation's `anchor_object_id` from the exact prior
canonical anchor record, never from copied observation fields. Swapped signed observations/publications,
stale signed observation replays, out-of-band branch advances, copied
anchors/seals/sequences, and rewritten bases fail even when each record is
individually valid.

Allowed Git checks execute the exact checker blob from the assigned parent,
not the candidate worktree. If that parent predates the checker, an external
coordinator installation must supply it; this bootstrap result is explicitly
ineligible for `trusted_push_eligible` until the checker is merged. The raw
checker pins whitespace/config behavior and disables external diff, textconv,
local attributes, and ambient configuration bypasses.

Remote-action, availability, resource, OOM snapshot, RSS, lifetime,
coordination-turn, dependency graph, handoff, run, and watcher evidence comes
only from an asymmetric attestation signed by an isolated external service
whose private key is absent from the implementation namespace. Local HMACs
and permission modes establish no trust. The external service maintains a
monotonic consume sequence/anchor and spent-nonce store. One atomic operation terminates
the implementation process, performs final timeline/run/ref/audit collection
through the consume instant, decides, spends the nonce, and returns the signed
decision; a second call fails before authority publication. Local receipt-age
checks cannot turn a preissued receipt into eligibility; the live receipt must
satisfy the same narrow freshness window as live PR/publication observations,
and receipt repository IDs/full names must match the installation manifest and
frozen delivery expectation. Any after-sign remote mutation invalidates the
decision. Incomplete coverage requires a credentialless, network-denied
process.

OOM history stores content-bearing file bytes, modes, hashes, original
assignment/scope/criteria/checks/budgets, and status in the protected
authority event. A completed replacement must restore every preserved change
or carry an attested explicit resolution mapping. Only exact proven host-tool
paths derive zero resource impact; linker scripts, Makefiles, assets, configs,
fonts, text, generated data, and every unclassified tracked input require a
closed build/map/resource receipt.
Sent/received/progressing prefixes remain non-eligible replacement progress,
and the replacement assignment must be strictly later than interruption.

Implementation subagents validate and commit locally but do not push. The
orchestrator pushes the exact commit under repository-owner context so Build
does not become `action_required`. If an already-pushed run for that same SHA
is `action_required`, the orchestrator reruns it with `gh run rerun <run-id>`
under owner context. Never create empty commits, weaken Actions approvals, or
use privileged `pull_request_target` just to bypass approval.

Local validation is change-focused by default. Run only the smallest tests
that directly cover the changed behavior and the one necessary compile or
runtime scenario. Do not run broad catalog validation, full repository test
suites, all-locale/all-feature profiles, broad archival builds, or every
supported profile locally unless the changed surface directly owns that gate
or focused evidence cannot answer the acceptance criterion. Combined Build CI
is the comprehensive final integration gate. Stop after focused checks pass,
commit the candidate, and hand it off.

CI waiting must not occupy a reasoning subagent. The orchestrator that
dispatches a workflow records its exact SHA and run ID, then returns
immediately. The orchestrator runs exactly one bounded direct shell watcher:
`timeout 90m gh run watch <run-id> --interval 30 --exit-status`. Rely on the
shell runtime's completion notification, and invoke a reasoning agent only
after the run is terminal to inspect logs or reviews. Do not repeatedly wake an
agent to poll, do not create duplicate watchers, and cancel superseded
candidate runs before dispatching replacement checks.

For a fleet with multiple active pull requests, designate one delivery
coordinator. It owns the run/PR ledger, starts or records exactly one direct
shell watcher per active run, receives terminal watcher notifications, triages
CI and review failures, routes local-only fixes to one owner, performs each
final merge gate and autonomous merge, and initiates the post-merge conflict
sweep. The coordinator must not poll, sleep, or keep a reasoning turn alive
solely to wait. Other agents must not duplicate watchers, fix ownership, or
merge decisions; they return validated local commits to the coordinator for
the trusted owner-context push.

Every delegated reasoning agent must be launched in background mode. Never use
a synchronous subagent invocation that blocks the main orchestrator. After
launching background work, continue every independent dependency-ready task
immediately. If the result is a true dependency and no independent work
remains, end the turn and rely on the automatic completion notification
instead of waiting synchronously or polling. Keep simple work that needs only
two to five direct tool calls in the main orchestrator rather than delegating
it.

After each merge, immediately inspect every open PR. Merge current `master`
only into PRs with real conflicts or shared-contract changes; refresh
independent conflicts concurrently and rerun only conflict-affected checks
plus replacement Build/review. Never pause or cancel unaffected PR CI because
of priority or unrelated `master` movement; cancel superseded CI only when its
candidate actually changes.

After each PR opens or updates, concurrently monitor exact-head Build CI,
Copilot comments/threads, and mergeability; triage review findings
immediately. Refresh real conflicts with a normal `master` merge. Monitor master-branch CI after every merge.
That means the exact-master combined Build CI and an open-PR conflict rescan.
Fix forward or revert a broken `master`;
unrelated PRs do not wait on healthy master runs.

All exact-head Build CI, Copilot-review, and post-merge master-branch CI
monitoring uses attached asynchronous shell watchers and is nonblocking.
Continue unrelated dependency-ready work while those watchers run; never
occupy a reasoning agent or stop with a waiting-only response. Cancel only a
superseded candidate run after that candidate actually changes. A broken
master Build requires an immediate fix-forward or revert and blocks that
issue's closure and remote completion, but not unrelated independent PRs.

## Development workflow skill

For incoming feature requests, ideas, bug reports, or regressions, invoke the
project-scoped `/development-workflow` skill. It owns core-vs-project
classification, bug reproduction/root-cause triage, selective change gates,
dependency/conflict tracking, local IDA/Ghidra/GDB and reference-resource
selection, evidence requirements, autonomous merge, and the precise hold
boundary for criteria the agent cannot validate. It is authoritative over
conflicting generic review or closure guidance and requires no human review or
approval.

## Meaningful test evidence

- **Evidence standard:** required
  - **behavior:** required
  - **parsed structural contract:** required
  - **generated output:** required
  - **compile/link properties:** required
  - **runtime state:** required
- **Prohibited evidence:** prohibited
  - **sole-evidence rule:** prohibited
  - **arbitrary strings:** prohibited
  - **comments:** prohibited
  - **helper names:** prohibited
  - **line numbers:** prohibited
  - **ordering:** prohibited
  - **implementation spelling:** prohibited
  - **Git-text rationale:** required. git-tracks=source,review,history;
    raw-tracked-text=not-behavior-evidence
- **Static-contract exception:** conditional
  - **source-text assertion:** permitted-only
  - **exact syntax/spelling/absence:** required
  - **documented public format:** one-of
  - **security boundary:** one-of
  - **generated-file contract:** one-of
  - **ABI/layout constraint:** one-of
  - **externally consumed protocol:** one-of
  - **named contract:** required
  - **irreplaceable evidence explanation:** required
- **Evidence preference:** ordered
  - **real function positive/adversarial inputs:** first
  - **parsed JSON/YAML/Make/AST/binary/schema:** second
  - **compile/link typed symbols/sections/resources/generated output:** third
  - **deterministic target-ROM/libmGBA behavior:** fourth
  - **narrowly justified source-text assertion:** last
- **Replacement and mutation controls:** required
  - **accepted requirement:** preserve
  - **stronger evidence:** required-or-duplicate
  - **duplicate gate:** no-independent-contract
  - **phrase-preserving behavior change:** fails
  - **semantics-preserving spelling/order refactor:** green

## Architecture

### Compiler & toolchain
- **agbcc**: a modified GCC 2.95 targeting ARM7TDMI (Thumb/ARM interwork). Located at `tools/agbcc/`. This is C89-era — no `//` comments in compiled code, no C99 features.
- Compiler flags: `-mthumb-interwork -Wimplicit -Wparentheses -Werror -O2 -fhex-asm`
- Source is preprocessed with `cpp`, piped through `iconv` (UTF-8 → CP932), then compiled with `agbcc`.
- Some files use the older compiler (`old_agbcc`) or different flags — see per-file overrides in `Makefile`.

### Decompilation workflow
Assembly lives in `asm/` (only `arm.s` and `arm_call.s` remain, plus data files in `data/`). Decompiled C goes in `src/`. The linking order in `ldscript.txt` determines ROM layout — when decompiling a function, you add `src/x.o(.text)` **before** `asm/x.o(.text)` and remove the function from the `.s` file. Keep **both** the `src/` and `asm/` linker entries until `asm/x.s` is fully empty; leaving a function in both places causes a `multiple definition` link error. For undeclared symbols, locate the type with `git grep "<symbol>" include/` and add the owning header (functions still living in `asm/` get a bare forward declaration, not `extern`). The decomp tutorial that walks a full function end-to-end now lives in `docs/archival-decomp.md` (this is archival-lane-only guidance, not the default modern framework path).

### Files that must NOT be decompiled
These are handwritten assembly, already commented, and stay in `asm/`: `crt0.s`, `libagbsyscall.s`, `libgcnmultiboot.s`, `m4a_1.s`, `m4a_3.s`, `arm.s`, `arm_call.s`.

### Proc system (cooperative multitasking)
The engine uses a **Proc** system (`include/proc.h`, `src/proc.c`) — a tree-based cooperative scheduler. Game entities are `struct Proc` with script tables (`struct ProcCmd[]`) that define behavior as sequences of commands: `PROC_CALL`, `PROC_REPEAT`, `PROC_SLEEP`, `PROC_YIELD`, `PROC_START_CHILD_BLOCKING`, etc. Local proc structs embed `PROC_HEADER` at offset 0 and add custom fields after.

### Memory sections
- `EWRAM_DATA` — variables placed in external work RAM (256 KB). Used for all large/global game state.
- `CONST_DATA` — data that *should* be `const` but was placed in `.data` in the original binary (use `SECTION(".data")`).
- `EWRAM_OVERLAY(id)` — overlaid EWRAM sections for memory reuse between screens.

### Key subsystems
- **Units**: `struct Unit` / `struct CharacterData` / `struct ClassData` in `bmunit.h`. Unit arrays: `gUnitArrayBlue` (player), `gUnitArrayRed` (enemy), `gUnitArrayGreen` (NPC), `gUnitArrayPurple`.
- **Events**: scripted cutscenes via an event engine (`src/event.c`, `src/eventscr*.c`). Event scripts are `ProcCmd` tables.
- **Battle animations**: `src/banim-*.c` — the largest subsystem by file count. Battle animation data is in `banim/` with a custom compression linker (`scripts/arm_compressing_linker.py`).
- **World map**: `src/worldmap_*.c` — overworld screen and navigation.
- **Text system**: text source in `texts/*.txt`, processed by `scripts/texttools/` into `src/msg_data.c` and `include/constants/msg.h`.

## Conventions

### Include order
Every `.c` file starts with `#include "global.h"` as the first include. This pulls in GBA types, `prelude.h`, `types.h`, `variables.h`, and `functions.h`. Other includes follow — constants headers (`constants/*.h`), then module headers.

### Naming
- Functions and types use `PascalCase` (`Proc_Start`, `struct Unit`, `GetItemAttributes`).
- Global variables use `gCamelCase` (`gActiveUnitId`, `gPaletteBuffer`).
- Static/local variables use `sCamelCase` (`sProcArray`, `sKeyStatusBuffer`).
- Constants/enums use `UPPER_SNAKE_CASE` (`UNIT_LEVEL_MAX`, `PROC_MARK_EVENT`).
- Many functions still have original `sub_XXXXXXXX` placeholder names from the ROM addresses. Renaming these to descriptive names is part of the decompilation effort.

### Struct layout comments
Struct fields are annotated with byte offset comments: `/* 0C */ struct Vec2 camera;`. This aids matching against the original binary layout.

### Header guards
All headers use `#ifndef GUARD_FILENAME_H` / `#define GUARD_FILENAME_H` style guards.

### Formatting
Configured in `.clang-format`: Allman braces, 4-space indent, 100-column limit, no tabs. `global.h` is always sorted first in includes.

### Legacy layout constraints
The current migration still relies on original ABI and data-layout details:
- Preserve register-sensitive code only where the legacy compiler still requires it.
- `STRUCT_PAD(from, to)` is used to preserve explicit structure layout.
- The `SHOULD_BE_CONST` marker denotes data that must remain writable for legacy placement.

### Asset extraction
Raw data blobs are migrated from `dump/`*.bin into typed source (C arrays, or
`.png`/`.pal`/`.map.bin` tilemap graphics) following `docs/dump_extraction_plan.md`; each
conversion must round-trip byte-identical.

**Two tilemap (TSA) formats** — committed as binary, distinguished by file
extension (the bytes cannot be told apart structurally, only by the engine
consumer):
- `.map.bin` — **headerless** (pokeemerald `GBA_4BPP` style): raw `u16` entries,
  rows top-to-bottom, no header; size = `2*W*H`. Used by banim, scrolling
  backgrounds, and other raw tilemap consumers.
- `.tsa.bin` — **headered** ("FEGBA struct" / tilemap-studio `FEGBA_4BPP`): a
  2-byte header (`u8 width-1`, `u8 height-1`) then `(W*H)` `u16` entries, rows
  **bottom-to-top**; size = `2 + 2*W*H`. Consumed by `TmApplyTsa` /
  `CallARM_FillTileRect` (the FE "TSA" tile arrangement). Symbols use the `Tsa_`
  prefix. A few `.tsa.bin` blobs concatenate multiple TSAs after the first
  header (e.g. `gUnknown_085B0F2C`).

Renaming between these is build-safe (only the file extension and the `.incbin`
path change, not the ROM bytes). LZ-compressed assets are committed
**decompressed** (the build recompresses via `Makefile %.lz` rules) — never
commit `.lz`/`.4bpp`/`.gbapal`/`.map.bin.lz`/`.tsa.bin.lz` artifacts. When a recompressed asset
only prefix-matches the original at the 4-byte LZ header, sweep gbagfx's
minimum match distance (`gbagfx in out.lz -mindist N`, default 2) and pin the
winning value per-target via `LZ_FLAGS := -mindist N` in the `Makefile` — this
is a min-distance mismatch, not incompatible compression. See
`docs/lz_suffix_diagnostic.md`.
