#!/usr/bin/env python3
"""Diff matching-vs-shifted RAM dumps to find the real state divergence.

The shifted ROM's RAM legitimately differs wherever a correctly-relocated pointer
is stored (shifted = matching + SHIFT, and the value is a ROM pointer). Those are
benign. Everything else that differs is a REAL divergence -- the footprint of a
pointer that did NOT relocate. Each real-diff address is resolved to its EWRAM/IWRAM
symbol (from the ELF) so the diverged game-state structure is named.

Usage: ramdiff.py <frame> [--dir DIR] [--elf ELF] [--shift 0x40000]
  expects <DIR>/matching_<frame>_ew.bin / _iw.bin and shifted_<frame>_ew.bin / _iw.bin
"""

import argparse
import bisect
import struct
import subprocess
import sys


def ram_syms(elf, nm):
    out = subprocess.run([nm, "-n", elf], capture_output=True, text=True).stdout
    syms = []
    for ln in out.splitlines():
        p = ln.split()
        if len(p) < 3:
            continue
        try:
            a = int(p[0], 16)
        except ValueError:
            continue
        if 0x02000000 <= a < 0x02040000 or 0x03000000 <= a < 0x03008000:
            syms.append((a, " ".join(p[2:])))
    syms.sort()
    return syms


def owner(syms, addrs, a):
    i = bisect.bisect_right(addrs, a) - 1
    if i >= 0:
        return syms[i][1], a - syms[i][0]
    return "?", 0


def diff_region(mpath, spath, base, shift, syms, addrs):
    try:
        m = open(mpath, "rb").read()
        s = open(spath, "rb").read()
    except FileNotFoundError:
        return None
    out = []
    n = min(len(m), len(s))
    for i in range(0, n - 3, 4):
        mw = struct.unpack_from("<I", m, i)[0]
        sw = struct.unpack_from("<I", s, i)[0]
        if mw == sw:
            continue
        # benign: a relocated ROM pointer stored in RAM (shifted-region target)
        if 0x08000100 <= mw < 0x08C00000 and sw == ((mw + shift) & 0xFFFFFFFF):
            continue
        sym, off = owner(syms, addrs, base + i)
        out.append((base + i, mw, sw, sym, off))
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("frame")
    ap.add_argument("--dir", default="/mnt/c/gbahawk_test/out")
    ap.add_argument("--elf", default="fireemblem8.elf")
    ap.add_argument("--prefix", default="arm-none-eabi-")
    ap.add_argument("--shift", default="0x40000")
    ap.add_argument("--max", type=int, default=60)
    args = ap.parse_args()
    shift = int(args.shift, 0)
    syms = ram_syms(args.elf, args.prefix + "nm")
    addrs = [s[0] for s in syms]

    total = []
    for region, base in (("ew", 0x02000000), ("iw", 0x03000000)):
        d = diff_region(f"{args.dir}/matching_{args.frame}_{region}.bin",
                        f"{args.dir}/shifted_{args.frame}_{region}.bin",
                        base, shift, syms, addrs)
        if d is None:
            print(f"(missing {region} dumps for frame {args.frame})")
            continue
        total += d

    print("=" * 74)
    print(f"RAM real-divergence at frame {args.frame}: {len(total)} word(s) "
          f"(benign relocated pointers excluded)")
    print("=" * 74)
    # group by owning symbol
    from collections import Counter
    bysym = Counter(t[3] for t in total)
    print("by owning symbol:")
    for sym, n in bysym.most_common(30):
        print(f"  {n:5d}  {sym}")
    print("-" * 74)
    for addr, mw, sw, sym, off in total[: args.max]:
        print(f"  0x{addr:08X} {sym}+0x{off:X}: {mw:#010x} -> {sw:#010x}")
    if len(total) > args.max:
        print(f"  ... and {len(total) - args.max} more")
    return 0


if __name__ == "__main__":
    sys.exit(main())
