#!/bin/sh
# Shifted-boot verification for the modern expansion linker.
#
# Builds a shifted ROM by passing --defsym=__text_shift=<SHIFT> (the P9-A
# interface MODERN_TEXT_SHIFT) and verifies it produces the same deterministic
# boot fingerprints as the normal ROM using gba_playtest behavior policy.
#
# Exit: 0=PASS, 1=FAIL (diverged), 2=setup/build error.
#
# Usage:
#   scripts/shiftcheck/modern_shifted_boot.sh [shift_amount]
#   SHIFTCHECK_SHIFT=0x80000 scripts/shiftcheck/modern_shifted_boot.sh
set -eu

die() { printf 'error: %s\n' "$*" >&2; exit 2; }

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
ROOT_DIR=$(CDPATH= cd -- "$SCRIPT_DIR/../.." && pwd)
cd "$ROOT_DIR"

# --- Configuration (all overridable via env) ---
SHIFT="${1:-${SHIFTCHECK_SHIFT:-0x40000}}"
OUTDIR="${SHIFTCHECK_OUTDIR:-build/shiftcheck}"
OBJECTS_LST="${SHIFTCHECK_OBJECTS_LST:-build/expansion-modern/debug/aapcs/link/objects.lst}"
BANIM_SYM="${SHIFTCHECK_BANIM_SYM:-banim/data_banim.o.sym.o}"
LDSCRIPT="${SHIFTCHECK_LDSCRIPT:-linker/expansion.ld}"
ROM_SIZE="${SHIFTCHECK_ROM_SIZE_BYTES:-0x01000000}"
PAD_TO="${SHIFTCHECK_PAD_TO:-0x9000000}"
SCENARIO="${SHIFTCHECK_SCENARIO:-tools/gba-playtest/scenarios/boot.json}"
EXPECTED="${SHIFTCHECK_EXPECTED:-tools/gba-playtest/fingerprints/boot.json}"
PREFIX="${PREFIX:-arm-none-eabi-}"

# --- Tool resolution ---
CC="${MODERN_CC:-$(command -v ${PREFIX}gcc 2>/dev/null || true)}"
LD="${MODERN_LD:-$(command -v ${PREFIX}ld 2>/dev/null || true)}"
OBJCOPY="${MODERN_OBJCOPY:-$(command -v ${PREFIX}objcopy 2>/dev/null || true)}"

[ -n "$CC" ] && [ -x "$CC" ] || die "cannot find ${PREFIX}gcc; set MODERN_CC or PATH"
[ -n "$LD" ] && [ -x "$LD" ] || die "cannot find ${PREFIX}ld; set MODERN_LD or PATH"
[ -n "$OBJCOPY" ] && [ -x "$OBJCOPY" ] || die "cannot find ${PREFIX}objcopy"

# --- Prerequisite check ---
[ -f "$LDSCRIPT" ]    || die "missing ldscript: $LDSCRIPT"
[ -f "$OBJECTS_LST" ] || die "missing objects list: $OBJECTS_LST (build expansion-modern-elf first)"
[ -f "$BANIM_SYM" ]   || die "missing banim sidecar: $BANIM_SYM"
[ -f "$SCENARIO" ]    || die "missing scenario: $SCENARIO"
[ -f "$EXPECTED" ]    || die "missing fingerprint: $EXPECTED"

# --- Library discovery ---
libgcc_dir=$("$CC" -mcpu=arm7tdmi -mthumb -print-libgcc-file-name | xargs dirname) \
    || die "cannot discover libgcc via $CC"
libc_dir=$("$CC" -mcpu=arm7tdmi -mthumb -print-file-name=libc.a | xargs dirname) \
    || die "cannot discover libc via $CC"

mkdir -p "$OUTDIR"

# --- Link with shift ---
printf 'Linking shifted ELF (__text_shift=%s)...\n' "$SHIFT"
"$LD" \
    --defsym=__rom_size="$ROM_SIZE" \
    --defsym=__text_shift="$SHIFT" \
    -T "$LDSCRIPT" \
    -Map "$OUTDIR/shifted.map" \
    @"$OBJECTS_LST" \
    -R "$BANIM_SYM" \
    -L "$libgcc_dir" \
    -L "$libc_dir" \
    -o "$OUTDIR/shifted.elf" \
    -lc -lnosys -lgcc \
    || die "shifted link failed"

# --- Convert to ROM ---
printf 'Converting to ROM...\n'
"$OBJCOPY" --strip-debug -O binary --pad-to "$PAD_TO" --gap-fill=0xff \
    "$OUTDIR/shifted.elf" "$OUTDIR/shifted.gba" \
    || die "objcopy failed"

# --- Verify boot behavior ---
printf 'Verifying shifted boot (shift=%s)...\n' "$SHIFT"
set +e
python3 tools/gba-playtest/gba_playtest.py verify \
    --rom "$OUTDIR/shifted.gba" \
    --scenario "$SCENARIO" \
    --expected "$EXPECTED" \
    --policy behavior
rc=$?
set -e

case "$rc" in
    0) printf 'SHIFTED BOOT: PASS (shift=%s)\n' "$SHIFT"; exit 0 ;;
    *) printf 'SHIFTED BOOT: FAIL (shift=%s, exit=%d)\n' "$SHIFT" "$rc" >&2; exit 1 ;;
esac
