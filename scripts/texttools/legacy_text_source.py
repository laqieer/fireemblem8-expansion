#!/usr/bin/env python3

import argparse
from pathlib import Path


MODERN_ONLY_MARKER = b"\n## MSG_SAVE_COMPAT_LEGACY\n"
EXPECTED_MODERN_ONLY_IDS = (
    b"MSG_SAVE_COMPAT_LEGACY",
    b"MSG_SAVE_COMPAT_HEADER_CORRUPT",
    b"MSG_SAVE_COMPAT_METADATA_CORRUPT",
    b"MSG_SAVE_COMPAT_OLDER",
    b"MSG_SAVE_COMPAT_NEWER",
    b"MSG_SAVE_COMPAT_CONFIG_INCOMPATIBLE",
    b"MSG_SAVE_COMPAT_UNKNOWN",
    b"MSG_SAVE_COMPAT_BACK",
    b"MSG_SAVE_COMPAT_ERASE_ALL",
    b"MSG_SAVE_COMPAT_ERASE_CONFIRM",
)


def build_legacy_source(source: bytes) -> bytes:
    if source.count(MODERN_ONLY_MARKER) != 1:
        raise ValueError("expected exactly one modern-only save-compatibility marker")

    legacy, modern = source.split(MODERN_ONLY_MARKER, 1)
    modern = b"## MSG_SAVE_COMPAT_LEGACY\n" + modern
    actual_ids = tuple(
        line[3:]
        for line in modern.splitlines()
        if line.startswith(b"## ")
    )

    if actual_ids != EXPECTED_MODERN_ONLY_IDS:
        raise ValueError(
            "modern-only text suffix changed; update the archival filter deliberately"
        )

    return legacy.rstrip(b"\n") + b"\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()

    output = build_legacy_source(args.source.read_bytes())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
