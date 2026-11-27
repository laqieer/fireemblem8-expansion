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

## TC-PORTRAIT-GENERATED-MOUTH-001: Exercise generated mouth slots

- **Feature / originating issue:** `formatted-portrait-package` /
  [issue #499](https://github.com/laqieer/fireemblem8-expansion/issues/499).
- **Supported configuration or artifact:** modern source checkout with Python 3
  and a host C compiler; the ARM fixture additionally needs the supported
  ARM toolchain and libmGBA. No saved state is needed.
- **Prerequisites and clean starting state:** run from an isolated checkout
  with no other test using `build/generated/assets/test-work`.

### Actions

1. Run `python3 -m unittest scripts.assets.tests.test_manifest.AssetManifestTests.test_generated_portrait_mouth_frames_follow_native_consumer`.
2. Observe the generated synthetic mouth and the real face consumer invoked by
   the host driver for stopped and talking states, both smile modes and all
   animation phases.
3. Run `python3 -m unittest tools.gba-playtest.tests.test_portrait_generated_mouth_runtime`
   to build and boot the disposable ARM fixture in libmGBA.

### Expected result

Each 4x2 tile transfer is within the generated component and contains the
authored closed frame when stopped or closed, and the authored open frame
otherwise. The existing Eirika component alias generates no replacement data.
On ARM, the generated `FaceData` table selects the generated component via
`GetPortraitData`; the real face consumer and `Register2dChrMove` copy both
tile rows to VRAM in every state without bounds or content errors.

### Negative control

At the pre-fix generator, the stopped non-smiling transfer attempts bytes
1280 through 1535 of a 512-byte component and the host driver fails.
The ARM fixture also runs that exact 512-byte frame layout and observes
out-of-bounds transfers rather than accepting stale VRAM.

### Interactions and save compatibility

The package depends on the existing asset manifest and native face renderer.
It has no known profile conflicts and changes no save, configuration identity,
localization IDs or legacy aliases. The generated component grows by 1024
bytes per generated portrait; no persistent RAM is allocated.

### Automation

The named unittest generates distinct synthetic frames and links the actual
`FaceMouth_Loop` into a host driver that checks each transfer's bounds and
bytes. The ARM unittest links the generated table and component with the real
consumer and tile-move function, boots a disposable libmGBA ROM and checks
the VRAM bytes and both positive and negative controls. Its transfer-queue
stub copies immediately, so it does not replace a full-game dialogue scenario
or visual acceptance of a project's artwork.

### Cleanup and limitations

The host unittest removes its disposable test-work directory; the ARM unittest
removes its temporary ROM and build files. Neither fixture proves full-game
dialogue scheduling, on-screen rendering, or expression quality. Archival
byte identity is not claimed.
