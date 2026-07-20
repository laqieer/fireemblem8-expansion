# config.mk -- central, committed configuration surface for the expansion
# framework's semantic version and default GBA ROM identity (issue #8).
#
# This file intentionally does NOT redefine or duplicate MODERN_CONFIG
# (debug|release), MODERN_ABI (aapcs|apcs-gnu), MODERN_ROM_SIZE (16M|32M),
# or MODERN_TEXT_SHIFT: those presets remain owned and validated in
# modern.mk. config.mk only owns the values modern.mk did not previously
# have anywhere: the framework's semantic version and the GBA cartridge
# header identity fields.
#
# Every value below is validated by scripts/modernize/expansion_config.py
# before any modern C/assembly compilation or linking is attempted (see
# modern.mk's "Framework configuration and ROM identity" section), and is
# embedded into every modern ROM's expansion metadata record (see
# include/expansion_metadata.h and src/expansion_metadata.c). Overriding a
# value on the `make` command line (e.g. `make ... EXPANSION_ROM_TITLE=...`)
# changes the built ROM's identity; see docs/config_identity.md for the
# full settings reference, including which settings affect ABI, ROM
# data/layout, runtime behavior, or future save compatibility.

# --- Framework semantic version --------------------------------------------
# Each component must be an integer in [0, 255]. Bump these to mark a
# framework/config-identity change; the packed/string forms are derived
# automatically (see FE8_EXPANSION_VERSION_PACKED in
# include/expansion_config.h) and both are embedded in every modern ROM.
EXPANSION_VERSION_MAJOR ?= 0
EXPANSION_VERSION_MINOR ?= 1
EXPANSION_VERSION_PATCH ?= 0

# --- GBA cartridge header identity ------------------------------------------
# Defaults match the values hardcoded today in src/rom_header.s (the legacy
# build path, left untouched). The modern ROM recipe (modern.mk) patches a
# copy of the built ROM's header with these same fields and regenerates the
# header checksum accordingly -- see scripts/modernize/finalize_rom_header.py.
#   EXPANSION_ROM_TITLE      -- up to 12 printable-ASCII bytes (NUL-padded).
#   EXPANSION_ROM_GAME_CODE  -- exactly 4 printable-ASCII bytes.
#   EXPANSION_ROM_MAKER_CODE -- exactly 2 printable-ASCII bytes.
#   EXPANSION_ROM_REVISION   -- an integer in [0, 255] (the header's
#                               "software version" byte).
EXPANSION_ROM_TITLE      ?= FIREEMBLEM2E
EXPANSION_ROM_GAME_CODE  ?= BE8E
EXPANSION_ROM_MAKER_CODE ?= 01
EXPANSION_ROM_REVISION   ?= 0

# --- Deterministic build identity -------------------------------------------
# Explicit override for the embedded build id, e.g. for a CI-provided value
# on a reproducible source-archive build that has no .git directory. Empty
# by default: modern.mk then falls back to `git rev-parse HEAD` (works the
# same for a normal branch checkout or a detached HEAD) and finally to the
# fixed "unknown" sentinel when no git metadata is available at all. Never
# a timestamp, branch name, or host path -- see docs/config_identity.md.
EXPANSION_BUILD_ID ?=
