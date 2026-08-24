# Debugtools tester cases

## TC-DEBUGTOOLS-DIAGNOSTICS-001: Typed State and Engine diagnostics

- **Feature / originating issue:** `debugtools-visual-status-diagnostics` /
  [issue #127](https://github.com/laqieer/fireemblem8-expansion/issues/127).
- **Supported configuration or artifact:** modern AAPCS debug source build
  with the existing debugtools gate; the identical release profile is the
  disabled control. Human steps use English; automation also covers the
  pseudo-locale geometry.
- **Prerequisites and clean starting state:** start from a clean title boot
  with the deterministic debugtools SRAM fixture and no external prototype or
  debug-display patch. Record the initial whole-SRAM hash. Reset to the same
  fixture between title, Chapter 2 map, and Chapter 4 prep legs.

### Actions

1. At title idle, press SELECT+R. Press R past the registered action page(s)
   to State and Engine. Select Refresh once, then Back.
2. Use Fast Boot: Chapter 2. On the interactive map, place the cursor on the
   known fixture unit, press SELECT+L, enter State, then close the session.
   Move to an empty in-bounds tile and repeat.
3. Use the deterministic Chapter 4 prep route. At live prep MapIdle, press
   SELECT+B and enter State.
4. In the map leg, open diagnostics and invoke the test-only forced owner
   teardown. Move the map cursor once after teardown.
5. Compare the final whole-SRAM hash with the initial hash.

### Expected result

Title exposes TITLE plus clock/RNG/proc/action/log/assert scalars and marks
map/cursor/unit rows unavailable. Chapter 2 exposes exact chapter, turn,
blue phase, tile coordinates, validated unit/character/class/HP,
weather/fog, and RNG values. The empty tile retains CURSOR validity but clears
UNIT and zeroes all unavailable unit fields. Prep reports PREP and the same
validated map semantics.

Refresh increments capture sequence exactly once. Forced teardown increments
one restoration counter, leaves `restorationMismatchMask == 0`, restores the
font counter and lock baseline, and returns to an interactive cursor. SRAM is
byte-identical.

### Negative control

Host cases cover NULL output, out-of-bounds/stale/empty units, active event,
battle ownership, repeated capture cadence, and forced teardown. Battle has no
hotkey or view. The release build exposes only the disabled provider stub,
omits internal view/owner/backup symbols, keeps all debug probes zero, and
continues semantic world-map progress under the same input.

### Interactions and save compatibility

This depends on issue #11's hub, transition, structured log and assert
foundation. It uses no action ID, so it is independent of prototype follow-up
issues #123 through #126. A future cursor-unit inspector may reuse the same
validated unit helpers. Active event/fade/map-animation/battle or another
display owner fails closed. No save field, epoch, migration, preference,
configuration identity, or generated gameplay data changes.

### Automation

```sh
python3 -m unittest tools.gba-playtest.tests.test_debugtools_diagnostics -v
python3 -m unittest tools.gba-playtest.tests.test_debugtools_registry -v
make expansion-modern-debugtools-diagnostics-check MODERN_CONFIG=debug MODERN_ABI=aapcs
make expansion-modern-debugtools-diagnostics-check MODERN_CONFIG=release MODERN_ABI=aapcs
```

The host driver executes the real provider and owner against sentinel-filled
BG maps. The libmGBA scenarios assert context, validity mask, capture/view and
restore counters, exact engine scalars, a post-close
`PlayerPhase_MainIdle` counter with zero game lock, and release inertness. No
framebuffer, screenshot, pointer, or subjective visual criterion applies.

### Cleanup and limitations

Close or force-end the session and reset the disposable fixture. Unsupported
surfaces are battle/event/fade/map animation, live non-modal overlay,
monochrome mode, the normal stat screen, VRAM/OAM estimation, arbitrary field
browsing, and performance profiling.
