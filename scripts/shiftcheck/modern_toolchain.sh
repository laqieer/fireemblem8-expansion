#!/bin/sh

shiftcheck_resolve_tool()
{
    requested=$1
    repo_root=$2
    fallback_name=$3

    case "$requested" in
        */*)
            if [ -x "$requested" ]; then
                printf '%s\n' "$requested"
                return 0
            fi
            ;;
        *)
            resolved=$(command -v "$requested" 2>/dev/null || true)
            if [ -n "$resolved" ] && [ -x "$resolved" ]; then
                printf '%s\n' "$resolved"
                return 0
            fi
            ;;
    esac

    for root in \
        "$repo_root/build/toolchain-root/usr" \
        "$repo_root/.deps/arm-toolchain-root/usr"
    do
        candidate="$root/bin/$fallback_name"
        if [ -x "$candidate" ]; then
            printf '%s\n' "$candidate"
            return 0
        fi
    done

    return 1
}

shiftcheck_libgcc_dir()
{
    cc=$1
    libgcc=$("$cc" -mcpu=arm7tdmi -mthumb -print-libgcc-file-name 2>/dev/null || true)
    if [ -n "$libgcc" ] && [ -f "$libgcc" ]; then
        dirname -- "$libgcc"
        return 0
    fi

    return 1
}

shiftcheck_newlib_dir()
{
    cc=$1
    override=${2:-}
    libc=$("$cc" -mcpu=arm7tdmi -mthumb -print-file-name=libc.a 2>/dev/null || true)
    if [ -n "$libc" ] && [ -f "$libc" ]; then
        dirname -- "$libc"
        return 0
    fi

    cc_bindir=$(CDPATH= cd -- "$(dirname -- "$cc")" && pwd)
    for directory in \
        "$override" \
        "$cc_bindir/../lib/arm-none-eabi/newlib"
    do
        if [ -n "$directory" ] && [ -f "$directory/libc.a" ] &&
            [ -f "$directory/libnosys.a" ]; then
            printf '%s\n' "$directory"
            return 0
        fi
    done

    return 1
}
