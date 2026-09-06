# Copilot Instructions — fireemblem8-expansion

This is a ROM-hack base derived from the **Fire Emblem: The Sacred Stones**
(GBA) decompilation. The expansion output is not required to be byte-identical
to the original ROM.

Before adding a prerequisite, gate or service in this repository, identify the
original accepted requirement or concrete risk, accepted threat model, smallest
existing mechanism, and why a simpler solution is insufficient. Review findings
do not automatically expand requirements: fix accepted-contract bugs, simplify
architecture-created problems, and separate optional hardening with honest
claims. At the existing third-round/8K reconsideration point, compare a concrete
simpler design and remove unnecessary machinery or split complete independent
contracts when needed—not merely write another note. Measure the original
end-to-end outcomes and total delivery cost; preserve real safety requirements
and every final gate.

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

Every new task commit, including WIP/checkpoints, must be owner-pushed
immediately. Do not wait for reviews, extra validation, CI, or batching after
the commit exists. Implementation owners immediately return the exact commit
for the coordinator's push; they do not push themselves. Publish or update the
dedicated PR (draft if incomplete) and canonical evidence comment with issue,
branch/head, owner, scope, state, remaining work, and blockers so other
contributors/agents do not duplicate it. Persistence does not authorize
handoff acceptance, terminal publication, merge, or closure. A failed push is
an explicit blocker, never a success claim. The canonical
[publication protocol](skills/development-workflow/SKILL.md#immediate-publication-and-visible-work)
retains every existing final gate.

Implementation subagents validate and commit locally but do not push. The
orchestrator pushes the exact commit under repository-owner context so Build
does not become `action_required`. If an already-pushed run for that same SHA
is `action_required`, the orchestrator reruns it with `gh run rerun <run-id>`
under owner context. Never create empty commits, weaken Actions approvals, or
use privileged `pull_request_target` just to bypass approval.

Finalize stable PR title/body before pushing the candidate, then freeze both
while its exact-head full Build is active. Update evolving evidence only in the
canonical comment. Use the isolated `pr-metadata edit` helper for later
title/body corrections; its default path defers active-full races. Essential
corrections require a nonempty reason and confirmation-comment-bound
`pr-metadata reconcile` after the same full Build succeeds. Preserve the
returned intent and confirmation comment IDs; the append-only owner-authored
pair and metadata-specific GitHub version are authoritative rather than caller
data or a mutable local ledger. The helper never edits/deletes those
transaction comments or cancels/dispatches a full Build, and title/body edits
never make a same-SHA full Build stale.

Local validation is change-focused by default. Run only the smallest tests
that directly cover the changed behavior and the one necessary compile or
runtime scenario. Do not run broad catalog validation, full repository test
suites, all-locale/all-feature profiles, broad archival builds, or every
supported profile locally unless the changed surface directly owns that gate
or focused evidence cannot answer the acceptance criterion. Combined Build CI
is the comprehensive final integration gate. Stop after focused checks pass,
commit the candidate, and hand it off.

## Sibling-family review convergence

For a high-risk or large change, use one fresh bounded read-only reviewer
before the first remote review. Keep implementer, reviewer and coordinator
ownership distinct. Use the existing task/tool interfaces, not another agent
backend. The reviewer can read the exact candidate and supplied evidence and
return its report; it cannot edit, push, comment, request review, dispatch CI
or merge. Enforce those actions at dispatch, not by accepting a claimed
permission list from candidate JSON.

The coordinator binds every accepted finding to its actual existing case,
production predicate and finite source model. Expand actions/items/targets,
lifecycle, wire/replay/stale bindings, generated owners/consumers/drift, and
enabled/disabled resources completely. A missing sibling still blocks after
the reported member is fixed. Use actual source-backed test observations,
not arbitrary pass labels or a whole-suite result relabeled as member/ROM
evidence. Select any new binding at an explicitly reviewed exact tool revision;
it can land in the same feature PR and does not require base-first installation.

First and second change requests produce bounded handoffs. The third creates
a sticky architecture/decomposition hold; new heads and later clean reviews
do not clear it without a coordinator disposition bound to the held round/head.
Stop new narrow work, but immediately publish already-created commits on their
assigned branch as explicitly ineligible WIP. Do not invent side branches or
delay persistence. GitHub IDs/heads/actors are facts; complete-content triage is
the coordinator's responsibility. COMMENTED or zero new inline comments is not
approval. Local audits never replace exact-head Copilot/security/Build or
exact-master completion.

Follow the [executable API and tester procedure](../docs/workflow-pilot.md#sibling-family-review-convergence).
The trusted coordinator and reviewed test tools are the authority. Read-only
roles and minimal environments are operational controls, not hostile same-UID
OS isolation. No broker, receipt/signature platform or protected installation
is required.

## CI waiting

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

### Bounded exact-SHA implementation handoffs

Use the [version-3 handoff contract](../docs/workflow-pilot.md#bounded-exact-sha-implementation-handoffs)
for bounded implementation cycles. The coordinator owns the assignment, real
Git/check/process observations and one locked session-local coordination
document; the implementation returns only the assignment ID, echoed parent,
result SHA and named evidence references. These records are not authenticated
data or publication capabilities.

Keep assignment sent, received, progressing, committed and handed-off distinct.
Use actual CLI events and OS exit observations, never tool-transport success or
printed pass labels. Retire an owner after its committed handoff or lifetime
limit and use a fresh owner for review. Permit explicitly recorded normal
upstream merges; apply task trailers/scope to task-owned changes, not imported
upstream history. Incremental assignment budgets do not replace full-PR
review-size preflight.

Keep one real direct watcher per exact GitHub run/attempt. Reconcile watcher
errors through GitHub; a process timeout is not CI failure or success. On
interruption, preserve and lock the original worktree before one bounded
replacement reuses it. Unknown PID/RSS/OOM observations remain unknown.
Record an always-on coordinator or an explicit availability plan before
unattended delivery; a plan is not a guarantee of uptime.

Only the existing coordinator publishes through Git/gh. Handoff tooling never
pushes, modifies remote refs or supplies credentials to candidate execution.
Use the existing reviewed-source/approved check route and platform role
permissions, not an alleged Python or same-UID OS sandbox. Immediate checkpoint
publication, metadata-event handling, final gates and completed-worktree
cleanup remain unchanged.

### Completed-worktree cleanup

After the PR is merged, all relevant exact-master CI is green, and
`make remote-completion-check` passes, the delivery coordinator must run the
[completed-worktree planner](../docs/workflow-pilot.md#completed-worktree-cleanup)
and apply it to explicitly selected eligible leftovers. Do not delete the
workspace earlier: failing master CI still needs its fix owner.
The coordinator supplies every assigned/active path with `--preserve`,
including agents between commands, and does not reassign a target during apply.
Keep the source/coordinator workspace outside the removal set.

Planning is read-only by default. Apply freshly verifies exact Git ownership,
local work, merged PR association, and automatic master Build plus relevant
exact-proof-commit CI; a candidate success or a merged PR alone is insufficient.
Historical completed work may use its verified post-merge master proof rather
than an unrelated newer run. Retain every ambiguous, active, locked, dirty,
untracked, unpushed, or missing/failed/pending-evidence workspace. Also retain
nested/bare Git repositories, private reflog/pseudoref/index resolve-undo
objects not durably reachable from shared refs, private configuration and
unclassified recovery/index metadata, and partial/promisor repositories whose
object lookups could fetch during a dry-run. Preserve filesystem byte paths in
mount/backlink checks and reports. Do not erase configuration/recovery records
or rewrite indexes to bypass these holds. Use only normal
`git worktree remove` through the helper, never force, unlock, delete branches,
globally prune, or recursively delete broad directories.
Record removed paths, observed allocated sizes, and precise retained reasons
in existing completion evidence; do not invent physical freed-byte totals or
commit a mutable worktree ledger. Cleanup never delays immediate owner-push:
an implementation agent returns each new commit immediately, including WIP,
and never keeps it locally while waiting for review, CI, or cleanup.

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
