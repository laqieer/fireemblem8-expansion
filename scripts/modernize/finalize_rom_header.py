#!/usr/bin/env python3
"""Patch a built flat GBA ROM's header identity fields in place, then
regenerate the header checksum (issue #8, deliverable 6).

Given a ROM freshly produced by `objcopy` from the modern ELF (which still
carries whatever title/game code/maker code/revision src/rom_header.s
hardcoded), this script overwrites those four fields with the configured
identity (see config.mk EXPANSION_ROM_*), recomputes the checksum byte over
the resulting header, and writes both back in place. It shares the exact
offset/format constants and the checksum formula with
scripts/modernize/verify_rom_header.py (imported, not duplicated) and with
scripts/modernize/expansion_config.py's field validators, so all three tools
agree on the header layout by construction.

Every input is validated *before* any byte of the ROM is modified: on any
invalid title/game code/maker code/revision, the script exits non-zero and
leaves the ROM file completely untouched.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import expansion_config as ec  # noqa: E402
import verify_rom_header as vrh  # noqa: E402


class FinalizeRomHeaderError(Exception):
    """The requested identity fields are invalid, or the ROM could not be patched."""


def finalize_rom_header(
    rom_path: Path,
    title: str,
    game_code: str,
    maker_code: str,
    revision,
) -> int:
    """Patch title/game code/maker code/revision into rom_path and recompute
    the checksum. Returns the new checksum byte. Raises
    FinalizeRomHeaderError (without touching the file) if any field is
    invalid, or if the ROM is too small to contain a GBA header.
    """
    title = ec.validate_title(title)
    game_code = ec.validate_game_code(game_code)
    maker_code = ec.validate_maker_code(maker_code)
    revision = ec.validate_revision(revision)

    try:
        data = bytearray(rom_path.read_bytes())
    except OSError as error:
        raise FinalizeRomHeaderError(f"failed to read {rom_path}: {error}") from error

    if len(data) < vrh.HEADER_END:
        raise FinalizeRomHeaderError(
            f"{rom_path} is too small ({len(data)} bytes) to contain a GBA header"
        )

    title_bytes = title.encode("ascii").ljust(vrh.HEADER_TITLE_SIZE, b"\x00")
    data[vrh.HEADER_TITLE_OFFSET : vrh.HEADER_TITLE_OFFSET + vrh.HEADER_TITLE_SIZE] = title_bytes

    game_code_bytes = game_code.encode("ascii")
    data[
        vrh.HEADER_GAME_CODE_OFFSET : vrh.HEADER_GAME_CODE_OFFSET + vrh.HEADER_GAME_CODE_SIZE
    ] = game_code_bytes

    maker_code_bytes = maker_code.encode("ascii")
    data[
        vrh.HEADER_MAKER_CODE_OFFSET : vrh.HEADER_MAKER_CODE_OFFSET + vrh.HEADER_MAKER_CODE_SIZE
    ] = maker_code_bytes

    data[vrh.HEADER_SOFTWARE_VERSION_OFFSET] = revision

    checksum = vrh.compute_checksum(bytes(data))
    data[vrh.HEADER_CHECKSUM_OFFSET] = checksum

    try:
        rom_path.write_bytes(data)
    except OSError as error:
        raise FinalizeRomHeaderError(f"failed to write {rom_path}: {error}") from error

    return checksum


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("rom", type=Path, help="path to the flat GBA ROM to patch in place")
    parser.add_argument("--title", help="ROM title (see config.mk EXPANSION_ROM_TITLE)")
    parser.add_argument("--game-code", help="ROM game code (EXPANSION_ROM_GAME_CODE)")
    parser.add_argument("--maker-code", help="ROM maker code (EXPANSION_ROM_MAKER_CODE)")
    parser.add_argument("--revision", help="ROM revision byte (EXPANSION_ROM_REVISION)")
    parser.add_argument(
        "--metadata-json",
        type=Path,
        default=None,
        help=(
            "path to a generated expansion_build_metadata.json; supplies "
            "defaults for any of --title/--game-code/--maker-code/--revision "
            "that are not given explicitly"
        ),
    )
    args = parser.parse_args(argv)

    metadata = None
    if args.metadata_json is not None:
        try:
            metadata = json.loads(args.metadata_json.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            print(f"error: failed to read {args.metadata_json}: {error}", file=sys.stderr)
            return 1

    def resolve(cli_value, metadata_key, flag):
        if cli_value is not None:
            return cli_value
        if metadata is not None and metadata_key in metadata:
            return metadata[metadata_key]
        print(f"error: missing required value for {flag} (no --metadata-json fallback)", file=sys.stderr)
        return None

    title = resolve(args.title, "rom_title", "--title")
    game_code = resolve(args.game_code, "rom_game_code", "--game-code")
    maker_code = resolve(args.maker_code, "rom_maker_code", "--maker-code")
    revision = resolve(args.revision, "rom_revision", "--revision")
    if None in (title, game_code, maker_code, revision):
        return 1

    try:
        checksum = finalize_rom_header(args.rom, title, game_code, maker_code, revision)
    except (ec.ConfigError, FinalizeRomHeaderError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    print(
        f"ROM header finalized: {args.rom} (title={title!r} game_code={game_code!r} "
        f"maker_code={maker_code!r} revision={revision} checksum=0x{checksum:02X})"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
