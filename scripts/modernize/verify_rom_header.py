#!/usr/bin/env python3
"""Verify a GBA ROM's header fields, size, and checksum.

Checks the fixed-format GBA cartridge header this project ships in
``src/rom_header.s``: exact ROM size, title, game code, maker code, the
fixed magic byte, and the checksum byte at offset 0xBD recomputed from the
header bytes at 0xA0..0xBC. This does not compare ROM contents byte-for-byte
against any other build; it only proves the produced ROM is a well-formed,
internally consistent GBA image with this project's expected identity
fields.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Offsets are absolute file offsets, matching the GBA cartridge header layout
# starting at 0x00 (entry point) through 0xBF (end of the fixed header).
HEADER_TITLE_OFFSET = 0xA0
HEADER_TITLE_SIZE = 12
HEADER_GAME_CODE_OFFSET = 0xAC
HEADER_GAME_CODE_SIZE = 4
HEADER_MAKER_CODE_OFFSET = 0xB0
HEADER_MAKER_CODE_SIZE = 2
HEADER_FIXED_BYTE_OFFSET = 0xB2
HEADER_CHECKSUM_RANGE = (0xA0, 0xBC)  # inclusive start, inclusive end
HEADER_CHECKSUM_OFFSET = 0xBD
HEADER_CHECKSUM_MAGIC = 0x19
HEADER_END = 0xC0

# Expected identity fields, matching src/rom_header.s exactly.
EXPECTED_TITLE = "FIREEMBLEM2E"
EXPECTED_GAME_CODE = "BE8E"
EXPECTED_MAKER_CODE = "01"
EXPECTED_FIXED_BYTE = 0x96
EXPECTED_SIZE = 16 * 1024 * 1024  # 16 MiB, matching --pad-to 0x9000000


class RomHeaderError(Exception):
    """The ROM's size, header fields, or checksum do not match expectations."""


def compute_checksum(data: bytes) -> int:
    """Recompute the header checksum byte from bytes 0xA0..0xBC inclusive."""
    start, end = HEADER_CHECKSUM_RANGE
    region = data[start : end + 1]
    return (-(sum(region) + HEADER_CHECKSUM_MAGIC)) & 0xFF


def decode_ascii_field(raw: bytes, label: str) -> str:
    value = raw.rstrip(b"\x00")
    if any(byte < 0x20 or byte > 0x7E for byte in value):
        raise RomHeaderError(f"ROM {label} is not printable ASCII: {raw!r}")
    return value.decode("ascii")


def verify_rom_header(path: Path) -> dict[str, object]:
    """Verify size, identity fields, and checksum; return the parsed facts.

    Raises RomHeaderError on any mismatch, naming the exact field and the
    expected vs. actual value.
    """
    size = path.stat().st_size
    if size != EXPECTED_SIZE:
        raise RomHeaderError(
            f"ROM size {size} bytes does not match expected {EXPECTED_SIZE} "
            f"bytes (16 MiB)"
        )

    data = path.read_bytes()
    if len(data) < HEADER_END:
        raise RomHeaderError("ROM is too small to contain a GBA header")

    title = decode_ascii_field(
        data[HEADER_TITLE_OFFSET : HEADER_TITLE_OFFSET + HEADER_TITLE_SIZE],
        "title",
    )
    if title != EXPECTED_TITLE:
        raise RomHeaderError(f"ROM title {title!r} != expected {EXPECTED_TITLE!r}")

    game_code = decode_ascii_field(
        data[HEADER_GAME_CODE_OFFSET : HEADER_GAME_CODE_OFFSET + HEADER_GAME_CODE_SIZE],
        "game code",
    )
    if game_code != EXPECTED_GAME_CODE:
        raise RomHeaderError(
            f"ROM game code {game_code!r} != expected {EXPECTED_GAME_CODE!r}"
        )

    maker_code = decode_ascii_field(
        data[HEADER_MAKER_CODE_OFFSET : HEADER_MAKER_CODE_OFFSET + HEADER_MAKER_CODE_SIZE],
        "maker code",
    )
    if maker_code != EXPECTED_MAKER_CODE:
        raise RomHeaderError(
            f"ROM maker code {maker_code!r} != expected {EXPECTED_MAKER_CODE!r}"
        )

    fixed_byte = data[HEADER_FIXED_BYTE_OFFSET]
    if fixed_byte != EXPECTED_FIXED_BYTE:
        raise RomHeaderError(
            f"ROM fixed header byte 0x{fixed_byte:02X} != expected "
            f"0x{EXPECTED_FIXED_BYTE:02X}"
        )

    expected_checksum = compute_checksum(data)
    actual_checksum = data[HEADER_CHECKSUM_OFFSET]
    if actual_checksum != expected_checksum:
        raise RomHeaderError(
            f"ROM checksum byte at 0x{HEADER_CHECKSUM_OFFSET:02X} is "
            f"0x{actual_checksum:02X}; recomputed from 0xA0..0xBC gives "
            f"0x{expected_checksum:02X}"
        )

    return {
        "size": size,
        "title": title,
        "game_code": game_code,
        "maker_code": maker_code,
        "fixed_byte": fixed_byte,
        "checksum": actual_checksum,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("rom", type=Path, help="path to the GBA ROM to verify")
    args = parser.parse_args(argv)

    try:
        facts = verify_rom_header(args.rom)
    except (RomHeaderError, OSError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    print(
        f"ROM header valid: {args.rom} "
        f"(size={facts['size']} title={facts['title']!r} "
        f"game_code={facts['game_code']!r} maker_code={facts['maker_code']!r} "
        f"fixed_byte=0x{facts['fixed_byte']:02X} "
        f"checksum=0x{facts['checksum']:02X})"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
