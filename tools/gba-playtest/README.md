# GBA headless playtest fingerprints

`gba_playtest.py` replays a strict JSON input scenario through libmGBA, captures
named framebuffer/RAM checkpoints, and emits deterministic JSON. It is intended
to compare behavior when ROM bytes or link addresses legitimately change.
Every capture binds the behavior to machine-readable ROM provenance: SHA-1,
byte size, header title, and header game code. The backend executes the same
immutable temporary ROM copy that is hashed, avoiding a hash/load path race.

The tool does not load save files, screenshots, or savestates. Framebuffer hashes
are FNV-1a-64 over canonical 24-bit RGB bytes (alpha/padding and host endianness
are excluded). The small C backend is compiled into a temporary directory for
each command, so no emulator-generated binary is left in the tree.
Compiler, pkg-config, and emulator processes all have bounded timeouts with
actionable diagnostics.

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

# Capture sorted, reproducible JSON
python3 tools/gba-playtest/gba_playtest.py capture \
  --rom fireemblem8.gba \
  --scenario tools/gba-playtest/scenarios/boot.json \
  --output /tmp/boot.json

# Replay and compare every checkpoint/hash/probe
python3 tools/gba-playtest/gba_playtest.py verify \
  --rom fireemblem8.gba \
  --scenario tools/gba-playtest/scenarios/boot.json \
  --expected tools/gba-playtest/fingerprints/boot.json

# Explicit migration comparison: report both ROM identities but compare behavior
python3 tools/gba-playtest/gba_playtest.py verify \
  --policy behavior \
  --rom candidate.gba \
  --scenario tools/gba-playtest/scenarios/boot.json \
  --expected tools/gba-playtest/fingerprints/boot.json
```

Exit statuses are 0 for success, 1 for a valid-but-different fingerprint, and 2
for malformed input, missing dependencies, or backend/setup failure. Verify
diagnostics identify the exact JSON path, expected value, and captured value.

### Verification policy

`verify` defaults to `--policy exact-rom`. This is the safe regression policy:
ROM SHA-1, size, title, game code, scenario identity, hashes, and probes must all
match. Accidentally testing the wrong ROM is therefore a hard mismatch.

`--policy behavior` is the explicit baseline-vs-candidate migration mode. It
compares scenario/checkpoint behavior while intentionally allowing ROM
provenance to differ, and always prints both complete identities. Use it only
when changed ROM bytes are expected; it never silently turns off identity
reporting. Capture JSON always contains provenance under `"rom"` regardless of
the later verification policy. Scenario schema version remains 1; provenance is
mandatory in fingerprint format version 2.

## Scenario format

A scenario is one strict JSON object. Unknown fields, duplicate JSON keys,
overlapping/out-of-order frame ranges, duplicate checkpoints/probes, malformed
expectations, and invalid key/address names are errors.

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
(`0x03000000`-`0x03007fff`). Address strings always have eight hex digits.
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

## Initial coverage and limits

`boot.json` is a no-input early boot capture. `title-progression.json` uses the
same deterministic six-frame A/START tap concept as
`scripts/shiftcheck/mgba_oracle.c`, with attribution retained in `backend.c`.
These scenarios cover boot and title/intro/menu progression only. Modern GCC
uses configuration-specific `title-progression-modern-{debug,release}.json`
fingerprints because optimization changes later title-animation timing.
Shifted links must match the baseline for their own configuration.
The legacy fingerprints were captured with libmGBA 0.10.2 from the baseline
ROM whose project checksum is `c25b145e37456171ada4b0d440bf88a19f4d509f`;
the modern title fingerprints record the debug/release ROM provenance directly.
An emulator-version change that alters rendered pixels is intentionally reported
as a fingerprint difference and should be reviewed rather than normalized away.
`tests/homebrew_fixture.py` generates a tiny original homebrew ROM in a temporary
directory and drives released/A-held/released frames, pixels, and a semantic RAM
value through capture and both verification policies. Only generator source is
committed.

The checked-in new-game, chapter, combat, and save stubs are intentionally
disabled. The generic shiftcheck input does not prove arrival in those states.
The deeper shiftcheck material is a GBAHawk full-game movie that is external,
requires a different emulator/BIOS, and is not distributed here. A savestate or
save binary would violate this harness's clean-boot evidence and repository
constraints. Enabling a stub therefore requires a reviewed per-frame clean-boot
route and a checkpoint that independently proves the intended state; no deeper
coverage is claimed today.
