"""Source-only builder for a tiny, freely generated GBA integration fixture.

The generated ROM selects bitmap mode 3, paints pixel 0 blue (released) or red
(A held), and mirrors KEYINPUT into EWRAM address 0x02000000. No Nintendo logo,
commercial ROM bytes, compiler, assembler, BIOS, save, or binary fixture is
required or committed.
"""

from __future__ import annotations

import struct
from pathlib import Path


ROM_SIZE = 0x400
ENTRY = 0xC0


def _word(rom: bytearray, address: int, value: int) -> None:
    struct.pack_into("<I", rom, address, value)


def build_homebrew_rom(path: Path) -> None:
    rom = bytearray(ROM_SIZE)

    # ARM branch from reset vector 0 to ENTRY.
    _word(rom, 0, 0xEA000000 | ((ENTRY - 8) // 4))

    rom[0xA0:0xAC] = b"GPTFIXTURE".ljust(12, b"\0")
    rom[0xAC:0xB0] = b"GPT0"
    rom[0xB0:0xB2] = b"00"
    rom[0xB2] = 0x96

    # ARM source, encoded directly so integration has no cross-toolchain
    # dependency. Literal offsets are relative to PC = instruction + 8.
    instructions = (
        0xE59F002C,  # ldr   r0, =0x04000000  (DISPCNT)
        0xE59F102C,  # ldr   r1, =0x00000403  (mode 3 + BG2)
        0xE1C010B0,  # strh  r1, [r0]
        0xE59F0028,  # loop: ldr r0, =0x04000130 (KEYINPUT)
        0xE1D010B0,  # ldrh  r1, [r0]
        0xE3110001,  # tst   r1, #1             (A is active-low)
        0x159F2020,  # ldrne r2, =0x00007c00   (released: blue)
        0x059F2020,  # ldreq r2, =0x0000001f   (held: red)
        0xE59F3020,  # ldr   r3, =0x06000000   (VRAM)
        0xE1C320B0,  # strh  r2, [r3]
        0xE59F301C,  # ldr   r3, =0x02000000   (semantic probe)
        0xE5831000,  # str   r1, [r3]
        0xEAFFFFF5,  # b     loop
    )
    literals = (
        0x04000000,
        0x00000403,
        0x04000130,
        0x00007C00,
        0x0000001F,
        0x06000000,
        0x02000000,
    )
    for index, instruction in enumerate(instructions):
        _word(rom, ENTRY + index * 4, instruction)
    literal_base = ENTRY + len(instructions) * 4
    for index, literal in enumerate(literals):
        _word(rom, literal_base + index * 4, literal)

    # Standard GBA header complement; the intentionally absent Nintendo logo is
    # not needed by libmGBA's HLE boot and avoids embedding proprietary bytes.
    rom[0xBD] = (-sum(rom[0xA0:0xBD]) - 0x19) & 0xFF
    path.write_bytes(rom)
