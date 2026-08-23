# Asset-authoring cases

These procedures cover shipped source-asset adapters that generate through
the existing manifest and runtime ownership seams. They require no additional
downstream or project content and use the already committed project-owned
`LORM_SP1_PROOF` fixture rather than introducing a second asset registry.

## TC-BANIM-PACKAGE-062: Generate and exercise a community battle-animation package

- **Feature / originating issue:** `battle-animation-package` /
  [issue #62](https://github.com/laqieer/fireemblem8-expansion/issues/62).
- **Supported configuration or artifact:** clean modern source checkout with
  Python 3, the supported ARM toolchain, libmGBA, and the committed
  project-owned `LORM_SP1_PROOF` package; the runtime proof uses an isolated
  modern AAPCS debug ROM with `FE8_BANIM_PACKAGE_RUNTIME_TEST=1`.
- **Prerequisites and clean starting state:** start at the repository root
  with no hand-edited generated output. Ordinary debug/release builds must not
  define the test-only runtime macro.

### Actions

1. Run `make assets-validate assets-generate assets-check`.
2. Run `python3 -m unittest scripts.assets.tests.test_manifest -v`.
3. Run
   `python3 -m unittest tools.gba-playtest.tests.test_banim_package_runtime -v`.
4. Run `make expansion-modern-banim-package-runtime-check`.
5. As a supplementary visual check only, enter an Ephraim Lord lance battle
   in an ordinary modern debug ROM and compare standing, attack, critical,
   ranged, and dodge presentation with the default build.

### Expected result

The host checks validate the versioned text/PNG package, class/linker binding,
and generated 4bpp, palette, left/right OAM, mode, motion, sound-opcode, and
compressor-linker payloads. The isolated libmGBA route enters Chapter 4's real
scripted `FIGHT`, selects `LORM_SP1_PROOF` once after ordinary resolution,
asserts the actual `CLASS_MOGALL` ID `0x5F`, five modes, normal and total
timing, the generated sound opcode, and decompressed OAM/palette payloads,
then records one selection, generated-data consumption, battle entry, and
completion.

### Negative control

Before `FIGHT`, every test-only runtime probe is zero. Ordinary builds omit
the macro and retain `CLASS_MOGALL -> AnimConf_90` and
`CLASS_EPHRAIM_LORD -> AnimConf_0`. Host fixtures reject unsupported commands
or sounds, malformed modes or PNGs, invalid resources, duplicate ownership,
unsafe paths, and missing, stale, orphaned, or non-atomic output.

### Interactions and save compatibility

The adapter depends on the sole asset manifest, existing `BattleAnimDef`,
`banim_data[]`, class generator, compressor linker, issue #67 publication
lock, and libmGBA harness. It conflicts with manual ownership of those
generated seams and with custom spell/runtime formats. It changes no save
bytes, configuration identity, localization data, default class mapping, or
engine ABI. Modern debug/release are supported; archival remains only a
compile/link boundary.

### Automation

- `make assets-validate assets-generate assets-check` validates and
  round-trips the committed package through `scripts/assets/manifest.py`.
- `python3 -m unittest scripts.assets.tests.test_manifest -v` exercises the
  positive package and fail-closed host fixtures.
- `python3 -m unittest tools.gba-playtest.tests.test_banim_package_runtime -v`
  checks that the runtime probe is confined to the scripted lifecycle.
- `make expansion-modern-banim-package-runtime-check` builds and executes the
  isolated libmGBA positive/default-control route through
  `tools/gba-playtest/run_banim_package_runtime_check.py`.

The local visual comparison is supplementary composition/palette evidence
only. Screenshots and subjective appearance never replace the host and
libmGBA assertions.

### Cleanup and limitations

Run `make assets-clean` to remove generated asset products and
`make clean_fast` to remove modern runtime outputs if desired. The runtime
target recreates only its named `build/banim-package-runtime/` root. Rolling
back a downstream package removes its manifest/package sources and disposable
outputs together. V1 does not support custom spells, unknown commands,
runtime switching, or opaque editor binaries.
