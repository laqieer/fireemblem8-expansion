"""Source-only builder for a tiny, freely generated GBA integration fixture.

The generated ROM selects bitmap mode 3, paints pixel 0 blue (released) or red
(A held), and mirrors KEYINPUT into EWRAM address 0x02000000. No Nintendo logo,
commercial ROM bytes, compiler, assembler, BIOS, save, or binary fixture is
required or committed.
"""

from __future__ import annotations

import os
import struct
import shutil
import subprocess
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


def build_seed_batch_rom(path: Path) -> None:
    """Build a fixture that mirrors the injected seed into terminal probes."""
    rom = bytearray(ROM_SIZE)

    _word(rom, 0, 0xEA000000 | ((ENTRY - 8) // 4))
    rom[0xA0:0xAC] = b"GPTBATCH".ljust(12, b"\0")
    rom[0xAC:0xB0] = b"GPB1"
    rom[0xB0:0xB2] = b"00"
    rom[0xB2] = 0x96

    instructions = (
        0xE59F000C,  # ldr r0, =0x02000000
        0xE5901000,  # ldr r1, [r0]
        0xE5801004,  # str r1, [r0, #4]
        0xE5801008,  # str r1, [r0, #8]
        0xEAFFFFFB,  # b loop
    )
    for index, instruction in enumerate(instructions):
        _word(rom, ENTRY + index * 4, instruction)
    _word(rom, ENTRY + len(instructions) * 4, 0x02000000)

    rom[0xBD] = (-sum(rom[0xA0:0xBD]) - 0x19) & 0xFF
    path.write_bytes(rom)


def build_two_chapter_planner_rom(path: Path) -> None:
    """Build a clean-boot two-step planner transport fixture.

    EWRAM words are stage, chapter, and terminal respectively. A single A
    press advances one normal fixture chapter; no save, savestate, or host
    memory write is needed to reset or continue the route.
    """
    rom = bytearray(ROM_SIZE)

    _word(rom, 0, 0xEA000000 | ((ENTRY - 8) // 4))
    rom[0xA0:0xAC] = b"GPTPLAN2".ljust(12, b"\0")
    rom[0xAC:0xB0] = b"GPP2"
    rom[0xB0:0xB2] = b"00"
    rom[0xB2] = 0x96

    instructions = (
        0xE59F004C,  # ldr r0, =0x02000000
        0xE3A01000,  # mov r1, #0
        0xE5801000,  # str r1, [r0]             (fresh clean boot state)
        0xE5901000,  # loop: ldr r1, [r0]       (stage)
        0xE59F2040,  # ldr r2, =0x04000130
        0xE1D230B0,  # ldrh r3, [r2]            (KEYINPUT)
        0xE3130001,  # tst r3, #1               (A is active-low)
        0x1A000003,  # bne no_input
        0xE3510002,  # cmp r1, #2
        0x2A000001,  # bhs no_input
        0xE2811001,  # add r1, r1, #1
        0xE5801000,  # str r1, [r0]
        0xE3510000,  # no_input: cmp r1, #0
        0x03A04001,  # moveq r4, #1
        0x13A04002,  # movne r4, #2
        0xE5804004,  # str r4, [r0, #4]         (chapter)
        0xE3510002,  # cmp r1, #2
        0x23A04001,  # movhs r4, #1
        0x33A04000,  # movlo r4, #0
        0xE5804008,  # str r4, [r0, #8]         (terminal)
        0xEAFFFFED,  # b loop
    )
    for index, instruction in enumerate(instructions):
        _word(rom, ENTRY + index * 4, instruction)
    _word(rom, ENTRY + len(instructions) * 4, 0x02000000)
    _word(rom, ENTRY + len(instructions) * 4 + 4, 0x04000130)

    rom[0xBD] = (-sum(rom[0xA0:0xBD]) - 0x19) & 0xFF
    path.write_bytes(rom)


def build_production_planner_rom(path: Path, elf: Path) -> None:
    """Link the production planner implementation into a tiny freestanding ROM."""
    root = Path(__file__).resolve().parents[3]
    compiler = shutil.which("arm-none-eabi-gcc")
    objcopy = shutil.which("arm-none-eabi-objcopy")
    if compiler is None or objcopy is None:
        raise RuntimeError("planner runtime toolchain unavailable")
    sources = (
        root / "src" / "expansion_autoplay_planner.c",
        root / "tools" / "gba-playtest" / "tests" / "c"
        / "expansion_autoplay_planner_runtime.c",
        root / "tools" / "gba-playtest" / "tests" / "c"
        / "expansion_autoplay_planner_runtime_start.s",
    )
    linker = (
        root
        / "tools"
        / "gba-playtest"
        / "tests"
        / "c"
        / "expansion_autoplay_planner_runtime.ld"
    )
    command = [
        compiler,
        "-mcpu=arm7tdmi",
        "-marm",
        "-mthumb-interwork",
        "-mabi=aapcs",
        "-std=gnu89",
        "-ffreestanding",
        "-fno-builtin",
        "-nostdlib",
        "-O2",
        "-I",
        str(root / "include"),
        "-I",
        str(root / "include" / "generated"),
        "-DFE8_EXPANSION_MODERN_BUILD=1",
        "-DFE8_EXPANSION_DEBUG=1",
        "-DFE8_EXPANSION_AUTOPLAY_PLANNER=1",
        "-DFE8_AUTOPLAY_PLANNER_RUNTIME_TEST=1",
        *map(str, sources),
        "-Wl,-T,{}".format(linker),
        "-Wl,--gc-sections",
        "-lgcc",
        "-o",
        str(elf),
    ]
    environment = dict(os.environ)
    environment["TMPDIR"] = str(path.parent)
    completed = subprocess.run(
        command,
        cwd=root,
        env=environment,
        capture_output=True,
        text=True,
    )
    if completed.returncode:
        raise RuntimeError(completed.stdout + completed.stderr)
    completed = subprocess.run(
        [objcopy, "-O", "binary", str(elf), str(path)],
        cwd=root,
        env=environment,
        capture_output=True,
        text=True,
    )
    if completed.returncode:
        raise RuntimeError(completed.stdout + completed.stderr)
    rom = bytearray(path.read_bytes())
    if len(rom) < ROM_SIZE:
        rom.extend(b"\0" * (ROM_SIZE - len(rom)))
    rom[0xBD] = (-sum(rom[0xA0:0xBD]) - 0x19) & 0xFF
    path.write_bytes(rom)


def _planner_symbol_addresses(elf: Path) -> dict[str, int]:
    nm = shutil.which("arm-none-eabi-nm")
    if nm is None:
        raise RuntimeError("planner runtime toolchain unavailable")
    completed = subprocess.run(
        [nm, "-g", str(elf)],
        capture_output=True,
        text=True,
    )
    if completed.returncode:
        raise RuntimeError(completed.stdout + completed.stderr)
    required = {
        "gExpansionAutoplayPlannerObservation",
        "gExpansionAutoplayPlannerCommand",
        "gExpansionAutoplayPlannerCampaignCheckpoint",
    }
    addresses: dict[str, int] = {}
    for line in completed.stdout.splitlines():
        fields = line.split()
        if len(fields) == 3 and fields[2] in required:
            addresses[fields[2]] = int(fields[0], 16)
    missing = required - addresses.keys()
    if missing:
        raise RuntimeError(
            "planner transport symbols missing: {}".format(
                ", ".join(sorted(missing))
            )
        )
    return addresses


def build_planner_transport_backend(path: Path, elf: Path) -> None:
    """Build the fixed-symbol stdin/stdout libmGBA planner adapter."""
    root = Path(__file__).resolve().parents[3]
    compiler = shutil.which(os.environ.get("CC", "cc"))
    if compiler is None:
        raise RuntimeError("planner transport host compiler unavailable")
    addresses = _planner_symbol_addresses(elf)
    command = [
        compiler,
        "-std=gnu11",
        "-O2",
        "-Wall",
        "-Wextra",
        "-Werror",
        "-I",
        str(root / "include"),
        "-I",
        str(root / "include" / "generated"),
        "-DPLANNER_OBSERVATION_ADDR=0x{:08x}u".format(
            addresses["gExpansionAutoplayPlannerObservation"]
        ),
        "-DPLANNER_COMMAND_ADDR=0x{:08x}u".format(
            addresses["gExpansionAutoplayPlannerCommand"]
        ),
        "-DPLANNER_CHECKPOINT_ADDR=0x{:08x}u".format(
            addresses["gExpansionAutoplayPlannerCampaignCheckpoint"]
        ),
        str(root / "tools" / "gba-playtest" / "planner_transport_backend.c"),
        "-o",
        str(path),
    ]
    pkg_config = shutil.which("pkg-config")
    if pkg_config is not None:
        flags = subprocess.run(
            [pkg_config, "--cflags", "--libs", "mgba"],
            capture_output=True,
            text=True,
        )
        if flags.returncode == 0:
            import shlex

            command.extend(shlex.split(flags.stdout))
        else:
            command.append("-lmgba")
    else:
        command.append("-lmgba")
    completed = subprocess.run(
        command,
        cwd=root,
        capture_output=True,
        text=True,
    )
    if completed.returncode:
        raise RuntimeError(completed.stdout + completed.stderr)
