"""Byte-exact, host-only Python mirror of tools/gba-playtest/backend.c's
hash_sram(): the same FNV-1a 64-bit construction over the 0x8000-byte raw
SRAM image, honoring the same `sram_hash_exclude_ranges` byte-skipping
semantics gba_playtest.py's scenario schema adds to the compiled libmGBA
backend (see docs/save_format.md's "SRAM hash policy: exact vs.
normalized").

This mirror exists purely so host-side stdlib `unittest` coverage can
prove properties of the hash-exclusion algorithm itself (see
test_sram_hash_normalization.py) without requiring libmGBA or a built ROM.
It is never imported by run_save_compat_checks.py, gba_playtest.py's own
capture/verify pipeline, or any check that asserts against a real ROM --
the single source of truth for what a check actually asserts against a
ROM remains the compiled backend.c.
"""

from __future__ import annotations

from typing import Iterable, Tuple

SRAM_IMAGE_SIZE = 0x8000

# Standard FNV-1a 64-bit offset basis / prime, matching backend.c's
# hash_framebuffer()/hash_sram() literals exactly
# (UINT64_C(14695981039346656037) / UINT64_C(1099511628211)).
_FNV_OFFSET_BASIS = 0xCBF29CE484222325
_FNV_PRIME = 0x100000001B3
_MASK64 = 0xFFFFFFFFFFFFFFFF


def compute_sram_hash(
    image: bytes, exclude_ranges: Iterable[Tuple[int, int]] = ()
) -> str:
    """Returns the same 'fnv1a64-sram:...'/'fnv1a64-sram-normalized:...'
    text gba_playtest.py's capture pipeline would report for `image`
    (must be exactly SRAM_IMAGE_SIZE bytes) with the given (offset,
    length) `exclude_ranges` skipped entirely (never substituted/zeroed),
    exactly mirroring backend.c's hash_sram()/offset_excluded()."""
    if len(image) != SRAM_IMAGE_SIZE:
        raise ValueError(f"image must be exactly {SRAM_IMAGE_SIZE} bytes, got {len(image)}")
    ranges = list(exclude_ranges)
    excluded = bytearray(SRAM_IMAGE_SIZE)
    for offset, length in ranges:
        if offset < 0 or length < 1 or offset + length > SRAM_IMAGE_SIZE:
            raise ValueError(
                f"exclude range (offset={offset}, length={length}) out of bounds "
                f"for a {SRAM_IMAGE_SIZE}-byte image"
            )
        for i in range(offset, offset + length):
            excluded[i] = 1
    hash_value = _FNV_OFFSET_BASIS
    for index, byte in enumerate(image):
        if excluded[index]:
            continue
        hash_value ^= byte
        hash_value = (hash_value * _FNV_PRIME) & _MASK64
    algorithm = "fnv1a64-sram-normalized" if ranges else "fnv1a64-sram"
    return f"{algorithm}:{hash_value:016x}"
