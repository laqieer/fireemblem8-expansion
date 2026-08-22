#!/usr/bin/env python3
"""Deterministic, stdlib-only BPS producer and verifier.

The encoder emits aligned SourceRead runs for unchanged bytes and TargetRead
runs only for changes. This keeps the encoding non-heuristic while ensuring a
patch cannot reconstruct its output without the exact source image. The
applier implements all four BPS action types so it verifies standard BPS files
as well.
"""

from __future__ import annotations

import argparse
import struct
import sys
import zlib
from pathlib import Path


MAGIC = b"BPS1"
IDENTITY = "stdlib-bps-source-target-read-v1"
ACTION_SOURCE_READ = 0
ACTION_TARGET_READ = 1
ACTION_SOURCE_COPY = 2
ACTION_TARGET_COPY = 3


class BpsError(ValueError):
    """A BPS stream or its source/target checksum is invalid."""


def crc32(data: bytes) -> int:
    return zlib.crc32(data) & 0xFFFFFFFF


def encode_number(value: int) -> bytes:
    if value < 0:
        raise BpsError("negative BPS number")
    result = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value == 0:
            result.append(byte | 0x80)
            return bytes(result)
        result.append(byte)
        value -= 1


def decode_number(data: bytes, offset: int) -> tuple[int, int]:
    value = 0
    shift = 1
    while True:
        if offset >= len(data):
            raise BpsError("truncated BPS number")
        byte = data[offset]
        offset += 1
        value += (byte & 0x7F) * shift
        if byte & 0x80:
            return value, offset
        shift <<= 7
        value += shift


def decode_signed(value: int) -> int:
    return -(value >> 1) if value & 1 else value >> 1


def create_patch(source: bytes, target: bytes, metadata: bytes = b"") -> bytes:
    """Create a deterministic, portable BPS patch for source -> target."""
    body = bytearray(MAGIC)
    body += encode_number(len(source))
    body += encode_number(len(target))
    body += encode_number(len(metadata))
    body += metadata

    offset = 0
    while offset < len(target):
        source_read = offset < len(source) and source[offset] == target[offset]
        start = offset
        offset += 1
        while offset < len(target):
            unchanged = offset < len(source) and source[offset] == target[offset]
            if unchanged != source_read:
                break
            offset += 1

        length = offset - start
        action = ACTION_SOURCE_READ if source_read else ACTION_TARGET_READ
        body += encode_number(((length - 1) << 2) | action)
        if action == ACTION_TARGET_READ:
            body += target[start:offset]

    body += struct.pack("<II", crc32(source), crc32(target))
    body += struct.pack("<I", crc32(bytes(body)))
    return bytes(body)


def apply_patch(source: bytes, patch: bytes) -> bytes:
    """Apply and fully validate a BPS patch without reading or writing paths."""
    if len(patch) < len(MAGIC) + 12 or patch[:4] != MAGIC:
        raise BpsError("invalid BPS header")
    if crc32(patch[:-4]) != struct.unpack_from("<I", patch, len(patch) - 4)[0]:
        raise BpsError("BPS patch checksum mismatch")

    offset = len(MAGIC)
    source_size, offset = decode_number(patch, offset)
    target_size, offset = decode_number(patch, offset)
    metadata_size, offset = decode_number(patch, offset)
    if source_size != len(source):
        raise BpsError("BPS source size mismatch")
    if offset + metadata_size > len(patch) - 12:
        raise BpsError("truncated BPS metadata")
    offset += metadata_size

    target = bytearray()
    source_relative = 0
    target_relative = 0
    actions_end = len(patch) - 12
    while len(target) < target_size:
        if offset >= actions_end:
            raise BpsError("truncated BPS actions")
        encoded, offset = decode_number(patch, offset)
        action = encoded & 3
        length = (encoded >> 2) + 1
        if len(target) + length > target_size:
            raise BpsError("BPS action overruns target")

        if action == ACTION_SOURCE_READ:
            start = len(target)
            end = start + length
            if end > len(source):
                raise BpsError("BPS SourceRead exceeds source")
            target += source[start:end]
        elif action == ACTION_TARGET_READ:
            if offset + length > actions_end:
                raise BpsError("truncated BPS TargetRead")
            target += patch[offset : offset + length]
            offset += length
        elif action in (ACTION_SOURCE_COPY, ACTION_TARGET_COPY):
            relative, offset = decode_number(patch, offset)
            if action == ACTION_SOURCE_COPY:
                source_relative += decode_signed(relative)
                end = source_relative + length
                if source_relative < 0 or end > len(source):
                    raise BpsError("BPS SourceCopy exceeds source")
                target += source[source_relative:end]
                source_relative = end
            else:
                target_relative += decode_signed(relative)
                if target_relative < 0:
                    raise BpsError("BPS TargetCopy has negative offset")
                for _ in range(length):
                    if target_relative >= len(target):
                        raise BpsError("BPS TargetCopy exceeds target")
                    target.append(target[target_relative])
                    target_relative += 1
        else:
            raise BpsError("unknown BPS action")

    if offset != actions_end:
        raise BpsError("trailing BPS action data")
    source_crc, target_crc, _patch_crc = struct.unpack_from("<III", patch, actions_end)
    if crc32(source) != source_crc:
        raise BpsError("BPS source checksum mismatch")
    if crc32(target) != target_crc:
        raise BpsError("BPS target checksum mismatch")
    return bytes(target)


def validate_apply_paths(source: Path, output: Path) -> None:
    if source.resolve() == output.resolve():
        raise BpsError("BPS output path must differ from source")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    create = subparsers.add_parser("create")
    create.add_argument("--source", type=Path, required=True)
    create.add_argument("--target", type=Path, required=True)
    create.add_argument("--output", type=Path, required=True)
    apply = subparsers.add_parser("apply")
    apply.add_argument("--source", type=Path, required=True)
    apply.add_argument("--patch", type=Path, required=True)
    apply.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "create":
            args.output.write_bytes(create_patch(args.source.read_bytes(), args.target.read_bytes()))
        else:
            validate_apply_paths(args.source, args.output)
            args.output.write_bytes(apply_patch(args.source.read_bytes(), args.patch.read_bytes()))
    except (BpsError, OSError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
