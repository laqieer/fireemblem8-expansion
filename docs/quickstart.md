# Fire Emblem 8U Quick Start

Get a working build of this ROM-hack base with a single command using the
bundled `scripts/quickstart.sh` helper. If you prefer manual setup or run on
another distro/package manager, see the README.

## Prerequisites

- _(Optional)_ A legally obtained copy of **Fire Emblem: The Sacred Stones
  (USA)**. The build does **not** need it; it is only used by `asmdiff.sh` for
  disassembly comparison. If you have one, place it at the repo root as
  `baserom.gba`, or pass `--rom /path/to/rom.gba` (or
  `FIREEMBLEM8U_ROM=/path/to/rom.gba`).
- Ubuntu/WSL (apt), Arch Linux/pacman, or macOS/Homebrew with sudo/admin access. The script only auto-installs dependencies for these package managers; other environments can still run manually.
- ~2.5 GB of free disk space and up to 15 minutes for the first full build.

## One-command setup

From the repo root, run:

```bash
./scripts/quickstart.sh [--rom /path/to/baserom.gba] [--refresh-agbcc]
```

What the script now does:

1. Copies `baserom.gba` from the `--rom` path (or `FIREEMBLEM8U_ROM`) if you provided one. A missing ROM is fine — it is optional and not required to build.
2. Detects your package manager (`apt`, `pacman`, or `brew`) and installs the prerequisites only when they’re not already available:
   - Toolchain (`arm-none-eabi-binutils`, `arm-none-eabi-gcc`, and newlib headers; the official Arm cask on macOS)
   - `pkg-config` / `pkgconf`
   - `libpng`
   - `python3`, `pip3`, `numpy`, `pillow`
3. Checks whether `tools/agbcc/bin/agbcc` already exists. If it does, the script reuses it; otherwise it clones and builds [`pret/agbcc`](https://github.com/pret/agbcc) inside `.deps/agbcc` (ignored by git), installs it into `tools/agbcc`, and you can force a refresh any time with `--refresh-agbcc`.
4. Fetches submodules (`git submodule update --init --recursive`). The FE6 SIO link payload is built from source via the [mgfembp](https://github.com/StanHash/mgfembp) submodule rather than a committed blob.
5. Builds helper tools via `./build_tools.sh`.
6. Runs parallel `make` to produce `fireemblem8.gba`, using `nproc`,
   `sysctl -n hw.logicalcpu`, or 1 job in that order. The first build also
   fetches/builds mgfembp's own agbcc variant (`010110-ThumbPatch`) for the
   payload sub-build.
On success you’ll see:

```
[✓] Build complete: /path/to/fireemblem8-expansion/fireemblem8.gba
```

## Troubleshooting

- **No ROM** – `baserom.gba` is optional; the build works without it. Provide
  `--rom /path/to/rom.gba` (or `FIREEMBLEM8U_ROM=/path/to/rom.gba`) only if you
  want to use `asmdiff.sh`.
- **No sudo/root** – apt/pacman installs require elevated privileges. Without
  sudo the script stops and asks you to install the prerequisites manually.
  Homebrew installs keep working without sudo.
- **Unsupported distro** – Install the prerequisites manually (arm-none-eabi toolchain, pkg-config, libpng, python3, pip, numpy, pillow) then rerun the script; it’ll skip package installs once the tools are on your PATH.
- **Already-installed toolchain** – The script detects `arm-none-eabi-*` binaries and skips reinstalling them. Existing `tools/agbcc` installs are reused too; run `./scripts/quickstart.sh --refresh-agbcc` if you need a fresh copy.
- **Stale Arch package database** – The script never performs a partial
  `pacman` upgrade. Complete a full
  `sudo pacman --sync --refresh --sysupgrade`, then rerun it.
- **Slower rebuilds** – Subsequent `make` runs are faster. For incremental
  work, run `make -j4` (or choose another suitable job count) manually.

After the script finishes, launch your preferred emulator with `fireemblem8.gba` or start modifying the source.

## Opt-in modern GCC object cohort

The modern bootstrap compiles eighteen verified C files and three handwritten
assembly files to ARM relocatable objects only. It does **not** link an ELF or a
modern ROM, and it does not replace the current legacy ROM build. The modern
`ap.o`, the five save objects (`bmsave-misc.o`, `bmsave-gmap.o`,
`bmsave-lib.o`, `bmsave.o`, and `bmsave-xmap.o`), the convoy/container object
(`bmcontainer.o`, which defines `ClearSupplyItems` and `GetConvoyItemArray` for
save dependency closure but is not itself one of the save objects), the Proc
scheduler object (`proc.o`), the hardware/input object (`hardware.o`, which
calls into `proc.o`'s `Proc_Start`), and the object defining `AgbMain` remain
compile-only; none is linked into or executed by the ROM. `proc.o` and
`hardware.o` are neither save nor container objects; they close prior
cohort-internal Proc and key/VBlank dependencies but do not claim OAM,
software-reset, callback, ABI, SRAM, EWRAM-overlay, or any other runtime
readiness. Cross-ABI layout probes cover the world-map save structures, but
this does not claim callback, ABI, SRAM, EWRAM-overlay, or save-persistence
readiness.

The cohort also assembles three handwritten files that must not be
decompiled (see `CONTRIBUTING.md`): `libagbsyscall.o` is a self-contained set
of BIOS SWI trampolines (`SoftReset`, `SoundBiasReset`, `SoundBiasSet`, and
others), while `arm.o` and `arm_call.o` are a coupled ARM/Thumb interwork
pair — `arm_call.o`'s Thumb trampolines branch directly into `arm.o`'s
ARM-mode functions, so they are promoted together. Adding all three closes 17
prior cohort-unsatisfied symbols (the debug/aapcs unsatisfied set moves from
139 to 131), including `ClearOAMBuffer`, `SoftReset`, `SoundBiasReset`, and
`SoundBiasSet`, while exposing nine new IWRAM/ROM data globals that `arm.o`
references but does not define: `gBmMapTerrain`, `gBmMapUnit`,
`gMovMapFillStPool1`, `gMovMapFillStPool2`, `gMovMapFillState`,
`gMsgHuffmanTable`, `gMsgHuffmanTableRoot`, `gWorkingBmMap`, and
`gWorkingTerrainMoveCosts`. `arm.s`'s 13 exported functions are not yet typed
in `include/functions.h` (most are placeholder `// ??? Name(???);` comments);
this cohort promotion assembles the handwritten source as-is and does not
type those exports, link, boot, or otherwise claim runtime readiness.

Install GCC, binutils, and newlib headers for `arm-none-eabi`. Package names are
`gcc-arm-none-eabi`, `binutils-arm-none-eabi`, and
`libnewlib-arm-none-eabi` on Ubuntu/WSL; `arm-none-eabi-gcc`,
`arm-none-eabi-binutils`, and `arm-none-eabi-newlib` on Arch; and
the official `gcc-arm-embedded` Homebrew cask on macOS:

```bash
brew install --cask gcc-arm-embedded
```

Do not use Homebrew core's `arm-none-eabi-gcc` formula for this cohort: it is
configured without target headers. If it is already installed and takes
precedence, run `brew uninstall arm-none-eabi-gcc`, install the cask above, and
rerun the quickstart. The script checks both `<stdlib.h>` and `global.h` after
installation.

The system toolchain selected by the existing `PREFIX` (default
`arm-none-eabi-`) is used by default. Ubuntu's `/usr/include/newlib` is detected
automatically when present, so apt users can run the plain commands:

```bash
make expansion-modern-toolchain-check
make expansion-modern-cohort
```

Outputs are isolated under
`build/expansion-modern/<config>/<abi>/` (C objects under `src/`, the three
handwritten assembly objects under `src/` and `asm/`, matching each source's
own directory) as twenty-one `.o` and twenty-one `.d` files. Select
`MODERN_CONFIG=debug` (`-Og -g3`, the default) or `MODERN_CONFIG=release`
(`-O2 -g0 -DNDEBUG`). Select the provisional `MODERN_ABI=aapcs` default
(GCC's default ABI, with no explicit `-mabi`) or `MODERN_ABI=apcs-gnu`. These
modes support migration experiments and do not declare a final ABI choice.
The language mode stays `-std=gnu11` with `-fgnu89-inline` added solely so
that plain (non-static) `inline` definitions keep emitting an external
symbol the way legacy agbcc always did, not a broader GNU89 language switch.

For unpacked/local toolchains, use generic overrides rather than editing the
makefile. Paths containing spaces are supported:

```bash
make expansion-modern-cohort \
  MODERN_TOOLCHAIN_ROOT="/path with spaces/toolchain/usr" \
  MODERN_BINUTILS_DIR="/path with spaces/binutils" \
  MODERN_NEWLIB_INCLUDE="/path with spaces/newlib"
```

`MODERN_BINUTILS_DIR` is passed to GCC as `-B<dir>/`, and
`MODERN_NEWLIB_INCLUDE` is optional when GCC already finds its target headers.
`MODERN_CC` and `MODERN_OBJDUMP` provide direct executable overrides.
Run `make expansion-modern-clean` to remove only `build/expansion-modern`
(this also removes the full-source outputs described below, since both
targets share the same isolated output tree).

### Full-source modern compilation target

`expansion-modern-all` compiles every currently supported translation unit —
all 435 authoritative C files (363 normal `src/*.c`, including the generated
`src/msg_data.c`, plus the 72 preprocessed data files under `src/data/**`) and
the same 3 handwritten assembly files as the fast cohort — to relocatable
objects only. Like `expansion-modern-cohort`, it does not link an ELF or a
modern ROM. `expansion-modern-cohort` remains the fast, default,
dependency-closure-focused migration target; `expansion-modern-all` is the
comprehensive target proving the complete currently-supported source set
still compiles under modern GCC as the source tree grows.

```bash
make expansion-modern-toolchain-check
make expansion-modern-all
```

Outputs land in the same isolated `build/expansion-modern/<config>/<abi>/`
tree as the fast cohort (objects already built by `expansion-modern-cohort`
are not recompiled, since the 18-file cohort is a strict subset of the
363-file full C list) as 438 `.o` and 438 primary `.d` files. `MODERN_CONFIG`,
`MODERN_ABI`, and the toolchain override variables above all apply the same
way.

Data files under `src/data/**` embed `INCBIN_U8`/`INCBIN_U16` binary and
graphics assets that modern GCC cannot consume directly. Each one is compiled
in two steps: `tools/preproc` expands the `INCBIN_*` macros into an
intermediate `<name>.pre.c` file (leaving `#include` directives untouched),
and modern GCC then compiles that intermediate with its own `-MMD`/`-MP`
header tracking. Because `expansion-modern-all` is one of the modern goals
that forces `NODEP=1` to skip the legacy dependency machinery, it does not
gate its own asset tracking on `NODEP` — doing so would silently disable
INCBIN rebuild detection for the modern build itself. Instead, `tools/scaninc`
scans the original, un-preprocessed data source once per build and appends a
second dependency rule (`<name>.pre.c: <name>.c <asset1> <asset2> ...`) into
the same `.d` file GCC already produces for the object. That appended rule
takes effect starting with the next `make` invocation, exactly like GCC's own
header dependency tracking already works one build later — touching a scanned
INCBIN asset (or a header) after the first build correctly triggers a rebuild
of the affected data object. Some referenced assets (for example `.4bpp.lz`
graphics) are themselves generated by the legacy build's own compression
rules and are not committed to the repository. Because the top-level
`Makefile` already `include`s `modern.mk`, running `make expansion-modern-all`
(rather than `make -f modern.mk ...`) keeps those existing generation/
compression rules active in the same invocation: once the appended
scaninc-derived asset prerequisite is present in a `.d` file, any missing
asset with an existing rule and available source input is built transitively
before the data object compiles, which can make the first full-source run
substantially slower than later ones. Assets with no generation rule, or
whose own source inputs (`.png`/`.pal`/etc.) are missing, still fail
explicitly and may require the normal setup/build path (e.g.
`./scripts/quickstart.sh` or `make fireemblem8.gba`) to produce or restore
them first. This target still only produces relocatable objects — it never
links an ELF or ROM.
`MODERN_ALL_C_SOURCES`, `MODERN_ALL_DATA_C_SOURCES`, and
`MODERN_ALL_ASM_SOURCES` (a separate list from `MODERN_COHORT_ASM_SOURCES`,
so overriding one does not blank the other) default to the full supported
source set and can be overridden the same way as the cohort variables.
