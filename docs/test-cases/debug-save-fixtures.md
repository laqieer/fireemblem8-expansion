# Debug save-fixture tester cases

## TC-DEBUGSAVE-001: volatile save-fixture isolation and recovery

**Purpose and issue:** Verify issue
[#128](https://github.com/laqieer/fireemblem8-expansion/issues/128):
a validated latest suspend can be cloned into the volatile RAM fixture,
temporarily changed, continued once, and reset without changing any ordinary
game, suspend, preference, global, auxiliary, metadata, or XMAP byte.

**Supported profile:** Source-built modern AAPCS debug, English, 16 MiB:

```bash
make expansion-modern-debug-save-fixture-check \
  MODERN_CONFIG=debug MODERN_ABI=aapcs
```

The release negative is:

```bash
make expansion-modern-debug-save-fixture-check \
  MODERN_CONFIG=release MODERN_ABI=aapcs
```

**Prerequisites and clean state:**

1. ARM toolchain and libmGBA from the documented quickstart.
2. No real user save. The gate generates an exact `0x8000`-byte CURRENT image
   under ignored build output and loads it through mGBA
   `temporary=true`.
3. The source contains valid game 0 and latest alternate suspend blocks,
   source tactician bytes `USER`, source completion count 1, chapter 2, and
   cursor `(9,4)`. Its generated suspend roster contains Eirika at `(8,4)`
   and one enemy at `(5,5)`, keeping the resumed map interactive without
   importing an external save image.
4. Record the source image as baseline `U[0x0000..0x7FFF]` after the normal
   boot SRAM probe.

**Positive actions:**

1. Advance to title and press `SELECT+R`.
2. Move to **Save State**, select **Suspend**.
3. Confirm preview shows RAM target, resolved suspend 4, backing game 0,
   CURRENT format/epoch, completion 3, and fixture tactician mode.
4. Select **Arm RAM**.
5. On the separate final screen move from default **Back** to **Run RAM**,
   then press fresh `L+R+A`.
6. Wait for resumed chapter state and the automatic phase-start suspend
   attempt.
7. Open the ordinary in-game Map Menu, move down four rows to **Suspend**,
   and press **A**. Confirm the disabled command refuses the manual suspend
   attempt, then close its help box and the menu with **B**.
8. Perform the ordinary soft-reset combo.

**Expected fixture bytes before consume:**

- `0x73A4..0x73A7`: `46 53 41 56` (`FSAV`);
- `0x73A8`: `02`;
- `0x73AA..0x73AB`: `02 00`;
- global completed bit set at `0x000E`;
- `0x0014..0x001F`:
  `01 02 03 00 00 00 00 00 00 00 00 00`;
- alternate suspend block info `0x00A4..0x00B3`: existing save magic,
  suspend kind, offset `0x204C`, size `0x1F78`, recomputed checksum;
- suspend name at `0x206C` and backing game-0 name at `0x3FE4`:
  `46 49 58 54 55 52 45 00 00 00 00`.
- first blue packed unit at `0x20D0`: PID/JID `01 02`, with HP bytes
  `10 10` at `0x20DE..0x20DF`;
- first red packed unit at `0x2B60`: PID/JID `47 41`, with HP bytes
  `14 14` at `0x2B6E..0x2B6F`.

**Expected observable result:**

- The existing game-control proc consumes one suspend target.
- Live chapter/cursor/tactician state is chapter 2, `(9,4)`, `FIXTURE`.
- Fixture completion queries return 3.
- Automatic and ordinary-UI manual suspend attempts are blocked and recorded;
  the manual attempt is the final recorded kind.
- At preview, Arm, active play, blocked write, and post-reset:
  `SRAM[0x0000..0x7FFF] == U[0x0000..0x7FFF]`.
- This exact comparison includes header/global, every block-info record, both
  suspend payloads, all game payloads, arena/rank/sound/bonus/probe bytes,
  expansion metadata/preferences, and XMAP.
- After reset, phase/token/guard are zero and the original source remains
  valid with `USER` and completion count 1.

**Negative controls:**

1. B from preview zeroizes the complete overlay and preserves exact SRAM.
2. CURRENT with no valid suspend returns target-invalid and preserves SRAM.
3. Current format with the wrong compatibility epoch returns
   source-not-current and preserves SRAM.
4. Soft reset before Arm clears preview/token and preserves SRAM.
5. Host real-code tests cover all seven non-CURRENT states, stale source
   between both confirmations, invalid backing game, wrong confirmation
   order, game target, consume failure, and direct cartridge-range blocking.
6. Map/prep preparation is absent; compatibility inspection remains
   read-only.
7. Release omits fixture/decoder symbols and repeated debug hotkeys are inert.
8. Archival save-reader code remains on its original preprocessor path.

**Interactions and save compatibility:** Reuses debugtools ID 9, the existing
classifier, title/game-control handoff, and current locale. No new feature
flag, registry, save byte, schema, checksum domain, format version, epoch, or
migration exists. External raw SRAM writers and competing overlay owners are
unsupported conflicts.

**Automation mapping:**

- `tools/gba-playtest/scenarios/debug-save-fixture-*.json`
- `tools/gba-playtest/run_debug_save_fixture_checks.py`
- `tools/gba-playtest/tests/test_debug_save_fixture.py`
- `tools/gba-playtest/tests/test_sram_fixture.py`
- `DebugSaveFixtureHostTests` in
  `tools/gba-playtest/tests/test_debugtools_registry.py`

All deterministic criteria are automated. There is no manual-only criterion.

**Cleanup and limitations:** Reset or return to title. Fixture data is
volatile, cannot be exported or saved, requires a valid CURRENT source, and
cannot be prepared from map, prep, blank, corrupt, older, newer, wrong-epoch,
or legacy data.
