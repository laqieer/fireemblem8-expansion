#!/bin/sh
set -eu

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
repo_root=$(CDPATH= cd -- "$script_dir/../.." && pwd)

default_out="$repo_root/build/shiftcheck/modern_relocs.elf"
if [ "$#" -gt 0 ] && [ "${1#-}" = "$1" ]; then
    out="$1"
    shift
else
    out="$default_out"
fi

case "$out" in
    *.elf) map_out="${out%.elf}.map" ;;
    *) map_out="$out.map" ;;
esac

LD=${LD:-arm-none-eabi-ld}
CC=${CC:-arm-none-eabi-gcc}
OBJECTS_LST=${OBJECTS_LST:-$repo_root/build/expansion-modern/debug/aapcs/link/objects.lst}
BANIM_SYM=${BANIM_SYM:-$repo_root/banim/data_banim.o.sym.o}
LDSCRIPT=${LDSCRIPT:-$repo_root/linker/expansion.ld}
ROM_SIZE_BYTES=${ROM_SIZE_BYTES:-0x01000000}

for path in "$OBJECTS_LST" "$BANIM_SYM" "$LDSCRIPT"; do
    if [ ! -f "$path" ]; then
        printf 'error: required input missing: %s\n' "$path" >&2
        exit 1
    fi
done

libgcc=$(
    "$CC" -mcpu=arm7tdmi -mthumb -print-libgcc-file-name
)
if [ -z "$libgcc" ] || [ ! -f "$libgcc" ]; then
    printf 'error: cannot discover libgcc via %s\n' "$CC" >&2
    exit 1
fi
libgcc_dir=$(dirname -- "$libgcc")

libc=$(
    "$CC" -mcpu=arm7tdmi -mthumb -print-file-name=libc.a
)
if [ -z "$libc" ] || [ ! -f "$libc" ]; then
    printf 'error: cannot discover libc.a via %s\n' "$CC" >&2
    exit 1
fi
libc_dir=$(dirname -- "$libc")

mkdir -p "$(dirname -- "$out")"

exec "$LD" \
    --orphan-handling=error \
    --defsym=__rom_size="$ROM_SIZE_BYTES" \
    -T "$LDSCRIPT" \
    -Map "$map_out" \
    @"$OBJECTS_LST" \
    -R "$BANIM_SYM" \
    -L "$libgcc_dir" \
    -L "$libc_dir" \
    -q \
    -o "$out" \
    -lc -lnosys -lgcc \
    "$@"
