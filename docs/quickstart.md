# Fire Emblem 8U Quick Start

Get a working build of this decompilation with a single command using the bundled `scripts/quickstart.sh` helper. (If you prefer manual setup or run on another distro/package manager, see the README section below.)

## Prerequisites

- _(Optional)_ A legally obtained copy of **Fire Emblem: The Sacred Stones (USA)**. The build does **not** need it — the result is verified against `checksum.sha1` — so it is only used by `asmdiff.sh` for disassembly comparison. If you have one, place it at the repo root as `baserom.gba`, or pass `--rom /path/to/rom.gba` (or `FIREEMBLEM8U_ROM=/path/to/rom.gba`).
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
7. Verifies the ROM hash with `sha1sum`, or `shasum -a 1` where GNU
   `sha1sum` is unavailable.

On success you’ll see:

```
fireemblem8.gba: OK
[✓] Build complete: /path/to/fireemblem8u/fireemblem8.gba
```

## Troubleshooting

- **No ROM** – `baserom.gba` is optional; the build works and self-verifies without it. Provide `--rom /path/to/rom.gba` (or `FIREEMBLEM8U_ROM=/path/to/rom.gba`) only if you want to use `asmdiff.sh`.
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

The modern bootstrap compiles thirteen verified C files to ARM relocatable
objects only. It does **not** link an ELF or a modern ROM, and it does not replace
the matching legacy ROM build. The modern `ap.o`, the three save objects
(`bmsave-misc.o`, `bmsave-gmap.o`, and `bmsave-lib.o`), and the object defining
`AgbMain` remain compile-only; none is linked into or executed by the ROM.
Cross-ABI layout probes cover the world-map save structures, but this does not
claim callback, ABI, SRAM, EWRAM-overlay, or save-persistence readiness.

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
`build/expansion-modern/<config>/<abi>/src/` as thirteen `.o` and thirteen `.d`
files. Select `MODERN_CONFIG=debug` (`-Og -g3`, the default) or
`MODERN_CONFIG=release` (`-O2 -g0 -DNDEBUG`). Select the provisional
`MODERN_ABI=aapcs` default (GCC's default ABI, with no explicit `-mabi`) or
`MODERN_ABI=apcs-gnu`. These modes support migration experiments and do not
declare a final ABI choice.

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
Run `make expansion-modern-clean` to remove only `build/expansion-modern`.
