#!/usr/bin/env python3
"""Capture deterministic build evidence for the final matching FE8 build."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import struct
import subprocess
import sys
from pathlib import Path
from typing import Any


class BaselineError(Exception):
    """An input or environment cannot produce trustworthy baseline evidence."""


REGIONS = {
    "ewram": (0x02000000, 256 * 1024),
    "iwram": (0x03000000, 32 * 1024),
    "rom": (0x08000000, 32 * 1024 * 1024),
}
SHT_NOBITS = 8
SHT_SYMTAB = 2
SHF_ALLOC = 2


def sha1_file(path: Path) -> str:
    digest = hashlib.sha1()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def decode_header_field(raw: bytes, label: str) -> str:
    value = raw.rstrip(b"\0 ")
    if any(byte < 0x20 or byte > 0x7E for byte in value):
        raise BaselineError(f"ROM header {label} is not printable ASCII")
    return value.decode("ascii")


def parse_rom(path: Path) -> dict[str, Any]:
    size = path.stat().st_size
    if size < 0xC0:
        raise BaselineError("ROM is too small to contain a GBA header")
    with path.open("rb") as stream:
        stream.seek(0xA0)
        header = stream.read(0x1D)
    if len(header) != 0x1D:
        raise BaselineError("ROM header is truncated")
    return {
        "header": {
            "game_code": decode_header_field(header[12:16], "game code"),
            "maker_code": decode_header_field(header[16:18], "maker code"),
            "revision": header[28],
            "title": decode_header_field(header[0:12], "title"),
        },
        "sha1": sha1_file(path),
        "size_bytes": size,
    }


def bounded_slice(data: bytes, offset: int, size: int, label: str) -> bytes:
    if offset < 0 or size < 0 or offset + size > len(data):
        raise BaselineError(f"ELF {label} extends past end of file")
    return data[offset : offset + size]


def c_string(table: bytes, offset: int, label: str) -> str:
    if offset >= len(table):
        raise BaselineError(f"ELF {label} string offset is out of range")
    end = table.find(b"\0", offset)
    if end < 0:
        raise BaselineError(f"ELF {label} string is unterminated")
    try:
        return table[offset:end].decode("utf-8")
    except UnicodeDecodeError as error:
        raise BaselineError(f"ELF {label} is not UTF-8") from error


def parse_elf(path: Path) -> tuple[dict[str, Any], list[dict[str, int | str]]]:
    data = path.read_bytes()
    if len(data) < 52 or data[:4] != b"\x7fELF":
        raise BaselineError("ELF input has an invalid ELF header")
    if data[4] != 1 or data[5] != 1:
        raise BaselineError("ELF input must be 32-bit little-endian")

    fields = struct.unpack_from("<HHIIIIIHHHHHH", data, 16)
    (
        _elf_type,
        machine,
        version,
        entry,
        _program_offset,
        section_offset,
        _flags,
        header_size,
        _program_entry_size,
        _program_count,
        section_entry_size,
        section_count,
        section_names_index,
    ) = fields
    if version != 1 or header_size < 52:
        raise BaselineError("ELF input has an unsupported header version")
    if section_count == 0 or section_entry_size < 40:
        raise BaselineError("ELF input has no usable section table")
    if section_names_index >= section_count:
        raise BaselineError("ELF section-name table index is out of range")
    bounded_slice(
        data,
        section_offset,
        section_entry_size * section_count,
        "section table",
    )

    sections: list[dict[str, int | str]] = []
    for index in range(section_count):
        offset = section_offset + index * section_entry_size
        values = struct.unpack_from("<IIIIIIIIII", data, offset)
        sections.append(
            {
                "name_offset": values[0],
                "type": values[1],
                "flags": values[2],
                "address": values[3],
                "offset": values[4],
                "size": values[5],
                "link": values[6],
                "info": values[7],
                "alignment": values[8],
                "entry_size": values[9],
            }
        )

    names_section = sections[section_names_index]
    names = bounded_slice(
        data,
        int(names_section["offset"]),
        int(names_section["size"]),
        "section-name table",
    )
    for section in sections:
        section["name"] = c_string(
            names, int(section["name_offset"]), "section name"
        )
        if section["type"] != SHT_NOBITS:
            bounded_slice(
                data,
                int(section["offset"]),
                int(section["size"]),
                f"section {section['name']!r}",
            )

    alloc_sections = [
        {
            "address": int(section["address"]),
            "name": str(section["name"]),
            "size_bytes": int(section["size"]),
            "type": "nobits" if section["type"] == SHT_NOBITS else "progbits",
        }
        for section in sections
        if int(section["flags"]) & SHF_ALLOC and int(section["size"]) > 0
    ]
    alloc_sections.sort(key=lambda item: (int(item["address"]), str(item["name"])))

    symbol_counts = {
        "bindings": {"global": 0, "local": 0, "other": 0, "weak": 0},
        "defined": 0,
        "named": 0,
        "types": {
            "file": 0,
            "function": 0,
            "no_type": 0,
            "object": 0,
            "other": 0,
            "section": 0,
        },
        "undefined": 0,
    }
    binding_names = {0: "local", 1: "global", 2: "weak"}
    type_names = {
        0: "no_type",
        1: "object",
        2: "function",
        3: "section",
        4: "file",
    }
    for section in sections:
        if section["type"] != SHT_SYMTAB:
            continue
        entry_size = int(section["entry_size"])
        if entry_size < 16 or int(section["size"]) % entry_size:
            raise BaselineError("ELF symbol table has an invalid entry size")
        link = int(section["link"])
        if link >= len(sections):
            raise BaselineError("ELF symbol string-table index is out of range")
        strings_section = sections[link]
        strings = bounded_slice(
            data,
            int(strings_section["offset"]),
            int(strings_section["size"]),
            "symbol string table",
        )
        table = bounded_slice(
            data,
            int(section["offset"]),
            int(section["size"]),
            "symbol table",
        )
        for offset in range(0, len(table), entry_size):
            name_offset, _value, _size, info, _other, section_index = (
                struct.unpack_from("<IIIBBH", table, offset)
            )
            name = c_string(strings, name_offset, "symbol name")
            if not name:
                continue
            symbol_counts["named"] += 1
            symbol_counts["defined" if section_index else "undefined"] += 1
            binding = binding_names.get(info >> 4, "other")
            symbol_type = type_names.get(info & 0xF, "other")
            symbol_counts["bindings"][binding] += 1
            symbol_counts["types"][symbol_type] += 1

    return (
        {
            "entry_address": entry,
            "machine": machine,
            "sections": alloc_sections,
            "sha1": hashlib.sha1(data).hexdigest(),
            "size_bytes": len(data),
            "symbols": symbol_counts,
        },
        sections,
    )


MAP_SECTION_RE = re.compile(
    r"^([A-Za-z_.][A-Za-z0-9_.]*)(?:[ \t]+|\r?\n[ \t]+)"
    r"0x([0-9a-fA-F]+)[ \t]+0x([0-9a-fA-F]+)[ \t]*$",
    re.MULTILINE,
)


def parse_map(path: Path) -> tuple[dict[str, Any], dict[str, dict[str, int]]]:
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as error:
        raise BaselineError("map input is not UTF-8 text") from error
    if "Linker script and memory map" not in text:
        raise BaselineError("map input lacks a GNU ld memory-map marker")
    sections: dict[str, dict[str, int]] = {}
    for match in MAP_SECTION_RE.finditer(text):
        name = match.group(1)
        if name in sections:
            raise BaselineError(f"map output section {name!r} is duplicated")
        sections[name] = {
            "address": int(match.group(2), 16),
            "size_bytes": int(match.group(3), 16),
        }
    if not sections:
        raise BaselineError("map input contains no output sections")
    return (
        {
            "output_sections": sections,
            "sha1": sha1_file(path),
            "size_bytes": path.stat().st_size,
        },
        sections,
    )


def memory_usage(
    alloc_sections: list[dict[str, int | str]], rom_size: int
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for name, (origin, capacity) in REGIONS.items():
        intervals: list[tuple[int, int]] = []
        allocated = 0
        for section in alloc_sections:
            address = int(section["address"])
            size = int(section["size_bytes"])
            if origin <= address < origin + capacity:
                if address + size > origin + capacity:
                    raise BaselineError(
                        f"ELF section {section['name']!r} exceeds {name} capacity"
                    )
                intervals.append((address, address + size))
                allocated += size
        intervals.sort()
        merged: list[list[int]] = []
        for start, end in intervals:
            if merged and start <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], end)
            else:
                merged.append([start, end])
        region = {
            "capacity_bytes": capacity,
            "high_watermark_bytes": (
                max((end for _, end in intervals), default=origin) - origin
            ),
            "occupied_bytes": sum(end - start for start, end in merged),
            "section_bytes_including_overlays": allocated,
        }
        if name == "rom":
            region["image_size_bytes"] = rom_size
        result[name] = region
    return result


def command_version(command: list[str], label: str) -> str:
    try:
        process = subprocess.run(
            command,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
    except OSError as error:
        raise BaselineError(f"cannot execute required tool {label}") from error
    first_line = process.stdout.splitlines()[0] if process.stdout else ""
    versions = re.findall(r"\b\d+\.\d+(?:\.\d+)*\b", first_line)
    if not versions:
        raise BaselineError(f"cannot normalize version for required tool {label}")
    return versions[-1]


def agbcc_version(path: Path, label: str) -> str:
    data = path.read_bytes()
    match = re.search(rb"\b\d+\.\d+-arm-\d+\b", data)
    if not match:
        raise BaselineError(f"cannot find embedded version in {label}")
    return match.group(0).decode("ascii")


def tool_versions(root: Path, prefix: str) -> dict[str, str]:
    return {
        "agbcc": agbcc_version(root / "tools/agbcc/bin/agbcc", "agbcc"),
        "arm_binutils_as": command_version([prefix + "as", "--version"], "assembler"),
        "arm_binutils_ld": command_version([prefix + "ld", "--version"], "linker"),
        "arm_binutils_objcopy": command_version(
            [prefix + "objcopy", "--version"], "objcopy"
        ),
        "arm_binutils_strip": command_version(
            [prefix + "strip", "--version"], "strip"
        ),
        "make": command_version(["make", "--version"], "make"),
        "old_agbcc": agbcc_version(
            root / "tools/agbcc/bin/old_agbcc", "old_agbcc"
        ),
        "python": ".".join(str(part) for part in sys.version_info[:3]),
    }


def source_commit(root: Path) -> str:
    command = [
        "git",
        "rev-list",
        "-1",
        "HEAD",
        "--",
        ".",
        ":(exclude)scripts/baseline/**",
        ":(exclude)reports/baseline/**",
    ]
    try:
        commit = subprocess.run(
            command,
            cwd=root,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError) as error:
        raise BaselineError("cannot determine source commit from git") from error
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise BaselineError("git returned an invalid source commit")
    return commit


def ensure_file(path: Path, label: str) -> None:
    if not path.is_file():
        raise BaselineError(f"{label} input is missing or not a regular file")


def capture(
    root: Path, rom_path: Path, elf_path: Path, map_path: Path, prefix: str
) -> dict[str, Any]:
    for path, label in (
        (rom_path, "ROM"),
        (elf_path, "ELF"),
        (map_path, "map"),
    ):
        ensure_file(path, label)
    rom = parse_rom(rom_path)
    elf, raw_sections = parse_elf(elf_path)
    map_evidence, map_sections = parse_map(map_path)

    for section in raw_sections:
        name = str(section["name"])
        if not (int(section["flags"]) & SHF_ALLOC) or int(section["size"]) == 0:
            continue
        if name not in map_sections:
            raise BaselineError(f"map lacks ELF allocatable section {name!r}")
        expected = map_sections[name]
        if (
            int(section["address"]) != expected["address"]
            or int(section["size"]) != expected["size_bytes"]
        ):
            raise BaselineError(f"ELF/map disagree on output section {name!r}")

    return {
        "elf": elf,
        "map": map_evidence,
        "memory": memory_usage(elf["sections"], rom["size_bytes"]),
        "rom": rom,
        "schema_version": 1,
        "source": {"commit": source_commit(root)},
        "tools": tool_versions(root, prefix),
    }


def serialize(manifest: dict[str, Any]) -> bytes:
    return (
        json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=True) + "\n"
    ).encode("utf-8")


def run_build(root: Path, jobs: int) -> None:
    if jobs < 1:
        raise BaselineError("--jobs must be at least 1")
    try:
        subprocess.run(
            ["make", "clean"], cwd=root, check=True, stdout=sys.stderr
        )
        subprocess.run(
            ["make", "compare", f"-j{jobs}"],
            cwd=root,
            check=True,
            stdout=sys.stderr,
        )
    except (OSError, subprocess.CalledProcessError) as error:
        raise BaselineError("clean/build/compare failed") from error


def resolve_input(root: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else root / path


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    default_root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build", action="store_true", help="clean and run make compare")
    parser.add_argument("--elf", default="fireemblem8.elf")
    parser.add_argument("--jobs", type=int, default=os.cpu_count() or 1)
    parser.add_argument("--map", dest="map_file", default="fireemblem8.map")
    parser.add_argument("--output", default="-", help="JSON file, or - for stdout")
    parser.add_argument("--prefix", default="arm-none-eabi-")
    parser.add_argument("--rom", default="fireemblem8.gba")
    parser.add_argument("--root", type=Path, default=default_root)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        root = args.root.resolve()
        if args.build:
            run_build(root, args.jobs)
        manifest = capture(
            root,
            resolve_input(root, args.rom),
            resolve_input(root, args.elf),
            resolve_input(root, args.map_file),
            args.prefix,
        )
        output = serialize(manifest)
        if args.output == "-":
            sys.stdout.buffer.write(output)
        else:
            destination = resolve_input(root, args.output)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(output)
    except (BaselineError, OSError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
