#!/usr/bin/env python3

import argparse
import hashlib
import json
import struct
from pathlib import Path

from scripts.texttools.legacy_text_source import build_legacy_source


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def allocated_sections(path: Path) -> dict[str, tuple[int, bytes]]:
    data = path.read_bytes()
    if data[:4] != b"\x7fELF" or data[4] != 1:
        raise ValueError(f"{path} is not an ELF32 object")

    byte_order = {1: "<", 2: ">"}.get(data[5])
    if byte_order is None:
        raise ValueError(f"{path} has an unsupported ELF byte order")

    section_offset = struct.unpack_from(byte_order + "I", data, 32)[0]
    section_entry_size = struct.unpack_from(byte_order + "H", data, 46)[0]
    section_count = struct.unpack_from(byte_order + "H", data, 48)[0]
    name_table_index = struct.unpack_from(byte_order + "H", data, 50)[0]
    if section_entry_size < 40 or name_table_index >= section_count:
        raise ValueError(f"{path} has an invalid ELF section table")

    headers = []
    for index in range(section_count):
        offset = section_offset + index * section_entry_size
        headers.append(struct.unpack_from(byte_order + "10I", data, offset))

    name_header = headers[name_table_index]
    names = data[name_header[4] : name_header[4] + name_header[5]]
    sections = {}

    for header in headers:
        name_offset, section_type, flags, _, offset, size, _, _, _, _ = header
        if not flags & 2:
            continue

        name_end = names.find(b"\0", name_offset)
        if name_end < 0:
            raise ValueError(f"{path} has an invalid ELF section name")

        name = names[name_offset:name_end].decode("ascii")
        payload = b"" if section_type == 8 else data[offset : offset + size]
        sections[name] = (size, payload)

    return sections


def object_fingerprint(path: Path) -> dict[str, dict[str, object]]:
    return {
        section: {"size": size, "sha256": sha256(payload)}
        for section, (size, payload) in sorted(allocated_sections(path).items())
    }


def source_fingerprints(root: Path) -> dict[str, str]:
    legacy_text = build_legacy_source((root / "texts/texts.txt").read_bytes())
    return {
        "legacy_text_source_sha256": sha256(legacy_text),
        "legacy_config_icons_sha256": sha256(
            (root / "graphics/misc/Img_ConfigUiIcons.legacy.png").read_bytes()
        ),
    }


def expected_rom_sha1(root: Path) -> str:
    baseline = json.loads((root / "reports/baseline/baseline.json").read_text())
    if baseline["source"]["commit"] != "a23ff74d824acc997621114d1f31e6f3d206653e":
        raise ValueError("baseline source commit is not the pinned archival comparison")

    return baseline["rom"]["sha1"]


def build_manifest(
    root: Path,
    object_paths: list[str],
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "sources": source_fingerprints(root),
        "objects": {
            path: object_fingerprint(root / path)
            for path in object_paths
        },
    }


def verify(root: Path, manifest_path: Path, rom_path: Path) -> None:
    manifest = json.loads(manifest_path.read_text())
    if manifest.get("schema_version") != 1:
        raise ValueError("unsupported archival identity manifest schema")

    actual_sources = source_fingerprints(root)
    if actual_sources != manifest["sources"]:
        raise ValueError(
            f"archival source identity mismatch: expected {manifest['sources']}, "
            f"got {actual_sources}"
        )

    failures = []
    for path, expected in manifest["objects"].items():
        actual = object_fingerprint(root / path)
        if actual != expected:
            failures.append(path)

    if failures:
        raise ValueError(
            "archival object identity mismatch: " + ", ".join(failures)
        )

    actual_rom_sha1 = hashlib.sha1(rom_path.read_bytes()).hexdigest()
    expected_sha1 = expected_rom_sha1(root)
    if actual_rom_sha1 != expected_sha1:
        raise ValueError(
            f"archival ROM identity mismatch: expected {expected_sha1}, "
            f"got {actual_rom_sha1}"
        )

    print(
        f"archival identity: source, object, and ROM checks passed ({expected_sha1})"
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--rom", type=Path, default=Path("fireemblem8.gba"))
    parser.add_argument("--write", nargs="*", metavar="OBJECT")
    args = parser.parse_args()

    root = Path(__file__).resolve().parent.parent
    manifest_path = args.manifest
    if not manifest_path.is_absolute():
        manifest_path = root / manifest_path

    if args.write is not None:
        manifest = build_manifest(root, args.write)
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
        return 0

    rom_path = args.rom if args.rom.is_absolute() else root / args.rom
    verify(root, manifest_path, rom_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
