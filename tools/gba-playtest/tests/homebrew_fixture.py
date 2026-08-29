"""Source-only builder for a tiny, freely generated GBA integration fixture.

The generated ROM selects bitmap mode 3, paints pixel 0 blue (released) or red
(A held), and mirrors KEYINPUT into EWRAM address 0x02000000. No Nintendo logo,
commercial ROM bytes, compiler, assembler, BIOS, save, or binary fixture is
required or committed.
"""

from __future__ import annotations

import os
import shlex
import struct
import shutil
import subprocess
from pathlib import Path


ROM_SIZE = 0x400
ENTRY = 0xC0


def _run_checked(command, root, environment=None):
    completed = subprocess.run(
        command,
        cwd=root,
        env=environment,
        capture_output=True,
        text=True,
    )
    if completed.returncode:
        raise RuntimeError(completed.stdout + completed.stderr)
    return completed


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


def build_production_planner_rom(
    path: Path,
    elf: Path,
    *,
    commit_delay_frames: int = 0,
    stall_after_commit: bool = False,
    ignore_commands: bool = False,
    transition_subcode: int = 2,
    candidate_mode: int = 0,
    flag_domain_mode: int = 0,
    acknowledgement_override: tuple[int, int] | None = None,
    zero_digest: bool = False,
    startup_delay_frames: int = 0,
    startup_state_override: int = 0,
    mutate_selected_item_before_commit: bool = False,
) -> None:
    """Link the production planner implementation into a tiny freestanding ROM."""
    if commit_delay_frames < 0:
        raise ValueError("commit_delay_frames must be non-negative")
    if transition_subcode not in {1, 2, 3}:
        raise ValueError(
            "planner transition subcode must be MNCH, MNC2, or MNC3"
        )
    if candidate_mode not in {0, 1, 2, 3, 4, 5}:
        raise ValueError("planner candidate mode is outside fixture bounds")
    if flag_domain_mode not in range(7):
        raise ValueError("planner flag-domain mode is outside fixture bounds")
    if not 0 <= startup_delay_frames <= 10000:
        raise ValueError("planner startup delay is outside fixture bounds")
    if not 0 <= startup_state_override <= 0xFFFFFFFF:
        raise ValueError("planner startup state is outside u32")
    if acknowledgement_override is not None and (
        len(acknowledgement_override) != 2
        or any(
            not 0 <= value <= 0xFFFFFFFF
            for value in acknowledgement_override
        )
    ):
        raise ValueError("planner acknowledgement override is outside u32")
    root = Path(__file__).resolve().parents[3]
    compiler = shutil.which("arm-none-eabi-gcc")
    objcopy = shutil.which("arm-none-eabi-objcopy")
    if compiler is None or objcopy is None:
        raise RuntimeError("planner runtime toolchain unavailable")
    sources = (
        root / "src" / "action_semantics.c",
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
    compile_flags = [
        "-mcpu=arm7tdmi",
        "-marm",
        "-mthumb-interwork",
        "-mabi=aapcs",
        "-std=gnu89",
        "-ffreestanding",
        "-fno-builtin",
        "-fno-unwind-tables",
        "-fno-asynchronous-unwind-tables",
        "-nostdlib",
        "-O2",
        "-ffunction-sections",
        "-fdata-sections",
        "-I",
        str(root / "include"),
        "-I",
        str(root / "include" / "generated"),
        "-DFE8_EXPANSION_MODERN_BUILD=1",
        "-DFE8_EXPANSION_DEBUG=1",
        "-DFE8_EXPANSION_AUTOPLAY_PLANNER=1",
        "-DFE8_AUTOPLAY_PLANNER_RUNTIME_TEST=1",
        f"-DFE8_AUTOPLAY_PLANNER_RUNTIME_COMMIT_DELAY_FRAMES={commit_delay_frames}",
        "-DFE8_AUTOPLAY_PLANNER_RUNTIME_STALL_AFTER_COMMIT={}".format(
            int(stall_after_commit)
        ),
        "-DFE8_AUTOPLAY_PLANNER_RUNTIME_IGNORE_COMMANDS={}".format(
            int(ignore_commands)
        ),
        "-DFE8_AUTOPLAY_PLANNER_RUNTIME_TRANSITION_SUBCODE={}".format(
            transition_subcode
        ),
        f"-DFE8_AUTOPLAY_PLANNER_RUNTIME_CANDIDATE_MODE={candidate_mode}",
        f"-DFE8_AUTOPLAY_PLANNER_RUNTIME_FLAG_DOMAIN_MODE={flag_domain_mode}",
        "-DFE8_AUTOPLAY_PLANNER_RUNTIME_ACK_OVERRIDE={}".format(
            int(acknowledgement_override is not None)
        ),
        "-DFE8_AUTOPLAY_PLANNER_RUNTIME_ACK_RESULT={}u".format(
            0 if acknowledgement_override is None
            else acknowledgement_override[0]
        ),
        "-DFE8_AUTOPLAY_PLANNER_RUNTIME_ACK_REJECTION={}u".format(
            0 if acknowledgement_override is None
            else acknowledgement_override[1]
        ),
        "-DFE8_AUTOPLAY_PLANNER_RUNTIME_ZERO_DIGEST={}".format(
            int(zero_digest)
        ),
        "-DFE8_AUTOPLAY_PLANNER_RUNTIME_STARTUP_DELAY={}".format(
            startup_delay_frames
        ),
        "-DFE8_AUTOPLAY_PLANNER_RUNTIME_STARTUP_STATE={}".format(
            startup_state_override
        ),
        "-DFE8_AUTOPLAY_PLANNER_RUNTIME_MUTATE_SELECTED_ITEM={}".format(
            int(mutate_selected_item_before_commit)
        ),
    ]
    environment = dict(os.environ)
    environment["TMPDIR"] = str(path.parent)
    production_objects = []
    for source_name in ("event", "eventscr", "bmio", "bmtarget", "bm"):
        output = path.parent / f"planner-production-{source_name}.o"
        _run_checked(
            [
                compiler,
                *compile_flags,
                "-c",
                str(root / "src" / f"{source_name}.c"),
                "-o",
                str(output),
            ],
            root,
            environment,
        )
        production_objects.append(output)
    command = [
        compiler,
        *compile_flags,
        *map(str, sources),
        *map(str, production_objects),
        "-Wl,-T,{}".format(linker),
        "-Wl,--gc-sections",
        "-Wl,--wrap=EndEventFaces",
        "-Wl,--wrap=ReadGameSaveCoreGfx",
        "-Wl,--wrap=UnlockGame",
        "-lgcc",
        "-o",
        str(elf),
    ]
    _run_checked(command, root, environment)
    _run_checked(
        [objcopy, "-O", "binary", str(elf), str(path)],
        root,
        environment,
    )
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


def build_planner_transport_backend(
    path: Path,
    elf: Path,
    *,
    acknowledgement_frame_limit: int = 120,
    response_frame_limit: int = 600,
    commit_completion_frame_limit: int = 18000,
    wall_timeout_ms: int = 5000,
    test_bootstrap: bool = False,
    ready_mutation: tuple[int, int, bool] | None = None,
) -> None:
    """Build the fixed-symbol stdin/stdout libmGBA planner adapter."""
    if min(
        acknowledgement_frame_limit,
        response_frame_limit,
        commit_completion_frame_limit,
        wall_timeout_ms,
    ) <= 0:
        raise ValueError("planner transport frame limits must be positive")
    if wall_timeout_ms > 5000:
        raise ValueError("planner transport wall timeout exceeds five seconds")
    if ready_mutation is not None and (
        not test_bootstrap
        or len(ready_mutation) != 3
        or not 0 <= ready_mutation[0] < 256
        or not 0 <= ready_mutation[1] <= 0xFFFFFFFF
        or type(ready_mutation[2]) is not bool
    ):
        raise ValueError("READY mutation requires a bounded test bootstrap")
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
        f"-DPLANNER_COMMAND_ACK_FRAME_LIMIT={acknowledgement_frame_limit}u",
        f"-DPLANNER_COMMAND_RESPONSE_FRAME_LIMIT={response_frame_limit}u",
        "-DPLANNER_COMMIT_COMPLETION_FRAME_LIMIT={}u".format(
            commit_completion_frame_limit
        ),
        f"-DPLANNER_DECISION_WALL_TIMEOUT_MS={wall_timeout_ms}u",
        f"-DPLANNER_TRANSPORT_TEST_BOOTSTRAP={int(test_bootstrap)}",
        "-DPLANNER_READY_MUTATION_WORD={}".format(
            256 if ready_mutation is None else ready_mutation[0]
        ),
        "-DPLANNER_READY_MUTATION_VALUE={}u".format(
            0 if ready_mutation is None else ready_mutation[1]
        ),
        "-DPLANNER_READY_MUTATION_XOR={}".format(
            0 if ready_mutation is None else int(ready_mutation[2])
        ),
        str(root / "tools" / "gba-playtest" / "planner_transport_backend.c"),
        *(
            [
                str(
                    root
                    / "tools"
                    / "gba-playtest"
                    / "tests"
                    / "c"
                    / "planner_transport_bootstrap.c"
                )
            ]
            if test_bootstrap
            else []
        ),
        "-o",
        str(path),
    ]
    command.extend(_libmgba_flags())
    _run_checked(command, root)


def _libmgba_flags() -> list[str]:
    pkg_config = shutil.which("pkg-config")
    if pkg_config is not None:
        flags = subprocess.run(
            [pkg_config, "--cflags", "--libs", "mgba"],
            capture_output=True,
            text=True,
        )
        if flags.returncode == 0:
            return shlex.split(flags.stdout)
    return ["-lmgba"]


def build_planner_transport_ack_driver(path: Path) -> None:
    root = Path(__file__).resolve().parents[3]
    compiler = shutil.which(os.environ.get("CC", "cc"))
    if compiler is None:
        raise RuntimeError("planner transport host compiler unavailable")
    command = [
        compiler,
        "-std=gnu11",
        "-O2",
        "-Wall",
        "-Wextra",
        "-Werror",
        "-Wno-unused-function",
        "-I",
        str(root / "include"),
        "-I",
        str(root / "include" / "generated"),
        "-DPLANNER_OBSERVATION_ADDR=0u",
        "-DPLANNER_COMMAND_ADDR=0u",
        "-DPLANNER_CHECKPOINT_ADDR=0u",
        "-DPLANNER_TRANSPORT_NO_MAIN=1",
        "-DPLANNER_TRANSPORT_LINE_TEST=1",
        str(root / "tools" / "gba-playtest" / "planner_transport_backend.c"),
        str(
            root
            / "tools"
            / "gba-playtest"
            / "tests"
            / "c"
            / "planner_transport_ack_driver.c"
        ),
        "-o",
        str(path),
        *_libmgba_flags(),
    ]
    _run_checked(command, root)
