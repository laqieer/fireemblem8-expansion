"""Synthetic 0x8000-byte SRAM image fixtures for gba-playtest save-gate
runtime scenarios (issue #2 slice 2, requirement 6).

All fixtures are generated at test/build time under caller-supplied,
ignored build/temp paths -- never committed as binaries. Byte layout is
derived from the Slice 1 format implementation/contracts in
scripts/modernize/save_format_tool.py (and its test helpers) rather than
duplicating magic numbers here: this module is a thin generator shell
around that existing, byte-exact source of truth.
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
_MODERNIZE_DIR = _REPO_ROOT / "scripts" / "modernize"
_MODERNIZE_TESTS_DIR = _MODERNIZE_DIR / "tests"
for _extra_path in (str(_MODERNIZE_DIR), str(_MODERNIZE_TESTS_DIR)):
    if _extra_path not in sys.path:
        sys.path.insert(0, _extra_path)

import save_format_tool as sft  # noqa: E402
from test_save_format_tool import make_header, make_image, make_meta  # noqa: E402


# Fixture state names, reusing the real classifier's own state constants
# (sft.SAVE_COMPAT_*) so a fixture name always matches the classification
# it is meant to produce.
STATE_CURRENT = sft.SAVE_COMPAT_CURRENT
STATE_VALID_LEGACY = sft.SAVE_COMPAT_VALID_LEGACY_OR_VANILLA
STATE_HEADER_CORRUPT = sft.SAVE_COMPAT_HEADER_CORRUPT
STATE_METADATA_CORRUPT = sft.SAVE_COMPAT_METADATA_CORRUPT
STATE_OLDER = sft.SAVE_COMPAT_MIGRATABLE_OLDER
STATE_NEWER = sft.SAVE_COMPAT_NEWER_UNSUPPORTED
STATE_CONFIG_INCOMPATIBLE = sft.SAVE_COMPAT_SAVE_CONFIG_INCOMPATIBLE
STATE_EMPTY = sft.SAVE_COMPAT_EMPTY

ALL_FIXTURE_STATES = (
    STATE_CURRENT,
    STATE_VALID_LEGACY,
    STATE_HEADER_CORRUPT,
    STATE_METADATA_CORRUPT,
    STATE_OLDER,
    STATE_NEWER,
    STATE_CONFIG_INCOMPATIBLE,
    STATE_EMPTY,
)


def resolve_epoch(repo_root: Path = _REPO_ROOT) -> int:
    return sft.resolve_save_compat_epoch(repo_root)


def build_fixture_image(state: str, repo_root: Path = _REPO_ROOT) -> bytes:
    """Builds a byte-exact 0x8000 SRAM image classified as `state` by the
    real classify_image()/classify_save_compat_raw() mirror, using only
    the existing Slice 1 helpers (no duplicated magic numbers)."""
    epoch = resolve_epoch(repo_root)

    if state == STATE_EMPTY:
        image = bytes([0xFF] * sft.SRAM_SIZE)
    elif state == STATE_HEADER_CORRUPT:
        header = make_header(valid=False)
        meta = make_meta(present=False)
        image = bytes(make_image(header, meta))
    elif state == STATE_VALID_LEGACY:
        header = make_header(valid=True)
        meta = make_meta(present=False)  # no FSAV magic -> legacy/vanilla
        image = bytes(make_image(header, meta))
    elif state == STATE_METADATA_CORRUPT:
        header = make_header(valid=True)
        meta = make_meta(
            format_version=sft.SAVE_FORMAT_VERSION_CURRENT,
            compat_epoch=epoch,
            corrupt_checksum=True,
        )
        image = bytes(make_image(header, meta))
    elif state == STATE_OLDER:
        header = make_header(valid=True)
        meta = make_meta(
            format_version=sft.SAVE_FORMAT_VERSION_CURRENT - 1
            if sft.SAVE_FORMAT_VERSION_CURRENT > 0 else 0,
            compat_epoch=epoch,
        )
        image = bytes(make_image(header, meta))
    elif state == STATE_NEWER:
        header = make_header(valid=True)
        meta = make_meta(
            format_version=sft.SAVE_FORMAT_VERSION_CURRENT + 1,
            compat_epoch=epoch,
        )
        image = bytes(make_image(header, meta))
    elif state == STATE_CONFIG_INCOMPATIBLE:
        header = make_header(valid=True)
        meta = make_meta(
            format_version=sft.SAVE_FORMAT_VERSION_CURRENT,
            compat_epoch=epoch + 1,
        )
        image = bytes(make_image(header, meta))
    elif state == STATE_CURRENT:
        header = make_header(valid=True)
        real_meta = sft.build_current_expansion_save_meta(repo_root)
        image = bytes(make_image(header, bytearray(real_meta.pack())))
    else:
        raise ValueError(f"unknown fixture state: {state!r}")

    actual_state = sft.classify_image(image, epoch)
    if actual_state != state:
        raise AssertionError(
            f"fixture generator bug: requested {state!r} but classify_image() "
            f"returned {actual_state!r}"
        )
    return image


def write_fixture(path: Path, state: str, repo_root: Path = _REPO_ROOT) -> Path:
    """Writes a generated fixture to `path` (expected to be under an
    ignored build/temp directory) and returns the path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(build_fixture_image(state, repo_root))
    return path


def migrate_fixture(source: Path, dest: Path, repo_root: Path = _REPO_ROOT) -> Path:
    """Invokes the existing, unmodified save_format_tool.py CLI (as a
    subprocess, exactly as a human operator would run it) to migrate
    `source` (must classify as SAVE_COMPAT_VALID_LEGACY_OR_VANILLA or
    SAVE_COMPAT_CURRENT) into `dest`, which must classify as
    SAVE_COMPAT_CURRENT afterward. Raises RuntimeError with the CLI's own
    stderr on any failure."""
    import subprocess

    dest.parent.mkdir(parents=True, exist_ok=True)
    tool = _MODERNIZE_DIR / "save_format_tool.py"
    result = subprocess.run(
        [
            sys.executable, str(tool),
            "--repo-root", str(repo_root),
            "migrate", str(source), str(dest), "--force",
        ],
        capture_output=True, text=True, check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"save_format_tool.py migrate failed (exit {result.returncode}): "
            f"{result.stderr.strip()}"
        )
    epoch = resolve_epoch(repo_root)
    migrated_state = sft.classify_image(dest.read_bytes(), epoch)
    if migrated_state != STATE_CURRENT:
        raise AssertionError(
            f"migrated fixture {dest} classified as {migrated_state!r}, expected "
            f"{STATE_CURRENT!r}"
        )
    return dest
