#!/usr/bin/env python3
"""Build the FE6 SIO multiboot payload with the modern ARM GCC toolchain."""

from __future__ import annotations

import argparse
import os
import shlex
import shutil
import struct
import subprocess
from pathlib import Path
from typing import Iterable

EXPECTED_C_SOURCES = (
    "async_upload.c",
    "debug_text.c",
    "game_control.c",
    "gbasram.c",
    "graphics_data.c",
    "hardware.c",
    "interrupts.c",
    "main.c",
    "oam.c",
    "proc.c",
    "ramfunc.c",
    "report.c",
    "save.c",
    "sio.c",
    "sprite.c",
)

EXPECTED_ASM_SOURCES = (
    "armfunc.s",
    "crt0.s",
    "fake_glue.s",
    "gbasvc.s",
)

EXPECTED_SOURCES = EXPECTED_C_SOURCES + EXPECTED_ASM_SOURCES

CFLAGS = (
    "-mcpu=arm7tdmi",
    "-mthumb",
    "-mthumb-interwork",
    "-std=gnu11",
    "-fgnu89-inline",
    "-ffreestanding",
    "-fno-builtin",
    "-fno-common",
    "-fno-pic",
    "-fno-pie",
    "-DMODERN=1",
    "-DVER_FINAL",
    "-O2",
    "-g0",
)

ASFLAGS = (
    "-mcpu=arm7tdmi",
    "-mthumb-interwork",
)

EXPECTED_ENTRY = 0x02010000
EXPECTED_FIRST_WORD = 0xE3A00012

FINAL_EMBED_ASSETS = (
    ("data/debug_font.png", "embed/debug_font.4bpp", None),
    ("embed/debug_font.4bpp", "embed/debug_font.4bpp.u32", "u32"),
    ("data/message_gfx.png", "embed/message_gfx.4bpp", None),
    ("embed/message_gfx.4bpp", "embed/message_gfx.4bpp.lz", None),
    ("embed/message_gfx.4bpp.lz", "embed/message_gfx.4bpp.lz.u32", "u32"),
    ("data/message_gfx.png", "embed/message_gfx.gbapal", None),
    ("embed/message_gfx.gbapal", "embed/message_gfx.gbapal.u16", "u16"),
    ("data/message_tm_1.bin", "embed/message_tm_1.bin.lz", None),
    ("embed/message_tm_1.bin.lz", "embed/message_tm_1.bin.lz.u32", "u32"),
    ("data/message_tm_2.bin", "embed/message_tm_2.bin.lz", None),
    ("embed/message_tm_2.bin.lz", "embed/message_tm_2.bin.lz.u32", "u32"),
    ("data/message_tm_3.bin", "embed/message_tm_3.bin.lz", None),
    ("embed/message_tm_3.bin.lz", "embed/message_tm_3.bin.lz.u32", "u32"),
)

_CATCHALLS = """

    .text.catchall : ALIGN(4)
    {
        *(.text .text.* .glue_7 .glue_7t)
    } > LOADRAM

    .rodata.catchall : ALIGN(4)
    {
        *(.rodata .rodata.*)
    } > LOADRAM

    .data.catchall : ALIGN(4)
    {
        *(.data .data.*)
    } > LOADRAM

    .bss.catchall (NOLOAD) : ALIGN(4)
    {
        *(.bss .bss.*)
        *(COMMON)
    } > IWRAM

    ewram_data.catchall (NOLOAD) : ALIGN(4)
    {
        *(ewram_data ewram_data.*)
    } > EWRAM
"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--submodule-root", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--cc", required=True)
    parser.add_argument("--ld", required=True)
    parser.add_argument("--objcopy", required=True)
    parser.add_argument("--gbagfx", required=True)
    parser.add_argument("--binutils-dir")
    parser.add_argument("--newlib-lib")
    return parser.parse_args()


def run(command: list[str], *, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        command,
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"command failed ({result.returncode}): {shlex.join(command)}\n"
            f"{result.stdout}"
        )
    return result


def gcc_command(cc: str, binutils_dir: str | None, *args: str) -> list[str]:
    command = [cc]
    if binutils_dir:
        command.append(_binutils_flag(binutils_dir))
    command.extend(args)
    return command


def _binutils_flag(binutils_dir: str) -> str:
    value = os.fspath(Path(binutils_dir))
    if not value.endswith(os.sep):
        value += os.sep
    return f"-B{value}"


def _ld_quote(path: Path) -> str:
    text = path.as_posix()
    if '"' in text or "\n" in text:
        raise ValueError(f"unsupported linker path: {text!r}")
    if " " in text:
        return f'"{text}"'
    return text


def validate_source_list(submodule_root: Path) -> tuple[list[Path], list[Path]]:
    src_dir = submodule_root / "src"
    actual_c = sorted(path.name for path in src_dir.glob("*.c"))
    actual_asm = sorted(path.name for path in src_dir.glob("*.s"))
    expected_c = sorted(EXPECTED_C_SOURCES)
    expected_asm = sorted(EXPECTED_ASM_SOURCES)

    if actual_c != expected_c:
        raise ValueError(
            "mgfembp C source list mismatch: "
            f"expected {expected_c}, got {actual_c}"
        )
    if actual_asm != expected_asm:
        raise ValueError(
            "mgfembp asm source list mismatch: "
            f"expected {expected_asm}, got {actual_asm}"
        )

    return ([src_dir / name for name in EXPECTED_C_SOURCES],
            [src_dir / name for name in EXPECTED_ASM_SOURCES])


def generate_linker_script_text(lds_text: str, output_dir: Path) -> str:
    result = lds_text
    for source_name in EXPECTED_SOURCES:
        object_name = source_name.rsplit('.', 1)[0] + '.o'
        result = result.replace(
            f"src/{object_name}",
            _ld_quote(output_dir / object_name),
        )

    glue_path = _ld_quote(output_dir / "*.o")
    result = result.replace(
        "src/*.o(fake_glue)",
        f"{glue_path}(fake_glue)",
    )
    result = result.replace(
        "*libc.a:memcpy.o(.text)",
        "*libc.a:libc_a-memcpy.o(.text .text.*)",
    )
    result = result.replace(
        "*libgcc.a:_call_via_rX.o(.text)",
        "*libgcc.a:_call_via_rX.o(.text .text.*)",
    )
    result = result.replace(
        "*libgcc.a:_divsi3.o(.text)",
        "*libgcc.a:_divsi3.o(.text .text.*)",
    )
    result = result.replace(
        "*libgcc.a:_dvmd_tls.o(.text)",
        "*libgcc.a:_dvmd_tls.o(.text .text.*)",
    )
    result = result.replace(
        "*libgcc.a:_modsi3.o(.text)",
        "*libgcc.a:_modsi3.o(.text .text.*)",
    )
    result = result.replace(
        "*libgcc.a:_udivsi3.o(.text)",
        "*libgcc.a:_udivsi3.o(.text .text.*)",
    )
    result = result.replace(
        "*libgcc.a:_umodsi3.o(.text)",
        "*libgcc.a:_umodsi3.o(.text .text.*)",
    )

    marker = "\n    /DISCARD/"
    if marker not in result:
        raise ValueError("expected /DISCARD/ section in linker script")
    result = result.replace(marker, _CATCHALLS + marker, 1)
    return result


def write_linker_script(submodule_root: Path, output_dir: Path) -> Path:
    lds_path = submodule_root / "mgfembp.lds"
    output_path = output_dir / "mgfembp.modern.lds"
    output_path.write_text(
        generate_linker_script_text(lds_path.read_text(encoding="utf-8"), output_dir),
        encoding="utf-8",
    )
    return output_path


def query_cc_path(cc: str, binutils_dir: str | None, *args: str) -> Path:
    result = run(gcc_command(cc, binutils_dir, *args))
    resolved = result.stdout.strip()
    path = Path(resolved)
    if not resolved or path.name == resolved:
        raise ValueError(f"compiler could not resolve {' '.join(args)}")
    return path


def discover_library_dirs(
    cc: str,
    binutils_dir: str | None,
    newlib_lib: str | None,
) -> list[Path]:
    libgcc_a = query_cc_path(cc, binutils_dir, "-mcpu=arm7tdmi", "-mthumb", "-mthumb-interwork", "-print-libgcc-file-name")
    if newlib_lib:
        libc_dir = Path(newlib_lib)
        libnosys_dir = Path(newlib_lib)
    else:
        libc_dir = query_cc_path(cc, binutils_dir, "-mcpu=arm7tdmi", "-mthumb", "-mthumb-interwork", "-print-file-name=libc.a").parent
        libnosys_dir = query_cc_path(cc, binutils_dir, "-mcpu=arm7tdmi", "-mthumb", "-mthumb-interwork", "-print-file-name=libnosys.a").parent

    lib_dirs: list[Path] = []
    for directory in (libgcc_a.parent, libc_dir, libnosys_dir):
        if not directory.is_dir():
            raise ValueError(f"library directory not found: {directory}")
        if directory not in lib_dirs:
            lib_dirs.append(directory)
    return lib_dirs


def discover_tool(cc: str, binutils_dir: str | None, tool_name: str) -> Path:
    result = run(gcc_command(cc, binutils_dir, f"-print-prog-name={tool_name}"))
    reported = result.stdout.strip()
    candidates: list[Path] = []

    if reported:
        reported_path = Path(reported)
        if reported_path.is_absolute():
            candidates.append(reported_path)
        if binutils_dir:
            candidates.append(Path(binutils_dir) / reported_path.name)
        candidates.append(Path(cc).resolve().parent / reported_path.name)
        found = shutil.which(reported)
        if found:
            candidates.append(Path(found))

    if binutils_dir:
        candidates.append(Path(binutils_dir) / tool_name)

    for candidate in candidates:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate

    raise ValueError(f"could not resolve tool: {tool_name}")


def write_embed_file(input_path: Path, output_path: Path, unit_kind: str) -> None:
    unit_size = {"u16": 2, "u32": 4}[unit_kind]
    units_per_line = {"u16": 12, "u32": 8}[unit_kind]
    fmt = {2: "0x%04X", 4: "0x%08X"}[unit_size]

    data = input_path.read_bytes()
    if len(data) % unit_size != 0:
        raise ValueError(
            f"{input_path} size {len(data)} is not a multiple of {unit_size}"
        )

    with output_path.open("w", encoding="utf-8") as handle:
        for start in range(0, len(data), unit_size * units_per_line):
            chunk = data[start:start + unit_size * units_per_line]
            values = []
            for offset in range(0, len(chunk), unit_size):
                value = int.from_bytes(chunk[offset:offset + unit_size], "little")
                values.append(f" {fmt % value},")
            handle.write("   " + "".join(values) + "\n")


def build_embed_assets(submodule_root: Path, output_dir: Path, gbagfx: str) -> None:
    for source_name, output_name, unit_kind in FINAL_EMBED_ASSETS:
        source_path = (
            output_dir / source_name if source_name.startswith("embed/")
            else submodule_root / source_name
        )
        output_path = output_dir / output_name
        output_path.parent.mkdir(parents=True, exist_ok=True)
        if unit_kind is None:
            run([gbagfx, str(source_path), str(output_path)])
        else:
            write_embed_file(source_path, output_path, unit_kind)


def compile_c_sources(cc: str, binutils_dir: str | None, submodule_root: Path, output_dir: Path, sources: Iterable[Path]) -> list[Path]:
    objects: list[Path] = []
    include_flags = [
        f"-I{submodule_root / 'include'}",
        f"-I{submodule_root}",
        f"-I{output_dir}",
    ]
    for source in sources:
        obj = output_dir / f"{source.stem}.o"
        command = gcc_command(cc, binutils_dir, *(CFLAGS + tuple(include_flags)), "-c", str(source), "-o", str(obj))
        run(command)
        objects.append(obj)
    return objects


def compile_asm_sources(cc: str, binutils_dir: str | None, submodule_root: Path, output_dir: Path, sources: Iterable[Path]) -> list[Path]:
    objects: list[Path] = []
    include_flags = [f"-I{submodule_root / 'asm' / 'include'}", f"-I{submodule_root / 'include'}"]
    for source in sources:
        obj = output_dir / f"{source.stem}.o"
        command = gcc_command(cc, binutils_dir, *(ASFLAGS + tuple(include_flags)), "-c", str(source), "-o", str(obj))
        run(command)
        objects.append(obj)
    return objects


def link_payload(ld: str, linker_script: Path, map_path: Path, elf_path: Path, objects: Iterable[Path], library_dirs: Iterable[Path]) -> None:
    command = [
        ld,
        "--no-undefined",
        "-T",
        str(linker_script),
        "-Map",
        str(map_path),
    ]
    command.extend(str(path) for path in objects)
    for directory in library_dirs:
        command.extend(["-L", str(directory)])
    command.extend(["-o", str(elf_path), "-lc", "-lnosys", "-lgcc"])
    run(command)


def read_elf_entry(elf_path: Path) -> int:
    header = elf_path.read_bytes()[:52]
    if len(header) < 52 or header[:4] != b"ELF":
        raise ValueError("output is not an ELF32 file")
    if header[4] != 1 or header[5] != 1:
        raise ValueError("expected 32-bit little-endian ELF")
    return struct.unpack_from("<I", header, 24)[0]


def verify_undefined_symbols(cc: str, binutils_dir: str | None, elf_path: Path) -> None:
    nm = discover_tool(cc, binutils_dir, "nm")
    result = run([str(nm), "-u", str(elf_path)])
    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    if lines:
        raise ValueError(f"undefined symbols remain: {lines}")


def objcopy_binary(objcopy: str, elf_path: Path, bin_path: Path) -> None:
    run([objcopy, "--strip-debug", "-O", "binary", str(elf_path), str(bin_path)])


def read_first_word(path: Path) -> int:
    data = path.read_bytes()
    if len(data) < 4:
        raise ValueError(f"binary too small: {path}")
    return struct.unpack_from("<I", data, 0)[0]


def compress_payload(gbagfx: str, bin_path: Path, output_path: Path) -> None:
    run([gbagfx, str(bin_path), str(output_path), "-mindist", "1"])


def build_payload(args: argparse.Namespace) -> None:
    submodule_root = Path(args.submodule_root).resolve()
    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    c_sources, asm_sources = validate_source_list(submodule_root)
    build_embed_assets(submodule_root, output_dir, args.gbagfx)
    linker_script = write_linker_script(submodule_root, output_dir)

    objects = []
    objects.extend(compile_c_sources(args.cc, args.binutils_dir, submodule_root, output_dir, c_sources))
    objects.extend(compile_asm_sources(args.cc, args.binutils_dir, submodule_root, output_dir, asm_sources))

    library_dirs = discover_library_dirs(args.cc, args.binutils_dir, args.newlib_lib)
    elf_path = output_dir / "fe6sio_payload.elf"
    map_path = output_dir / "fe6sio_payload.map"
    bin_path = output_dir / "fe6sio_payload.bin"
    lz_path = output_dir / "fe6sio_payload.bin.lz"

    link_payload(args.ld, linker_script, map_path, elf_path, objects, library_dirs)
    verify_undefined_symbols(args.cc, args.binutils_dir, elf_path)

    entry = read_elf_entry(elf_path)
    if entry != EXPECTED_ENTRY:
        raise ValueError(f"unexpected entry point: 0x{entry:08X}")

    objcopy_binary(args.objcopy, elf_path, bin_path)

    first_word = read_first_word(bin_path)
    if first_word != EXPECTED_FIRST_WORD:
        raise ValueError(f"unexpected first word: 0x{first_word:08X}")

    compress_payload(args.gbagfx, bin_path, lz_path)


def main() -> int:
    args = parse_args()
    build_payload(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
