#!/usr/bin/env python3
"""Convert an opaque .bin blob into an in-place inline `.2byte` halfword table.

Emits a GAS data block (little-endian u16 entries, 8 per line) bracketed by a
size-assertion so any length drift fails assembly at that blob. Intended to
replace `.incbin "X.bin"` lines for u16 color/data tables that have no
byte-exact typed asset form (see reports/blob_extraction_classification.md).

Usage:
    bin_to_asm_halfwords.py FILE.bin LABEL [--comment "RGB15 color table"]
prints the block (label NOT included; caller keeps the existing `.global`/label).
"""
import argparse
import struct
import sys


def emit(path, label, comment):
    data = open(path, "rb").read()
    if len(data) % 2 != 0:
        sys.exit(f"{path}: odd length {len(data)} not representable as u16 table")
    words = struct.unpack(f"<{len(data)//2}H", data)
    lines = []
    if comment:
        lines.append(f"\t@ {comment} ({len(words)} u16 entries, {len(data)} bytes)")
    for i in range(0, len(words), 8):
        chunk = ", ".join(f"0x{w:04X}" for w in words[i:i + 8])
        lines.append(f"\t.2byte {chunk}")
    end = f".L_end_{label}"
    lines.append(f"{end}:")
    lines.append(f"\t.if ({end} - {label}) != {len(data)}")
    lines.append(f"\t.error \"{label} size mismatch\"")
    lines.append("\t.endif")
    return "\n".join(lines) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("bin")
    ap.add_argument("label")
    ap.add_argument("--comment", default="")
    a = ap.parse_args()
    sys.stdout.write(emit(a.bin, a.label, a.comment))


if __name__ == "__main__":
    main()
