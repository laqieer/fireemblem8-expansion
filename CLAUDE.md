# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a ROM-hack base derived from the **Fire Emblem: The Sacred Stones**
(GBA) decompilation. The expansion output is not required to be
byte-identical to the original ROM.

This repository's default, supported contribution path is the **modern
`arm-none-eabi` GCC/AAPCS release framework** (see
[`docs/quickstart.md`](docs/quickstart.md) and
[`docs/framework-support.md`](docs/framework-support.md)). The original
agbcc-based decompilation build is preserved as an explicit, separate
**archival** lane (`make legacy` / `make fireemblem8.gba`) for
byte-for-byte decomp-matching work only — see
[`docs/archival-decomp.md`](docs/archival-decomp.md). Start from the
[documentation index](docs/README.md) or
[`CONTRIBUTING.md`](CONTRIBUTING.md) for the full contributor workflow;
this file only covers what an AI agent needs while editing code in this
repository.

## Build Commands

```bash
# Default, supported path: modern release ROM, boot-verified (no agbcc involved)
make

# Equivalent explicit form
make all

# Archival lane: agbcc-based fireemblem8.gba, explicit target required
make legacy -j$(nproc)   # equivalently: make fireemblem8.gba -j$(nproc)

# Clean all build artifacts (slow — recompresses battle animations)
make clean

# Clean everything except battle animation compression outputs (preferred)
make clean_fast
```

A bare `make`/`make all` always builds and boot-verifies the modern
release ROM and never requires, builds, or resolves to a `tools/agbcc`
executable or library. Modern ROM correctness is judged by successful
link, boot, and runtime behavior, not by equality with the vanilla ROM;
the archival lane remains available for byte-level matching investigations
without a whole-source/object/ROM identity hash gate.

Release provenance derives path, mode, and gitlink membership from the
immutable target tree. Never commit redundant source/blob/object/ROM/commit
snapshots, per-file blob hashes, or duplicated submodule pins. Committed
provenance may preserve human exact-path facts only; configuration fingerprints,
external source/dependency hashes, extracted-content integrity, behavioral
framebuffer/SRAM hashes, format CRCs/checksums, exact candidate binding, and
release-time artifact hashes remain required where consumed.

## Remote Completion Gate

For any task that changes tracked files, local implementation and tests are
not completion. Unless the user explicitly says not to commit or push, commit
and push all intended changes, require clean candidate Build CI and Copilot
review, merge, then require the same combined Build CI on the exact `master`
revision before `make remote-completion-check` can pass. Only the technically
used patch publisher is master-only.

For an objective to resolve all repository issues, also close the resolved
issues and require `make all-issues-completion-check` to pass. Track commit,
push, CI, and issue closure as explicit dependent todos from the start.

CI waiting must not occupy a reasoning subagent. The subagent that dispatches
a workflow records its exact SHA and run ID, then returns immediately. The
orchestrator runs exactly one bounded direct shell watcher:
`timeout 90m gh run watch <run-id> --interval 30 --exit-status`. Rely on the
shell runtime's completion notification, and invoke a reasoning agent only
after the run is terminal to inspect logs or reviews. Do not repeatedly wake an
agent to poll, do not create duplicate watchers, and cancel superseded
candidate runs before dispatching replacement checks.

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

## First-time setup

First-time setup: `./scripts/quickstart.sh` installs/probes the modern
toolchain, an ARM GDB debugger, the mGBA GDB-server frontend, and libmGBA by
default, **no agbcc of any kind**; pass
`--legacy` (or `--refresh-agbcc`) only when you need the archival lane,
which installs agbcc instead. A legally obtained `baserom.gba` is
optional and only needed by `asmdiff.sh`. See
[`docs/quickstart.md`](docs/quickstart.md) for full setup/troubleshooting
and [`docs/framework-support.md`](docs/framework-support.md) for exactly
which hosts/toolchains/targets are supported vs. archival-only.

Persistent feature/profile choices use `./configure --help` followed by
`make`. The generated `config.autotools.mk` and `GNUmakefile` are ignored;
direct `make VAR=value` remains supported for one-off overrides.

Authoring game content (characters/classes/items/supports/etc.) goes
through the generated-data platform rather than hand-written C tables —
see [`docs/generated_data_tutorial.md`](docs/generated_data_tutorial.md)
(`make generated-data-validate`, `-generate`, `-check`, `-test`).


Optional starter features are four default-off, fingerprinted flags with
validated dependencies; never infer the profile from the item cap alone. See
[`docs/starter_features.md`](docs/starter_features.md). Expansion UI strings
and locale IDs use the independent catalog under `texts/expansion/`; see
[`docs/localization.md`](docs/localization.md) and run the stdlib localization
suite before changing registry/catalog data.

## Compiler & Toolchain

- **Modern (default release lane)**: `arm-none-eabi` GCC targeting AAPCS.
  No agbcc of any kind is involved in `make`/`make all`.
- **agbcc (archival lane only)**: a modified GCC 2.95 targeting ARM7TDMI
  (Thumb/ARM interwork), located at `tools/agbcc/`, used only by
  `make legacy` / `make fireemblem8.gba`. This is **C89-era** — no `//`
  comments in compiled code, no C99 features.
  - Compiler flags: `-mthumb-interwork -Wimplicit -Wparentheses -Werror -O2 -fhex-asm`
  - Source is preprocessed with `cpp`, piped through `iconv` (UTF-8 → CP932), then compiled with `agbcc`
  - Some files use `old_agbcc` or different flags — see per-file overrides in `Makefile`

## Architecture

### Archival decomp workflow

Assembly lives in `asm/`, decompiled C goes in `src/`. The linking order
in `ldscript.txt` determines ROM layout. When decompiling a function: add
`src/x.o(.text)` **before** `asm/x.o(.text)` in `ldscript.txt`, remove the
function from the `.s` file, and write the C equivalent in `src/x.c`. The
full tutorial, rules, setup steps, and asset-extraction references live
in [`docs/archival-decomp.md`](docs/archival-decomp.md) — this is
archival-lane-only guidance for byte-for-byte matching work, not the
default modern framework path described above.

### Proc System (Cooperative Multitasking)

The engine uses a **Proc** system (`include/proc.h`, `src/proc.c`) — a tree-based cooperative scheduler. Game entities are `struct Proc` with script tables (`struct ProcCmd[]`) defining behavior as command sequences: `PROC_CALL`, `PROC_REPEAT`, `PROC_SLEEP`, `PROC_YIELD`, `PROC_START_CHILD_BLOCKING`, etc. Local proc structs embed `PROC_HEADER` at offset 0 and add custom fields after.

### Memory Sections

- `EWRAM_DATA` — variables in external work RAM (256 KB), used for large/global game state
- `CONST_DATA` / `SECTION(".data")` — data that logically should be `const` but was in `.data` in the original binary
- `EWRAM_OVERLAY(id)` — overlaid EWRAM sections for memory reuse between screens

### Key Subsystems

- **Units**: `struct Unit` / `struct CharacterData` / `struct ClassData` in `bmunit.h`. Arrays: `gUnitArrayBlue` (player), `gUnitArrayRed` (enemy), `gUnitArrayGreen` (NPC), `gUnitArrayPurple`
- **Events**: scripted cutscenes via event engine (`src/event.c`, `src/eventscr*.c`)
- **Battle animations**: `src/banim-*.c` — largest subsystem. Data in `banim/` with custom compression linker (`scripts/arm_compressing_linker.py`)
- **World map**: `src/worldmap_*.c`
- **Text system**: source in `texts/*.txt`, processed by `scripts/texttools/` into `src/msg_data.c` and `include/constants/msg.h`

## Code Conventions

### Include Order

Every `.c` file starts with `#include "global.h"` as the **first** include. This pulls in GBA types, `prelude.h`, `types.h`, `variables.h`, and `functions.h`. Then constants headers (`constants/*.h`), then module headers.

### Naming

- Functions and types: `PascalCase` (`Proc_Start`, `struct Unit`, `GetItemAttributes`)
- Global variables: `gCamelCase` (`gActiveUnitId`, `gPaletteBuffer`)
- Static/local variables: `sCamelCase` (`sProcArray`, `sKeyStatusBuffer`)
- Constants/enums: `UPPER_SNAKE_CASE` (`UNIT_LEVEL_MAX`, `PROC_MARK_EVENT`)
- Many functions retain `sub_XXXXXXXX` placeholder names from ROM addresses (renaming these to descriptive names is part of the archival decompilation effort)

### Struct Layout

Struct fields are annotated with byte offset comments: `/* 0C */ struct Vec2 camera;`

### Header Guards

All headers use `#ifndef GUARD_FILENAME_H` / `#define GUARD_FILENAME_H`.

### Formatting

`.clang-format` configured: Allman braces, 4-space indent, 100-column limit, no tabs. `global.h` is always sorted first in includes.

### Legacy Layout Constraints

The current migration still relies on original ABI and data-layout details:
- Preserve register-sensitive code only where the legacy compiler still requires it
- `STRUCT_PAD(from, to)` pads struct fields to preserve explicit layout
- `SHOULD_BE_CONST` marks data that must stay writable for legacy placement
