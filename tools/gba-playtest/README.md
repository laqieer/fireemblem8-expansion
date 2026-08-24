# GBA headless playtest fingerprints

## Capturing mGBA debug-register logs

For an isolated real-core capture, set `GBA_PLAYTEST_LOG_CAPTURE` to a
writable file before invoking `capture` or `verify`. The libmGBA backend
records its normal log callback as tab-separated category, level, and message
records in that file; it otherwise remains silent. Issue #68's
`tools/gba-playtest/run_debuglog_checks.py` uses this seam to assert one
`FE8LOG ready` mGBA message in the debug ROM and no message in release. Set
`TMPDIR` to a repository-local build directory when running the command in an
environment that forbids system temporary directories.

`gba_playtest.py` replays a strict JSON input scenario through libmGBA, captures
named framebuffer/RAM checkpoints, and emits deterministic JSON. It is intended
to compare behavior when ROM bytes or link addresses legitimately change.
Every capture binds the behavior to machine-readable ROM provenance: SHA-1,
byte size, header title, and header game code. The backend executes the same
immutable temporary ROM copy that is hashed, avoiding a hash/load path race.
Capture output always includes that diagnostic identity. A behavior-policy
expected baseline may omit it because behavior verification does not compare
ROM identity; exact-ROM verification still requires it.

The tool does not load save files, screenshots, or savestates. Framebuffer hashes
are FNV-1a-64 over canonical 24-bit RGB bytes (alpha/padding and host endianness
are excluded). The small C backend is compiled into a temporary directory for
each command, so no emulator-generated binary is left in the tree.
Compiler, pkg-config, and emulator processes all have bounded timeouts with
actionable diagnostics. `--retries N` (default 0, i.e. a single attempt) adds
up to `N` additional attempts, capped at `MAX_RETRIES_CAP` (5) regardless of
the requested value, and applies only to a process **time-out** -- the one
condition here that can plausibly be transient host scheduling/load. A
non-zero exit code, a malformed backend/compiler diagnostic, or a fingerprint
mismatch are deterministic outcomes and are never retried by any code path in
this tool; retrying those would silently launder a real, reproducible failure
into intermittent-looking flake. Every retried attempt (not just the final
one) is printed to stderr with its attempt number, so a flaky time-out is
always visible even when a later attempt succeeds.

## Dependencies

Python 3.10+ is sufficient for parsing, serialization, diagnostics, and host tests.
Capture/verify additionally require a C compiler and libmGBA development files.

Linux (Debian/Ubuntu):

```sh
sudo apt-get install build-essential libmgba-dev
python3 tools/gba-playtest/gba_playtest.py backend-check
```

macOS (Homebrew):

```sh
brew install pkg-config mgba
python3 tools/gba-playtest/gba_playtest.py backend-check
```

If libmGBA or the compiler is unavailable, `capture`, `verify`, and
`backend-check` fail with exit status 2 and an installation diagnostic. They
never silently pass. The backend integration unit test is the only test that may
skip, and its skip reason includes the backend diagnostic.

## Commands

```sh
# Host tests (no ROM and no libmGBA needed, except the explicit integration test)
python3 -m unittest discover -s tools/gba-playtest/tests -v

# Same suite, explicitly host-only: every ROM-dependent live-integration test
# skips by mode, regardless of which ROMs exist in the worktree (see
# "Host-only test mode" below). This is the exact CI `host-tests` command.
GBA_PLAYTEST_HOST_ONLY=1 python3 -m unittest discover -s tools/gba-playtest/tests -v

# Capture sorted, reproducible JSON
python3 tools/gba-playtest/gba_playtest.py capture \
  --rom fireemblem8.gba \
  --scenario tools/gba-playtest/scenarios/boot.json \
  --output build/boot-capture.json

# Replay and compare every checkpoint/hash/probe
python3 tools/gba-playtest/gba_playtest.py verify \
  --rom fireemblem8.gba \
  --scenario tools/gba-playtest/scenarios/boot.json \
  --expected tools/gba-playtest/fingerprints/boot.json

# Explicit behavior comparison: report the candidate ROM identity, while an
# older baseline identity is shown when the expected file retains one.
python3 tools/gba-playtest/gba_playtest.py verify \
  --policy behavior \
  --rom candidate.gba \
  --scenario tools/gba-playtest/scenarios/boot.json \
  --expected tools/gba-playtest/fingerprints/boot.json
```

Exit statuses are 0 for success, 1 for a valid-but-different fingerprint, and 2
for malformed input, missing dependencies, or backend/setup failure. Verify
diagnostics identify the exact JSON path, expected value, and captured value.

### Baseline refresh policy

A checked-in fingerprint (`--expected`) is a reviewed oracle, never output
`verify` can write, rewrite, or otherwise refresh itself -- there is no
`verify --write-baseline`-style flag anywhere in this tool, on either a
passing or a failing comparison (see
`tools/gba-playtest/tests/test_baseline_no_autorefresh.py`). Refreshing a
baseline is exclusively a human-run, explicit `capture -o <path>` invocation
followed by the ordinary reviewed-commit process for that changed file --
matching `docs/issue-resolution-policy.md`'s "Baseline and fingerprint
review": a mismatch is a signal to investigate the behavior change, not to
silently regenerate the fingerprint. Before committing a refreshed
fingerprint, record in the commit/PR: which checkpoints changed, the old vs.
new hash/probe values, and the semantic reason (the "fingerprint capture 必须
显式" contract for this harness).

### Verification policy

`verify` defaults to `--policy exact-rom`. This is the safe regression policy:
ROM SHA-1, size, title, game code, scenario identity, hashes, and probes must all
match. Accidentally testing the wrong ROM is therefore a hard mismatch.

`--policy behavior` is the explicit baseline-vs-candidate migration mode. It
compares scenario/checkpoint behavior while intentionally allowing ROM
provenance to differ. Its expected baseline may omit the otherwise-unused
`"rom"` object; diagnostics label that omitted baseline identity and always
print the captured candidate identity. Use it only when changed ROM bytes are
expected; it never silently turns off capture identity reporting. Capture JSON
always contains provenance under `"rom"` regardless of the later verification
policy. Fixed-frame schema version 1 uses fingerprint format version 2;
bounded run-until schema version 2 uses fingerprint format version 3.
Exact-ROM expected fingerprints in either format require valid provenance.

## Host-only test mode

`GBA_PLAYTEST_HOST_ONLY=1` is the single public switch that makes this test
suite host-only. It is the one thing that decides whether the ROM-dependent
tests run; the presence, absence, freshness or content of a build artifact
never does.

```sh
# Exactly what CI `host-tests` (and the first gate of
# `scripts/upstream_port/verify.py`) runs: fast, deterministic and
# artifact-independent.
GBA_PLAYTEST_HOST_ONLY=1 python3 -m unittest discover -s tools/gba-playtest/tests -v
```

* **Host-only mode**: every Category B (live-integration) test raises an
  explicit `unittest.SkipTest` *before* it opens, stats, hashes or hands any
  ROM/ELF/save artifact to libmGBA -- even when the debug, release and legacy
  ROMs all exist, and even when one appears mid-run because a build is
  running concurrently. Category A tests (scenario/schema parsing,
  generators, config, save/migration fixtures, timeouts, retry policy,
  deterministic sorted-JSON output, provenance/diagnostics, the host-compiled
  debugtools drivers and the homebrew libmGBA backend integration) all keep
  running -- this is not a blanket suite-level skip.
* **Normal mode** (variable unset, or `0`/`false`/`no`/`off`): unchanged
  behavior. If a ROM has been built, its live scenarios still run against it;
  if it has not, they skip exactly as before. Local runtime debugging is
  therefore unaffected.
* An unrecognized value (for example `GBA_PLAYTEST_HOST_ONLY=maybe`) is
  refused with an actionable error instead of silently falling back to
  running live integration.

Why: `build/` is git-ignored and user-owned, so gating live runs on
"does the ROM file exist" made the *host* lane a function of local artifact
timing -- a clean checkout skipped, while a worktree holding a stale or
concurrently rebuilding ROM ran live captures against it and reported
fingerprint failures that say nothing about the commit under test (worst
under `python3 -m scripts.upstream_port verify --jobs 2`, whose later gates
rewrite exactly those artifacts). Live/runtime coverage is owned by the ROM
gates -- `make expansion-modern-linker-check` and
`make expansion-modern-itemexpansion-check` (build.yml `build` job; the last
four of the ten `verify` gates) -- which build the ROM they then boot, so
nothing is lost by removing the opportunistic host-lane runs. No fingerprint
refresh and no `clean` is involved either way.

The classification of every test module, the registry of live TestCase
classes and the enforcement tests live in
`tools/gba-playtest/tests/host_mode.py` and
`tools/gba-playtest/tests/test_host_only_mode.py`: a new live test must build
its ROM path through `host_mode` and register its class, or the host-only
regression fails.

## Scenario format

A scenario is one strict JSON object. Unknown fields, duplicate JSON keys,
overlapping/out-of-order frame ranges, duplicate checkpoints/probes, malformed
expectations, and invalid key/address names are errors.

### Fixed-frame schema version 1

```json
{
  "schema_version": 1,
  "name": "example",
  "description": "Optional human context.",
  "frames": [
    {"start": 90, "end": 95, "keys": ["A", "RIGHT"]},
    {"start": 150, "end": 155, "keys": ["START"]}
  ],
  "checkpoints": [
    {
      "name": "after-input",
      "frame": 180,
      "framebuffer": true,
      "expected_framebuffer_hash": "fnv1a64-rgb24:0123456789abcdef",
      "probes": []
    }
  ]
}
```

Frame ranges are inclusive. `keys` is the complete held-key state for that
range: keys are pressed at `start` and released after `end`; gaps mean all keys
released. Valid names are `A`, `B`, `SELECT`, `START`, `RIGHT`, `LEFT`, `UP`,
`DOWN`, `R`, and `L`. For frame N, input is applied, one frame runs, and then a
checkpoint at N is captured.

Probes may be 1, 2, or 4 aligned bytes in EWRAM
(`0x02000000`-`0x0203ffff`) or IWRAM
(`0x03000000`-`0x03007fff`). Address strings may have eight hex digits or use
a bounded ELF binding such as `gExpansionLanguageMenuProbe+0x04`. Symbolic
probes require `--elf` for capture/verify execution; `--nm` selects the exact
tool explicitly, while `MODERN_NM` and `MODERN_TOOLCHAIN_ROOT` are the
configured fallbacks. The backend plan receives the resolved RAM address, but
captured fingerprints retain the symbolic expression. A relink therefore
changes execution addresses without rewriting scenario or fingerprint
semantics.
Optional probe values are lowercase, fixed-width little-endian integer
renderings. Optional inline expectations make capture fail immediately; normal
regression verification uses a separate checked-in fingerprint.
Only probe documented semantic state whose address and meaning remain stable
under the intended compiler/linker migration. Arbitrary region-base words,
allocator scratch, and relocated pointers are not valid behavioral oracles.
The boot/title scenarios intentionally use framebuffer-only checkpoints. The
source-generated integration fixture uses `0x02000000` only because its own
documented program explicitly mirrors KEYINPUT there.

Disabled schema-ready stubs additionally use `"disabled": true` and a non-empty
`"blocker"`. They may have no checkpoints, and capture rejects them explicitly.

Schema version 1 and its format-version-2 fingerprints are unchanged. Existing
fixed-frame files require no migration and still generate the same plan and
fingerprint structure.

### Bounded semantic run-until schema version 2

Schema version 2 replaces the fixed `checkpoints` array with one bounded
`run_until` profile and one terminal checkpoint template:

```json
{
  "schema_version": 2,
  "name": "bounded-example",
  "frames": [
    {"start": 90, "end": 95, "keys": ["SELECT", "START", "R"]}
  ],
  "run_until": {
    "max_frames": 18001,
    "terminal_conditions": [
      {
        "reason": "success",
        "all": [
          {
            "address": "gExpansionAutoplayTelemetry+0x04",
            "size": 4,
            "operator": "eq",
            "value": "0x00000003"
          }
        ]
      },
      {
        "reason": "objective_failure",
        "all": [
          {
            "address": "gExpansionAutoplayTelemetry+0x04",
            "size": 4,
            "operator": "eq",
            "value": "0x00000004"
          }
        ]
      }
    ],
    "stall": {
      "max_unchanged_frames": 1800,
      "progress": {
        "address": "gExpansionAutoplayTelemetry+0x18",
        "size": 4
      },
      "work_expected": {
        "address": "gExpansionAutoplayTelemetry+0x04",
        "size": 4,
        "operator": "eq",
        "value": "0x00000002"
      }
    },
    "turn_limit": {
      "maximum": 3,
      "address": "gPlaySt+0x10",
      "size": 2
    },
    "action_limit": {
      "maximum": 62,
      "address": "gExpansionAutoplayTelemetry+0x18",
      "size": 4
    },
    "checkpoint": {
      "name": "semantic-terminal",
      "framebuffer": false,
      "probes": [
        {"address": "gExpansionAutoplayTelemetry+0x18", "size": 4}
      ]
    }
  }
}
```

`max_frames` is an unconditional positive frame count. A value of N executes
at most zero-based frames 0 through N-1; every input range must end before N.
The terminal checkpoint deliberately has no authored `frame`: the backend
captures it exactly once on the first terminal frame.

Each terminal condition is a conjunction (`all`) of unsigned comparisons over
the same bounded 1/2/4-byte literal or ELF-symbol probes used by checkpoints.
Operators are `eq`, `ne`, `lt`, `le`, `gt`, and `ge`; values are lowercase
fixed-width hexadecimal strings. Exactly one `success` definition is required.
`objective_failure` and `controller_exhausted` definitions are optional.
Duplicate predicates/reasons, internally impossible conjunctions, or two
terminal definitions that can overlap are rejected before ROM execution.
The parser also rejects a success definition statically precluded by its
declared turn/action ceiling.

`turn_limit` and `action_limit` are optional named semantic counters. Their
positive `maximum` is checked after explicit terminal conditions, so success
observed exactly at a counter bound remains success. `stall` is optional and
requires a ROM-supplied monotonic progress epoch plus a separate
`work_expected` comparison. Only consecutive unchanged epoch transitions
while work is expected count toward `max_unchanged_frames`; a defend/wait
objective can report work not expected and remain stationary without being
called stalled. Any epoch regression is a deterministic backend error, never
normalized into a terminal result.

Terminal priority is explicit conditions, `engine_stall`, `max_turns`,
`max_actions`, then `max_frames`. Exactly one of these stable reasons is
emitted:

- `success` - the authored success state became observable;
- `objective_failure` - the ROM reported an authored loss/failure state;
- `controller_exhausted` - explicit ROM telemetry reported no legal action;
- `engine_stall` - the monotonic progress epoch stopped while work was expected;
- `max_frames`, `max_turns`, or `max_actions` - the corresponding hard budget
  was reached first.

Run-until captures use fingerprint format version 3. They retain normal
scenario and ROM provenance, contain exactly one checkpoint, and add
`terminal.reason`, `terminal.frame`, plus probe-shaped `terminal.turn` and
`terminal.actions` values (or `null` when the counter is unbound). The
checkpoint frame must equal the terminal frame. Behavior-policy verification
compares this typed terminal record as well as checkpoint data.

All seven reasons are deterministic semantic outcomes, not host process
timeouts. They return one fingerprint and are never retried. `--retries`
continues to apply only to a transient compiler/pkg-config/backend process
timeout; it cannot turn an objective failure, exhausted controller, stall, or
budget result into success.

### Accelerated-fidelity schema version 3

Issue #88 adds a strict `execution_profile` to bounded run-until scenarios:
`normal-fidelity` or `accelerated-fidelity`. Schema-v1/v2 inputs and
fingerprint formats remain unchanged. Both profiles execute every selected
frame through the same `core->runFrame()` path; no profile can skip engine,
Proc, event, battle, movement, camera, trap, phase, controller, or save logic.

The accelerated profile has one explicit `play_state_config` binding and
`config_apply_frame`. At that frame, only the existing `gPlaySt.config`
game-speed bit and the animation option selected by
`BANIM_PRESENTATION_POLICY_OFF` are applied inside the disposable libmGBA
core. Normal fidelity accepts no configuration write. The profile also owns a
non-empty `trace` array of normal 1/2/4-byte semantic probes, canonicalized by
binding and size so input order cannot change the fingerprint shape. The
backend samples it each emulated frame and emits a full snapshot only on a
semantic change, retaining action/RNG order without making host wall-clock time
a behavioral oracle. Event commands append the first observed state and every
later bounded ordered command/slot-C/counter/chapter-flag/permanent-flag
transition at their commit seam; the terminal
checkpoint compares every record and rejects overflow. The endpoint covers
active blue, red, and green unit slots. Its declared frame bound multiplied by
trace-probe count may not exceed 450,000 records, bounding backend stdout and
the host's captured trace memory.

Schema-v3 uses plan format 5 and fingerprint format 4. Format 5 retains
framebuffer allocation/rendering but adds one checkpoint flag: when a
semantic-only checkpoint has `framebuffer: false`, it does not calculate or
emit an unused whole-frame hash. Region/pixel capture still requires
`framebuffer: true`. The format-4 fingerprint records profile application
details and the ordered trace, while preserving normal ROM provenance,
terminal reason/counters, and terminal semantic checkpoint.

The focused reproducible command is:

```sh
make expansion-modern-autoplay-accelerated-fidelity-check \
  MODERN_CONFIG=debug MODERN_ABI=aapcs
```

It writes an ignored benchmark JSON beside the debug ROM. That report records
libmGBA version, host/runner identity, source commit, ROM provenance/config,
emulated-frame counts, and three wall-clock samples. The checked Chapter 2
fixture freezes 17,135 normal-fidelity frames and 16,869 accelerated-fidelity
frames (266 fewer); wall-clock samples are evidence only. The paired
comparator requires equal ROM provenance, terminal semantic state, ordered
trace, RNG values, unit state/items, flags/objective result, and turn/action
counters, and its perturbed-trace negative must fail. Repeated captures of the
same profile compare complete format-4 fingerprints, including terminal and
trace frames; format-4 validation rejects profile or trace frames after the
terminal. The accelerated runtime also verifies that the existing presentation
policy seam reports `BANIM_PRESENTATION_POLICY_OFF`, not merely matching config
bits. Visual/audio/timing cases stay on their normal-fidelity scenarios.

## Initial coverage and limits

`boot.json` is a no-input early boot capture. `title-progression.json` uses the
same deterministic six-frame A/START tap concept as
`scripts/shiftcheck/mgba_oracle.c`, with attribution retained in `backend.c`.
Modern GCC uses configuration-specific
`title-progression-modern-{debug,release}.json` fingerprints because
optimization changes later title-animation timing. Shifted links must match
the baseline for their own configuration. The legacy fingerprints were
captured with libmGBA 0.10.2 from the baseline ROM whose project checksum is
`c25b145e37456171ada4b0d440bf88a19f4d509f`; the modern title fingerprints
record the debug/release ROM provenance directly. An emulator-version change
that alters rendered pixels is intentionally reported as a fingerprint
difference and should be reviewed rather than normalized away.
`tests/homebrew_fixture.py` generates a tiny original homebrew ROM in a
temporary directory and drives released/A-held/released frames, pixels, and a
semantic RAM value through capture and both verification policies. Only
generator source is committed.

### Deterministic runtime scenario coverage (issue #13)

Every scenario below is reached from a clean boot with no committed
savestate/save file; every non-framebuffer claim of "arrival" is proven by a
semantic probe (an EWRAM/SRAM field, or a whole-SRAM hash) derived from this
build's own symbol table or a documented struct offset, never framebuffer
similarity alone.

| Scenario | Proves | Config(s) |
| --- | --- | --- |
| `boot.json` | Early boot progress | debug + release (behavior policy) |
| `title-progression.json` | Title/intro/menu progression | debug + release |
| `new-game.json` | Ordinary Save-Menu New Game creation: New Game -> Easy -> first empty slot, with a before/after whole-SRAM hash proving the real `SaveMenuWriteNewGame`/`WriteGameSave`-class write happened, and `gPlaySt.chapterIndex`/`faction` probes confirming the created game begins at the Prologue (`CHAPTER_L_PROLOGUE`, player phase) | debug + release |
| `debugtools-hub-modern-{debug,release}.json` (issue #11) | The debug-only "Fast Boot: Chapter 2" launcher's deterministic clean-boot chapter/map **arrival**: reaches an interactive first stable Player Phase on the real Chapter 2 map, proven via relocation-independent cursor/phase/proc-state/hub-count semantic probes plus per-slot `struct Unit.state` fields and a stable fixture-seeded whole-SRAM hash (no unit `pCharacterData` ROM pointer is asserted as an oracle); release mirror proves the hub/launcher are compiled out and inert | debug (live) / release (negative) |
| `debugtools-map-hub-modern-{debug,release}.json` (issue #11) | The map-phase debug hub stays reachable and leaves the real, interactive Chapter 2 map genuinely interactive afterward | debug (live) / release (negative) |
| `debugtools-{prep,timer,ch4-prep-launch}-*` (issue #11) | Additional debug-tool launcher/hotkey/diagnostics behavior and their release-inert negatives -- see `docs/debugtools.md` | debug + release, per file |
| `savecompat-current.json` / `savecompat-dialog-back.json` / `savecompat-erase.json` | Save-compatibility classification, non-destructive Back, and confirmed Erase across all `SaveCompatState` values | debug + release |
| `savesuspend-resume-modern-debug.json` | Full write -> soft-reset -> reload round trip: an ordinary Map Menu **Suspend**, a real soft-reset key combo, and **Resume** through `ReadSuspendSave()`, with `gPlaySt.chapterIndex`/`faction`/cursor and a unit-item probe proving the exact manually-saved state (not the earlier auto-save) was restored | debug only (depends on the debug-only Chapter 2 launcher) |
| `combat.json` (issue #13) | The chapter's own scripted `FIGHT` in Chapter 4 resolved by the REAL battle engine (`Event3F_ScriptBattle`, `EV_CMD_SCRIPT_BATTLE`): the target enemy `gUnitArrayRed[0]` is alive at full HP (`maxHP` `0x0202eba6` and `curHP` `0x0202eba7` both `15`), `curHP` transitions `15 -> 0` at the resolving SCRIPT_BATTLE frame while `maxHP` stays `15`, then `pCharacterData` (`0x0202eb94`) is cleared to a null `0x00000000` (death) -- relocation-independent semantic HP scalars and a null-field marker, never a nonzero pointer, framebuffer, or timing | debug only (debug-only Fast Boot launcher) |
| `save-load.json` (issue #13) | Normal (non-Suspend) game-save write + load: SaveMenu New Game -> slot 0 write, a real A+B+SELECT+START soft reset, then SaveMenu RESTART -> `PostSaveMenuHandler` -> `ReadGameSave(0)`; `playthroughIdentifier` (`0x020210bc`)/`chapterModeIndex` (`0x020210bf`) go `1 -> 0 -> 1`, `gameSaveSlot` (`0x020210b0`) `== 0`, and before/after whole-SRAM hashes differ | debug only (debug-calibrated soft-reset) |
| `debugtools-ch4-prep-positive-modern-debug.json` (issue #11) | Live prep-screen arrival + SELECT+B prep hotkey: rests `gProcScr_SALLYCURSOR` in `PrepScreenProc_MapIdle` and fires the hotkey; `prepScreenObservedCount` (`0x02031854`) `0 -> 1` (reachable only from MapIdle, so it is the relocation-independent proof the hotkey fired live), `PLAY_FLAG_PREPSCREEN` held throughout, idempotent 2nd press, safe return to prep -- no proc ROM-pointer oracle | debug only (debug-only launcher + hotkey) |
| `debugtools-tools-modern-{debug,release}.json` (issues #11/#125) | The five shipped bounded tools driven **live** from the real Chapter 2 map hub. Issue #125 adds cursor slot/character/class inspection, a read-only HP preview, exact confirmed HP `17 -> 16`, heal `16 -> 17`, typed empty-tile rejection, matching before/after SRAM hashes, and post-cleanup cursor movement. Existing Convoy `0 -> 1`, Flag `0 -> 1`, RNG reseed, and read-only Save assertions remain. Symbol-bound semantic probes only; release replays identical input with the established probe zero while editor code/state/probe symbols are omitted | debug (live) / release (negative) |
| `run_autoplay_checks.py` generated scenarios (issue #85) | `TC-AUTOPLAY-001`: a clean Chapter 2 debug-only activation chord drives a full blue phase through the existing AI and records legal actions, faction-relation checks, completion, and progression; clean Prologue debug/release defaults remain PLAYER with zero blue AI actions | debug (positive + negative) / release (negative) |
| `run_autoplay_bounds_checks.py` generated scenarios (issue #86) | `TC-AUTOPLAY-BOUNDS-001`: the same debug COMPUTER route stops at its first semantic completion (frame 17134 in the checked candidate), while clean debug/release PLAYER controls reach `max_frames` at frame 3950 with zero actions; the generated homebrew fixture separately covers all seven terminal reasons | debug (positive + negative) / release (negative) |
| `run_accelerated_fidelity_checks.py` generated paired scenarios (issue #88) | `TC-AUTOPLAY-ACCEL-001`: same-ROM normal/accelerated Chapter 2 profiles preserve terminal probes, ordered telemetry/RNG trace, active-unit state/items, objective result, and turn/action counts while reducing 17,135 frames to 16,869; the benchmark reports non-gating wall-clock samples and the perturbation control rejects divergence | debug only |

New-game, chapter/map arrival, combat, and normal save/load are all enabled,
verified scenarios -- see the coverage table above. **No `*.stub.json` files
remain in the repository** (the `scenarios/stubs/` directory is gone):
`combat.json` and `save-load.json` replaced the former combat/save stubs, and
`debugtools-ch4-prep-positive-modern-debug.json` proves the live prep-screen
arrival. `tools/gba-playtest/tests/test_stub_scenarios.py` now asserts that no
stub scenarios remain and that `combat.json`/`save-load.json` are enabled with
semantic (non-framebuffer) checkpoints; no test treats a stub as success. A
savestate or save binary remains prohibited as a shortcut: every scenario is
reached from a clean boot and proves the intended state via a semantic probe
(an EWRAM/SRAM field or a whole-SRAM hash), never framebuffer/timing
similarity alone.

### Supported CI host matrix

CI (`.github/workflows/build.yml`) runs on `ubuntu-latest` only, in two
separate jobs: a fast `host-tests` lane (this test suite only, no
`arm-none-eabi` toolchain) and a `build` lane (the full modern ROM/linker
gate, both `debug` and `release`, `MODERN_ABI=aapcs` only -- see
`docs/config_identity.md`). macOS (Homebrew) is documented above for local
development but is not exercised by CI. The archival/decomp `agbcc`
`fireemblem8.gba` path (see `docs/issue-resolution-policy.md` "Supported
modern path vs. archival decomp path") continues to build locally, unrelated
to and unaffected by this harness, but it is not part of any CI gate here and
must never be read as a substitute runtime gate for the modern path.

## Tester-facing procedure

[`TC-CORE-007`](../../docs/test-cases/core-framework.md#tc-core-007-runtime-harness-detects-mismatch)
records the clean behavior-policy capture/verify procedure, host-only switch,
deliberate mismatch control, and no-auto-refresh boundary.
